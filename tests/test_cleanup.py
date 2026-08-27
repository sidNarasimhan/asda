from asda.ingestion.cleanup import (
    canonical_company_map,
    clean_company,
    extract_emails,
    extract_phones,
    infer_company_from_email,
    is_dnr,
    pick_email,
    split_contacts,
    tidy_person_name,
)
from asda.ingestion.normalize import normalize_row
from asda.ingestion.workbook import tables_from
from asda.models.lead import LeadStatus


def test_census_collapses_duplicate_rows(tmp_path):
    from openpyxl import Workbook
    from asda.ingestion.census import census_files

    path = tmp_path / "copies.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Consolidated Accounts"
    ws.append(["Sl No", "Company Name", "Point of Contact", "Email ID"])
    for _ in range(7):
        ws.append([1, "Razorpay", "Anand Choksi", "anand.choksi@razorpay.com"])
        ws.append([2, "Razorpay", "Prathamesh Joshi", "prathamesh.joshi@razorpay.com"])
    wb.save(path)
    report = census_files([path])
    assert report["named_people_rows"] == 14
    assert report["unique_people"] == 2


def test_company_aliases_collapse_to_one_name():
    mapping = canonical_company_map(
        [
            "Aequs",
            "Aequs Limited",
            "Acko",
            "Acko General Insurance",
            "Ace Multi Axes Systems Limited",
            "Ace Turtle Services Private Limited",
            "Affine",
            "Affine Analytics",
        ]
    )
    assert mapping["Aequs"] == mapping["Aequs Limited"] == "Aequs Limited"
    assert mapping["Acko"] == mapping["Acko General Insurance"]
    assert "Acko" in mapping["Acko"]
    assert mapping["Affine"] == mapping["Affine Analytics"]
    assert mapping["Ace Multi Axes Systems Limited"] != mapping["Ace Turtle Services Private Limited"]


def test_infer_acko_from_email():
    known = ["Acko General Insurance", "Razorpay", "Porter (SmartShift Logistics)"]
    assert infer_company_from_email("vivek.s@acko.com", known) == "Acko General Insurance"
    assert infer_company_from_email("someone@gmail.com", known) == ""


def test_tidy_names():
    assert tidy_person_name("CHARAN", "RAJU G") == ("Charan", "Raju G")
    assert tidy_person_name("venkatesan", "babu") == ("Venkatesan", "Babu")
    assert tidy_person_name("Dr.", "Basavanna")[0] == "Dr."


def test_prefers_work_email_over_gmail():
    emails = extract_emails(
        "aniket.tale@gmail.com\nPersonal email\naniket.tale@clear.in\naniket_tale@whirlpool.com"
    )
    assert "aniket.tale@clear.in" in emails
    picked = pick_email(emails, company_name="Cleartax (Defmacro Software Private Limited)")
    assert picked == "aniket.tale@clear.in"


def test_indian_phone_to_e164():
    assert extract_phones("9686860491") == "+919686860491"
    assert extract_phones("9663909661 | 9886652466") == "+919663909661"
    assert extract_phones("24-06-2026: DNR") == ""
    assert extract_phones("+97400182260") == "+919740018226"


def test_address_is_not_a_company():
    assert clean_company("SJR Cyber Laskar, Hosur Rd, Adugodi, Bengaluru, Karnataka 560030") == ""
    assert clean_company("Talk about SOC") == ""
    assert clean_company(" Razorpay") == "Razorpay"


def test_dnr_from_remarks():
    assert is_dnr("DNR x2 . Sent a message and email.")
    assert is_dnr("24-06-2026: DNR. Left a voice message.")
    assert not is_dnr("Told me to send an email")


def test_split_two_people_in_one_cell():
    people = split_contacts(
        "Krish Srikant (VP IT), Sivakami Balan (GM - IT, Supply Chain)"
    )
    assert len(people) == 2
    assert people[0][0] == "Krish Srikant"
    assert "VP IT" in people[0][1]
    assert people[1][0] == "Sivakami Balan"


def test_normalize_workbook_row_continuation_company():
    lead = normalize_row(
        {
            "Sl No": "",
            "Company Name": "Ace Multi Axes Systems Limited",
            "Point of Contact": "Manjunath",
            "Designation": "IT Team",
            "Phone Number": "7022279088",
            "Email ID": "manjunath@acemulti.net",
            "Remarks": "Share profile on Email.",
        },
        source="csv",
    )
    assert lead.first_name == "Manjunath"
    assert lead.email == "manjunath@acemulti.net"
    assert lead.phone == "+917022279088"
    assert lead.company.name == "Ace Multi Axes Systems Limited"
    assert lead.company.domain == "acemulti.net"


