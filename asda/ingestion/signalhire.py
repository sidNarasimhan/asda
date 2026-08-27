"""SignalHire lead discovery and synchronous person enrichment."""

from __future__ import annotations

import json
import secrets
from typing import Any

import httpx

from asda.config import get_settings
from asda.ingestion.base import LeadSource
from asda.ingestion.normalize import is_valid_lead, normalize_row
from asda.models.lead import Lead, LeadQuery

BASE_URL = "https://www.signalhire.com/api/v1"


def _linkedin_handle(url: str) -> str:
    value = (url or "").lower().strip().rstrip("/")
    marker = "/in/"
    return value.split(marker, 1)[1].split("/", 1)[0] if marker in value else ""


def _request_store() -> "Path":
    from pathlib import Path

    return get_settings().data_dir / "signalhire_requests.json"


def _load_requests() -> dict[str, Any]:
    path = _request_store()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_requests(requests: dict[str, Any]) -> None:
    path = _request_store()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(requests, indent=2))


class SignalHireSource(LeadSource):
    name = "signalhire"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else get_settings().signalhire_api_key

    def _headers(self) -> dict[str, str]:
        return {"apikey": self.api_key, "Content-Type": "application/json"}

    def validate_config(self) -> None:
        if not self.api_key:
            raise ValueError("SIGNALHIRE_API_KEY is not set")

    def healthcheck(self) -> dict[str, str]:
        try:
            self.validate_config()
            with httpx.Client(timeout=15) as client:
                response = client.get(f"{BASE_URL}/credits", headers=self._headers())
            response.raise_for_status()
            credits = response.json().get("credits", response.headers.get("X-Credits-Left", "?"))
            return {"source": self.name, "status": "ok", "detail": f"{credits} credits available"}
        except Exception as exc:  # noqa: BLE001 - source health must not crash
            return {"source": self.name, "status": "misconfigured", "detail": str(exc)}

    def fetch(self, query: LeadQuery) -> list[Lead]:
        """Discover leads, or enrich explicit identifiers in ``extra.identifiers``.

        Discovery uses SignalHire Search API (no contact data). Identifier lookups
        use its synchronous person endpoint and may consume contact credits.
        """
        self.validate_config()
        identifiers = [str(value).strip() for value in query.extra.get("identifiers", []) if str(value).strip()]
        if identifiers:
            return self._lookup_people(identifiers[:100], query.limit)
        return self._search(query)

    def enrich_existing(self, leads: list[Lead]) -> dict[str, Lead]:
        """Synchronously enrich existing leads without requiring a public callback."""
        self.validate_config()
        item_to_lead = {
            (lead.linkedin_url or lead.email or lead.phone).strip().lower(): lead
            for lead in leads
            if lead.linkedin_url or lead.email or lead.phone
        }
        if not item_to_lead:
            return {}
        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"{BASE_URL}/candidate/search", headers=self._headers(),
                json={"items": list(item_to_lead), "withoutWaterfall": True},
            )
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list):
            results = results.get("results", []) if isinstance(results, dict) else []
        enriched_by_id: dict[str, Lead] = {}
        for result in results:
            if not isinstance(result, dict) or result.get("status") != "success":
                continue
            original = item_to_lead.get(str(result.get("item", "")).strip().lower())
            candidate = result.get("candidate")
            if not original or not isinstance(candidate, dict):
                continue
            mapped: list[Lead] = []
            self._append_profiles(mapped, [candidate], 1)
            if mapped and (
                not original.linkedin_url
                or _linkedin_handle(mapped[0].linkedin_url) == _linkedin_handle(original.linkedin_url)
            ):
                enriched_by_id[original.id] = mapped[0]
        return enriched_by_id

    def _search(self, query: LeadQuery) -> list[Lead]:
        payload = dict(query.extra.get("signalhire_query") or {})
        if query.titles and "currentTitle" not in payload:
            payload["currentTitle"] = " OR ".join(query.titles)
        if query.keywords and "keywords" not in payload:
            payload["keywords"] = query.keywords
        if query.locations and "location" not in payload:
            payload["location"] = query.locations if len(query.locations) > 1 else query.locations[0]
        if query.industries and "industries" not in payload:
            payload["industries"] = query.industries
        if query.domains and "currentCompany" not in payload:
            payload["currentCompany"] = " OR ".join(query.domains)
        payload["size"] = min(max(int(payload.get("size", query.limit)), 1), 100)
        if not any(key not in {"size", "excludeRevealed", "excludeWatched", "excludeInLists", "excludeInProgress", "excludeEmailed"} for key in payload):
            raise ValueError("SignalHire needs a title, keyword, location, company, industry, or signalhire_query filter")

        leads: list[Lead] = []
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{BASE_URL}/candidate/searchByQuery", headers=self._headers(), json=payload)
            response.raise_for_status()
            data = response.json()
            self._append_profiles(leads, data.get("profiles", []), query.limit)
            request_id, scroll_id = data.get("requestId"), data.get("scrollId")
            while scroll_id and request_id and len(leads) < query.limit:
                response = client.post(
                    f"{BASE_URL}/candidate/scrollSearch/{request_id}",
                    headers=self._headers(), json={"scrollId": scroll_id},
                )
                response.raise_for_status()
                data = response.json()
                self._append_profiles(leads, data.get("profiles", []), query.limit)
                scroll_id = data.get("scrollId")
        return leads[: query.limit]

    def _lookup_people(self, identifiers: list[str], limit: int) -> list[Lead]:
        with httpx.Client(timeout=45) as client:
            response = client.post(
                f"{BASE_URL}/candidate/search", headers=self._headers(),
                json={"items": identifiers, "withoutWaterfall": True},
            )
            response.raise_for_status()
            results = response.json()
        if isinstance(results, dict):
            results = results.get("results") or results.get("profiles") or []
        rows = [item.get("candidate", item) for item in results if isinstance(item, dict) and item.get("status", "success") == "success"]
        leads: list[Lead] = []
        self._append_profiles(leads, rows, limit)
        return leads

    def submit_enrichment(self, leads: list[Lead], callback_base_url: str) -> int:
        """Start a full-coverage asynchronous contact lookup for existing leads."""
        self.validate_config()
        callback_base_url = callback_base_url.rstrip("/")
        if not callback_base_url.startswith("https://"):
            raise ValueError("SignalHire enrichment needs a public HTTPS callback URL")
        usable = [lead for lead in leads if lead.linkedin_url or lead.email or lead.phone][:100]
        if not usable:
            raise ValueError("No leads have a LinkedIn URL, email, or phone to enrich")
        token = secrets.token_urlsafe(24)
        callback_url = f"{callback_base_url}/api/ingest/signalhire/callback?token={token}"
        items = [lead.linkedin_url or lead.email or lead.phone for lead in usable]
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{BASE_URL}/candidate/search", headers=self._headers(),
                json={"items": items, "callbackUrl": callback_url},
            )
        response.raise_for_status()
        request_id = str(response.json()["requestId"])
        requests = _load_requests()
        requests[request_id] = {
            "token": token,
            "item_to_lead_id": {item.strip().lower(): lead.id for item, lead in zip(items, usable)},
        }
        _save_requests(requests)
        return int(request_id)

    @staticmethod
    def _append_profiles(leads: list[Lead], profiles: list[Any], limit: int) -> None:
        for profile in profiles:
            if not isinstance(profile, dict) or len(leads) >= limit:
                continue
            experience = profile.get("experience") or []
            current = next(
                (item for item in experience if isinstance(item, dict) and item.get("current")),
                next((item for item in experience if isinstance(item, dict)), {}),
            )
            contacts = profile.get("contacts") or []
            emails = [
                str(item.get("value", "")) for item in contacts
                if isinstance(item, dict) and item.get("type") == "email"
            ]
            phones = [
                str(item.get("value", "")) for item in contacts
                if isinstance(item, dict) and item.get("type") == "phone"
            ]
            social = profile.get("social") or []
            linkedin = next(
                (item.get("link", "") for item in social if isinstance(item, dict) and item.get("type") == "li"),
                "",
            )
            locations = profile.get("locations") or []
            location = profile.get("location", "")
            if not location and locations and isinstance(locations[0], dict):
                location = locations[0].get("name", "")
            row = {
                "full_name": profile.get("fullName", ""),
                "location": location,
                "title": current.get("position", current.get("title", profile.get("currentTitle", ""))),
                "company": current.get("company", profile.get("currentCompany", "")),
                "company_size": current.get("companySize", current.get("company_size", "")),
                "linkedin_url": linkedin or profile.get("linkedinUrl", profile.get("linkedin", "")),
                "email": " ".join(emails),
                "phone": " ".join(phones),
                "signalhire_uid": profile.get("uid", ""),
            }
            lead = normalize_row(row, source="signalhire")
            ok, _ = is_valid_lead(lead)
            if ok:
                leads.append(lead)
