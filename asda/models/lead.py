"""Unified Lead schema — the contract every source and every agent shares."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadStatus(str, Enum):
    NEW = "new"
    RESEARCHING = "researching"
    RESEARCHED = "researched"
    SEQUENCED = "sequenced"
    CONNECTED = "connected"
    REPLIED = "replied"
    MEETING_BOOKED = "meeting_booked"
    CLOSED = "closed"
    SUPPRESSED = "suppressed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"


PIPELINE_COLUMNS = [
    ("new", "New"),
    ("researched", "Researched"),
    ("sequenced", "In sequence"),
    ("connected", "Connected"),
    ("replied", "Replied"),
    ("meeting_booked", "Meeting"),
    ("closed", "Closed"),
    ("suppressed", "Suppressed"),
]


class Company(BaseModel):
    name: str = ""
    domain: str = ""
    industry: str = ""
    size: str = ""
    location: str = ""
    linkedin_url: str = ""
    description: str = ""


class ResearchCard(BaseModel):
    summary: str = ""
    key_signals: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    recent_news: list[str] = Field(default_factory=list)
    personalization_hooks: list[str] = Field(default_factory=list)
    unique_to_this_person: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    hiring_signals: list[str] = Field(default_factory=list)
    icp_rationale: str = ""
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("summary", "icp_rationale", mode="before")
    @classmethod
    def _as_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, dict):
            import json

            return json.dumps(v)
        return str(v)

    @field_validator(
        "key_signals",
        "pain_points",
        "recent_news",
        "personalization_hooks",
        "unique_to_this_person",
        "tech_stack",
        "hiring_signals",
        "sources",
        mode="before",
    )
    @classmethod
    def _as_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, dict):
            return [str(x) for x in v.values()]
        return [str(x) for x in v]


class SequenceState(BaseModel):
    sequence_id: str = ""
    channel: str = ""
    step_index: int = 0
    next_action_at: datetime | None = None
    variant: str = "A"
    paused: bool = False
    reason: str = ""
    # Dual-channel outreach
    email_step: int = 0
    email_replied: bool = False
    next_email_at: datetime | None = None
    linkedin_stage: str = "idle"  # idle | connect_sent | connected | messaging | done
    linkedin_messages_sent: int = 0
    linkedin_connected: bool = False
    linkedin_replied: bool = False
    next_linkedin_at: datetime | None = None
    max_linkedin_messages: int = 3
    last_inbound: str = ""
    thread: list[dict] = Field(default_factory=list)
    # Exclusive playbook: one conversation, then drop the rest. Call is last.
    email_dropped: bool = False
    linkedin_dropped: bool = False
    phone_stage: str = "idle"  # idle | queued | calling | done | skipped
    phone_execution_id: str = ""
    next_call_at: datetime | None = None
    last_touch_at: datetime | None = None


class Outcome(BaseModel):
    kind: str
    label: str = ""
    value: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class Lead(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = "manual"
    raw_data: dict[str, Any] = Field(default_factory=dict)
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    emails: list[str] = Field(default_factory=list)
    phone: str = ""
    linkedin_url: str = ""
    title: str = ""
    company: Company = Field(default_factory=Company)
    research_card: ResearchCard | None = None
    score: int = Field(default=0, ge=0, le=100)
    status: LeadStatus = LeadStatus.NEW
    sequence_state: SequenceState = Field(default_factory=SequenceState)
    outcomes: list[Outcome] = Field(default_factory=list)
    fingerprint: str = ""
    tags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("email", mode="before")
    @classmethod
    def _norm_email(cls, v: Any) -> str:
        return str(v or "").strip().lower()

    @field_validator("linkedin_url", mode="before")
    @classmethod
    def _norm_li(cls, v: Any) -> str:
        url = str(v or "").strip()
        if url and not url.startswith("http"):
            url = "https://" + url
        return url.rstrip("/")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def touch(self) -> None:
        self.updated_at = utcnow()

    def add_outcome(self, kind: str, label: str = "", **meta: Any) -> None:
        self.outcomes.append(Outcome(kind=kind, label=label, meta=meta))
        self.touch()


class LeadQuery(BaseModel):
    """Universal fetch contract for every LeadSource."""

    keywords: str | None = None
    titles: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    company_sizes: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    limit: int = 50
    extra: dict[str, Any] = Field(default_factory=dict)
