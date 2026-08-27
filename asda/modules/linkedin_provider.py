from __future__ import annotations

import logging
from typing import Any, Protocol

from asda.models.content import GeneratedContent
from asda.models.lead import Lead
from asda.modules.phantombuster import (
    PhantomBusterClient,
    PhantomBusterError,
    outreach_argument,
    write_lead_csv,
)
from asda.runtime import effective

logger = logging.getLogger(__name__)


class LinkedInProvider(Protocol):
    name: str

    def connect(self, lead: Lead, note: str, content: GeneratedContent | None = None) -> dict: ...

    def message(self, lead: Lead, body: str) -> dict: ...


class DryRunLinkedIn:
    name = "dry_run"

    def connect(self, lead: Lead, note: str, content: GeneratedContent | None = None) -> dict:
        logger.info("[dry-run] LI outreach → %s | %s", lead.linkedin_url, note[:80])
        return {
            "status": "dry_run",
            "action": "outreach",
            "to": lead.linkedin_url,
            "delegated": True,
        }

    def message(self, lead: Lead, body: str) -> dict:
        logger.info("[dry-run] LI message skipped (outreach phantom owns follow-ups) → %s", lead.linkedin_url)
        return {"status": "dry_run", "action": "delegated"}


class PhantomBusterLinkedIn:
    name = "phantombuster"

    def __init__(self) -> None:
        self.client = PhantomBusterClient()
        self.cfg = effective()

    def _leads_url(self, lead: Lead) -> str:
        write_lead_csv(lead)
        if self.cfg.public_base_url:
            return self.cfg.public_base_url.rstrip("/") + f"/feeds/{lead.id}.csv"
        if self.cfg.linkedin_sheet_url:
            return self.cfg.linkedin_sheet_url
        # Last resort: some Outreach builds accept a profile URL
        return lead.linkedin_url

    def connect(self, lead: Lead, note: str, content: GeneratedContent | None = None) -> dict:
        if not self.cfg.pb_cookie:
            raise PhantomBusterError("LinkedIn session cookie (li_at) is missing")
        if not self.cfg.pb_connect_agent_id:
            raise PhantomBusterError("LinkedIn bot is not connected")
        content = content or GeneratedContent()
        arg = outreach_argument(lead, content, self.cfg.pb_cookie, self._leads_url(lead))
        try:
            result = self.client.launch(self.cfg.pb_connect_agent_id, arg)
        except PhantomBusterError as exc:
            logger.warning("LinkedIn launch blocked: %s", exc)
            return {"status": "error", "error": str(exc)[:400], "delegated": False}
        result["delegated"] = True
        result.setdefault("status", "launched")
        return result

    def message(self, lead: Lead, body: str) -> dict:
        return {"status": "delegated", "detail": "LinkedIn Outreach phantom sends follow-ups"}


def get_linkedin() -> LinkedInProvider:
    cfg = effective()
    if cfg.dry_run or not cfg.pb_key_set:
        return DryRunLinkedIn()
    return PhantomBusterLinkedIn()
