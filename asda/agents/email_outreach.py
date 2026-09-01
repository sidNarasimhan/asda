from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from asda.agents.base import audit
from asda.models.audit import AuditEntry
from asda.models.content import GeneratedContent
from asda.models.events import EventType
from asda.models.lead import Lead, LeadStatus
from asda.modules.esp import get_esp
from asda.modules.safety import SafetyGate


class EmailOutreachAgent:
    name = "email_outreach"

    def __init__(self) -> None:
        self.esp = get_esp()
        self.safety = SafetyGate()

    def send_next(
        self, lead: Lead, content: GeneratedContent, **_: Any
    ) -> tuple[Lead, list[AuditEntry]]:
        if not lead.email:
            return lead, [audit(self.name, "skipped", "no email on lead")]
        if lead.sequence_state.email_replied:
            return lead, [audit(self.name, "skipped", "already replied")]

        ok, reason = self.safety.allow("email", lead)
        if not ok:
            lead.sequence_state.reason = reason
            # Pacing and daily caps are temporary holds. Do not permanently
            # pause a lead merely because another message just went out.
            if reason == "too_soon":
                from asda.modules.safety import next_slot

                lead.sequence_state.next_email_at = next_slot("email", datetime.now(timezone.utc))
                return lead, [audit(self.name, "held", reason)]
            if reason == "daily_cap_reached":
                lead.sequence_state.next_email_at = datetime.now(timezone.utc) + timedelta(hours=1)
                return lead, [audit(self.name, "held", reason)]
            lead.sequence_state.paused = True
            return lead, [audit(self.name, "blocked", reason)]

        emails = content.emails_b if lead.sequence_state.variant == "B" else content.emails
        if not emails:
            return lead, [audit(self.name, "skipped", "no emails generated")]
        step = int(lead.sequence_state.email_step or 0)
        if step >= len(emails):
            return lead, [audit(self.name, "complete", "email sequence finished")]

        # Instantly owns the whole email sequence (warmup, delays, inbox).
        if self.esp.name == "instantly" and hasattr(self.esp, "enqueue_sequence") and step == 0:
            result = self.esp.enqueue_sequence(lead, content)
            lead.sequence_state.email_step = len(emails)
            lead.sequence_state.step_index = len(emails)
            lead.sequence_state.next_email_at = None
            lead.sequence_state.channel = "email"
            lead.status = LeadStatus.SEQUENCED
            lead.add_outcome("email_enqueued", "instantly campaign", provider="instantly")
            if result.get("status") != "dry_run":
                self.safety.record_send("email")
            return lead, [
                audit(self.name, "enqueued", "Instantly campaign", result=result, event=EventType.EMAIL_QUEUED.value)
            ]
        if self.esp.name == "instantly" and step > 0:
            return lead, [audit(self.name, "skipped", "Instantly owns remaining email steps")]

        email = emails[step]
        result = self.esp.send(lead, email)
        if result.get("status") != "dry_run":
            self.safety.record_send("email")
        lead.status = LeadStatus.SEQUENCED
        lead.sequence_state.channel = "email"
        lead.sequence_state.email_step = step + 1
        lead.sequence_state.step_index = lead.sequence_state.email_step
        lead.sequence_state.last_touch_at = datetime.now(timezone.utc)
        next_delay = 1
        if step != 0 and step + 1 < len(emails):
            next_delay = int(emails[step + 1].delay_days or 3)
        # After the first follow-up we stop mailing and let the call leg take over.
        if lead.sequence_state.email_step >= 2:
            lead.sequence_state.next_email_at = None
        else:
            lead.sequence_state.next_email_at = datetime.now(timezone.utc) + timedelta(days=next_delay)
        if result.get("status") != "dry_run":
            lead.add_outcome("email_sent", email.subject, step=email.step, provider=self.esp.name)
            from asda.ops.activity import log

            log(
                EventType.EMAIL_SENT,
                lead=lead,
                summary=f"Sent email to {lead.full_name}",
                subject=email.subject,
                provider=self.esp.name,
            )
        else:
            lead.add_outcome("email_enqueued", email.subject, step=email.step, provider="dry_run")
        return lead, [
            audit(
                self.name,
                "sent",
                email.subject,
                provider=self.esp.name,
                result=result,
                event=EventType.EMAIL_SENT.value,
            )
        ]

    # Back-compat for older orchestrator calls
    def run(self, lead: Lead, content: GeneratedContent, **kw: Any) -> tuple[Lead, list[AuditEntry]]:
        return self.send_next(lead, content, **kw)
