from datetime import datetime, timedelta, timezone

from asda.models.content import GeneratedContent, LinkedInSequence, SequenceEmail
from asda.models.lead import Lead, SequenceState
from asda.ops.tracker import tracker
from asda.ops.voice import humanize, looks_like_ai


def test_tracker_shows_scheduled_mail_and_linkedin():
    lead = Lead(
        first_name="Ava",
        last_name="Chen",
        email="ava@northwind.io",
        linkedin_url="https://www.linkedin.com/in/avachen",
        sequence_state=SequenceState(
            email_step=1,
            next_email_at=datetime.now(timezone.utc) + timedelta(days=3),
            linkedin_stage="connect_sent",
        ),
    )
    content = GeneratedContent(
        emails=[
            SequenceEmail(step=1, subject="Diwali lanes", body="Ava, saw the Bengaluru lanes."),
            SequenceEmail(step=2, subject="follow", body="any luck on RTO?"),
        ],
        linkedin=LinkedInSequence(connection_note="Saw your ops post on RTO."),
    )
    tr = tracker(lead, content)
    assert tr["channels"]["email"]
    assert tr["channels"]["linkedin"]
    assert tr["email"][0]["status"] == "sent"
    assert tr["email"][1]["status"] == "scheduled"
    assert any(x["label"] == "Connection request" for x in tr["linkedin"])


def test_humanize_strips_em_dash_and_filler():
    raw = "I hope this email finds you well — wanted to reach out to unlock synergy."
    out = humanize(raw)
    assert "—" not in out
    assert "hope this email" not in out.lower()
    assert looks_like_ai(raw) is True
    assert looks_like_ai("Ava, saw you opened Pune this week. How is COD looking?") is False
