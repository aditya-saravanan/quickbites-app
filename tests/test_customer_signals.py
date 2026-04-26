"""Tests for customer_signals.detect_close_signal + classify_close_response."""

from __future__ import annotations

from agent.customer_signals import classify_close_response, detect_close_signal


# --- detect_close_signal ---

def test_close_token_anywhere_in_message():
    s = detect_close_signal("Sure, that works. CLOSE: thanks for the help.")
    assert s is not None
    assert s.kind == "close_token"
    assert s.confidence == 1.0


def test_close_token_at_start_of_line():
    s = detect_close_signal("CLOSE: all good")
    assert s is not None and s.kind == "close_token"


def test_winddown_short_thanks():
    s = detect_close_signal("Thanks!")
    assert s is not None and s.kind == "winddown"


def test_winddown_take_care():
    s = detect_close_signal("take care")
    assert s is not None and s.kind == "winddown"


def test_winddown_emoji_only():
    s = detect_close_signal("👍")
    assert s is not None and s.kind == "winddown"


def test_winddown_thats_all():
    s = detect_close_signal("That's all")
    assert s is not None and s.kind == "winddown"


def test_acceptance_phrase():
    s = detect_close_signal("okay that works for me")
    assert s is not None and s.kind == "acceptance"


def test_acceptance_with_negation_does_not_fire():
    # "but" after acceptance => still has objection
    assert detect_close_signal("okay that works but I want a bigger refund") is None


def test_negative_innocuous_long_message():
    long = (
        "I'm really not sure what to do here, the rider was a bit rude but I "
        "don't want to escalate, can you just look into it?"
    )
    assert detect_close_signal(long) is None


def test_ignore_the_dish_does_not_fire():
    # Critical negative: this is a substantive request, not wind-down.
    msg = "Please ignore the dish I got, I want a different one"
    assert detect_close_signal(msg) is None


def test_empty_message_treated_as_winddown():
    s = detect_close_signal("")
    assert s is not None and s.kind == "winddown"


# --- classify_close_response ---

def test_classify_yes():
    assert classify_close_response("yes please") == "affirmative"


def test_classify_no_nothing_else():
    assert classify_close_response("no nothing else") == "affirmative"


def test_classify_were_good():
    assert classify_close_response("we're good") == "affirmative"


def test_classify_explicit_close():
    assert classify_close_response("CLOSE") == "affirmative"


def test_classify_wait_actually():
    assert classify_close_response("wait, actually I have another question") == "negative"


def test_classify_one_more_thing():
    assert classify_close_response("one more thing — refund") == "negative"


def test_classify_negative_wins_when_both_present():
    # "yeah ... wait" — should be negative because customer is qualifying
    assert classify_close_response("yeah, but wait, one more thing") == "negative"


def test_classify_no_dont():
    assert classify_close_response("no don't close yet") == "negative"


def test_classify_ambiguous():
    assert classify_close_response("hmm I'm not sure") == "ambiguous"


def test_classify_substantive_complaint_is_ambiguous():
    # A new substantive complaint isn't a close-yes/no answer
    assert classify_close_response("the food was cold") == "ambiguous"


def test_classify_empty_is_ambiguous():
    assert classify_close_response("") == "ambiguous"
