"""Export ASDA's saved Batch 1 drafts as a single review CSV."""

from __future__ import annotations

import csv
from pathlib import Path

from asda.db.repository import Repository
from asda.db.session import get_session


OUT = Path("outputs/altisec_batch_1_drafts.csv")


def value(items, index: int, field: str) -> str:
    return str(getattr(items[index], field, "")) if len(items) > index else ""


def main() -> None:
    session = get_session()
    repo = Repository(session)
    leads = [lead for lead in repo.list_leads(limit=5000) if "altisec_batch_1_draft" in lead.tags]
    leads.sort(key=lambda lead: (-lead.score, lead.full_name.lower()))
    columns = [
        "lead_id", "full_name", "title", "company", "email", "phone", "linkedin_url", "score", "review_status",
        "email_1_subject", "email_1_body", "email_2_subject", "email_2_body", "email_3_subject", "email_3_body", "email_4_subject", "email_4_body",
        "linkedin_connection_note", "linkedin_message_1", "linkedin_message_2", "linkedin_message_3",
        "whatsapp_template_name", "whatsapp_message_1", "whatsapp_message_2",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for lead in leads:
            content = repo.get_content(lead.id)
            if not content:
                continue
            writer.writerow({
                "lead_id": lead.id, "full_name": lead.full_name, "title": lead.title,
                "company": lead.company.name, "email": lead.email, "phone": lead.phone,
                "linkedin_url": lead.linkedin_url, "score": lead.score,
                "review_status": "draft-only; no outreach approved",
                "email_1_subject": value(content.emails, 0, "subject"), "email_1_body": value(content.emails, 0, "body"),
                "email_2_subject": value(content.emails, 1, "subject"), "email_2_body": value(content.emails, 1, "body"),
                "email_3_subject": value(content.emails, 2, "subject"), "email_3_body": value(content.emails, 2, "body"),
                "email_4_subject": value(content.emails, 3, "subject"), "email_4_body": value(content.emails, 3, "body"),
                "linkedin_connection_note": content.linkedin.connection_note,
                "linkedin_message_1": value(content.linkedin.messages, 0, "body"),
                "linkedin_message_2": value(content.linkedin.messages, 1, "body"),
                "linkedin_message_3": value(content.linkedin.messages, 2, "body"),
                "whatsapp_template_name": content.whatsapp.template_name,
                "whatsapp_message_1": value(content.whatsapp.messages, 0, "body"),
                "whatsapp_message_2": value(content.whatsapp.messages, 1, "body"),
            })
    session.close()
    print(f"wrote {len(leads)} rows to {OUT}")


if __name__ == "__main__":
    main()
