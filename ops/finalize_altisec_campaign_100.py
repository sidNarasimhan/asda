"""Build the manually re-audited Altisec campaign-1 review set.

This script intentionally uses only current-role facts checked against each
person's LinkedIn profile.  It does not invent news, initiatives, metrics, or
technology stacks.  All outreach remains paused for human review.
"""

from __future__ import annotations

from collections import defaultdict

from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.content import (
    GeneratedContent,
    LinkedInMessage,
    LinkedInSequence,
    SequenceEmail,
    WhatsAppMessage,
    WhatsAppSequence,
)
from asda.models.lead import LeadStatus, ResearchCard


FINAL_IDS = [
    # Retained from the original batch after direct profile review.
    "93155242-26f8-48b2-9823-0d7229966d4e", "9c38c976-6f54-45f6-9a4a-315b0af42ccd",
    "56e2acf1-7d7a-4329-aaa0-aca43b6c909f", "d7dead05-d9b6-4c9b-8aa6-669a10daf751",
    "0dc38368-9b52-4bdb-b9c7-c2a312dd65ad", "35817d7e-8672-4fd7-b545-b7771d9bd0dc",
    "121102a0-421c-40f2-bdd6-819f5888e279", "4efd02d0-3857-4145-892b-021030ee4765",
    "4a0af3cd-121c-476a-aa38-0685c8a022a7", "11cbea6a-bdac-4a68-bcaf-2fc4417ec7a1",
    "ca5df370-85fd-41aa-b377-e2406c6d3c4d", "8a5af7f9-5e5f-43b8-99cf-6b0e3234d1a1",
    "a490a3fb-b6ca-4b0d-9605-73097a0dd7e5", "02d0417d-2916-4f9c-a17c-433c8cc294da",
    "106eddba-fabb-49b5-b995-e50ebf17e3cb", "876770ac-8e56-4c9d-865f-fa71d2eecb21",
    "24b77bc9-ee4b-4fa8-b209-5f08a252238c", "a2242051-8252-4f81-98b8-12719a979fbf",
    "0bf40937-3747-4388-8b29-7928d0477947", "3a730261-e45c-43c0-ab79-cd5154cb1974",
    "6ebefcbd-6f8a-4c49-a0ce-c307a1615932", "a7eb3624-a0e5-4124-b365-271346cec614",
    "3121bab5-2473-4e20-ba26-ad2402641e76", "3d6cd507-0535-4acc-a59c-8c3e0cbd1a9f",
    "e688e4e4-0842-41d7-a66b-d2bdaa232b79", "269b4826-4497-49b0-af28-e3c53b52fa11",
    "970a473b-fffc-47f2-9209-26cfe4db4b74", "fd57e4a2-4ea5-4486-9e81-ca50ba3e95b8",
    "0dbbebcc-7128-41d5-8c42-544af93bfc2d", "98f51000-1f10-4d82-afa2-c1d763b57074",
    "81fbc247-3fd0-4c63-b2ae-e6dec2bac247", "76ef7373-9741-4615-a1c0-20d7f4b59dcb",
    "4351b046-4ee6-4c04-b47e-39e3d6ed1d32", "e1df4a4a-f529-4ee5-8b17-5f9cbae8ab66",
    "1df388e3-75a0-4997-8008-4599235b83d8", "247f0d53-aa99-4cc4-86dd-47501a129323",
    "7c650b87-9bdb-4baf-a304-0fb49629ebc1", "eddca7e5-5f5a-4c15-b39f-b6f414db7aea",
    "6a4814ae-bd27-48fe-ad44-712d07d33969", "9a8c2d09-119b-48a8-876d-294644bff429",
    "02fa3602-8baa-44c2-8869-e133379809e3", "180e8318-f4e5-4e88-a46e-8399b0b2c5dd",
    "a9138550-04b9-4109-900a-479101505547", "3cef120b-5f31-4f1b-95eb-5a09852f1882",
    "152c285e-9cfc-4844-973f-6f429c6acb8f", "7d072763-7e6b-4112-a83b-5e24a666d745",
    "0896297c-7df2-42ee-b225-b3c2c71d1785", "cec9894c-67e1-4ab5-8684-6e077fb4d58b",
    "2691b81f-bc05-4e0a-b42c-864cc715858a", "acf473c2-1c4b-43c9-8cd4-c5deb8360d22",
    "f96b6d7d-0a40-4ba7-96a3-c307ebffaedd", "6ee32029-b346-4a3b-afa7-eea2439c3edd",
    "67c8e026-410d-4b8d-a319-982ea4d53395",
    # Replacements selected after current-role/company review.
    "f02f3b07-505b-4215-a643-0b146d5210fa", "a3a1d9da-217d-49ed-b04c-aec89f059f92",
    "fb2dc163-1ea9-4429-b62f-7020a38abeef", "ecaa48cd-9416-4df2-bce4-49996a02b995",
    "10e12f99-4499-4fe8-8a28-29502656be43", "a2899180-2ebe-4470-8cea-ab0b2482813f",
    "92ce4c57-f654-4e60-97c5-53fe1854ded8", "b411655d-b583-442d-84b7-dd84548dda98",
    "30e0efd2-18eb-451e-b65f-68a0f1615faa", "ce45022b-a45e-4348-9c6b-7f0108e45505",
    "e2629693-073f-45f0-bf39-f082a0f81e2a", "000a5391-edc6-4e1a-9628-1bc1bab2d766",
    "99bdfef5-0784-4bce-ade6-3b373b4e6519", "04764f36-5046-45e2-8b1d-c4df34c4a6f7",
    "1f55a47f-69c9-442d-9180-c6272ae04514", "e1c85955-b5ab-426a-9557-69cfad790102",
    "a3351e9e-4734-4551-b401-dab3a100a661", "d257c188-0478-421c-ad79-b7ba62e48f02",
    "9c094d5d-a9c7-4fb3-b404-9f76e0d3c837", "1e010f05-2055-4fba-83e1-0a4b9c107aa8",
    "06f92092-23f8-4709-b0b3-1224d0d0bbe7", "9b3f420e-5a2d-468f-8c75-a5084c14101b",
    "b77654e1-6cc6-43b9-8b3e-d9c28b86cf89", "ce4d9737-0722-4501-8d4c-d845165672b1",
    "93d347d1-3b81-4d39-8e92-779f13179fb1", "a05e69db-3072-48ce-bf86-bc7406433637",
    "82126587-7749-4be0-9195-62af852d577e", "811d3f04-5be4-4ce9-8b65-9bbdc85adf6b",
    "b2786dc5-f431-4787-b25b-b1b24086a640", "cfc817a0-c9a5-41b9-aa9f-aca073dd2596",
    "beea324f-170d-4b68-8bf3-974ad06f956e", "2bddaa63-3c11-4232-b308-5c00d15e1da2",
    "60255c5e-05ce-4eba-9b51-118017d4cb81", "e86bdbc2-145b-4dec-9381-b1c059427535",
    "f7a8fcfc-5a3c-46ec-bb1a-b44cba119b5f", "df07fe5e-781d-4da0-86b0-bebf9a94949a",
    "f20d5696-b13f-4d26-83f9-bd61b04a035d", "062862f4-575e-4e50-836b-994a609ef583",
    "15184c39-3411-4693-bad6-c727846139b5", "e05c66a2-4222-426a-a35a-a7c2db2b6d0b",
    "2cb2ae16-fc83-4bf0-ab10-4336e2126e3e", "476d61bb-037c-454d-aa0c-6e305468c190",
    "ac758067-5e4c-4cdf-a8cb-1eb12bd28484", "c9189238-796c-4f79-947d-b0ac09fddfe7",
    "eeb9eb48-7f58-49f0-9bf4-a6151b78ee6c", "8f03995c-32e2-4b1d-a513-8a1fca5638b0",
    "e0d4cde2-89d4-49af-9405-32098d64c850",
]


