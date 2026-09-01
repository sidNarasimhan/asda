"""Send only the approved initial Altisec email to the tagged 100-lead cohort.

The job is resumable. A lead is tagged only after SMTP reports success, and is
then paused with every later channel held so the normal worker cannot duplicate
the first email or start LinkedIn, WhatsApp, calls, or email follow-ups.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.models.lead import LeadStatus
from asda.modules.esp import SMTPESP
from asda.modules.safety import SafetyGate
from asda.ops.activity import log
from asda.models.events import EventType


BATCH_TAG = "altisec_batch_1_draft"
SENT_TAG = "altisec_initial_2026_09_01_sent"
PROGRESS = Path("data/altisec_initial_100_progress.json")
HOLD_REASON = "Initial email sent; all follow-ups, LinkedIn, WhatsApp, and calls held by user"


def write_progress(**values) -> None:
    current = {}
    if PROGRESS.exists():
        try:
            current = json.loads(PROGRESS.read_text())
        except Exception:
            current = {}
    current.update(values)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROGRESS.write_text(json.dumps(current, indent=2))


def cohort_ids() -> list[str]:
    session = get_session()
    try:
        leads = [lead for lead in Repository(session).list_leads(limit=20_000) if BATCH_TAG in lead.tags]
        if len(leads) != 100:
            raise RuntimeError(f"Expected exactly 100 tagged leads, found {len(leads)}")
        if any(not lead.email for lead in leads):
            raise RuntimeError("Every lead must have an email before launch")
        return [lead.id for lead in leads]
    finally:
        session.close()


def already_sent(lead_id: str) -> bool:
    session = get_session()
    try:
        lead = Repository(session).get_lead(lead_id)
        return bool(lead and SENT_TAG in lead.tags)
    finally:
        session.close()


def send_one(lead_id: str) -> tuple[str, str]:
    session = get_session()
    try:
        repo = Repository(session)
        lead = repo.get_lead(lead_id)
        if not lead:
            raise RuntimeError(f"Lead disappeared: {lead_id}")
        if SENT_TAG in lead.tags:
            return lead.full_name, "already_sent"
        content = repo.get_content(lead.id)
        if not content or not content.emails:
            raise RuntimeError(f"No approved email content for {lead.full_name}")
        email = content.emails[0]
        result = SMTPESP().send(lead, email)
        if result.get("status") != "sent":
            raise RuntimeError(f"SMTP did not confirm send for {lead.full_name}: {result}")

        lead.tags = list(dict.fromkeys([*lead.tags, SENT_TAG]))
        lead.sequence_state.email_step = max(1, lead.sequence_state.email_step)
        lead.sequence_state.step_index = max(1, lead.sequence_state.step_index)
        lead.sequence_state.next_email_at = None
        lead.sequence_state.email_dropped = True
        lead.sequence_state.linkedin_dropped = True
        lead.sequence_state.next_linkedin_at = None
        lead.sequence_state.phone_stage = "skipped"
        lead.sequence_state.next_call_at = None
        lead.sequence_state.paused = True
        lead.sequence_state.reason = HOLD_REASON
        lead.status = LeadStatus.SEQUENCED
        lead.add_outcome(
            "email_sent",
            email.subject,
            step=1,
            provider="smtp",
            campaign="altisec_initial_100_2026_09_01",
        )
        repo.save_lead(lead)
        session.commit()
        name, address, subject = lead.full_name, lead.email, email.subject
    finally:
        session.close()

    SafetyGate().record_send("email")
    log(
        EventType.EMAIL_SENT,
        lead_id=lead_id,
        summary=f"Sent approved initial email to {name}",
        subject=subject,
        provider="smtp",
        campaign="altisec_initial_100_2026_09_01",
    )
    return name, address


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=180)
    args = parser.parse_args()
    init_db()
    ids = cohort_ids()
    completed = sum(already_sent(lead_id) for lead_id in ids)
    write_progress(status="running", total=100, sent=completed, failed=[])

    failures: list[dict[str, str]] = []
    for lead_id in ids:
        if already_sent(lead_id):
            continue
        try:
            name, address = send_one(lead_id)
            completed += 1
            failures = [item for item in failures if item["lead_id"] != lead_id]
            write_progress(status="running", total=100, sent=completed, last_name=name, last_email=address, failed=failures)
            print(f"sent {completed}/100 {name} <{address}>", flush=True)
        except Exception as exc:
            failures.append({"lead_id": lead_id, "error": str(exc)[:300]})
            write_progress(status="running_with_errors", total=100, sent=completed, failed=failures)
            print(f"failed {lead_id}: {exc}", flush=True)
        if completed < 100:
            time.sleep(random.randint(args.interval, args.interval + 30))

    status = "complete" if completed == 100 else "incomplete"
    write_progress(status=status, total=100, sent=completed, failed=failures)
    print(f"{status}: {completed}/100", flush=True)


if __name__ == "__main__":
    main()
