"""Persistent playbook the learning loop writes and outreach reads.

This is how ASDA grows like an employee: Sunday study produces rules,
then every new sequence is written against those rules.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asda.config import get_settings

_EMPTY = {
    "prompt_updates": [],
    "scoring_updates": {},
    "updated_at": None,
    "periods": [],
}


def _path() -> Path:
    p = get_settings().data_dir / "playbook.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_playbook() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return dict(_EMPTY)
    try:
        data = json.loads(path.read_text())
        return {**_EMPTY, **data}
    except Exception:
        return dict(_EMPTY)


def save_playbook(data: dict[str, Any]) -> dict[str, Any]:
    payload = {**_EMPTY, **data, "updated_at": datetime.now(timezone.utc).isoformat()}
    _path().write_text(json.dumps(payload, indent=2))
    return payload


def merge_learning(
    prompt_updates: list[str] | None = None,
    scoring_updates: dict[str, float] | None = None,
    period: str = "",
) -> dict[str, Any]:
    current = load_playbook()
    seen = {str(x).strip().lower() for x in current["prompt_updates"]}
    for item in prompt_updates or []:
        text = str(item).strip()
        if text and text.lower() not in seen:
            current["prompt_updates"].append(text)
            seen.add(text.lower())
    current["prompt_updates"] = current["prompt_updates"][-40:]
    scores = dict(current.get("scoring_updates") or {})
    for key, value in (scoring_updates or {}).items():
        try:
            scores[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    current["scoring_updates"] = scores
    if period:
        periods = list(current.get("periods") or [])
        if period not in periods:
            periods.append(period)
        current["periods"] = periods[-24:]
    return save_playbook(current)


def playbook_block() -> str:
    data = load_playbook()
    lines: list[str] = []
    for rule in data.get("prompt_updates") or []:
        lines.append(f"- {rule}")
    for key, value in (data.get("scoring_updates") or {}).items():
        lines.append(f"- scoring:{key}={value}")
    if not lines:
        return ""
    return "PLAYBOOK (earned from prior weeks — follow these)\n" + "\n".join(lines)
