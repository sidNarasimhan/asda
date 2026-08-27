"""Evolving memory for the SDR agent.

Kinds:
  episode     — something that happened
  fact        — durable world knowledge
  person      — unique notes about a lead
  preference  — CBO rules (pause, do-not-contact, tone)
  playbook    — what works in outreach
  goal        — current objective
  mistake     — what not to repeat
"""

from asda.memory.reflect import reflect
from asda.memory.store import (
    is_blocked,
    memory_block,
    recent,
    remember,
    search,
    seed_if_empty,
)

__all__ = [
    "is_blocked",
    "memory_block",
    "recent",
    "reflect",
    "remember",
    "search",
    "seed_if_empty",
]
