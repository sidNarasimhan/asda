"""Place the last-leg Bolna call. Never first. Never if another channel is live."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from asda.agents.base import audit
from asda.config import get_settings
from asda.models.audit import AuditEntry
from asda.models.events import EventType
from asda.models.lead import Lead
from asda.modules.bolna import BolnaClient, BolnaError, e164
from asda.modules.safety import SafetyGate


class PhoneOutreachAgent:
    name = "phone_outreach"

    def __init__(self) -> None:
        self.client = BolnaClient()
        self.safety = SafetyGate()

    def place(self, lead: Lead) -> tuple[Lead, list[AuditEntry]]:
        st = lead.sequence_state
        if not lead.phone:
            return lead, [audit(self.name, "skipped", "no phone")]
        if st.email_replied or st.linkedin_replied:
            st.phone_stage = "skipped"
            return lead, [audit(self.name, "skipped", "conversation already live")]
        ok, reason = self.safety.allow("email", lead)  # reuse cap bucket
        if not ok:
            return lead, [audit(self.name, "blocked", reason)]
        offer = get_settings().offer or {}
        user_data = {
            "lead_name": lead.first_name or lead.full_name,
            "lead_company": lead.company.name,
            "cbo_name": offer.get("cbo_name") or "our founder",
            "company": offer.get("company_name") or "",
        }
        try:
            result = self.client.call(lead.phone, user_data=user_data)
        except BolnaError as exc:
            return lead, [audit(self.name, "failed", str(exc)[:200])]
        st.phone_stage = "calling" if result.get("status") != "dry_run" else "queued"
        st.phone_execution_id = str(result.get("execution_id") or "")
        st.last_touch_at = datetime.now(timezone.utc)
        lead.add_outcome("call_placed", e164(lead.phone), status=result.get("status"))
        from asda.ops.activity import log

        log(
            EventType.CALL_PLACED,
            lead=lead,
            summary=f"Called {lead.full_name}" if result.get("status") != "dry_run" else f"Would call {lead.full_name}",
            status=result.get("status"),
            execution_id=st.phone_execution_id,
        )
        return lead, [
            audit(
                self.name,
                "placed" if result.get("status") != "dry_run" else "dry_run",
                e164(lead.phone),
                result=result,
            )
        ]
