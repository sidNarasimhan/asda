"""Content Generation Agent — email + LinkedIn + call script, with A/B variants."""

from __future__ import annotations

from typing import Any

from asda.agents.base import audit
from asda.config import get_settings
from asda.llm.client import LLMClient, get_llm
from asda.llm.prompts import CONTENT_SYSTEM, content_user
from asda.models.audit import AuditEntry
from asda.models.content import GeneratedContent
from asda.models.lead import Lead


class ContentAgent:
    name = "content"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm()

    def run(
        self,
        lead: Lead,
        winning_patterns: str = "",
        **_: Any,
    ) -> tuple[Lead, GeneratedContent, list[AuditEntry]]:
        settings = get_settings()
        research = (
            lead.research_card.model_dump_json(indent=2)
            if lead.research_card
            else "{}"
        )
        from asda.ops.playbook import playbook_block

        patterns = winning_patterns or playbook_block()
        try:
            from asda.memory.store import memory_block

            mem = memory_block(lead)
            if mem:
                patterns = (patterns + "\n\n" + mem).strip()
        except Exception:
            pass
        content = self.llm.parse(
            CONTENT_SYSTEM,
            content_user(
                lead.model_dump_json(indent=2),
                research,
                settings.offer,
                winning_patterns=patterns,
            ),
            GeneratedContent,
            model=settings.model_frontier,
        )
        content = _enforce_limits(content)
        content = _human_copy(content)
        logs = [
            audit(
                self.name,
                "generated",
                f"{len(content.emails)} emails + LinkedIn + call script",
                subjects=[e.subject for e in content.emails],
            )
        ]
        return lead, content, logs


def _enforce_limits(content: GeneratedContent) -> GeneratedContent:
    note = content.linkedin.connection_note
    if len(note) > 280:
        content.linkedin.connection_note = note[:277].rstrip() + "..."
    return content


def _human_copy(content: GeneratedContent) -> GeneratedContent:
    from asda.ops.voice import humanize

    for seq in (content.emails, content.emails_b):
        for item in seq or []:
            item.subject = humanize(item.subject)
            item.body = humanize(item.body)
    if content.linkedin:
        content.linkedin.connection_note = humanize(content.linkedin.connection_note)
        for msg in content.linkedin.messages or []:
            if hasattr(msg, "body"):
                msg.body = humanize(msg.body)
    if content.whatsapp:
        for message in content.whatsapp.messages or []:
            message.body = humanize(message.body)
    return content
