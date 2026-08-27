"""Poll IMAP for inbound replies and match them to sequenced leads."""

from __future__ import annotations

import email
import imaplib
import logging
from email.header import decode_header
from email.message import Message

from asda.runtime import effective

logger = logging.getLogger(__name__)


def _decode(value: str | bytes | None) -> str:
    if not value:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _header(msg: Message, name: str) -> str:
    raw = msg.get(name, "")
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return " ".join(out)


def _body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def fetch_unseen(limit: int = 30) -> list[dict]:
    cfg = effective()
    if cfg.graph_verified:
        try:
            from asda.modules.graph_mail import fetch_unseen as graph_fetch

            return graph_fetch(limit=limit)
        except Exception:
            logger.exception("Graph inbox poll failed")
            return []
    if not cfg.imap_verified or not cfg.imap_host or not cfg.imap_user:
        return []
    mail = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        mail.login(cfg.imap_user, cfg.imap_password)
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        ids = (data[0] or b"").split()[-limit:]
        messages = []
        for uid in ids:
            _, raw = mail.fetch(uid, "(RFC822)")
            if not raw or not raw[0] or not isinstance(raw[0], tuple):
                continue
            msg = email.message_from_bytes(raw[0][1])
            sender = _header(msg, "From")
            addr = sender
            if "<" in sender and ">" in sender:
                addr = sender.split("<", 1)[1].split(">", 1)[0]
            messages.append(
                {
                    "from": addr.strip().lower(),
                    "subject": _header(msg, "Subject"),
                    "body": _body(msg)[:4000],
                }
            )
        return messages
    except Exception:
        logger.exception("IMAP poll failed")
        return []
    finally:
        try:
            mail.logout()
        except Exception:
            pass
