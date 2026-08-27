"""Weekly brief for the CBO — what the employee did."""

from __future__ import annotations

from datetime import datetime, timezone

from asda.config import get_settings
from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.lead import LeadStatus
from asda.runtime import effective


def build_brief() -> str:
    session = get_session()
    try:
        repo = Repository(session)
        metrics = repo.metrics()
        leads = repo.list_leads(limit=400)
        stored = repo.latest_insight()
        patterns = repo.winning_patterns(5)
    finally:
        session.close()
    week = datetime.now(timezone.utc).strftime("%Y-W%W")
    meetings = [l for l in leads if l.status is LeadStatus.MEETING_BOOKED]
    replies = [l for l in leads if l.status is LeadStatus.REPLIED]
    sequenced = [l for l in leads if l.status is LeadStatus.SEQUENCED]
    from asda.ops.analytics import scoreboard as month_board

    board = month_board()
    lines = [
        f"ASDA weekly brief — {week}",
        "",
        f"Leads in book: {metrics['total_leads']}",
        f"In sequence: {len(sequenced)}",
        f"Replies: {metrics['replies']}",
        f"Meetings: {metrics['meetings']}",
        f"Meetings / 100 leads: {metrics['meetings_per_100']}",
        "",
        f"This month vs target ({board['period']}):",
    ]
    for row in board["rows"]:
        lines.append(f"- {row['label']}: {row['actual']} / {row['target']} ({row['pace_label']})")
    lines += [
        "",
        "Meetings this book:",
    ]
    if meetings:
        for l in meetings[:15]:
            lines.append(f"- {l.full_name} · {l.company.name} · {l.email}")
    else:
        lines.append("- none yet")
    lines.append("")
    lines.append("Recent replies:")
    if replies:
        for l in replies[:10]:
            lines.append(f"- {l.full_name} · {l.company.name}")
    else:
        lines.append("- none yet")
    if stored or patterns:
        lines += ["", "What I'm learning:"]
        if stored and stored.get("summary"):
            lines.append(str(stored["summary"]))
        for p in patterns[:5]:
            lines.append(f"- {p.kind}: {p.text} (lift {p.lift:.2f})")
    offer = get_settings().offer
    lines += ["", f"— {offer.get('product_name') or 'ASDA'} for {offer.get('cbo_name') or 'you'}"]
    return "\n".join(lines)


def email_brief_to_cbo() -> dict:
    cfg = effective()
    body = build_brief()
    dest = cfg.cbo_email or cfg.smtp_user
    if not dest:
        return {"status": "skipped", "reason": "no inbox to send the brief to", "brief": body}
    from asda.models.content import SequenceEmail
    from asda.models.lead import Company, Lead
    from asda.modules.esp import get_esp

    fake = Lead(
        first_name=dest.split("@")[0],
        email=dest,
        company=Company(name="internal"),
    )
    result = get_esp().send(fake, SequenceEmail(step=0, subject="ASDA weekly brief", body=body))
    return {"status": "sent", "via": result, "brief": body}
