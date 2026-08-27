"""Email service providers. Instantly preferred; SMTP as a self-hosted fallback."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

import httpx

from asda.config import get_settings
from asda.models.content import GeneratedContent, SequenceEmail
from asda.models.lead import Lead

logger = logging.getLogger(__name__)


class ESP(Protocol):
    name: str

    def send(self, lead: Lead, email: SequenceEmail) -> dict: ...


class DryRunESP:
    name = "dry_run"

    def send(self, lead: Lead, email: SequenceEmail) -> dict:
        logger.info("[dry-run] email → %s | %s", lead.email, email.subject)
        return {"status": "dry_run", "to": lead.email, "subject": email.subject}


class InstantlyESP:
    """Hands the whole sequence to Instantly. Instantly owns warmup, send windows, follow-ups."""

    name = "instantly"

    def send(self, lead: Lead, email: SequenceEmail) -> dict:
        from asda.modules.instantly import InstantlyClient

        if email.step >= 90:
            logger.info("[instantly] reply draft for %s: %s", lead.email, email.body[:120])
            return {"status": "draft", "channel": "instantly_unibox", "body": email.body}
        client = InstantlyClient()
        dummy = GeneratedContent(emails=[email])
        return client.enqueue(lead, dummy)

    def enqueue_sequence(self, lead: Lead, content: GeneratedContent) -> dict:
        from asda.modules.instantly import InstantlyClient

        return InstantlyClient().enqueue(lead, content)


class SMTPESP:
    name = "smtp"

    def send(self, lead: Lead, email: SequenceEmail) -> dict:
        from asda.runtime import effective

        s = effective()
        if not s.smtp_host:
            raise RuntimeError("SMTP is not configured — add it in Setup")
        msg = EmailMessage()
        frm = s.smtp_from or s.smtp_user
        msg["Subject"] = email.subject
        msg["From"] = frm
        msg["To"] = lead.email
        reply_to = (getattr(s, "smtp_reply_to", "") or "").strip()
        if reply_to and reply_to.lower() != (frm or "").lower():
            msg["Reply-To"] = reply_to
        msg.set_content(email.body)
        with smtplib.SMTP(s.smtp_host, int(s.smtp_port or 587)) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return {"status": "sent", "to": lead.email}


def get_esp() -> ESP:
    from asda.runtime import effective

    s = get_settings()
    cfg = effective()
    if cfg.dry_run:
        return DryRunESP()
    if cfg.instantly_key_set or s.instantly_api_key:
        return InstantlyESP()
    if cfg.smtp_host:
        return SMTPESP()
    return DryRunESP()
