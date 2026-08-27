"""Fast deterministic ICP ranking used before expensive research/enrichment."""

from __future__ import annotations

from asda.db.repository import Repository
from asda.db.session import get_session


_EXECUTIVE = ("ciso", "chief information security", "cio", "chief information", "cto", "vp information security")
_SECURITY = ("head of information security", "head of cybersecurity", "director information security", "director cybersecurity", "head of security")
_IT = ("head of it", "it director", "head of infrastructure", "head of cloud", "head of risk", "head of grc", "data protection officer", "security architect", "security manager")
_NEGATIVE = ("student", "intern", "trainee", "recruiter", "talent acquisition", "hr manager")
_SECTORS = ("bank", "finance", "insurance", "fintech", "health", "pharma", "manufact", "saas", "software", "retail", "energy", "telecom")


def score_lead(lead) -> tuple[int, str]:
    text = " ".join((lead.title, lead.company.name, lead.company.industry, lead.company.description)).lower()
    if any(term in text for term in _NEGATIVE):
        return 0, "Excluded role"
    score = 15 if lead.company.name else 0
    if any(term in text for term in _EXECUTIVE):
        score += 60
    elif any(term in text for term in _SECURITY):
        score += 50
    elif any(term in text for term in _IT):
        score += 35
    size = (lead.company.size or "").replace(" ", "")
    if size in {"5001-10000", "10000+", "10001+"}:
        score += 25
    elif size in {"1001-5000", "501-1000"}:
        score += 20
    elif size in {"201-500"}:
        score += 15
    elif size in {"51-200"}:
        score += 8
    if any(term in text for term in _SECTORS):
        score += 12
    if lead.linkedin_url:
        score += 8
    if lead.email:
        score += 5
    if lead.phone:
        score += 3
    return min(score, 100), "Altisec cybersecurity ICP"


def rank_all() -> dict[str, int]:
    session = get_session()
    try:
        repo = Repository(session)
        leads = repo.list_leads(limit=20_000)
        for lead in leads:
            score, reason = score_lead(lead)
            lead.score = score
            lead.notes = [note for note in lead.notes if not note.startswith("icp_rank:")]
            lead.notes.append(f"icp_rank:{score} {reason}")
            repo.save_lead(lead)
        session.commit()
        return {"ranked": len(leads), "high_value": sum(lead.score >= 70 for lead in leads)}
    finally:
        session.close()
