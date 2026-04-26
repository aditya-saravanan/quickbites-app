"""Tests for disclosure_filter — the user-facing confidentiality scrubber."""

from __future__ import annotations

from agent.disclosure_filter import (
    compose_safe_response,
    scan_disclosure,
    scrub_response,
)


# --- Per-tag positives ---

def test_cites_complaint_history_fires_on_high_number():
    tags = scan_disclosure("you have a high number of complaints")
    assert "cites_complaint_history" in tags


def test_cites_complaint_history_fires_on_past_complaints():
    tags = scan_disclosure("Looking at your past complaints")
    assert "cites_complaint_history" in tags


def test_cites_refund_history_fires():
    tags = scan_disclosure("we noticed your recent refunds")
    assert "cites_refund_history" in tags


def test_cites_account_age_fires():
    assert "cites_account_age" in scan_disclosure("your new account")
    assert "cites_account_age" in scan_disclosure("your account is new")


def test_cites_abuse_score_fires():
    assert "cites_abuse_score" in scan_disclosure("you're flagged on our records")
    assert "cites_abuse_score" in scan_disclosure("you're on our system as a risk")


def test_cites_rider_history_fires():
    assert "cites_rider_history" in scan_disclosure(
        "the rider has a clean record on these orders"
    )


def test_cites_restaurant_history_fires():
    assert "cites_restaurant_history" in scan_disclosure(
        "Pizza Spice has had similar complaints from other customers"
    )
    assert "cites_restaurant_history" in scan_disclosure(
        "the restaurant's rating is on the lower end"
    )


def test_cites_internal_score_fires():
    assert "cites_internal_score" in scan_disclosure("our policy states this clearly")
    assert "cites_internal_score" in scan_disclosure("per our guidelines")


# --- Critical negatives that must NOT fire ---

def test_confirming_order_does_not_fire_restaurant_tag():
    assert scan_disclosure("I can see your order from Pizza Spice") == []


def test_empathy_phrasing_does_not_fire():
    assert scan_disclosure("I'm sorry the food was cold") == []
    assert scan_disclosure("That sounds really frustrating") == []


def test_announcing_complaint_action_does_not_fire():
    assert scan_disclosure("I've filed a complaint against the rider") == []


def test_confirming_refund_action_does_not_fire():
    assert scan_disclosure("I've issued a ₹200 wallet credit") == []


def test_normal_resolution_text_clean():
    msg = (
        "I'm sorry the order arrived cold. I've issued a ₹500 wallet credit "
        "and filed a complaint with the restaurant. You'll see the credit "
        "in your account within an hour."
    )
    assert scan_disclosure(msg) == []


# --- End-to-end scrub_response ---

def test_clean_text_passes_through_unchanged():
    text = "I'm so sorry about this. Here's a wallet credit of ₹200."
    actions = [{"type": "issue_refund", "order_id": 1, "amount_inr": 200, "method": "wallet_credit"}]
    out, fired, mutated = scrub_response(text, actions)
    assert out == text
    assert fired == []
    assert mutated == actions


def test_disclosure_with_refund_announces_amount_not_followup():
    """Defect 3: refund action present but disclosure tag fires → composed
    response MUST mention the refund amount, NOT 'colleague will follow up'."""
    text = "Given your past complaints, I'm crediting you ₹820"
    actions = [
        {
            "type": "issue_refund",
            "order_id": 10,
            "amount_inr": 820,
            "method": "wallet_credit",
        }
    ]
    out, fired, mutated = scrub_response(text, actions)
    assert "820" in out
    assert "complaint" not in out.lower() or "logged a complaint" in out.lower()
    assert "colleague" not in out.lower()
    assert "follow up" not in out.lower() or "follow up with you" in out.lower()
    assert "cites_complaint_history" in fired
    # Refund preserved.
    assert any(a["type"] == "issue_refund" and a["amount_inr"] == 820 for a in mutated)


def test_disclosure_with_refund_and_complaint_announces_both():
    text = "Your refund frequency is concerning but I'll credit you and file a complaint"
    actions = [
        {"type": "issue_refund", "order_id": 10, "amount_inr": 500, "method": "wallet_credit"},
        {"type": "file_complaint", "order_id": 10, "target_type": "restaurant"},
    ]
    out, fired, _ = scrub_response(text, actions)
    assert "500" in out
    assert "restaurant" in out.lower()
    assert "cites_refund_history" in fired


def test_disclosure_with_complaint_only():
    text = "Per our policy, I'm filing a complaint against the rider"
    actions = [{"type": "file_complaint", "order_id": 10, "target_type": "rider"}]
    out, fired, _ = scrub_response(text, actions)
    assert "rider" in out.lower()
    assert "cites_internal_score" in fired


def test_disclosure_with_escalate_only():
    text = "Looking at your past complaints, I'm escalating this for review"
    actions = [{"type": "escalate_to_human", "reason": "abuse pattern observed"}]
    out, fired, mutated = scrub_response(text, actions)
    assert "escalat" in out.lower() or "senior agent" in out.lower()
    assert "complaint" not in out.lower()
    assert "cites_complaint_history" in fired
    assert any(a["type"] == "escalate_to_human" for a in mutated)


def test_disclosure_with_no_action_auto_coerces_escalate():
    text = "Given the high number of complaints on your account, I can't refund"
    actions: list = []
    out, fired, mutated = scrub_response(text, actions)
    assert "complaints" not in out.lower()
    assert any(a["type"] == "escalate_to_human" for a in mutated)


def test_disclosure_with_close_uses_close_template():
    text = "Per our policy, your refund history doesn't qualify. Closing this."
    actions = [{"type": "close", "outcome_summary": "refused refund"}]
    out, fired, mutated = scrub_response(text, actions)
    assert "closing" in out.lower() or "close" in out.lower()
    assert any(a["type"] == "close" for a in mutated)


def test_multiple_tags_fire():
    text = "Per our policy, your past complaints and recent refunds put you on our records"
    tags = scan_disclosure(text)
    assert "cites_complaint_history" in tags
    assert "cites_refund_history" in tags
    assert "cites_internal_score" in tags
    assert "cites_abuse_score" in tags


# --- compose_safe_response unit tests ---

def test_compose_refund_only_includes_amount():
    out, _ = compose_safe_response(
        [{"type": "issue_refund", "amount_inr": 250, "method": "wallet_credit"}],
        ["cites_complaint_history"],
    )
    assert "250" in out
    assert "wallet credit" in out.lower()


def test_compose_refund_method_cash_uses_payment_method_phrase():
    out, _ = compose_safe_response(
        [{"type": "issue_refund", "amount_inr": 500, "method": "cash"}],
        ["cites_complaint_history"],
    )
    assert "500" in out
    assert "original payment" in out.lower()


def test_compose_no_action_auto_coerces_escalate():
    out, mutated = compose_safe_response([], ["cites_abuse_score"])
    assert any(a["type"] == "escalate_to_human" for a in mutated)
    assert "escalate" in out.lower() or "senior agent" in out.lower()
