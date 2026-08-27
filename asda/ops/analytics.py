"""Daily snapshots, funnel numbers, and human-readable activity for the CBO desk."""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timezone

from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.models.lead import PIPELINE_COLUMNS
from asda.ops.playbook import load_playbook
from asda.runtime import load_runtime

EVENT_LABELS = {
    "lead.ingested": "Added a lead",
    "lead.deduped": "Merged a duplicate",
    "research.started": "Started research",
    "research.completed": "Finished research",
    "lead.scored": "Scored a lead",
    "lead.suppressed": "Skipped (not a fit)",
    "content.generated": "Wrote copy",
    "approval.requested": "Asked for a look",
    "approval.granted": "Approved",
    "approval.rejected": "Rejected",
    "email.queued": "Queued an email",
    "email.sent": "Sent an email",
    "email.bounced": "Email bounced",
    "linkedin.queued": "Queued LinkedIn",
    "linkedin.sent": "Sent LinkedIn",
    "reply.received": "Got a reply",
    "reply.classified": "Read a reply",
    "meeting.booked": "Booked a meeting",
    "handoff.completed": "Handed off",
    "learning.updated": "Updated the playbook",
    "safety.paused": "Paused for safety",
    "pipeline.failed": "Hit an error",
    "worker.started": "Employee started",
    "worker.stopped": "Employee stopped",
    "employee.talk": "Talked to ASDA",
    "config.updated": "Updated company config",
    "sequence.step": "Advanced a sequence",
    "snapshot.saved": "Saved a daily snapshot",
    "memory.written": "Remembered",
    "memory.reflected": "Updated memory",
    "cbo.asked": "Asked the CBO",
    "call.queued": "Queued a call",
    "call.placed": "Placed a call",
    "call.completed": "Call finished",
}

FUNNEL_ORDER = [
    ("new", "New"),
    ("researched", "Researched"),
    ("sequenced", "In sequence"),
    ("connected", "Connected"),
    ("replied", "Replied"),
    ("meeting_booked", "Meeting"),
]


def capture_snapshot() -> dict:
    init_db()
    session = get_session()
    try:
        repo = Repository(session)
        metrics = repo.metrics()
        outcomes = repo.outcome_counts()
        payload = {
            **metrics,
            "outcomes": outcomes,
            "emails_sent": outcomes.get("email_sent", 0),
            "li_connects": outcomes.get("linkedin_connect", 0),
        }
        repo.save_snapshot(date.today().isoformat(), payload)
        session.commit()
        return payload
    finally:
        session.close()


