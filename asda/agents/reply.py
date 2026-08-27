"""Reply & Conversation Agent — classify, auto-reply, book, escalate."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from asda.agents.base import audit
from asda.config import get_settings
from asda.llm.client import LLMClient, get_llm
from asda.llm.prompts import REPLY_SYSTEM, reply_user
from asda.models.audit import AuditEntry
from asda.models.lead import Lead, LeadStatus
from asda.models.outcomes import ReplyClass
from asda.modules.crm import get_crm, notify_slack


class ReplyDecision(BaseModel):
    classification: ReplyClass = ReplyClass.OTHER
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    should_auto_reply: bool = False
    draft: str = ""
    book_meeting: bool = False
    escalate: bool = False
    reason: str = ""


class ReplyAgent:
    name = "reply"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm()

    def run(self, lead: Lead, thread: str, **_: Any) -> tuple[Lead, ReplyDecision, list[AuditEntry]]:
        settings = get_settings()
        extra = ""
        try:
            from asda.memory.store import memory_block

            extra = memory_block(lead)
        except Exception:
            extra = ""
        decision = self.llm.parse(
            REPLY_SYSTEM,
            reply_user(thread, lead.model_dump_json(indent=2), settings.offer, extra_context=extra),
            ReplyDecision,
            model=settings.model_fast,
        )
        logs = [
            audit(
                self.name,
                "classified",
                decision.reason,
                classification=decision.classification.value,
                confidence=decision.confidence,
            )
        ]
        lead.status = LeadStatus.REPLIED
        lead.add_outcome("reply", decision.classification.value, draft=decision.draft)
        from asda.bus.events import get_bus
        from asda.models.events import EventType

        get_bus().emit_type(EventType.REPLY_RECEIVED, lead.id, classification=decision.classification.value)
        try:
            from asda.memory.store import remember

            remember(
                f"{lead.full_name} replied ({decision.classification.value}): {decision.reason or (thread or '')[:180]}",
                kind="episode",
                lead_id=lead.id,
                subject=lead.full_name,
                source="reply",
                importance=0.7 if decision.book_meeting else 0.55,
                event=False,
            )
            if decision.classification in {ReplyClass.UNSUBSCRIBE, ReplyClass.BOUNCE}:
                remember(
                    f"Do not contact {lead.full_name} ({lead.email}) — {decision.classification.value}",
                    kind="preference",
                    lead_id=lead.id,
                    subject=lead.full_name,
                    tags=["do_not_contact"],
                    importance=0.95,
                    source="reply",
                )
        except Exception:
            pass

        cls = decision.classification
        unsure = float(decision.confidence or 0) < 0.62 or (
            cls == ReplyClass.OTHER and not decision.book_meeting and not decision.should_auto_reply
        )
        if unsure and cls not in {ReplyClass.UNSUBSCRIBE, ReplyClass.BOUNCE, ReplyClass.SPAM}:
            decision.should_auto_reply = False
            decision.escalate = True
            lead.status = LeadStatus.AWAITING_APPROVAL
            lead.sequence_state.paused = True
            lead.sequence_state.reason = "waiting on CBO"
            try:
                from asda.ops.cbo import ask_cbo

                ask_cbo(
                    question=decision.reason or "I am not sure how to reply.",
                    lead_name=lead.full_name,
                    company=lead.company.name,
                    thread=thread,
                    draft=decision.draft,
                )
            except Exception:
                pass
            logs.append(audit(self.name, "asked_cbo", decision.reason))
            return lead, decision, logs

        if decision.classification in {ReplyClass.UNSUBSCRIBE, ReplyClass.BOUNCE, ReplyClass.SPAM}:
            lead.status = LeadStatus.SUPPRESSED
            lead.add_outcome("suppressed", decision.classification.value)
            logs.append(audit(self.name, "suppressed", decision.classification.value))
            return lead, decision, logs

        if decision.book_meeting:
            lead.status = LeadStatus.MEETING_BOOKED
            handoff = "Meeting requested — coordinate manually with Karthik."
            lead.add_outcome("meeting_requested", handoff)
            get_bus().emit_type(EventType.MEETING_BOOKED, lead.id, handoff=handoff)
            get_crm().create_deal(lead)
            notify_slack(
                f"Meeting path opened: {lead.full_name} @ {lead.company.name} "
                f"(score {lead.score})\n{handoff}\n{lead.research_card.summary if lead.research_card else ''}"
            )
            logs.append(audit(self.name, "meeting_requested", handoff))

        if decision.should_auto_reply and decision.draft and lead.email:
            try:
                from asda.models.content import SequenceEmail
                from asda.modules.esp import get_esp

                get_esp().send(
                    lead,
                    SequenceEmail(step=99, subject="Re: connecting", body=decision.draft),
                )
                logs.append(audit(self.name, "auto_replied", decision.draft[:120]))
            except Exception as exc:
                logs.append(audit(self.name, "auto_reply_failed", str(exc)))

        if decision.escalate or lead.score >= settings.safety.get("scoring", {}).get(
            "always_hitl_above", 90
        ):
            notify_slack(
                f"High-intent reply from {lead.full_name} ({lead.email})\n"
                f"Class: {decision.classification.value}\nDraft:\n{decision.draft}"
            )
            logs.append(audit(self.name, "escalated", decision.reason))

        return lead, decision, logs
