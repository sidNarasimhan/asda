from asda.agents.sequence import SequenceEngine
from asda.models.content import (
    GeneratedContent,
    LinkedInMessage,
    LinkedInSequence,
    SequenceEmail,
)
from asda.models.lead import LeadStatus


def _content() -> GeneratedContent:
    return GeneratedContent(
        emails=[
            SequenceEmail(step=1, delay_days=0, subject="One", body="body 1"),
            SequenceEmail(step=2, delay_days=3, subject="Two", body="body 2"),
            SequenceEmail(step=3, delay_days=7, subject="Three", body="body 3"),
        ],
        linkedin=LinkedInSequence(
            connection_note="Congrats on the round.",
            messages=[
                LinkedInMessage(step=1, kind="follow_up", body="msg 1", delay_days=2),
                LinkedInMessage(step=2, kind="follow_up", body="msg 2", delay_days=5),
                LinkedInMessage(step=3, kind="follow_up", body="msg 3", delay_days=9),
            ],
        ),
    )


def test_start_sends_email_and_delegates_linkedin(sample_lead):
    lead, logs = SequenceEngine().start(sample_lead, _content())
    assert lead.status is LeadStatus.SEQUENCED
    assert lead.sequence_state.email_step == 1
    assert lead.sequence_state.linkedin_stage == "delegated"
    assert any(a.action in {"sent", "connect"} for a in logs)


def test_delegated_linkedin_does_not_double_message(sample_lead):
    engine = SequenceEngine()
    content = _content()
    lead, _ = engine.start(sample_lead, content)
    lead, _ = engine.tick(lead, content)
    assert lead.sequence_state.linkedin_messages_sent == 0
    assert lead.sequence_state.linkedin_stage == "delegated"


def test_email_reply_drops_linkedin_and_call(fake_llm, sample_lead):
    engine = SequenceEngine()
    sample_lead.phone = "+919800011122"
    lead, _ = engine.start(sample_lead, _content())
    from asda.agents.reply import ReplyAgent

    lead, _, _ = engine.ingest_reply(
        lead, "Sure, let's coordinate a time", "email", reply_agent=ReplyAgent(llm=fake_llm)
    )
    assert lead.sequence_state.email_replied
    assert lead.sequence_state.linkedin_dropped is True
    assert lead.sequence_state.phone_stage == "skipped"


def test_call_only_after_email_followup_and_quiet_linkedin(sample_lead):
    engine = SequenceEngine()
    sample_lead.phone = "+919800011122"
    content = _content()
    lead, _ = engine.start(sample_lead, content)
    lead.sequence_state.email_step = 2
    lead.sequence_state.next_email_at = None
    lead.sequence_state.linkedin_stage = "connect_sent"
    lead.sequence_state.next_linkedin_at = None  # due
    lead, logs = engine.tick(lead, content)
    assert lead.sequence_state.phone_stage == "queued"
    assert lead.sequence_state.next_call_at is not None


def test_reply_stops_email_and_can_book(fake_llm, sample_lead):
    engine = SequenceEngine()
    lead, _ = engine.start(sample_lead, _content())
    from asda.agents.reply import ReplyAgent

    lead, decision, _ = engine.ingest_reply(
        lead, "Sure, let's coordinate a time", "email", reply_agent=ReplyAgent(llm=fake_llm)
    )
    assert lead.sequence_state.email_replied
    assert decision.book_meeting
    assert lead.status is LeadStatus.MEETING_BOOKED
