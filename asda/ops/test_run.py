"""One-click smoke test: ingest mock Apollo CSV, write copy, hit email + LinkedIn send paths."""

from __future__ import annotations

from pathlib import Path

from asda.agents.email_outreach import EmailOutreachAgent
from asda.agents.linkedin_outreach import LinkedInOutreachAgent
from asda.agents.orchestrator import Orchestrator
from asda.config import ROOT, get_settings
from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.ingestion.csv_source import CSVSource
from asda.models.lead import LeadQuery
from asda.runtime import effective

CSV = ROOT / "sample_data" / "apollo_india_d2c.csv"


def ingest_mock() -> dict:
    init_db()
    leads = CSVSource().fetch(LeadQuery(limit=50, extra={"path": str(CSV)}))
    session = get_session()
    created = 0
    ids = []
    try:
        repo = Repository(session)
        for lead in leads:
            lead.tags = list(set(lead.tags + ["mock_apollo"]))
            saved, is_new = repo.upsert_lead(lead)
            created += int(is_new)
            ids.append(saved.id)
        session.commit()
    finally:
        session.close()
    return {"ingested": len(leads), "new": created, "ids": ids}


def run_smoke() -> dict:
    """Research one lead, send a test email to YOUR inbox, attempt LinkedIn launch."""
    init_db()
    cfg = effective()
    ingest = ingest_mock()
    session = get_session()
    try:
        repo = Repository(session)
        lead = None
        for lid in ingest["ids"]:
            cand = repo.get_lead(lid)
            if cand and cand.status.value == "new":
                lead = cand
                break
        if lead is None:
            lead = repo.get_lead(ingest["ids"][0])
    finally:
        session.close()

    if lead is None:
        return {"ok": False, "error": "no leads after ingest"}

    orch = Orchestrator()
    researched = orch.run(lead, skip_outreach=True)
    lead = researched["lead"]
    content = researched["content"]
    out: dict = {
        "ok": True,
        "lead": lead.full_name,
        "company": lead.company.name,
        "score": lead.score,
        "decision": researched["decision"],
        "email_subject": content.emails[0].subject if content and content.emails else None,
        "linkedin_note": content.linkedin.connection_note if content else None,
        "email_send": None,
        "linkedin_send": None,
    }
    if researched["decision"] == "suppressed" or content is None:
        out["ok"] = False
        out["error"] = researched.get("error") or "lead suppressed before outreach"
        return out

    # Email: send the first step to YOUR mailbox so we don't bounce fake Apollo emails
    target = cfg.smtp_user
    if not target or not cfg.smtp_verified:
        out["email_send"] = {"reached": False, "error": "mailbox not connected in Setup"}
    else:
        saved_email = lead.email
        lead.email = target
        try:
            lead, logs = EmailOutreachAgent().send_next(lead, content)
            out["email_send"] = {
                "reached": True,
                "to": target,
                "provider": logs[0].data.get("provider") if logs else None,
                "action": logs[0].action if logs else None,
                "detail": logs[0].detail if logs else None,
            }
        except Exception as exc:
            out["email_send"] = {"reached": True, "to": target, "error": str(exc)}
        lead.email = saved_email

    # LinkedIn: real PhantomBuster launch path (may fail — that still proves we reached it)
    try:
        lead, logs = LinkedInOutreachAgent().send_connect(lead, content)
        payload = logs[0].data if logs else {}
        out["linkedin_send"] = {
            "reached": True,
            "provider": payload.get("provider") or (logs[0].data.get("result") if logs else None),
            "action": logs[0].action if logs else None,
            "result": payload.get("result"),
            "error": None,
        }
    except Exception as exc:
        out["linkedin_send"] = {"reached": True, "error": str(exc)}

    session = get_session()
    try:
        Repository(session).save_lead(lead)
        session.commit()
    finally:
        session.close()
    return out
