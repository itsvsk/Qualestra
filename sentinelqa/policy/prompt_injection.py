from __future__ import annotations

import re

PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore (all |your )?(previous|system) instructions",
        r"you are now",
        r"reveal (the )?(system prompt|credentials|secrets)",
        r"visit https?://",
        r"execute (this )?(code|command|sql)",
    )
)


def detect_prompt_injection(text: str) -> tuple[str, ...]:
    return tuple(pattern.pattern for pattern in PATTERNS if pattern.search(text))
