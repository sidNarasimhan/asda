"""Research & Scoring Agent — live web search + structured ResearchCard."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from asda.agents.base import audit
from asda.config import get_settings
from asda.llm.client import LLMClient, get_llm
from asda.llm.prompts import RESEARCH_SYSTEM, research_user, score_user
from asda.models.audit import AuditEntry
from asda.models.lead import Lead, LeadStatus, ResearchCard

logger = logging.getLogger(__name__)


class ScoreResult(BaseModel):
    score: int = Field(default=0, ge=0, le=100)
    rationale: str = ""
    disqualify: bool = False
    disqualify_reason: str = ""


class ResearchAgent:
    name = "research"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm()

    def run(self, lead: Lead, **_: Any) -> tuple[Lead, list[AuditEntry]]:
        settings = get_settings()
        lead.status = LeadStatus.RESEARCHING
        logs = [audit(self.name, "started", f"Researching {lead.full_name} @ {lead.company.name}")]

        extra = _search_hint(lead)
        try:
            from asda.memory.store import memory_block

            mem = memory_block(lead)
            if mem:
                extra = extra + "\n\n" + mem
        except Exception:
            pass
        card = self.llm.parse(
            RESEARCH_SYSTEM,
            research_user(
                lead.model_dump_json(indent=2),
                settings.offer,
                extra_context=extra,
            ),
            ResearchCard,
            model=settings.model_frontier,
            tools=[{"type": "web_search"}],
        )
        lead.research_card = card
        try:
            from asda.memory.store import remember

            for hook in (card.unique_to_this_person or card.personalization_hooks or [])[:5]:
                remember(
                    f"{lead.full_name} @ {lead.company.name}: {hook}",
                    kind="person",
                    lead_id=lead.id,
                    subject=lead.full_name,
                    source="research",
                    importance=0.7,
                    event=False,
                )
            if card.summary:
                remember(
                    card.summary[:500],
                    kind="person",
                    lead_id=lead.id,
                    subject=lead.full_name,
                    source="research",
                    importance=0.6,
                    event=False,
                )
        except Exception:
            pass
        logs.append(
            audit(
                self.name,
                "researched",
                card.summary[:240],
                hooks=card.personalization_hooks,
                confidence=card.confidence,
            )
        )

        from asda.ops.playbook import playbook_block

        scored = self.llm.parse(
            "You are a strict ICP scorer. Be conservative. Disqualify on mismatch.",
            score_user(
                lead.model_dump_json(indent=2),
                card.model_dump_json(indent=2),
                settings.offer,
                scoring_notes=playbook_block(),
            ),
            ScoreResult,
            model=settings.model_fast,
        )
        lead.score = scored.score
        lead.notes.append(f"score:{scored.score} {scored.rationale}")
        if scored.disqualify:
            lead.status = LeadStatus.SUPPRESSED
            lead.add_outcome("suppressed", scored.disqualify_reason or "disqualified")
        else:
            lead.status = LeadStatus.RESEARCHED
        logs.append(
            audit(
                self.name,
                "scored",
                scored.rationale,
                score=scored.score,
                disqualify=scored.disqualify,
            )
        )
        return lead, logs


def _search_hint(lead: Lead) -> str:
    bits = [lead.full_name, lead.title, lead.company.name, lead.company.domain]
    q = " ".join(b for b in bits if b)
    return (
        f"Use web search for recent news, funding, hiring, and tech signals about: {q}. "
        "Also check the company website and LinkedIn if a URL is present."
    )
