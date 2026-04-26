# QuickBites AI Support Agent

A production-style customer-support agent that role-plays QuickBites support
against the QuickBites simulator. Reads structured data from `app.db`,
unstructured policy from `policy_and_faq.md`, and decides on actions
(`issue_refund`, `file_complaint`, `escalate_to_human`, `flag_abuse`,
`close`) for each turn of the conversation.

## Run it

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# 2. Configure
cp .env.example .env
# fill in ANTHROPIC_API_KEY, SIMULATOR_BASE_URL, CANDIDATE_TOKEN, CONTROL_PLANE_TOKEN

# 3. Build the policy index (one-shot)
python -m scripts.build_policy_index

# 4. Smoke test against dev scenarios
python -m scripts.run_dev_eval

# 5. Run the FastAPI control plane
uvicorn service.api:app --host 0.0.0.0 --port 8080
```

Or with Docker:

```bash
docker build -t quickbites-agent .
docker run -p 8080:8080 --env-file .env quickbites-agent
```

## Control-plane API

All routes except `/healthz` require `X-Control-Plane-Token: $CONTROL_PLANE_TOKEN`.

| Method | Path | Purpose |
|---|---|---|
| GET  | `/healthz` | Liveness; reports app_db / audit_db / policy_index status |
| POST | `/run` | Kick off a batch run. Body: `{mode, scenario_ids?, max_sessions?, confirm_prod}` |
| GET  | `/run/{run_id}` | Live + persisted run state |
| GET  | `/summary` | Proxies `/v1/candidate/summary` from the simulator |
| GET  | `/sessions?run_id=` | List audit sessions |
| GET  | `/sessions/{session_id}` | Full audit trail for one session (replay-able) |

```bash
# Dev run
curl -X POST :8080/run \
  -H "X-Control-Plane-Token: $CONTROL_PLANE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"dev","scenario_ids":[101,102,103,104,105]}'

# Prod run (single confirmation gate)
curl -X POST :8080/run \
  -H "X-Control-Plane-Token: $CONTROL_PLANE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"prod","confirm_prod":true}'

# Final score
curl -H "X-Control-Plane-Token: $CONTROL_PLANE_TOKEN" :8080/summary
```

## Architecture (high level)

```
┌──────────────────────────────────────────────────────────┐
│ FastAPI control plane (service/api.py)                   │
│  /run /summary /sessions/:id   ←─── reviewer             │
│       │                                                   │
│       ▼                                                   │
│ Worker (service/worker.py)                                │
│  - drives simulator client loop                           │
│  - per scenario: start → prefetch → loop turns            │
│       │                                                   │
│       ▼                                                   │
│ Orchestrator (agent/orchestrator.py)                      │
│  - injection filter → system prompt assembly →           │
│    LLM tool-use loop → submit_decision → validators      │
│       │                                                   │
│       ├──── tools (agent/tools/*)                         │
│       │     - 6 narrow SQL tools (read-only, parameterized)│
│       │     - policy_lookup (embedding RAG)              │
│       │     - compute_abuse_score                         │
│       │                                                   │
│       └──── audit (audit/store.py) ──→ audit.db          │
└──────────────────────────────────────────────────────────┘
                            │ HTTPS
                            ▼
                ┌─────────────────────────┐
                │ QuickBites simulator     │
                │ /v1/session/start        │
                │ /v1/session/:id/reply    │
                └─────────────────────────┘
```

The simulator is the **server**; this agent is the **client**. The "hosted
URL" required by the assignment is the FastAPI control plane above — it is
what reviewers hit, not what the simulator dials into.

## Testing

```bash
pytest tests/ -v              # 47 unit tests
python -m scripts.run_dev_eval # end-to-end against dev rehearsal scenarios 101–105
```

## Layout

- `agent/` — pure logic (orchestrator, tools, prefetch, abuse, policy index, prompts, decision validators)
- `service/` — FastAPI + worker + simulator client
- `audit/` — separate SQLite-backed audit store
- `scripts/` — one-shots (build_policy_index, init_audit_db, run_dev_eval)
- `tests/` — pytest suites with synthetic fixture DB

See `DESIGN.md` for the full design rationale.
