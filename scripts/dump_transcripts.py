"""Dump readable transcripts for each scenario in the latest eval run.

For each session in eval_results_latest.json:
  - Reads the audit DB for our side (customer messages, bot replies, actions,
    validation notes, latency, abuse signals).
  - Fetches the simulator's canonical transcript (dev mode only).
  - Writes a markdown file per scenario.

Usage:
    python -m scripts.dump_transcripts                   # latest run
    python -m scripts.dump_transcripts <results.json>    # specific run
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.store import AuditStore
from service.settings import get_settings
from service.simulator_client import SimulatorClient


def _format_action(a: dict) -> str:
    parts = [f"**{a.get('type')}**"]
    for k, v in a.items():
        if k == "type":
            continue
        parts.append(f"{k}={v}")
    return " · ".join(parts)


def _scenario_label(scenario_id: int) -> str:
    labels = {
        101: "cold_food_quality_complaint",
        102: "rider_courtesy_complaint",
        103: "serial_refunder_abuse",
        104: "scenario_104",
        105: "scenario_105",
    }
    return labels.get(scenario_id, f"scenario_{scenario_id}")


def render_markdown(
    scenario_id: int,
    session_id: str,
    audit_payload: dict,
    sim_transcript: dict | None,
    eval_pass: bool,
    eval_failures: list[str],
) -> str:
    sess = audit_payload.get("session", {})
    turns = audit_payload.get("turns", [])

    lines: list[str] = []
    lines.append(f"# Scenario {scenario_id} · {_scenario_label(scenario_id)}")
    lines.append("")
    lines.append(f"- **Session ID:** `{session_id}`")
    lines.append(f"- **Mode:** {sess.get('mode')}")
    lines.append(f"- **Opened:** {sess.get('opened_at')}")
    lines.append(f"- **Closed:** {sess.get('closed_at')}")
    lines.append(f"- **Close reason:** `{sess.get('close_reason')}`")
    lines.append(f"- **Eval result:** {'✅ PASS' if eval_pass else '❌ FAIL'}")
    if eval_failures:
        for f in eval_failures:
            lines.append(f"  - {f}")
    lines.append(f"- **Turns:** {len(turns)}")
    lines.append("")

    # Per-turn token + latency summary
    total_input = sum(int(t.get("input_tokens") or 0) for t in turns)
    total_output = sum(int(t.get("output_tokens") or 0) for t in turns)
    total_cache_read = sum(int(t.get("cache_read_tokens") or 0) for t in turns)
    total_latency = sum(int(t.get("latency_ms") or 0) for t in turns)
    lines.append("## Telemetry")
    lines.append("")
    lines.append(
        f"- Fresh input: **{total_input:,}** · "
        f"cache-read: **{total_cache_read:,}** · "
        f"output: **{total_output:,}** tokens"
    )
    total_in = total_input + total_cache_read
    cache_ratio = (total_cache_read / total_in) if total_in else 0
    lines.append(
        f"- Cache hit ratio: **{cache_ratio:.0%}** "
        f"({total_cache_read:,} cached / {total_in:,} total input)"
    )
    lines.append(f"- Total latency: **{total_latency / 1000:.1f}s** "
                 f"({total_latency // max(len(turns), 1)}ms/turn avg)")
    lines.append("")

    # Per-turn full trace
    lines.append("## Conversation")
    lines.append("")
    for t in turns:
        lines.append(f"### Turn {t['turn_number']}")
        lines.append("")
        lines.append(f"**Customer:** {t['customer_msg']}")
        lines.append("")
        lines.append(f"**Bot:** {t['bot_msg']}")
        lines.append("")
        actions = json.loads(t.get("parsed_actions_json") or "[]")
        if actions:
            lines.append("**Actions emitted:**")
            for a in actions:
                lines.append(f"- {_format_action(a)}")
            lines.append("")
        injection = json.loads(t.get("injection_flags_json") or "[]")
        if injection:
            lines.append(f"**Injection signals:** `{injection}`")
            lines.append("")
        notes = json.loads(t.get("validation_notes_json") or "[]")
        if notes:
            lines.append("**Validation notes:**")
            for n in notes:
                lines.append(f"- `{n}`")
            lines.append("")
        if t.get("abuse_score") is not None:
            lines.append(f"**abuse_score_used:** {t['abuse_score']}")
            lines.append("")
        if t.get("reasoning"):
            r = t["reasoning"]
            if len(r) > 500:
                r = r[:500] + "…"
            lines.append(f"**Internal reasoning:** {r}")
            lines.append("")
        tool_calls = json.loads(t.get("tool_calls_json") or "[]")
        if tool_calls:
            lines.append("**Tools called:**")
            for tc in tool_calls:
                lines.append(f"- `{tc['name']}({tc.get('args', {})})`")
            lines.append("")
        if t.get("error"):
            lines.append(f"**ERROR:** `{t['error']}`")
            lines.append("")
        lines.append(f"_Turn latency: {t.get('latency_ms')}ms · "
                     f"input {t.get('input_tokens')} (cache-read {t.get('cache_read_tokens')}) · "
                     f"output {t.get('output_tokens')} tokens_")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Simulator-side canonical transcript (if available — dev mode only)
    if sim_transcript:
        lines.append("## Simulator-side canonical transcript (dev only)")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(sim_transcript, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


async def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eval_results_latest.json")
    if not src.exists():
        print(f"Eval results file not found: {src}")
        print("Run `python -m scripts.run_eval_suite` first.")
        return 1

    eval_data = json.loads(src.read_text(encoding="utf-8"))
    settings = get_settings()
    audit = AuditStore(settings.audit_db_path)
    sim = SimulatorClient(settings.simulator_base_url, settings.candidate_token)

    out_dir = Path("transcripts")
    out_dir.mkdir(exist_ok=True)

    for r in eval_data["results"]:
        scenario_id = r["scenario_id"]
        session_id = r["session_id"]
        payload = audit.get_session(session_id)
        if payload is None:
            print(f"  scenario {scenario_id}: no audit data for {session_id}")
            continue
        try:
            sim_transcript = await sim.transcript(session_id)
        except Exception as e:
            sim_transcript = {"error": str(e)}

        md = render_markdown(
            scenario_id=scenario_id,
            session_id=session_id,
            audit_payload=payload,
            sim_transcript=sim_transcript,
            eval_pass=r["passed"],
            eval_failures=r["failures"],
        )
        path = out_dir / f"scenario_{scenario_id}.md"
        path.write_text(md, encoding="utf-8")
        print(f"  ✓ wrote {path} ({len(payload.get('turns', []))} turns)")

    print(f"\nAll transcripts saved to {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
