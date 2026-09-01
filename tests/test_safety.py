from datetime import datetime, timezone

from asda.config import get_settings
from asda.models.lead import Lead
from asda.modules.safety import SafetyGate


def test_dry_run_always_allows():
    assert get_settings().dry_run is True
    ok, reason = SafetyGate().allow("email", Lead(email="a@b.com"))
    assert ok
    assert reason == "dry_run"


def test_role_accounts_blocked_when_not_dry(monkeypatch, tmp_path):
    monkeypatch.setenv("ASDA_DRY_RUN", "false")
    monkeypatch.setenv("ASDA_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from asda.config import Settings
    from asda.runtime import update_runtime

    s = Settings()
    assert s.dry_run is False
    update_runtime(live_confirmed=True, dry_run=False)
    gate = SafetyGate()
    gate.settings = s
    ok, reason = gate.allow("email", Lead(email="noreply@harborops.com"))
    assert not ok
    assert reason == "role_account_suppressed"
    get_settings.cache_clear()


def test_linkedin_window_weekdays_and_hours():
    gate = SafetyGate()
    saturday = datetime(2026, 8, 22, 6, 0, tzinfo=timezone.utc)  # 11:30 IST Saturday
    ok, why = gate._linkedin_window(saturday)
    assert not ok
    assert why == "weekend"
    monday_early = datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc)  # 7:30 IST Monday
    ok, why = gate._linkedin_window(monday_early)
    assert not ok
    assert why == "outside_hours"
    monday_open = datetime(2026, 8, 24, 5, 30, tzinfo=timezone.utc)  # 11:00 IST Monday
    ok, why = gate._linkedin_window(monday_open)
    assert ok


def test_linkedin_connect_daily_cap_is_strict(monkeypatch, tmp_path):
    monkeypatch.setenv("ASDA_DRY_RUN", "false")
    monkeypatch.setenv("ASDA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ASDA_LINKEDIN_CONNECT_DAILY_CAP", "8")
    get_settings.cache_clear()
    from asda.config import Settings
    from asda.runtime import update_runtime

    update_runtime(live_confirmed=True, dry_run=False)
    gate = SafetyGate()
    gate.settings = Settings()
    for _ in range(8):
        gate.record_send("linkedin_connect")
    ok, reason = gate.allow("linkedin_connect", Lead(linkedin_url="https://www.linkedin.com/in/x"))
    assert not ok
    assert reason == "daily_cap_reached"
    get_settings.cache_clear()


def test_held_connect_is_not_marked_sent():
    from asda.agents.linkedin_outreach import LinkedInOutreachAgent
    from asda.models.content import GeneratedContent
    from asda.models.lead import Lead

    agent = LinkedInOutreachAgent()
    agent.safety.allow = lambda *a, **k: (False, "daily_cap_reached")
    lead = Lead(first_name="Ava", linkedin_url="https://www.linkedin.com/in/ava")
    lead, logs = agent.send_connect(lead, GeneratedContent())
    assert lead.sequence_state.linkedin_stage in {"", "idle"}
    assert not any(o.kind == "linkedin_connect" for o in lead.outcomes)
    assert logs and logs[0].action == "held"


def test_email_minimum_gap_is_enforced_when_live(monkeypatch, tmp_path):
    monkeypatch.setenv("ASDA_DRY_RUN", "false")
    monkeypatch.setenv("ASDA_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from asda.config import Settings
    from asda.db.models import EventRow
    from asda.db.session import get_session, init_db
    init_db()
    session = get_session()
    session.add(EventRow(id="recent-email", type="email.sent", payload={}, ts=datetime.now(timezone.utc)))
    session.commit()
    session.close()
    gate = SafetyGate()
    gate.settings = Settings()
    ok, reason = gate._min_gap_ok("email")
    assert not ok
    assert reason == "too_soon"
    get_settings.cache_clear()
