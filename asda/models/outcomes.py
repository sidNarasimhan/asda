from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ReplyClass(str, Enum):
    INTERESTED = "interested"
    QUESTION = "question"
    NOT_NOW = "not_now"
    WRONG_PERSON = "wrong_person"
    UNSUBSCRIBE = "unsubscribe"
    OOO = "ooo"
    BOUNCE = "bounce"
    SPAM = "spam"
    OTHER = "other"


class Pattern(BaseModel):
    kind: str  # opener | subject | hook | icp_attr | angle
    text: str
    lift: float = 0.0
    sample_size: int = 0
    notes: str = ""


class LearningInsight(BaseModel):
    period: str
    summary: str
    patterns: list[Pattern] = Field(default_factory=list)
    prompt_updates: list[str] = Field(default_factory=list)
    scoring_updates: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
