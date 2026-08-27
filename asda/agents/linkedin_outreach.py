from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from asda.agents.base import audit
from asda.models.audit import AuditEntry
from asda.models.content import GeneratedContent, LinkedInMessage
from asda.models.lead import Lead, LeadStatus
from asda.modules.linkedin_provider import get_linkedin
from asda.modules.safety import SafetyGate


def _followups(content: GeneratedContent) -> list[LinkedInMessage]:
    msgs = [m for m in content.linkedin.messages if m.kind != "connection_note"]
    if not msgs:
        msgs = list(content.linkedin.messages)
    return msgs[:3]


class LinkedInOutreachAgent:
    name = "linkedin_outreach"

    def __init__(self) -> None:
        self.provider = get_linkedin()
        self.safety = SafetyGate()

    def send_connect(
        self, lead: Lead, content: GeneratedContent, **_: Any
    ) -> tuple[Lead, list[AuditEntry]]:
        if not lead.linkedin_url:
            return lead, [audit(self.name, "skipped", "no linkedin url")]
        ok, reason = self.safety.allow("linkedin_connect", lead)
        if not ok:
            # Leave stage idle so we retry later. Never mark this as sent.
            lead.sequence_state.next_linkedin_at = datetime.now(timezone.utc) + timedelta(minutes=30)
            return lead, [audit(self.name, "held", reason)]
        note = content.linkedin.connection_note
        try:
            result = self.provider.connect(lead, note, content)
        except Exception as exc:
            return lead, [audit(self.name, "failed", str(exc)[:240])]
        status = str(result.get("status") or "")
        if result.get("error") or status in {"error", "failed", "skipped"}:
            return lead, [audit(self.name, "failed", str(result.get("error") or status)[:240])]
        if status != "dry_run":
            self.safety.record_send("linkedin_connect")
        # LinkedIn Outreach.js runs invite + 3 follow-ups itself
        if result.get("delegated") or status in {"dry_run", "success", "launched", "running", ""}:
            lead.sequence_state.linkedin_stage = "delegated"
            lead.sequence_state.next_linkedin_at = None
        else:
            lead.sequence_state.linkedin_stage = "connect_sent"
            lead.sequence_state.next_linkedin_at = datetime.now(timezone.utc) + timedelta(days=2)
        lead.status = LeadStatus.SEQUENCED
        lead.add_outcome("linkedin_connect", note[:80], provider=self.provider.name)
        if status != "dry_run":
            from asda.models.events import EventType
            from asda.ops.activity import log

            log(
                EventType.LINKEDIN_SENT,
                lead=lead,
                summary=f"Queued LinkedIn invite for {lead.full_name}",
                kind="connect",
                provider=self.provider.name,
            )
        return lead, [audit(self.name, "connect", provider=self.provider.name, result=result)]

    def send_followup(
        self, lead: Lead, content: GeneratedContent, **_: Any
    ) -> tuple[Lead, list[AuditEntry]]:
        if lead.sequence_state.linkedin_replied:
            return lead, [audit(self.name, "skipped", "already replied")]
        msgs = _followups(content)
        sent = lead.sequence_state.linkedin_messages_sent
        if sent >= min(3, lead.sequence_state.max_linkedin_messages, max(len(msgs), 3)):
            lead.sequence_state.linkedin_stage = "done"
            return lead, [audit(self.name, "complete", "linkedin follow-ups finished")]
        ok, reason = self.safety.allow("linkedin", lead)
        if not ok:
            return lead, [audit(self.name, "blocked", reason)]

        if sent < len(msgs):
            body = msgs[sent].body
        else:
            body = (
                f"Hi {lead.first_name} — circling back with a specific idea based on "
                f"{lead.company.name}. Open to a 15-min look this week?"
            )
        result = self.provider.message(lead, body)
        if result.get("status") != "dry_run":
            self.safety.record_send("linkedin")
        lead.sequence_state.linkedin_messages_sent = sent + 1
        lead.sequence_state.linkedin_stage = "messaging"
        lead.sequence_state.next_linkedin_at = datetime.now(timezone.utc) + timedelta(days=3)
        if lead.status != LeadStatus.REPLIED:
            lead.status = LeadStatus.CONNECTED
        lead.add_outcome("linkedin_message", body[:80], step=sent + 1, provider=self.provider.name)
        return lead, [audit(self.name, "message", step=sent + 1, result=result)]

    def run(self, lead: Lead, content: GeneratedContent, **kw: Any) -> tuple[Lead, list[AuditEntry]]:
        st = lead.sequence_state
        if st.linkedin_stage in {"", "idle"}:
            return self.send_connect(lead, content, **kw)
        return self.send_followup(lead, content, **kw)
