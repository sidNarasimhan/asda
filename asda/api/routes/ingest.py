from __future__ import annotations

from pathlib import Path

from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from asda.config import get_settings
from asda.ingestion.registry import get_registry
from asda.models.lead import LeadQuery

router = APIRouter()


@router.post("/signalhire/callback")
async def signalhire_callback(request: Request, token: str = "") -> dict:
    """Accept SignalHire's async waterfall result and merge it into its source lead."""
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from asda.ingestion.enrichment import apply_verified_enrichment
    from asda.ingestion.signalhire import SignalHireSource, _load_requests, _save_requests

    request_id = request.headers.get("Request-Id", "")
    requests = _load_requests()
    pending = requests.get(str(request_id))
    if not pending or not token or token != pending.get("token"):
        raise HTTPException(403, "Invalid SignalHire callback")
    body: Any = await request.json()
    results = body if isinstance(body, list) else body.get("results", [])
    session = get_session()
    updated = 0
    try:
        repo = Repository(session)
        for result in results:
            if not isinstance(result, dict) or result.get("status") != "success":
                continue
            lead_id = pending["item_to_lead_id"].get(str(result.get("item", "")).strip().lower())
            existing = repo.get_lead(lead_id) if lead_id else None
            if not existing or not isinstance(result.get("candidate"), dict):
                continue
            enriched = []
            SignalHireSource._append_profiles(enriched, [result["candidate"]], 1)
            if enriched:
                apply_verified_enrichment(existing, enriched[0])
                existing.raw_data["signalhire_uid"] = result["candidate"].get("uid", "")
                existing.raw_data["signalhire_enriched"] = True
                repo.save_lead(existing)
                updated += 1
        session.commit()
    finally:
        session.close()
    requests.pop(str(request_id), None)
    _save_requests(requests)
    return {"ok": True, "updated": updated}


@router.post("/signalhire/enrich")
def enrich_signalhire(lead_ids: list[str]) -> dict:
    """Enrich existing leads locally via SignalHire's synchronous Person API."""
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from asda.ingestion.enrichment import apply_verified_enrichment
    from asda.ingestion.signalhire import SignalHireSource

    if not lead_ids:
        raise HTTPException(422, "Provide one or more lead IDs")
    session = get_session()
    try:
        repo = Repository(session)
        originals = [repo.get_lead(lead_id) for lead_id in lead_ids]
        originals = [lead for lead in originals if lead is not None]
        returned = SignalHireSource().enrich_existing(originals)
        results = []
        for original in originals:
            enriched = returned.get(original.id)
            if enriched:
                apply_verified_enrichment(original, enriched)
                repo.save_lead(original)
            results.append({
                "lead_id": original.id,
                "name": original.full_name,
                "matched": bool(enriched),
                "email_found": bool(original.email),
                "phone_found": bool(original.phone),
            })
        session.commit()
        return {"processed": len(originals), "results": results}
    finally:
        session.close()


def _persist(leads) -> dict:
    from asda.ingestion.pipeline import persist_leads

    return persist_leads(leads, emit_each=len(leads) <= 25)


@router.post("/csv")
async def ingest_csv(file: UploadFile = File(...)) -> dict:
    settings = get_settings()
    dest = settings.data_dir / "uploads" / (file.filename or "upload.csv")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    source = get_registry().get("csv")
    leads = source.fetch(LeadQuery(limit=10_000, extra={"path": str(dest)}))
    result = _persist(leads)
    result["path"] = str(dest)
    return result


@router.post("/csv/path")
def ingest_csv_path(path: str) -> dict:
    if not Path(path).exists():
        raise HTTPException(404, f"file not found: {path}")
    leads = get_registry().get("csv").fetch(LeadQuery(limit=10_000, extra={"path": path}))
    return _persist(leads)


@router.post("/webhook")
async def ingest_webhook(request: Request) -> dict:
    payload: Any = await request.json()
    leads = get_registry().get("webhook").fetch(
        LeadQuery(limit=10_000, extra={"payload": payload})
    )
    return _persist(leads)


@router.post("/apollo")
def ingest_apollo(query: LeadQuery) -> dict:
    try:
        leads = get_registry().get("apollo").fetch(query)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _persist(leads)


@router.post("/{source_name}")
def ingest_named(source_name: str, query: LeadQuery) -> dict:
    try:
        source = get_registry().get(source_name)
        leads = source.fetch(query)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return _persist(leads)


@router.get("/sources")
def list_sources() -> dict:
    registry = get_registry()
    return {"sources": [s.healthcheck() for s in registry.all()]}
