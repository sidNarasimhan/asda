"""ASDA — talk to your SDR. Setup, pipeline, weekly brief."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from asda.agents.employee import talk
from asda.agents.orchestrator import Orchestrator
from asda.agents.report import build_brief, email_brief_to_cbo
from asda.agents.sequence import SequenceEngine
from asda.config import get_settings
from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.ingestion.apollo import ApolloPlanError, ApolloSource
from asda.ingestion.csv_source import CSVSource
from asda.models.lead import PIPELINE_COLUMNS, LeadQuery
from asda.ops.worker import ensure_worker, worker_status
from asda.runtime import effective, setup_status
from asda.ui.setup import render_setup

st.set_page_config(page_title="ASDA", page_icon="⚡", layout="wide")
init_db()
settings = get_settings()
cfg = effective()
status = setup_status()
_worker = ensure_worker()

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.1rem; max-width: 1100px; }
      h1 { letter-spacing: -0.04em; font-size: 1.65rem; }
      div[data-testid="stMetric"] { background: #0f172a08; padding: 10px 12px; border-radius: 12px; }
      .card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def repo_session():
    s = get_session()
    return s, Repository(s)


with st.sidebar:
    st.markdown("**ASDA**")
    st.caption("Your SDR. Talk to it like a hire.")
    page = st.radio("Go", ["Talk", "Pipeline", "Leads", "Setup"], label_visibility="collapsed")
    st.divider()
    st.caption("LIVE" if not cfg.dry_run else "Learning mode · not sending yet")
    w = worker_status()
    st.caption("Worker on" if w.get("running") else "Worker starting…")
    done = sum(1 for v in status["steps"].values() if v)
    st.progress(done / max(len(status["steps"]), 1), text=f"Onboarding {done}/{len(status['steps'])}")


if page == "Talk":
    st.title("Talk to ASDA")
    session, repo = repo_session()
    try:
        m = repo.metrics()
    finally:
        session.close()
    a, b, c, d = st.columns(4)
    a.metric("Leads", m["total_leads"])
    b.metric("Replies", m["replies"])
    c.metric("Meetings", m["meetings"])
    d.metric("Per 100", m["meetings_per_100"])

    if "chat" not in st.session_state:
        st.session_state.chat = []
    for turn in st.session_state.chat:
        with st.chat_message(turn["role"]):
            st.write(turn["text"])
            if turn.get("applied"):
                st.caption(" · ".join(turn["applied"]))

    prompt = st.chat_input("e.g. Only target series-B SaaS CROs. Pause LinkedIn this week. How did we do?")
    if prompt:
        st.session_state.chat.append({"role": "user", "text": prompt})
        with st.spinner("ASDA…"):
            result = talk(prompt)
        st.session_state.chat.append(
            {"role": "assistant", "text": result["reply"], "applied": result.get("applied")}
        )
        st.rerun()

    st.divider()
    st.subheader("This week's brief")
    if st.button("Write the brief"):
        st.session_state.brief = build_brief()
    if st.session_state.get("brief"):
        st.text(st.session_state.brief)
        if st.button("Email it to me"):
            st.json(email_brief_to_cbo())
    st.caption("The worker also emails this every Monday 8:00 if your address is in Setup.")
    st.divider()
    st.subheader("Test run")
    st.write(
        "Loads a mock Apollo-style list (India D2C / gifting). Writes a real pitch. "
        "Sends one test email to **your** Gmail. Tries LinkedIn (OK if LinkedIn itself errors)."
    )
    if st.button("Run a test now", type="primary"):
        from asda.ops.test_run import run_smoke

        with st.spinner("Research + test email + LinkedIn… this can take a minute"):
            result = run_smoke()
        if result.get("ok"):
            st.success(f"{result['lead']} @ {result['company']} · score {result['score']}")
        else:
            st.error(result.get("error") or "Test did not finish")
        st.write("**Email step**", result.get("email_send"))
        st.write("**LinkedIn step**", result.get("linkedin_send"))
        if result.get("email_subject"):
            st.caption("Subject: " + result["email_subject"])
        if result.get("linkedin_note"):
            st.caption("LI note: " + result["linkedin_note"][:200])

elif page == "Pipeline":
    st.title("Board")
    session, repo = repo_session()
    try:
        leads = repo.list_leads(limit=400)
        by: dict = {}
        for lead in leads:
            by.setdefault(lead.status.value, []).append(lead)
        cols = st.columns(len(PIPELINE_COLUMNS))
        for (key, label), col in zip(PIPELINE_COLUMNS, cols):
            items = by.get(key, [])
            with col:
                st.markdown(f"**{label}** · {len(items)}")
                for lead in items[:10]:
                    st.markdown(
                        f"<div class='card'><b>{lead.full_name or '—'}</b><br>"
                        f"<small>{lead.company.name}<br>score {lead.score}</small></div>",
                        unsafe_allow_html=True,
                    )
        if not leads:
            st.info("Add a CSV or Apollo list under Leads. Then tell ASDA to start.")
    finally:
        session.close()

elif page == "Leads":
    st.title("Leads")
    tab_in, tab_run, tab_one = st.tabs(["Add", "Run", "One lead"])
    with tab_in:
        st.caption("Apollo Free can't search via API. Export CSV from Apollo and drop it here.")
        uploaded = st.file_uploader("CSV", type=["csv"])
        if uploaded:
            dest = settings.data_dir / "uploads" / uploaded.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(uploaded.getvalue())
            found = CSVSource().fetch(LeadQuery(limit=10_000, extra={"path": str(dest)}))
            session, repo = repo_session()
            n = 0
            try:
                for lead in found:
                    _, created = repo.upsert_lead(lead)
                    n += int(created)
                session.commit()
            finally:
                session.close()
            st.success(f"{len(found)} rows · {n} new")
        with st.expander("Apollo API search (paid plan)"):
            titles = st.text_input("Titles", "CEO, VP Sales")
            geo = st.text_input("Locations", "United States")
            if st.button("Search Apollo"):
                q = LeadQuery(
                    titles=[t.strip() for t in titles.split(",") if t.strip()],
                    locations=[g.strip() for g in geo.split(",") if g.strip()],
                    limit=15,
                )
                try:
                    found = ApolloSource().fetch(q)
                    session, repo = repo_session()
                    try:
                        for lead in found:
                            repo.upsert_lead(lead)
                        session.commit()
                    finally:
                        session.close()
                    st.success(f"{len(found)} ingested")
                except ApolloPlanError as exc:
                    st.error(str(exc))

    with tab_run:
        n_run = st.slider("How many new leads", 1, 20, 5)
        send = st.checkbox("Start outreach after research", value=False)
        if send and cfg.dry_run:
            st.caption("Still in learning mode — outreach is logged, not sent.")
        if st.button("Run", type="primary"):
            session, repo = repo_session()
            try:
                batch = repo.list_leads(status="new", limit=n_run)
            finally:
                session.close()
            orch = Orchestrator()
            rows = []
            for lead in batch:
                r = orch.run(lead, skip_outreach=not send)
                rows.append(
                    {
                        "name": r["lead"].full_name,
                        "score": r["lead"].score,
                        "decision": r["decision"],
                    }
                )
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            else:
                st.info("Nothing in New.")

    with tab_one:
        session, repo = repo_session()
        try:
            leads = repo.list_leads(limit=200)
            if not leads:
                st.info("No leads.")
            else:
                label_map = {f"{l.full_name} · {l.company.name}": l.id for l in leads}
                pick = st.selectbox("Open", list(label_map))
                lead = next(l for l in leads if l.id == label_map[pick])
                st.write(lead.email, lead.linkedin_url, lead.phone)
                st.caption(lead.status.value)
                inbound = st.text_area("Paste a reply")
                if st.button("Handle reply") and inbound:
                    lead, decision, _ = SequenceEngine().ingest_reply(lead, inbound, "email")
                    repo.save_lead(lead)
                    session.commit()
                    st.write(decision.draft)
        finally:
            session.close()

elif page == "Setup":
    render_setup()
