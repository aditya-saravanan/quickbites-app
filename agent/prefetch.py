"""Build the per-session context bundle from the opening order_id.

This is the single biggest cost-saver: instead of an LLM tool-loop just to
discover basic facts, we hand the model a complete picture up front and
cache it as a system-prompt block.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import abuse
from .tools import sql

# Match patterns like "order #1234", "order 1234", "#1234", "order id 1234"
_ORDER_ID_RE = re.compile(
    r"\b(?:order(?:\s+id)?\s*#?\s*|#)(\d{1,6})\b",
    re.IGNORECASE,
)


def parse_order_id(text: str) -> int | None:
    """Extract a single order_id from a customer message, if unambiguous."""
    matches = _ORDER_ID_RE.findall(text or "")
    if not matches:
        return None
    candidates = {int(m) for m in matches if 0 < int(m) < 1_000_000}
    if len(candidates) != 1:
        return None
    return next(iter(candidates))


@dataclass
class PrefetchBundle:
    order_id: int | None = None
    found: bool = False
    order: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = None
    customer: dict[str, Any] | None = None
    customer_summary: dict[str, Any] | None = None
    recent_orders: list[dict[str, Any]] | None = None
    complaint_history: dict[str, Any] | None = None
    refund_history: dict[str, Any] | None = None
    restaurant_stats: dict[str, Any] | None = None
    rider_summary: dict[str, Any] | None = None
    abuse: dict[str, Any] | None = None
    data_quality: list[str] = field(default_factory=list)
    missing_reason: str | None = None
    secondary_orders: dict[int, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "found": self.found,
            "missing_reason": self.missing_reason,
            "order": self.order,
            "items": self.items,
            "customer": self.customer,
            "customer_summary": self.customer_summary,
            "recent_orders": self.recent_orders,
            "complaint_history": self.complaint_history,
            "refund_history": self.refund_history,
            "restaurant_stats": self.restaurant_stats,
            "rider_summary": self.rider_summary,
            "abuse": self.abuse,
            "data_quality": self.data_quality,
            "secondary_orders": self.secondary_orders,
        }

    @classmethod
    def empty(cls, missing_reason: str) -> "PrefetchBundle":
        return cls(missing_reason=missing_reason)


def build_prefetch_bundle(order_id: int, conn: sqlite3.Connection) -> PrefetchBundle:
    """Fetch everything the agent typically needs in one shot."""
    bundle = PrefetchBundle(order_id=order_id)
    order_resp = sql.lookup_order(conn, order_id)
    if not order_resp.get("found"):
        bundle.missing_reason = "order_not_found"
        return bundle

    bundle.found = True
    bundle.order = order_resp["order"]
    bundle.items = order_resp["items"]

    if bundle.order["status"] == "delivered" and not bundle.order.get("delivered_at"):
        bundle.data_quality.append("delivered_status_without_timestamp")

    customer_id = bundle.order["customer_id"]
    cust = sql.lookup_customer_history(conn, customer_id, days=90)
    if cust.get("found"):
        bundle.customer = cust["profile"]
        bundle.customer_summary = cust["summary"]
        bundle.recent_orders = cust["orders"]
    else:
        bundle.data_quality.append("customer_profile_missing")

    bundle.complaint_history = sql.lookup_complaint_history(conn, customer_id)
    bundle.refund_history = sql.lookup_refund_history(conn, customer_id, days=30)

    restaurant_id = bundle.order.get("restaurant_id")
    if restaurant_id:
        rstats = sql.lookup_restaurant_stats(conn, restaurant_id)
        if rstats.get("found"):
            bundle.restaurant_stats = {
                "profile": rstats["profile"],
                "complaint_rate_per_order": rstats["complaint_rate_per_order"],
                "complaints_by_type": rstats["complaints_by_type"],
            }

    rider_id = bundle.order.get("rider_id")
    if rider_id:
        ri = sql.lookup_rider_incidents(conn, rider_id)
        if ri.get("found"):
            bundle.rider_summary = {"rider": ri["rider"], "summary": ri["summary"]}

    bundle.abuse = abuse.compute_abuse_score(customer_id, conn).to_dict()

    return bundle
