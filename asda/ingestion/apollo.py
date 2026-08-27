"""Apollo people search + enrichment.

Free-plan keys authenticate but block People Search and People Match.
We still try those endpoints, fall back to Contacts, and surface a clear
upgrade message. CSV export from the Apollo UI always works.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from asda.config import get_settings
from asda.ingestion.base import LeadSource
from asda.ingestion.normalize import fingerprint_for, is_valid_lead, normalize_row
from asda.models.lead import Lead, LeadQuery

logger = logging.getLogger(__name__)

SEARCH_URLS = (
    "https://api.apollo.io/api/v1/mixed_people/api_search",
    "https://api.apollo.io/api/v1/mixed_people/search",
)
MATCH_URL = "https://api.apollo.io/api/v1/people/match"
CONTACTS_URL = "https://api.apollo.io/api/v1/contacts/search"
HEALTH_URL = "https://api.apollo.io/api/v1/auth/health"


class ApolloPlanError(RuntimeError):
    """Raised when the key is valid but the plan cannot access search/enrich."""


class ApolloSource(LeadSource):
    name = "apollo"

    def __init__(self, api_key: str | None = None) -> None:
        if api_key is not None:
            self.api_key = api_key
        else:
            try:
                from asda.runtime import effective

                self.api_key = effective().apollo_api_key
            except Exception:
                self.api_key = get_settings().apollo_api_key

    def _headers(self) -> dict[str, str]:
        # Apollo wants a single X-Api-Key. Sending both x-api-key and X-Api-Key
        # made match return 401 "Invalid API key" on a key that is otherwise valid.
        return {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self.api_key,
        }

    def validate_config(self) -> None:
        if not self.api_key:
            raise ValueError("APOLLO_API_KEY is not set")

    def healthcheck(self) -> dict[str, str]:
        try:
            self.validate_config()
        except Exception as exc:
            return {"source": self.name, "status": "misconfigured", "detail": str(exc)}
        info = self.probe()
        return {
            "source": self.name,
            "status": "ok" if info.get("logged_in") else "error",
            "detail": info.get("detail", ""),
        }

    def probe(self) -> dict[str, Any]:
        self.validate_config()
        out: dict[str, Any] = {"logged_in": False, "search": False, "enrich": False, "contacts": False}
        with httpx.Client(timeout=20) as client:
            h = client.get(HEALTH_URL, headers=self._headers())
            if h.status_code == 200:
                body = h.json()
                out["logged_in"] = bool(body.get("is_logged_in") or body.get("healthy"))
            r = client.post(
                SEARCH_URLS[0],
                headers=self._headers(),
                json={"per_page": 1, "page": 1, "person_titles": ["CEO"]},
            )
            out["search"] = r.status_code == 200
            if r.status_code == 403:
                out["plan_blocked"] = True
                out["detail"] = (
                    "Apollo key is valid, but People Search/Enrichment are not on the Free plan. "
                    "Upgrade at apollo.io/pricing, or export a CSV from Apollo and upload it here."
                )
            r2 = client.post(CONTACTS_URL, headers=self._headers(), json={"page": 1, "per_page": 1})
            out["contacts"] = r2.status_code == 200
            if r2.status_code == 200:
                out["saved_contacts"] = (r2.json().get("pagination") or {}).get("total_entries", 0)
            org = client.post(
                "https://api.apollo.io/api/v1/mixed_companies/search",
                headers=self._headers(),
                json={"q_organization_name": "altisec", "per_page": 1, "page": 1},
            )
            if org.status_code != 200:
                org = client.post(
                    "https://api.apollo.io/api/v1/organizations/search",
                    headers=self._headers(),
                    json={"q_organization_name": "altisec", "per_page": 1, "page": 1},
                )
            out["org_search"] = org.status_code == 200
        if out.get("search"):
            out["detail"] = "People Search works — you can ingest from Apollo."
            out["buy"] = False
        elif out.get("logged_in"):
            out["buy"] = True
            out["detail"] = (
                "Key is valid. People Search/Match are locked on Free. "
                "Do not buy the ~$65 plan unless you want Apollo to pull people. "
                "CSV upload and (if available) organization search already work."
            )
        elif not out.get("detail"):
            out["detail"] = "Apollo connected" if out.get("logged_in") else "Could not authenticate"
            out["buy"] = False
        return out

    def fetch(self, query: LeadQuery) -> list[Lead]:
        self.validate_config()
        payload = _search_payload(query)
        last_error: str | None = None
        with httpx.Client(timeout=40) as client:
            for url in SEARCH_URLS:
                resp = client.post(url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    people = resp.json().get("people") or resp.json().get("contacts") or []
                    return self._enrich_people(people, query.limit)
                if resp.status_code == 403:
                    last_error = resp.text
                    break
                last_error = f"{resp.status_code} {resp.text[:200]}"

            # Saved contacts (works on Free)
            contacts = self._fetch_contacts(client, query)
            if contacts:
                return contacts

        raise ApolloPlanError(
            "Apollo People Search is not available on this plan. "
            "Export a CSV from the Apollo UI, or upgrade. "
            f"({(last_error or '')[:180]})"
        )

    def _fetch_contacts(self, client: httpx.Client, query: LeadQuery) -> list[Lead]:
        resp = client.post(
            CONTACTS_URL,
            headers=self._headers(),
            json={"page": 1, "per_page": min(query.limit, 100)},
        )
        if resp.status_code != 200:
            return []
        rows = resp.json().get("contacts") or []
        leads: list[Lead] = []
        for person in rows:
            lead = person_to_lead(person, source="apollo_contacts")
            ok, _ = is_valid_lead(lead)
            if ok:
                leads.append(lead)
        return leads[: query.limit]

    def _enrich_people(self, people: list[dict], limit: int) -> list[Lead]:
        leads: list[Lead] = []
        for person in people[:limit]:
            stub = person_to_lead(person, source=self.name)
            try:
                stub = enrich_lead(stub, api_key=self.api_key)
            except ApolloPlanError:
                pass
            except Exception:
                logger.exception("enrich failed for %s", stub.full_name)
            ok, _ = is_valid_lead(stub)
            if ok:
                leads.append(stub)
        return leads


def _search_payload(query: LeadQuery) -> dict:
    payload: dict = {"page": int(query.extra.get("page") or 1), "per_page": min(query.limit, 100)}
    if query.titles:
        payload["person_titles"] = query.titles
    if query.locations:
        payload["person_locations"] = query.locations
    if query.industries:
        payload["q_organization_keyword_tags"] = query.industries
    if query.company_sizes:
        payload["organization_num_employees_ranges"] = query.company_sizes
    if query.keywords:
        payload["q_keywords"] = query.keywords
    if query.domains:
        payload["q_organization_domains_list"] = query.domains
    # Don't blindly merge extra — it may contain UI-only keys
    for k in ("person_seniorities", "contact_email_status", "include_similar_titles"):
        if k in query.extra:
            payload[k] = query.extra[k]
    return payload


def person_to_lead(person: dict[str, Any], source: str = "apollo") -> Lead:
    org = person.get("organization") or person.get("account") or {}
    contact = person.get("contact") or {}
    phone = (
        person.get("sanitized_phone")
        or person.get("phone")
        or contact.get("sanitized_phone")
        or ""
    )
    if not phone:
        nums = contact.get("phone_numbers") or person.get("phone_numbers") or []
        if nums and isinstance(nums[0], dict):
            phone = nums[0].get("sanitized_number") or nums[0].get("raw_number") or ""
    row = {
        "first_name": person.get("first_name") or contact.get("first_name"),
        "last_name": person.get("last_name") or contact.get("last_name"),
        "email": person.get("email") or contact.get("email"),
        "phone": phone,
        "linkedin_url": person.get("linkedin_url") or contact.get("linkedin_url"),
        "title": person.get("title") or contact.get("title"),
        "company_name": org.get("name") or person.get("organization_name"),
        "company_domain": org.get("primary_domain") or org.get("website_url"),
        "industry": org.get("industry"),
        "size": org.get("estimated_num_employees"),
        "location": person.get("city") or org.get("city"),
        "apollo_id": person.get("id"),
        "headline": person.get("headline"),
    }
    lead = normalize_row({**row, "_apollo": person}, source=source)
    if person.get("id"):
        lead.raw_data["apollo_id"] = person["id"]
    if not lead.fingerprint:
        lead.fingerprint = fingerprint_for(lead)
    return lead


def enrich_lead(lead: Lead, api_key: str | None = None) -> Lead:
    """Fill email, phone, LinkedIn via people/match. No-ops on free-plan 403."""
    if api_key:
        key = api_key
    else:
        try:
            from asda.runtime import effective

            key = effective().apollo_api_key
        except Exception:
            key = get_settings().apollo_api_key
    if not key:
        return lead
    params: dict[str, Any] = {}
    apollo_id = (lead.raw_data or {}).get("apollo_id")
    if apollo_id:
        params["id"] = apollo_id
    if lead.email:
        params["email"] = lead.email
    if lead.linkedin_url:
        params["linkedin_url"] = lead.linkedin_url
    if lead.first_name:
        params["first_name"] = lead.first_name
    if lead.last_name:
        params["last_name"] = lead.last_name
    if lead.company.domain:
        params["domain"] = lead.company.domain
    if lead.company.name:
        params["organization_name"] = lead.company.name
    if not params:
        return lead
    params["reveal_personal_emails"] = True
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": key,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(MATCH_URL, params=params, headers=headers)
    if resp.status_code == 403:
        raise ApolloPlanError("People Enrichment is not on this Apollo plan")
    if resp.status_code >= 400:
        logger.warning("apollo match %s: %s", resp.status_code, resp.text[:200])
        return lead
    person = (resp.json() or {}).get("person") or {}
    if not person:
        return lead
    filled = person_to_lead(person, source=lead.source)
    if filled.email:
        lead.email = filled.email
    if filled.phone:
        lead.phone = filled.phone
    if filled.linkedin_url:
        lead.linkedin_url = filled.linkedin_url
    if filled.title and not lead.title:
        lead.title = filled.title
    if filled.company.name and not lead.company.name:
        lead.company = filled.company
    lead.raw_data["apollo_id"] = person.get("id") or lead.raw_data.get("apollo_id")
    lead.raw_data["enriched"] = True
    lead.fingerprint = fingerprint_for(lead)
    return lead
