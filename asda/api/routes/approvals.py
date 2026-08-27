from __future__ import annotations

from fastapi import APIRouter, HTTPException

from asda.agents.orchestrator import Orchestrator
from asda.bus.events import get_bus
from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.events import EventType

router = APIRouter()


@router.get("")
def pending() -> list[dict]:
    session = get_session()
    try:
        rows = Repository(session).pending_approvals()
        return [
            {
                "id": r.id,
                "lead_id": r.lead_id,
                "stage": r.stage,
                "status": r.status,
                "payload": r.payload,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()


@router.post("/{approval_id}/decide")
def decide(approval_id: str, body: dict) -> dict:
    decision = body.get("decision", "approve")
    actor = body.get("actor", "human")
    session = get_session()
    try:
        repo = Repository(session)
        row = repo.decide_approval(
            approval_id, "approved" if decision == "approve" else "rejected", actor
        )
        if not row:
            raise HTTPException(404, "approval not found")
        lead = repo.get_lead(row.lead_id)
        session.commit()
    finally:
        session.close()

    if not lead:
        raise HTTPException(404, "lead not found")

    if decision != "approve":
        get_bus().emit_type(EventType.APPROVAL_REJECTED, lead.id, stage=row.stage, actor=actor)
        return {"status": "rejected", "lead_id": lead.id}

    get_bus().emit_type(EventType.APPROVAL_GRANTED, lead.id, stage=row.stage, actor=actor)
    result = Orchestrator().continue_after_approval(lead, row.stage)
    return {
        "status": "approved",
        "decision": result["decision"],
        "lead": result["lead"].model_dump(mode="json"),
    }
