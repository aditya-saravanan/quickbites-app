"""Tests for the emoji strip in agent.decision_schema."""

from __future__ import annotations

from agent.decision_schema import Decision, validate_and_clamp
from agent.prefetch import PrefetchBundle


def _decision(response: str, **overrides) -> Decision:
    return Decision(
        response=response,
        actions=overrides.get("actions", []),
        reasoning=overrides.get("reasoning", "test reasoning here"),
        confidence=overrides.get("confidence", 0.8),
    )


def _empty_prefetch() -> PrefetchBundle:
    return PrefetchBundle.empty(missing_reason="test")


def test_strips_thumbs_up_emoji():
    d = _decision("Thanks for the patience! 👍")
    out, notes = validate_and_clamp(d, _empty_prefetch())
    assert "👍" not in out.response
    assert "validation_emoji_stripped" in notes


def test_strips_multiple_emoji():
    d = _decision("All sorted 🙏 — refund issued ✅ 😊")
    out, notes = validate_and_clamp(d, _empty_prefetch())
    assert "🙏" not in out.response
    assert "✅" not in out.response
    assert "😊" not in out.response
    assert "validation_emoji_stripped" in notes


def test_keeps_punctuation_intact():
    d = _decision("Sorry — that's frustrating! Let me help :)")
    out, notes = validate_and_clamp(d, _empty_prefetch())
    assert out.response == "Sorry — that's frustrating! Let me help :)"
    assert "validation_emoji_stripped" not in notes


def test_keeps_rupee_symbol_intact():
    """₹ is a currency symbol, not an emoji — must survive the strip."""
    d = _decision("I've credited ₹500 to your wallet.")
    out, notes = validate_and_clamp(d, _empty_prefetch())
    assert "₹500" in out.response
    assert "validation_emoji_stripped" not in notes


def test_keeps_ellipsis_intact():
    d = _decision("Looking into this...")
    out, notes = validate_and_clamp(d, _empty_prefetch())
    assert out.response == "Looking into this..."


def test_collapses_double_spaces_after_strip():
    d = _decision("Refund 🙏 issued.")
    out, _ = validate_and_clamp(d, _empty_prefetch())
    assert "  " not in out.response
    assert "Refund issued." == out.response or "Refund issued" in out.response
