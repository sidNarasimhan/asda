from __future__ import annotations

from typing import Any, Protocol

from asda.models.audit import AuditEntry
from asda.models.lead import Lead


class Agent(Protocol):
    name: str

    def run(self, lead: Lead, **kwargs: Any) -> Lead: ...


def audit(agent: str, action: str, detail: str = "", **data: Any) -> AuditEntry:
    return AuditEntry(agent=agent, action=action, detail=detail, data=data)
