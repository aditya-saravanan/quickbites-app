"""FastAPI control plane.

Routes:
  GET  /healthz                 — public liveness probe
  POST /run                     — kick off a batch run (control-plane auth)
  GET  /run/{run_id}            — run status + per-session summaries
  GET  /summary                 — proxies /v1/candidate/summary upstream
  GET  /sessions                — list audit sessions
  GET  /sessions/{session_id}   — full audit trail for one session
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from agent.policy_index import build_or_load_index
from agent.tools.sql import open_ro_connection
from audit.store import AuditStore

from .auth import require_control_plane_token
from .run_registry import RunRegistry
from .settings import get_settings
from .simulator_client import SimulatorClient, SimulatorError
from .worker import schedule_run

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.audit_store = AuditStore(settings.audit_db_path)
    app.state.run_registry = RunRegistry()
    if Path(settings.policy_file).exists():
        try:
            build_or_load_index(settings.policy_file, settings.policy_index_path)
        except Exception as e:
            log.warning(
                "policy index unavailable at startup: %s "
                "(policy_lookup tool will degrade gracefully; inline policy still in use)",
                e,
            )
    if Path(settings.app_db_path).exists():
        open_ro_connection(settings.app_db_path).close()
    yield


app = FastAPI(title="QuickBites Support Agent", lifespan=lifespan)


class RunRequest(BaseModel):
    mode: str = Field(pattern=r"^(dev|prod)$")
    scenario_ids: list[int] | None = None
    max_sessions: int | None = Field(default=None, ge=1, le=44)
    confirm_prod: bool = False


class RunResponse(BaseModel):
    run_id: str
    status: str


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    settings = get_settings()
    out: dict[str, Any] = {"status": "ok"}
    out["app_db"] = "ok" if Path(settings.app_db_path).exists() else "missing"
    out["audit_db"] = "ok" if Path(settings.audit_db_path).exists() else "uninitialized"
    out["policy_index"] = "ok" if Path(settings.policy_index_path).exists() else "uninitialized"
    out["anthropic_key"] = "set" if settings.anthropic_api_key else "missing"
    out["candidate_token"] = "set" if settings.candidate_token else "missing"
    out["agent_version"] = settings.agent_version
    return out


@app.post("/run", response_model=RunResponse, dependencies=[Depends(require_control_plane_token)])
async def post_run(req: RunRequest) -> RunResponse:
    settings = get_settings()
    audit: AuditStore = app.state.audit_store
    registry: RunRegistry = app.state.run_registry

    if req.mode == "prod":
        if not req.confirm_prod:
            raise HTTPException(400, "prod_run_requires_confirm_prod=true")
        if not settings.candidate_token:
            raise HTTPException(400, "candidate_token_not_configured")
        if registry.has_running_prod() or audit.has_running_prod():
            raise HTTPException(409, "prod_run_already_in_progress")
        used = audit.count_prod_sessions()
        requested = req.max_sessions or settings.max_prod_sessions_per_run
        if used + requested > settings.prod_session_cap_total:
            raise HTTPException(
                400,
                f"would_exceed_prod_cap: used={used} requested={requested} cap={settings.prod_session_cap_total}",
            )

    run_id = schedule_run(
        mode=req.mode,
        scenario_ids=req.scenario_ids,
        max_sessions=req.max_sessions,
        registry=registry,
        audit_store=audit,
    )
    return RunResponse(run_id=run_id, status="running")


@app.get("/run/{run_id}", dependencies=[Depends(require_control_plane_token)])
async def get_run(run_id: str) -> dict[str, Any]:
    audit: AuditStore = app.state.audit_store
    registry: RunRegistry = app.state.run_registry
    info = registry.get(run_id)
    persisted = audit.get_run(run_id)
    if info is None and persisted is None:
        raise HTTPException(404, "run_not_found")
    return {
        "run_id": run_id,
        "live": info.__dict__ if info else None,
        "persisted": persisted,
    }


@app.get("/summary", dependencies=[Depends(require_control_plane_token)])
async def get_summary() -> dict[str, Any]:
    settings = get_settings()
    sim = SimulatorClient(settings.simulator_base_url, settings.candidate_token)
    try:
        return await sim.candidate_summary()
    except SimulatorError as e:
        raise HTTPException(502, f"simulator_error:{e}")


@app.get("/sessions", dependencies=[Depends(require_control_plane_token)])
async def list_sessions(
    run_id: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    audit: AuditStore = app.state.audit_store
    return {"sessions": audit.list_sessions(run_id=run_id, limit=limit)}


@app.get("/sessions/{session_id}", dependencies=[Depends(require_control_plane_token)])
async def get_session(
    session_id: str,
    include_pii: int = 0,
    x_pii_token: str | None = Header(default=None, alias="X-PII-Token"),
) -> dict[str, Any]:
    audit: AuditStore = app.state.audit_store
    pii_requested = include_pii == 1
    if pii_requested:
        expected_pii_token = os.environ.get("PII_TOKEN")
        if not expected_pii_token or x_pii_token != expected_pii_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bad_pii_token")
    payload = audit.get_session(session_id, include_pii=pii_requested)
    if payload is None:
        raise HTTPException(404, "session_not_found")
    return payload