def test_poc_is_the_person_not_the_company():
    lead = normalize_row(
        {
            "Company Name": "Wipro",
            "P.O.C": "Janmejay Behera,",
            "Email ID": "janmejaybehera@gmail.com",
            "Contact No": "9739568568",
        },
        source="csv",
    )
    assert "Janmejay" in lead.full_name
    assert lead.company.name == "Wipro"
    assert lead.email == "janmejaybehera@gmail.com"


def test_client_name_maps_to_person():
    lead = normalize_row(
        {
            "Client Name": "Ismail Mohideen",
            "Organization": "Meesho",
            "Designation": "Head of IT",
        },
        source="csv",
    )
    assert lead.first_name == "Ismail"
    assert lead.company.name == "Meesho"


def test_placeholder_rows_are_not_people():
    from asda.ingestion.normalize import is_valid_lead

    lead = normalize_row(
        {
            "Company Name": "Tunga Aerospace",
            "P.O.C": "[Name]",
            "Email ID": "[Email]",
            "Designation": "[Title]",
        },
        source="csv",
    )
    ok, reason = is_valid_lead(lead)
    assert not ok
    assert reason == "placeholder"


def test_dnr_row_is_suppressed_not_mailed():
    lead = normalize_row(
        {
            "Company Name": "Cleartax",
            "Point of Contact": "Aniket Tale",
            "Email ID": "aniket.tale@clear.in\naniket.tale@gmail.com",
            "Remarks": "DNR x2 . Sent a message and email.",
        },
        source="csv",
    )
    assert lead.email == "aniket.tale@clear.in"
    assert lead.status == LeadStatus.SUPPRESSED
    assert "dnr" in lead.tags


def test_linkedin_inside_parens_does_not_break_name():
    lead = normalize_row(
        {
            "Point of Contact": "Somashekar J N (https://www.linkedin.com/in/somashekar-j-n-9b50582a2/)",
            "Email ID": "somashekareng@gmail.com",
            "Company Name": "Acko",
            "Designation": "Assistant Manager - IT",
        },
        source="csv",
    )
    assert "(" not in lead.full_name
    assert lead.full_name.startswith("Somashekar")
    assert lead.linkedin_url.endswith("somashekar-j-n-9b50582a2")
    assert ")" not in lead.linkedin_url


def test_linkedin_stripped_out_of_name():
    lead = normalize_row(
        {
            "Point of Contact": "Vivek Selvaraj (https://www.linkedin.com/in/vivekselvaraj01/)",
            "Email ID": "vivek.s@acko.com",
            "Company Name": "Acko",
        },
        source="csv",
    )
    assert "linkedin" not in lead.first_name.lower()
    assert lead.first_name == "Vivek"
    assert "vivekselvaraj01" in lead.linkedin_url
    assert lead.email == "vivek.s@acko.com"


def test_work_and_personal_emails_are_both_kept():
    lead = normalize_row(
        {
            "Name": "Keshavan B",
            "Company": "4C Pharma Solutions",
            "Work Email": "keshavan@4cpharma.com",
            "Personal Email": "kesmgr@yahoo.com",
        },
        source="csv",
    )
    assert lead.email == "keshavan@4cpharma.com"
    assert "kesmgr@yahoo.com" in lead.emails


def test_in_linkedin_canonicalizes_to_www():
    from asda.ingestion.cleanup import extract_linkedin

    assert extract_linkedin("https://in.linkedin.com/in/rajivkelkar") == "https://www.linkedin.com/in/rajivkelkar"


def test_xlsx_workbook_forward_fills_company(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "accounts.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "List"
    ws.append(["Sl No", "Company Name", "Point of Contact", "Designation", "Email ID", "Remarks"])
    ws.append([1, "Razorpay", "Prathamesh Joshi", "Engineering Manager, Security", "prathamesh.joshi@razorpay.com", ""])
    ws.append(["", "Talk about SOC", "Anand Choksi", "Head - Information Security", "anand.choksi@razorpay.com", ""])
    wb.save(path)
    tables = tables_from(path)
    assert tables
    rows = tables[0][1]
    companies = [r["Company Name"] for r in rows]
    assert companies[0] == "Razorpay"
    assert companies[1] == "Razorpay"
    from asda.ingestion.csv_source import CSVSource
    from asda.models.lead import LeadQuery

    leads = CSVSource().fetch(LeadQuery(limit=10, extra={"path": str(path)}))
    assert len(leads) == 2
    assert all(l.company.name == "Razorpay" for l in leads)
    assert {l.email for l in leads} == {
        "prathamesh.joshi@razorpay.com",
        "anand.choksi@razorpay.com",
    }
