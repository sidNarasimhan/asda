"""Ingest a file (CSV or Excel), clean it, persist canonical leads."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from asda.ingestion.cleanup import (
    canonical_company_map,
    clean_company,
    company_core,
    extract_emails,
    extract_linkedin,
    extract_phones,
    infer_company_from_email,
    looks_like_address,
    pick_email,
    tidy_person_name,
)
from asda.ingestion.csv_source import CSVSource
from asda.ingestion.normalize import fingerprint_for, is_valid_lead, normalize_row
from asda.models.lead import Lead, LeadQuery, LeadStatus


def parse_file(path: str | Path, *, source: str = "csv", limit: int = 20_000) -> dict[str, Any]:
    extra: dict[str, Any] = {"path": str(path)}
    leads = CSVSource().fetch(LeadQuery(limit=limit, extra=extra))
    from asda.ingestion.workbook import inspect as inspect_workbook

    extra.setdefault("sheets", inspect_workbook(path).get("sheets") or [])
    dnr = sum(1 for l in leads if "dnr" in l.tags)
    with_email = sum(1 for l in leads if l.email)
    with_li = sum(1 for l in leads if l.linkedin_url)
    with_phone = sum(1 for l in leads if l.phone)
    return {
        "path": str(path),
        "leads": leads,
        "parsed": len(leads),
        "skipped": extra.get("skipped") or 0,
        "skip_reasons": extra.get("skip_reasons") or {},
        "sheets": extra.get("sheets") or [],
        "dnr": dnr,
        "with_email": with_email,
        "with_linkedin": with_li,
        "with_phone": with_phone,
        "source": source,
    }


def persist_leads(leads: list[Lead], *, emit_each: bool = False) -> dict[str, Any]:
    from asda.bus.events import get_bus
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from asda.models.events import EventType
    from sqlalchemy import text

    session = get_session()
    try:
        session.execute(text("PRAGMA busy_timeout=60000"))
    except Exception:
        pass
    created = merged = 0
    ids: list[str] = []
    try:
        repo = Repository(session)
        bus = get_bus()
        for lead in leads:
            saved, is_new = repo.upsert_lead(lead)
            ids.append(saved.id)
            if is_new:
                created += 1
                if emit_each:
                    bus.emit_type(EventType.LEAD_INGESTED, saved.id, source=saved.source)
            else:
                merged += 1
                if emit_each:
                    bus.emit_type(EventType.LEAD_DEDUPED, saved.id, source=saved.source)
        session.commit()
        if not emit_each and leads:
            bus.emit_type(
                EventType.LEAD_INGESTED,
                summary=f"Ingested {created} new, merged {merged}",
                created=created,
                merged=merged,
            )
        return {"ingested": created, "deduped": merged, "ids": ids, "total": created + merged}
    finally:
        session.close()


def ingest_path(path: str | Path, *, source: str = "csv") -> dict[str, Any]:
    parsed = parse_file(path, source=source)
    result = persist_leads(parsed["leads"])
    parsed.pop("leads")
    parsed.update(result)
    try:
        from asda.ingestion.census import census_files, load_report

        existing = load_report().get("files") or []
        files = []
        for item in existing + [str(path)]:
            if item not in files:
                files.append(item)
        parsed["census"] = census_files(files)
    except Exception:
        pass
    return parsed


_STATUS_RANK = {
    "meeting_booked": 0,
    "replied": 1,
    "connected": 2,
    "sequenced": 3,
    "awaiting_approval": 4,
    "researched": 5,
    "researching": 6,
    "failed": 7,
    "new": 8,
    "suppressed": 9,
    "closed": 10,
}


def _emails_of(lead: Lead) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for addr in [lead.email, *(lead.emails or [])]:
        a = (addr or "").lower().strip()
        if a and "@" in a and a not in seen:
            seen.add(a)
            found.append(a)
    for value in (lead.raw_data or {}).values():
        for addr in extract_emails(value):
            if addr not in seen:
                seen.add(addr)
                found.append(addr)
    return found


def _li_of(lead: Lead) -> str:
    return extract_linkedin(lead.linkedin_url) or extract_linkedin(
        " ".join(str(v) for v in (lead.raw_data or {}).values() if v)
    )


def _phone10(lead: Lead) -> str:
    digits = "".join(ch for ch in (lead.phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def _name_key(lead: Lead) -> str:
    name = re.sub(r"\s+", " ", (lead.full_name or "").strip().lower())
    co = company_core(lead.company.name or "") or (lead.company.name or "").strip().lower()
    if not name or not co or name in {"unknown"}:
        return ""
    return name + "|" + co


def _name_tokens(lead: Lead) -> set[str]:
    return set(re.findall(r"[a-z]{2,}", (lead.full_name or "").lower()))


def _fold_person(keep: Lead, extra: Lead) -> None:
    keep.raw_data = {**(keep.raw_data or {}), **(extra.raw_data or {})}
    emails = _emails_of(keep) + _emails_of(extra)
    keep.emails = list(dict.fromkeys(emails))
    keep.email = pick_email(keep.emails, company_name=keep.company.name or extra.company.name) or keep.email or extra.email
    if extra.linkedin_url and not keep.linkedin_url:
        keep.linkedin_url = extra.linkedin_url
    keep.linkedin_url = _li_of(keep) or keep.linkedin_url
    if extra.phone and not keep.phone:
        keep.phone = extra.phone
    if extra.title and not keep.title:
        keep.title = extra.title
    if extra.first_name and not keep.first_name:
        keep.first_name = extra.first_name
    if extra.last_name and (not keep.last_name or len(extra.last_name) > len(keep.last_name)):
        keep.last_name = extra.last_name
    incoming_co = clean_company(extra.company.name)
    have_co = (keep.company.name or "").strip()
    if incoming_co and (not have_co or looks_like_address(have_co) or len(incoming_co) > len(have_co)):
        keep.company.name = incoming_co
    if extra.company.domain and not keep.company.domain:
        keep.company.domain = extra.company.domain
    for note in extra.notes or []:
        if note and note not in keep.notes:
            keep.notes.append(note)
    if "dnr" in (extra.tags or []) and "dnr" not in keep.tags:
        keep.tags.append("dnr")
        keep.status = LeadStatus.SUPPRESSED


def merge_duplicate_leads(limit: int = 20_000) -> int:
    """Same human: email, LinkedIn handle, or name+company. Shared switchboards do not merge."""
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from sqlalchemy import text

    session = get_session()
    try:
        session.execute(text("PRAGMA busy_timeout=60000"))
    except Exception:
        pass
    merged = 0
    try:
        repo = Repository(session)
        leads = repo.list_leads(limit=limit)
        n = len(leads)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        email_at: dict[str, int] = {}
        li_at: dict[str, int] = {}
        name_groups: dict[str, list[int]] = {}
        phone_groups: dict[str, list[int]] = {}
        for i, lead in enumerate(leads):
            for addr in _emails_of(lead):
                prev = email_at.get(addr)
                if prev is not None:
                    union(i, prev)
                email_at[addr] = i
            handle = _li_of(lead)
            if handle:
                prev = li_at.get(handle)
                if prev is not None:
                    union(i, prev)
                li_at[handle] = i
            nk = _name_key(lead)
            if nk:
                name_groups.setdefault(nk, []).append(i)
            ph = _phone10(lead)
            if ph:
                phone_groups.setdefault(ph, []).append(i)

        for idxs in name_groups.values():
            if len(idxs) < 2:
                continue
            for a in idxs[1:]:
                ea = set(_emails_of(leads[idxs[0]]))
                eb = set(_emails_of(leads[a]))
                pa, pb = _phone10(leads[idxs[0]]), _phone10(leads[a])
                if ea and eb and not (ea & eb) and pa and pb and pa != pb:
                    continue
                union(idxs[0], a)
            # also pairwise for remaining after first-as-hub misses
            for i, a in enumerate(idxs):
                for b in idxs[i + 1 :]:
                    if find(a) == find(b):
                        continue
                    ea, eb = set(_emails_of(leads[a])), set(_emails_of(leads[b]))
                    pa, pb = _phone10(leads[a]), _phone10(leads[b])
                    if ea and eb and not (ea & eb) and pa and pb and pa != pb:
                        continue
                    union(a, b)

        slug_groups: dict[str, list[int]] = {}
        for i, lead in enumerate(leads):
            name = re.sub(r"\s+", " ", (lead.full_name or "").strip().lower())
            slug = re.sub(r"[^a-z0-9]", "", company_core(lead.company.name or "") or (lead.company.name or "").lower())
            if name and len(slug) >= 8:
                slug_groups.setdefault(name, []).append((i, slug))
        for pairs in slug_groups.values():
            if len(pairs) < 2:
                continue
            for a, sa in pairs:
                for b, sb in pairs:
                    if a >= b:
                        continue
                    if sa == sb or sa.startswith(sb) or sb.startswith(sa):
                        union(a, b)

        for idxs in phone_groups.values():
            if len(idxs) < 2:
                continue
            for i, a in enumerate(idxs):
                ta = _name_tokens(leads[a])
                for b in idxs[i + 1 :]:
                    if ta & _name_tokens(leads[b]):
                        union(a, b)

        groups: dict[int, list[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)

        for idxs in groups.values():
            if len(idxs) < 2:
                continue
            people = [leads[i] for i in idxs]
            people.sort(
                key=lambda l: (
                    _STATUS_RANK.get(getattr(l.status, "value", str(l.status)), 9),
                    0 if l.email else 1,
                    0 if l.linkedin_url else 1,
                    -len(l.full_name or ""),
                )
            )
            keep = people[0]
            for extra in people[1:]:
                _fold_person(keep, extra)
                repo.delete_lead(extra.id)
                merged += 1
            keep.linkedin_url = _li_of(keep) or keep.linkedin_url
            if keep.emails:
                keep.email = pick_email(keep.emails, company_name=keep.company.name) or keep.email
            keep.fingerprint = fingerprint_for(keep)
            repo.save_lead(keep)
        session.commit()
        return merged
    finally:
        session.close()


def reclean_lead(lead: Lead) -> Lead:
    """Re-run cleanup on a stored lead using the original row."""
    row = dict(lead.raw_data or {})
    if lead.company.name and not any(
        str(k).lower().strip() in {"company name", "company", "account name"} for k in row
    ):
        row["Company Name"] = lead.company.name
    fresh = normalize_row(row, source=lead.source or "csv")
    emails = list(dict.fromkeys(_emails_of(lead) + _emails_of(fresh) + (fresh.emails or [])))
    if emails:
        lead.emails = emails
        lead.email = pick_email(emails, company_name=fresh.company.name or lead.company.name) or lead.email or fresh.email
    elif fresh.email:
        lead.email = fresh.email
    if fresh.linkedin_url:
        lead.linkedin_url = fresh.linkedin_url
    lead.linkedin_url = extract_linkedin(lead.linkedin_url) or lead.linkedin_url
    cleaned_phone = extract_phones(fresh.phone or lead.phone)
    if cleaned_phone:
        lead.phone = cleaned_phone
    if fresh.first_name:
        lead.first_name = fresh.first_name
    if fresh.last_name:
        lead.last_name = fresh.last_name
    lead.first_name, lead.last_name = tidy_person_name(lead.first_name, lead.last_name)
    if fresh.title:
        lead.title = fresh.title
    company = clean_company(fresh.company.name) or clean_company(lead.company.name)
    if company:
        lead.company.name = company
    elif lead.company.name and not clean_company(lead.company.name):
        lead.company.name = ""
    if fresh.company.domain:
        lead.company.domain = fresh.company.domain
    if fresh.notes and not lead.notes:
        lead.notes = fresh.notes
    if "dnr" in fresh.tags and "dnr" not in lead.tags:
        lead.tags.append("dnr")
        if lead.status == LeadStatus.NEW:
            lead.status = LeadStatus.SUPPRESSED
            lead.add_outcome("do_not_contact", "Marked DNR on the source sheet")
    ok, _ = is_valid_lead(lead)
    if ok:
        lead.fingerprint = fingerprint_for(lead)
    return lead


def reset_book_fresh(limit: int = 20_000) -> dict[str, Any]:
    """Treat the current book as a new list: no sequences, no old research."""
    from asda.db.models import ContentRow
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from asda.models.lead import SequenceState
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import text

    session = get_session()
    try:
        session.execute(text("PRAGMA busy_timeout=60000"))
    except Exception:
        pass
    fresh = dnr = 0
    try:
        repo = Repository(session)
        for lead in repo.list_leads(limit=limit):
            reclean_lead(lead)
            keep_dnr = "dnr" in lead.tags
            lead.score = 0
            lead.research_card = None
            lead.sequence_state = SequenceState()
            lead.outcomes = [o for o in (lead.outcomes or []) if o.kind == "do_not_contact"]
            if keep_dnr:
                lead.status = LeadStatus.SUPPRESSED
                if not any(o.kind == "do_not_contact" for o in lead.outcomes):
                    lead.add_outcome("do_not_contact", "Marked DNR on the source sheet")
                dnr += 1
            else:
                lead.status = LeadStatus.NEW
                fresh += 1
                session.execute(sql_delete(ContentRow).where(ContentRow.lead_id == lead.id))
            repo.save_lead(lead)
        session.commit()
    finally:
        session.close()
    dupes = merge_duplicate_leads(limit=limit)
    return {"fresh": fresh, "dnr": dnr, "total": fresh + dnr, "merged": dupes}


def reclean_book(limit: int = 20_000) -> dict[str, Any]:
    from asda.db.repository import Repository
    from asda.db.session import get_session
    from sqlalchemy import text

    session = get_session()
    try:
        session.execute(text("PRAGMA busy_timeout=60000"))
    except Exception:
        pass
    touched = dnr = 0
    try:
        repo = Repository(session)
        leads = repo.list_leads(limit=limit)
        for lead in leads:
            reclean_lead(lead)
        mapping = canonical_company_map([l.company.name for l in leads])
        known = sorted(
            {mapping.get(n, n) for n in mapping}
            | {l.company.name for l in leads if clean_company(l.company.name)}
        )
        junk_raw: dict[str, str] = {}
        for lead in leads:
            raw_co = str((lead.raw_data or {}).get("Company Name") or "")
            if raw_co and not clean_company(raw_co):
                guessed = infer_company_from_email(lead.email, known)
                if guessed:
                    junk_raw[raw_co] = guessed
        for lead in leads:
            before = (lead.email, lead.company.name, lead.first_name, lead.phone, lead.status.value)
            if lead.company.name in mapping:
                lead.company.name = mapping[lead.company.name]
            if not clean_company(lead.company.name):
                inferred = infer_company_from_email(lead.email, known)
                raw_co = str((lead.raw_data or {}).get("Company Name") or "")
                inferred = inferred or junk_raw.get(raw_co) or ""
                if inferred:
                    lead.company.name = inferred
            if "linkedin.com" in (lead.phone or "").lower():
                lead.phone = ""
            after = (lead.email, lead.company.name, lead.first_name, lead.phone, lead.status.value)
            if before != after:
                repo.save_lead(lead)
                touched += 1
            else:
                repo.save_lead(lead)
            if "dnr" in lead.tags:
                dnr += 1
        session.commit()
        return {"updated": touched, "dnr": dnr, "companies": len(set(l.company.name for l in leads if l.company.name))}
    finally:
        session.close()