CORRECTIONS = {
    "93155242-26f8-48b2-9823-0d7229966d4e": ("Managing Director & CIO", "FedEx", "fedex.com"),
    "9c38c976-6f54-45f6-9a4a-315b0af42ccd": ("EVP & CISO", "SBM Bank (India)", "sbmbank.co.in"),
    "0dc38368-9b52-4bdb-b9c7-c2a312dd65ad": ("Head of Infrastructure & Information Security", "Novo Nordisk", "novonordisk.com"),
    "11cbea6a-bdac-4a68-bcaf-2fc4417ec7a1": ("Chief Information & Digital Officer", "Bosch Global Software Technologies", "bosch.com"),
    "02d0417d-2916-4f9c-a17c-433c8cc294da": ("Co-founder, Chief Technology & Growth Officer", "Unimech Aerospace and Manufacturing", "unimechaerospace.com"),
    "106eddba-fabb-49b5-b995-e50ebf17e3cb": ("SVP, CISO & CAIO", "Mphasis", "mphasis.com"),
    "24b77bc9-ee4b-4fa8-b209-5f08a252238c": ("Co-founder", "Ather Energy", "atherenergy.com"),
    "3a730261-e45c-43c0-ab79-cd5154cb1974": ("Sr. EVP - CIO & Central Operations", "Fino Payments Bank", "finobank.com"),
    "6ebefcbd-6f8a-4c49-a0ce-c307a1615932": ("Head of IT & CISO", "Avineon India", "avineonindia.com"),
    "3121bab5-2473-4e20-ba26-ad2402641e76": ("Director - IT & Information Security", "LeadSquared", "leadsquared.com"),
    "e688e4e4-0842-41d7-a66b-d2bdaa232b79": ("Director - IT", "Tally Solutions", "tallysolutions.com"),
    "269b4826-4497-49b0-af28-e3c53b52fa11": ("Co-founder & CTO", "Constelli Signals", "constelli.com"),
    "98f51000-1f10-4d82-afa2-c1d763b57074": ("Group CTO", "Embassy Group", "embassyindia.com"),
    "247f0d53-aa99-4cc4-86dd-47501a129323": ("Information Security Architect", "Circana", "circana.com"),
    "152c285e-9cfc-4844-973f-6f429c6acb8f": ("Founder, VRse Builder", "AutoVRse", "autovrse.in"),
    "7d072763-7e6b-4112-a83b-5e24a666d745": ("Global CISO & DPO", "ANSR", "ansr.com"),
    "0896297c-7df2-42ee-b225-b3c2c71d1785": ("CISO & CTSO", "Yotta Data Services", "yotta.com"),
    "cec9894c-67e1-4ab5-8684-6e077fb4d58b": ("CISO", "Capillary Technologies", "capillarytech.com"),
    "2691b81f-bc05-4e0a-b42c-864cc715858a": ("Head & Senior Director - Information Technology", "ACL Digital", "acldigital.com"),
    "ce45022b-a45e-4348-9c6b-7f0108e45505": ("Chief Technology Officer", "Biocon", "biocon.com"),
    "e2629693-073f-45f0-bf39-f082a0f81e2a": ("IT Director", "Meesho", "meesho.com"),
    "99bdfef5-0784-4bce-ade6-3b373b4e6519": ("Chief Information Officer", "Bharat Fritz Werner", "bfw.co.in"),
    "04764f36-5046-45e2-8b1d-c4df34c4a6f7": ("CISO - UPI & Wealth", "Groww", "groww.in"),
    "1f55a47f-69c9-442d-9180-c6272ae04514": ("CISO", "CRED", "cred.club"),
    "a3351e9e-4734-4551-b401-dab3a100a661": ("CISO & Data Protection Officer", "Sammaan Capital", "sammaancapital.com"),
    "9c094d5d-a9c7-4fb3-b404-9f76e0d3c837": ("CISO", "TVS Holdings", "tvsholdings.com"),
    "a05e69db-3072-48ce-bf86-bc7406433637": ("Head - Infrastructure Security", "CRED", "cred.club"),
    "b2786dc5-f431-4787-b25b-b1b24086a640": ("Senior Manager - Cyber Security", "Eli Lilly and Company", "lilly.com"),
    "cfc817a0-c9a5-41b9-aa9f-aca073dd2596": ("Manager - Information Security", "Tally Solutions", "tallysolutions.com"),
    "beea324f-170d-4b68-8bf3-974ad06f956e": ("Vice President - IT & CISO", "TVS Mobility", "tvs.in"),
    "e05c66a2-4222-426a-a35a-a7c2db2b6d0b": ("Chief Information Security Officer", "Essentra", "essentra.com"),
    "062862f4-575e-4e50-836b-994a609ef583": ("General Manager - Information Technology", "Bosch Global Software Technologies", "bosch.com"),
    "476d61bb-037c-454d-aa0c-6e305468c190": ("Information Security, Risk, Audit & Compliance Leader", "Aster DM Healthcare", "asterdmhealthcare.com"),
    "ac758067-5e4c-4cdf-a8cb-1eb12bd28484": ("Cybersecurity Advisor", "Eli Lilly and Company", "lilly.com"),
    "8f03995c-32e2-4b1d-a513-8a1fca5638b0": ("Chief Digital & Information Officer", "Adecco India", "adecco.co.in"),
}

