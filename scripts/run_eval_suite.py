"""Dev eval suite: runs all 5 rehearsal scenarios, asserts per-scenario
expectations, prints pass/fail, writes results JSON for diffing.

Usage:
    python -m scripts.run_eval_suite

Exit code is non-zero on any FAIL.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.store import AuditStore
from service.run_registry import RunRegistry
from service.settings import get_settings
from service.worker import run_batch

DEV_SCENARIOS = [101, 102, 103, 104, 105]
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "dev_eval_snapshot.json"
RESULTS_LATEST = Path("eval_results_latest.json")


@dataclass
class ScenarioResult:
    scenario_id: int
    name: str
    passed: bool
    failures: list[str]
    actions_taken: list[dict]
    close_reason: str | None
    session_id: str


def _check(scenario_id: int, snapshot: dict, actions: list[dict], close_reason: str | None) -> list[str]:
    """Return list of failure strings for this scenario; empty list = pass."""
    failures: list[str] = []
    spec = snapshot.get(str(scenario_id), {})
    if spec.get("_capture_first"):
        # No assertions yet; just record this run for future comparison.
        return []

    types = [a.get("type") for a in actions]

    required = spec.get("expected_action_types_required") or []
    for r in required:
        if r not in types:
            failures.append(f"missing_required_action:{r}")

    any_of = spec.get("expected_action_types_any_of") or []
    if any_of and not any(a in types for a in any_of):
        failures.append(f"missing_any_of:{any_of}")

    forbidden = spec.get("forbidden_action_types") or []
    for f in forbidden:
        if f in types:
            failures.append(f"forbidden_action_present:{f}")

    if (
        spec.get("expected_complaint_target")
        and "file_complaint" in types
    ):
        complaint_targets = {
            a.get("target_type") for a in actions if a.get("type") == "file_complaint"
        }
        if spec["expected_complaint_target"] not in complaint_targets:
            failures.append(
                f"wrong_complaint_target:expected={spec['expected_complaint_target']}"
                f":got={complaint_targets}"
            )

    if spec.get("max_refund_inr_per_order"):
        cap = int(spec["max_refund_inr_per_order"])
        per_order: dict = {}
        for a in actions:
            if a.get("type") == "issue_refund":
                oid = a.get("order_id")
                per_order[oid] = per_order.get(oid, 0) + int(a.get("amount_inr", 0))
        for oid, total in per_order.items():
            if total > cap:
                failures.append(f"refund_over_cap:order_{oid}:total_{total}>cap_{cap}")

    if spec.get("close_reason_must_be_in"):
        allowed = spec["close_reason_must_be_in"]
        if close_reason not in allowed:
            failures.append(f"close_reason_unexpected:{close_reason}_not_in_{allowed}")

    return failures


def _gather_session_actions(audit: AuditStore, session_id: str) -> list[dict]:
    payload = audit.get_session(session_id) or {}
    out: list[dict] = []
    for t in payload.get("turns", []):
        try:
            out.extend(json.loads(t["parsed_actions_json"] or "[]"))
        except Exception:
            pass
    return out


def _short(s: str | None, n: int = 12) -> str:
    return (s[:n] + "…") if s and len(s) > n else (s or "—")


async def main() -> int:
    settings = get_settings()
    audit = AuditStore(settings.audit_db_path)
    registry = RunRegistry()
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    print("== QuickBites dev eval suite ==")
    info = registry.create("dev", scenarios_requested=len(DEV_SCENARIOS))
    print(f"run_id={info.run_id} scenarios={DEV_SCENARIOS}\n")
    await run_batch(
        run_id=info.run_id,
        mode="dev",
        scenario_ids=DEV_SCENARIOS,
        max_sessions=None,
        registry=registry,
        audit_store=audit,
    )

    persisted = audit.get_run(info.run_id) or {}
    sessions = persisted.get("sessions", [])

    results: list[ScenarioResult] = []
    for s in sessions:
        sid = s["session_id"]
        scenario_id = int(s.get("scenario_id") or 0)
        name = snapshot.get(str(scenario_id), {}).get("name", f"scenario_{scenario_id}")
        actions = _gather_session_actions(audit, sid)
        close_reason = s.get("close_reason")
        failures = _check(scenario_id, snapshot, actions, close_reason)
        results.append(
            ScenarioResult(
                scenario_id=scenario_id,
                name=name,
                passed=not failures,
                failures=failures,
                actions_taken=actions,
                close_reason=close_reason,
                session_id=sid,
            )
        )

    print("\n== Results ==")
    for r in sorted(results, key=lambda x: x.scenario_id):
        status = "PASS" if r.passed else "FAIL"
        action_types = [a.get("type") for a in r.actions_taken]
        print(
            f"  Scenario {r.scenario_id} ({r.name}): {status}"
            f"  actions={action_types}"
            f"  close_reason={r.close_reason}"
            f"  session={_short(r.session_id)}"
        )
        for f in r.failures:
            print(f"    - {f}")

    n_pass = sum(1 for r in results if r.passed)
    n_total = len(results)
    n_clean_close = sum(1 for r in results if r.close_reason in ("bot_closed", "customer_closed"))
    n_turn_cap = sum(1 for r in results if r.close_reason == "turn_cap")
    print(
        f"\nSummary: {n_pass}/{n_total} PASS · "
        f"clean_close {n_clean_close}/{n_total} · "
        f"turn_cap {n_turn_cap}/{n_total}"
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {
        "run_id": info.run_id,
        "timestamp": timestamp,
        "results": [
            {
                "scenario_id": r.scenario_id,
                "name": r.name,
                "passed": r.passed,
                "failures": r.failures,
                "actions_taken": r.actions_taken,
                "close_reason": r.close_reason,
                "session_id": r.session_id,
            }
            for r in results
        ],
    }
    Path(f"eval_results_{timestamp}.json").write_text(json.dumps(out, indent=2))
    RESULTS_LATEST.write_text(json.dumps(out, indent=2))
    print(f"\nResults written to eval_results_{timestamp}.json and {RESULTS_LATEST}")

    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
