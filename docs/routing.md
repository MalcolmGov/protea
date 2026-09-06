# Routing — which model serves which task

Design decisions are in `adr/ADR-010-router.md`. The policy lives in `configs/routing/zara-v0.yaml`.

## How a call is routed

1. **Classify.** `task_type` from request metadata, else from shape (tools → `tool_calling`, schema → `structured_output`) and a phrase list; complexity `low | medium | high` from prompt length, tool and connector counts and financial signals.
2. **Constrain.** `privacy: strict` keeps only self-hosted routes (none → refuse). Candidates that do not declare the task type or whose complexity ceiling is exceeded are dropped.
3. **Check evidence.** A benchmark-gated route (Protea) needs a committed ZaraBench report scoring the task's category at or above its threshold, and the tenant must be inside the canary share.
4. **Prefer.** Survivors are ordered by the cost policy (`cheapest`, `balanced`, `best`); a `pinned_route` wins if it survived. Nothing eligible → the policy's default route.
5. **Execute with fallback.** The chosen route, then the fallback order, up to `max_attempts`. Invalid structured output (after gate repairs), provider errors, bad finish reasons and low confidence all trigger the next route.
6. **Score and record.** Confidence from evidence (gate outcome, declared tools, benchmark score, recent success rate); a route event with every fallback goes to the configured sink.

## Policy file

```yaml
candidates:
  - name: protea-agent          # route id used for pinning and events
    provider: protea            # any provider from `protea providers list`
    model: protea-agent
    self_hosted: true           # eligible under privacy: strict
    cost_tier: 0                # 0 free/self-hosted … 5 most expensive
    quality_tier: 3
    requires_benchmark: true    # needs a report in reports_dir
    task_types: [tool_calling, structured_output, …]
    version: "0.1"
default_route: frontier-sonnet  # must not require a benchmark
fallback_order: [frontier-sonnet, frontier-opus, frontier-openai]
thresholds: {tool_calling: 0.80, structured_output: 0.85}
default_threshold: 0.75
canary_percent: 0               # share of tenants allowed onto benchmark-gated routes
min_confidence: 0.6
max_attempts: 3
```

Validate with `protea config validate configs/routing/zara-v0.yaml`.

## Commands

```bash
protea route explain tool_calling --tools 5 --tenant acme       # decision + every candidate's verdict, no model call
protea route explain chat --privacy strict                       # exit 1: no self-hosted route serves chat
protea route explain structured_output --pin protea-agent --json
protea route matrix                                              # category scores per model from evaluation/reports
```

## Facade endpoints

Set `routing_policy: configs/routing/zara-v0.yaml` in the serve config (and optionally `route_events: /var/log/protea/routes.jsonl`). The facade then mounts:

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/v1/route/generate` | `{request, task_type?, privacy?, cost_policy?, connector_count?, pinned_route?}` | `{ok, route, served_route, served_model, reason, attempts, fallbacks, confidence, response}` |
| POST | `/v1/route/structured` | the same plus `schema` and `max_repairs?` | the same plus `{valid, output, errors, repairs}` |

Status codes: 200 accepted answer; 422 every route answered but none passed validation or confidence (the last answer is returned); 502 every route failed; 409 policy forbids all routes; 501 routing not configured.

The facade's own backend serves any candidate whose provider name matches it, so a `protea` candidate reuses the engine connection; other candidates are built from environment credentials on first use.

## Turning Protea on

1. Run ZaraBench against the served model and commit the report under `evaluation/reports/` (`protea evaluate run --provider protea`).
2. `protea route matrix` shows the category scores; `protea route explain <task>` shows which categories now pass their thresholds.
3. Raise `canary_percent` in a reviewed policy change (5 → 25 → 100) and watch `protea_route_total{route="protea-agent",ok=…}` and `protea_route_fallbacks_total` on `/metrics`, plus the route events.
4. Rollback is `canary_percent: 0` or removing the task type from the candidate; pinned agents keep their route only while it still passes the gate.

## Events

`RouteEvent` fields: `request_id, created_at, task_type, complexity, privacy, tenant_ref (hashed), canary, pinned, chosen_route, served_route, served_model, reason, attempts, fallbacks[{from_route, to_route, reason, attempt}], confidence, gate_valid, latency_ms, input_tokens, output_tokens, ok, error`. No prompt or completion text is ever recorded. Usage events from the provider layer carry `fallback: true` on fallback attempts.
