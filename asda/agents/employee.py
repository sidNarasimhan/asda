"""Talk to ASDA like an employee — rules, offer tweaks, status, pause."""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field

from asda.config import ROOT, get_settings
from asda.db.repository import Repository
from asda.db.session import get_session
from asda.llm.client import LLMClient, get_llm
from asda.runtime import load_runtime, update_runtime

SYSTEM = """You are ASDA, a hired SDR with evolving memory.
The CBO talks to you in plain language. You:
- Answer how the pipeline is doing using STATS and MEMORY
- Change targeting / tone / rules when they ask
- Pause or resume outreach
- Review campaign drafts, apply copy changes, and record requested schedules
- Put durable instructions in `remember` (do-not-contact, ICP, tone)
- Never invent metrics — use the stats provided
Keep replies short, like Slack to your boss.
"""


class EmployeeAction(BaseModel):
    reply: str = ""
    pause_outreach: bool | None = None
    resume_outreach: bool | None = None
    min_score: int | None = None
    campaign_schedule: str = ""
    draft_instruction: str = ""
    offer_updates: dict[str, Any] = Field(default_factory=dict)
    remember: list[str] = Field(default_factory=list)
    notes: str = ""


def _offer_path():
    p = ROOT / "config" / "offer.yaml"
    return p


def _apply_offer_updates(updates: dict[str, Any]) -> None:
    path = _offer_path()
    current = yaml.safe_load(path.read_text()) or {}
    for k, v in updates.items():
        if v in (None, "", {}, []):
            continue
        current[k] = v
    path.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True))


def _simple_talk(message: str, stats: dict[str, Any], rt) -> dict[str, Any] | None:
    text = (message or "").strip().lower()
    if not text:
        return None
    if any(k in text for k in ("pause", "stop sending", "hold outreach", "stop all", "don't send")):
        update_runtime(dry_run=True)
        from asda.memory.store import remember

        remember(
            "CBO paused outreach. Do not send until they say go.",
            kind="preference",
            importance=0.9,
            source="talk",
            tags=["paused"],
        )
        return {
            "reply": "Paused. I won't send until you say go.",
            "applied": ["paused sending (dry-run on)", "remembered pause"],
            "notes": "simple-intent",
        }
    if any(k in text for k in ("resume", "start sending", "unpause", "go live", "keep sending")):
        if rt.live_confirmed:
            update_runtime(dry_run=False)
            return {"reply": "Back on. I'll send the next due steps.", "applied": ["resumed sending"], "notes": "simple-intent"}
        return {
            "reply": "I can resume once live sending is checked on Home.",
            "applied": ["cannot resume — live mode not confirmed"],
            "notes": "simple-intent",
        }
    if any(k in text for k in ("how are we", "how's the", "how is the", "status", "the week", "what happened", "how are things")):
        from asda.ops.analytics import scoreboard as month_board

        board = month_board()
        bits = [
            f"{stats.get('total_leads', 0)} leads in the book.",
            f"This month ({board['period']}) is {board['overall'].lower()}.",
        ]
        for row in board["rows"]:
            bits.append(f"{row['label']} {row['actual']}/{row['target']}.")
        return {"reply": " ".join(bits), "applied": [], "notes": "simple-intent"}
    if any(k in text for k in ("draft", "campaign plan", "review campaign", "review copy")):
        session = get_session()
        try:
            repo = Repository(session)
            leads = repo.list_leads(limit=20_000)
            contactable = [
                lead for lead in leads
                if (lead.email or lead.phone)
                and "dnr" not in lead.tags
                and lead.status.value not in {"suppressed", "replied", "meeting_booked", "closed"}
            ]
            drafted = sum(bool(repo.get_content(lead.id)) for lead in contactable)
        finally:
            session.close()
        return {"reply": f"{drafted}/{len(contactable)} contactable leads have a saved multi-channel draft: four emails, a LinkedIn connection plus three follow-ups, and two WhatsApp drafts. Tell me the change you want—for example ‘make follow-up 2 shorter’ or ‘use a more technical tone’. Nothing is sent.", "applied": [], "notes": "draft-review"}
    if any(k in text for k in ("schedule", "weekdays", "cadence", "send at", "send between")):
        update_runtime(campaign_schedule=(message or "").strip())
        return {"reply": "Saved that requested campaign schedule. It applies only after explicit approval; drafts remain review-only.", "applied": ["saved campaign schedule"], "notes": "schedule"}
    return None


def talk(message: str, llm: LLMClient | None = None) -> dict[str, Any]:
    session = get_session()
    try:
        stats = Repository(session).metrics()
    finally:
        session.close()
    offer = get_settings().offer
    rt = load_runtime()
    from asda.ops.onboard import consume as consume_onboard
    from asda.ops.onboard import next_step, ready_to_work

    landed = consume_onboard(message)
    if landed:
        _log_talk(message, landed)
        return landed
    if llm is None:
        simple = _simple_talk(message, stats, rt)
        if simple:
            _log_talk(message, simple)
            return simple
        if not ready_to_work():
            step = next_step()
            if step:
                result = {
                    "reply": step["ask"],
                    "applied": [],
                    "notes": "onboard-prompt",
                }
                _log_talk(message, result)
                return result
    mem = ""
    try:
        from asda.memory.store import memory_block

        mem = memory_block(query=message, limit=8)
    except Exception:
        mem = ""
    user = (
        f"STATS: {stats}\n"
        f"OFFER: {offer}\n"
        f"{mem}\n"
        f"DRY_RUN: {rt.dry_run} live_confirmed={rt.live_confirmed}\n"
        f"CBO SAID: {message}\n"
        "If they change ICP, tone, company, or CTA, put keys in offer_updates.\n"
        "If they say stop sending, pause_outreach=true. If they say go, resume_outreach=true.\n"
        "If they specify cadence or send hours, put the exact request in campaign_schedule. If they ask to change draft copy, put the instruction in draft_instruction.\n"
        "If they name a person to leave alone, put a do-not-contact line in remember."
    )
    action = (llm or get_llm()).parse(SYSTEM, user, EmployeeAction, model=get_settings().model_fast)
    applied: list[str] = []
    if action.offer_updates:
        _apply_offer_updates(action.offer_updates)
        applied.append("updated offer.yaml")
    if action.pause_outreach:
        update_runtime(dry_run=True)
        applied.append("paused sending (dry-run on)")
    if action.resume_outreach and rt.live_confirmed:
        update_runtime(dry_run=False)
        applied.append("resumed sending")
    elif action.resume_outreach and not rt.live_confirmed:
        applied.append("cannot resume — live mode not confirmed")
    if action.min_score is not None:
        update_runtime(min_score=action.min_score)
        applied.append(f"min score {action.min_score}")
    if action.campaign_schedule:
        update_runtime(campaign_schedule=action.campaign_schedule)
        applied.append("saved campaign schedule")
    if action.remember:
        from asda.memory.store import remember as mem_write

        for line in action.remember:
            kind = "preference" if "do not" in line.lower() or "don't" in line.lower() else "fact"
            mem_write(line, kind=kind, source="talk", importance=0.8)
            applied.append(f"remembered {line[:60]}")
    result = {"reply": action.reply, "applied": applied, "notes": action.notes}
    _log_talk(message, result)
    return result


def _log_talk(message: str, result: dict[str, Any]) -> None:
    from asda.models.events import EventType
    from asda.ops.activity import log

    log(
        EventType.EMPLOYEE_TALK,
        actor="cbo",
        summary=(result.get("reply") or message)[:180],
        applied=result.get("applied") or [],
    )
