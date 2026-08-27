from asda.agents.learning import LearningAgent
from asda.db.repository import Repository
from asda.db.session import get_session
from asda.models.lead import LeadStatus


def test_learning_extracts_patterns(fake_llm, sample_lead):
    sample_lead.status = LeadStatus.MEETING_BOOKED
    sample_lead.score = 88
    sample_lead.add_outcome("meeting_requested", "Coordinate manually with Karthik")
    session = get_session()
    Repository(session).upsert_lead(sample_lead)
    session.commit()
    session.close()

    insight = LearningAgent(llm=fake_llm).run()
    assert "Funding" in insight.summary or insight.patterns
    assert insight.patterns[0].lift == 2.3

    session = get_session()
    stored = Repository(session).winning_patterns()
    session.close()
    assert stored
