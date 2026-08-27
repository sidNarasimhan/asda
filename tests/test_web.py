from fastapi.testclient import TestClient

from asda.api.main import app


def test_home_redirects_to_full_page_onboard():
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/onboard")


def test_onboard_is_full_page_wizard():
    client = TestClient(app)
    r = client.get("/onboard")
    assert r.status_code == 200
    body = r.text
    assert "Hire your SDR" in body
    assert "Who I work for" in body
    assert "Step" in body and "of" in body
    assert "Pipeline" not in body
    assert "Desk checklist" not in body
    assert ">Home<" not in body


def test_onboard_who_step():
    client = TestClient(app)
    r = client.post(
        "/onboard",
        data={"step": "who", "company_name": "Altisec", "cbo_name": "Sid"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    nxt = client.get("/onboard")
    assert nxt.status_code == 200
    assert "OpenRouter" in nxt.text or "Brain" in nxt.text


def test_save_targets():
    client = TestClient(app)
    r = client.post(
        "/targets",
        data={"outreach": "10000", "replies": "250", "meetings": "40"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    from asda.runtime import load_runtime

    rt = load_runtime()
    assert rt.target_replies == 250
    assert rt.target_meetings == 40


def test_setup_redirects_onboard():
    client = TestClient(app)
    r = client.get("/setup", follow_redirects=False)
    assert r.status_code == 303
    assert "/onboard" in r.headers["location"]


def test_pipeline_page():
    client = TestClient(app)
    r = client.get("/pipeline")
    assert r.status_code == 200
    assert "Pipeline" in r.text
    assert "Mail" in r.text
    assert "LinkedIn" in r.text
    assert "Invite sent" in r.text


def test_leads_page_shows_direct_linkedin():
    from asda.db.repository import Repository
    from asda.db.session import get_session, init_db
    from asda.ingestion.normalize import normalize_row

    init_db()
    lead = normalize_row(
        {
            "Point of Contact": "Aniket Tale",
            "Company Name": "Cleartax",
            "Email ID": "aniket.tale@clear.in",
            "LinkedIn": "https://www.linkedin.com/in/aniket-tale-88314939/",
        },
        source="csv",
    )
    session = get_session()
    try:
        Repository(session).upsert_lead(lead)
        session.commit()
    finally:
        session.close()
    client = TestClient(app)
    page = client.get("/leads")
    assert page.status_code == 200
    assert "aniket.tale@clear.in" in page.text
    assert "linkedin.com/in/aniket-tale-88314939" in page.text
    assert "Has LinkedIn" in page.text
    filtered = client.get("/leads?ch=linkedin")
    assert "aniket-tale-88314939" in filtered.text


def test_share_password_blocks_anonymous():
    from asda.runtime import update_runtime

    update_runtime(share_password="test-share")
    client = TestClient(app)
    blocked = client.get("/leads", follow_redirects=False)
    assert blocked.status_code == 303
    assert "/login" in blocked.headers["location"]
    signed = client.post(
        "/login",
        data={"username": "asda", "password": "test-share", "next": "/leads"},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    page = client.get("/leads")
    assert page.status_code == 200
    assert "Drop CSV" in page.text
    update_runtime(share_password="")


def test_leads_has_dropzone():
    client = TestClient(app)
    page = client.get("/leads")
    assert page.status_code == 200
    assert "Drop CSV or Excel here" in page.text
    assert 'name="files"' in page.text
    assert "multiple" in page.text


def test_upload_csv_adds_people():
    client = TestClient(app)
    payload = b"Point of Contact,Company Name,Email ID\nAda West,Acme,ada@acme.test\n"
    r = client.post(
        "/leads/upload",
        files=[("files", ("people.csv", payload, "text/csv"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "/leads" in loc
    assert "ok=" in loc
    listed = client.get("/leads")
    assert "ada@acme.test" in listed.text
    assert "Ada West" in listed.text


def test_leads_and_reports_pages():
    client = TestClient(app)
    leads = client.get("/leads")
    assert leads.status_code == 200
    assert "By company" in leads.text
    reports = client.get("/reports")
    assert reports.status_code == 200
    assert "How I grow" in reports.text
    assert "Monday" in reports.text


def test_activity_page():
    client = TestClient(app)
    r = client.get("/activity")
    assert r.status_code == 200
    assert "Activity" in r.text
    assert "Hire ASDA" not in r.text


def test_mcp_tools_http():
    client = TestClient(app)
    r = client.get("/api/agent/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["tools"]]
    assert "asda.talk" in names
    assert "asda.worker.start" in names
    assert "asda.status" in names


def test_settings_has_no_instantly():
    client = TestClient(app)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Instantly" not in r.text
    assert "Apollo search" in r.text
    assert "Optional" in r.text
    assert "Company" in r.text
    assert "mcpServers" in r.text
    assert "OpenRouter" in r.text
    assert "POST /mcp" in r.text
    assert "Connect email" in r.text
    assert "Microsoft 365 work" in r.text
    assert "Outlook.com / Hotmail" in r.text
    assert "Add another" not in r.text
