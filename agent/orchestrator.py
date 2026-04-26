"""Per-turn agent loop: customer message in → bot reply + actions out.

Wraps the LLM tool-use loop, dispatches tool calls, validates the final
decision, and returns an audit record for durable storage.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import abuse, claim_verifier, customer_signals, injection_filter, prefetch as prefetch_mod, prompts
from .decision_schema import Decision, validate_and_clamp
from .llm_client import LLMClient, LLMUsage
from .session_state import SessionState
from .settings import POLICY_FILE
from .tools.definitions import TOOL_DEFINITIONS
from .tools.dispatch import dispatch


MAX_TOOL_HOPS = 8
COOLDOWN_TURNS_AFTER_BACKOFF = 2
FAIL_SAFE_REPLY = (
    "I'm having trouble accessing your information right now. Let me get a "
    "team member to help you — please hold for a moment."
)

# Tags from injection_filter that indicate hostile intent (not just clumsy
# customer phrasing). These get folded into the session abuse signal once
# the customer is identified.
_HOSTILE_INJECTION_TAGS = {
    "ignore_prior",
    "role_injection",
    "role_override",
    "policy_override",
    "system_override",
    "reveal_instructions",
    "large_refund_demand",
}


def _accumulate_session_signals(
    state: SessionState, customer_message: str, injection_tags: list[str]
) -> None:
    """Append new pre-ID hostile signals to state.session_signals (idempotent)."""
    seen = {s["signal"] for s in state.session_signals}

    # Hostile injection tags become a single session-level signal,
    # weight 0.30. Once fired it doesn't re-add on subsequent turns.
    if "injection_attempt_session" not in seen:
        if any(t in _HOSTILE_INJECTION_TAGS for t in injection_tags):
            state.session_signals.append(
                {
                    "signal": "injection_attempt_session",
                    "weight": 0.30,
                    "matched_text": ",".join(t for t in injection_tags if t in _HOSTILE_INJECTION_TAGS),
                    "turn": state.turn_number,
                }
            )
            seen.add("injection_attempt_session")

    for sig in customer_signals.detect_session_signals(customer_message):
        if sig["signal"] in seen:
            continue
        sig["turn"] = state.turn_number
        state.session_signals.append(sig)
        seen.add(sig["signal"])


def _apply_abuse_boost(state: SessionState, abuse_payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct an AbuseResult from a dict, boost it, return the new dict."""
    if not abuse_payload or "score" not in abuse_payload:
        return abuse_payload
    if not state.session_signals:
        return abuse_payload
    base = abuse.AbuseResult(
        score=float(abuse_payload.get("score") or 0.0),
        rule_version=str(abuse_payload.get("rule_version") or "v1"),
        fire_threshold=float(abuse_payload.get("fire_threshold") or 0.6),
        fired=bool(abuse_payload.get("fired") or False),
        signals=[
            abuse.Signal(
                name=s.get("name", ""),
                weight=float(s.get("weight") or 0.0),
                fired=bool(s.get("fired") or False),
                value=s.get("value") or {},
            )
            for s in (abuse_payload.get("signals") or [])
        ],
        customer_id=int(abuse_payload.get("customer_id") or -1),
    )
    boosted = abuse.boost_with_session_signals(base, state.session_signals)
    return boosted.to_dict()


