"""Worker heartbeat — what the 24/7 employee is doing right now."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asda.config import get_settings

JOBS: list[dict[str, Any]] = [
    {"id": "due_steps", "label": "Due mail & LinkedIn steps", "every": "5 min", "seconds": 300},
    {"id": "new_leads", "label": "Research + write new leads", "every": "5 min", "seconds": 300},
    {"id": "email_inbox", "label": "Read mailbox for replies", "every": "3 min", "seconds": 180},
    {"id": "li_inbox", "label": "Read LinkedIn inbox", "every": "15 min", "seconds": 900},
    {"id": "csv_inbox", "label": "Watch CSV drop folder", "every": "2 min", "seconds": 120},
    {"id": "agent_tick", "label": "Agent tick (recall → act → remember)", "every": "5 min", "seconds": 300},
    {"id": "learn", "label": "Sunday playbook rewrite", "every": "Sun 02:00", "seconds": None},
    {"id": "cbo_brief", "label": "Monday CBO brief", "every": "Mon 08:00", "seconds": None},
    {"id": "snapshot", "label": "Daily numbers snapshot", "every": "20:00", "seconds": None},
]

_LABEL = {j["id"]: j["label"] for j in JOBS}


def _path() -> Path:
    p = get_settings().data_dir / "heartbeat.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def beat(job: str, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    current = load()
    last = dict(current.get("last") or {})
    stamp = _now().isoformat()
    last[job] = {"ts": stamp, "status": status, "detail": str(detail)[:240]}
    history = list(current.get("history") or [])
    history.append({"job": job, "ts": stamp, "status": status, "detail": str(detail)[:240]})
    payload = {
        "ts": stamp,
        "job": job,
        "status": status,
        "detail": str(detail)[:240],
        "last": last,
        "history": history[-30:],
        **extra,
    }
    _path().write_text(json.dumps(payload, indent=2))
    return payload


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        raw = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _ago(ts: str | None) -> str:
    then = _parse(ts)
    if not then:
        return "never"
    seconds = int((_now() - then).total_seconds())
    if seconds < 20:
        return "just now"
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _until(seconds: int | None, last_ts: str | None) -> str:
    if not seconds:
        return "on schedule"
    then = _parse(last_ts)
    if not then:
        return f"every {seconds // 60}m"
    remain = seconds - int((_now() - then).total_seconds())
    if remain <= 0:
        return "due now"
    if remain < 90:
        return f"in {remain}s"
    return f"in {remain // 60}m"


def snapshot(worker: dict | None = None) -> dict[str, Any]:
    """UI-facing 'what am I doing' block."""
    data = load()
    running = bool((worker or {}).get("running"))
    ts = data.get("ts")
    age = None
    then = _parse(ts)
    if then:
        age = int((_now() - then).total_seconds())
    stale = running and (age is None or age > 12 * 60)
    job = data.get("job") or ""
    status = data.get("status") or ""
    label = _LABEL.get(job, job or "idle")

    if not running:
        headline = "I'm stopped"
        detail = "Start the employee to keep sequences, inbox, and research running overnight."
        state = "stopped"
    elif status == "running":
        headline = f"Right now — {label}"
        detail = data.get("detail") or "Working."
        state = "busy"
    elif stale:
        headline = "Process is up, last tick is stale"
        detail = f"Last heartbeat {_ago(ts)}. I'll restart on the next page load if needed."
        state = "stale"
    elif ts:
        headline = f"Idle · last {label.lower()}"
        detail = (data.get("detail") or "Waiting for the next sweep.") + f" · {_ago(ts)}"
        state = "idle"
    else:
        headline = "Employee is on · waiting for first tick"
        detail = "Due-step sweep runs every 5 minutes."
        state = "idle"

    last = data.get("last") or {}
    jobs = []
    for spec in JOBS:
        entry = last.get(spec["id"]) or {}
        jobs.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "every": spec["every"],
                "last": _ago(entry.get("ts")),
                "next": _until(spec.get("seconds"), entry.get("ts")),
                "status": entry.get("status") or "",
                "detail": entry.get("detail") or "",
            }
        )
    return {
        "headline": headline,
        "detail": detail,
        "state": state,
        "job": job,
        "status": status,
        "ago": _ago(ts),
        "stale": stale,
        "running": running,
        "jobs": jobs,
        "history": list(reversed(data.get("history") or []))[:8],
    }
