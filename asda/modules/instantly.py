"""Instantly.ai v2 — Gmail Workspace + Outlook OAuth, campaigns, personalized leads."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from asda.config import get_settings
from asda.models.content import GeneratedContent
from asda.models.lead import Lead
from asda.runtime import effective

logger = logging.getLogger(__name__)

BASE = "https://api.instantly.ai/api/v2"


class InstantlyError(RuntimeError):
    pass


class InstantlyClient:
    def __init__(self, api_key: str | None = None) -> None:
        s = get_settings()
        rt = effective()
        self.api_key = api_key or rt.instantly_api_key or s.instantly_api_key
        self.campaign_id = rt.instantly_campaign_id or s.instantly_campaign_id

    @property
    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise InstantlyError("Instantly API key is missing")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        with httpx.Client(timeout=30) as client:
            resp = client.request(method, f"{BASE}{path}", headers=self._headers, **kwargs)
        return resp

    def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "detail": "No Instantly API key"}
        try:
            accounts = self.list_accounts()
            campaigns = self.list_campaigns()
            return {
                "ok": True,
                "accounts": accounts,
                "campaigns": campaigns,
                "campaign_id": self.campaign_id,
            }
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    def list_accounts(self) -> list[dict[str, Any]]:
        resp = self._request("GET", "/accounts")
        if resp.status_code >= 400:
            resp = self._request("GET", "/account")
        if resp.status_code >= 400:
            raise InstantlyError(f"accounts {resp.status_code}: {resp.text[:240]}")
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items") or data.get("accounts") or []
        out = []
        for a in items:
            if isinstance(a, str):
                out.append({"email": a})
            elif isinstance(a, dict):
                out.append(
                    {
                        "email": a.get("email") or a.get("username") or "",
                        "provider": a.get("provider") or a.get("type") or "",
                        "warmup": a.get("warmup_status") or a.get("status") or "",
                    }
                )
        return out

    def list_campaigns(self) -> list[dict[str, Any]]:
        resp = self._request("GET", "/campaigns")
        if resp.status_code >= 400:
            raise InstantlyError(f"campaigns {resp.status_code}: {resp.text[:240]}")
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items") or data.get("campaigns") or []
        return [
            {"id": str(c.get("id") or ""), "name": c.get("name") or ""}
            for c in items
            if isinstance(c, dict)
        ]

    def oauth_init(self, provider: str) -> dict[str, Any]:
        """provider: google | microsoft. Returns auth_url for the CBO to click."""
        slug = "google" if provider in {"google", "gmail", "workspace"} else "microsoft"
        resp = self._request("POST", f"/oauth/{slug}/init")
        if resp.status_code >= 400:
            raise InstantlyError(f"oauth init: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def oauth_status(self, session_id: str) -> dict[str, Any]:
        resp = self._request("GET", f"/oauth/session/status/{session_id}")
        if resp.status_code >= 400:
            raise InstantlyError(f"oauth status: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def enqueue(self, lead: Lead, content: GeneratedContent) -> dict[str, Any]:
        """Push a fully personalized sequence as custom variables. Instantly owns send + follow-ups."""
        if not self.campaign_id:
            raise InstantlyError("Pick an Instantly campaign in Setup")
        emails = content.emails or []
        custom: dict[str, Any] = {
            "linkedin_url": lead.linkedin_url,
            "title": lead.title,
            "hook": (lead.research_card.personalization_hooks or [""])[0] if lead.research_card else "",
        }
        for i, e in enumerate(emails[:6], start=1):
            custom[f"s{i}_subject"] = e.subject
            custom[f"s{i}_body"] = e.body
        if emails:
            custom["subject"] = emails[0].subject
            custom["body"] = emails[0].body
        payload = {
            "campaign_id": self.campaign_id,
            "email": lead.email,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "company_name": lead.company.name,
            "personalization": emails[0].body if emails else "",
            "custom_variables": custom,
            "skip_if_in_workspace": True,
        }
        resp = self._request("POST", "/leads", json=payload)
        if resp.status_code >= 400:
            raise InstantlyError(f"add lead: {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else {"status": "ok"}

    def recent_replies(self, limit: int = 50) -> list[dict[str, Any]]:
        """Best-effort Unibox pull. Instantly shapes vary; we tolerate misses."""
        for path, body in (
            ("/emails", None),
            ("/unibox/emails", None),
            ("/lead/list", {"limit": limit, "filter": {"is_unread_email": True}}),
        ):
            try:
                resp = self._request("GET" if body is None else "POST", path, json=body)
                if resp.status_code >= 400:
                    continue
                data = resp.json()
                items = data if isinstance(data, list) else data.get("items") or data.get("emails") or []
                out = []
                for it in items[:limit]:
                    if not isinstance(it, dict):
                        continue
                    out.append(
                        {
                            "from": (it.get("from") or it.get("lead") or it.get("email") or "").lower(),
                            "subject": it.get("subject") or "",
                            "body": it.get("body") or it.get("text") or it.get("snippet") or "",
                            "type": it.get("email_type") or it.get("type") or "",
                        }
                    )
                if out:
                    return out
            except Exception:
                logger.exception("instantly replies via %s failed", path)
        return []


def get_instantly() -> InstantlyClient:
    return InstantlyClient()