NAME_CORRECTIONS = {
    "a3351e9e-4734-4551-b401-dab3a100a661": ("Prakash", "Kumar Ranjan"),
    "4351b046-4ee6-4c04-b47e-39e3d6ed1d32": ("Durga Prasad", "Dube"),
    "e1df4a4a-f529-4ee5-8b17-5f9cbae8ab66": ("Jagannath", "Sahoo"),
    "76ef7373-9741-4615-a1c0-20d7f4b59dcb": ("Devendran", "Thirunavukarasu"),
    "e0d4cde2-89d4-49af-9405-32098d64c850": ("Vinod", "S Chippalkatti"),
    "7d072763-7e6b-4112-a83b-5e24a666d745": ("Sandeep", "Kumar Akkimolla"),
    "7c650b87-9bdb-4baf-a304-0fb49629ebc1": ("Kamesh", "Babu R"),
}


def scope_for(lead) -> tuple[str, str, str]:
    text = f"{lead.title} {lead.company.name}".lower()
    if any(x in text for x in ("manufactur", "aerospace", "energy", "electronics", "bosch", "tvs", "biocon", "pharma")):
        return ("IT/OT resilience, identity and vulnerability management", "industrial and enterprise security", "security across connected operations")
    if any(x in text for x in ("bank", "fintech", "payments", "payu", "upstox", "groww", "razorpay", "moneyview", "insurance")):
        return ("application, cloud and identity security", "digital-finance security", "resilience across customer-facing systems")
    if any(x in text for x in ("ciso", "security", "cyber", "risk", "grc", "dpo")):
        return ("control validation, security operations and incident readiness", "security-program assurance", "the areas your team most wants independently tested")
    if any(x in text for x in ("cto", "engineering", "technology")):
        return ("application, cloud and product security", "secure engineering", "security without slowing product delivery")
    return ("cloud, identity and operational resilience", "enterprise security", "the security priorities behind your IT roadmap")


