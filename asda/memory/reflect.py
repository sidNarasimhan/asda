"""Consolidate episodes into durable facts, playbook rules, and decay noise."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from asda.db.models import MemoryRow
from asda.db.session import get_session, init_db
from asda.memory.store import remember


class Reflection(BaseModel):
    summary: str = ""
    facts: list[str] = Field(default_factory=list)
    playbook: list[str] = Field(default_factory=list)
    mistakes: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reflect(llm: Any | None = None) -> dict[str, Any]:
    """Decay stale episodes, promote strong ones, optionally ask the LLM to name rules."""
    init_db()
    session = get_session()
    decayed = 0
    promoted = 0
    episodes: list[MemoryRow] = []
    try:
        rows = list(session.scalars(select(MemoryRow).where(MemoryRow.active == 1)))
        now = _now()
        for row in rows:
            then = row.updated_at or row.created_at or now
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            age = (now - then.astimezone(timezone.utc)).total_seconds() / 86400
            if row.kind == "episode":
                episodes.append(row)
                if age > 21 and (row.uses or 0) < 2:
                    row.importance = max(0.05, float(row.importance or 0.4) * 0.85)
                    decayed += 1
                if age > 45 and float(row.importance or 0) < 0.2:
                    row.active = 0
                    decayed += 1
                elif (row.uses or 0) >= 3 and float(row.importance or 0) >= 0.6:
                    promoted += 1
            elif row.kind in {"fact", "playbook", "preference"} and (row.uses or 0) >= 1:
                # Used memories get a little stronger — this is the evolution.
                row.importance = min(1.0, float(row.importance or 0.5) + 0.01 * min(row.uses or 0, 5))
        session.commit()
        episode_texts = [r.text for r in episodes[-40:]]
    finally:
        session.close()

    extracted = Reflection()
    if episode_texts and llm is not None:
        try:
            extracted = llm.parse(
                "You consolidate an SDR's memory. Promote durable facts and playbook rules. "
                "Drop noise. Short lines. No generic sales advice.",
                "EPISODES:\n- " + "\n- ".join(episode_texts[-30:]),
                Reflection,
            )
        except Exception:
            extracted = Reflection(summary=f"Held {len(episode_texts)} episodes.")
    elif episode_texts:
        extracted = Reflection(summary=f"Held {len(episode_texts)} episodes. Decayed {decayed}.")

    written = 0
    for line in extracted.facts:
        if remember(line, kind="fact", source="reflect", importance=0.7, event=False):
            written += 1
    for line in extracted.playbook:
        if remember(line, kind="playbook", source="reflect", importance=0.75, event=False):
            written += 1
            from asda.ops.playbook import merge_learning

            merge_learning([line], period=_now().strftime("%Y-W%W"))
    for line in extracted.mistakes:
        if remember(line, kind="mistake", source="reflect", importance=0.8, event=False):
            written += 1
    for line in extracted.people:
        if remember(line, kind="person", source="reflect", importance=0.65, event=False):
            written += 1

    from asda.models.events import EventType
    from asda.ops.activity import log

    summary = extracted.summary or f"Reflected. +{written} durable memories, decayed {decayed}."
    log(EventType.MEMORY_REFLECTED, summary=summary[:180], written=written, decayed=decayed)
    remember(
        summary,
        kind="episode",
        subject="reflection",
        source="reflect",
        importance=0.4,
        event=False,
        tags=["reflection"],
    )
    return {
        "summary": summary,
        "written": written,
        "decayed": decayed,
        "promoted": promoted,
        "episodes": len(episode_texts),
    }
