"""End-to-end state-machine tests using a stub LLM.

Each test scripts the orchestrator with sequenced StubLLM responses and
customer messages, then asserts the close_state transitions and the
resulting actions/directives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.orchestrator import _advance_close_state
from agent.prefetch import PrefetchBundle
from agent.prompts import (
    CONFIRM_V1_PHRASING,
    CONFIRM_V2_PHRASING,
    build_close_directive,
)
from agent.session_state import SessionState


@dataclass
class StubBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = None  # type: ignore[assignment]


def _state() -> SessionState:
    s = SessionState(session_id="s1", scenario_id=1, mode="dev", max_turns=8)
    s.turns_remaining = 8
    s.prefetch = PrefetchBundle(order_id=10, found=True)
    s.prefetch.order = {"id": 10, "total_inr": 500, "customer_id": 1, "status": "delivered"}
    return s


# --- _advance_close_state state-machine transitions ---

def test_normal_to_awaiting_v1_on_winddown_after_substantive_action():
    s = _state()
    s.has_taken_substantive_action = True
    new_state, kind = _advance_close_state(s, "thanks!")
    assert new_state == "awaiting_v1"
    assert kind == "winddown"
    assert s.close_confirmation_attempts == 1


def test_normal_winddown_ignored_when_no_substantive_action_yet():
    # Guard: don't close on "thanks!" if the bot hasn't done anything.
    s = _state()
    s.has_taken_substantive_action = False
    new_state, _ = _advance_close_state(s, "thanks!")
    assert new_state == "normal"


def test_normal_to_awaiting_v1_on_close_token():
    s = _state()
    new_state, kind = _advance_close_state(s, "Sure, sounds fair. CLOSE: thanks")
    assert new_state == "awaiting_v1"
    assert kind == "close_token"


def test_normal_no_signal_stays_normal():
    s = _state()
    new_state, _ = _advance_close_state(
        s, "I'd like a refund please, the food was completely cold"
    )
    assert new_state == "normal"


def test_v1_affirmative_to_emit_close():
    s = _state()
    s.close_state = "awaiting_v1"
    s.close_confirmation_attempts = 1
    new_state, _ = _advance_close_state(s, "yes please")
    assert new_state == "emit_close"


def test_v1_negative_back_to_normal():
    s = _state()
    s.close_state = "awaiting_v1"
    new_state, _ = _advance_close_state(s, "wait, actually one more thing")
    assert new_state == "normal"
    assert s.close_confirmation_attempts == 0


def test_v1_ambiguous_to_v2():
    s = _state()
    s.close_state = "awaiting_v1"
    s.close_confirmation_attempts = 1
    new_state, _ = _advance_close_state(s, "hmm, I'm not sure")
    assert new_state == "awaiting_v2"
    assert s.close_confirmation_attempts == 2


def test_v2_ambiguous_backs_off_to_cooldown():
    s = _state()
    s.close_state = "awaiting_v2"
    s.close_confirmation_attempts = 2
    new_state, _ = _advance_close_state(s, "well, maybe")
    assert new_state == "cooldown"
    assert s.cooldown_turns_remaining > 0
    assert s.close_confirmation_attempts == 0


def test_v2_affirmative_to_emit_close():
    s = _state()
    s.close_state = "awaiting_v2"
    new_state, _ = _advance_close_state(s, "yes we're done")
    assert new_state == "emit_close"


def test_cooldown_decrements_then_normal():
    s = _state()
    s.close_state = "cooldown"
    s.cooldown_turns_remaining = 2
    _advance_close_state(s, "anything")
    assert s.close_state == "cooldown"
    assert s.cooldown_turns_remaining == 1
    _advance_close_state(s, "still anything")
    assert s.close_state == "normal"
    assert s.cooldown_turns_remaining == 0


def test_strong_signal_with_one_turn_left_skips_to_emit_close():
    s = _state()
    s.turns_remaining = 1
    new_state, _ = _advance_close_state(s, "CLOSE: bye")
    assert new_state == "emit_close"


# --- Directive rendering ---

def test_directive_v1_includes_v1_phrasing():
    d = build_close_directive(state="awaiting_v1", confirmation_attempt=1, last_signal_kind="winddown")
    assert d is not None
    assert CONFIRM_V1_PHRASING in d
    assert "do NOT emit" in d.lower() or "do not emit" in d.lower()


def test_directive_v2_uses_different_wording_than_v1():
    d2 = build_close_directive(state="awaiting_v2", confirmation_attempt=2)
    assert d2 is not None
    assert CONFIRM_V2_PHRASING in d2
    assert CONFIRM_V1_PHRASING not in d2  # critical: distinct wording


def test_directive_emit_close_requires_close_action():
    d = build_close_directive(state="emit_close", confirmation_attempt=0)
    assert d is not None
    assert "emit_close_now" in d


def test_directive_cooldown_tells_model_to_stop():
    d = build_close_directive(state="cooldown", confirmation_attempt=0)
    assert d is not None
    assert "stop" in d.lower() or "do not" in d.lower()


def test_directive_normal_returns_none():
    assert build_close_directive(state="normal", confirmation_attempt=0) is None


# --- Decision validators honor state machine ---

def test_validate_strips_premature_close_when_awaiting_v1():
    from agent.decision_schema import Decision, validate_and_clamp

    d = Decision(
        response="Shall we wrap up?",
        actions=[{"type": "close", "outcome_summary": "premature attempt"}],
        reasoning="x",
        confidence=0.7,
        close_intent="ask_user_to_close",
    )
    bundle = PrefetchBundle(order_id=10, found=True)
    bundle.order = {"id": 10, "total_inr": 500, "customer_id": 1, "status": "delivered"}
    out, notes = validate_and_clamp(d, bundle, close_state="awaiting_v1")
    assert all(a["type"] != "close" for a in out.actions)
    assert any("validation_dropped_premature_close" in n for n in notes)


def test_validate_injects_close_when_state_is_emit_close_and_action_missing():
    from agent.decision_schema import Decision, validate_and_clamp

    d = Decision(
        response="Take care!",
        actions=[],
        reasoning="user confirmed",
        confidence=0.9,
        close_intent="emit_close_now",
    )
    bundle = PrefetchBundle(order_id=10, found=True)
    bundle.order = {"id": 10, "total_inr": 500, "customer_id": 1, "status": "delivered"}
    out, notes = validate_and_clamp(d, bundle, close_state="emit_close")
    assert any(a["type"] == "close" for a in out.actions)
    assert any("validation_injected_close" in n for n in notes)
