from pathlib import Path

from asda.db.repository import Repository
from asda.db.session import get_session
from asda.ingestion.csv_source import CSVSource
from asda.ingestion.normalize import extract_domain, is_valid_lead, normalize_row
from asda.ingestion.registry import get_registry
from asda.ingestion.webhook import WebhookSource
from asda.models.lead import LeadQuery


def test_csv_ingest():
    path = Path(__file__).resolve().parents[1] / "sample_data" / "leads.csv"
    leads = CSVSource().fetch(LeadQuery(limit=10, extra={"path": str(path)}))
    assert len(leads) == 5
    assert leads[0].email.endswith("@northwind.io")
    assert leads[0].company.domain == "northwind.io"
    assert leads[0].fingerprint


def test_email_id_takes_first_address():
    lead = normalize_row(
        {
            "Point of Contact": "Aniket Tale",
            "Email ID": "aniket.tale@clear.in\naniket.tale@gmail.com",
            "Company Name": "Cleartax",
            "Designation": "IT Manager",
        },
        source="csv",
    )
    assert lead.first_name == "Aniket"
    assert lead.email == "aniket.tale@clear.in"
    assert lead.title == "IT Manager"
    assert lead.company.name == "Cleartax"


def test_alias_columns():
    lead = normalize_row(
        {
            "First Name": "Ava",
            "Last Name": "Chen",
            "Email Address": "ava@x.com",
            "Job Title": "VP Sales",
            "Organization": "X",
            "Website": "https://www.x.com",
        },
        source="csv",
    )
    assert lead.first_name == "Ava"
    assert lead.title == "VP Sales"
    assert lead.company.domain == "x.com"


def test_webhook_list():
    leads = WebhookSource().fetch(
        LeadQuery(extra={"payload": [{"name": "Priya Nair", "email": "p@lumenfield.co"}]})
    )
    assert len(leads) == 1
    assert leads[0].first_name == "Priya"


def test_dedup_on_email():
    session = get_session()
    repo = Repository(session)
    a = normalize_row({"email": "ava@x.com", "first_name": "Ava", "company": "X"}, "csv")
    b = normalize_row({"email": "ava@x.com", "first_name": "Ava", "title": "VP"}, "apollo")
    saved, created = repo.upsert_lead(a)
    assert created
    merged, created2 = repo.upsert_lead(b)
    assert not created2
    assert merged.title == "VP"
    assert saved.id == merged.id
    session.close()


def test_merge_linkedin_host_and_name_company():
    from asda.ingestion.pipeline import persist_leads, merge_duplicate_leads, reclean_book

    a = normalize_row(
        {"Name": "Rajiv Kelkar", "Company": "First Meridian", "LinkedIn": "https://in.linkedin.com/in/rajivkelkar"},
        "csv",
    )
    b = normalize_row(
        {
            "Name": "Rajiv Kelkar",
            "Company": "First Meridian",
            "Work Email": "rajiv.kelkar@firstmeridian.com",
            "LinkedIn": "https://www.linkedin.com/in/rajivkelkar",
        },
        "csv",
    )
    persist_leads([a, b])
    reclean_book()
    merge_duplicate_leads()
    session = get_session()
    people = Repository(session).list_leads(limit=50)
    session.close()
    named = [p for p in people if "Rajiv" in p.full_name]
    assert len(named) == 1
    assert named[0].email == "rajiv.kelkar@firstmeridian.com"
    assert named[0].linkedin_url == "https://www.linkedin.com/in/rajivkelkar"


def test_merge_name_and_company_when_one_copy_has_email():
    from asda.ingestion.pipeline import persist_leads, merge_duplicate_leads

    a = normalize_row({"Client Name": "Ismail Mohideen", "Organization": "Meesho"}, "csv")
    b = normalize_row(
        {"Client Name": "Ismail Mohideen", "Organization": "Meesho", "Email ID": "ismail.mohideen@meesho.com"},
        "csv",
    )
    persist_leads([a, b])
    n = merge_duplicate_leads()
    session = get_session()
    people = [p for p in Repository(session).list_leads(limit=50) if "Ismail" in p.full_name]
    session.close()
    assert n >= 1
    assert len(people) == 1
    assert people[0].email == "ismail.mohideen@meesho.com"


