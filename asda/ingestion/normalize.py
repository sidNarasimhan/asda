"""Normalize messy source rows into the unified Lead schema + fingerprint.

Accepts Apollo, HubSpot, Salesforce, Clay, ZoomInfo, LinkedIn Sales Nav, and
one-off CSVs. Extra columns stay on raw_data. Identity can be email, LinkedIn,
phone, or a name.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from asda.ingestion.cleanup import (
    PERSONAL_DOMAINS,
    clean_company,
    email_domain,
    extract_emails,
    extract_linkedin,
    extract_phones,
    is_dnr,
    parse_name_title,
    pick_email,
    remarks_of,
    split_person_name,
    text as cell_text,
    tidy_person_name,
)
from asda.models.lead import Company, Lead, LeadStatus

_ALIASES: dict[str, tuple[str, ...]] = {
    "first_name": (
        "first_name",
        "firstname",
        "first",
        "given_name",
        "givenname",
        "fname",
        "contact_first_name",
        "prenom",
        "prename",
    ),
    "last_name": (
        "last_name",
        "lastname",
        "last",
        "surname",
        "family_name",
        "lname",
        "contact_last_name",
        "nom",
    ),
    "full_name": (
        "name",
        "full_name",
        "fullname",
        "contact_name",
        "person_name",
        "lead_name",
        "contact",
        "display_name",
        "point_of_contact",
        "contact_person",
        "poc",
        "p_o_c",
        "client_name",
    ),
    "email": (
        "email",
        "work_email",
        "email_address",
        "workemail",
        "e_mail",
        "primary_email",
        "business_email",
        "person_email",
        "contact_email",
        "emails",
        "email_1",
        "email_id",
        "emailid",
        "work_email_address",
        "courriel",
        "mail",
    ),
    "phone": (
        "phone",
        "phone_number",
        "mobile",
        "direct_phone",
        "work_phone",
        "corporate_phone",
        "mobile_phone",
        "mobile_number",
        "direct_dial",
        "direct_phone_number",
        "cellphone",
        "cell",
        "whatsapp",
        "telephone",
        "phone_numbers",
        "mobile_phone_number",
        "primary_phone",
        "number",
        "contact_no",
        "contact_details",
    ),
    "linkedin_url": (
        "linkedin_url",
        "linkedin",
        "linkedin_profile",
        "person_linkedin_url",
        "li_url",
        "linkedin_profile_url",
        "linkedin_contact_profile_url",
        "profile_url",
        "person_linkedin",
        "linkedin_profile_link",
        "linkedinurl",
        "li_profile",
        "contact_linkedin",
    ),
    "title": (
        "title",
        "job_title",
        "jobtitle",
        "position",
        "role",
        "designation",
        "job_position",
        "person_title",
        "headline",
    ),
    "company_name": (
        "company",
        "company_name",
        "organization",
        "organization_name",
        "account",
        "org",
        "account_name",
        "employer",
        "companyname",
        "associated_company",
        "account_name_name",
        "societe",
        "société",
    ),
    "company_domain": (
        "domain",
        "company_domain",
        "website",
        "company_website",
        "organization_website",
        "website_url",
        "company_url",
        "org_domain",
        "primary_domain",
        "web",
    ),
    "industry": ("industry", "company_industry", "organization_industry", "sector", "vertical"),
    "size": (
        "size",
        "company_size",
        "employees",
        "employee_count",
        "organization_num_employees",
        "num_employees",
        "number_of_employees",
        "headcount",
        "employees_count",
    ),
    "city": ("city", "person_city", "company_city", "hq_city"),
    "state": ("state", "region", "province", "person_state", "company_state"),
    "country": ("country", "person_country", "company_country", "nation"),
    "location": ("location", "company_location", "geo", "hq_location", "address"),
}

_EMAIL_SKIP = ("status", "bounce", "confidence", "grade", "valid", "catch", "quality", "reveal")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_LI_RE = re.compile(r"(https?://)?([a-z]+\.)?linkedin\.com/(in|pub)/[^\s,;]+", re.I)
_PHONE_RE = re.compile(r"(\+?\d[\d\-\s().]{7,}\d)")


def _flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key is None:
            continue
        path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
            out[str(key)] = value
    return out


def _clean_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "n/a", "na", "-", "--"}:
        return ""
    return text


def _skip_email_header(key: str) -> bool:
    return any(tok in key for tok in _EMAIL_SKIP)


_WEAK_TOKENS = {"name", "contact", "number", "mail", "org", "account", "title"}


def _pick(flat: dict[str, Any], *names: str, skip: tuple[str, ...] = ()) -> str:
    cleaned: dict[str, Any] = {}
    for k, v in flat.items():
        cleaned[_clean_key(k)] = v
    ranked: list[tuple[int, str]] = []
    for raw_key, value in cleaned.items():
        if skip and any(tok in raw_key for tok in skip):
            continue
        text = _text(value)
        if not text:
            continue
        score = 0
        for name in names:
            n = _clean_key(name)
            if raw_key == n:
                score = max(score, 100)
            elif raw_key.endswith("_" + n) or raw_key.startswith(n + "_"):
                score = max(score, 80)
            elif n in raw_key.split("_") and n not in _WEAK_TOKENS:
                score = max(score, 50)
        if score:
            ranked.append((score, text))
    if not ranked:
        return ""
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]


def extract_domain(value: str) -> str:
    if not value:
        return ""
    value = value.strip()
    if "@" in value and "://" not in value and " " not in value:
        return value.split("@", 1)[1].lower()
    if "://" not in value:
        value = "https://" + value
    host = urlparse(value).netloc.lower()
    return host.removeprefix("www.")


def fingerprint_for(lead: Lead) -> str:
    """Stable person key. Email, LinkedIn, and phone are the same human."""
    if lead.email:
        key = f"email:{lead.email.lower().strip()}"
    elif lead.linkedin_url:
        key = f"li:{lead.linkedin_url.lower().rstrip('/')}"
    elif lead.phone:
        key = f"phone:{''.join(ch for ch in lead.phone if ch.isdigit())[-10:]}"
    else:
        key = "name:" + "|".join(
            [
                lead.first_name.lower(),
                lead.last_name.lower(),
                (lead.company.domain or lead.company.name).lower(),
            ]
        )
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _infer_email(flat: dict[str, Any]) -> str:
    for value in flat.values():
        text = _text(value)
        m = _EMAIL_RE.search(text)
        if m:
            return m.group(0).lower()
    return ""


def _infer_linkedin(flat: dict[str, Any]) -> str:
    for key, value in flat.items():
        text = _text(value)
        if not text:
            continue
        m = _LI_RE.search(text)
        if m:
            url = m.group(0)
            if not url.lower().startswith("http"):
                url = "https://" + url
            return url.split("?")[0].rstrip("/")
        ck = _clean_key(key)
        if ck in {"url", "profile", "profile_link"} and "linkedin.com" in text.lower():
            return text.split("?")[0]
    return ""


def _infer_phone(flat: dict[str, Any]) -> str:
    for value in flat.values():
        text = _text(value)
        if _EMAIL_RE.search(text) or "linkedin.com" in text.lower():
            continue
        m = _PHONE_RE.search(text)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if 8 <= len(digits) <= 15:
                return m.group(1).strip()
    return ""


def normalize_row(row: dict[str, Any], source: str) -> Lead:
    flat = _flatten(row)
    company_name = clean_company(_pick(flat, *_ALIASES["company_name"]))
    domain = extract_domain(_pick(flat, *_ALIASES["company_domain"]))

    first = cell_text(_pick(flat, *_ALIASES["first_name"], skip=("company", "organization", "account", "org")))
    last = cell_text(_pick(flat, *_ALIASES["last_name"], skip=("company", "organization", "account", "org")))
    full, title_from_name = parse_name_title(
        _pick(flat, *_ALIASES["full_name"], skip=("company", "organization", "account", "org", "employer"))
    )
    if not first and full:
        first, last = split_person_name(full)
    else:
        first, extra_title = parse_name_title(first)
        title_from_name = title_from_name or extra_title
        if first and " " in first and not last:
            first, last = split_person_name(first)
    first, last = tidy_person_name(first, last)

    email_blob = _pick(flat, *_ALIASES["email"], skip=_EMAIL_SKIP)
    emails = extract_emails(email_blob)
    extras = extract_emails(" ".join(cell_text(v) for v in flat.values()))
    for addr in extras:
        if addr not in emails:
            emails.append(addr)
    email = pick_email(emails, company_name=company_name, company_domain=domain)
    if email and email_domain(email) not in PERSONAL_DOMAINS:
        domain = domain or email_domain(email)

    linkedin = extract_linkedin(
        _pick(
            flat,
            *_ALIASES["linkedin_url"],
            skip=("company", "organization", "account", "org", "employer"),
        )
    ) or extract_linkedin(" ".join(cell_text(v) for v in flat.values()))

    phone = extract_phones(_pick(flat, *_ALIASES["phone"]))
    if not phone:
        for key, value in flat.items():
            ck = _clean_key(str(key))
            if any(tok in ck.split("_") for tok in ("phone", "mobile", "whatsapp", "cell", "tel", "dial")):
                phone = extract_phones(value)
                if phone:
                    break

    city = _pick(flat, *_ALIASES["city"])
    state = _pick(flat, *_ALIASES["state"])
    country = _pick(flat, *_ALIASES["country"])
    location = _pick(flat, *_ALIASES["location"]) or ", ".join(p for p in (city, state, country) if p)
    title = cell_text(_pick(flat, *_ALIASES["title"])) or title_from_name

    remarks = remarks_of({str(k): v for k, v in row.items() if k is not None})
    lead = Lead(
        source=source,
        raw_data={str(k): v for k, v in row.items() if k is not None},
        first_name=first,
        last_name=last,
        email=email,
        emails=list(emails),
        phone=phone,
        linkedin_url=linkedin,
        title=title,
        company=Company(
            name=company_name,
            domain=domain,
            industry=_pick(flat, *_ALIASES["industry"]),
            size=_pick(flat, *_ALIASES["size"]),
            location=location,
        ),
        notes=[remarks] if remarks else [],
    )
    if is_dnr(remarks, full, first):
        lead.tags.append("dnr")
        lead.status = LeadStatus.SUPPRESSED
        lead.add_outcome("do_not_contact", "Marked DNR on the source sheet")
    lead.fingerprint = fingerprint_for(lead)
    return lead


_PLACEHOLDER = re.compile(r"^\[.+\]$")
_HEADER_NAMES = {
    "client name",
    "organization",
    "designation",
    "company name",
    "company",
    "contact name",
    "p.o.c",
}
_NO_CONTACT = re.compile(r"poc needs research|to be (added|updated)|no poc|tbd", re.I)


def is_valid_lead(lead: Lead) -> tuple[bool, str]:
    if lead.email and "@" not in lead.email:
        return False, "invalid email"
    person = (lead.full_name or "").strip()
    first = (lead.first_name or "").strip()
    if _PLACEHOLDER.match(person) or _PLACEHOLDER.match(first):
        return False, "placeholder"
    if _NO_CONTACT.search(person) or _NO_CONTACT.search(first):
        return False, "no contact yet"
    if person.lower() in _HEADER_NAMES or first.lower() in _HEADER_NAMES:
        return False, "header row"
    letters = sum(ch.isalpha() for ch in person)
    digits = sum(ch.isdigit() for ch in person)
    if person and digits >= 6 and digits >= letters:
        return False, "phone-as-name"
    if (lead.title or "").strip() in {"[Title]", "[title]"} and not (lead.email or lead.linkedin_url or lead.phone):
        return False, "placeholder"
    if lead.email or lead.emails or lead.linkedin_url or lead.phone:
        return True, ""
    if not first:
        return False, "missing identity (name, email, linkedin, or phone)"
    company = (lead.company.name or "").strip().lower()
    if company and person.lower() and (person.lower() == company or person.lower() in company or company in person.lower()):
        return False, "company-only row"
    return True, ""
