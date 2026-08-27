from datetime import datetime, timedelta, timezone

from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.lead import Lead, LeadStatus, SequenceState
from asda.ops.hygiene import looks_fake, purge_fake_leads
from asda.ops.workboard import workboard


def _save(lead: Lead):
    session = get_session()
    try:
        Repository(session).upsert_lead(lead)
        session.commit()
    finally:
        session.close()


def test_email_and_linkedin_lanes(sample_lead):
    now = datetime.now(timezone.utc)
    sample_lead.status = LeadStatus.SEQUENCED
    sample_lead.sequence_state = SequenceState(
        email_step=1,
        next_email_at=now + timedelta(days=3),
        linkedin_stage="connect_sent",
        next_linkedin_at=now + timedelta(days=1),
    )
    _save(sample_lead)
    board = workboard({"running": False})
    email_sent = [l["name"] for l in board["email"][1]["leads"]]
    li_invites = [l["name"] for l in board["linkedin"][0]["leads"]]
    assert sample_lead.full_name in email_sent
    assert sample_lead.full_name in li_invites
    assert board["now"]["headline"]


def test_conversation_lane(sample_lead):
    sample_lead.status = LeadStatus.REPLIED
    sample_lead.sequence_state = SequenceState(
        email_step=1,
        email_replied=True,
        thread=[{"channel": "email", "role": "them", "text": "let's talk Thursday"}],
    )
    _save(sample_lead)
    board = workboard({"running": False})
    convos = [l["name"] for l in board["email"][2]["leads"]]
    assert sample_lead.full_name in convos


def test_purge_keeps_live_people(sample_lead):
    from asda.models.lead import Company, Lead
    from asda.ingestion.normalize import fingerprint_for

    sample_lead.email = "ava.chen@northwind.io"
    sample_lead.company = Company(name="Northwind Analytics")
    sample_lead.source = "csv"
    sample_lead.fingerprint = fingerprint_for(sample_lead)
    _save(sample_lead)

    keeper = Lead(
        first_name="Sanath",
        last_name="Narasimhan",
        email="sanath.narasimhan@gmail.com",
        source="manual",
        company=Company(name="Personal"),
    )
    keeper.fingerprint = fingerprint_for(keeper)
    _save(keeper)

    assert looks_fake(sample_lead)
    assert not looks_fake(keeper)
    result = purge_fake_leads()
    assert result["count"] >= 1
    assert any("Sanath" in n for n in result["kept"])
