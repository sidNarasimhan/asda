"""Remove sample / fake leads so the book only has real people."""

from __future__ import annotations

from typing import Any

from asda.db.repository import Repository
from asda.db.session import get_session, init_db
from asda.models.lead import Lead

KEEP_DEFAULT = ("sanath", "narasimhan", "kushal", "aralihalli", "boxup")

FAKE_HINTS = (
    "northwind",
    "adventureworks",
    "contoso",
    "fabrikam",
    "harborops",
    "example.com",
    "mailinator",
    "sample",
    "fake",
    "threadmill",
    "nisha reddy",
    "barebloom",
    "gifthearth",
    "masalabox",
    "hearthandco",
    "novawell",
    "lumenhome",
    "boxday",
    "lumenfield",
    "brightlane",
    "keelhr",
    "keel hr",
)


def _sample_emails() -> set[str]:
    from asda.config import ROOT

    emails: set[str] = set()
    folder = ROOT / "sample_data"
    if not folder.exists():
        return emails
    for path in folder.glob("*.csv"):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            for part in line.split(","):
                bit = part.strip().lower()
                if "@" in bit:
                    emails.add(bit)
    return emails


def is_keeper(lead: Lead, keep: tuple[str, ...] = KEEP_DEFAULT) -> bool:
    blob = " ".join(
        [
            lead.full_name,
            lead.email,
            lead.company.name,
            lead.linkedin_url,
            lead.source,
        ]
    ).lower()
    return any(n in blob for n in keep)


def looks_fake(lead: Lead) -> bool:
    if is_keeper(lead):
        return False
    blob = " ".join(
        [
            lead.full_name,
            lead.email,
            lead.company.name,
            lead.source,
            " ".join(lead.tags),
        ]
    ).lower()
    if any(h in blob for h in FAKE_HINTS):
        return True
    if (lead.email or "").lower() in _sample_emails():
        return True
    if lead.source in {"sample", "mock", "mock_apollo"}:
        return True
    return False


def purge_fake_leads(*, keep: tuple[str, ...] = KEEP_DEFAULT) -> dict[str, Any]:
    init_db()
    session = get_session()
    removed: list[str] = []
    kept: list[str] = []
    try:
        repo = Repository(session)
        for lead in repo.list_leads(limit=5000):
            if is_keeper(lead, keep) or not looks_fake(lead):
                kept.append(lead.full_name or lead.id)
                continue
            repo.delete_lead(lead.id)
            removed.append(lead.full_name or lead.id)
        session.commit()
    finally:
        session.close()
    from asda.models.events import EventType
    from asda.ops.activity import log

    log(EventType.CONFIG_UPDATED, summary=f"Cleared {len(removed)} sample/fake leads", removed=len(removed))
    return {"removed": removed, "kept": kept, "count": len(removed)}
