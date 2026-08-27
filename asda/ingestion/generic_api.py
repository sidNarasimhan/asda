"""Catch-all connector for ZoomInfo / SignalHire / Clay / anything HTTP + JSON.

Configure via LeadQuery.extra:

    {
      "url": "https://...",
      "method": "POST",
      "headers": {"Authorization": "Bearer ..."},
      "json": {...},
      "records_path": "data.people"   # dotted path to the list
    }
"""

from __future__ import annotations

from typing import Any

import httpx

from asda.ingestion.base import LeadSource
from asda.ingestion.normalize import is_valid_lead, normalize_row
from asda.models.lead import Lead, LeadQuery


def _dig(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class GenericAPISource(LeadSource):
    name = "generic_api"

    def __init__(self, default_name: str = "generic_api") -> None:
        self.name = default_name

    def fetch(self, query: LeadQuery) -> list[Lead]:
        url = query.extra.get("url")
        if not url:
            raise ValueError("query.extra.url is required for GenericAPISource")
        method = str(query.extra.get("method", "GET")).upper()
        headers = query.extra.get("headers") or {}
        json_body = query.extra.get("json")
        params = query.extra.get("params")
        with httpx.Client(timeout=30) as client:
            resp = client.request(method, url, headers=headers, json=json_body, params=params)
            resp.raise_for_status()
            data = resp.json()

        records_path = query.extra.get("records_path")
        records = _dig(data, records_path) if records_path else data
        if isinstance(records, dict):
            records = records.get("results") or records.get("data") or [records]
        if not isinstance(records, list):
            records = []

        leads: list[Lead] = []
        for row in records:
            if not isinstance(row, dict):
                continue
            lead = normalize_row(row, source=self.name)
            ok, _ = is_valid_lead(lead)
            if ok:
                leads.append(lead)
            if len(leads) >= query.limit:
                break
        return leads
