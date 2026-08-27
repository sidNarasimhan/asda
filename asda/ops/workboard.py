"""Channel workboards — mail + LinkedIn pipelines the CBO can glance at."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.models.lead import Lead
from asda.ops.heartbeat import snapshot as now_snapshot
from asda.ops.worker import worker_status


def _card(lead: Lead, sub: str) -> dict[str, Any]:
    st = lead.sequence_state
    nxt = st.next_email_at or st.next_linkedin_at or st.next_action_at
    when = ""
    if nxt:
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        delta = int((nxt - datetime.now(timezone.utc)).total_seconds())
        if delta <= 0:
            when = "due now"
        elif delta < 3600:
            when = f"in {max(delta, 0) // 60}m"
        elif delta < 86400:
            when = f"in {delta // 3600}h"
        else:
            when = f"in {delta // 86400}d"
    return {
        "id": lead.id,
        "name": lead.full_name or "—",
        "company": lead.company.name,
        "title": lead.title,
        "score": lead.score,
        "sub": sub,
        "when": when,
        "href": f"/leads/{lead.id}",
        "status": lead.status.value,
    }


def _email_lane(lead: Lead) -> tuple[str, str] | None:
    if not lead.email:
        return None
    st = lead.sequence_state
    thread = [m for m in (st.thread or []) if m.get("channel") == "email"]
    if st.email_replied or thread:
        last = (thread[-1].get("text") if thread else st.last_inbound) or "conversation"
        return "conversations", (last or "")[:80]
    if st.email_step >= 1:
        return "sent", f"email {st.email_step} sent"
    if lead.status.value in {"new", "researched", "awaiting_approval"}:
        return "queued", "waiting on research/copy" if lead.status.value != "researched" else "ready to send"
    if lead.status.value in {"sequenced", "connected", "replied", "meeting_booked"}:
        return "queued", "next follow-up queued"
    return None


def _li_lane(lead: Lead) -> tuple[str, str] | None:
    if not lead.linkedin_url:
        return None
    st = lead.sequence_state
    stage = st.linkedin_stage or "idle"
    thread = [m for m in (st.thread or []) if m.get("channel") == "linkedin"]
    if st.linkedin_replied or thread:
        last = (thread[-1].get("text") if thread else st.last_inbound) or "conversation"
        return "conversations", (last or "")[:80]
    if stage in {"messaging", "done"} or st.linkedin_messages_sent >= 1:
        n = st.linkedin_messages_sent or 1
        return "first_message", f"{n} message{'s' if n != 1 else ''} sent"
    if st.linkedin_connected or stage == "connected" or lead.status.value == "connected":
        return "accepted", "accepted, first message queued"
    if stage in {"connect_sent", "delegated"}:
        return "connect_sent", "invite sent"
    if lead.status.value in {"sequenced", "researched"} and stage in {"", "idle"}:
        return "connect_sent", "invite queued"
    return None


def _bucket(leads: list[Lead], kind: str) -> dict[str, list[dict[str, Any]]]:
    if kind == "email":
        keys = ("queued", "sent", "conversations")
        pick = _email_lane
    else:
        keys = ("connect_sent", "accepted", "first_message", "conversations")
        pick = _li_lane
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in keys}
    for lead in leads:
        lane = pick(lead)
        if not lane:
            continue
        col, sub = lane
        if col not in out:
            continue
        out[col].append(_card(lead, sub))
    return out


def workboard(worker: dict | None = None) -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        leads = Repository(session).list_leads(limit=500)
    finally:
        session.close()
    mail = _bucket(leads, "email")
    linkedin = _bucket(leads, "linkedin")
    calls = {"queued": [], "calling": [], "done": []}
    for lead in leads:
        if not lead.phone:
            continue
        stage = lead.sequence_state.phone_stage or "idle"
        if stage == "queued":
            calls["queued"].append(_card(lead, "call after mail and LinkedIn"))
        elif stage == "calling":
            calls["calling"].append(_card(lead, "in flight"))
        elif stage == "done":
            calls["done"].append(_card(lead, "completed"))

    def _cols(mapping: dict[str, list], labels: list[tuple[str, str]]) -> list[dict]:
        return [
            {"key": key, "label": label, "count": len(mapping.get(key) or []), "leads": (mapping.get(key) or [])[:12]}
            for key, label in labels
        ]

    wst = worker if worker is not None else worker_status()
    return {
        "now": now_snapshot(wst),
        "email": _cols(
            mail,
            [("queued", "To send"), ("sent", "On mail"), ("conversations", "Conversations")],
        ),
        "linkedin": _cols(
            linkedin,
            [
                ("connect_sent", "Invite sent"),
                ("accepted", "Accepted"),
                ("first_message", "First message"),
                ("conversations", "Conversations"),
            ],
        ),
        "counts": {
            "email_queued": len(mail["queued"]),
            "email_sent": len(mail["sent"]),
            "email_convos": len(mail["conversations"]),
            "li_invites": len(linkedin["connect_sent"]),
            "li_accepted": len(linkedin["accepted"]),
            "li_messages": len(linkedin["first_message"]),
            "li_convos": len(linkedin["conversations"]),
            "calls_queued": len(calls["queued"]),
            "calls_live": len(calls["calling"]),
        },
        "calls": _cols(
            calls,
            [("queued", "Calls queued"), ("calling", "Calling"), ("done", "Called")],
        ),
    }
