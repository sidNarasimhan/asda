from asda.db.repository import Repository
from asda.db.session import get_session
from asda.ingestion.normalize import fingerprint_for
from asda.models.lead import Company, Lead


def test_same_person_email_and_linkedin_merge():
    session = get_session()
    repo = Repository(session)
    a = Lead(
        first_name="Sam",
        last_name="Iyer",
        email="sam@hearth.co",
        company=Company(name="Hearth"),
        source="csv",
    )
    a.fingerprint = fingerprint_for(a)
    repo.upsert_lead(a)
    b = Lead(
        first_name="Sam",
        last_name="Iyer",
        email="sam@hearth.co",
        linkedin_url="https://www.linkedin.com/in/samiyer",
        phone="+91 98450 11111",
        company=Company(name="Hearth"),
        source="csv",
    )
    b.fingerprint = fingerprint_for(b)
    merged, created = repo.upsert_lead(b)
    session.commit()
    assert created is False
    assert merged.linkedin_url.endswith("samiyer")
    assert merged.phone
    assert len(repo.list_leads(limit=20)) == 1
    session.close()


def test_digest_csv_into_book(tmp_path):
    from asda.ops.digest import digest_bytes

    csv = b"first_name,last_name,email,company\nAva,Chen,ava@northwind.io,Northwind\n"
    out = digest_bytes("people.csv", csv)
    assert out["kind"] == "leads"
    assert out["leads"] == 1
