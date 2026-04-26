"""PII redaction for audit writes.

Scrubs phone, email, and Indian-style addresses from free-text fields
before they hit the audit DB. Raw values are stored separately in
pii_vault behind stricter access for forensic replay.
"""

from __future__ import annotations

import re
from typing import Any

_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def redact_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (redacted_text, [(field, raw_value), ...])."""
    extracted: list[tuple[str, str]] = []

    def _phone_sub(m: re.Match[str]) -> str:
        extracted.append(("phone", m.group(0)))
        return "[REDACTED-PHONE]"

    def _email_sub(m: re.Match[str]) -> str:
        extracted.append(("email", m.group(0)))
        return "[REDACTED-EMAIL]"

    redacted = _PHONE_RE.sub(_phone_sub, text)
    redacted = _EMAIL_RE.sub(_email_sub, redacted)
    return redacted, extracted


def redact_dict_strings(obj: Any) -> Any:
    """Recursively redact phone/email patterns in any string within a JSON-like structure."""
    if isinstance(obj, str):
        out, _ = redact_text(obj)
        return out
    if isinstance(obj, list):
        return [redact_dict_strings(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_dict_strings(v) for k, v in obj.items()}
    return obj
