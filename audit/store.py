"""AuditStore: thin SQLite-backed persistence for runs/sessions/turns."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.orchestrator import AuditRecord
from agent.session_state import SessionState

from .redact import redact_dict_strings, redact_text

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def create_run(
        self, run_id: str, mode: str, scenarios_requested: int | None, notes: str | None = None
    ) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO runs (run_id, mode, started_at, status, scenarios_requested, notes) "
                "VALUES (?, ?, ?, 'running', ?, ?)",
                (run_id, mode, _now_iso(), scenarios_requested, notes),
            )

    def finish_run(self, run_id: str, status: str) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "UPDATE runs SET status=?, finished_at=? WHERE run_id=?",
                (status, _now_iso(), run_id),
            )

    def increment_run_progress(self, run_id: str) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "UPDATE runs SET scenarios_completed = scenarios_completed + 1 WHERE run_id=?",
                (run_id,),
            )

    def has_running_prod(self) -> bool:
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT 1 FROM runs WHERE mode='prod' AND status='running' LIMIT 1"
            ).fetchone()
            return row is not None

    def count_prod_sessions(self) -> int:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT COUNT(*) FROM sessions WHERE mode='prod'").fetchone()
            return int(row[0] or 0)

    def open_session(
        self, *, run_id: str, state: SessionState, prefetch_json: dict[str, Any], agent_version: str, rule_version: str
    ) -> None:
        redacted_prefetch = redact_dict_strings(prefetch_json)
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO sessions (session_id, run_id, scenario_id, mode, opened_at, "
                "max_turns, prefetch_json, agent_version, rule_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    state.session_id,
                    run_id,
                    state.scenario_id,
                    state.mode,
                    _now_iso(),
                    state.max_turns,
                    json.dumps(redacted_prefetch, ensure_ascii=False),
                    agent_version,
                    rule_version,
                ),
            )

    def close_session(
        self,
        *,
        session_id: str,
        close_reason: str | None,
        final_score: dict[str, Any] | None,
    ) -> None:
        score_int: int | None = None
        if isinstance(final_score, dict):
            for key in ("score", "total", "aggregate"):
                if key in final_score and isinstance(final_score[key], (int, float)):
                    score_int = int(final_score[key])
                    break
        with self._lock, self._connect() as c:
            c.execute(
                "UPDATE sessions SET closed_at=?, close_reason=?, final_score=?, final_score_json=? "
                "WHERE session_id=?",
                (
                    _now_iso(),
                    close_reason,
                    score_int,
                    json.dumps(final_score) if final_score is not None else None,
                    session_id,
                ),
            )

    def write_turn(self, audit: AuditRecord) -> None:
        cust_msg, pii = redact_text(audit.customer_message)
        bot_msg, _ = redact_text(audit.bot_message or "")
        with self._lock, self._connect() as c:
            c.execute(
                """
                INSERT INTO turns (
                    session_id, turn_number, ts, customer_msg, bot_msg,
                    injection_flags_json, tool_calls_json, llm_responses_json,
                    parsed_actions_json, reasoning, confidence,
                    abuse_score, abuse_signals_json, validation_notes_json,
                    latency_ms, input_tokens, output_tokens,
                    cache_read_tokens, cache_write_tokens, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    audit.session_id,
                    audit.turn_number,
                    _now_iso(),
                    cust_msg,
                    bot_msg,
                    json.dumps(audit.injection_flags),
                    json.dumps(audit.tool_calls, ensure_ascii=False),
                    json.dumps(audit.llm_responses, ensure_ascii=False)[:200000],
                    json.dumps(audit.parsed_actions),
                    audit.reasoning,
                    audit.confidence,
                    audit.abuse_score,
                    json.dumps(audit.abuse_signals) if audit.abuse_signals is not None else None,
                    json.dumps(audit.validation_notes),
                    audit.latency_ms,
                    audit.usage.input_tokens,
                    audit.usage.output_tokens,
                    audit.usage.cache_read_tokens,
                    audit.usage.cache_write_tokens,
                    audit.error,
                ),
            )
            for field, raw in pii:
                c.execute(
                    "INSERT OR REPLACE INTO pii_vault (session_id, turn_number, field, value) "
                    "VALUES (?, ?, ?, ?)",
                    (audit.session_id, audit.turn_number, field, raw),
                )

    def list_sessions(self, *, run_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as c:
            if run_id:
                rows = c.execute(
                    "SELECT session_id, run_id, scenario_id, mode, opened_at, closed_at, "
                    "close_reason, final_score FROM sessions WHERE run_id=? "
                    "ORDER BY opened_at DESC LIMIT ?",
                    (run_id, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT session_id, run_id, scenario_id, mode, opened_at, closed_at, "
                    "close_reason, final_score FROM sessions "
                    "ORDER BY opened_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_session(self, session_id: str, *, include_pii: bool = False) -> dict[str, Any] | None:
        with self._lock, self._connect() as c:
            session = c.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None:
                return None
            turns = c.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_number ASC",
                (session_id,),
            ).fetchall()
            pii = []
            if include_pii:
                pii = [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM pii_vault WHERE session_id = ?", (session_id,)
                    ).fetchall()
                ]
            return {"session": dict(session), "turns": [dict(t) for t in turns], "pii": pii}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as c:
            r = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if r is None:
                return None
            sessions = c.execute(
                "SELECT session_id, scenario_id, close_reason, final_score "
                "FROM sessions WHERE run_id = ? ORDER BY opened_at ASC",
                (run_id,),
            ).fetchall()
            return {"run": dict(r), "sessions": [dict(s) for s in sessions]}
