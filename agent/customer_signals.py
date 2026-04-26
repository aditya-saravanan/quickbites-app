"""Customer-signal detectors used by the close-confirmation state machine.

Pure functions, no LLM dependency. Two detectors:
  - `detect_close_signal(message)` -> CloseSignal | None
  - `classify_close_response(message)` -> "affirmative" | "negative" | "ambiguous"

The state machine in `agent/orchestrator.py` consumes these and decides
whether to ask the user to confirm closing, emit close, or back off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


CloseKind = Literal["close_token", "winddown", "acceptance"]


@dataclass
class CloseSignal:
    kind: CloseKind
    matched_text: str
    confidence: float


# --- Close-signal detectors ---

_CLOSE_TOKEN_RE = re.compile(r"\bCLOSE\s*:", re.IGNORECASE)

# Wind-down: short messages of gratitude/farewell. Originally lifted from
# orchestrator._WINDDOWN_RE; expanded with additional satisfaction words.
_WINDDOWN_RE = re.compile(
    r"^[\s\W]*(?:"
    r"thanks|thank\s+you|thx|ty|cheers|appreciate(?:\s+(?:it|that))?|"
    r"take\s+care|bye|goodbye|good\s+night|"
    r"that(?:'s|\s+is)?\s+all|that(?:'s|\s+is)?\s+it|all\s+done|all\s+good|"
    r"sounds\s+good|we(?:'re|\s+are)?\s+good|no\s+thanks|no\s+thank\s+you|"
    r"i(?:'m|\s+am)?\s+(?:fine|good|ok(?:ay)?)|"
    r"perfect|great|awesome|wonderful|brilliant|"
    r"ok(?:ay)?\s*[,.!]?\s*$|done|"
    r"\U0001f44d|\U0001f64f|\U0001f60a|❤(?:️)?|\U0001f44b"  # 👍 🙏 😊 ❤️ 👋
    r")[\s\W]*$",
    re.IGNORECASE,
)

# Acceptance: customer has agreed with a proposal/resolution.
_ACCEPTANCE_RE = re.compile(
    r"\b(?:"
    r"okay\s+that\s+works|that\s+works|that(?:'s|\s+is)?\s+fair|"
    r"sounds\s+(?:fair|good|right)|agreed|deal|fine\s+by\s+me|"
    r"alright|fair\s+enough|works\s+for\s+me|"
    r"i(?:'ll|\s+will)?\s+take\s+(?:that|it)|i\s+accept"
    r")\b",
    re.IGNORECASE,
)

# If the message has a "but" preceding the acceptance phrase, it's not real
# acceptance — they're objecting to part of it. e.g., "okay that works but..."
_NEGATION_BEFORE_ACCEPT_RE = re.compile(
    r"\b(?:but|however|although|except|though)\b", re.IGNORECASE
)


def detect_close_signal(message: str) -> CloseSignal | None:
    """Return the strongest close signal in the message, or None."""
    if not message or not message.strip():
        return CloseSignal("winddown", "", 0.7)

    # 1. Strongest: explicit CLOSE: token anywhere
    m = _CLOSE_TOKEN_RE.search(message)
    if m:
        return CloseSignal("close_token", m.group(0), 1.0)

    msg_stripped = message.strip()

    # 2. Wind-down: short message of gratitude/farewell
    if len(msg_stripped) <= 30:
        m = _WINDDOWN_RE.search(msg_stripped)
        if m:
            return CloseSignal("winddown", m.group(0), 0.7)
    if len(msg_stripped) <= 8:
        return CloseSignal("winddown", msg_stripped, 0.7)

    # 3. Acceptance phrasing — only if not negated by "but/however/etc."
    m = _ACCEPTANCE_RE.search(message)
    if m:
        # If a negation word appears anywhere AFTER the accept phrase, treat
        # as acceptance-with-pushback (still a signal of ongoing engagement,
        # not closure).
        rest = message[m.end():]
        if _NEGATION_BEFORE_ACCEPT_RE.search(rest):
            return None
        # If a negation word appears BEFORE the accept phrase, it might be
        # "I won't accept that". Check.
        before = message[: m.start()]
        if _NEGATION_BEFORE_ACCEPT_RE.search(before):
            return None
        return CloseSignal("acceptance", m.group(0), 0.6)

    return None


# --- Customer response classifier (used after a confirm-to-close question) ---

# Affirmative: the customer is OK to close.
# `\W+` between "no" and the rest allows punctuation like "No, that's all"
# or "No. Thanks!" — common natural phrasings that "\s+" alone misses.
_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|sounds\s+good|"
    r"all\s+done|all\s+good|that(?:'s|\s+is)?\s+all|"
    r"no\W+(?:thanks|thank\s+you|nothing|"
    r"that(?:'s|\s+is)?\s+(?:it|all|fine))|"
    r"we(?:'re|\s+are)?\s+good|you\s+can\s+close|please\s+close|"
    r"thanks\s*(?:bye|goodbye)?|cheers|done|"
    r"close\s+(?:it|this|the\s+(?:chat|case|ticket))|"
    r"\bCLOSE\b)",
    re.IGNORECASE,
)

# Negative: the customer wants to continue.
_NEGATIVE_RE = re.compile(
    r"\b(?:wait|hold\s+on|actually|one\s+more|another\s+(?:thing|question|issue)|"
    r"i\s+(?:have|need|wanted)\s+(?:another|one\s+more|to\s+ask)|"
    r"don(?:'t|\s+not)?\s+close|not\s+yet|just\s+one\s+more|"
    r"hang\s+on|before\s+(?:you|we)|by\s+the\s+way)\b",
    re.IGNORECASE,
)


# --- Pre-ID hostile-signal detectors ---
# These are pre-customer-identification abuse signals — chargeback threats,
# legal threats, excessive escalation demands. Used by the orchestrator to
# build a session-level signal list that gets folded into compute_abuse_score
# once the customer is identified, so abuse history can't be laundered by
# withholding identification.

_CHARGEBACK_THREAT_RE = re.compile(
    r"\b(?:charge[\s-]?back|charge[\s-]?backs?|"
    r"dispute\s+(?:this|the)\s+(?:charge|payment|transaction)|"
    r"file\s+(?:a\s+)?dispute|"
    r"stop\s+payment|"
    r"reverse\s+(?:the\s+)?(?:charge|payment|transaction))\b",
    re.IGNORECASE,
)

_LEGAL_THREAT_RE = re.compile(
    r"\b(?:i(?:'ll|\s+will)?\s+sue|"
    r"sue\s+(?:you|quickbites)|"
    r"(?:my\s+)?lawyer|attorney|"
    r"small\s+claims|"
    r"consumer\s+(?:court|forum|protection)|"
    r"legal\s+action|"
    r"(?:file\s+(?:a\s+)?)?complaint\s+(?:to|with)\s+(?:consumer|cyber|police))\b",
    re.IGNORECASE,
)

_ESCALATION_DEMAND_RE = re.compile(
    r"\b(?:get\s+me\s+(?:a\s+)?(?:manager|supervisor|human|real\s+person)|"
    r"speak\s+to\s+(?:a\s+)?(?:manager|supervisor|human|real\s+person)|"
    r"transfer\s+(?:me\s+)?to\s+(?:a\s+)?(?:manager|supervisor|human|real\s+person)|"
    r"i\s+want\s+(?:to\s+speak\s+to\s+)?(?:a\s+)?(?:manager|supervisor|human))\b",
    re.IGNORECASE,
)


_SESSION_SIGNAL_RULES: list[tuple[str, float, "re.Pattern[str]"]] = [
    # Frustration-driven signals are weighted lighter than deliberate
    # injection attempts (which carry weight 0.30). A frustrated customer
    # making one threat shouldn't push past the fire threshold on its
    # own — these stack with DB-derived signals (high_complaint_rate,
    # repeat_rejection_history) to form a fuller picture.
    ("chargeback_threat", 0.10, _CHARGEBACK_THREAT_RE),
    ("legal_threat", 0.10, _LEGAL_THREAT_RE),
    ("escalation_demand", 0.05, _ESCALATION_DEMAND_RE),
]


def detect_session_signals(message: str) -> list[dict]:
    """Return the list of pre-ID hostile signals fired by `message`.

    Each entry: {"signal", "weight", "matched_text"}. The orchestrator
    is responsible for idempotent accumulation (don't double-count the
    same signal across turns).
    """
    if not message or not message.strip():
        return []
    out: list[dict] = []
    for name, weight, pattern in _SESSION_SIGNAL_RULES:
        m = pattern.search(message)
        if m:
            out.append({"signal": name, "weight": weight, "matched_text": m.group(0)})
    return out


def classify_close_response(
    message: str,
) -> Literal["affirmative", "negative", "ambiguous"]:
    """Classify a customer's response to a 'shall we wrap up?' question.

    Negative wins ties (defensive: better to keep talking than close on misread).
    Empty / very short / wind-down-style messages are read as affirmative —
    when the bot has already asked "shall we wrap up?", a casual "thanks!" is
    answering yes.
    """
    if not message or not message.strip():
        return "ambiguous"

    # Negative wins outright.
    if _NEGATIVE_RE.search(message):
        return "negative"

    # Direct affirmative phrasing.
    if _AFFIRMATIVE_RE.search(message.strip()):
        return "affirmative"

    # Wind-down detected → in the context of having been asked to close,
    # this counts as affirmative. (detect_close_signal returns winddown for
    # short gratitude/farewell messages and emoji-only.)
    if detect_close_signal(message) is not None:
        return "affirmative"

    return "ambiguous"
