"""Abuse-detector unit tests against synthetic customers."""

from __future__ import annotations

from agent.abuse import (
    AbuseResult,
    Signal,
    boost_with_session_signals,
    compute_abuse_score,
)


def _signal(result, name):
    for s in result.signals:
        if s.name == name:
            return s
    raise AssertionError(f"signal {name} not in result")


def test_clean_loyal_customer_does_not_fire(fixture_conn):
    r = compute_abuse_score(1, fixture_conn)
    assert r.score == 0.0
    assert r.fired is False
    for s in r.signals:
        if s.name != "claim_contradicts_data":
            assert s.fired is False, f"unexpected fire on {s.name}"


def test_mild_complainer_below_threshold(fixture_conn):
    r = compute_abuse_score(2, fixture_conn)
    # 2/8 = 0.25 — below 0.50 threshold
    assert _signal(r, "high_complaint_rate").fired is False
    assert r.fired is False


def test_refund_burster_fires_only_burst_signal(fixture_conn):
    r = compute_abuse_score(3, fixture_conn)
    assert _signal(r, "recent_refund_burst").fired is True
    assert _signal(r, "high_complaint_rate").fired is False
    assert r.score == 0.25
    assert r.fired is False  # below 0.60 fire threshold


def test_new_account_with_complaints(fixture_conn):
    r = compute_abuse_score(4, fixture_conn)
    sig = _signal(r, "new_account_with_complaints")
    assert sig.fired is True
    assert sig.value["age_days"] is not None and sig.value["age_days"] < 30
    assert _signal(r, "high_complaint_rate").fired is True  # 3/5 = 0.60


def test_rejection_repeater(fixture_conn):
    r = compute_abuse_score(5, fixture_conn)
    assert _signal(r, "repeat_rejection_history").fired is True
    # Customer 5 has 5/8 complaints rejected → high_complaint_rate also fires
    assert _signal(r, "high_complaint_rate").fired is True


def test_combined_abuser_fires_above_threshold(fixture_conn):
    r = compute_abuse_score(6, fixture_conn)
    fired_names = [s.name for s in r.signals if s.fired]
    assert "high_complaint_rate" in fired_names
    assert "recent_refund_burst" in fired_names
    assert "repeat_rejection_history" in fired_names
    assert r.score >= 0.60
    assert r.fired is True


def test_claim_contradicts_data_signal_external(fixture_conn):
    r1 = compute_abuse_score(1, fixture_conn, claim_contradicts_data=False)
    r2 = compute_abuse_score(1, fixture_conn, claim_contradicts_data=True)
    assert _signal(r1, "claim_contradicts_data").fired is False
    assert _signal(r2, "claim_contradicts_data").fired is True


def test_score_clamped_to_one(fixture_conn):
    r = compute_abuse_score(6, fixture_conn, claim_contradicts_data=True)
    assert 0.0 <= r.score <= 1.0


# --- boost_with_session_signals ---

def _bare_result(score: float = 0.0) -> AbuseResult:
    return AbuseResult(
        score=score,
        rule_version="v1",
        fire_threshold=0.6,
        fired=score >= 0.6,
        signals=[],
        customer_id=99,
    )


def test_boost_with_empty_signals_is_identity():
    base = _bare_result(0.4)
    out = boost_with_session_signals(base, [])
    assert out.score == 0.4
    assert out is base or out.signals == base.signals


def test_boost_adds_session_signal_weight():
    base = _bare_result(0.40)
    out = boost_with_session_signals(
        base,
        [{"signal": "injection_attempt_session", "weight": 0.30, "matched_text": "ignore prior"}],
    )
    assert abs(out.score - 0.70) < 1e-6
    assert out.fired is True
    fired = [s.name for s in out.signals if s.fired]
    assert "injection_attempt_session" in fired


def test_boost_caps_at_one():
    base = _bare_result(0.85)
    # Five frustration signals stack to 0.40 raw, but the SESSION_BOOST_CAP
    # of 0.30 limits how much they can add. 0.85 + 0.30 = 1.15 → clamped 1.0.
    out = boost_with_session_signals(
        base,
        [
            {"signal": "chargeback_threat", "weight": 0.10},
            {"signal": "legal_threat", "weight": 0.10},
            {"signal": "escalation_demand", "weight": 0.05},
            {"signal": "injection_attempt_session", "weight": 0.30},
        ],
    )
    assert out.score == 1.0
    assert out.fired is True


def test_boost_session_contribution_capped_at_cap_value():
    """Frustration signals can't compound past the cap. Three signals
    summing to 0.25 raw stay below 0.30 (the cap), so all contribute."""
    base = _bare_result(0.40)
    out = boost_with_session_signals(
        base,
        [
            {"signal": "chargeback_threat", "weight": 0.10},
            {"signal": "legal_threat", "weight": 0.10},
            {"signal": "escalation_demand", "weight": 0.05},
        ],
    )
    # 0.40 + 0.25 = 0.65 (under cap, so full sum applies)
    assert abs(out.score - 0.65) < 1e-6


def test_boost_session_contribution_capped_when_exceeded():
    """When raw session weights exceed the cap, only the cap is added."""
    base = _bare_result(0.40)
    out = boost_with_session_signals(
        base,
        [
            {"signal": "chargeback_threat", "weight": 0.10},
            {"signal": "legal_threat", "weight": 0.10},
            {"signal": "escalation_demand", "weight": 0.05},
            # Hypothetical extras pushing total > 0.30
            {"signal": "extra_signal_a", "weight": 0.10},
            {"signal": "extra_signal_b", "weight": 0.10},
        ],
    )
    # Raw total = 0.45, capped at 0.30 → 0.40 + 0.30 = 0.70.
    assert abs(out.score - 0.70) < 1e-6


def test_boost_chargeback_alone_does_not_fire_threshold():
    """Defect 7: scenario-103-style customer with DB-fired signals (0.45)
    and a chargeback threat alone should NOT auto-fire abuse threshold."""
    base = _bare_result(0.45)
    out = boost_with_session_signals(
        base,
        [{"signal": "chargeback_threat", "weight": 0.10}],
    )
    assert out.score < 0.6
    assert out.fired is False


def test_boost_injection_alone_still_fires_threshold():
    """Sanity: injection at full 0.30 weight + 0.45 base must still fire."""
    base = _bare_result(0.45)
    out = boost_with_session_signals(
        base,
        [{"signal": "injection_attempt_session", "weight": 0.30}],
    )
    assert out.score >= 0.6
    assert out.fired is True


def test_boost_skips_duplicate_signal_names():
    base = AbuseResult(
        score=0.30,
        rule_version="v1",
        fire_threshold=0.6,
        fired=False,
        signals=[Signal(name="chargeback_threat", weight=0.10, fired=True)],
        customer_id=99,
    )
    out = boost_with_session_signals(
        base, [{"signal": "chargeback_threat", "weight": 0.10}]
    )
    # No double-count: a duplicate-named session signal is ignored.
    assert out.score == 0.30


def test_boost_marks_session_source_in_signal_value():
    base = _bare_result(0.20)
    out = boost_with_session_signals(
        base, [{"signal": "legal_threat", "weight": 0.20, "matched_text": "I'll sue"}]
    )
    sess = next(s for s in out.signals if s.name == "legal_threat")
    assert sess.value.get("source") == "session"
    assert "sue" in sess.value.get("matched_text", "")
