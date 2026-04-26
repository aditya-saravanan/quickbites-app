"""Prompt-injection tripwire for untrusted customer messages.

Tags suspicious patterns; never drops content. The audit trail is the point.
The system prompt is the primary defense; this module exists to surface
signal so the model re-anchors on policy.
"""

from __future__ import annotations

import html
import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_prior",
        re.compile(
            r"\b(?:ignore|forget|disregard)\s+(?:(?:all|any|the|previous|prior|above|earlier)\s+)+"
            r"(?:instructions|rules|prompt|messages|context|policies|policy)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_injection",
        re.compile(
            r"^\s*(?:system|developer|assistant)\s*(?:[:\-]|\s+(?:override|message|update))",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"\b(?:you\s+are\s+now|act\s+as|pretend\s+(?:you\s+are|to\s+be)|"
            r"new\s+(?:instructions|rules)|override\s+(?:your|the)\s+"
            r"(?:role|policy|rules|policies))",
            re.IGNORECASE,
        ),
    ),
    (
        "policy_override",
        re.compile(
            r"\boverride\b[^.\n]{0,40}\b(?:polic(?:y|ies)|rules|cap|limit)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_override",
        re.compile(
            r"\bsystem\s+(?:override|message|update|prompt)\b"
            r"|\boverride\s*[:\-]",
            re.IGNORECASE,
        ),
    ),
    (
        "tag_smuggling",
        re.compile(r"</?(?:policy|system|customer_message|session_context|tool_result)\b", re.IGNORECASE),
    ),
    (
        "large_refund_demand",
        re.compile(
            r"(?:credit\s+me|refund\s+me|give\s+me|i\s+want|i\s+demand)\s+"
            r"(?:back\s+)?₹?\s*(\d{4,})",
            re.IGNORECASE,
        ),
    ),
    (
        "reveal_instructions",
        re.compile(
            r"\b(?:show|reveal|tell|print|reproduce|repeat|output|leak)\s+"
            r"(?:me\s+)?(?:your|the)\s+(?:system\s+prompt|instructions|policy|rules|prompt)",
            re.IGNORECASE,
        ),
    ),
]


def scan(message: str) -> list[str]:
    """Return list of triggered tags. Empty list = clean."""
    hits: list[str] = []
    for tag, pattern in _PATTERNS:
        if pattern.search(message):
            hits.append(tag)
    return hits


def safe_wrap(message: str, *, turn: int) -> str:
    """HTML-escape any tag-smuggling fragments, then wrap in customer_message."""
    escaped = html.escape(message, quote=False)
    return f'<customer_message turn="{turn}">{escaped}</customer_message>'
