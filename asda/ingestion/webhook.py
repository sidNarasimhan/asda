from __future__ import annotations

from typing import Any

from asda.ingestion.base import LeadSource
from asda.ingestion.normalize import is_valid_lead, normalize_row
from asda.models.lead import Lead, LeadQuery


class WebhookSource(LeadSource):
    """Accept a single payload or a list posted to /ingest/webhook."""

    name = "webhook"

    def fetch(self, query: LeadQuery) -> list[Lead]:
        payload: Any = query.extra.get("payload")
        if payload is None:
            return []
        rows = payload if isinstance(payload, list) else [payload]
        leads: list[Lead] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            lead = normalize_row(row, source=self.name)
            ok, _ = is_valid_lead(lead)
            if ok:
                leads.append(lead)
        return leads[: query.limit]
