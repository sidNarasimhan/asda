from __future__ import annotations

from fastapi import APIRouter, HTTPException

from asda.ingestion.apollo import ApolloSource
from asda.modules.mail import check_imap, check_smtp
from asda.modules.phantombuster import PhantomBusterClient, PhantomBusterError
from asda.runtime import effective, load_runtime, setup_status, update_runtime

router = APIRouter()


@router.get("")
def status() -> dict:
    return setup_status() | {"effective": effective().model_dump(), "runtime": load_runtime().model_dump()}


@router.post("")
def save(body: dict) -> dict:
    allowed = set(load_runtime().model_fields)
    payload = {k: v for k, v in body.items() if k in allowed}
    cfg = update_runtime(**payload)
    return {"ok": True, "runtime": cfg.model_dump(), "status": setup_status()}


@router.post("/test/apollo")
def test_apollo() -> dict:
    try:
        return ApolloSource().probe()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/test/phantombuster")
def test_pb() -> dict:
    try:
        return PhantomBusterClient().validate()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/test/mail")
def test_mail(body: dict) -> dict:
    smtp_ok, smtp_msg = check_smtp(
        body.get("smtp_host", ""),
        int(body.get("smtp_port") or 587),
        body.get("smtp_user", ""),
        body.get("smtp_password", ""),
    )
    imap_ok, imap_msg = check_imap(
        body.get("imap_host", ""),
        int(body.get("imap_port") or 993),
        body.get("imap_user") or body.get("smtp_user", ""),
        body.get("imap_password") or body.get("smtp_password", ""),
    )
    if smtp_ok:
        update_runtime(
            smtp_host=body.get("smtp_host"),
            smtp_port=int(body.get("smtp_port") or 587),
            smtp_user=body.get("smtp_user"),
            smtp_password=body.get("smtp_password"),
            smtp_from=body.get("smtp_from") or body.get("smtp_user"),
            imap_host=body.get("imap_host"),
            imap_user=body.get("imap_user") or body.get("smtp_user"),
            imap_password=body.get("imap_password") or body.get("smtp_password"),
            smtp_verified=True,
            imap_verified=imap_ok,
        )
    return {"smtp_ok": smtp_ok, "smtp": smtp_msg, "imap_ok": imap_ok, "imap": imap_msg}


@router.post("/phantoms/ensure")
def ensure_phantoms() -> dict:
    try:
        return PhantomBusterClient().ensure_linkedin_phantoms()
    except PhantomBusterError as exc:
        raise HTTPException(400, str(exc)) from exc
