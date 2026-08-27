from asda.ingestion.normalize import fingerprint_for, is_valid_lead
from asda.models.lead import Company, Lead, LeadStatus


def test_email_normalized():
    lead = Lead(email="  Marcus@HarborOps.COM ")
    assert lead.email == "marcus@harborops.com"


def test_linkedin_prefixed():
    lead = Lead(linkedin_url="linkedin.com/in/ava")
    assert lead.linkedin_url.startswith("https://")


def test_fingerprint_stable():
    a = Lead(email="a@b.com")
    b = Lead(email="A@B.com")
    assert fingerprint_for(a) == fingerprint_for(b)


def test_invalid_without_identity():
    ok, reason = is_valid_lead(Lead())
    assert not ok
    assert "identity" in reason


def test_status_enum_roundtrip():
    lead = Lead(first_name="A", status=LeadStatus.RESEARCHED, company=Company(name="X"))
    dumped = lead.model_dump()
    again = Lead.model_validate(dumped)
    assert again.status is LeadStatus.RESEARCHED
