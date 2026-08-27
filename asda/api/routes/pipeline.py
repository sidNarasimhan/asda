from __future__ import annotations

from fastapi import APIRouter, HTTPException

from asda.agents.orchestrator import Orchestrator
from asda.agents.sequence import SequenceEngine
from asda.db.repository import Repository
from asda.db.session import get_session

router = APIRouter()


@router.post("/run/{lead_id}")
def run_one(lead_id: str, skip_outreach: bool = False) -> dict:
    session = get_session()
    try:
        lead = Repository(session).get_lead(lead_id)
        if not lead:
            raise HTTPException(404, "lead not found")
    finally:
        session.close()
    result = Orchestrator().run(lead, skip_outreach=skip_outreach)
    return {
        "decision": result["decision"],
        "error": result["error"],
        "lead": result["lead"].model_dump(mode="json"),
        "content": result["content"].model_dump(mode="json") if result["content"] else None,
        "audit": [a.model_dump(mode="json") for a in result["audit"]],
    }


@router.post("/run-batch")
def run_batch(limit: int = 10, skip_outreach: bool = True) -> dict:
    session = get_session()
    try:
        leads = Repository(session).list_leads(status="new", limit=limit)
    finally:
        session.close()
    orch = Orchestrator()
    results = []
    for lead in leads:
        r = orch.run(lead, skip_outreach=skip_outreach)
        results.append({"id": r["lead"].id, "decision": r["decision"], "score": r["lead"].score})
    return {"ran": len(results), "results": results}


@router.post("/tick/{lead_id}")
def tick(lead_id: str) -> dict:
    session = get_session()
    try:
        repo = Repository(session)
        lead = repo.get_lead(lead_id)
        if not lead:
            raise HTTPException(404, "lead not found")
        content = repo.get_content(lead_id)
        if not content:
            raise HTTPException(400, "no content generated yet")
        lead, logs = SequenceEngine().tick(lead, content)
        repo.save_lead(lead)
        session.commit()
        return {"lead": lead.model_dump(mode="json"), "audit": [a.model_dump(mode="json") for a in logs]}
    finally:
        session.close()


@router.post("/reply/{lead_id}")
def handle_reply(lead_id: str, body: dict) -> dict:
    thread = body.get("thread") or body.get("text") or ""
    session = get_session()
    try:
        repo = Repository(session)
        lead = repo.get_lead(lead_id)
        if not lead:
            raise HTTPException(404, "lead not found")
        channel = body.get("channel") or "email"
        lead, decision, logs = SequenceEngine().ingest_reply(lead, thread, channel)
        repo.save_lead(lead)
        session.commit()
        return {
            "lead": lead.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "audit": [a.model_dump(mode="json") for a in logs],
        }
    finally:
        session.close()
