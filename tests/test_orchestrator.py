"""Orchestrator tests with a stub LLM. Verifies tool dispatch + decision extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.orchestrator import Orchestrator
from agent.prefetch import PrefetchBundle
from agent.session_state import SessionState


@dataclass
class StubBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = None  # type: ignore[assignment]


@dataclass
class StubResponse:
    content: list[StubBlock]
    stop_reason: str
    usage: Any = None


class StubLLM:
    def __init__(self, responses: list[StubResponse]) -> None:
        self._responses = responses
        self._calls = 0

    def create(self, **_kwargs: Any) -> StubResponse:
        if self._calls >= len(self._responses):
            raise AssertionError("StubLLM exhausted")
        r = self._responses[self._calls]
        self._calls += 1
        return r


def _state_with_order() -> SessionState:
    state = SessionState(session_id="s1", scenario_id=101, mode="dev", max_turns=8)
    state.turns_remaining = 8
    state.prefetch = PrefetchBundle(order_id=10, found=True)
    state.prefetch.order = {"id": 10, "total_inr": 500, "customer_id": 1, "status": "delivered"}
    return state


def test_orchestrator_extracts_decision_from_first_hop(monkeypatch, tmp_path: Path):
    decision_input = {
        "response": "Sorry, here's a wallet credit.",
        "actions": [
            {"type": "issue_refund", "order_id": 10, "amount_inr": 100, "method": "wallet_credit"},
        ],
        "reasoning": "Cold food.",
        "confidence": 0.85,
    }
    resp = StubResponse(
        content=[StubBlock(type="tool_use", id="t1", name="submit_decision", input=decision_input)],
        stop_reason="tool_use",
    )
    llm = StubLLM([resp])
    monkeypatch.setattr(
        "agent.orchestrator.Path",
        lambda *a, **k: type("P", (), {"read_text": lambda self, encoding="utf-8": "policy text"})(),
    )

    o = Orchestrator(llm=llm, app_conn=MagicMock(), policy_index=MagicMock())  # type: ignore[arg-type]
    o._policy_text = "policy"
    state = _state_with_order()
    bot_msg, actions, audit = o.run_turn(state, "My order #10 was cold")
    assert "wallet credit" in bot_msg.lower()
    assert actions[0]["type"] == "issue_refund"
    assert actions[0]["amount_inr"] == 100
    assert audit.error is None


def test_orchestrator_runs_tool_then_decision(monkeypatch):
    tool_call = StubResponse(
        content=[StubBlock(type="tool_use", id="t1", name="lookup_order", input={"order_id": 10})],
        stop_reason="tool_use",
    )
    decision_input = {
        "response": "Refund issued.",
        "actions": [
            {"type": "issue_refund", "order_id": 10, "amount_inr": 50, "method": "wallet_credit"},
            {"type": "close", "outcome_summary": "wallet credit applied"},
        ],
        "reasoning": "small issue",
        "confidence": 0.7,
    }
    final = StubResponse(
        content=[StubBlock(type="tool_use", id="t2", name="submit_decision", input=decision_input)],
        stop_reason="tool_use",
    )
    llm = StubLLM([tool_call, final])

    fake_app_conn = MagicMock()
    monkeypatch.setattr(
        "agent.tools.dispatch.sql.lookup_order",
        lambda conn, oid: {"found": True, "order": {"id": oid, "total_inr": 500}, "items": []},
    )

    o = Orchestrator(llm=llm, app_conn=fake_app_conn, policy_index=MagicMock())  # type: ignore[arg-type]
    o._policy_text = "policy"
    state = _state_with_order()
    bot_msg, actions, audit = o.run_turn(state, "Anything new on my order #10?")
    assert audit.error is None
    assert len(audit.tool_calls) == 1
    assert audit.tool_calls[0]["name"] == "lookup_order"
    assert actions[-1]["type"] == "close"


def test_orchestrator_clamps_overlarge_refund(monkeypatch):
    decision_input = {
        "response": "Refund.",
        "actions": [
            {"type": "issue_refund", "order_id": 10, "amount_inr": 9999, "method": "cash"},
        ],
        "reasoning": "test clamp",
        "confidence": 0.5,
    }
    resp = StubResponse(
        content=[StubBlock(type="tool_use", id="t1", name="submit_decision", input=decision_input)],
        stop_reason="tool_use",
    )
    o = Orchestrator(llm=StubLLM([resp]), app_conn=MagicMock(), policy_index=MagicMock())  # type: ignore[arg-type]
    o._policy_text = "policy"
    state = _state_with_order()
    _, actions, audit = o.run_turn(state, "Refund 9999")
    assert actions[0]["amount_inr"] == 500  # clamped to total
    assert any("validation_clamp" in n for n in audit.validation_notes)


def test_orchestrator_drops_malformed_refund_rather_than_escalating(monkeypatch):
    # Refund with no amount_inr is unsafe — should be dropped, not crash the turn.
    bad = {
        "response": "x",
        "actions": [{"type": "issue_refund"}],
        "reasoning": "bad",
        "confidence": 0.5,
    }
    resp = StubResponse(
        content=[StubBlock(type="tool_use", id="t1", name="submit_decision", input=bad)],
        stop_reason="tool_use",
    )
    o = Orchestrator(llm=StubLLM([resp]), app_conn=MagicMock(), policy_index=MagicMock())  # type: ignore[arg-type]
    o._policy_text = "policy"
    state = _state_with_order()
    _, actions, audit = o.run_turn(state, "msg")
    assert actions == []
    assert audit.error is None


def test_orchestrator_hard_fails_on_truly_invalid_response(monkeypatch):
    # Confidence > 1.0 violates Pydantic; orchestrator escalates as fail-safe.
    bad = {
        "response": "x",
        "actions": [],
        "reasoning": "bad",
        "confidence": 9.5,
    }
    resp = StubResponse(
        content=[StubBlock(type="tool_use", id="t1", name="submit_decision", input=bad)],
        stop_reason="tool_use",
    )
    o = Orchestrator(llm=StubLLM([resp]), app_conn=MagicMock(), policy_index=MagicMock())  # type: ignore[arg-type]
    o._policy_text = "policy"
    state = _state_with_order()
    _, actions, audit = o.run_turn(state, "msg")
    assert actions[0]["type"] == "escalate_to_human"
    assert audit.error is not None and "decision_validation_failed" in audit.error


def test_orchestrator_strips_premature_refund_on_claim_discrepancy():
    """Defect 2: customer says 'cold curry' on a pizza+bread order. The
    orchestrator must detect the mismatch and strip any refund the model
    emits before the customer has clarified which items were affected."""
    decision_input = {
        "response": "Crediting you ₹200 for the cold curry.",
        "actions": [
            {"type": "issue_refund", "order_id": 10, "amount_inr": 200, "method": "wallet_credit"},
        ],
        "reasoning": "Customer said curry was cold.",
        "confidence": 0.7,
    }
    resp = StubResponse(
        content=[StubBlock(type="tool_use", id="t1", name="submit_decision", input=decision_input)],
        stop_reason="tool_use",
    )
    o = Orchestrator(llm=StubLLM([resp]), app_conn=MagicMock(), policy_index=MagicMock())  # type: ignore[arg-type]
    o._policy_text = "policy"
    state = _state_with_order()
    state.prefetch.items = [
        {"item_name": "Margherita", "qty": 1, "price_inr": 300},
        {"item_name": "Garlic Bread", "qty": 1, "price_inr": 200},
    ]
    _, actions, audit = o.run_turn(state, "My curry was cold, refund please")
    # Refund must be stripped; the model is supposed to ask first.
    assert all(a["type"] != "issue_refund" for a in actions)
    assert any(
        "validation_dropped_premature_action_pre_clarification" in n
        for n in audit.validation_notes
    )


def test_orchestrator_blocks_refund_when_session_abuse_fired():
    """Defect 4: pre-ID injection + chargeback signals push abuse over
    threshold. Refund is stripped, escalate_to_human is injected, and
    the customer never sees the underlying signals (handled by scrubber
    when the model cites them; here we just check the action-level fix)."""
    decision_input = {
        "response": "Issuing a refund.",
        "actions": [
            {"type": "issue_refund", "order_id": 10, "amount_inr": 500, "method": "wallet_credit"},
        ],
        "reasoning": "Customer demanded refund.",
        "confidence": 0.4,
    }
    resp = StubResponse(
        content=[StubBlock(type="tool_use", id="t1", name="submit_decision", input=decision_input)],
        stop_reason="tool_use",
    )
    o = Orchestrator(llm=StubLLM([resp]), app_conn=MagicMock(), policy_index=MagicMock())  # type: ignore[arg-type]
    o._policy_text = "policy"
    state = _state_with_order()
    # Simulate accumulated session signals + a base abuse score that, when
    # boosted, fires the threshold. With the 0.30 session-boost cap, a base
    # of 0.45 + injection (0.30) = 0.75 → fired.
    state.session_signals = [
        {"signal": "injection_attempt_session", "weight": 0.30, "matched_text": "ignore prior"},
    ]
    state.prefetch.abuse = {
        "score": 0.45,
        "fired": False,
        "fire_threshold": 0.6,
        "rule_version": "v1",
        "signals": [],
        "customer_id": 1,
    }
    _, actions, audit = o.run_turn(state, "Just refund me already")
    assert all(a["type"] != "issue_refund" for a in actions)
    assert any(a["type"] == "escalate_to_human" for a in actions)
    assert any("validation_refund_blocked_by_session_abuse" in n for n in audit.validation_notes)
