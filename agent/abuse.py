"""Versioned, rules-based abuse detector.

Pure function over a read-only SQLite connection. No LLM calls inside.
Calibrated against the actual data distribution in app.db (2026-04-13 snapshot):
  - complaint_rate >= 0.50 fires on 10/50 customers (~20%, the abusive cohort)
  - >= 3 refunds in 30d fires on 1 customer
  - >= 2 rejected complaints fires on 7 customers
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .settings import SIMULATED_NOW

ABUSE_RULES_V1: dict[str, Any] = {
    "version": "v1",
    "fire_threshold": 0.60,
    "rules": {
        "high_complaint_rate": {
            "weight": 0.30,
            "threshold_rate": 0.50,
            "min_orders": 5,
            "description": "Complaint-to-order ratio >= 50% (min 5 orders).",
        },
        "recent_refund_burst": {
            "weight": 0.25,
            "threshold_count": 3,
            "window_days": 30,
            "description": ">= 3 refunds issued in the last 30 days.",
        },
        "new_account_with_complaints": {
            "weight": 0.20,
            "max_account_age_days": 30,
            "min_complaints": 2,
            "description": "Account < 30 days old with >= 2 complaints.",
        },
        "repeat_rejection_history": {
            "weight": 0.15,
            "threshold_count": 2,
            "description": ">= 2 prior complaints with status='rejected'.",
        },
        "claim_contradicts_data": {
            "weight": 0.10,
            "description": "LLM-judged signal injected via tool input. Signal-only.",
            "computed_externally": True,
        },
    },
}


@dataclass
class Signal:
    name: str
    weight: float
    fired: bool
    value: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "weight": self.weight, "fired": self.fired, "value": self.value}


@dataclass
class AbuseResult:
    score: float
    rule_version: str
    fire_threshold: float
    fired: bool
    signals: list[Signal]
    customer_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "score": round(self.score, 3),
            "rule_version": self.rule_version,
            "fire_threshold": self.fire_threshold,
            "fired": self.fired,
            "signals": [s.to_dict() for s in self.signals],
        }


def compute_abuse_score(
    customer_id: int,
    conn: sqlite3.Connection,
    *,
    claim_contradicts_data: bool = False,
    now: str = SIMULATED_NOW,
    rules: dict[str, Any] = ABUSE_RULES_V1,
) -> AbuseResult:
    """Compute a weighted heuristic score (NOT a probability) in [0, 1].

    Each rule fires independently; the score is the sum of fired weights, clamped.
    Returns full per-signal trace for audit/explainability.
    """
    cur = conn.cursor()
    signals: list[Signal] = []

    rcfg = rules["rules"]["high_complaint_rate"]
    cur.execute(
        "SELECT (SELECT COUNT(*) FROM orders WHERE customer_id=?) AS n_orders, "
        "(SELECT COUNT(*) FROM complaints WHERE customer_id=?) AS n_complaints",
        (customer_id, customer_id),
    )
    row = cur.fetchone()
    n_orders = int(row[0] or 0)
    n_complaints = int(row[1] or 0)
    rate = (n_complaints / n_orders) if n_orders else 0.0
    fired = n_orders >= rcfg["min_orders"] and rate >= rcfg["threshold_rate"]
    signals.append(
        Signal(
            "high_complaint_rate",
            rcfg["weight"],
            fired,
            {"rate": round(rate, 3), "n_orders": n_orders, "n_complaints": n_complaints},
        )
    )

    rcfg = rules["rules"]["recent_refund_burst"]
    cur.execute(
        "SELECT COUNT(*) FROM refunds WHERE customer_id=? "
        "AND issued_at >= date(?, '-' || ? || ' days')",
        (customer_id, now, rcfg["window_days"]),
    )
    n_recent = int(cur.fetchone()[0] or 0)
    fired = n_recent >= rcfg["threshold_count"]
    signals.append(
        Signal(
            "recent_refund_burst",
            rcfg["weight"],
            fired,
            {"count": n_recent, "window_days": rcfg["window_days"]},
        )
    )

    rcfg = rules["rules"]["new_account_with_complaints"]
    cur.execute(
        "SELECT julianday(?) - julianday(joined_at) AS age_days, "
        "(SELECT COUNT(*) FROM complaints WHERE customer_id=?) AS n_complaints "
        "FROM customers WHERE id=?",
        (now, customer_id, customer_id),
    )
    row = cur.fetchone()
    if row is None:
        age_days, n_c = None, 0
        fired = False
    else:
        age_days = float(row[0]) if row[0] is not None else None
        n_c = int(row[1] or 0)
        fired = (
            age_days is not None
            and age_days < rcfg["max_account_age_days"]
            and n_c >= rcfg["min_complaints"]
        )
    signals.append(
        Signal(
            "new_account_with_complaints",
            rcfg["weight"],
            fired,
            {"age_days": round(age_days, 1) if age_days is not None else None, "n_complaints": n_c},
        )
    )

    rcfg = rules["rules"]["repeat_rejection_history"]
    cur.execute(
        "SELECT COUNT(*) FROM complaints WHERE customer_id=? AND status='rejected'",
        (customer_id,),
    )
    n_rej = int(cur.fetchone()[0] or 0)
    fired = n_rej >= rcfg["threshold_count"]
    signals.append(
        Signal("repeat_rejection_history", rcfg["weight"], fired, {"count": n_rej})
    )

    rcfg = rules["rules"]["claim_contradicts_data"]
    signals.append(
        Signal(
            "claim_contradicts_data",
            rcfg["weight"],
            bool(claim_contradicts_data),
            {"externally_provided": True},
        )
    )

    score = sum(s.weight for s in signals if s.fired)
    score = max(0.0, min(1.0, score))
    return AbuseResult(
        score=score,
        rule_version=rules["version"],
        fire_threshold=rules["fire_threshold"],
        fired=score >= rules["fire_threshold"],
        signals=signals,
        customer_id=customer_id,
    )


SESSION_BOOST_CAP = 0.30


def boost_with_session_signals(
    base: AbuseResult, session_signals: list[dict[str, Any]]
) -> AbuseResult:
    """Fold pre-ID hostile signals into a base AbuseResult.

    Each entry in `session_signals` contributes its weight to the score
    and appears as a fired Signal with `value.source = "session"` so
    audit can distinguish DB-derived from session-derived signals.

    The total session contribution is capped at SESSION_BOOST_CAP — a
    deliberate injection attack alone (weight 0.30) can take the score
    over threshold, but multiple frustration-driven signals (chargeback
    threat, legal threat, escalation demand) cannot compound past the
    same ceiling. The cap exists so a frustrated-but-legitimate
    customer doesn't get treated like an active attacker just for
    repeating their displeasure across turns.
    """
    if not session_signals:
        return base

    extra_signals: list[Signal] = []
    extra_weight_total = 0.0
    extra_weight_capped = 0.0
    seen: set[str] = {s.name for s in base.signals}
    for s in session_signals:
        name = s.get("signal")
        if not name or name in seen:
            continue
        seen.add(name)
        weight = float(s.get("weight") or 0.0)
        extra_weight_total += weight
        extra_signals.append(
            Signal(
                name=name,
                weight=weight,
                fired=True,
                value={"source": "session", "matched_text": s.get("matched_text", "")[:120]},
            )
        )

    extra_weight_capped = min(extra_weight_total, SESSION_BOOST_CAP)
    new_score = max(0.0, min(1.0, base.score + extra_weight_capped))
    return AbuseResult(
        score=new_score,
        rule_version=base.rule_version,
        fire_threshold=base.fire_threshold,
        fired=new_score >= base.fire_threshold,
        signals=base.signals + extra_signals,
        customer_id=base.customer_id,
    )
