from types import SimpleNamespace

from asda.modules.mail import check_imap, check_smtp, connect_reply_inbox, resolve_mail_spec


def test_smtp_requires_fields():
    ok, msg = check_smtp("", 587, "", "")
    assert not ok
    assert "required" in msg.lower()


def test_imap_requires_fields():
    ok, msg = check_imap("", 993, "", "")
    assert not ok


def test_outlook_personal_vs_work_hosts():
    personal = resolve_mail_spec("outlook_personal", "sam@hotmail.com")
    assert personal["smtp_host"] == "smtp-mail.outlook.com"
    assert personal["imap_host"] == "outlook.office365.com"
    work = resolve_mail_spec("outlook_work", "sam@acme.com")
    assert work["smtp_host"] == "smtp.office365.com"
    auto = resolve_mail_spec("gmail", "pat@outlook.com")
    assert auto["id"] == "outlook_personal"
    m365 = resolve_mail_spec("outlook", "pat@company.in")
    assert m365["smtp_host"] == "smtp.office365.com"


def test_connect_reply_inbox_rejects_empty():
    ok, msg = connect_reply_inbox("", "")
    assert ok is False
    assert "gmail" in msg.lower() or "password" in msg.lower()


def test_outlook_onboard_saves_without_imap(monkeypatch):
    from asda.ops.onboard import _save_mail
    from asda.runtime import load_runtime

    monkeypatch.setattr("asda.modules.mail.check_smtp", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr("asda.modules.mail.check_imap", lambda *a, **k: (False, "Basic authentication is disabled"))
    err = _save_mail("karthik.t@altisec.in", "abcd efgh ijkl mnop", provider="outlook_work")
    assert err is None
    rt = load_runtime()
    assert rt.smtp_verified is True
    assert rt.imap_verified is False
    assert rt.graph_skipped is True
    assert rt.smtp_user == "karthik.t@altisec.in"


def test_smtp_sets_reply_to_when_gmail_inbox(monkeypatch):
    captured = {}

    class DummySMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a, **k):
            pass

        def send_message(self, msg):
            captured["reply"] = msg.get("Reply-To")
            captured["from"] = msg.get("From")

    monkeypatch.setattr("smtplib.SMTP", DummySMTP)
    monkeypatch.setattr(
        "asda.runtime.effective",
        lambda: SimpleNamespace(
            smtp_host="smtp.office365.com",
            smtp_port=587,
            smtp_user="karthik.t@altisec.in",
            smtp_password="x",
            smtp_from="karthik.t@altisec.in",
            smtp_reply_to="replies@gmail.com",
        ),
    )
    from asda.models.content import SequenceEmail
    from asda.models.lead import Lead
    from asda.modules.esp import SMTPESP

    SMTPESP().send(Lead(email="prospect@acme.com", first_name="Sam"), SequenceEmail(subject="Hi", body="Hello"))
    assert captured["from"] == "karthik.t@altisec.in"
    assert captured["reply"] == "replies@gmail.com"
