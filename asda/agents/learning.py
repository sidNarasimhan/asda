"""Learning Loop — mines outcomes, stores winning patterns, surfaces weekly insights.

Heuristic mining always runs (no API bill). The frontier model then names the
patterns in English and writes playbook rules the content agent will follow.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from asda.config import get_settings
from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.llm.client import LLMClient, get_llm
from asda.llm.prompts import LEARNING_SYSTEM
from asda.models.lead import LeadStatus
from asda.models.outcomes import LearningInsight, Pattern
from asda.ops.playbook import merge_learning, playbook_block


class LearningPayload(BaseModel):
    summary: str = ""
    patterns: list[Pattern] = Field(default_factory=list)
    prompt_updates: list[str] = Field(default_factory=list)
    scoring_updates: dict[str, float] = Field(default_factory=dict)


class LearningAgent:
    name = "learning"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm()

    def run(self) -> LearningInsight:
        init_db()
        session = get_session()
        try:
            repo = Repository(session)
            leads = repo.list_leads(limit=1000)
            snapshot = _snapshot(leads)
            heuristics = _heuristic_patterns(leads)
            try:
                payload = self.llm.parse(
                    LEARNING_SYSTEM,
                    (
                        "Analyze these outcome stats and sample notes. "
                        "Extract winning patterns with estimated lift. "
                        "Write prompt_updates as rules a copywriter must follow next week.\n\n"
                        f"{snapshot}\n\nHEURISTIC CANDIDATES:\n"
                        + "\n".join(f"- {p.kind}: {p.text} lift={p.lift}" for p in heuristics[:12])
                    ),
                    LearningPayload,
                    model=get_settings().model_frontier,
                )
            except Exception:
                payload = LearningPayload(
                    summary=_heuristic_summary(leads),
                    patterns=heuristics,
                    prompt_updates=[p.text for p in heuristics[:5]],
                )
            patterns = list(payload.patterns or [])
            seen = {p.text.strip().lower() for p in patterns}
            for item in heuristics:
                if item.text.strip().lower() not in seen:
                    patterns.append(item)
            if patterns:
                repo.save_patterns(patterns)
            period = datetime.now(timezone.utc).strftime("%Y-W%W")
            merge_learning(payload.prompt_updates, payload.scoring_updates, period)
            try:
                from asda.memory.store import remember

                for rule in (payload.prompt_updates or [])[:8]:
                    remember(rule, kind="playbook", source="learn", importance=0.8, event=False)
                if payload.summary:
                    remember(payload.summary, kind="episode", source="learn", subject="weekly", importance=0.5, event=False)
            except Exception:
                pass
            insight = LearningInsight(
                period=period,
                summary=payload.summary or _heuristic_summary(leads),
                patterns=patterns,
                prompt_updates=payload.prompt_updates,
                scoring_updates=payload.scoring_updates,
            )
            repo.save_insight(insight.period, insight.model_dump(mode="json"))
            session.commit()
            from asda.models.events import EventType
            from asda.ops.activity import log

            log(
                EventType.LEARNING_UPDATED,
                summary=(insight.summary or "Updated the playbook")[:180],
                period=insight.period,
                patterns=len(insight.patterns),
            )
            return insight
        finally:
            session.close()

    def patterns_block(self) -> str:
        init_db()
        session = get_session()
        try:
            patterns = Repository(session).winning_patterns()
            lines = [f"- [{p.kind}] {p.text} (lift={p.lift:.2f}, n={p.sample_size})" for p in patterns]
            extra = playbook_block()
            if extra:
                lines.append(extra)
            return "\n".join(lines) if lines else extra
        finally:
            session.close()


def _snapshot(leads: list) -> str:
    statuses = Counter(l.status.value if isinstance(l.status, LeadStatus) else l.status for l in leads)
    outcomes = Counter()
    notes: list[str] = []
    for lead in leads:
        for o in lead.outcomes:
            outcomes[o.kind] += 1
        if lead.research_card and lead.score >= 70:
            notes.append(
                f"{lead.full_name} score={lead.score} hooks={lead.research_card.personalization_hooks[:2]}"
            )
    return (
        f"status_counts={dict(statuses)}\n"
        f"outcome_counts={dict(outcomes)}\n"
        f"high_score_samples={notes[:30]}\n"
        f"n_leads={len(leads)}"
    )


_WON = {LeadStatus.REPLIED, LeadStatus.MEETING_BOOKED, LeadStatus.CLOSED}


def _heuristic_summary(leads: list) -> str:
    won = [l for l in leads if l.status in _WON]
    meetings = [l for l in leads if l.status is LeadStatus.MEETING_BOOKED]
    if not leads:
        return "No leads yet — I'll start keeping a playbook once we work a list."
    if not won:
        return (
            f"Worked {len(leads)} leads, no replies yet. "
            "I'll keep the copy tight and log what gets through once conversations start."
        )
    top = Counter((l.company.industry or "unknown") for l in won).most_common(1)
    industry = top[0][0] if top else "the current ICP"
    return (
        f"{len(won)} conversations from {len(leads)} leads, {len(meetings)} meetings. "
        f"Strongest pocket so far: {industry}."
    )


def _heuristic_patterns(leads: list) -> list[Pattern]:
    buckets: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"pos": 0, "n": 0})
    won_n = 0
    for lead in leads:
        won = lead.status in _WON
        won_n += int(won)
        keys: list[tuple[str, str]] = []
        if lead.company.industry:
            keys.append(("icp_attr", lead.company.industry))
        loc = lead.company.location or (lead.raw_data or {}).get("City") or ""
        if loc:
            keys.append(("icp_attr", str(loc)))
        if lead.title:
            keys.append(("icp_attr", lead.title))
        if lead.research_card:
            for hook in (lead.research_card.personalization_hooks or [])[:2]:
                if hook:
                    keys.append(("hook", str(hook)[:80]))
        for kind, text in keys:
            buckets[(kind, text)]["n"] += 1
            buckets[(kind, text)]["pos"] += int(won)
    baseline = (won_n / len(leads)) if leads else 0
    out: list[Pattern] = []
    for (kind, text), stats in buckets.items():
        if stats["n"] < 2:
            continue
        rate = stats["pos"] / stats["n"]
        lift = (rate / baseline) if baseline else (1.0 + rate)
        if lift < 1.15 and stats["pos"] == 0:
            continue
        out.append(
            Pattern(
                kind=kind,
                text=text,
                lift=round(float(lift), 2),
                sample_size=stats["n"],
                notes=f"{stats['pos']}/{stats['n']} converted",
            )
        )
    out.sort(key=lambda p: (p.lift, p.sample_size), reverse=True)
    return out[:12]