def role_phrase(lead) -> str:
    title = lead.title.lower()
    if any(x in title for x in ("ciso", "security", "cyber", "risk", "grc", "dpo")):
        return "your security remit"
    if any(x in title for x in ("cio", "information officer", "it director", "head of it")):
        return "your IT leadership remit"
    if any(x in title for x in ("cto", "technology", "engineering")):
        return "your technology leadership remit"
    return "your leadership remit"


def draft_for(lead, account_role: str) -> GeneratedContent:
    first = lead.first_name or lead.full_name.split()[0]
    company = lead.company.name
    theme, angle, outcome = scope_for(lead)
    role = role_phrase(lead)
    signature = "Warm regards,\n\nKarthik BT\nSales Executive - Cybersecurity Solutions\n+91-8105432939"
    emails = [
        SequenceEmail(step=1, delay_days=0, subject=f"Cybersecurity support for {company}", angle=angle, body=(
            f"Hi {first},\n\nI’m Karthik from Altisec Technologies. We work with organizations on cybersecurity across endpoint security, SOC and MDR, cloud security, data security and DLP, OT security, VAPT, and security compliance.\n\n"
            "I wanted to reach out to understand how your team is currently managing cybersecurity and whether you are evaluating any new solutions or services. If relevant, I’d be happy to have a brief discussion and understand your current setup and priorities.\n\n"
            f"Please let me know if we could connect for a short discussion.\n\n{signature}")),
        SequenceEmail(step=2, delay_days=3, subject=f"Re: security priorities at {company}", angle="practical scope", body=(
            f"Hi {first},\n\nFollowing up on my earlier note. Altisec Technologies supports organizations across endpoint security, SOC and MDR, cloud security, data security, OT security, VAPT, and compliance.\n\nPlease let me know if we could connect for a short discussion.\n\n{signature}")),
        SequenceEmail(step=3, delay_days=7, subject=f"One focused starting point for {company}", angle="independent validation", body=(
            f"Hi {first},\n\nIf your team is evaluating any cybersecurity solutions or services, I’d be happy to understand your current setup and priorities and see where Altisec Technologies could help.\n\nWould you be available for a brief discussion?\n\n{signature}")),
        SequenceEmail(step=4, delay_days=12, subject="Should I close this out?", angle="close the loop", body=(
            f"Hi {first},\n\nI wanted to follow up once more. If cybersecurity services are relevant for your team, please let me know and we can arrange a short discussion.\n\n{signature}")),
    ]
    linkedin = LinkedInSequence(
        connection_note=f"Hi {first}—I’m with Altisec. We help IT and security teams assess and improve {theme}. Thought it would be useful to connect.",
        messages=[
            LinkedInMessage(step=1, delay_days=2, body=f"Thanks for connecting, {first}. Altisec helps companies assess and fix cybersecurity gaps. For {company}, we could start with {theme}. Would a short call be useful?"),
            LinkedInMessage(step=2, delay_days=6, body="Altisec can assess the problem, test the controls and help with remediation across cloud, IAM, application security, SOC and incident response. Happy to send a concise starting scope."),
            LinkedInMessage(step=3, delay_days=10, body=f"I’ll leave it there, {first}. If {theme} becomes a priority at {company}, I’d be glad to compare notes."),
        ],
    )
    whatsapp = WhatsAppSequence(messages=[
        WhatsAppMessage(step=1, delay_days=0, body=f"Hi {first}, Karthik from Altisec here. We help security and IT teams find and fix gaps across cloud, applications, identity and SOC. Would a focused review of {theme} be useful at {company}?"),
        WhatsAppMessage(step=2, delay_days=6, body=f"Hi {first}, following up once on my Altisec note. If {theme} is relevant at {company}, I can send a short starting scope. Otherwise I’ll leave it here."),
    ])
    return GeneratedContent(
        emails=emails, linkedin=linkedin, whatsapp=whatsapp,
        style_notes=f"Manually re-audited 2026-09-01. Current role/company only; no event trigger or unsupported claim. Account role: {account_role}. Draft-only; no outreach approved.",
    )


