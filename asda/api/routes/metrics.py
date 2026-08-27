from __future__ import annotations

from fastapi import APIRouter

from asda.agents.learning import LearningAgent
from asda.db.repository import Repository
from asda.db.session import get_session
from asda.ingestion.registry import get_registry
from asda.modules.safety import SafetyGate

router = APIRouter()


@router.get("")
def metrics() -> dict:
    session = get_session()
    try:
        return Repository(session).metrics()
    finally:
        session.close()


@router.get("/safety")
def safety() -> dict:
    return SafetyGate().snapshot()


@router.get("/sources")
def sources() -> dict:
    return {"sources": [s.healthcheck() for s in get_registry().all()]}


@router.post("/learn")
def learn() -> dict:
    insight = LearningAgent().run()
    return insight.model_dump(mode="json")


@router.get("/patterns")
def patterns() -> dict:
    session = get_session()
    try:
        items = Repository(session).winning_patterns()
        return {"patterns": [p.model_dump() for p in items]}
    finally:
        session.close()
