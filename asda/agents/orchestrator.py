"""Orchestrator — LangGraph state machine + sequential fallback.

Graph:
  ingest/load → research → score_gate → content → hitl? → outreach → handoff → END
                                              ↘ suppress → END
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from asda.agents.content import ContentAgent
from asda.agents.handoff import HandoffAgent
from asda.agents.learning import LearningAgent
from asda.agents.research import ResearchAgent
from asda.agents.sequence import SequenceEngine
from asda.ingestion.apollo import ApolloPlanError, enrich_lead
from asda.bus.events import get_bus
from asda.config import get_settings
from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.llm.client import LLMClient
from asda.models.audit import AuditEntry
from asda.models.content import GeneratedContent
from asda.models.events import EventType
from asda.models.lead import Lead, LeadStatus

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    lead: dict
    content: dict
    audit: list[dict]
    decision: str
    error: str
    skip_outreach: bool


class Orchestrator:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.research = ResearchAgent(llm)
        self.content = ContentAgent(llm)
        self.sequence = SequenceEngine()
        self.handoff = HandoffAgent()
        self.learning = LearningAgent(llm)
        self.settings = get_settings()

    def _gate(self):
        from asda.runtime import effective

        cfg = effective()
        return cfg.min_score, cfg.hitl

    def run(
        self,
        lead: Lead,
        *,
        skip_outreach: bool = False,
        persist: bool = True,
    ) -> dict[str, Any]:
        init_db()
        bus = get_bus()
        audit_log: list[AuditEntry] = []
        content: GeneratedContent | None = None

        session = get_session() if persist else None
        repo = Repository(session) if session else None

        try:
            try:
                lead = enrich_lead(lead)
            except ApolloPlanError:
                pass
            if repo:
                repo.save_lead(lead)

            lead.status = LeadStatus.RESEARCHING
            bus.emit_type(EventType.RESEARCH_STARTED, lead.id)
            lead, logs = self.research.run(lead)
            audit_log.extend(logs)
            bus.emit_type(EventType.RESEARCH_COMPLETED, lead.id, score=lead.score)
            bus.emit_type(EventType.LEAD_SCORED, lead.id, score=lead.score)
            if repo:
                repo.save_lead(lead)

            min_score, hitl = self._gate()
            # Account-list CSVs are already picked people. Don't kill them because
            # web research returned a thin ICP score against a different offer.
            raw_keys = {str(k).strip().lower() for k in (lead.raw_data or {})}
            account_sheet = bool(raw_keys & {"point of contact", "email id", "remarks", "designation"})
            seed = (
                account_sheet
                and (lead.email or lead.linkedin_url)
                and (lead.source or "").lower() in {"csv", "sheets", "upload", "whatsapp"}
            )
            if seed:
                lead.score = max(int(lead.score or 0), 65)
                if lead.status == LeadStatus.SUPPRESSED:
                    lead.status = LeadStatus.RESEARCHED
            if (lead.status == LeadStatus.SUPPRESSED and not seed) or lead.score < min_score:
                lead.status = LeadStatus.SUPPRESSED
                bus.emit_type(EventType.LEAD_SUPPRESSED, lead.id, score=lead.score)
                if repo:
                    repo.save_lead(lead)
                    session.commit()
                return _result(lead, content, audit_log, "suppressed")

            if "research" in hitl:
                return self._hold(lead, content, audit_log, "research", repo, session)

            patterns = self.learning.patterns_block()
            lead, content, logs = self.content.run(lead, winning_patterns=patterns)
            audit_log.extend(logs)
            bus.emit_type(
                EventType.CONTENT_GENERATED,
                lead.id,
                subjects=[e.subject for e in content.emails],
            )
            if repo:
                repo.save_content(lead.id, content)
                repo.save_lead(lead)

            if "content" in hitl:
                return self._hold(lead, content, audit_log, "content", repo, session)

            if skip_outreach:
                if repo:
                    session.commit()
                return _result(lead, content, audit_log, "content_ready")

            if "outreach" in hitl:
                return self._hold(lead, content, audit_log, "outreach", repo, session)

            lead, logs = self._outreach(lead, content)
            audit_log.extend(logs)
            try:
                from asda.memory.store import remember

                remember(
                    f"Started sequence for {lead.full_name} @ {lead.company.name} (score {lead.score})",
                    kind="episode",
                    lead_id=lead.id,
                    subject=lead.full_name,
                    source="orchestrator",
                    importance=0.5,
                    event=False,
                )
            except Exception:
                pass
            lead, logs = self.handoff.run(lead, content)
            audit_log.extend(logs)
            bus.emit_type(EventType.HANDOFF_COMPLETED, lead.id)
            if repo:
                repo.save_lead(lead)
                session.commit()
            return _result(lead, content, audit_log, "sequenced")
        except Exception as exc:
            logger.exception("Pipeline failed for %s", lead.id)
            lead.status = LeadStatus.FAILED
            bus.emit_type(EventType.PIPELINE_FAILED, lead.id, error=str(exc))
            if repo:
                repo.save_lead(lead)
                session.commit()
            return _result(lead, content, audit_log, "failed", error=str(exc))
        finally:
            if session:
                session.close()

    def continue_after_approval(self, lead: Lead, stage: str) -> dict[str, Any]:
        """Resume a HITL-paused lead."""
        init_db()
        session = get_session()
        repo = Repository(session)
        stored = repo.get_content(lead.id)
        try:
            if stage == "research":
                return self.run(lead, persist=True)
            content = stored or GeneratedContent()
            if stage == "content":
                lead, logs = self._outreach(lead, content)
                lead, hlogs = self.handoff.run(lead, content)
                repo.save_lead(lead)
                session.commit()
                return _result(lead, content, logs + hlogs, "sequenced")
            if stage == "outreach":
                lead, logs = self._outreach(lead, content)
                lead, hlogs = self.handoff.run(lead, content)
                repo.save_lead(lead)
                session.commit()
                return _result(lead, content, logs + hlogs, "sequenced")
            return _result(lead, content, [], "noop")
        finally:
            session.close()

    def _outreach(
        self, lead: Lead, content: GeneratedContent
    ) -> tuple[Lead, list[AuditEntry]]:
        return self.sequence.start(lead, content)

    def _hold(
        self,
        lead: Lead,
        content: GeneratedContent | None,
        audit_log: list[AuditEntry],
        stage: str,
        repo: Repository | None,
        session: Any,
    ) -> dict[str, Any]:
        lead.status = LeadStatus.AWAITING_APPROVAL
        get_bus().emit_type(EventType.APPROVAL_REQUESTED, lead.id, stage=stage)
        if repo:
            payload = {
                "stage": stage,
                "score": lead.score,
                "summary": lead.research_card.summary if lead.research_card else "",
            }
            if content:
                payload["subjects"] = [e.subject for e in content.emails]
                repo.save_content(lead.id, content)
            repo.request_approval(lead.id, stage, payload)
            repo.save_lead(lead)
            session.commit()
        return _result(lead, content, audit_log, f"awaiting_{stage}")


def _result(
    lead: Lead,
    content: GeneratedContent | None,
    audit_log: list[AuditEntry],
    decision: str,
    error: str = "",
) -> dict[str, Any]:
    return {
        "lead": lead,
        "content": content,
        "audit": audit_log,
        "decision": decision,
        "error": error,
    }


def run_lead(lead: Lead, **kwargs: Any) -> dict[str, Any]:
    return Orchestrator().run(lead, **kwargs)


def build_graph():
    """Optional LangGraph wrapper for visualization / checkpointing."""
    from langgraph.graph import END, START, StateGraph

    orch = Orchestrator()

    def research_node(state: PipelineState) -> PipelineState:
        lead = Lead.model_validate(state["lead"])
        result = orch.run(lead, skip_outreach=state.get("skip_outreach", False))
        return {
            "lead": result["lead"].model_dump(mode="json"),
            "content": result["content"].model_dump(mode="json") if result["content"] else {},
            "audit": [a.model_dump(mode="json") for a in result["audit"]],
            "decision": result["decision"],
            "error": result["error"],
        }

    graph = StateGraph(PipelineState)
    graph.add_node("pipeline", research_node)
    graph.add_edge(START, "pipeline")
    graph.add_edge("pipeline", END)
    return graph.compile()
