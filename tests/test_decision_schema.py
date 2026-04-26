"""Decision schema + post-LLM validator tests."""

from __future__ import annotations

import pytest

from agent.decision_schema import Decision, validate_and_clamp
from agent.prefetch import PrefetchBundle


def _bundle(order_id: int, total: int) -> PrefetchBundle:
    b = PrefetchBundle(order_id=order_id, found=True)
    b.order = {"id": order_id, "total_inr": total, "customer_id": 1, "status": "delivered"}
    return b


def test_valid_decision_passes():
    d = Decision(
        response="Sorry, I've issued a small wallet credit.",
        actions=[
            {"type": "issue_refund", "order_id": 10, "amount_inr": 100, "method": "wallet_credit"},
            {"type": "close", "outcome_summary": "wallet credit applied"},
        ],
        reasoning="Cold food on a small order.",
        confidence=0.8,
    )
    cleaned, notes = validate_and_clamp(d, _bundle(10, 500))
    assert cleaned.actions[0]["amount_inr"] == 100
    assert cleaned.actions[-1]["type"] == "close"
    assert "validation_clamp" not in " ".join(notes)


def test_refund_exceeds_total_is_clamped():
    d = Decision(
        response="Refund applied.",
        actions=[
            {"type": "issue_refund", "order_id": 10, "amount_inr": 1000, "method": "cash"},
        ],
        reasoning="Total refund.",
        confidence=0.6,
    )
    cleaned, notes = validate_and_clamp(d, _bundle(10, 500))
    assert cleaned.actions[0]["amount_inr"] == 500
    assert any("validation_clamp" in n for n in notes)


def test_multiple_refunds_summed_against_total():
    d = Decision(
        response="Refund applied.",
        actions=[
            {"type": "issue_refund", "order_id": 10, "amount_inr": 300, "method": "cash"},
            {"type": "issue_refund", "order_id": 10, "amount_inr": 300, "method": "wallet_credit"},
        ],
        reasoning="Split refund.",
        confidence=0.6,
    )
    cleaned, notes = validate_and_clamp(d, _bundle(10, 500))
    total = sum(a["amount_inr"] for a in cleaned.actions if a["type"] == "issue_refund")
    assert total == 500
    assert any("validation_clamp" in n for n in notes)


def test_flag_abuse_dropped_when_score_low():
    d = Decision(
        response="Filing this for review.",
        actions=[{"type": "flag_abuse", "reason": "high refund frequency observed"}],
        reasoning="Abuse score 0.30.",
        confidence=0.5,
        abuse_score_used=0.30,
    )
    cleaned, notes = validate_and_clamp(d, _bundle(10, 500))
    assert all(a["type"] != "flag_abuse" for a in cleaned.actions)
    assert any("validation_dropped_flag" in n for n in notes)


def test_flag_abuse_kept_when_score_high():
    d = Decision(
        response="Filing this for review.",
        actions=[{"type": "flag_abuse", "reason": "abuse score 0.70 with 3 fired signals"}],
        reasoning="Score 0.70.",
        confidence=0.7,
        abuse_score_used=0.70,
    )
    cleaned, _ = validate_and_clamp(d, _bundle(10, 500))
    assert any(a["type"] == "flag_abuse" for a in cleaned.actions)


def test_response_phone_email_redacted():
    d = Decision(
        response="Reach me at +91-9876543210 or test@example.com",
        actions=[],
        reasoning="redact me",
        confidence=0.5,
    )
    cleaned, notes = validate_and_clamp(d, _bundle(10, 500))
    assert "REDACTED-PHONE" in cleaned.response
    assert "REDACTED-EMAIL" in cleaned.response
    assert any("pii_phone" in n for n in notes)
    assert any("pii_email" in n for n in notes)


def test_unknown_action_type_dropped():
    d = Decision(
        response="x",
        actions=[{"type": "issue_credit", "amount_inr": 100}],
        reasoning="bogus",
        confidence=0.5,
    )
    assert d.actions == []


def test_escalate_missing_reason_backfilled():
    d = Decision(
        response="ok",
        actions=[{"type": "escalate_to_human"}],
        reasoning="x",
        confidence=0.5,
    )
    assert d.actions[0]["reason"]
    assert len(d.actions[0]["reason"]) >= 10


def test_close_runs_last():
    d = Decision(
        response="Done.",
        actions=[
            {"type": "close", "outcome_summary": "wrap up"},
            {"type": "issue_refund", "order_id": 10, "amount_inr": 100, "method": "wallet_credit"},
        ],
        reasoning="ordering check",
        confidence=0.7,
    )
    cleaned, _ = validate_and_clamp(d, _bundle(10, 500))
    assert cleaned.actions[-1]["type"] == "close"
