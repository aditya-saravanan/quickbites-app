"""Tests for the pre-ID hostile-signal detector in customer_signals."""

from __future__ import annotations

from agent.customer_signals import detect_session_signals


def _names(sigs):
    return {s["signal"] for s in sigs}


# --- Chargeback positives ---

def test_chargeback_threat_fires_on_chargeback():
    assert "chargeback_threat" in _names(
        detect_session_signals("I'll do a chargeback if you don't refund me")
    )


def test_chargeback_threat_fires_on_dispute_charge():
    assert "chargeback_threat" in _names(
        detect_session_signals("I'm going to dispute this charge with my bank")
    )


def test_chargeback_threat_fires_on_reverse_payment():
    assert "chargeback_threat" in _names(
        detect_session_signals("just reverse the payment then")
    )


# --- Chargeback negatives ---

def test_chargeback_does_not_fire_on_unrelated_dispute():
    # "dispute" outside payment context shouldn't fire.
    assert "chargeback_threat" not in _names(
        detect_session_signals("I dispute that the rider was rude")
    )


# --- Legal-threat positives ---

def test_legal_threat_fires_on_sue():
    assert "legal_threat" in _names(detect_session_signals("I'll sue you for this"))


def test_legal_threat_fires_on_lawyer():
    assert "legal_threat" in _names(
        detect_session_signals("My lawyer will be in touch")
    )


def test_legal_threat_fires_on_consumer_court():
    assert "legal_threat" in _names(
        detect_session_signals("I'll take this to consumer court")
    )


# --- Legal-threat negatives ---

def test_legal_threat_does_not_fire_on_lawful():
    assert "legal_threat" not in _names(detect_session_signals("This is unlawful"))


# --- Escalation-demand positives ---

def test_escalation_demand_fires_on_get_me_a_manager():
    assert "escalation_demand" in _names(
        detect_session_signals("get me a manager right now")
    )


def test_escalation_demand_fires_on_speak_to_human():
    assert "escalation_demand" in _names(
        detect_session_signals("I want to speak to a real person")
    )


# --- Negatives ---

def test_polite_message_fires_nothing():
    assert detect_session_signals("My order arrived cold, can I get a refund?") == []


def test_empty_message_returns_empty():
    assert detect_session_signals("") == []
    assert detect_session_signals("   ") == []


# --- Weight sanity ---

def test_chargeback_weight_is_lower_than_injection():
    """Frustration signals weight lighter than deliberate injection (0.30)."""
    sigs = detect_session_signals("I'll do a chargeback")
    assert sigs and sigs[0]["weight"] == 0.10


def test_legal_weight_is_lower_than_injection():
    sigs = detect_session_signals("my lawyer is calling")
    assert sigs and sigs[0]["weight"] == 0.10


def test_escalation_demand_weight_is_smallest():
    sigs = detect_session_signals("get me a manager")
    assert sigs and sigs[0]["weight"] == 0.05
