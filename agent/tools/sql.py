"""Read-only SQL tool handlers.

All queries are parameterized via ? placeholders. The connection is opened
read-only (?mode=ro). No free-form SQL is exposed to the LLM.

Customer PII (phone, email, exact address) is omitted from projections —
the model never sees it. Only loyalty_tier, joined_at, city, wallet_balance.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..settings import APP_DB_PATH, SIMULATED_NOW


def open_ro_connection(path: str = APP_DB_PATH) -> sqlite3.Connection:
    """Open the application DB in read-only mode."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def lookup_order(conn: sqlite3.Connection, order_id: int) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT o.id, o.customer_id, o.restaurant_id, o.rider_id,
               o.placed_at, o.delivered_at, o.status,
               o.subtotal_inr, o.delivery_fee_inr, o.total_inr,
               o.payment_method, o.promo_code,
               r.name AS restaurant_name, rd.name AS rider_name
        FROM orders o
        LEFT JOIN restaurants r ON r.id = o.restaurant_id
        LEFT JOIN riders rd ON rd.id = o.rider_id
        WHERE o.id = ?
        """,
        (order_id,),
    )
    order = _row_to_dict(cur.fetchone())
    if order is None:
        return {"found": False, "order_id": order_id}

    cur.execute(
        "SELECT item_name, qty, price_inr FROM order_items WHERE order_id = ?",
        (order_id,),
    )
    items = [dict(r) for r in cur.fetchall()]
    return {"found": True, "order": order, "items": items}


def lookup_customer_history(
    conn: sqlite3.Connection, customer_id: int, days: int = 90, now: str = SIMULATED_NOW
) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        # NOTE: phone and email deliberately NOT projected.
        "SELECT id, name, city, joined_at, loyalty_tier, wallet_balance_inr "
        "FROM customers WHERE id = ?",
        (customer_id,),
    )
    profile = _row_to_dict(cur.fetchone())
    if profile is None:
        return {"found": False, "customer_id": customer_id}

    cur.execute(
        """
        SELECT id, placed_at, status, total_inr, restaurant_id, payment_method
        FROM orders
        WHERE customer_id = ? AND placed_at >= date(?, '-' || ? || ' days')
        ORDER BY placed_at DESC
        LIMIT 25
        """,
        (customer_id, now, days),
    )
    orders = [dict(r) for r in cur.fetchall()]
    n_delivered = sum(1 for o in orders if o["status"] == "delivered")
    n_cancelled = sum(1 for o in orders if o["status"] == "cancelled")
    total_spend = sum(int(o["total_inr"] or 0) for o in orders if o["status"] == "delivered")
    return {
        "found": True,
        "profile": profile,
        "orders": orders,
        "summary": {
            "n_orders_window": len(orders),
            "n_delivered": n_delivered,
            "n_cancelled": n_cancelled,
            "total_spend_inr_window": total_spend,
            "window_days": days,
        },
    }


def lookup_complaint_history(conn: sqlite3.Connection, customer_id: int) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, order_id, target_type, target_id, raised_at, status,
               resolution, resolution_amount_inr,
               substr(description, 1, 200) AS description
        FROM complaints
        WHERE customer_id = ?
        ORDER BY raised_at DESC
        LIMIT 30
        """,
        (customer_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    n_open = sum(1 for c in rows if c["status"] == "open")
    n_resolved = sum(1 for c in rows if c["status"] == "resolved")
    n_rejected = sum(1 for c in rows if c["status"] == "rejected")
    return {
        "complaints": rows,
        "summary": {
            "n_total": len(rows),
            "n_open": n_open,
            "n_resolved": n_resolved,
            "n_rejected": n_rejected,
        },
    }


def lookup_refund_history(
    conn: sqlite3.Connection, customer_id: int, days: int = 30, now: str = SIMULATED_NOW
) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, order_id, amount_inr, type, issued_at, reason
        FROM refunds
        WHERE customer_id = ? AND issued_at >= date(?, '-' || ? || ' days')
        ORDER BY issued_at DESC
        """,
        (customer_id, now, days),
    )
    rows = [dict(r) for r in cur.fetchall()]
    total = sum(int(r["amount_inr"] or 0) for r in rows)
    return {
        "refunds": rows,
        "total_amount_inr": total,
        "count": len(rows),
        "window_days": days,
    }


def lookup_rider_incidents(conn: sqlite3.Connection, rider_id: int) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT id, name, city, joined_at FROM riders WHERE id = ?", (rider_id,))
    rider = _row_to_dict(cur.fetchone())
    if rider is None:
        return {"found": False, "rider_id": rider_id}

    cur.execute(
        """
        SELECT id, order_id, type, reported_at, verified,
               substr(notes, 1, 200) AS notes
        FROM rider_incidents
        WHERE rider_id = ?
        ORDER BY reported_at DESC
        LIMIT 50
        """,
        (rider_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    n_verified = sum(1 for r in rows if r["verified"] == 1)
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    return {
        "found": True,
        "rider": rider,
        "incidents": rows,
        "summary": {
            "n_total": len(rows),
            "n_verified": n_verified,
            "n_unverified": len(rows) - n_verified,
            "by_type": by_type,
        },
    }


def lookup_restaurant_stats(conn: sqlite3.Connection, restaurant_id: int) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.id, r.name, r.cuisine, r.city, r.area, r.joined_at,
               ROUND(AVG(rv.rating), 2) AS avg_rating,
               COUNT(rv.id) AS n_reviews
        FROM restaurants r
        LEFT JOIN reviews rv ON rv.restaurant_id = r.id
        WHERE r.id = ?
        GROUP BY r.id
        """,
        (restaurant_id,),
    )
    row = _row_to_dict(cur.fetchone())
    if row is None:
        return {"found": False, "restaurant_id": restaurant_id}

    cur.execute(
        """
        SELECT cp.target_type, COUNT(*) AS n
        FROM complaints cp
        JOIN orders o ON o.id = cp.order_id
        WHERE o.restaurant_id = ?
        GROUP BY cp.target_type
        """,
        (restaurant_id,),
    )
    complaints_by_type = {r["target_type"]: int(r["n"]) for r in cur.fetchall()}

    cur.execute("SELECT COUNT(*) FROM orders WHERE restaurant_id = ?", (restaurant_id,))
    n_orders = int(cur.fetchone()[0] or 0)
    n_total_complaints = sum(complaints_by_type.values())
    rate = (n_total_complaints / n_orders) if n_orders else 0.0

    return {
        "found": True,
        "profile": row,
        "complaints_by_type": complaints_by_type,
        "n_orders": n_orders,
        "complaint_rate_per_order": round(rate, 3),
    }
