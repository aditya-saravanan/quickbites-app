"""Audit store round-trip tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.llm_client import LLMUsage
from agent.orchestrator import AuditRecord
from agent.prefetch import PrefetchBundle
from agent.session_state import SessionState
from audit.store import AuditStore


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    db = tmp_path / "audit.db"
    return AuditStore(str(db))


def test_run_lifecycle(store):
    store.create_run("r_test", "dev", scenarios_requested=2)
    store.increment_run_progress("r_test")
    store.increment_run_progress("r_test")
    store.finish_run("r_test", "completed")
    info = store.get_run("r_test")
    assert info["run"]["status"] == "completed"
    assert info["run"]["scenarios_completed"] == 2


def test_session_and_turn_round_trip(store):
    store.create_run("r1", "dev", 1)
    state = SessionState(
        session_id="s1",
        scenario_id=101,
        mode="dev",
        max_turns=8,
        prefetch=PrefetchBundle(order_id=10, found=True),
    )
    state.prefetch.order = {"id": 10, "total_inr": 500, "customer_id": 1, "status": "delivered"}
    store.open_session(
        run_id="r1",
        state=state,
        prefetch_json=state.prefetch.to_dict(),
        agent_version="test",
        rule_version="v1",
    )
    audit = AuditRecord(
        session_id="s1",
        turn_number=1,
        customer_message="My order #10 was missing the naan; my phone +91-9876543210",
        bot_message="Sorry. Wallet credit issued.",
        injection_flags=["foo"],
        tool_calls=[{"name": "lookup_order", "args": {"order_id": 10}, "result": {"x": 1}}],
        llm_responses=[{"hop": 0, "stop_reason": "tool_use", "content": []}],
        parsed_actions=[
            {"type": "issue_refund", "order_id": 10, "amount_inr": 50, "method": "wallet_credit"}
        ],
        reasoning="cold food",
        confidence=0.9,
        abuse_score=0.0,
        validation_notes=[],
        latency_ms=1234,
        usage=LLMUsage(input_tokens=10, output_tokens=20, cache_read_tokens=5, cache_write_tokens=0),
    )
    store.write_turn(audit)
    store.close_session(session_id="s1", close_reason="bot_closed", final_score=None)

    payload = store.get_session("s1")
    assert payload is not None
    assert payload["session"]["close_reason"] == "bot_closed"
    assert len(payload["turns"]) == 1
    turn = payload["turns"][0]
    assert "REDACTED-PHONE" in turn["customer_msg"]
    assert turn["latency_ms"] == 1234
    # PII not surfaced without flag
    assert payload["pii"] == []
    # PII vault has the raw value
    payload_with_pii = store.get_session("s1", include_pii=True)
    assert any(p["field"] == "phone" for p in payload_with_pii["pii"])


def test_has_running_prod_detection(store):
    store.create_run("r_prod", "prod", 22)
    assert store.has_running_prod() is True
    store.finish_run("r_prod", "completed")
    assert store.has_running_prod() is False
