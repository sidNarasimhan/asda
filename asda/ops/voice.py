"""Make outreach sound like a person, not a model.

Research (2025-2026 cold email / LinkedIn):
- Specific observation, then one question. No pitch in the first LinkedIn note.
- Ban filler, triplets, not-X-but-Y, em dashes, booking-link dumps on email 1.
- If you would not say it across a table, do not send it.
"""

from __future__ import annotations

import re

BANNED_PHRASES = (
    "i hope this email finds you well",
    "i hope you're well",
    "hope you're well",
    "hope this finds you",
    "just circling back",
    "just following up",
    "wanted to reach out",
    "reaching out because",
    "i wanted to introduce",
    "synergy",
    "unlock",
    "leverage",
    "streamline",
    "game-changer",
    "game changer",
    "cutting-edge",
    "best-in-class",
    "deep dive",
    "circle back",
    "touch base",
    "per my last",
    "as per my last",
    "quick question for you",
    "i'd love to hop on",
    "jump on a call",
    "end-to-end",
    "robust",
    "seamless",
    "empower",
    "supercharge",
)

_EM = re.compile(r"\s*[\u2014\u2013—–]\s*")


def humanize(text: str) -> str:
    """Strip model tells. Never introduce em dashes."""
    if not text:
        return text
    out = _EM.sub(", ", text)
    out = out.replace("\u2014", ", ").replace("\u2013", "-")
    out = re.sub(r"\bnot\s+[^.?]{2,40},\s+but\b", "but", out, flags=re.I)
    for phrase in BANNED_PHRASES:
        out = re.sub(re.escape(phrase), "", out, flags=re.I)
    out = re.sub(r" {2,}", " ", out)
    out = re.sub(r"\s+,", ",", out)
    return out.strip()


def looks_like_ai(text: str) -> bool:
    if not text:
        return True
    if "\u2014" in text or "—" in text:
        return True
    low = text.lower()
    return any(p in low for p in BANNED_PHRASES[:12])
