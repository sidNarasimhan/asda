from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    LEAD_INGESTED = "lead.ingested"
    LEAD_DEDUPED = "lead.deduped"
    RESEARCH_STARTED = "research.started"
    RESEARCH_COMPLETED = "research.completed"
    LEAD_SCORED = "lead.scored"
    LEAD_SUPPRESSED = "lead.suppressed"
    CONTENT_GENERATED = "content.generated"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    EMAIL_QUEUED = "email.queued"
    EMAIL_SENT = "email.sent"
    EMAIL_BOUNCED = "email.bounced"
    LINKEDIN_QUEUED = "linkedin.queued"
    LINKEDIN_SENT = "linkedin.sent"
    REPLY_RECEIVED = "reply.received"
    REPLY_CLASSIFIED = "reply.classified"
    MEETING_BOOKED = "meeting.booked"
    HANDOFF_COMPLETED = "handoff.completed"
    LEARNING_UPDATED = "learning.updated"
    SAFETY_PAUSED = "safety.paused"
    PIPELINE_FAILED = "pipeline.failed"
    WORKER_STARTED = "worker.started"
    WORKER_STOPPED = "worker.stopped"
    EMPLOYEE_TALK = "employee.talk"
    CONFIG_UPDATED = "config.updated"
    SEQUENCE_STEP = "sequence.step"
    SNAPSHOT_SAVED = "snapshot.saved"
    MEMORY_WRITTEN = "memory.written"
    MEMORY_REFLECTED = "memory.reflected"
    CBO_ASKED = "cbo.asked"
    CALL_QUEUED = "call.queued"
    CALL_PLACED = "call.placed"
    CALL_COMPLETED = "call.completed"


class Event(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: EventType
    lead_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "system"
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