def buyer_rank(title: str) -> int:
    t = title.lower()
    if "ciso" in t or "chief information security" in t: return 100
    if "cio" in t or "chief information officer" in t: return 95
    if "chief digital" in t: return 92
    if "cto" in t or "chief technology" in t: return 90
    if "security" in t or "cyber" in t: return 85
    if "head of it" in t or "it director" in t: return 80
    return 65


def run() -> dict[str, int]:
    if len(FINAL_IDS) != 100 or len(set(FINAL_IDS)) != 100:
        raise RuntimeError(f"Final set must contain 100 unique IDs, got {len(FINAL_IDS)}/{len(set(FINAL_IDS))}")
    session = get_session(); repo = Repository(session)
    all_leads = repo.list_leads(limit=20_000)
    by_id = {lead.id: lead for lead in all_leads}
    missing = [lead_id for lead_id in FINAL_IDS if lead_id not in by_id]
    if missing: raise RuntimeError(f"Missing lead IDs: {missing}")

    # Remove the old batch tag without deleting any source record.
    for lead in all_leads:
        if "altisec_batch_1_draft" in lead.tags:
            lead.tags = [tag for tag in lead.tags if tag != "altisec_batch_1_draft"]
            repo.save_lead(lead)

    selected = [by_id[lead_id] for lead_id in FINAL_IDS]
    for lead in selected:
        if lead.id in CORRECTIONS:
            title, company, domain = CORRECTIONS[lead.id]
            lead.title, lead.company.name, lead.company.domain = title, company, domain
        if lead.id in NAME_CORRECTIONS:
            lead.first_name, lead.last_name = NAME_CORRECTIONS[lead.id]

    grouped = defaultdict(list)
    for lead in selected: grouped[lead.company.name.strip().lower()].append(lead)
    account_role = {}
    for group in grouped.values():
        group.sort(key=lambda lead: (-buyer_rank(lead.title), lead.full_name.lower()))
        for index, lead in enumerate(group): account_role[lead.id] = "primary" if index == 0 else "backup_same_account"

    for lead in selected:
        theme, _, _ = scope_for(lead)
        email_domain = lead.email.rsplit("@", 1)[-1].lower() if "@" in lead.email else ""
        company_domain = (lead.company.domain or "").lower()
        contact_check = "company-domain email" if email_domain and email_domain == company_domain else "personal/alternate email; review before send"
        lead.research_card = ResearchCard(
            summary=f"{lead.full_name} is currently {lead.title} at {lead.company.name}. Profile reviewed on 2026-09-01.",
            key_signals=[f"Current role: {lead.title} at {lead.company.name}", contact_check],
            pain_points=[f"Potential discussion area, not a claimed active project: {theme}"],
            personalization_hooks=[f"Reference only {role_phrase(lead)}; do not quote the full LinkedIn headline."],
            unique_to_this_person=[f"Current ownership indicated by the role '{lead.title}'."],
            icp_rationale=f"Relevant owner or influencer for {theme}. Account role: {account_role[lead.id]}.",
            sources=[lead.linkedin_url, "https://altisec.in"], confidence=0.9 if "company-domain" in contact_check else 0.78,
        )
        lead.tags = list(dict.fromkeys([*lead.tags, "altisec_batch_1_draft", "profile_reaudited_2026_09_01", account_role[lead.id]]))
        lead.status = LeadStatus.NEW
        lead.sequence_state.paused = True
        lead.sequence_state.reason = "Final campaign draft review; no outreach approved"
        lead.score = max(lead.score, buyer_rank(lead.title))
        lead.notes = [n for n in lead.notes if "urbanebolt" not in n.lower() and not n.startswith("Profile re-audited 2026-09-01")]
        lead.notes.append(f"Profile re-audited 2026-09-01; {account_role[lead.id]}; {contact_check}.")
        repo.save_lead(lead)
        repo.save_content(lead.id, draft_for(lead, account_role[lead.id]))
    session.commit(); session.close()
    return {"selected": len(selected), "primary_accounts": sum(v == "primary" for v in account_role.values()), "backups": sum(v != "primary" for v in account_role.values())}


if __name__ == "__main__": print(run())
