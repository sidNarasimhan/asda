from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ASDA_DRY_RUN", "true")
os.environ.setdefault("ASDA_HITL_STAGES", "")
os.environ.setdefault("XAI_API_KEY", "")
os.environ.setdefault("OPENROUTER_API_KEY", "")
os.environ.setdefault("ASDA_LLM_PROVIDER", "auto")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ASDA_DATA_DIR"] = str(Path(__file__).parent / "_tmp_data")

from asda.config import get_settings  # noqa: E402
from asda.db import session as db_session  # noqa: E402
from asda.llm.client import FakeLLM, reset_llm  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ASDA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ASDA_HITL_STAGES", "")
    monkeypatch.setenv("ASDA_DRY_RUN", "true")
    get_settings.cache_clear()
    db_session._ENGINE = None
    db_session._SESSION = None
    reset_llm()
    from asda.db.session import init_db

    init_db()
    yield
    get_settings.cache_clear()
    db_session._ENGINE = None
    db_session._SESSION = None
    reset_llm()


@pytest.fixture
def fake_llm():
    from asda.models.content import CallScript, GeneratedContent, LinkedInSequence, SequenceEmail
    from asda.models.lead import ResearchCard

    return FakeLLM(
        {
            "ResearchCard": ResearchCard(
                summary="HarborOps just raised a Series A and is hiring AEs.",
                key_signals=["Series A", "hiring AEs"],
                pain_points=["outbound is manual"],
                personalization_hooks=["Series A AE hiring"],
                unique_to_this_person=["HarborOps Series A and AE hiring in the last quarter"],
                icp_rationale="CRO at 80-person B2B SaaS",
                confidence=0.8,
            ),
            "ScoreResult": {"score": 78, "rationale": "strong ICP", "disqualify": False},
            "GeneratedContent": GeneratedContent(
                emails=[
                    SequenceEmail(
                        step=1,
                        delay_days=0,
                        subject="Series A + new AEs",
                        body="Marcus — saw HarborOps is hiring AEs after the round. Most teams at this stage still write sequences by hand. Worth a 15-min look?",
                        angle="funding+hiring",
                    ),
                    SequenceEmail(
                        step=2,
                        delay_days=3,
                        subject="quick thought on AE ramp",
                        body="If AE ramp is the bottleneck, the research-to-first-touch loop is usually where time dies.",
                        angle="ramp",
                    ),
                ],
                linkedin=LinkedInSequence(
                    connection_note="Congrats on the round — curious how you're ramping new AEs."
                ),
                call_script=CallScript(opener="Caught the Series A note."),
            ),
            "ReplyDecision": {
                "classification": "interested",
                "confidence": 0.9,
                "should_auto_reply": True,
                "draft": "Great — Karthik will coordinate a time.",
                "book_meeting": True,
                "escalate": True,
                "reason": "asked to meet",
            },
            "LearningPayload": {
                "summary": "Funding hooks convert.",
                "patterns": [
                    {
                        "kind": "opener",
                        "text": "mention recent funding",
                        "lift": 2.3,
                        "sample_size": 40,
                    }
                ],
                "prompt_updates": ["Prefer funding openers for Series A+"],
                "scoring_updates": {"funding_signal": 8.0},
            },
        }
    )


@pytest.fixture
def sample_lead():
    from asda.ingestion.normalize import fingerprint_for
    from asda.models.lead import Company, Lead

    lead = Lead(
        first_name="Marcus",
        last_name="Reed",
        email="marcus@harborops.com",
        title="CRO",
        linkedin_url="https://www.linkedin.com/in/marcusreed",
        company=Company(name="HarborOps", domain="harborops.com", industry="SaaS", size="80"),
        source="csv",
    )
    lead.fingerprint = fingerprint_for(lead)
    return lead
