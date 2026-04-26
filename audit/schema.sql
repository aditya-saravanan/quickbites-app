-- Audit DB schema. Separate from app.db (read-only) so writes never affect
-- the source of truth. Designed for replay: a session can be deterministically
-- re-run from (prefetch_json, agent_version, turns.customer_msg ordered).

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    mode                TEXT NOT NULL CHECK (mode IN ('dev','prod')),
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    status              TEXT NOT NULL,
    scenarios_requested INTEGER,
    scenarios_completed INTEGER NOT NULL DEFAULT 0,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    scenario_id    INTEGER,
    mode           TEXT NOT NULL,
    opened_at      TEXT NOT NULL,
    closed_at      TEXT,
    close_reason   TEXT,
    max_turns      INTEGER,
    final_score    INTEGER,
    final_score_json TEXT,
    prefetch_json  TEXT NOT NULL,
    agent_version  TEXT NOT NULL,
    rule_version   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           TEXT NOT NULL REFERENCES sessions(session_id),
    turn_number          INTEGER NOT NULL,
    ts                   TEXT NOT NULL,
    customer_msg         TEXT NOT NULL,
    bot_msg              TEXT,
    injection_flags_json TEXT,
    tool_calls_json      TEXT NOT NULL,
    llm_responses_json   TEXT NOT NULL,
    parsed_actions_json  TEXT NOT NULL,
    reasoning            TEXT,
    confidence           REAL,
    abuse_score          REAL,
    abuse_signals_json   TEXT,
    validation_notes_json TEXT,
    latency_ms           INTEGER,
    input_tokens         INTEGER,
    output_tokens        INTEGER,
    cache_read_tokens    INTEGER,
    cache_write_tokens   INTEGER,
    error                TEXT,
    UNIQUE (session_id, turn_number)
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_run ON sessions(run_id);
CREATE INDEX IF NOT EXISTS idx_sessions_scenario ON sessions(scenario_id);

CREATE TABLE IF NOT EXISTS pii_vault (
    session_id   TEXT NOT NULL,
    turn_number  INTEGER NOT NULL,
    field        TEXT NOT NULL,
    value        TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_number, field)
);
