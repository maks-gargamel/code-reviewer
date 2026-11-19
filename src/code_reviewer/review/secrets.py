"""Маскирование секретов перед отправкой diff в облачную модель."""

from __future__ import annotations

import math
import re
from collections import Counter

PLACEHOLDER = "<REDACTED_SECRET>"

_KNOWN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
)

_SENSITIVE_QUOTED_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|apikey|secret|password|passwd|token|access[_-]?key)\b"
    r"(\s*[:=]\s*)(['\"])([^\r\n'\"]{6,})\3"
)

_QUOTED_CANDIDATE = re.compile(r"(['\"])([A-Za-z0-9+/=_\-!$%^&*]{32,})\1")
_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")


def _shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _is_high_entropy_secret(value: str) -> bool:
    if value == PLACEHOLDER or "://" in value or "/" in value:
        return False
    if _HEX_ONLY.match(value):
        return False
    classes = sum(
        (
            any(ch.islower() for ch in value),
            any(ch.isupper() for ch in value),
            any(ch.isdigit() for ch in value),
            any(not ch.isalnum() for ch in value),
        )
    )
    if classes < 3:
        return False
    return _shannon_entropy(value) >= 4.2


def redact(text: str) -> tuple[str, int]:
    """Заменить секреты на маркер `<REDACTED_SECRET>`, не ломая идентификаторы и выражения."""
    count = 0

    for pattern in _KNOWN_PATTERNS:
        text, replaced = pattern.subn(PLACEHOLDER, text)
        count += replaced

    def _replace_assignment(match: re.Match[str]) -> str:
        nonlocal count
        value = match.group(4)
        if value == PLACEHOLDER:
            return match.group(0)
        count += 1
        quote = match.group(3)
        return f"{match.group(1)}{match.group(2)}{quote}{PLACEHOLDER}{quote}"

    text = _SENSITIVE_QUOTED_ASSIGNMENT.sub(_replace_assignment, text)

    def _replace_entropy(match: re.Match[str]) -> str:
        nonlocal count
        value = match.group(2)
        if not _is_high_entropy_secret(value):
            return match.group(0)
        count += 1
        quote = match.group(1)
        return f"{quote}{PLACEHOLDER}{quote}"

    text = _QUOTED_CANDIDATE.sub(_replace_entropy, text)
    return text, count
