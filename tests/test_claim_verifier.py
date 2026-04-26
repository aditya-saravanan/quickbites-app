"""Tests for agent.claim_verifier — claim-vs-order discrepancy + scope ambiguity."""

from __future__ import annotations

from agent.claim_verifier import assess_scope, extract_item_claims, verify


def _line_items(*items):
    """Helper to build [{item_name, qty}] from tuples like ("Margherita", 1)."""
    return [{"item_name": name, "qty": qty} for name, qty in items]


# --- Item-claim extraction ---

def test_extract_picks_up_curry():
    assert "curry" in extract_item_claims("the curry was cold")


def test_extract_picks_up_pizza_and_burger():
    nouns = extract_item_claims("my pizza and burger were both wrong")
    assert "pizza" in nouns
    assert "burger" in nouns


def test_extract_returns_empty_for_generic_complaint():
    assert extract_item_claims("the food was cold and late") == []
    assert extract_item_claims("the rider was rude") == []


def test_extract_ignores_unknown_words():
    assert extract_item_claims("the soup was salty and the sauce was off") == ["soup"]


# --- verify(): claim-vs-order discrepancy ---

def test_discrepancy_when_curry_claimed_on_pizza_order():
    """Scenario 101 case: cold curry claimed on pizza+bread order → discrepancy."""
    items = _line_items(("Margherita", 1), ("Garlic Bread", 1))
    v = verify("My curry was cold and missing items", items)
    assert v.has_discrepancy is True
    assert "curry" in v.unmatched_items
    assert v.matched_items == []


def test_no_discrepancy_when_pizza_claimed_on_pizza_order():
    """Customer says 'pizza' and order has 'Margherita' (a pizza) → matched via alias."""
    items = _line_items(("Margherita", 1))
    v = verify("My pizza was cold", items)
    assert v.has_discrepancy is False
    assert "pizza" in v.matched_items


def test_no_discrepancy_for_generic_complaint_without_item_noun():
    items = _line_items(("Margherita", 1))
    v = verify("the food was cold", items)
    assert v.has_discrepancy is False
    assert v.matched_items == []
    assert v.unmatched_items == []


def test_no_discrepancy_when_rice_claimed_on_rice_order():
    items = _line_items(("Sticky Rice", 2), ("Pad Thai", 1))
    v = verify("the sticky rice was missing", items)
    assert v.has_discrepancy is False
    assert "rice" in v.matched_items


def test_partial_discrepancy_when_one_of_two_nouns_unmatched():
    items = _line_items(("Margherita", 1))
    v = verify("the pizza was cold and the curry was missing", items)
    assert v.has_discrepancy is True
    assert "pizza" in v.matched_items
    assert "curry" in v.unmatched_items


def test_line_items_summary_shape():
    items = _line_items(("Margherita", 1), ("Garlic Bread", 2))
    v = verify("the curry was cold", items)
    assert "Margherita" in v.line_items_summary
    assert "Garlic Bread" in v.line_items_summary


# --- assess_scope(): quantity ambiguity ---

def test_scope_clarification_fires_for_qty_2_without_quantity_hint():
    """Scenario 103 case: 'sticky rice missing' on order with 2× sticky rice."""
    items = _line_items(("Sticky Rice", 2), ("Pad Thai", 1))
    s = assess_scope("the sticky rice was missing", items)
    assert s.needs_quantity_confirm is True
    assert s.item_mentioned == "rice"
    assert s.matching_qty == 2


def test_scope_no_clarification_when_quantity_hint_present():
    items = _line_items(("Sticky Rice", 2))
    s = assess_scope("both rices were missing", items)
    assert s.needs_quantity_confirm is False


def test_scope_no_clarification_when_qty_is_one():
    items = _line_items(("Sticky Rice", 1))
    s = assess_scope("the rice was missing", items)
    assert s.needs_quantity_confirm is False


def test_scope_clarification_fires_when_multiple_line_items_match():
    items = _line_items(("Margherita", 1), ("Pepperoni", 1), ("Garlic Bread", 1))
    s = assess_scope("the pizza was cold", items)
    assert s.needs_quantity_confirm is True
    assert s.matching_count == 2


def test_scope_no_clarification_when_no_item_match():
    items = _line_items(("Margherita", 1))
    s = assess_scope("the curry was cold", items)
    assert s.needs_quantity_confirm is False


def test_scope_no_clarification_for_multiple_distinct_nouns():
    """Multiple nouns means the customer is being specific elsewhere."""
    items = _line_items(("Sticky Rice", 2), ("Pad Thai", 1))
    s = assess_scope("the rice and the noodles were both bad", items)
    assert s.needs_quantity_confirm is False


def test_scope_no_clarification_with_explicit_number():
    items = _line_items(("Sticky Rice", 2))
    s = assess_scope("one of my rices was missing", items)
    assert s.needs_quantity_confirm is False
