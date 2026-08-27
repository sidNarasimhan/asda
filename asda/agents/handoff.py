from __future__ import annotations

from typing import Any

from asda.agents.base import audit
from asda.models.audit import AuditEntry
from asda.models.content import GeneratedContent
from asda.models.lead import Lead
from asda.modules.crm import get_crm, notify_slack


class HandoffAgent:
    name = "handoff"

    def run(
        self, lead: Lead, content: GeneratedContent | None = None, **_: Any
    ) -> tuple[Lead, list[AuditEntry]]:
        crm = get_crm()
        crm.upsert_contact(lead)
        crm.attach_research(lead, content)
        if lead.research_card:
            crm.log_activity(
                lead,
                f"ICP score {lead.score}. {lead.research_card.summary}",
            )
        if lead.status.value == "meeting_booked":
            crm.create_deal(lead)
            notify_slack(
                f"Handoff → CBO: {lead.full_name} @ {lead.company.name} (score {lead.score})"
            )
        logs = [audit(self.name, "synced", f"crm={crm.name}")]
        return lead, logs
