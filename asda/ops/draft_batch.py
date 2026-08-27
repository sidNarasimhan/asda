"""Research and draft multi-channel campaigns without sending any outreach."""

from __future__ import annotations

import argparse

from asda.agents.content import ContentAgent
from asda.agents.research import ResearchAgent
from asda.db.repository import Repository
from asda.db.session import get_session


def eligible(lead) -> bool:
    return bool((lead.email or lead.phone) and "dnr" not in lead.tags and lead.status.value not in {"suppressed", "replied", "meeting_booked", "closed"})


def run(limit: int = 0) -> dict[str, int]:
    session = get_session()
    repo = Repository(session)
    research_agent, content_agent = ResearchAgent(), ContentAgent()
    leads = [lead for lead in repo.list_leads(limit=20_000) if eligible(lead)]
    leads.sort(key=lambda lead: (-lead.score, lead.full_name.lower()))
    if limit:
        leads = leads[:limit]
    complete = failed = 0
    for index, lead in enumerate(leads, 1):
        try:
            lead, _ = research_agent.run(lead)
            lead, content, _ = content_agent.run(lead)
            repo.save_lead(lead)
            repo.save_content(lead.id, content)
            session.commit()
            complete += 1
            print(f"{index}/{len(leads)} drafted {lead.full_name}", flush=True)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            failed += 1
            print(f"{index}/{len(leads)} failed {lead.full_name}: {exc}", flush=True)
    session.close()
    return {"selected": len(leads), "drafted": complete, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    print(run(parser.parse_args().limit))