def _advance_close_state(
    state: SessionState, customer_message: str
) -> tuple[str, str | None]:
    """Move the close-confirmation state machine one step forward.

    Returns (new_state, last_signal_kind_or_none).
    Side-effects state.close_state, cooldown_turns_remaining,
    last_close_signal, and close_confirmation_attempts.
    """
    current = state.close_state
    last_kind: str | None = None

    # COOLDOWN: just decrement
    if current == "cooldown":
        state.cooldown_turns_remaining = max(0, state.cooldown_turns_remaining - 1)
        if state.cooldown_turns_remaining == 0:
            state.close_state = "normal"
        return state.close_state, None

    # Customer responded to a confirmation question
    if current in ("awaiting_v1", "awaiting_v2"):
        cls = customer_signals.classify_close_response(customer_message)
        if cls == "affirmative":
            state.close_state = "emit_close"
        elif cls == "negative":
            state.close_state = "normal"
            state.close_confirmation_attempts = 0
            state.last_close_signal = None
        else:  # ambiguous
            if current == "awaiting_v1":
                state.close_state = "awaiting_v2"
                state.close_confirmation_attempts = 2
            else:
                # Two ambiguous in a row → back off per user constraint.
                state.close_state = "cooldown"
                state.cooldown_turns_remaining = COOLDOWN_TURNS_AFTER_BACKOFF
                state.close_confirmation_attempts = 0
                state.last_close_signal = None
        return state.close_state, (
            state.last_close_signal.get("kind") if state.last_close_signal else None
        )

    # NORMAL: detect new signal
    if current == "normal":
        signal = customer_signals.detect_close_signal(customer_message)
        if signal is not None:
            # Guard: don't trigger close-confirmation on weak signals before
            # the bot has actually done anything. Closing on "thanks!" before
            # we've issued a refund / filed a complaint / escalated is a
            # premature close that loses substantive-action rubric points.
            if signal.kind != "close_token" and not state.has_taken_substantive_action:
                return "normal", None
            state.last_close_signal = {
                "kind": signal.kind,
                "matched_text": signal.matched_text,
                "confidence": signal.confidence,
            }
            state.close_state = "awaiting_v1"
            state.close_confirmation_attempts = 1
            last_kind = signal.kind

            # Edge case: <=1 turns remaining + strong signal → skip the
            # confirm dance and emit close directly.
            if state.turns_remaining <= 1 and signal.confidence >= 1.0:
                state.close_state = "emit_close"
        return state.close_state, last_kind

    # EMIT_CLOSE was terminal previous turn — should have reset, but be safe.
    state.close_state = "normal"
    return "normal", None


@dataclass
class AuditRecord:
    session_id: str
    turn_number: int
    customer_message: str
    bot_message: str = ""
    injection_flags: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    llm_responses: list[dict[str, Any]] = field(default_factory=list)
    parsed_actions: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    confidence: float | None = None
    abuse_score: float | None = None
    abuse_signals: list[dict[str, Any]] | None = None
    validation_notes: list[str] = field(default_factory=list)
    latency_ms: int = 0
    usage: LLMUsage = field(default_factory=LLMUsage)
    error: str | None = None


