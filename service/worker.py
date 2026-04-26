"""Async batch runner that drives the simulator client loop.

For each scenario:
  1. start_session -> opening message + max_turns
  2. parse order_id, build prefetch bundle
  3. run_turn -> bot_message + actions
  4. write audit
  5. reply to simulator -> next customer message
  6. loop until done
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.llm_client import LLMClient
from agent.orchestrator import Orchestrator
from agent.policy_index import build_or_load_index
from agent.prefetch import build_prefetch_bundle, parse_order_id
from agent.session_state import SessionState
from agent.tools.sql import open_ro_connection
from audit.store import AuditStore

from .run_registry import RunRegistry
from .settings import get_settings
from .simulator_client import SimulatorClient, SimulatorError

log = logging.getLogger(__name__)


async def run_batch(
    *,
    run_id: str,
    mode: str,
    scenario_ids: list[int] | None,
    max_sessions: int | None,
    registry: RunRegistry,
    audit_store: AuditStore,
) -> None:
    settings = get_settings()
    settings_now = settings.simulated_now
    sim = SimulatorClient(settings.simulator_base_url, settings.candidate_token)
    llm = LLMClient(model=settings.anthropic_model, api_key=settings.anthropic_api_key)
    try:
        policy_idx = build_or_load_index(settings.policy_file, settings.policy_index_path)
    except Exception as e:
        log.warning("policy_index unavailable; policy_lookup tool will degrade: %s", e)
        policy_idx = None
    app_conn = open_ro_connection(settings.app_db_path)
    orchestrator = Orchestrator(llm, app_conn, policy_idx)

    registry.update(run_id, status="running")
    audit_store.create_run(run_id, mode, scenarios_requested=_calc_requested(scenario_ids, max_sessions, mode))

    try:
        if mode == "dev" and scenario_ids:
            for sid in scenario_ids:
                await _run_one(
                    run_id=run_id,
                    mode=mode,
                    sim=sim,
                    orchestrator=orchestrator,
                    registry=registry,
                    audit_store=audit_store,
                    scenario_id=sid,
                    settings_now=settings_now,
                )
        else:
            n = max_sessions if max_sessions is not None else (
                settings.max_prod_sessions_per_run if mode == "prod" else 5
            )
            for _ in range(n):
                try:
                    await _run_one(
                        run_id=run_id,
                        mode=mode,
                        sim=sim,
                        orchestrator=orchestrator,
                        registry=registry,
                        audit_store=audit_store,
                        scenario_id=None,
                        settings_now=settings_now,
                    )
                except SimulatorError as e:
                    if "http_409" in str(e):
                        log.info("run %s: scenarios exhausted (%s)", run_id, e)
                        break
                    raise
        registry.update(run_id, status="completed")
        audit_store.finish_run(run_id, "completed")
    except Exception as e:
        log.exception("run %s failed", run_id)
        registry.update(run_id, status="failed", error=f"{type(e).__name__}:{e}")
        audit_store.finish_run(run_id, "failed")


async def _run_one(
    *,
    run_id: str,
    mode: str,
    sim: SimulatorClient,
    orchestrator: Orchestrator,
    registry: RunRegistry,
    audit_store: AuditStore,
    scenario_id: int | None,
    settings_now: str,
) -> None:
    start = await sim.start_session(mode, scenario_id)
    state = SessionState.from_start(start)

    customer_msg = start.get("customer_message", "")
    order_id = parse_order_id(customer_msg)
    if order_id is not None:
        from agent.prefetch import PrefetchBundle

        try:
            state.prefetch = build_prefetch_bundle(order_id, orchestrator.app_conn)
        except Exception as e:
            log.warning("prefetch failed for order %s: %s", order_id, e)
            state.prefetch = PrefetchBundle.empty(missing_reason=f"prefetch_error:{e}")
    else:
        from agent.prefetch import PrefetchBundle

        state.prefetch = PrefetchBundle.empty(missing_reason="no_order_id_in_open_message")

    rule_version = (
        state.prefetch.abuse.get("rule_version", "v1") if state.prefetch.abuse else "v1"
    )

    audit_store.open_session(
        run_id=run_id,
        state=state,
        prefetch_json=state.prefetch.to_dict(),
        agent_version=get_settings().agent_version,
        rule_version=rule_version,
    )

    while not state.done:
        bot_msg, actions, audit = orchestrator.run_turn(state, customer_msg)
        audit_store.write_turn(audit)

        try:
            reply = await sim.reply(state.session_id, bot_msg, actions)
        except SimulatorError as e:
            log.error("simulator reply failed for session %s: %s", state.session_id, e)
            audit_store.close_session(
                session_id=state.session_id,
                close_reason=f"agent_aborted:{e}",
                final_score=None,
            )
            return

        state.turns_remaining = int(reply.get("turns_remaining", 0))
        state.done = bool(reply.get("done"))
        if state.done:
            state.close_reason = reply.get("close_reason")
            state.final_score = reply.get("score")
            break
        customer_msg = reply.get("customer_message") or ""

    audit_store.close_session(
        session_id=state.session_id,
        close_reason=state.close_reason,
        final_score=state.final_score if isinstance(state.final_score, dict) else None,
    )
    audit_store.increment_run_progress(run_id)
    registry.add_session(
        run_id,
        {
            "session_id": state.session_id,
            "scenario_id": state.scenario_id,
            "close_reason": state.close_reason,
            "final_score": state.final_score,
        },
    )


def _calc_requested(
    scenario_ids: list[int] | None, max_sessions: int | None, mode: str
) -> int | None:
    if scenario_ids is not None:
        return len(scenario_ids)
    if max_sessions is not None:
        return max_sessions
    if mode == "prod":
        return get_settings().max_prod_sessions_per_run
    return None


def schedule_run(
    *,
    mode: str,
    scenario_ids: list[int] | None,
    max_sessions: int | None,
    registry: RunRegistry,
    audit_store: AuditStore,
) -> str:
    info = registry.create(mode, _calc_requested(scenario_ids, max_sessions, mode))
    asyncio.create_task(
        run_batch(
            run_id=info.run_id,
            mode=mode,
            scenario_ids=scenario_ids,
            max_sessions=max_sessions,
            registry=registry,
            audit_store=audit_store,
        )
    )
    return info.run_id
