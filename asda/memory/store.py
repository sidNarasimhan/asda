"""Persistent evolving memory. Strengthen on reuse, merge near-duplicates."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from asda.db.models import MemoryRow
from asda.db.session import get_session, init_db

KINDS = ("episode", "fact", "person", "preference", "playbook", "goal", "mistake")
_KIND_WEIGHT = {
    "preference": 3.2,
    "goal": 2.6,
    "mistake": 2.4,
    "playbook": 2.2,
    "person": 2.0,
    "fact": 1.8,
    "episode": 1.0,
}
_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "at",
    "is",
    "it",
    "this",
    "that",
    "from",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in _STOP}


def _norm(text: str) -> str:
    return " ".join(sorted(_tokens(text)))


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.72


def _row_to_dict(row: MemoryRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "lead_id": row.lead_id,
        "subject": row.subject,
        "text": row.text,
        "tags": list(row.tags or []),
        "importance": float(row.importance or 0),
        "uses": int(row.uses or 0),
        "source": row.source,
        "active": bool(row.active),
        "meta": dict(row.meta or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _lookup_lead_id(name: str) -> str | None:
    needle = (name or "").strip().lower()
    if not needle:
        return None
    try:
        from asda.db.repository import Repository
        from asda.db.session import get_session

        session = get_session()
        try:
            for lead in Repository(session).list_leads(limit=500):
                if needle in lead.full_name.lower() or needle == (lead.email or "").lower() or needle == lead.id.lower():
                    return lead.id
        finally:
            session.close()
    except Exception:
        return None
    return None


def remember(
    text: str,
    *,
    kind: str = "fact",
    lead_id: str | None = None,
    subject: str = "",
    tags: list[str] | None = None,
    importance: float = 0.55,
    source: str = "agent",
    meta: dict[str, Any] | None = None,
    event: bool = True,
) -> dict[str, Any]:
    """Write a memory. Near-duplicates are strengthened instead of copied."""
    text = (text or "").strip()
    if not text:
        return {}
    kind = kind if kind in KINDS else "fact"
    importance = max(0.05, min(1.0, float(importance)))
    if lead_id and str(lead_id).count("-") != 4:
        subject = subject or str(lead_id)
        lead_id = _lookup_lead_id(str(lead_id))
    init_db()
    session = get_session()
    try:
        stmt = select(MemoryRow).where(MemoryRow.kind == kind, MemoryRow.active == 1)
        if lead_id:
            stmt = stmt.where(MemoryRow.lead_id == lead_id)
        candidates = list(session.scalars(stmt.limit(200)))
        match = next((r for r in candidates if _similar(r.text, text)), None)
        if match is None and subject:
            subj = subject.strip().lower()
            match = next(
                (r for r in candidates if (r.subject or "").strip().lower() == subj and r.kind == kind),
                None,
            )
        now = _now()
        if match:
            match.importance = min(1.0, float(match.importance or 0) + 0.08)
            match.uses = int(match.uses or 0) + 1
            match.last_used_at = now
            match.updated_at = now
            if len(text) > len(match.text or ""):
                match.text = text
            if tags:
                match.tags = list({*(match.tags or []), *tags})
            if meta:
                match.meta = {**(match.meta or {}), **meta}
            session.commit()
            out = _row_to_dict(match)
            out["merged"] = True
            return out

        row = MemoryRow(
            id=str(uuid4()),
            kind=kind,
            lead_id=lead_id or None,
            subject=(subject or "")[:256],
            text=text[:4000],
            tags=list(tags or []),
            importance=importance,
            uses=0,
            source=source,
            active=1,
            meta=dict(meta or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        out = _row_to_dict(row)
        out["merged"] = False
        if event:
            try:
                from asda.models.events import EventType
                from asda.ops.activity import log

                log(
                    EventType.MEMORY_WRITTEN,
                    lead_id=lead_id,
                    summary=f"Remembered [{kind}] {text[:120]}",
                    kind=kind,
                    memory_id=row.id,
                )
            except Exception:
                pass
        return out
    finally:
        session.close()


def search(
    query: str = "",
    *,
    lead_id: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 12,
    mark_used: bool = True,
) -> list[dict[str, Any]]:
    init_db()
    session = get_session()
    try:
        stmt = select(MemoryRow).where(MemoryRow.active == 1)
        if kinds:
            stmt = stmt.where(MemoryRow.kind.in_(kinds))
        rows = list(session.scalars(stmt.limit(800)))
        q = _tokens(query)
        now = _now()
        scored: list[tuple[float, MemoryRow]] = []
        for row in rows:
            score = float(row.importance or 0.3) * _KIND_WEIGHT.get(row.kind, 1.0)
            if lead_id and row.lead_id == lead_id:
                score += 3.2
            elif lead_id and row.lead_id and row.lead_id != lead_id:
                score *= 0.35
            if q:
                overlap = len(q & _tokens(f"{row.text} {row.subject} {' '.join(row.tags or [])}"))
                if overlap:
                    score += overlap * 0.55
                elif query:
                    score *= 0.55
            age_days = 0.0
            if row.updated_at:
                then = row.updated_at
                if then.tzinfo is None:
                    then = then.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now - then.astimezone(timezone.utc)).total_seconds() / 86400)
            score *= math.exp(-age_days / 50.0)
            score += min(int(row.uses or 0), 12) * 0.04
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [r for _, r in scored[:limit]]
        if mark_used:
            for row in picked:
                row.uses = int(row.uses or 0) + 1
                row.last_used_at = now
            session.commit()
        return [_row_to_dict(r) for r in picked]
    finally:
        session.close()


def recent(limit: int = 12, kinds: list[str] | None = None) -> list[dict[str, Any]]:
    init_db()
    session = get_session()
    try:
        stmt = select(MemoryRow).where(MemoryRow.active == 1).order_by(MemoryRow.updated_at.desc()).limit(limit * 3)
        if kinds:
            stmt = stmt.where(MemoryRow.kind.in_(kinds))
        rows = list(session.scalars(stmt))
        return [_row_to_dict(r) for r in rows[:limit]]
    finally:
        session.close()


def for_lead(lead_id: str, limit: int = 10) -> list[dict[str, Any]]:
    return search("", lead_id=lead_id, limit=limit, mark_used=False)


def memory_block(lead: Any | None = None, query: str = "", limit: int = 10) -> str:
    """Prompt snippet. Empty string if nothing useful yet."""
    lead_id = getattr(lead, "id", None) if lead is not None else None
    q = query
    if lead is not None and not q:
        q = f"{getattr(lead, 'full_name', '')} {getattr(getattr(lead, 'company', None), 'name', '')} {getattr(lead, 'title', '')}"
    items = search(q, lead_id=lead_id, limit=limit, mark_used=bool(lead_id or query))
    if not items:
        prefs = search("preference rule playbook", kinds=["preference", "playbook", "goal", "mistake"], limit=6, mark_used=False)
        items = prefs
    if not items:
        return ""
    lines = ["EVOLVING MEMORY (earned — follow this over generic SDR copy)"]
    for item in items:
        who = f" · {item['subject']}" if item.get("subject") else ""
        lines.append(f"- [{item['kind']}]{who} {item['text']}")
    return "\n".join(lines)


def is_blocked(lead: Any) -> bool:
    """True if memory says do not contact this person."""
    lead_id = getattr(lead, "id", None)
    blob = " ".join(
        [
            getattr(lead, "full_name", "") or "",
            getattr(lead, "email", "") or "",
            getattr(getattr(lead, "company", None), "name", "") or "",
        ]
    ).lower()
    prefs = search(
        blob,
        lead_id=lead_id,
        kinds=["preference"],
        limit=20,
        mark_used=False,
    )
    for p in prefs:
        t = (p.get("text") or "").lower()
        if "do not contact" in t or "do_not_contact" in t or "no further mail" in t or "don't email" in t or "dont email" in t:
            if lead_id and p.get("lead_id") == lead_id:
                return True
            name = (getattr(lead, "full_name", "") or "").lower()
            if name and name in t:
                return True
    return False


def deactivate(memory_id: str) -> None:
    init_db()
    session = get_session()
    try:
        row = session.get(MemoryRow, memory_id)
        if row:
            row.active = 0
            row.updated_at = _now()
            session.commit()
    finally:
        session.close()


def count(kind: str | None = None) -> int:
    init_db()
    session = get_session()
    try:
        stmt = select(MemoryRow).where(MemoryRow.active == 1)
        if kind:
            stmt = stmt.where(MemoryRow.kind == kind)
        return len(list(session.scalars(stmt)))
    finally:
        session.close()


def seed_if_empty() -> int:
    """First-run identity so the agent is not a blank slate."""
    if count() > 0:
        return 0
    from asda.config import get_settings

    offer = get_settings().offer or {}
    n = 0
    company = offer.get("company_name") or "this company"
    n += bool(
        remember(
            f"I am ASDA, the hired SDR for {company}. I research each person, write unique mail, "
            "run email + LinkedIn, book meetings, and remember what works.",
            kind="goal",
            subject=company,
            importance=0.9,
            source="seed",
            event=False,
        )
    )
    if offer.get("tone"):
        n += bool(
            remember(
                f"Tone of voice: {str(offer.get('tone'))[:400]}",
                kind="preference",
                subject=company,
                importance=0.7,
                source="seed",
                event=False,
            )
        )
    if offer.get("call_to_action"):
        n += bool(
            remember(
                f"CTA: {offer.get('call_to_action')}",
                kind="preference",
                subject=company,
                importance=0.65,
                source="seed",
                event=False,
            )
        )
    icp = offer.get("icp") or {}
    titles = icp.get("titles") if isinstance(icp, dict) else None
    if titles:
        n += bool(
            remember(
                "ICP titles: " + ", ".join(str(t) for t in titles[:12]),
                kind="fact",
                subject="ICP",
                importance=0.7,
                source="seed",
                event=False,
            )
        )
    n += bool(
        remember(
            "Never send generic mail. Sentence 1 must be unique to this person. "
            "If research is thin, ask one specific question instead of pitching.",
            kind="playbook",
            importance=0.85,
            source="seed",
            event=False,
        )
    )
    _seed_from_leads()
    return n


def _seed_from_leads() -> None:
    try:
        from asda.db.repository import Repository
        from asda.db.session import get_session
    except Exception:
        return
    session = get_session()
    try:
        leads = Repository(session).list_leads(limit=50)
    finally:
        session.close()
    for lead in leads:
        blob = f"{lead.full_name} {lead.email} {lead.company.name}".lower()
        if "kushal" in blob or "boxup" in blob:
            remember(
                f"Do not contact {lead.full_name} ({lead.email}) further — CBO hold. LinkedIn stop at invite.",
                kind="preference",
                lead_id=lead.id,
                subject=lead.full_name,
                tags=["do_not_contact"],
                importance=0.95,
                source="seed",
                event=False,
            )
        if "sanath" in blob:
            remember(
                f"{lead.full_name} is a live test inbox at {lead.email}. He replied 'got it' to the delivery test. "
                "Treat him as a conversation, not a cold pitch.",
                kind="person",
                lead_id=lead.id,
                subject=lead.full_name,
                importance=0.8,
                source="seed",
                event=False,
            )
        card = lead.research_card
        if card and (card.unique_to_this_person or card.personalization_hooks):
            hooks = card.unique_to_this_person or card.personalization_hooks
            remember(
                f"{lead.full_name} @ {lead.company.name}: " + "; ".join(hooks[:4]),
                kind="person",
                lead_id=lead.id,
                subject=lead.full_name,
                importance=0.6,
                source="seed",
                event=False,
            )
