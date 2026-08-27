"""Due-step worker: sequences, inbox, PhantomBuster results, weekly learning."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from asda.agents.learning import LearningAgent
from asda.agents.sequence import SequenceEngine
from asda.config import get_settings
from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.models.lead import LeadStatus
from asda.modules.imap_inbox import fetch_unseen
from asda.runtime import effective

logger = logging.getLogger(__name__)


def _wrap(fn, name: str):
    def inner():
        from asda.ops.heartbeat import beat

        beat(name, "running")
        try:
            result = fn()
            beat(name, "ok", f"{result}" if result is not None else "ok")
            return result
        except Exception as exc:
            beat(name, "error", str(exc)[:200])
            logger.exception("%s failed", name)
            return None

    inner.__name__ = name
    return inner


def advance_new_leads(limit: int = 1) -> int:
    """Research + write (and send if live) for leads sitting in New / Researched."""
    from asda.agents.orchestrator import Orchestrator
    from asda.runtime import effective

    cfg = effective()
    init_db()
    session = get_session()
    try:
        repo = Repository(session)
        leads = repo.list_leads(status=LeadStatus.NEW, limit=limit)
        leads += repo.list_leads(status=LeadStatus.RESEARCHED, limit=limit)
    finally:
        session.close()
    seen: set[str] = set()
    processed = 0
    orch = Orchestrator()
    for lead in leads:
        if lead.id in seen:
            continue
        seen.add(lead.id)
        if lead.sequence_state.paused:
            continue
        if any(o.kind in {"email_sent", "linkedin_connect", "linkedin_message", "do_not_contact"} for o in lead.outcomes):
            continue
        try:
            from asda.memory.store import is_blocked

            if is_blocked(lead):
                continue
        except Exception:
            pass
        orch.run(lead, skip_outreach=bool(cfg.dry_run))
        processed += 1
    if processed:
        from asda.models.events import EventType
        from asda.ops.activity import log

        log(EventType.SEQUENCE_STEP, summary=f"Researched {processed} new lead(s)", count=processed)
    logger.info("advanced %s new leads", processed)
    return processed


def process_due_steps() -> int:
    init_db()
    session = get_session()
    processed = 0
    engine = SequenceEngine()
    try:
        repo = Repository(session)
        leads = repo.list_leads(status=LeadStatus.SEQUENCED, limit=200)
        leads += repo.list_leads(status=LeadStatus.CONNECTED, limit=200)
        now = datetime.now(timezone.utc)
        seen: set[str] = set()
        for lead in leads:
            if lead.id in seen:
                continue
            seen.add(lead.id)
            nxt = lead.sequence_state.next_action_at
            if lead.sequence_state.paused:
                continue
            if nxt and nxt > now:
                continue
            content = repo.get_content(lead.id)
            if not content:
                continue
            lead, _ = engine.tick(lead, content)
            repo.save_lead(lead)
            processed += 1
        session.commit()
    finally:
        session.close()
    logger.info("processed %s due steps", processed)
    if processed:
        from asda.models.events import EventType
        from asda.ops.activity import log

        log(EventType.SEQUENCE_STEP, summary=f"Worker sent {processed} due step(s)", count=processed)
    return processed


def poll_email_inbox() -> int:
    messages = fetch_unseen()
    if not messages:
        return 0
    init_db()
    session = get_session()
    handled = 0
    engine = SequenceEngine()
    try:
        repo = Repository(session)
        leads = repo.list_leads(limit=500)
        by_email = {l.email: l for l in leads if l.email}
        for msg in messages:
            lead = by_email.get(msg["from"])
            if not lead:
                continue
            thread = f"Subject: {msg['subject']}\n\n{msg['body']}"
            lead, _, _ = engine.ingest_reply(lead, thread, "email")
            repo.save_lead(lead)
            handled += 1
        session.commit()
    finally:
        session.close()
    logger.info("handled %s email replies", handled)
    return handled


def poll_linkedin_inbox() -> int:
    cfg = effective()
    if cfg.dry_run or not cfg.pb_inbox_agent_id:
        return 0
    from asda.modules.phantombuster import PhantomBusterClient, message_argument

    client = PhantomBusterClient()
    try:
        if cfg.pb_cookie:
            client.launch(cfg.pb_inbox_agent_id, {"sessionCookie": cfg.pb_cookie})
        rows = client.fetch_output_rows(cfg.pb_inbox_agent_id)
    except Exception:
        logger.exception("linkedin inbox poll failed")
        return 0
    if not rows:
        return 0
    init_db()
    session = get_session()
    handled = 0
    engine = SequenceEngine()
    try:
        repo = Repository(session)
        leads = repo.list_leads(limit=500)
        by_li = {(l.linkedin_url or "").rstrip("/").lower(): l for l in leads if l.linkedin_url}
        for row in rows:
            url = str(row.get("profileUrl") or row.get("linkedinUrl") or row.get("url") or "")
            url = url.rstrip("/").lower()
            lead = by_li.get(url)
            if not lead:
                continue
            text = str(row.get("message") or row.get("text") or row.get("lastMessage") or "")
            if not text:
                continue
            # skip our own outbound
            if "asda" in text.lower() and row.get("direction") == "out":
                continue
            lead, decision, _ = engine.ingest_reply(lead, text, "linkedin")
            if decision.should_auto_reply and decision.draft and not cfg.dry_run:
                try:
                    client.launch(
                        cfg.pb_message_agent_id,
                        message_argument(lead.linkedin_url, decision.draft, cfg.pb_cookie),
                    )
                except Exception:
                    logger.exception("linkedin auto-reply launch failed")
            repo.save_lead(lead)
            handled += 1
        session.commit()
    finally:
        session.close()
    return handled


def watch_inbox() -> int:
    settings = get_settings()
    inbox = settings.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    from asda.ingestion.pipeline import ingest_path

    ingested = 0
    for pattern in ("*.csv", "*.tsv", "*.xlsx", "*.xls"):
        for path in inbox.glob(pattern):
            result = ingest_path(path)
            ingested += int(result.get("total") or 0)
            path.rename(path.with_suffix(path.suffix + ".done"))
    if ingested:
        logger.info("folder-watch ingested %s leads", ingested)
    return ingested


def poll_instantly_replies() -> int:
    from asda.modules.instantly import InstantlyClient
    from asda.runtime import effective

    cfg = effective()
    if not cfg.instantly_key_set:
        return 0
    try:
        messages = InstantlyClient().recent_replies()
    except Exception:
        logger.exception("instantly inbox failed")
        return 0
    if not messages:
        return 0
    init_db()
    session = get_session()
    handled = 0
    engine = SequenceEngine()
    try:
        repo = Repository(session)
        by_email = {l.email: l for l in repo.list_leads(limit=500) if l.email}
        for msg in messages:
            addr = (msg.get("from") or "").split("<")[-1].split(">")[0].strip().lower()
            lead = by_email.get(addr)
            if not lead or not msg.get("body"):
                continue
            lead, _, _ = engine.ingest_reply(lead, msg["body"], "email")
            repo.save_lead(lead)
            handled += 1
        session.commit()
    finally:
        session.close()
    return handled


def _sunday_brief() -> None:
    from asda.agents.report import email_brief_to_cbo
    from asda.agents.learning import LearningAgent

    try:
        LearningAgent().run()
    except Exception:
        logger.exception("weekly learn failed")
    email_brief_to_cbo()


def _daily_snapshot() -> None:
    from asda.ops.analytics import capture_snapshot

    capture_snapshot()


def agent_tick() -> str:
    from asda.agents.brain import tick

    result = tick()
    applied = result.get("applied") or []
    return result.get("thought") or ("; ".join(applied[:3]) if applied else "tick")


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()
    sched = BlockingScheduler()
    from asda.ops.heartbeat import beat

    beat("worker", "ok", "scheduler started")
    sched.add_job(_wrap(process_due_steps, "due_steps"), "interval", minutes=5, id="due_steps")
    sched.add_job(_wrap(advance_new_leads, "new_leads"), "interval", minutes=5, id="new_leads")
    sched.add_job(_wrap(watch_inbox, "csv_inbox"), "interval", minutes=2, id="csv_inbox")
    sched.add_job(_wrap(poll_email_inbox, "email_inbox"), "interval", minutes=3, id="email_inbox")
    sched.add_job(_wrap(poll_linkedin_inbox, "li_inbox"), "interval", minutes=15, id="li_inbox")
    sched.add_job(_wrap(lambda: LearningAgent().run() and 1, "learn"), "cron", day_of_week="sun", hour=2, id="learn")
    sched.add_job(_wrap(_sunday_brief, "cbo_brief"), "cron", day_of_week="mon", hour=8, id="cbo_brief")
    sched.add_job(_wrap(_daily_snapshot, "snapshot"), "cron", hour=20, id="snapshot")
    sched.add_job(_wrap(poll_instantly_replies, "instantly_inbox"), "interval", minutes=5, id="instantly_inbox")
    sched.add_job(_wrap(agent_tick, "agent_tick"), "interval", minutes=5, id="agent_tick")
    logger.info("worker started")
    # First sweep immediately so the desk is not empty for 5 minutes
    try:
        _wrap(process_due_steps, "due_steps")()
        _wrap(advance_new_leads, "new_leads")()
        _wrap(agent_tick, "agent_tick")()
    except Exception:
        logger.exception("initial sweep failed")
    sched.start()


if __name__ == "__main__":
    run_forever()
