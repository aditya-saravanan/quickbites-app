"""Prefetch bundle + order_id parser tests."""

from __future__ import annotations

from agent.prefetch import build_prefetch_bundle, parse_order_id


def test_parse_order_id_simple():
    assert parse_order_id("My order #123 was missing items") == 123


def test_parse_order_id_with_label():
    assert parse_order_id("order id 4567 was late") == 4567


def test_parse_order_id_ambiguous_returns_none():
    assert parse_order_id("Order 100 vs order 200 confused me") is None


def test_parse_order_id_no_match():
    assert parse_order_id("nothing arrived") is None


def test_build_prefetch_bundle_for_clean_customer(fixture_conn):
    b = build_prefetch_bundle(101, fixture_conn)
    assert b.found is True
    assert b.order["id"] == 101
    assert len(b.items) == 2
    assert b.customer["loyalty_tier"] == "gold"
    # PII suppressed
    assert "phone" not in b.customer
    assert "email" not in b.customer
    assert b.abuse["score"] == 0.0
    assert b.complaint_history["summary"]["n_total"] == 0


def test_build_prefetch_bundle_for_abuser(fixture_conn):
    b = build_prefetch_bundle(601, fixture_conn)
    assert b.found is True
    assert b.abuse["fired"] is True
    assert b.abuse["score"] >= 0.60
    assert b.complaint_history["summary"]["n_rejected"] >= 3


def test_build_prefetch_bundle_missing_order(fixture_conn):
    from agent.tools import sql

    r = sql.lookup_order(fixture_conn, 9999)
    assert r["found"] is False
