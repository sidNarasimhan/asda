"""Fast, review-only Altisec campaign drafts for every contactable non-DNR lead."""

from __future__ import annotations

from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.content import GeneratedContent, LinkedInMessage, LinkedInSequence, SequenceEmail, WhatsAppMessage, WhatsAppSequence
from asda.models.lead import LeadStatus


def _theme(lead) -> str:
    text = f"{lead.title} {lead.company.industry}".lower()
    if any(word in text for word in ("ciso", "security", "cyber", "risk", "grc")):
        return "security operations, detection, and response"
    if any(word in text for word in ("cloud", "infrastructure", "technology", "cto", "it")):
        return "cloud, identity, and operational resilience"
    return "cybersecurity resilience and risk reduction"


def draft_for(lead) -> GeneratedContent:
    name_parts = lead.full_name.split()
    first = lead.first_name or (name_parts[0] if name_parts else "there")
    company = lead.company.name or "your team"
    role = lead.title or "technology leadership"
    theme = _theme(lead)
    emails = [
        SequenceEmail(step=1, delay_days=0, subject=f"{company}: {theme}", body=f"Hi {first},\n\nI’m Karthik from Altisec. We help Indian and EMEA organisations assess, build, and run cybersecurity programs—across SOC/SOAR, cloud security, IAM, vulnerability management, red teaming, and incident response.\n\nI’m reaching out because, as {role} at {company}, {theme} is likely to sit with your team. Altisec is useful when leaders need an independent view of the gaps that matter most, plus a practical remediation plan rather than another tool.\n\nWould a 20-minute security-priorities conversation be useful?\n\nKarthik", angle="who we are, why Altisec, role relevance"),
        SequenceEmail(step=2, delay_days=3, subject=f"Re: {company} security priorities", body=f"Hi {first},\n\nAltisec is a cybersecurity services team, not a point-product vendor. We support security leaders with SOC optimisation, cloud and identity security, offensive testing, and incident readiness.\n\nFor a {role}, the value is a focused assessment that identifies the priority control gaps and a path to close them without adding unnecessary operational overhead.\n\nIs that a conversation worth having for {company}?\n\nKarthik", angle="service clarity and business outcome"),
        SequenceEmail(step=3, delay_days=7, subject=f"Independent validation for {company}", body=f"Hi {first},\n\nOne reason teams bring in Altisec is to independently validate whether their detection, identity, cloud, and response controls reflect the risks they carry today.\n\nGiven your responsibility for {role} at {company}, I thought that might be timely. We can start with the area that is most relevant—SOC, cloud, IAM, application security, or incident readiness.\n\nOpen to a brief conversation?\n\nKarthik", angle="independent validation"),
        SequenceEmail(step=4, delay_days=12, subject=f"Should I close this out, {first}?", body=f"Hi {first},\n\nI’ll close the loop after this. Altisec helps organisations assess and strengthen SOC, cloud, identity, offensive-security, and incident-response capability.\n\nIf an independent security assessment is not relevant for {company} right now, I will not follow up further. If it is, I’m happy to arrange a 20-minute discussion around your current priority.\n\nKarthik", angle="respectful close"),
    ]
    linkedin = LinkedInSequence(
        connection_note=f"Hi {first}—I work with Altisec on practical cyber resilience across SOC, cloud, IAM, and offensive security. Thought it would be good to connect.",
        messages=[
            LinkedInMessage(step=1, delay_days=2, kind="follow_up", body=f"Thanks for connecting, {first}. Given your work in {role}, is {theme} on the agenda at {company}?"),
            LinkedInMessage(step=2, delay_days=5, kind="follow_up", body="Altisec helps teams turn security priorities into a practical assessment and remediation plan across SOC, cloud, IAM, and app security. Happy to share a relevant example if useful."),
            LinkedInMessage(step=3, delay_days=9, kind="follow_up", body=f"I’ll leave it there. If a short conversation on {theme} becomes useful at {company}, I’m easy to find."),
        ],
    )
    whatsapp = WhatsAppSequence(messages=[
        WhatsAppMessage(step=1, delay_days=0, body=f"Hi {first}, Karthik here from Altisec. We help security and IT leaders with {theme}, including SOC, cloud, IAM, and incident readiness. Is this relevant at {company}? Reply STOP to opt out."),
        WhatsAppMessage(step=2, delay_days=6, body=f"Hi {first}, following up once on {theme}. If it is not relevant for {company}, I will close this out. If it is, I can share a concise Altisec assessment approach."),
    ])
    return GeneratedContent(emails=emails, linkedin=linkedin, whatsapp=whatsapp, style_notes="Draft-only baseline tailored to current title/company. Enrich with lead-specific web research before send approval.")


def run() -> dict[str, int]:
    session = get_session(); repo = Repository(session)
    leads = [lead for lead in repo.list_leads(limit=20_000) if (lead.email or lead.phone) and "dnr" not in lead.tags and lead.status.value not in {"suppressed", "replied", "meeting_booked", "closed"}]
    for lead in leads:
        lead.status = LeadStatus.NEW
        lead.sequence_state.paused = False
        lead.sequence_state.reason = "draft-only; no outreach approved"
        repo.save_lead(lead)
        repo.save_content(lead.id, draft_for(lead))
    session.commit(); session.close()
    return {"drafted": len(leads)}


if __name__ == "__main__":
    print(run())
