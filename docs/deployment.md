# Deployment

How Protea reaches a serving host and how a model reaches traffic. Runbooks for the day-to-day are in
`operations.md`; container definitions in `deployment/protea/`.

## Topology

```
aria (Hetzner, EU)  ──HTTPS──▶  protea-facade (CPU, port 8080)  ──private──▶  protea-infer (vLLM, GPU, port 8000)
                                    │ /v1/route/*  (routing policy, canary, fallback to frontier providers)
                                    └ /metrics, /readyz, route events (JSONL)
```

- **Facade** — `ghcr.io/malcolmgov/protea-facade`, `protea serve facade --config configs/serve/facade.yaml`. Set
  `routing_policy` in the serve config to mount `/v1/route/*`; `route_events` for the JSONL sink.
- **Engine** — `ghcr.io/malcolmgov/protea-infer`, the command printed by `protea serve vllm`; mounts the adapter from
  the registry checkpoint at `/adapters`. Needs a 24 GB-class GPU for Qwen3-8B bf16 (execution boundary).
- **Compose** — `deployment/protea/docker-compose.yml` runs both on one GPU box with matching stop grace periods.

## Release pipeline

```
train ─▶ validate ─▶ zarabench ─▶ security ─▶ compare production ─▶ candidate ─▶ staging ─▶ canary 5→25→100 ─▶ production
```

```bash
protea release check protea-agent-0.1.0                 # every stage with its evidence; exit 1 when blocked
protea release promote protea-agent-0.1.0 --to candidate
protea release promote protea-agent-0.1.0 --to staging
protea release promote protea-agent-0.1.0 --to canary   # canary_percent = first step on configs/routing/zara-v0.yaml
protea release canary --step                            # next step after the soak (observe_minutes)
protea release promote protea-agent-0.1.0 --to production   # only at canary 100 %
```

Evidence the checks read: `registry/models.json` (checkpoint, artifact hash, commit, model card),
`registry/datasets.json` (dataset hash), `evaluation/reports/zarabench-0.1/` and `evaluation/reports/security-0.1/`
(latest non-mock report whose model matches the registry key), the routing policy. Explicit report paths can be
passed (`--zarabench`, `--security`, `--base`, `--frontier`). Every action appends to `registry/release-log.jsonl`.

Each promotion is a commit: the registry and the routing policy are files, reviewed like code.

## Rollback

```bash
protea release rollback --reason "p95 above budget"     # canary 0, production model deprecated, previous reinstated
protea release watch summary.json [--rollback]          # evaluate the triggers against an observability window
```

Triggers (release config `rollback`): validation pass rate below 0.90, fallback rate above 0.10, error rate above
0.02, p95 above 8 000 ms, negative feedback share above 0.30 — evaluated only on windows with at least 50 calls.

## Load test

```bash
protea serve loadtest --url http://127.0.0.1:8080 --concurrency 16 --requests 500 --latency-budget-ms 4000
protea serve loadtest --url https://facade.example --confirm ...   # remote: the backend may bill per token
```

Record the p95 and output tokens/s in the economics config (`gpu.output_tokens_per_second`) once measured on the
real engine; until then the value there is an assumption.
