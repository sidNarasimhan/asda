"""Mail the CBO: Monday reports, and questions the agent cannot answer alone."""

from __future__ import annotations

from email.mime.text import MIMEText
import smtplib
from typing import Any

from asda.runtime import effective


def cbo_inbox() -> str:
    cfg = effective()
    return (cfg.cbo_email or cfg.smtp_user or "").strip()


def send_to_cbo(subject: str, body: str) -> dict[str, Any]:
    cfg = effective()
    dest = cbo_inbox()
    if not dest:
        return {"ok": False, "reason": "no CBO inbox configured"}
    if not cfg.smtp_host or not cfg.smtp_user or not cfg.smtp_password:
        return {"ok": False, "reason": "mailbox not connected", "dest": dest, "preview": body[:400]}
    if cfg.dry_run:
        return {"ok": True, "status": "dry_run", "dest": dest, "subject": subject, "preview": body[:400]}
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_from or cfg.smtp_user
    msg["To"] = dest
    try:
        with smtplib.SMTP(cfg.smtp_host, int(cfg.smtp_port or 587), timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(cfg.smtp_user, cfg.smtp_password.replace(" ", ""))
            smtp.sendmail(msg["From"], [dest], msg.as_string())
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:200], "dest": dest}
    from asda.models.events import EventType
    from asda.ops.activity import log

    log(EventType.CBO_ASKED, summary=f"Emailed CBO: {subject[:80]}", dest=dest)
    return {"ok": True, "status": "sent", "dest": dest, "subject": subject}


def ask_cbo(*, question: str, lead_name: str = "", company: str = "", thread: str = "", draft: str = "") -> dict[str, Any]:
    """When the agent does not know how to reply, ask the human."""
    dest = cbo_inbox()
    who = f"{lead_name} @ {company}".strip(" @")
    body = (
        f"I need a call from you before I reply.\n\n"
        f"Who: {who or 'unknown'}\n"
        f"Why I'm stuck: {question}\n\n"
        f"Thread:\n{thread[:2500] or '(none)'}\n\n"
        f"Draft I would send if you say go:\n{draft or '(none)'}\n\n"
        f"Reply in the ASDA chat with what to do, or say 'send the draft'."
    )
    subject = f"ASDA needs you: {who or 'a lead'}"
    result = send_to_cbo(subject, body)
    try:
        from asda.memory.store import remember

        remember(
            f"Asked CBO about {who}: {question[:200]}",
            kind="episode",
            subject=lead_name or "CBO",
            source="cbo",
            importance=0.7,
            event=False,
        )
    except Exception:
        pass
    result["question"] = question
    result["dest"] = dest
    return result
