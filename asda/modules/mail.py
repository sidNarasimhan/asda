"""Gmail / Outlook SMTP + IMAP. Outlook personal and Microsoft 365 work use different hosts."""

from __future__ import annotations

import imaplib
import smtplib


PROVIDERS = {
    "gmail": {
        "id": "gmail",
        "label": "Google (Gmail or Google Workspace)",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "password_label": "16-character App Password",
        "how": [
            "Use the Google inbox you send sales mail from.",
            "Open https://myaccount.google.com/security while signed into that account.",
            "Turn on 2-Step Verification if it is off.",
            "Open https://myaccount.google.com/apppasswords",
            "App name: ASDA. Create. Copy the 16-character code.",
            "Paste that code below. Not your normal Gmail password. Spaces are fine.",
        ],
    },
    "outlook_personal": {
        "id": "outlook_personal",
        "label": "Outlook.com / Hotmail / Live (personal)",
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "password_label": "Microsoft App Password",
        "how": [
            "This is for @outlook.com, @hotmail.com, @live.com, @msn.com.",
            "Sign in at outlook.live.com.",
            "Settings (gear) → Mail → Forwarding and IMAP. Turn on Let devices and apps use IMAP. Save.",
            "Turn on two-step verification: https://account.microsoft.com/security",
            "Create an app password: https://account.live.com/proofs/AppPassword → Create a new app password → name it ASDA.",
            "Paste that 16-character app password below. Not your usual Microsoft password.",
        ],
    },
    "outlook_work": {
        "id": "outlook_work",
        "label": "Microsoft 365 (work email)",
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "password_label": "Mailbox password or app password",
        "how": [
            "This is for work email like you@yourcompany.com on Microsoft 365.",
            "Username is your full email address. Use a Microsoft app password, not your normal login.",
            "Sending can work. Reading this same inbox usually cannot: Microsoft turns off IMAP passwords, and Graph needs an admin.",
            "That is fine. After send is connected, add a Gmail reply inbox so prospect replies still come back.",
            "Do not wait on IT for Graph. App passwords plus Gmail replies is the path that does not need an admin.",
        ],
    },
}

# Back-compat alias used by older forms
PROVIDERS["outlook"] = PROVIDERS["outlook_work"]

_PERSONAL_DOMAINS = ("outlook.com", "hotmail.com", "live.com", "msn.com")


def resolve_mail_spec(provider: str, user: str = "") -> dict:
    """Pick SMTP/IMAP hosts. Known domains win so a Hotmail address is not sent to Gmail servers."""
    p = (provider or "").strip().lower()
    addr = (user or "").strip().lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    if domain in {"gmail.com", "googlemail.com"}:
        return PROVIDERS["gmail"]
    if domain in _PERSONAL_DOMAINS:
        return PROVIDERS["outlook_personal"]
    if p in {"gmail", "google"}:
        return PROVIDERS["gmail"]
    if p in {"outlook_personal", "hotmail", "live"}:
        return PROVIDERS["outlook_personal"]
    if p in {"outlook", "outlook_work", "microsoft", "microsoft365", "office365", "m365"}:
        return PROVIDERS["outlook_work"]
    return PROVIDERS["gmail"]


def _auth_hint(host: str, exc: Exception) -> str:
    err = str(exc)
    if "gmail" in host:
        return (
            f"Google rejected the login. Use a 16-character App Password, not your Gmail password. ({err})"
        )
    if "smtp-mail.outlook.com" in host:
        return (
            "Outlook.com rejected the login. Turn on IMAP in Outlook settings, then paste an App Password "
            f"from account.live.com/proofs/AppPassword (two-step must be on). ({err})"
        )
    if "5.7.139" in err or "did not meet the criteria" in err.lower():
        return (
            "Microsoft 365 blocked SMTP login (error 5.7.139). This is an admin setting, not a wrong password. "
            "In Microsoft 365 admin center: Users → Active users → your user → Mail → Manage email apps. "
            "Turn on IMAP and Authenticated SMTP. Then Entra admin center → Properties → manage Security defaults. "
            "If Security defaults are On, basic SMTP is blocked: either create an App Password "
            "(account.microsoft.com/security, MFA must be on) or ask IT to allow SMTP AUTH for this mailbox. "
            "Username must be the full mailbox address."
        )
    return (
        "Microsoft 365 rejected the login. Confirm Authenticated SMTP and IMAP are enabled for this mailbox "
        "(admin center → Users → you → Mail → Manage email apps). "
        f"If 2FA is on, try an App Password. If SMTP AUTH is off for the tenant, this cannot work. ({err})"
    )


def check_smtp(host: str, port: int, user: str, password: str) -> tuple[bool, str]:
    if not host or not user or not password:
        return False, "SMTP host, user, and password are required"
    password = password.replace(" ", "")
    try:
        with smtplib.SMTP(host, int(port), timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(user, password)
        return True, f"SMTP login ok ({user} @ {host})"
    except smtplib.SMTPAuthenticationError as exc:
        return False, _auth_hint(host, exc)
    except Exception as exc:
        return False, f"SMTP failed: {exc}"


def check_imap(host: str, port: int, user: str, password: str) -> tuple[bool, str]:
    if not host or not user or not password:
        return False, "IMAP host, user, and password are required"
    password = password.replace(" ", "")
    try:
        mail = imaplib.IMAP4_SSL(host, int(port), timeout=20)
        try:
            mail.login(user, password)
            mail.select("INBOX")
        finally:
            try:
                mail.logout()
            except Exception:
                pass
        return True, f"IMAP login ok ({user} @ {host})"
    except imaplib.IMAP4.error as exc:
        return False, _auth_hint(host, exc)
    except Exception as exc:
        return False, f"IMAP failed: {exc}"


def connect_reply_inbox(user: str, password: str) -> tuple[bool, str]:
    """Gmail IMAP for replies when the send mailbox cannot be read (typical M365)."""
    from asda.runtime import update_runtime

    addr = (user or "").strip()
    pw = (password or "").replace(" ", "")
    if "@" not in addr or len(pw) < 8:
        return False, "I need a Gmail address and a 16-character Google app password."
    spec = PROVIDERS["gmail"]
    ok, msg = check_imap(spec["imap_host"], spec["imap_port"], addr, pw)
    if not ok:
        return False, str(msg)
    update_runtime(
        imap_host=spec["imap_host"],
        imap_port=spec["imap_port"],
        imap_user=addr,
        imap_password=pw,
        imap_verified=True,
        smtp_reply_to=addr,
        graph_skipped=True,
        graph_verified=False,
        ms_device_code="",
        ms_user_code="",
        ms_verify_url="",
    )
    return True, f"Replies will land in {addr}. Outbound mail stays on the work address, with Reply-To set to Gmail."