class Orchestrator:
    def __init__(self, llm: LLMClient, app_conn: Any, policy_index: Any) -> None:
        self.llm = llm
        self.app_conn = app_conn
        self.policy_index = policy_index
        self._policy_text = Path(POLICY_FILE).read_text(encoding="utf-8")

    def run_turn(
        self, state: SessionState, customer_message: str
    ) -> tuple[str, list[dict[str, Any]], AuditRecord]:
        t0 = time.monotonic()
        state.turn_number += 1
        audit = AuditRecord(
            session_id=state.session_id,
            turn_number=state.turn_number,
            customer_message=customer_message,
        )

        injection_tags = injection_filter.scan(customer_message)
        audit.injection_flags = injection_tags

        # Accumulate pre-ID hostile signals (chargeback threats, legal
        # threats, prompt-injection attempts). These get folded into the
        # abuse score once the customer is identified.
        _accumulate_session_signals(state, customer_message, injection_tags)

        # If we already have prefetch-style abuse data from session start,
        # boost it now so the model sees the live picture in block 2.
        if state.prefetch.abuse and state.session_signals:
            state.prefetch.abuse = _apply_abuse_boost(state, state.prefetch.abuse)

        # Lazy mid-session prefetch. The customer may not give an order_id
        # in their opening message (especially when starting with abuse /
        # injection). When they reveal it later we want the same context
        # the session-start prefetch would have built — and we want it
        # BEFORE the LLM hop so claim verification can fire on the same
        # turn the bot sees the order data.
        if state.prefetch.order_id is None:
            parsed = prefetch_mod.parse_order_id(customer_message)
            if parsed is not None:
                try:
                    state.prefetch = prefetch_mod.build_prefetch_bundle(
                        parsed, self.app_conn
                    )
                    if state.prefetch.abuse and state.session_signals:
                        state.prefetch.abuse = _apply_abuse_boost(
                            state, state.prefetch.abuse
                        )
                except Exception:
                    # Prefetch failure is non-fatal — the model will fall
                    # back to tool calls.
                    pass

        # Accumulate food-category nouns the customer has mentioned in
        # ANY turn so claims made before prefetch existed still get
        # checked against the order.
        for noun in claim_verifier.extract_item_claims(customer_message):
            if noun not in state.claimed_food_nouns:
                state.claimed_food_nouns.append(noun)

        # Claim-vs-order verification + scope clarification — fires the
        # first turn we have BOTH a populated prefetch AND at least one
        # accumulated food noun. claim_verified is only set once the
        # verifier has actually examined real items.
        if state.prefetch.order and not state.claim_verified:
            if state.claimed_food_nouns:
                verification = claim_verifier.verify_nouns(
                    state.claimed_food_nouns, state.prefetch.items
                )
                scope = claim_verifier.assess_scope(
                    customer_message, state.prefetch.items
                )
                state.claim_verified = True
                if verification.has_discrepancy:
                    state.claim_discrepancy = verification.to_dict()
                elif scope.needs_quantity_confirm:
                    state.scope_clarification = scope.to_dict()

        # Advance the close-confirmation state machine BEFORE building the
        # prompt so the directive reflects this turn's state.
        new_close_state, last_kind = _advance_close_state(state, customer_message)
        close_directive = prompts.build_close_directive(
            state=new_close_state,
            confirmation_attempt=state.close_confirmation_attempts,
            last_signal_kind=last_kind
            or (state.last_close_signal.get("kind") if state.last_close_signal else None),
        )
        claim_directive = prompts.build_claim_directive(state.claim_discrepancy)
        scope_directive = (
            prompts.build_scope_directive(state.scope_clarification)
            if state.claim_discrepancy is None
            else None
        )
        abuse_directive = prompts.build_abuse_directive(state.prefetch.abuse)

        wrapped_user = injection_filter.safe_wrap(customer_message, turn=state.turn_number)
        if not state.history:
            state.history = []
        # Anthropic disallows adjacent user messages. If the previous turn ended
        # with a user-role tool_result message, merge this turn's customer text
        # into that message rather than appending a second user message.
        new_text_block = {"type": "text", "text": wrapped_user}
        if state.history and state.history[-1]["role"] == "user":
            existing = state.history[-1]
            if isinstance(existing["content"], str):
                existing["content"] = [{"type": "text", "text": existing["content"]}]
            existing["content"].append(new_text_block)
        else:
            state.history.append({"role": "user", "content": [new_text_block]})

        system_blocks = prompts.build_system_blocks(
            policy_text=self._policy_text,
            prefetch_json=state.prefetch.to_dict(),
            block3=prompts.render_block3(
                turn_number=state.turn_number,
                turns_remaining=state.turns_remaining,
                injection_tags=injection_tags,
                close_directive=close_directive,
                claim_directive=claim_directive,
                scope_directive=scope_directive,
                abuse_directive=abuse_directive,
            ),
        )

        ctx = {"app_conn": self.app_conn, "policy_index": self.policy_index, "state": state}

        for hop in range(MAX_TOOL_HOPS):
            # Force the model to call SOME tool every hop (never reply with bare
            # text), and on the last hop force submit_decision specifically.
            tool_choice = (
                {"type": "tool", "name": "submit_decision"}
                if hop == MAX_TOOL_HOPS - 1
                else {"type": "any"}
            )
            try:
                resp = self.llm.create(
                    system=system_blocks,
                    messages=state.history,
                    tools=TOOL_DEFINITIONS,
                    tool_choice=tool_choice,
                )
            except Exception as e:
                audit.error = f"llm_error:{type(e).__name__}:{e}"
                audit.latency_ms = int((time.monotonic() - t0) * 1000)
                audit.bot_message = FAIL_SAFE_REPLY
                actions = [
                    {"type": "escalate_to_human", "reason": f"agent_llm_error: {type(e).__name__}"}
                ]
                audit.parsed_actions = actions
                return FAIL_SAFE_REPLY, actions, audit

            usage = LLMUsage.from_response(resp)
            audit.usage.input_tokens += usage.input_tokens
            audit.usage.output_tokens += usage.output_tokens
            audit.usage.cache_read_tokens += usage.cache_read_tokens
            audit.usage.cache_write_tokens += usage.cache_write_tokens

            content_blocks = _serialize_response(resp)
            audit.llm_responses.append({"hop": hop, "stop_reason": resp.stop_reason, "content": content_blocks})

            tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                # Model replied with text but no tool call. Salvage the text as the
                # user-facing reply with empty actions, rather than escalating.
                text_chunks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
                bot_msg = "\n".join(text_chunks).strip() or FAIL_SAFE_REPLY
                # Append assistant message and a stub user message so history stays well-formed.
                state.history.append(
                    {"role": "assistant", "content": [{"type": "text", "text": bot_msg}]}
                )
                audit.error = "no_tool_use_recovered_text_only"
                audit.bot_message = bot_msg
                audit.parsed_actions = []
                audit.latency_ms = int((time.monotonic() - t0) * 1000)
                return bot_msg, [], audit

            assistant_content: list[dict[str, Any]] = []
            for b in resp.content:
                btype = getattr(b, "type", "")
                if btype == "text":
                    assistant_content.append({"type": "text", "text": b.text})
                elif btype == "tool_use":
                    assistant_content.append(
                        {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                    )
            state.history.append({"role": "assistant", "content": assistant_content})

            decision_tu = next((tu for tu in tool_uses if tu.name == "submit_decision"), None)
            if decision_tu is not None:
                # Anthropic requires every tool_use to be followed by a tool_result, even
                # for our terminal submit_decision. Acknowledge it so the next turn's
                # history stays valid.
                ack_results: list[dict[str, Any]] = []
                for tu in tool_uses:
                    if tu.name == "submit_decision":
                        ack_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": '{"status":"submitted"}',
                            }
                        )
                    else:
                        # Other tool_uses on the same hop still need real results
                        # because the assistant block already references them.
                        result = dispatch(tu.name, tu.input or {}, ctx)
                        audit.tool_calls.append(
                            {"name": tu.name, "args": tu.input, "result": _summarize_for_audit(result)}
                        )
                        ack_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": json.dumps(result, ensure_ascii=False)[:32000],
                            }
                        )
                state.history.append({"role": "user", "content": ack_results})
                return _finalize(audit, t0, state, decision_tu.input)

            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                args = tu.input or {}
                result = dispatch(tu.name, args, ctx)
                summarized = _summarize_for_audit(result)
                audit.tool_calls.append({"name": tu.name, "args": args, "result": summarized})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result, ensure_ascii=False)[:32000],
                    }
                )
                if tu.name == "compute_abuse_score" and "score" in result:
                    # Boost with pre-ID session signals so the model sees a
                    # truthful picture (refund-laundering by withholding ID
                    # is the attack we're closing).
                    boosted = _apply_abuse_boost(state, result)
                    cid = int(args.get("customer_id", -1))
                    state.abuse_cache[cid] = boosted
                    # Also surface in prefetch so block 2 reflects the boost
                    # on subsequent turns and the validator's abuse_fired
                    # signal stays accurate.
                    state.prefetch.abuse = boosted
                    # Update the tool_result we just queued so the model
                    # reads the boosted score, not the unboosted one.
                    tool_results[-1]["content"] = json.dumps(boosted, ensure_ascii=False)[:32000]
                if tu.name == "lookup_order" and result.get("found"):
                    found_order = result.get("order") or {}
                    found_id = int(found_order.get("id") or 0) or None
                    if found_id is not None:
                        # Treat first successful lookup as the focal order so
                        # downstream action backfill (issue_refund, file_complaint)
                        # has a primary order_id to attach to.
                        if state.prefetch.order_id is None:
                            state.prefetch.order_id = found_id
                            state.prefetch.found = True
                            state.prefetch.order = found_order
                            state.prefetch.items = result.get("items") or []
                        else:
                            state.prefetch.secondary_orders[found_id] = result

            state.history.append({"role": "user", "content": tool_results})

        audit.error = "tool_hop_cap_exhausted"
        audit.bot_message = FAIL_SAFE_REPLY
        actions = [{"type": "escalate_to_human", "reason": "agent_tool_hop_cap_exhausted"}]
        audit.parsed_actions = actions
        audit.latency_ms = int((time.monotonic() - t0) * 1000)
        return FAIL_SAFE_REPLY, actions, audit


