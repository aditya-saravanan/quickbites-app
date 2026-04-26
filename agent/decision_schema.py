"""Pydantic schema for the submit_decision tool + post-LLM validators.

The validators are the floor for policy enforcement: they clamp refunds,
drop unsupported abuse flags, and scrub PII regardless of what the model
emitted. They're the last line of defense against prompt injection.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from . import disclosure_filter
from .prefetch import PrefetchBundle


class IssueRefundAction(BaseModel):
    type: Literal["issue_refund"]
    order_id: int
    amount_inr: int = Field(ge=1)
    method: Literal["cash", "wallet_credit"]


class FileComplaintAction(BaseModel):
    type: Literal["file_complaint"]
    order_id: int
    target_type: Literal["restaurant", "rider", "app"]


class EscalateAction(BaseModel):
    type: Literal["escalate_to_human"]
    reason: str = Field(min_length=10, max_length=500)


class FlagAbuseAction(BaseModel):
    type: Literal["flag_abuse"]
    reason: str = Field(min_length=10, max_length=500)


class CloseAction(BaseModel):
    type: Literal["close"]
    outcome_summary: str = Field(min_length=5, max_length=500)


Action = IssueRefundAction | FileComplaintAction | EscalateAction | FlagAbuseAction | CloseAction


class Decision(BaseModel):
    response: str = Field(min_length=1, max_length=1500)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str = Field(min_length=1, max_length=3000)
    confidence: float = Field(ge=0.0, le=1.0)
    abuse_score_used: float | None = Field(default=None, ge=0.0, le=1.0)
    close_intent: Literal["continue", "ask_user_to_close", "emit_close_now"] = "continue"

    @field_validator("actions")
    @classmethod
    def validate_action_shapes(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for a in v:
            atype = a.get("type")
            if atype == "issue_refund":
                a = dict(a)
                if "method" not in a:
                    a["method"] = "wallet_credit"
                # The model sometimes emits the customer's payment instrument
                # ('upi', 'card', 'netbanking') as the method. Refunds to the
                # original payment instrument map to 'cash' in our schema; an
                # ambiguous credit goes to wallet_credit.
                method_alias = {
                    "upi": "cash",
                    "card": "cash",
                    "credit_card": "cash",
                    "debit_card": "cash",
                    "netbanking": "cash",
                    "bank_transfer": "cash",
                    "original_payment_method": "cash",
                    "original": "cash",
                    "wallet": "wallet_credit",
                    "credit": "wallet_credit",
                }
                m_lower = str(a["method"]).strip().lower()
                if m_lower in method_alias:
                    a["method"] = method_alias[m_lower]
                # If amount_inr is missing/zero, we can't safely guess — drop it.
                if not a.get("amount_inr"):
                    normalized.append({"type": "_dropped_unknown", "original": a})
                    continue
                # order_id can be backfilled later in validate_and_clamp from prefetch.
                if "order_id" not in a:
                    a["order_id"] = -1  # placeholder; validate_and_clamp will resolve
                IssueRefundAction(**a)
                normalized.append(a)
            elif atype == "file_complaint":
                a = dict(a)
                if "target_type" not in a:
                    # Mark for later inference in validate_and_clamp where we
                    # have access to reasoning + customer history.
                    a["target_type"] = "_infer_"
                if "order_id" not in a:
                    a["order_id"] = -1
                # Skip the strict pydantic validation when target_type=_infer_
                if a["target_type"] != "_infer_":
                    FileComplaintAction(**a)
                normalized.append(a)
            elif atype == "escalate_to_human":
                # Backfill missing/short reason rather than failing the whole decision.
                a = dict(a)
                reason = (a.get("reason") or "").strip()
                if len(reason) < 10:
                    a["reason"] = reason or "agent_requested_escalation_without_specific_reason"
                EscalateAction(**a)
                normalized.append(a)
            elif atype == "flag_abuse":
                a = dict(a)
                reason = (a.get("reason") or "").strip()
                if len(reason) < 10:
                    a["reason"] = reason or "abuse_pattern_observed_no_specific_reason_given"
                FlagAbuseAction(**a)
                normalized.append(a)
            elif atype == "close":
                a = dict(a)
                summary = (a.get("outcome_summary") or "").strip()
                if len(summary) < 5:
                    # Closing the session is destructive; if the model didn't
                    # commit to a real summary, drop the close rather than
                    # ending the conversation prematurely.
                    normalized.append({"type": "_dropped_unknown", "original": a})
                    continue
                CloseAction(**a)
                normalized.append(a)
            else:
                # Unknown action type: drop with a marker rather than crash.
                normalized.append({"type": "_dropped_unknown", "original": a})
        return [a for a in normalized if a.get("type") != "_dropped_unknown"]


_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Strip emoji + miscellaneous pictographs from user-facing replies. The
# system prompt forbids these too, but a regex floor enforces it even
# when the model slips. Standard punctuation is unaffected.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
    "\U0001FA70-\U0001FAFF"  # symbols & pictographs extended-A
    "\U00002600-\U000027BF"  # misc symbols + dingbats (✅, ❤, ☎)
    "\U0001F000-\U0001F2FF"  # mahjong, domino, playing cards, enclosed letters
    "]+",
    flags=re.UNICODE,
)
_DOUBLE_SPACE_RE = re.compile(r"  +")


_RIDER_RE = re.compile(r"\brider\b|\bdelivery\s+person\b", re.IGNORECASE)
_RESTAURANT_RE = re.compile(
    r"\brestaurant\b|\bkitchen\b|\bcook(?:ed|ing)?\b|"
    r"\bfood\s+(?:was|quality|cold|hot|wrong|missing|bad|good)\b|"
    r"\bcold\s+(?:food|curry|pizza|biryani|rice)\b|"
    r"\bmissing\s+items?\b|\bwrong\s+items?\b|\bsoggy\b",
    re.IGNORECASE,
)
_APP_RE = re.compile(
    r"\bapp\b|\bdouble[-\s]?charge\b|\bpromo\b|\bpayment\b|\bcoupon\b",
    re.IGNORECASE,
)


def _infer_complaint_target(reasoning: str, hint_text: str = "") -> str:
    """Infer target_type from the bot's reasoning + any conversation hint.

    Returns one of: 'rider', 'restaurant', 'app'. Falls back to 'app'.
    """
    blob = (reasoning or "") + "\n" + (hint_text or "")
    if _RIDER_RE.search(blob):
        return "rider"
    if _RESTAURANT_RE.search(blob):
        return "restaurant"
    if _APP_RE.search(blob):
        return "app"
    return "app"


def validate_and_clamp(
    decision: Decision,
    prefetch: PrefetchBundle,
    *,
    close_state: str = "normal",
    cooldown_remaining: int = 0,
    last_customer_message: str = "",
    claim_discrepancy_active: bool = False,
    scope_clarification_active: bool = False,
    abuse_fired: bool = False,
    abuse_fired_signals: list[str] | None = None,
) -> tuple[Decision, list[str]]:
    """Apply policy floors. Returns (mutated_decision, validation_notes).

    Mutations:
      - refund > order total -> clamp to total, append validation_clamp note
      - flag_abuse with score < 0.6 -> drop, append validation_dropped_flag
      - escalate_to_human with reason < 10 chars -> already rejected by Pydantic
      - close with refund/complaint -> allowed; close runs last
      - PII patterns in response -> redact
    """
    notes: list[str] = []
    actions = list(decision.actions)
    cleaned: list[dict[str, Any]] = []
    close_action: dict[str, Any] | None = None

    primary_oid = prefetch.order_id if prefetch.order_id is not None else None
    for a in actions:
        if a.get("order_id") == -1:
            if primary_oid is not None:
                a["order_id"] = primary_oid
                notes.append(f"backfilled_order_id_to_{primary_oid}_for_{a['type']}")
            else:
                notes.append(f"dropped_{a['type']}_missing_order_id_no_prefetch")
                a["type"] = "_dropped_unknown"
        if a.get("type") == "file_complaint" and a.get("target_type") == "_infer_":
            inferred = _infer_complaint_target(
                decision.reasoning or "",
                last_customer_message or "",
            )
            a["target_type"] = inferred
            notes.append(f"inferred_complaint_target_to_{inferred}")
    actions = [a for a in actions if a.get("type") != "_dropped_unknown"]

    order_totals = _build_order_total_map(prefetch)

    refund_sums: dict[int, int] = {}
    for a in actions:
        if a["type"] == "issue_refund":
            refund_sums[a["order_id"]] = refund_sums.get(a["order_id"], 0) + int(a["amount_inr"])

    refund_caps: dict[int, int] = {}
    for oid, requested in refund_sums.items():
        total = order_totals.get(oid)
        if total is None:
            refund_caps[oid] = 0
            notes.append(f"unknown_order_for_refund:{oid}")
        elif requested > total:
            refund_caps[oid] = total
            notes.append(f"validation_clamp:order_{oid}:requested_{requested}_clamped_to_{total}")
        else:
            refund_caps[oid] = requested

    refund_applied: dict[int, int] = {oid: 0 for oid in refund_sums}
    for a in actions:
        atype = a["type"]
        if atype == "close":
            close_action = a
            continue
        if atype == "flag_abuse":
            score = decision.abuse_score_used or 0.0
            if score < 0.6:
                notes.append(f"validation_dropped_flag:abuse_score_{score:.2f}_below_threshold")
                continue
        if atype == "issue_refund":
            oid = a["order_id"]
            cap = refund_caps.get(oid, 0)
            if cap == 0:
                continue
            remaining = cap - refund_applied[oid]
            if remaining <= 0:
                continue
            requested = int(a["amount_inr"])
            allowed = min(requested, remaining)
            new_a = dict(a)
            new_a["amount_inr"] = allowed
            refund_applied[oid] += allowed
            cleaned.append(new_a)
            continue
        cleaned.append(a)

    if close_action is not None:
        cleaned.append(close_action)

    response = decision.response
    if _PHONE_RE.search(response):
        response = _PHONE_RE.sub("[REDACTED-PHONE]", response)
        notes.append("validation_pii_phone_scrubbed")
    if _EMAIL_RE.search(response):
        response = _EMAIL_RE.sub("[REDACTED-EMAIL]", response)
        notes.append("validation_pii_email_scrubbed")

    # Pre-clarification action strip. Claim discrepancy beats scope —
    # asking the customer to clarify items is more fundamental than
    # asking quantity within an item.
    if claim_discrepancy_active:
        before = len(cleaned)
        cleaned = [
            a for a in cleaned
            if a.get("type") not in ("issue_refund", "file_complaint")
        ]
        if len(cleaned) != before:
            notes.append("validation_dropped_premature_action_pre_clarification")
    elif scope_clarification_active:
        before = len(cleaned)
        cleaned = [a for a in cleaned if a.get("type") != "issue_refund"]
        if len(cleaned) != before:
            notes.append("validation_dropped_premature_refund_pre_scope")

    # Boosted abuse-score override. When the score has crossed the fire
    # threshold (DB rules + session signals combined), refunds are
    # blocked unconditionally and an escalate_to_human is forced — the
    # model's prompt instructs the same, but we don't rely on a single
    # decision being correct.
    if abuse_fired:
        before = len(cleaned)
        cleaned = [a for a in cleaned if a.get("type") != "issue_refund"]
        if len(cleaned) != before:
            notes.append("validation_refund_blocked_by_session_abuse")
        if not any(a.get("type") == "escalate_to_human" for a in cleaned):
            sigs = ",".join(abuse_fired_signals or []) or "abuse_fired"
            cleaned.append(
                {
                    "type": "escalate_to_human",
                    "reason": f"abuse_score_fired:{sigs}",
                }
            )
            notes.append("validation_injected_escalate_for_abuse")

    # State-machine override: strip premature close, inject required close.
    if close_state in ("awaiting_v1", "awaiting_v2"):
        before_strip = len(cleaned)
        cleaned = [a for a in cleaned if a.get("type") != "close"]
        if len(cleaned) != before_strip:
            notes.append(f"validation_dropped_premature_close:state_{close_state}")
    elif close_state == "emit_close":
        if not any(a.get("type") == "close" for a in cleaned):
            summary = response[:80].strip().replace("\n", " ") or "user_confirmed_close"
            cleaned.append({"type": "close", "outcome_summary": summary})
            notes.append("validation_injected_close:state_emit_close")

    # Confidentiality scrubber: rewrite + auto-coerce escalate if needed.
    scrubbed_text, fired_tags, mutated_actions = disclosure_filter.scrub_response(
        response, cleaned
    )
    if fired_tags:
        notes.append("validation_disclosure_scrubbed:" + ",".join(fired_tags))
        response = scrubbed_text
        cleaned = mutated_actions

    # Final emoji strip — defense in depth against the no-emoji rule.
    new_response = _EMOJI_RE.sub("", response)
    if new_response != response:
        new_response = _DOUBLE_SPACE_RE.sub(" ", new_response).strip()
        if new_response:
            notes.append("validation_emoji_stripped")
            response = new_response

    final_close_intent = decision.close_intent
    # Sync close_intent with the state-machine outcome so audit reflects truth.
    if close_state == "emit_close":
        final_close_intent = "emit_close_now"
    elif close_state in ("awaiting_v1", "awaiting_v2"):
        final_close_intent = "ask_user_to_close"

    return Decision(
        response=response,
        actions=cleaned,
        reasoning=decision.reasoning,
        confidence=decision.confidence,
        abuse_score_used=decision.abuse_score_used,
        close_intent=final_close_intent,
    ), notes


def _build_order_total_map(prefetch: PrefetchBundle) -> dict[int, int]:
    out: dict[int, int] = {}
    if prefetch.order:
        out[int(prefetch.order["id"])] = int(prefetch.order["total_inr"])
    for oid, sec in prefetch.secondary_orders.items():
        if sec.get("found") and sec.get("order"):
            out[int(oid)] = int(sec["order"]["total_inr"])
    return out
