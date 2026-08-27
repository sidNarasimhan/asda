from asda.modules.graph_mail import _ms_error, _tenant, start_device_login


def test_device_login_needs_client_id():
    out = start_device_login("")
    assert out["ok"] is False
    assert "client" in out["error"].lower()


def test_tenant_from_mailbox_domain():
    class Fake:
        ms_tenant = ""
        smtp_user = "karthik.t@altisec.in"
        smtp_from = ""
        imap_user = ""
        ms_user = ""

    assert _tenant(Fake()) == "altisec.in"


def test_tenant_explicit_wins():
    class Fake:
        ms_tenant = "contoso.onmicrosoft.com"
        smtp_user = "a@other.com"
        smtp_from = ""
        imap_user = ""
        ms_user = ""

    assert _tenant(Fake()) == "contoso.onmicrosoft.com"


def test_ms_error_hides_json_blob():
    raw = '{"error":"invalid_request","error_description":"AADSTS50059: No tenant-identifying information found. Trace ID: abc"}'
    msg = _ms_error(raw)
    assert "AADSTS50059" not in msg or "mailbox domain" in msg
    assert "Trace ID" not in msg
    assert "mailbox domain" in msg
