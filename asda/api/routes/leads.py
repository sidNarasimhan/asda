from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.lead import Lead

router = APIRouter()


@router.get("")
def list_leads(
    status: str | None = None,
    min_score: int | None = None,
    sort: str = Query(default="relevance", pattern="^(relevance|recent)$"),
    limit: int = Query(default=100, le=500),
) -> list[dict]:
    session = get_session()
    try:
        leads = Repository(session).list_leads(status=status, limit=limit, min_score=min_score)
        if sort == "relevance":
            leads.sort(key=lambda lead: (-lead.score, lead.full_name.lower()))
        return [l.model_dump(mode="json") for l in leads]
    finally:
        session.close()


@router.get("/{lead_id}")
def get_lead(lead_id: str) -> dict:
    session = get_session()
    try:
        repo = Repository(session)
        lead = repo.get_lead(lead_id)
        if not lead:
            raise HTTPException(404, "lead not found")
        return {
            "lead": lead.model_dump(mode="json"),
            "content": (
                c.model_dump(mode="json") if (c := repo.get_content(lead_id)) else None
            ),
            "events": repo.events_for(lead_id),
        }
    finally:
        session.close()


@router.post("")
def create_lead(payload: dict) -> dict:
    lead = Lead.model_validate(payload)
    from asda.ingestion.normalize import fingerprint_for

    if not lead.fingerprint:
        lead.fingerprint = fingerprint_for(lead)
    session = get_session()
    try:
        saved, created = Repository(session).upsert_lead(lead)
        session.commit()
        return {"created": created, "lead": saved.model_dump(mode="json")}
    finally:
        session.close()
