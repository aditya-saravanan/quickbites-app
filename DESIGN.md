# QuickBites Support Agent — Design Doc

## Problem

Replace QuickBites' frontline support layer with a bot that reads customer
chat, looks up structured facts from `app.db` and unstructured policy from
`policy_and_faq.md`, and emits zero-or-more graded actions per turn. The
simulator role-plays the customer; the bot role-plays QuickBites support.
Grading is by decision quality (refund correctness 30%, refund-amount
adherence 20%, complaint handling 15%, abuse handling 15%, escalation 10%,
clean close 10%).

## Architecture

The system is a single-process Python service with three layers:

1. **`agent/`** — pure logic. The orchestrator wraps a Claude tool-use
   loop. Six narrow SQL tools, one policy-RAG tool, and one abuse-score
   tool are exposed. The model's required final tool call is
   `submit_decision`, which carries the user-facing reply, the structured
   actions array, and an internal reasoning trace.
2. **`service/`** — FastAPI control plane + async worker. The worker
   drives the simulator client loop. The control-plane endpoints
   (`/run`, `/summary`, `/sessions/:id`) are what reviewers hit.
3. **`audit/`** — separate SQLite DB recording every turn (customer
   message, prefetched bundle, every tool call with args + result, raw
   LLM response, parsed actions, reasoning, validation notes, latency,
   token usage). PII is redacted at write time and held in `pii_vault`
   behind a stricter token. The schema supports deterministic replay.

### Data flow per session

```
start_session → parse order_id → build_prefetch_bundle (single SQL fan-out:
  order + items + customer (no PII) + recent orders + complaints + refunds
  + restaurant stats + rider summary + abuse_score) → insert as cached
  block 2 in the system prompt
loop turn:
  injection_filter.scan(customer_msg) → tags
  build system prompt: [block1 cached: role + hard rules + full policy +
    tool catalog + injection paragraph] + [block2 cached: prefetch JSON]
    + [block3 uncached: turn_no, turns_remaining, injection tags]
  wrap user message in <customer_message> with HTML-escaping
  LLM tool-use loop (cap 8 hops):
    tool_use → dispatch via parameterized SQL → tool_result fed back
    submit_decision → run validate_and_clamp → emit
  write audit row → simulator.reply(bot_msg, actions) → next customer_msg
```

## Key design decisions

### Hybrid policy retrieval (inline + lookup tool)

The full ~4KB policy is inlined verbatim in the system prompt as the
source of truth, marked `cache_control: ephemeral` so it's read-cache after
turn 1 (~10× cost reduction on input tokens). A `policy_lookup` tool
backed by sentence-transformers embeddings is also exposed for targeted
re-grounding. The lookup is optional; inline is load-bearing.

**Why not pure inline?** Same accuracy, slightly cheaper, but doesn't
demonstrate the RAG technique. The hybrid pays a tiny constant cost (one
embedding model and a 10-chunk index) for an artifact reviewers can
inspect.

**Why not pure RAG?** The policy has cross-cutting rules ("Hard rules"
apply to every decision; the Resolution Ladder must be evaluated as a
whole, not per-chunk). Top-k retrieval risks recall failures that
translate directly into rubric-graded policy violations. Retrieval
variance also creates a fairness problem — two semantically similar
customer cases with different phrasings could retrieve different chunks
and get different decisions. Inline is the deterministic floor.

### Hybrid data access (prefetch + narrow tools)

On session start, the parser extracts `order_id` from the opening
message and `agent/prefetch.py` runs a fixed bundle of queries. The
result is injected as a cached system-prompt block. For most turns the
agent doesn't need to call any data tool — the bundle already contains
the customer profile, opening order, last 25 orders in a 90-day window,
all complaints, recent refunds, the restaurant's stats, the rider's
incident summary, and a versioned abuse score with fired signals.

When the customer pivots ("actually it's order #423"), the model calls
`lookup_order` with the new id; secondary orders are tracked in
`state.prefetch.secondary_orders` so the refund-cap validator
re-targets the correct order's `total_inr`.

**No free-form SQL is ever exposed to the LLM.** Every tool is a
parameterized template with a typed input schema and a redacted output.

### Rules + LLM hybrid for abuse detection (`agent/abuse.py`)

A pure function evaluates 5 rules against the read-only DB:

| Rule | Weight | Threshold |
|---|---|---|
| `high_complaint_rate` | 0.30 | `complaints/orders ≥ 0.50` AND `orders ≥ 5` |
| `recent_refund_burst` | 0.25 | ≥3 refunds in last 30 days |
| `new_account_with_complaints` | 0.20 | `account_age < 30d` AND `complaints ≥ 2` |
| `repeat_rejection_history` | 0.15 | ≥2 prior rejected complaints |
| `claim_contradicts_data` | 0.10 | LLM-injected signal, signal-only |

