"""Tool dispatcher: maps Claude tool_use names to handler functions.

Handlers receive the parsed JSON args and a context dict containing the
read-only DB connection and the policy index. Errors are caught and
returned as {"error": ...} so the model can recover.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from .. import abuse, policy_index
from . import sql

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _h_lookup_order(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    return sql.lookup_order(ctx["app_conn"], int(args["order_id"]))


def _h_lookup_customer_history(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    return sql.lookup_customer_history(
        ctx["app_conn"], int(args["customer_id"]), days=int(args.get("days", 90))
    )


def _h_lookup_complaint_history(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    return sql.lookup_complaint_history(ctx["app_conn"], int(args["customer_id"]))


def _h_lookup_refund_history(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    return sql.lookup_refund_history(
        ctx["app_conn"], int(args["customer_id"]), days=int(args.get("days", 30))
    )


def _h_lookup_rider_incidents(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    return sql.lookup_rider_incidents(ctx["app_conn"], int(args["rider_id"]))


def _h_lookup_restaurant_stats(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    return sql.lookup_restaurant_stats(ctx["app_conn"], int(args["restaurant_id"]))


def _h_policy_lookup(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    idx = ctx.get("policy_index")
    if idx is None:
        return {
            "query": args["query"],
            "chunks": [],
            "note": "policy_index_unavailable; rely on the full policy already in your system prompt",
        }
    q_emb = policy_index.embed_query(args["query"])
    chunks = idx.search(q_emb, k=3)
    return {"query": args["query"], "chunks": chunks}


def _h_compute_abuse_score(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    conn: sqlite3.Connection = ctx["app_conn"]
    return abuse.compute_abuse_score(
        int(args["customer_id"]),
        conn,
        claim_contradicts_data=bool(args.get("claim_contradicts_data", False)),
    ).to_dict()


HANDLERS: dict[str, ToolHandler] = {
    "lookup_order": _h_lookup_order,
    "lookup_customer_history": _h_lookup_customer_history,
    "lookup_complaint_history": _h_lookup_complaint_history,
    "lookup_refund_history": _h_lookup_refund_history,
    "lookup_rider_incidents": _h_lookup_rider_incidents,
    "lookup_restaurant_stats": _h_lookup_restaurant_stats,
    "policy_lookup": _h_policy_lookup,
    "compute_abuse_score": _h_compute_abuse_score,
}


def dispatch(name: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown_tool:{name}"}
    try:
        return handler(args, ctx)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
