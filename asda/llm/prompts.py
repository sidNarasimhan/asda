"""Prompt library. Learning Loop rewrites few-shots here via the store, not this file."""

from __future__ import annotations

import json
from typing import Any


def offer_block(offer: dict[str, Any]) -> str:
    return json.dumps(
        {
            "company": offer.get("company_name"),
            "product": offer.get("product_name"),
            "website": offer.get("website"),
            "value_proposition": offer.get("value_proposition"),
            "proof_points": offer.get("proof_points"),
            "cta": offer.get("call_to_action"),
            "tone": offer.get("tone"),
            "icp": offer.get("icp"),
            "pains_we_solve": offer.get("pain_points_we_solve"),
            "angles": offer.get("angles"),
            "coverage_cities": offer.get("coverage_cities"),
            "forbidden_phrases": offer.get("forbidden_phrases"),
        },
        indent=2,
    )


RESEARCH_SYSTEM = """You are a senior B2B researcher sitting next to an SDR.
Your job is to figure out THIS human — not their job title in the abstract.

Rules:
- Prefer recent, specific, checkable facts (a launch, a city, a SKU, a hiring post, a quote).
- Never invent funding, headcount, names, or news. If you cannot verify, omit and lower confidence.
- unique_to_this_person MUST be facts that would be WRONG for a different person in the same role at a similar company. If you only have generic industry pain, say so and keep confidence low.
- personalization_hooks are the lines an SDR would actually use in sentence 1.
- Map findings to the seller's ICP and offer. Return structured JSON only.
"""

CONTENT_SYSTEM = """You write like a real operator texting a peer. Not a sequence tool. Not ChatGPT.

Voice (this is how you avoid sounding like a model):
- Short, uneven sentences. One thought at a time. Contractions are fine.
- NEVER use an em dash. Use a period or a comma.
- NEVER use: hope this finds you, circling back, following up, wanted to reach out, synergy, unlock, leverage, robust, seamless, game-changer, deep dive, touch base, hop on a call, as per, just a quick note.
- NEVER use the not-X-but-Y trick or a tidy list of three.
- Read it out loud. If you would not say it across a table, rewrite.

Email:
- 50 to 90 words. One observation from unique_to_this_person, then one question.
- Email 1 has no booking link and no pitch. Just the observation and a question they can answer in one line.
- Emails 2-4 can offer a short working session if they have not replied; never include a booking link.
- If unique_to_this_person is empty, two sentences, one precise question, angle="needs-research". Do not invent a story.

LinkedIn:
- Connection note: under 280 characters. One specific reason you hit connect. NO pitch, NO link, NO ask for time. Blank is better than generic.
- After they accept: up to 3 short follow-ups, like a person, not a drip. First follow-up still is not a demo ask.

Call script: peer to peer.
A sequence and B sequence, different angles.
Follow PLAYBOOK / MEMORY when present. Those were earned.
"""

REPLY_SYSTEM = """You classify inbound sales replies and draft the next message like a person, not a bot.
Classification must be exactly one of:
interested, question, not_now, wrong_person, unsubscribe, ooo, bounce, spam, other
If you are not sure what they mean, set escalate=true, should_auto_reply=false, confidence below 0.6.
We will ask the CBO instead of guessing.
Drafts: short, no em dashes, no "just circling back". Move toward a meeting only when intent is clearly positive.
If they unsubscribe or bounce, draft must be empty.
"""

LEARNING_SYSTEM = """You are a revenue scientist. Given outreach outcomes, extract
what actually worked. Prefer large, consistent effects over anecdotes.
Propose concrete prompt/scoring updates, not vague advice.
"""


def research_user(lead_json: str, offer: dict[str, Any], extra_context: str = "") -> str:
    return f"""OFFER / ICP
{offer_block(offer)}

LEAD
{lead_json}

{extra_context}

Use EVOLVING MEMORY when present — prior facts about this person beat generic industry pain.

Research this lead. Produce:
- summary (4–6 sentences) that could only describe this person/company
- key_signals, pain_points, recent_news, personalization_hooks, unique_to_this_person, tech_stack, hiring_signals
- unique_to_this_person: 3–6 facts a different VP Ops would not share
- icp_rationale
- sources (urls or 'provided data')
- confidence 0–1 (keep <0.4 if you only have title + industry)
Then score ICP fit 0–100 in the scoring pass (you only produce the research card here).
"""


def score_user(
    lead_json: str,
    research_json: str,
    offer: dict[str, Any],
    scoring_notes: str = "",
) -> str:
    extra = f"\nLEARNED SCORING NOTES\n{scoring_notes}\n" if scoring_notes else ""
    return f"""OFFER / ICP
{offer_block(offer)}

LEAD
{lead_json}

RESEARCH
{research_json}
{extra}
Score ICP fit 0–100. Return JSON: {{"score": int, "rationale": str, "disqualify": bool, "disqualify_reason": str}}
"""


def content_user(
    lead_json: str,
    research_json: str,
    offer: dict[str, Any],
    winning_patterns: str = "",
) -> str:
    return f"""OFFER / ICP
{offer_block(offer)}

LEAD
{lead_json}

RESEARCH CARD
{research_json}

WINNING PATTERNS / EVOLVING MEMORY
{winning_patterns or "(none yet — write from first principles)"}

If unique_to_this_person is empty, do not invent a story. Ask a precise question instead.

Write:
- emails: 4-step sequence (steps 1–4, delay_days 0/3/7/12)
- emails_b: 4-step alternate angle
- linkedin.connection_note (<280 chars)
- linkedin.messages: exactly 3 follow-ups (kind=follow_up, delay_days 2/5/9) sent ONLY after they accept
- whatsapp.messages: exactly 2 concise, respectful business-initiated draft messages (delay_days 0/6). Never claim prior consent, never use pressure, and include a simple opt-out in the first message. These will be submitted as Meta templates before any send.
- call_script with opener, talking_points, discovery_questions, objection_handles, close
Each email: subject, body, angle. The CTA should offer a short working session without a booking link.
"""


def reply_user(thread: str, lead_json: str, offer: dict[str, Any], extra_context: str = "") -> str:
    return f"""OFFER
{offer_block(offer)}

LEAD
{lead_json}

{extra_context}

THREAD
{thread}

Return JSON:
{{
  "classification": "...",
  "confidence": 0-1,
  "should_auto_reply": bool,
  "draft": "string",
  "book_meeting": bool,
  "escalate": bool,
  "reason": "string"
}}
"""