Score is the sum of fired weights, clamped to `[0, 1]`. Documented as a
weighted heuristic, not a probability. Threshold to fire: 0.60.

The `0.50` complaint-rate threshold was calibrated against the actual
data: at `0.40`, 32% of customers (16/50) fire — too many. At `0.50`, 10
customers (20%) fire — the cohort that maps to "clearly abusive" in the
data spec. Versioned as `ABUSE_RULES_V1` so future tuning is auditable.

The LLM cannot flag abuse on intuition alone: the validator drops any
`flag_abuse` action whose `abuse_score_used < 0.6`, and the system
prompt requires the model to name ≥2 fired signals in `reasoning`. This
gives reproducibility and a fairness audit trail that a pure-LLM
classifier doesn't.

### Decision validators (`agent/decision_schema.py`) — the policy floor

After every LLM `submit_decision`, the Pydantic-validated decision is
passed through `validate_and_clamp`. This is the last line of defense
against prompt injection and model error:

- **Refund > order total → clamped to total** (per-order sum across
  multiple `issue_refund` actions). Logged as `validation_clamp` rather
  than escalated, so a near-miss isn't punished twice.
- **`flag_abuse` with `abuse_score_used < 0.6` → dropped** with
  `validation_dropped_flag` note.
- **Phone/email patterns in `response` → redacted** to `[REDACTED-PHONE]`
  / `[REDACTED-EMAIL]` (defense-in-depth for the policy rule "never
  reveal PII or internals").
- **`close` runs last** when co-emitted with refund/complaint actions.

### Prompt-injection defense (4 layers)

1. **Regex tripwire** (`agent/injection_filter.py`): tags but doesn't
   drop. Patterns for `ignore_prior`, `role_injection`, `role_override`,
   `policy_override`, `tag_smuggling`, `large_refund_demand`,
   `reveal_instructions`. Audit-friendly negative cases included
   ("ignore the dish I got" must not fire `ignore_prior`).
2. **HTML-escaping** of any tag-smuggling fragments before wrapping the
   customer message in `<customer_message turn="N">…</customer_message>`.
3. **System prompt anchoring** (`agent/prompts.py`): the cached block 1
   includes a paragraph telling the model that anything inside
   `<customer_message>` tags is untrusted user data.
4. **Per-turn re-anchor** in the uncached block 3: when injection tags
   are present, the prompt explicitly cites them and tells the model to
   re-anchor on policy.

Even if all four layers fail, the **refund-cap validator** prevents
large-refund extraction independently of the model's reasoning.

### Auditability (`audit/store.py`)

Three tables: `runs`, `sessions`, `turns` (+ `pii_vault`). Every turn
captures: `customer_msg` (redacted), `bot_msg`, `injection_flags_json`,
full `tool_calls_json`, raw `llm_responses_json`, `parsed_actions_json`,
`reasoning`, `confidence`, `abuse_score`, `abuse_signals_json`,
`validation_notes_json`, `latency_ms`, full token-usage breakdown, and
any `error`. The `prefetch_json` and `agent_version` (git sha) on the
session row enable deterministic replay.

The `/replay/{session_id}` endpoint is reserved for re-running saved
customer messages through the *current* agent code without calling the
simulator — the right tool for validating prompt or rule changes
against historical sessions before deploying new logic to prod runs.

## Limitations and next steps

1. **Single LLM call per hop.** No self-consistency / multi-sample
   voting. With a stronger budget I'd run 3 samples and majority-vote
   on the actions, breaking ties in favor of escalation.
2. **Embedding model is loaded eagerly.** Cold-starts in Docker pre-build
   the index; in production this should be a separate sidecar or the
   chunks should be HNSW-indexed in `sqlite-vec`.
3. **Abuse rule version is hardcoded.** A real deployment needs A/B
   threshold experiments through the `/replay` endpoint, gated by a
   shadow-mode flag that logs flags without acting on them.
4. **No structured eval harness.** The dev rehearsal scenarios (101–105)
   are run via `scripts/run_dev_eval.py` which prints per-scenario
   summaries; a regression suite would assert specific action types per
   scenario and fail PRs that lower the score.
5. **No streaming UI.** Reviewers see audit trails after sessions
   complete; in production this should stream turn-by-turn.

## Tradeoffs not taken

- **Free-form SQL**: rejected. Risks malformed queries, prompt-injection
  SQL, harder to audit individual decisions.
- **Pure-LLM abuse classifier**: rejected. Opaque, varies turn-to-turn,
  can't be A/B tested or defended in a fairness audit.
- **OpenTelemetry tracing**: skipped for this MVP — the audit log
  captures everything OTel would, in a domain-specific schema.
- **Multi-service deployment**: skipped. A single FastAPI process is the
  right shape for 22 graded scenarios; splitting RAG / decision /
  worker into microservices would be premature.
