"""In-process registry of running batch jobs.

Authoritative state lives in audit.db (runs/sessions tables); this is just
a lightweight view used by the API layer for live progress reporting.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunInfo:
    run_id: str
    mode: str
    status: str = "queued"
    scenarios_requested: int | None = None
    scenarios_completed: int = 0
    sessions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunInfo] = {}
        self._lock = threading.Lock()

    def create(self, mode: str, scenarios_requested: int | None) -> RunInfo:
        rid = f"r_{uuid.uuid4().hex[:12]}"
        info = RunInfo(run_id=rid, mode=mode, scenarios_requested=scenarios_requested)
        with self._lock:
            self._runs[rid] = info
        return info

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            info = self._runs.get(run_id)
            if info is None:
                return
            for k, v in fields.items():
                setattr(info, k, v)

    def add_session(self, run_id: str, session_summary: dict[str, Any]) -> None:
        with self._lock:
            info = self._runs.get(run_id)
            if info is None:
                return
            info.sessions.append(session_summary)
            info.scenarios_completed += 1

    def get(self, run_id: str) -> RunInfo | None:
        with self._lock:
            return self._runs.get(run_id)

    def has_running_prod(self) -> bool:
        with self._lock:
            return any(r.mode == "prod" and r.status == "running" for r in self._runs.values())
