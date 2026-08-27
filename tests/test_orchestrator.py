from asda.agents.orchestrator import Orchestrator
from asda.agents.reply import ReplyAgent
from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.lead import LeadStatus


def test_pipeline_researches_and_writes(fake_llm, sample_lead):
    orch = Orchestrator(llm=fake_llm)
    result = orch.run(sample_lead, skip_outreach=True)
    assert result["decision"] in {"content_ready", "sequenced"}
    lead = result["lead"]
    assert lead.score == 78
    assert lead.research_card is not None
    assert "Series A" in lead.research_card.summary
    assert result["content"] is not None
    assert result["content"].emails[0].subject
    assert lead.status in {LeadStatus.RESEARCHED, LeadStatus.SEQUENCED, LeadStatus.AWAITING_APPROVAL}


def test_low_score_suppresses(fake_llm, sample_lead):
    fake_llm.canned["ScoreResult"] = {
        "score": 20,
        "rationale": "too small",
        "disqualify": False,
    }
    orch = Orchestrator(llm=fake_llm)
    result = orch.run(sample_lead, skip_outreach=True)
    assert result["decision"] == "suppressed"
    assert result["lead"].status is LeadStatus.SUPPRESSED


def test_disqualify_flag(fake_llm, sample_lead):
    fake_llm.canned["ScoreResult"] = {
        "score": 70,
        "rationale": "agency",
        "disqualify": True,
        "disqualify_reason": "agency",
    }
    result = Orchestrator(llm=fake_llm).run(sample_lead, skip_outreach=True)
    assert result["lead"].status is LeadStatus.SUPPRESSED


def test_reply_books_meeting(fake_llm, sample_lead):
    lead, decision, _ = ReplyAgent(llm=fake_llm).run(sample_lead, "Sure, let's coordinate a time")
    assert decision.book_meeting
    assert lead.status is LeadStatus.MEETING_BOOKED


def test_persists_lead(fake_llm, sample_lead):
    Orchestrator(llm=fake_llm).run(sample_lead, skip_outreach=True)
    session = get_session()
    saved = Repository(session).get_lead(sample_lead.id)
    session.close()
    assert saved is not None
    assert saved.score == 78
