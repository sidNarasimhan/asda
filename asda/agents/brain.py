"""The agent loop: perceive → recall → plan → act → remember.

Hands (SMTP, PhantomBuster, sequences) stay deterministic. This brain decides
what matters, writes memory, and will not contact people memory has blocked.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from asda.llm.client import FakeLLM, LLMClient, get_llm
from asda.memory.reflect import reflect
from asda.memory.store import memory_block, recent, remember, search, seed_if_empty

SYSTEM = """You are ASDA, an autonomous SDR with evolving memory.
Each tick you: recall, decide the smallest useful next actions, then remember what you learned.
Never invent metrics. Never contact someone tagged do_not_contact.
Prefer unique, person-specific work over volume.
Tools:
- reflect: consolidate episodes into facts/playbook
- harvest: turn recent events into memories
- hold: mark a lead do-not-contact
- note: write a fact/person/playbook memory
- noop: nothing extra this tick (hands already run due-steps/inbox)
"""


class MemoryDraft(BaseModel):
    kind: str = "fact"
    text: str = ""
    lead_id: str = ""
    subject: str = ""
    importance: float = 0.6


class TickAction(BaseModel):
    tool: str = "noop"
    lead_id: str = ""
    note: str = ""


class TickPlan(BaseModel):
    thought: str = ""
    goal: str = ""
    actions: list[TickAction] = Field(default_factory=list)
    memories: list[MemoryDraft] = Field(default_factory=list)


def perceive() -> dict[str, Any]:
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from asda.ops.workboard import workboard
    from asda.runtime import effective

    session = get_session()
    try:
        stats = Repository(session).metrics()
        events = Repository(session).recent_events(12)
    finally:
        session.close()
    cfg = effective()
    board = workboard()
    mem = recent(8)
    return {
        "stats": stats,
        "events": [
            {
                "type": e.get("type"),
                "summary": (e.get("payload") or {}).get("summary") or e.get("type"),
                "who": (e.get("payload") or {}).get("lead_name"),
            }
            for e in events
        ],
        "board": board.get("counts"),
        "now": (board.get("now") or {}).get("headline"),
        "live": cfg.live_confirmed and not cfg.dry_run,
        "memory": [{"kind": m["kind"], "text": m["text"][:180]} for m in mem],
        "blocked": [m["text"][:160] for m in search("do not contact", kinds=["preference"], limit=8, mark_used=False)],
    }


_HARVEST_TYPES = {
    "email.sent",
    "linkedin.sent",
    "reply.received",
    "reply.classified",
    "meeting.booked",
    "employee.talk",
    "learning.updated",
    "sequence.step",
    "lead.suppressed",
    "memory.reflected",
}


def harvest_events() -> int:
    """Turn new activity into episodes if we have not stored that event yet."""
    from asda.db.repository import Repository
    from asda.db.session import get_session

    session = get_session()
    try:
        events = Repository(session).recent_events(40)
    finally:
        session.close()
    seen = {(m.get("meta") or {}).get("event_id") for m in recent(80, kinds=["episode"])}
    n = 0
    for e in events:
        eid = e.get("id")
        if not eid or eid in seen:
            continue
        if e.get("type") not in _HARVEST_TYPES:
            continue
        payload = e.get("payload") or {}
        text = payload.get("summary") or e.get("type") or "event"
        who = payload.get("lead_name") or ""
        kind = "episode"
        low = text.lower()
        if e.get("type") == "employee.talk" and any(w in low for w in ("pause", "do not", "don't")):
            kind = "preference"
        remember(
            f"{who + ': ' if who else ''}{text}",
            kind=kind,
            lead_id=e.get("lead_id"),
            subject=who,
            source="harvest",
            meta={"event_id": eid, "event_type": e.get("type")},
            importance=0.45,
            event=False,
        )
        n += 1
    return n


def _heuristic_plan(ctx: dict[str, Any]) -> TickPlan:
    actions: list[TickAction] = [TickAction(tool="harvest", note="fold new events into memory")]
    episodes = sum(1 for m in ctx.get("memory") or [] if m.get("kind") == "episode")
    if episodes >= 2 or (ctx.get("stats") or {}).get("total_leads"):
        actions.append(TickAction(tool="reflect", note="promote what keeps showing up"))
    return TickPlan(
        thought="Hands run sequences and inbox. I harvest and reflect so copy stays unique.",
        goal="Book meetings without generic mail; honour do-not-contact.",
        actions=actions,
    )


def execute(plan: TickPlan, llm: LLMClient | None = None) -> list[str]:
    applied: list[str] = []
    for draft in plan.memories:
        if not (draft.text or "").strip():
            continue
        remember(
            draft.text,
            kind=draft.kind or "fact",
            lead_id=draft.lead_id or None,
            subject=draft.subject,
            importance=draft.importance or 0.6,
            source="brain",
        )
        applied.append(f"remembered [{draft.kind}] {draft.text[:80]}")
    for action in plan.actions:
        tool = (action.tool or "noop").lower()
        if tool in {"noop", "none", ""}:
            continue
        if tool == "harvest":
            n = harvest_events()
            applied.append(f"harvested {n} events")
        elif tool == "reflect":
            use = llm
            prior = [m for m in recent(6) if m.get("subject") == "reflection"]
            if prior:
                use = None  # heuristic decay; LLM reflect next time episodes pile up
            out = reflect(use)
            applied.append(out.get("summary") or "reflected")
        elif tool == "hold" and action.lead_id:
            remember(
                action.note or f"Do not contact lead {action.lead_id}",
                kind="preference",
                lead_id=action.lead_id,
                tags=["do_not_contact"],
                importance=0.95,
                source="brain",
            )
            applied.append("held a lead")
        elif tool == "note" and action.note:
            remember(action.note, kind="fact", lead_id=action.lead_id or None, source="brain")
            applied.append("noted a fact")
    return applied


def tick(llm: LLMClient | None = None) -> dict[str, Any]:
    """One agent cycle. Safe to run every few minutes alongside the hands."""
    seed_if_empty()
    ctx = perceive()
    plan = _heuristic_plan(ctx)
    client = llm
    if client is None:
        try:
            client = get_llm()
        except Exception:
            client = FakeLLM()
    if not isinstance(client, FakeLLM):
        try:
            mem = memory_block(query="playbook preference goal this week", limit=8)
            user = (
                f"STATE: {ctx}\n{mem}\n"
                "Plan this tick. Prefer harvest+reflect. Only hold a lead if memory or events say so."
            )
            from asda.config import get_settings

            plan = client.parse(SYSTEM, user, TickPlan, model=get_settings().model_fast)
            if not plan.actions:
                plan = _heuristic_plan(ctx)
        except Exception:
            plan = _heuristic_plan(ctx)
    applied = execute(plan, llm=client if not isinstance(client, FakeLLM) else None)
    remember(
        f"Tick: {plan.thought or 'harvest/reflect'}. " + "; ".join(applied[:4]),
        kind="episode",
        subject="tick",
        source="brain",
        importance=0.35,
        tags=["tick"],
        event=False,
    )
    if plan.goal:
        remember(plan.goal, kind="goal", source="brain", importance=0.7, event=False)
    return {
        "thought": plan.thought,
        "goal": plan.goal,
        "applied": applied,
        "memory": recent(6),
    }
