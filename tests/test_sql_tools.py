"""SQL tools unit tests + parameter-binding safety check."""

from __future__ import annotations

from agent.tools import sql


def test_lookup_order_returns_full_payload(fixture_conn):
    r = sql.lookup_order(fixture_conn, 101)
    assert r["found"] is True
    assert r["order"]["id"] == 101
    assert r["order"]["restaurant_name"] == "Tandoor House"
    assert r["order"]["rider_name"] == "Ravi K"
    assert len(r["items"]) == 2
    assert r["items"][0]["item_name"] == "Butter Chicken"


def test_lookup_order_missing(fixture_conn):
    r = sql.lookup_order(fixture_conn, 9999)
    assert r["found"] is False


def test_customer_history_omits_pii(fixture_conn):
    r = sql.lookup_customer_history(fixture_conn, 1, days=90)
    assert r["found"] is True
    assert "phone" not in r["profile"]
    assert "email" not in r["profile"]
    assert r["profile"]["loyalty_tier"] == "gold"
    assert r["summary"]["n_delivered"] == 10


def test_complaint_history_summary_buckets(fixture_conn):
    r = sql.lookup_complaint_history(fixture_conn, 5)
    assert r["summary"]["n_rejected"] == 5
    assert r["summary"]["n_total"] == 5


def test_refund_history_filters_by_window(fixture_conn):
    r = sql.lookup_refund_history(fixture_conn, 3, days=30)
    assert r["count"] == 4
    assert r["total_amount_inr"] == 850


def test_rider_incidents_buckets(fixture_conn):
    r = sql.lookup_rider_incidents(fixture_conn, 1)
    assert r["found"] is True
    assert r["summary"]["n_unverified"] == 1
    assert r["summary"]["n_verified"] == 0


def test_restaurant_stats(fixture_conn):
    r = sql.lookup_restaurant_stats(fixture_conn, 1)
    assert r["found"] is True
    assert r["profile"]["name"] == "Tandoor House"
    assert r["complaint_rate_per_order"] >= 0


def test_sql_injection_string_is_treated_as_data(fixture_conn):
    # int parsing in dispatcher catches this too, but ensure parameter binding is in fact used.
    try:
        sql.lookup_order(fixture_conn, "1; DROP TABLE customers")  # type: ignore[arg-type]
    except (TypeError, ValueError, Exception):
        pass
    cur = fixture_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM customers")
    assert cur.fetchone()[0] == 6
