"""Dev-mode eval harness.

Runs the 5 rehearsal scenarios (101–105) end-to-end against the simulator,
fetches each transcript, and prints a one-line per-scenario summary.

Usage:
    python -m scripts.run_dev_eval
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

DEV_SCENARIOS = [101, 102, 103, 104, 105]


async def main() -> None:
    settings = get_settings()
    audit = AuditStore(settings.audit_db_path)
    registry = RunRegistry()
    info = registry.create("dev", scenarios_requested=len(DEV_SCENARIOS))
    await run_batch(
        run_id=info.run_id,
        mode="dev",
        scenario_ids=DEV_SCENARIOS,
        max_sessions=None,
        registry=registry,
        audit_store=audit,
    )

    sim = SimulatorClient(settings.simulator_base_url, settings.candidate_token)
    print(f"\n=== Dev run {info.run_id} complete ===")
    persisted = audit.get_run(info.run_id) or {}
    sessions = persisted.get("sessions", [])
    for s in sessions:
        sid = s["session_id"]
        try:
            t = await sim.transcript(sid)
        except Exception as e:
            print(f"  scenario={s.get('scenario_id')} session={sid} TRANSCRIPT_ERR={e}")
            continue
        actions: list[dict] = []
        for entry in t.get("turns", []) or t.get("messages", []) or []:
            if isinstance(entry, dict):
                acts = entry.get("actions") or []
                actions.extend(acts)
        action_types = [a.get("type") for a in actions]
        print(
            f"  scenario={s.get('scenario_id')} close_reason={s.get('close_reason')} "
            f"actions={action_types}"
        )
    print("\nFull transcripts dumped to dev_eval_transcripts.json")
    transcripts: dict[str, dict] = {}
    for s in sessions:
        try:
            transcripts[s["session_id"]] = await sim.transcript(s["session_id"])
        except Exception as e:
            transcripts[s["session_id"]] = {"error": str(e)}
    Path("dev_eval_transcripts.json").write_text(json.dumps(transcripts, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
