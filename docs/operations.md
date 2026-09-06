# Operations

Runbooks. Everything here is a command in this repository or a file under review; nothing needs console access to a
cloud provider except the GPU host itself.

## Daily

| Check | Command / place | Healthy when |
|---|---|---|
| Facade up and ready | `GET /readyz`; `protea_backend_ready 1` on `/metrics` | 200, backend probe within `ready_ttl_s` |
| Error and fallback rates | `protea_route_total{ok="false"}`, `protea_route_fallbacks_total`; aria `/v1/observability/summary?scope=platform` | below the rollback triggers |
| Latency | `protea_request_seconds` histogram per route; aria summary `latency_ms_p95` | p95 under the latency budget |
| Canary state | `protea release canary` | matches the release log and the last reviewed policy change |
| Feedback | aria `/v1/observability/feedback` | negative share below 0.30 per model version |

## Promote a model

1. Commit its ZaraBench report (`evaluate run --provider protea --confirm`, with the judge) and security report
   (`security run --provider protea --confirm`).
2. `protea release check <key>` — fix every blocker it names.
3. Promote step by step (`deployment.md`); soak each canary step for `observe_minutes`, running `release watch` on the
   latest observability summary before the next step.
4. Open the PR with the registry, policy and reports changes; production is the merge.

## Roll back

1. `protea release rollback --reason "<what breached>"` (or `release watch --rollback`).
2. Confirm `canary_percent: 0` on the routing policy and the registry shows the previous production model.
3. Redeploy the facade only if the served adapter changed; the router reads the policy at start.
4. Post-incident: add a probe to the security suite or a task family to ZaraBench that would have caught it.

## Rotate credentials

- Facade token: set a new `PROTEA_FACADE_TOKEN`, restart the facade, then update aria's `MIAI_MODEL_GATEWAY_KEY` and
  `PROTEA_FACADE_TOKEN`. Old token invalid immediately.
- Engine token: new `PROTEA_INFERENCE_TOKEN` on the engine and the facade together (compose restart).
- Provider keys (frontier routes): rotate in the facade environment; `protea providers list` shows what is configured.

## Backups and retention

- Registry files, routing and release configs, reports and locks live in git; the repository is the backup.
- Adapters and checkpoints: `protea-storage push` to the configured bucket after each accepted run; the registry
  entry's `artifact_sha256` verifies a restore.
- Route events (`route_events` JSONL) and aria's `api_usage` / `audit_events` / `model_feedback`: retain 90 days,
  identifiers only; no prompt text is stored.

## Incident classes

| Symptom | First action |
|---|---|
| Facade 503 on `/readyz` | engine health: `docker compose logs protea-infer`; GPU memory; restart engine, facade recovers on its own |
| Validation pass rate dropping on the Protea route | `release watch`; roll back the canary; compare the report of the served adapter with the promoted one (`artifact_sha256`) |
| Fallback rate rising | provider errors on `/metrics` (`protea_backend_errors_total`); check the frontier provider status and keys |
| Cost spike | aria summary `by_model` cost; a fallback storm sends Protea traffic to frontier models — roll back and investigate |
| Suspected data leak in answers | run `security run --provider protea` against the served model; roll back on any family below the floor |
