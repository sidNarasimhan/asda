from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from asda.config import get_settings
from asda.models.content import GeneratedContent
from asda.models.lead import Lead

logger = logging.getLogger(__name__)


class CRM(Protocol):
    name: str

    def upsert_contact(self, lead: Lead) -> dict: ...

    def log_activity(self, lead: Lead, body: str, activity_type: str = "NOTE") -> dict: ...

    def create_deal(self, lead: Lead, name: str | None = None) -> dict: ...

    def attach_research(self, lead: Lead, content: GeneratedContent | None = None) -> dict: ...


class NullCRM:
    name = "null"

    def upsert_contact(self, lead: Lead) -> dict:
        return {"status": "skipped", "reason": "no crm configured", "lead_id": lead.id}

    def log_activity(self, lead: Lead, body: str, activity_type: str = "NOTE") -> dict:
        logger.info("[crm-null] %s %s: %s", lead.id, activity_type, body[:120])
        return {"status": "skipped"}

    def create_deal(self, lead: Lead, name: str | None = None) -> dict:
        return {"status": "skipped"}

    def attach_research(self, lead: Lead, content: GeneratedContent | None = None) -> dict:
        return {"status": "skipped"}


class HubSpotCRM:
    name = "hubspot"
    BASE = "https://api.hubapi.com"

    def __init__(self) -> None:
        from asda.runtime import effective

        self.token = effective().hubspot_access_token or get_settings().hubspot_access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def upsert_contact(self, lead: Lead) -> dict:
        props = {
            "email": lead.email,
            "firstname": lead.first_name,
            "lastname": lead.last_name,
            "jobtitle": lead.title,
            "phone": lead.phone,
            "company": lead.company.name,
            "website": lead.company.domain,
            "hs_linkedin_url": lead.linkedin_url,
        }
        payload = {
            "idProperty": "email",
            "inputs": [{"id": lead.email, "properties": props}],
        }
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{self.BASE}/crm/v3/objects/contacts/batch/upsert",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    def log_activity(self, lead: Lead, body: str, activity_type: str = "NOTE") -> dict:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{self.BASE}/crm/v3/objects/notes",
                json={"properties": {"hs_note_body": body, "hs_timestamp": lead.updated_at.isoformat()}},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    def create_deal(self, lead: Lead, name: str | None = None) -> dict:
        payload = {
            "properties": {
                "dealname": name or f"{lead.company.name} — {lead.full_name}",
                "dealstage": "appointmentscheduled",
                "pipeline": "default",
            }
        }
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{self.BASE}/crm/v3/objects/deals", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    def attach_research(self, lead: Lead, content: GeneratedContent | None = None) -> dict:
        card = lead.research_card
        body = f"# Research — {lead.full_name}\n\nScore: {lead.score}\n\n"
        if card:
            body += f"{card.summary}\n\nHooks: {', '.join(card.personalization_hooks)}\n"
        return self.log_activity(lead, body)


class WebhookCRM:
    """Any CRM that accepts a JSON webhook (HubSpot ops, Close, Attio, custom)."""

    name = "webhook"

    def __init__(self, url: str) -> None:
        self.url = url

    def _post(self, event: str, lead: Lead, extra: dict[str, Any] | None = None) -> dict:
        payload = {
            "event": event,
            "lead": lead.model_dump(mode="json"),
            **(extra or {}),
        }
        with httpx.Client(timeout=15) as client:
            resp = client.post(self.url, json=payload)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return {"status": "ok", "code": resp.status_code}

    def upsert_contact(self, lead: Lead) -> dict:
        return self._post("contact.upsert", lead)

    def log_activity(self, lead: Lead, body: str, activity_type: str = "NOTE") -> dict:
        return self._post("activity.log", lead, {"body": body, "activity_type": activity_type})

    def create_deal(self, lead: Lead, name: str | None = None) -> dict:
        return self._post("deal.create", lead, {"name": name or f"{lead.company.name} — {lead.full_name}"})

    def attach_research(self, lead: Lead, content: GeneratedContent | None = None) -> dict:
        extra = {"content": content.model_dump(mode="json") if content else None}
        return self._post("research.attach", lead, extra)


def get_crm() -> CRM:
    from asda.runtime import effective

    cfg = effective()
    if cfg.hubspot_access_token:
        return HubSpotCRM()
    if cfg.crm_webhook_url:
        return WebhookCRM(cfg.crm_webhook_url)
    return NullCRM()


def notify_slack(text: str) -> dict[str, Any]:
    url = get_settings().slack_webhook_url
    if not url:
        logger.info("[slack-dry] %s", text)
        return {"status": "skipped"}
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json={"text": text})
        resp.raise_for_status()
    return {"status": "sent"}
