"""Claim-vs-order verification + scope-clarification heuristics.

Pure functions, no LLM dependency. Two responsibilities:

  - `verify(message, line_items)` returns a `ClaimVerification` describing
    whether nouns the customer mentioned exist in the order's line items.
  - `assess_scope(message, line_items)` returns a `ScopeClarification`
    describing whether the customer's claim is item-specific but
    quantity-ambiguous (e.g., 2× sticky rice on order, customer just says
    "the rice was missing" — was it 1 or both?).

The orchestrator stores the result on session state; the validator
strips premature actions; the prompt-assembly layer injects a
force-confirm directive into block 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Customer-message food nouns we care about. Each maps to a list of
# *aliases* that may appear in line_item names. The customer might say
# "pizza" while the order says "Margherita" — those still match.
_CATEGORY_ALIASES: dict[str, list[str]] = {
    "pizza": ["pizza", "margherita", "pepperoni", "farmhouse", "hawaiian"],
    "burger": ["burger", "patty"],
    "curry": ["curry", "korma", "makhani", "rogan josh", "tikka masala", "butter chicken"],
    "biryani": ["biryani"],
    "rice": ["rice"],
    "bread": ["bread", "naan", "roti", "paratha", "croissant"],
    "noodles": ["noodles", "pad thai"],
    "pasta": ["pasta", "alfredo"],
    "salad": ["salad"],
    "soup": ["soup", "tom yum"],
    "drink": ["coffee", "lassi", "tea", "shake", "juice", "drink", "raita"],
    "dessert": ["cake", "dessert", "ice cream", "puff"],
    "kebab": ["kebab", "seekh"],
    "paneer": ["paneer"],
    "chicken": ["chicken"],
    "fries": ["fries"],
    "dal": ["dal"],
}

# Customer-claim noun → look here. Built from the keys + the unique names
# that we want to recognize directly (so e.g. "biryani" is detected as a
# claim noun even though the customer might say "the biryani was cold").
_CLAIM_NOUN_RE = re.compile(
    r"\b("
    + "|".join(sorted(_CATEGORY_ALIASES.keys(), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

# Quantity words the customer may use to disambiguate scope. If any of
# these are present alongside an item noun, scope is considered already
# specified — no clarification needed.
_QUANTITY_HINT_RE = re.compile(
    r"\b(?:both|all|every|either|each|"
    r"one|two|three|four|five|six|seven|eight|"
    r"a\s+couple|a\s+few|"
    r"\d+)\b",
    re.IGNORECASE,
)


@dataclass
class ClaimVerification:
    matched_items: list[str] = field(default_factory=list)
    unmatched_items: list[str] = field(default_factory=list)
    has_discrepancy: bool = False
    line_items_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_items": self.matched_items,
            "unmatched_items": self.unmatched_items,
            "has_discrepancy": self.has_discrepancy,
            "line_items_summary": self.line_items_summary,
        }


@dataclass
class ScopeClarification:
    item_mentioned: str = ""
    matching_qty: int = 0
    matching_count: int = 0
    needs_quantity_confirm: bool = False
    line_items_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_mentioned": self.item_mentioned,
            "matching_qty": self.matching_qty,
            "matching_count": self.matching_count,
            "needs_quantity_confirm": self.needs_quantity_confirm,
            "line_items_summary": self.line_items_summary,
        }


def extract_item_claims(message: str) -> list[str]:
    """Return distinct claim-noun categories present in the message."""
    if not message:
        return []
    found = {m.group(1).lower() for m in _CLAIM_NOUN_RE.finditer(message)}
    return sorted(found)


def _line_item_matches_noun(line_item_name: str, noun: str) -> bool:
    name_lower = line_item_name.lower()
    for alias in _CATEGORY_ALIASES.get(noun, [noun]):
        if alias in name_lower:
            return True
    return False


def _line_items_summary(line_items: list[dict[str, Any]]) -> str:
    if not line_items:
        return "(empty)"
    parts: list[str] = []
    for li in line_items:
        name = li.get("item_name") or li.get("name") or "?"
        qty = li.get("qty") or li.get("quantity") or 1
        parts.append(f"{qty}× {name}" if qty != 1 else str(name))
    return ", ".join(parts)


def verify(
    message: str, line_items: list[dict[str, Any]] | None
) -> ClaimVerification:
    """Compare claim nouns against line items.

    A discrepancy fires only when the customer named a specific food
    category that has no representation at all in the order. Generic
    complaints with no item nouns ("the food was cold", "late delivery")
    return has_discrepancy=False.
    """
    return verify_nouns(extract_item_claims(message), line_items)


def verify_nouns(
    nouns: list[str], line_items: list[dict[str, Any]] | None
) -> ClaimVerification:
    """Compare a pre-extracted list of claim nouns against line items.

    Use this when claims accumulated across multiple turns need to be
    verified together (e.g., the customer mentioned "curry" before the
    order_id was known and thus before prefetch existed).
    """
    line_items = line_items or []
    summary = _line_items_summary(line_items)
    if not nouns:
        return ClaimVerification(line_items_summary=summary)

    matched: list[str] = []
    unmatched: list[str] = []
    for noun in nouns:
        if any(_line_item_matches_noun(li.get("item_name", ""), noun) for li in line_items):
            matched.append(noun)
        else:
            unmatched.append(noun)
    return ClaimVerification(
        matched_items=matched,
        unmatched_items=unmatched,
        has_discrepancy=bool(unmatched),
        line_items_summary=summary,
    )


def assess_scope(
    message: str, line_items: list[dict[str, Any]] | None
) -> ScopeClarification:
    """Decide whether the customer's complaint is quantity-ambiguous.

    Triggers when:
      - exactly one claim noun is present in the message,
      - that noun matches at least one line item,
      - matching item(s) have qty > 1 OR multiple line items match the noun,
      - and the customer did NOT specify a quantity word ("both", "all",
        "one", a number) elsewhere in the message.
    """
    line_items = line_items or []
    summary = _line_items_summary(line_items)
    nouns = extract_item_claims(message)
    if len(nouns) != 1:
        return ScopeClarification(line_items_summary=summary)

    noun = nouns[0]
    matches = [li for li in line_items if _line_item_matches_noun(li.get("item_name", ""), noun)]
    if not matches:
        return ScopeClarification(item_mentioned=noun, line_items_summary=summary)

    total_qty = sum(int(li.get("qty") or 1) for li in matches)
    matching_count = len(matches)
    has_quantity_hint = bool(_QUANTITY_HINT_RE.search(message))

    needs = (total_qty > 1 or matching_count > 1) and not has_quantity_hint

    return ScopeClarification(
        item_mentioned=noun,
        matching_qty=total_qty,
        matching_count=matching_count,
        needs_quantity_confirm=needs,
        line_items_summary=summary,
    )
