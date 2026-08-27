"""Every real action ASDA takes goes through here. No synthetic rows."""

from __future__ import annotations

from typing import Any

from asda.bus.events import get_bus
from asda.models.events import Event, EventType
from asda.models.lead import Lead


def log(
    event_type: EventType | str,
    *,
    lead: Lead | None = None,
    lead_id: str | None = None,
    actor: str = "asda",
    summary: str = "",
    **payload: Any,
) -> Event:
    if isinstance(event_type, str):
        event_type = EventType(event_type)
    data = {k: v for k, v in payload.items() if v is not None}
    if summary:
        data["summary"] = summary
    if lead is not None:
        lead_id = lead.id
        data.setdefault("lead_name", lead.full_name)
        if lead.company and lead.company.name:
            data.setdefault("company", lead.company.name)
    return get_bus().emit_type(event_type, lead_id, actor=actor, **data)
