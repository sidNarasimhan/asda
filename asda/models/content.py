from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


def _as_str_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [f"{k}: {val}" if not str(val).startswith(str(k)) else str(val) for k, val in v.items()]
    return [str(x) for x in v]


class SequenceEmail(BaseModel):
    step: int = 1
    delay_days: int = 0
    subject: str = ""
    body: str = ""
    variant: str = "A"
    angle: str = ""


class LinkedInMessage(BaseModel):
    step: int = 1
    delay_days: int = 0
    kind: str = "follow_up"
    body: str = ""
    variant: str = "A"


class LinkedInSequence(BaseModel):
    connection_note: str = ""
    messages: list[LinkedInMessage] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _note_from_dict(cls, v):
        if isinstance(v, str):
            return {"connection_note": v, "messages": []}
        return v


class WhatsAppMessage(BaseModel):
    step: int = 1
    delay_days: int = 0
    body: str = ""


class WhatsAppSequence(BaseModel):
    """Drafts only; business-initiated delivery requires an approved Meta template."""
    template_name: str = "altisec_security_intro"
    messages: list[WhatsAppMessage] = Field(default_factory=list)


class CallScript(BaseModel):
    opener: str = ""
    talking_points: list[str] = Field(default_factory=list)
    discovery_questions: list[str] = Field(default_factory=list)
    objection_handles: list[str] = Field(default_factory=list)
    close: str = ""

    @field_validator(
        "talking_points", "discovery_questions", "objection_handles", mode="before"
    )
    @classmethod
    def _listify(cls, v):
        return _as_str_list(v)


class GeneratedContent(BaseModel):
    emails: list[SequenceEmail] = Field(default_factory=list)
    emails_b: list[SequenceEmail] = Field(default_factory=list)
    linkedin: LinkedInSequence = Field(default_factory=LinkedInSequence)
    whatsapp: WhatsAppSequence = Field(default_factory=WhatsAppSequence)
    call_script: CallScript = Field(default_factory=CallScript)
    style_notes: str = ""

    @model_validator(mode="after")
    def _number_steps(self):
        for i, e in enumerate(self.emails, 1):
            if not e.step:
                e.step = i
        for i, m in enumerate(self.linkedin.messages, 1):
            if not m.step:
                m.step = i
        return self
