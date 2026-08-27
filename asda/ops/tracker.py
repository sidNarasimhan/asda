"""Per-lead tracker: what went out, what is queued, conversations, CBO holds."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from asda.models.content import GeneratedContent
from asda.models.lead import Lead


def _when(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = int((dt - now).total_seconds())
    if delta <= 0:
        return "due now"
    if delta < 3600:
        return f"in {max(delta, 0) // 60}m"
    if delta < 86400:
        return f"in {delta // 3600}h"
    return f"in {delta // 86400}d"


def _past(dt: datetime | None) -> bool:
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= datetime.now(timezone.utc)


def tracker(lead: Lead, content: GeneratedContent | None = None) -> dict[str, Any]:
    st = lead.sequence_state
    emails_out: list[dict[str, Any]] = []
    planned = list((content.emails if content else []) or [])
    for item in planned:
        sent = st.email_step >= item.step
        if sent:
            status = "sent"
        elif st.email_dropped or st.paused:
            status = "dropped"
        else:
            status = "scheduled"
        emails_out.append(
            {
                "channel": "email",
                "step": item.step,
                "label": item.subject or f"Email {item.step}",
                "preview": (item.body or "")[:140],
                "status": status,
                "when": "" if sent else _when(st.next_email_at) if item.step == st.email_step + 1 or (st.email_step == 0 and item.step == 1) else f"+{item.delay_days}d",
            }
        )
    if lead.email and not planned:
        if st.email_step >= 1:
            emails_out.append({"channel": "email", "step": st.email_step, "label": f"Email {st.email_step}", "preview": "", "status": "sent", "when": ""})
        elif st.next_email_at:
            emails_out.append({"channel": "email", "step": 1, "label": "First email", "preview": "", "status": "scheduled", "when": _when(st.next_email_at)})
        elif lead.status.value in {"new", "researched"}:
            emails_out.append({"channel": "email", "step": 0, "label": "First email", "preview": "after research/copy", "status": "queued", "when": ""})

    li_msgs = list((content.linkedin.messages if content and content.linkedin else []) or [])
    linkedin: list[dict[str, Any]] = []
    if lead.linkedin_url:
        stage = st.linkedin_stage or "idle"
        if stage in {"", "idle"}:
            linkedin.append({"channel": "linkedin", "step": 0, "label": "Connection request", "preview": (content.linkedin.connection_note if content and content.linkedin else ""), "status": "queued" if not st.paused else "paused", "when": _when(st.next_linkedin_at)})
        elif stage in {"connect_sent", "delegated"}:
            linkedin.append({"channel": "linkedin", "step": 0, "label": "Connection request", "preview": "invite sent", "status": "sent", "when": "waiting for accept"})
        if st.linkedin_connected or stage in {"connected", "messaging", "done"}:
            linkedin.append({"channel": "linkedin", "step": 0, "label": "Invite accepted", "preview": "", "status": "sent", "when": ""})
        for i, msg in enumerate(li_msgs, start=1):
            sent = st.linkedin_messages_sent >= i
            linkedin.append(
                {
                    "channel": "linkedin",
                    "step": i,
                    "label": f"Message {i}",
                    "preview": (msg.body if hasattr(msg, "body") else str(msg))[:140],
                    "status": "sent" if sent else ("paused" if st.paused else "scheduled"),
                    "when": _when(st.next_linkedin_at) if (not sent and st.linkedin_messages_sent + 1 == i) else "",
                }
            )
        if not li_msgs and st.linkedin_messages_sent:
            linkedin.append({"channel": "linkedin", "step": st.linkedin_messages_sent, "label": f"Message {st.linkedin_messages_sent}", "preview": "", "status": "sent", "when": ""})

    phone = []
    if lead.phone:
        stage = st.phone_stage or "idle"
        label = {
            "idle": "Call (only if mail and LinkedIn stay quiet)",
            "queued": "Call queued",
            "calling": "Call in flight",
            "done": "Call completed",
            "skipped": "Call skipped (conversation on another channel)",
        }.get(stage, "Phone")
        phone.append(
            {
                "channel": "phone",
                "step": 0,
                "label": label,
                "preview": lead.phone,
                "status": stage,
                "when": _when(st.next_call_at) if stage == "queued" else "",
            }
        )

    thread = list(st.thread or [])
    waiting = lead.status.value == "awaiting_approval" or bool(st.paused and "cbo" in (st.reason or "").lower())
    next_mail = _when(st.next_email_at) if lead.email and not st.email_replied else ("replied" if st.email_replied else "")
    next_li = _when(st.next_linkedin_at) if lead.linkedin_url and not st.linkedin_replied else ("replied" if st.linkedin_replied else "")
    return {
        "status": lead.status.value,
        "paused": st.paused,
        "reason": st.reason,
        "waiting_on_cbo": waiting,
        "email": emails_out,
        "linkedin": linkedin,
        "phone": phone,
        "thread": thread[-20:],
        "next_email": next_mail,
        "next_linkedin": next_li,
        "email_step": st.email_step,
        "linkedin_stage": st.linkedin_stage or "idle",
        "linkedin_messages_sent": st.linkedin_messages_sent,
        "phone_stage": st.phone_stage or "idle",
        "next_call": _when(st.next_call_at) if st.phone_stage == "queued" else st.phone_stage,
        "channels": {
            "email": bool(lead.email),
            "linkedin": bool(lead.linkedin_url),
            "phone": bool(lead.phone),
        },
        "company_key": (lead.company.domain or lead.company.name or "").lower(),
    }


def summary_line(tr: dict[str, Any]) -> str:
    bits = []
    if tr["channels"].get("email"):
        bits.append(f"mail {tr.get('next_email') or tr['email_step']}")
    if tr["channels"].get("linkedin"):
        bits.append(f"li {tr.get('linkedin_stage')}")
    if tr["channels"].get("phone"):
        bits.append(f"call {tr.get('phone_stage') or 'idle'}")
    if tr.get("waiting_on_cbo"):
        bits.append("waiting on you")
    return " · ".join(bits)
