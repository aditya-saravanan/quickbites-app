"""User-facing confidentiality scrubber.

Detects when the bot's response cites internal data (customer's complaint
history, refund frequency, restaurant ratings, rider records, abuse
signals, internal scores, the policy document) as the reason for an
action — which is a hard policy violation.

When triggered: rewrite the response to a neutral safe template (action-
only, no WHY), preserve the original in the audit trail, log the fired
tags. The internal `reasoning` field is unaffected — it's the right place
to record WHY decisions were made.

This is a defense-in-depth layer; the system prompt also instructs the
model not to do this. The scrubber is the floor that enforces the rule
even when the model slips.
"""

from __future__ import annotations

import re
from typing import Any

# Each pattern carries a tag name. Tag-and-rewrite, not silent drop.
_DISCLOSURE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "cites_complaint_history",
        re.compile(
            r"\b(?:your|past|prior|previous|recent|several|multiple|frequent|"
            r"high\s+number\s+of|number\s+of)\s+\w*\s*complaints?\b"
            r"|\bcomplaint\s+(?:history|pattern|frequency|rate|record)\b"
            r"|\b(?:high|increased|elevated)\s+complaint\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cites_refund_history",
        re.compile(
            r"\b(?:your|recent|multiple|frequent|several|the\s+number\s+of|past|prior)\s+\w*\s*refunds?\b"
            r"|\brefund\s+(?:history|frequency|pattern|rate|record)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cites_account_age",
        re.compile(
            r"\b(?:new|recently\s+created|brand[-\s]new|just\s+joined)\s+account\b"
            r"|\baccount\s+(?:age|is\s+(?:new|recent))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cites_abuse_score",
        re.compile(
            r"\b(?:abuse\s+score|risk\s+score|risk\s+rating|"
            r"flagged(?:\s+(?:on|in|by))?|"
            r"on\s+(?:our|your)\s+(?:records|system|file)|"
            r"in\s+(?:our|your)\s+(?:records|system|file)|"
            r"yellow\s+flag(?:ged|s)?|red\s+flag(?:ged|s)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cites_rider_history",
        re.compile(
            r"\brider'?s?\s+(?:record|history|incidents?|track\s+record|complaint(?:s)?)\b"
            r"|\b(?:clean|verified|spotless)\s+(?:rider|record)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cites_restaurant_history",
        re.compile(
            r"\brestaurant'?s?\s+(?:rating|history|track\s+record|"
            r"complaint\s+(?:rate|pattern|history|count)|reviews?)\b"
            r"|\bsimilar\s+complaints?\s+from\s+other\s+customers?\b"
            r"|\bother\s+customers?\s+(?:have|had)\s+(?:also\s+)?(?:reported|complained)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cites_internal_score",
        re.compile(
            r"\b(?:internal\s+(?:score|review|system|note|rating)"
            r"|policy\s+(?:document|states|says|requires|dictates)"
            r"|our\s+policy\s+(?:says|states|requires|prevents)"
            r"|per\s+our\s+(?:policy|guidelines))\b",
            re.IGNORECASE,
        ),
    ),
]


# Action-aware safe templates. The scrubber composes the user-facing
# response from these phrases based on what the bot actually emitted in
# `actions[]`. Critical: when the bot issued a refund, the rewritten
# response must announce it — telling the customer "a colleague will
# follow up" while we just credited them ₹X is trust-destroying.
_EMPATHY_DEFAULT = "I'm sorry about the trouble with this order."
_EMPATHY_THANKS = "Thanks for the details."
_CLOSE_TEMPLATE = (
    "Thanks for getting in touch. We're closing this out — please reach "
    "out anytime if anything else comes up."
)
_DEFAULT_TEMPLATE = (
    "Thanks for the details — let me get a colleague to take a closer "
    "look at this for you. They'll be in touch within 24 hours."
)
_REFUND_DECLINED_TEMPLATE = (
    "I appreciate you sharing this. I'm not able to apply a refund here "
    "myself, so I'd like to escalate it for a senior agent to review. "
    "You'll hear back within 24 hours."
)


def scan_disclosure(text: str) -> list[str]:
    """Return list of fired tag names. Empty list = clean."""
    if not text:
        return []
    return [tag for tag, pattern in _DISCLOSURE_PATTERNS if pattern.search(text)]


def _refund_phrase(refund_actions: list[dict[str, Any]], *, leading: bool) -> str:
    total = sum(int(a.get("amount_inr") or 0) for a in refund_actions)
    methods = {a.get("method", "wallet_credit") for a in refund_actions}
    method_label = (
        "to your original payment method"
        if methods == {"cash"}
        else "as wallet credit"
        if methods == {"wallet_credit"}
        else "to your account"
    )
    prefix = "I've issued" if leading else "I've also issued"
    return f"{prefix} ₹{total} {method_label} for this order."


def _complaint_phrase(complaint_actions: list[dict[str, Any]], *, leading: bool) -> str:
    targets = {a.get("target_type") for a in complaint_actions}
    prefix = "I've logged" if leading else "I've also logged"
    if targets == {"rider"}:
        return f"{prefix} a complaint with our rider operations team."
    if targets == {"restaurant"}:
        return f"{prefix} a complaint with the restaurant."
    if targets == {"app"}:
        return f"{prefix} this with our product team."
    return f"{prefix} a complaint with the relevant team."


def _escalate_phrase(*, leading: bool, has_refund_request: bool = False) -> str:
    prefix = "I've escalated" if leading else "I've also escalated"
    if has_refund_request and leading:
        return (
            "I've escalated this to a senior agent so the refund can be "
            "processed — they'll follow up with you within 24 hours."
        )
    return f"{prefix} this to a senior agent who'll follow up with you within 24 hours."


def compose_safe_response(
    actions: list[dict[str, Any]], fired_tags: list[str]
) -> tuple[str, list[dict[str, Any]]]:
    """Build a user-facing response that accurately reflects `actions`.

    Returns (text, mutated_actions). `mutated_actions` may include an
    auto-coerced `escalate_to_human` action when the disclosure fired
    but no concrete remedial action was emitted — the typical pattern
    when the model declined a refund while citing internal data.
    """
    mutated = list(actions)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for a in actions:
        by_type.setdefault(a.get("type", ""), []).append(a)

    if "close" in by_type:
        return _CLOSE_TEMPLATE, mutated

    refunds = by_type.get("issue_refund", [])
    complaints = by_type.get("file_complaint", [])
    escalates = by_type.get("escalate_to_human", [])

    parts: list[str] = []
    if refunds:
        parts.append(_EMPATHY_DEFAULT)
        parts.append(_refund_phrase(refunds, leading=True))
        if complaints:
            parts.append(_complaint_phrase(complaints, leading=False))
        if escalates:
            parts.append(_escalate_phrase(leading=False))
        return " ".join(parts), mutated

    if complaints:
        parts.append(_EMPATHY_DEFAULT)
        parts.append(_complaint_phrase(complaints, leading=True))
        if escalates:
            parts.append(_escalate_phrase(leading=False))
        return " ".join(parts), mutated

    if escalates:
        return _EMPATHY_THANKS + " " + _escalate_phrase(leading=True), mutated

    # No concrete action was taken but the response cited internal data.
    # Typical pattern: model declined a refund. Auto-coerce escalate so
    # the customer has a path forward.
    if not any(a.get("type") == "escalate_to_human" for a in mutated):
        mutated.append(
            {
                "type": "escalate_to_human",
                "reason": (
                    "auto_coerced_disclosure_scrub: "
                    f"original response cited {','.join(fired_tags)}"
                ),
            }
        )
    return _REFUND_DECLINED_TEMPLATE, mutated


def scrub_response(
    text: str, actions: list[dict[str, Any]]
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Scrub text if any disclosure tag fires.

    Returns (rewritten_text, fired_tags, mutated_actions). The new text
    is composed from the action set so it never lies about what the bot
    just did — issuing a refund and then telling the customer "we'll
    look into it" was the bug.
    """
    fired = scan_disclosure(text)
    if not fired:
        return text, [], list(actions)

    composed, mutated = compose_safe_response(actions, fired)
    return composed, fired, mutated
