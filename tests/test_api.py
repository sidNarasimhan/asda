from fastapi.testclient import TestClient

from asda.api.main import app


def test_health():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_ingest_webhook_and_list():
    client = TestClient(app)
    resp = client.post(
        "/api/ingest/webhook",
        json={"first_name": "Ava", "last_name": "Chen", "email": "ava@northwind.io", "company": "Northwind"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    listed = client.get("/api/leads")
    assert listed.status_code == 200
    assert any(l["email"] == "ava@northwind.io" for l in listed.json())