def _rel(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        raw = ts.replace("Z", "+00:00")
        then = datetime.fromisoformat(raw)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        seconds = int((datetime.now(timezone.utc) - then.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return ts[11:16] if len(ts) >= 16 else ts
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    if days < 14:
        return f"{days}d ago"
    return then.strftime("%d %b")


def _funnel(by_status: dict[str, int], total: int) -> list[dict]:
    steps = []
    peak = max([total] + list(by_status.values()) + [1])
    for key, label in FUNNEL_ORDER:
        count = int(by_status.get(key, 0))
        steps.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "pct": round(count / total * 100, 1) if total else 0,
                "width": round(count / peak * 100, 1) if peak else 0,
            }
        )
    return steps


def _conversions(metrics: dict, outcomes: dict) -> dict:
    total = int(metrics.get("total_leads") or 0)
    sequenced = int((metrics.get("by_status") or {}).get("sequenced", 0))
    connected = int((metrics.get("by_status") or {}).get("connected", 0))
    replied = int(metrics.get("replies") or 0)
    meetings = int(metrics.get("meetings") or 0)
    emailed = int(outcomes.get("email_sent") or 0)
    li = int(outcomes.get("linkedin_connect") or outcomes.get("linkedin_sent") or 0)
    touched = max(emailed, sequenced + connected + replied + meetings)
    return {
        "emails_sent": emailed,
        "li_connects": li,
        "reply_rate": round(replied / touched * 100, 1) if touched else 0,
        "meeting_rate": round(meetings / max(replied, 1) * 100, 1) if replied else 0,
        "meetings_per_100": metrics.get("meetings_per_100") or 0,
    }


def _wow(snapshots: list[dict]) -> dict:
    if len(snapshots) < 2:
        return {"leads": 0, "replies": 0, "meetings": 0}
    newest, older = snapshots[0], snapshots[-1]
    return {
        "leads": int(newest.get("total_leads") or 0) - int(older.get("total_leads") or 0),
        "replies": int(newest.get("replies") or 0) - int(older.get("replies") or 0),
        "meetings": int(newest.get("meetings") or 0) - int(older.get("meetings") or 0),
    }


def _month_window() -> tuple[datetime, datetime, str, int, int]:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        end = start.replace(year=now.year + 1, month=1)
    else:
        end = start.replace(month=now.month + 1)
    label = now.strftime("%B %Y")
    days_in = monthrange(now.year, now.month)[1]
    return start, end, label, now.day, days_in


def _quota(label: str, actual: int, target: int, hint: str = "", sub: str = "") -> dict:
    target = max(int(target or 0), 0)
    actual = max(int(actual or 0), 0)
    pct = min(100.0, round(actual / target * 100, 1)) if target else 0
    start, _, _, day, days_in = _month_window()
    _ = start
    remaining_days = max(days_in - day + 1, 1)
    pace = round(target * (day / days_in)) if days_in else target
    needed = max(0, target - actual)
    per_day = int((needed + remaining_days - 1) // remaining_days) if needed else 0
    if actual >= target and target:
        tone, pace_label = "hit", "Hit"
    elif actual >= pace:
        tone, pace_label = "on", "On pace"
    else:
        tone, pace_label = "behind", "Behind"
    return {
        "label": label,
        "hint": hint,
        "sub": sub,
        "actual": actual,
        "target": target,
        "pct": pct,
        "pace": pace,
        "needed": needed,
        "per_day": per_day,
        "tone": tone,
        "pace_label": pace_label,
    }


def scoreboard(actuals: dict | None = None) -> dict:
    """Monthly target vs actual for the Home desk."""
    start, end, label, day, days_in = _month_window()
    if actuals is None:
        init_db()
        session = get_session()
        try:
            actuals = Repository(session).month_actuals(start, end)
        finally:
            session.close()
    rt = load_runtime()
    outreach = _quota(
        "Outreach",
        actuals["outreach"],
        rt.target_outreach,
        hint="People emailed or reached on LinkedIn this month",
        sub=f"{actuals['emails']} emails · {actuals['linkedin']} LinkedIn",
    )
    replies = _quota(
        "Replies",
        actuals["replies"],
        rt.target_replies,
        hint="Conversations started",
    )
    meetings = _quota(
        "Calls booked",
        actuals["meetings"],
        rt.target_meetings,
        hint="Meetings requested",
    )
    rows = [outreach, replies, meetings]
    behind = sum(1 for r in rows if r["tone"] == "behind")
    hit = all(r["tone"] == "hit" for r in rows if r["target"])
    if hit:
        overall = "Hit"
    elif behind:
        overall = "Behind pace"
    else:
        overall = "On pace"
    return {
        "period": label,
        "day": day,
        "days_in": days_in,
        "overall": overall,
        "rows": rows,
        "targets": {
            "outreach": rt.target_outreach,
            "replies": rt.target_replies,
            "meetings": rt.target_meetings,
        },
        "actuals": actuals,
    }


def _memories() -> dict:
    try:
        from asda.memory.store import recent

        items = recent(16)
    except Exception:
        items = []
    groups = {"preference": [], "playbook": [], "person": [], "fact": [], "goal": [], "episode": []}
    for item in items:
        bucket = item.get("kind") if item.get("kind") in groups else "fact"
        groups.setdefault(bucket, []).append(item)
    return {
        "entries": items,
        "preferences": groups.get("preference") or [],
        "playbook": groups.get("playbook") or [],
        "people": groups.get("person") or [],
        "facts": groups.get("fact") or [],
        "goals": groups.get("goal") or [],
        "episodes": groups.get("episode") or [],
    }


def dashboard_context() -> dict:
    init_db()
    session = get_session()
    try:
        repo = Repository(session)
        metrics = repo.metrics()
        outcomes = repo.outcome_counts()
        start, end, _, _, _ = _month_window()
        month = repo.month_actuals(start, end)
        leads = repo.list_leads(limit=80)
        by: dict[str, list] = {}
        for lead in leads:
            by.setdefault(lead.status.value, []).append(lead)
        board = []
        for key, label in PIPELINE_COLUMNS:
            items = by.get(key, [])
            count = int((metrics.get("by_status") or {}).get(key, len(items)))
            if key in {"closed", "suppressed"} and count == 0:
                continue
            shown = items[:8]
            board.append(
                {
                    "key": key,
                    "label": label,
                    "count": count,
                    "leads": shown,
                    "more": max(0, count - len(shown)),
                }
            )
        events = []
        for e in repo.recent_events(40):
            payload = e.get("payload") or {}
            who = payload.get("lead_name") or payload.get("company") or ""
            kind = EVENT_LABELS.get(e.get("type") or "", e.get("type") or "Update")
            summary = payload.get("summary") or ""
            label = kind if e.get("type") == "employee.talk" and summary else (summary or kind)
            if e.get("type") == "employee.talk" and summary:
                label = f"Talked — {summary}"
            events.append(
                {
                    **e,
                    "label": label,
                    "who": who,
                    "when": _rel(e.get("ts")),
                }
            )
        snapshots = list(reversed(repo.snapshots(14)))
        snapshots_newest_first = list(repo.snapshots(14))
        playbook = load_playbook()
        insight = repo.latest_insight()
        industries = Counter(
            (l.company.industry or l.company.location or "unknown") for l in leads if l.score >= 70
        )
        return {
            "metrics": metrics,
            "outcomes": outcomes,
            "board": board,
            "leads": leads,
            "events": events,
            "patterns": repo.winning_patterns(8),
            "insight": insight,
            "snapshots": snapshots_newest_first,
            "funnel": _funnel(metrics.get("by_status") or {}, metrics.get("total_leads") or 0),
            "conversions": _conversions(metrics, outcomes),
            "wow": _wow(snapshots_newest_first),
            "playbook": playbook,
            "top_icp": industries.most_common(5),
            "scoreboard": scoreboard(month),
            "memories": _memories(),
        }
    finally:
        session.close()
