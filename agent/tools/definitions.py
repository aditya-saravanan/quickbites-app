"""Anthropic tool definitions exposed to the LLM.

Kept minimal and named so the agent can pick them confidently. The
`submit_decision` tool is the agent's required final action.
"""

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "description": (
            "Fetch full details of an order by id, including line items, "
            "restaurant, and rider. Use when the customer mentions a different "
            "order than the one already in <session_context>."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "lookup_customer_history",
        "description": (
            "Fetch the customer's recent orders and basic profile. Already "
            "included in <session_context> for the opening order's customer; "
            "call only if you need a wider window than 90 days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer"},
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 90},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "lookup_complaint_history",
        "description": "All complaints filed by a customer (most recent 30).",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "integer"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "lookup_refund_history",
        "description": "Refunds issued to a customer in the last N days (default 30).",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer"},
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "lookup_rider_incidents",
        "description": (
            "All incidents recorded for a rider, with verified/unverified split. "
            "Use to assess credibility of rider-related complaints."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"rider_id": {"type": "integer"}},
            "required": ["rider_id"],
        },
    },
    {
        "name": "lookup_restaurant_stats",
        "description": "Average review rating and complaint pattern for a restaurant.",
        "input_schema": {
            "type": "object",
            "properties": {"restaurant_id": {"type": "integer"}},
            "required": ["restaurant_id"],
        },
    },
    {
        "name": "policy_lookup",
        "description": (
            "Search the policy_and_faq document for the most relevant chunks "
            "to a query (top-3, cosine similarity). The full policy is already "
            "in <policy> in the system prompt; use this only when you want "
            "verbatim re-grounding on a specific edge case."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 3, "maxLength": 200}},
            "required": ["query"],
        },
    },
    {
        "name": "compute_abuse_score",
        "description": (
            "Run the rules-based abuse scorer for a customer. Already invoked "
            "during prefetch; call again only if you've discovered a different "
            "customer_id than the one in <session_context>. Pass "
            "claim_contradicts_data=true if your reading of the conversation "
            "indicates the customer's claim contradicts the data on file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer"},
                "claim_contradicts_data": {"type": "boolean", "default": False},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "submit_decision",
        "description": (
            "Submit your final reply and zero or more actions for this turn. "
            "MUST be the last tool you call this turn. The 'response' is the "
            "natural-language reply the customer will see; 'actions' is the "
            "structured action list that gets graded; 'reasoning' is internal "
            "and never shown to the customer."
        ),
        "input_schema": {
            "type": "object",
            "required": ["response", "actions", "reasoning", "confidence", "close_intent"],
            "properties": {
                "response": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1500,
                    "description": (
                        "Plain-English reply to the customer. Polite, concise, "
                        "no PII (phone/email), no policy verbatim, never mention "
                        "being a bot. NEVER cite the customer's complaint history, "
                        "refund history, account age, or any internal data signal "
                        "as a reason — that's a hard policy violation."
                    ),
                },
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "enum": [
                                    "issue_refund",
                                    "file_complaint",
                                    "escalate_to_human",
                                    "flag_abuse",
                                    "close",
                                ]
                            }
                        },
                        "required": ["type"],
                    },
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Internal trace tying decision to data + policy. Cite "
                        "specific signals (e.g., 'abuse_score=0.55 with "
                        "high_complaint_rate fired'). This is NEVER shown to "
                        "the customer — be candid about WHY here."
                    ),
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "abuse_score_used": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "close_intent": {
                    "enum": ["continue", "ask_user_to_close", "emit_close_now"],
                    "description": (
                        "Your stance on closing the chat this turn. "
                        "'continue' = no close-related action. "
                        "'ask_user_to_close' = your response asks the customer "
                        "whether to wrap up; do NOT include a close action. "
                        "'emit_close_now' = include a `close` action in actions[] "
                        "(used after the customer has confirmed they're done)."
                    ),
                },
            },
        },
    },
]
