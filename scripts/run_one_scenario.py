"""Run a single dev scenario end-to-end. Useful for cheap smoke testing.

Usage:
    python -m scripts.run_one_scenario [scenario_id]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.store import AuditStore
from service.run_registry import RunRegistry
from service.settings import get_settings
from service.simulator_client import SimulatorClient
from service.worker import run_batch


async def main() -> None:
    scenario_id = int(sys.argv[1]) if len(sys.argv) > 1 else 101
    settings = get_settings()
    audit = AuditStore(settings.audit_db_path)
    registry = RunRegistry()
    info = registry.create("dev", scenarios_requested=1)
    print(f"==> Run {info.run_id} : dev scenario {scenario_id}")
    await run_batch(
        run_id=info.run_id,
        mode="dev",
        scenario_ids=[scenario_id],
        max_sessions=None,
        registry=registry,
        audit_store=audit,
    )

    persisted = audit.get_run(info.run_id) or {}
    sessions = persisted.get("sessions", [])
    print(f"\n==> Run done: {len(sessions)} session(s)")
    for s in sessions:
        print(f"   session_id={s['session_id']} close_reason={s.get('close_reason')}")
        sid = s["session_id"]
        full = audit.get_session(sid) or {}
        for t in full.get("turns", []):
            print(f"\n   --- turn {t['turn_number']} (latency {t['latency_ms']}ms) ---")
            print(f"     CUSTOMER: {t['customer_msg'][:200]}")
            print(f"     BOT:      {t['bot_msg'][:200]}")
            actions = json.loads(t["parsed_actions_json"] or "[]")
            for a in actions:
                print(f"     ACTION:   {a}")
            if t["error"]:
                print(f"     ERROR:    {t['error']}")
            if t["validation_notes_json"]:
                notes = json.loads(t["validation_notes_json"])
                if notes:
                    print(f"     NOTES:    {notes}")
            tool_calls = json.loads(t["tool_calls_json"] or "[]")
            for tc in tool_calls:
                print(f"     TOOL:     {tc['name']}({tc['args']})")
        # Try to fetch the simulator-side transcript too
        try:
            sim = SimulatorClient(settings.simulator_base_url, settings.candidate_token)
            transcript = await sim.transcript(sid)
            Path(f"transcript_{sid}.json").write_text(
                json.dumps(transcript, indent=2, ensure_ascii=False)
            )
            print(f"\n   Simulator transcript saved to transcript_{sid}.json")
        except Exception as e:
            print(f"   (transcript fetch failed: {e})")


if __name__ == "__main__":
    asyncio.run(main())