def _finalize(
    audit: AuditRecord, t0: float, state: SessionState, raw_input: dict[str, Any]
) -> tuple[str, list[dict[str, Any]], AuditRecord]:
    try:
        decision = Decision(**raw_input)
    except Exception as e:
        audit.error = f"decision_validation_failed:{type(e).__name__}:{e}"
        audit.bot_message = FAIL_SAFE_REPLY
        actions = [
            {
                "type": "escalate_to_human",
                "reason": f"decision_validation_failed: {type(e).__name__}",
            }
        ]
        audit.parsed_actions = actions
        audit.latency_ms = int((time.monotonic() - t0) * 1000)
        return FAIL_SAFE_REPLY, actions, audit

    abuse_fired = bool(state.prefetch.abuse and state.prefetch.abuse.get("fired"))
    abuse_fired_signals = (
        [s.get("name") for s in (state.prefetch.abuse.get("signals") or []) if s.get("fired")]
        if abuse_fired
        else None
    )
    cleaned, notes = validate_and_clamp(
        decision,
        state.prefetch,
        close_state=state.close_state,
        cooldown_remaining=state.cooldown_turns_remaining,
        last_customer_message=audit.customer_message,
        claim_discrepancy_active=state.claim_discrepancy is not None,
        scope_clarification_active=state.scope_clarification is not None,
        abuse_fired=abuse_fired,
        abuse_fired_signals=abuse_fired_signals,
    )
    audit.bot_message = cleaned.response
    audit.parsed_actions = cleaned.actions
    audit.reasoning = cleaned.reasoning
    audit.confidence = cleaned.confidence
    audit.abuse_score = cleaned.abuse_score_used
    if state.prefetch.abuse:
        audit.abuse_signals = state.prefetch.abuse.get("signals")
    audit.validation_notes = notes
    audit.latency_ms = int((time.monotonic() - t0) * 1000)

    # Mark substantive action taken so close-confirmation can fire on
    # weak signals from this point on.
    SUBSTANTIVE_TYPES = {"issue_refund", "file_complaint", "escalate_to_human", "flag_abuse"}
    if any(a.get("type") in SUBSTANTIVE_TYPES for a in cleaned.actions):
        state.has_taken_substantive_action = True

    # Reset state machine after close was emitted; reset cooldown attempts
    # tracker after a normal turn to keep counters tidy.
    if state.close_state == "emit_close":
        state.close_state = "normal"
        state.close_confirmation_attempts = 0
        state.last_close_signal = None

    # Clear claim/scope discrepancy after this turn — the bot has
    # asked the clarifying question; the customer's next message is
    # the answer, not a fresh claim. (Validator already stripped
    # premature actions on this turn.)
    if state.claim_discrepancy is not None:
        state.claim_discrepancy = None
    if state.scope_clarification is not None:
        state.scope_clarified = True
        state.scope_clarification = None

    return cleaned.response, cleaned.actions, audit


def _serialize_response(resp: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in resp.content:
        btype = getattr(b, "type", "")
        if btype == "text":
            out.append({"type": "text", "text": b.text})
        elif btype == "tool_use":
            out.append({"type": "tool_use", "name": b.name, "input": b.input})
    return out


def _summarize_for_audit(result: dict[str, Any]) -> dict[str, Any]:
    """Trim large arrays so the audit row stays readable."""
    if not isinstance(result, dict):
        return {"value": str(result)[:500]}
    summary: dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, list) and len(v) > 5:
            summary[k] = {"_truncated_list": True, "n": len(v), "first": v[:3]}
        elif isinstance(v, str) and len(v) > 500:
            summary[k] = v[:500] + "..."
        else:
            summary[k] = v
    return summary
