"""Per-session state held in memory by the worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .prefetch import PrefetchBundle


CloseState = Literal["normal", "awaiting_v1", "awaiting_v2", "cooldown", "emit_close"]


@dataclass
class SessionState:
    session_id: str
    scenario_id: int | None
    mode: str
    max_turns: int
    turn_number: int = 0
    turns_remaining: int = 0
    done: bool = False
    close_reason: str | None = None
    final_score: dict[str, Any] | None = None
    prefetch: PrefetchBundle = field(default_factory=PrefetchBundle)
    history: list[dict[str, Any]] = field(default_factory=list)  # Anthropic messages array
    abuse_cache: dict[int, dict[str, Any]] = field(default_factory=dict)
    clarification_count: int = 0
    human_request_count: int = 0
    opened_at: str = ""

    # --- Close-confirmation state machine ---
    close_state: CloseState = "normal"
    cooldown_turns_remaining: int = 0
    last_close_signal: dict[str, Any] | None = None
    close_confirmation_attempts: int = 0
    # Set True once the bot emits any substantive action
    # (issue_refund / file_complaint / escalate_to_human / flag_abuse).
    # Wind-down / acceptance signals are ignored while this is False — we
    # don't close on a satisfaction signal before doing anything substantive.
    has_taken_substantive_action: bool = False

    # --- Pre-ID abuse-signal memory ---
    # Hostile signals (chargeback threats, legal threats, prompt-injection
    # attempts, excessive escalation demands) detected before we know the
    # customer_id. On lookup_customer success these get folded into the
    # abuse score so a customer can't launder hostility by withholding ID.
    session_signals: list[dict[str, Any]] = field(default_factory=list)

    # --- Claim-vs-order verification (defect 2) ---
    claim_verified: bool = False
    claim_discrepancy: dict[str, Any] | None = None
    # Food-category nouns the customer has mentioned across the whole
    # session ("curry", "pizza", "rice"). Accumulated each turn so a
    # claim made before the order_id was revealed (and thus before
    # prefetch existed) still gets compared against line items.
    claimed_food_nouns: list[str] = field(default_factory=list)

    # --- Scope clarification (defect 5) ---
    scope_clarified: bool = False
    scope_clarification: dict[str, Any] | None = None

    @classmethod
    def from_start(cls, start_resp: dict[str, Any]) -> "SessionState":
        return cls(
            session_id=start_resp["session_id"],
            scenario_id=start_resp.get("scenario_id"),
            mode=start_resp.get("mode", "dev"),
            max_turns=int(start_resp.get("max_turns", 8)),
            turns_remaining=int(start_resp.get("max_turns", 8)),
        )