def test_switchboard_phone_does_not_merge_different_people():
    from asda.ingestion.pipeline import persist_leads, merge_duplicate_leads

    a = normalize_row(
        {"Name": "Sidram Singh", "Company": "AXISCADES", "Phone Number": "8041939000"},
        "csv",
    )
    b = normalize_row(
        {
            "Name": "Vishwanath G Chinivalar",
            "Company": "AXISCADES",
            "Phone Number": "8041939000",
            "Email ID": "vishwanath.c@axiscades.com",
        },
        "csv",
    )
    persist_leads([a, b])
    merge_duplicate_leads()
    session = get_session()
    people = Repository(session).list_leads(limit=50)
    session.close()
    names = sorted(p.full_name for p in people)
    assert "Sidram Singh" in names
    assert any("Vishwanath" in n for n in names)
    assert len(people) == 2


def test_apollo_export_shape(tmp_path):
    path = tmp_path / "apollo.csv"
    path.write_text(
        "First Name,Last Name,Title,Company,Email,Email Status,Corporate Phone,Person Linkedin Url,Website,# Employees,City,Country,Company Linkedin Url\n"
        "Ananya,Mehta,CEO,BareBloom,ananya@barebloom.in,Verified,+91 98450 11223,https://www.linkedin.com/in/ananya-mehta,https://barebloom.in,42,Bengaluru,India,https://www.linkedin.com/company/barebloom\n"
    )
    leads = CSVSource().fetch(LeadQuery(limit=10, extra={"path": str(path)}))
    assert len(leads) == 1
    lead = leads[0]
    assert lead.first_name == "Ananya"
    assert lead.email == "ananya@barebloom.in"
    assert "ananya-mehta" in lead.linkedin_url
    assert "company/barebloom" not in lead.linkedin_url
    assert lead.company.domain == "barebloom.in"
    assert lead.phone
    assert lead.company.size == "42"


def test_messy_delimiter_and_extra_columns(tmp_path):
    path = tmp_path / "eu.csv"
    path.write_text(
        "Nom;Prenom;Societe;Courriel;Poste;LinkedIn;Notes;Score\n"
        "Dupont;Marie;Acme;marie@acme.fr;COO;https://linkedin.com/in/mariedupont;ignore me;99\n"
    )
    # Semicolon file. Our aliases don't include French Nom/Prenom/Courriel,
    # so value inference should still catch email + LinkedIn + name-ish company.
    leads = CSVSource().fetch(LeadQuery(limit=10, extra={"path": str(path)}))
    assert len(leads) == 1
    assert leads[0].email == "marie@acme.fr"
    assert leads[0].first_name == "Marie"
    assert leads[0].last_name == "Dupont"
    assert leads[0].company.name == "Acme"
    assert "mariedupont" in leads[0].linkedin_url


def test_full_name_and_buried_fields():
    lead = normalize_row(
        {
            "Contact Name": "Reed, Marcus",
            "Primary Contact": "Reach marcus@harborops.com or +1 512 555 0101",
            "Profile": "linkedin.com/in/marcusreed",
            "Employer": "HarborOps",
            "Junk": "not a lead field",
            "Email Status": "Verified",
        },
        source="csv",
    )
    assert lead.first_name == "Marcus"
    assert lead.last_name == "Reed"
    assert lead.email == "marcus@harborops.com"
    assert "marcusreed" in lead.linkedin_url
    assert lead.company.name == "HarborOps"


def test_phone_only_is_valid():
    lead = normalize_row({"Mobile": "+91 98450 11223", "Company": "BoxUp"}, "csv")
    ok, _ = is_valid_lead(lead)
    assert ok
    assert lead.phone


def test_extract_domain_from_site_and_email():
    assert extract_domain("https://www.harborops.com/about") == "harborops.com"
    assert extract_domain("marcus@harborops.com") == "harborops.com"


def test_registry_has_core_sources():
    names = get_registry().names()
    for required in ("csv", "apollo", "webhook", "sheets", "zoominfo", "signalhire", "clay"):
        assert required in names


def test_signalhire_profile_normalization():
    from asda.ingestion.signalhire import SignalHireSource

    leads = []
    SignalHireSource._append_profiles(
        leads,
        [{
            "uid": "10000000000000000000000000001006",
            "fullName": "Aaron Smith",
            "location": "London, United Kingdom",
            "experience": [{"company": "Saward Dawson", "title": "Accountant"}],
            "social": [{"type": "li", "link": "https://www.linkedin.com/in/aaron-smith"}],
            "contacts": [
                {"type": "email", "value": "aaron@sawarddawson.com"},
                {"type": "phone", "value": "+44 20 1234 5678"},
            ],
        }],
        10,
    )
    assert len(leads) == 1
    assert leads[0].full_name == "Aaron Smith"
    assert leads[0].title == "Accountant"
    assert leads[0].company.name == "Saward Dawson"
    assert leads[0].linkedin_url.endswith("aaron-smith")
    assert leads[0].email == "aaron@sawarddawson.com"
    assert leads[0].phone
