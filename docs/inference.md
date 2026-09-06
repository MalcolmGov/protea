# Inference — engine, facade, wiring Zara

Design decisions are in `adr/ADR-009-inference.md`. Container definitions are in `deployment/protea/`.

## Components

| Component | Runs on | Command / image | Port |
|---|---|---|---|
| Engine (vLLM OpenAI server) | GPU host | `ghcr.io/malcolmgov/protea-infer` — `protea serve vllm --run` inside | 8000 |
| Facade (auth, validation gate, ops endpoints) | any CPU | `ghcr.io/malcolmgov/protea-facade` — `protea serve facade` | 8080 |
| Storage helper | job hosts | `protea-storage push|pull` | – |

`deployment/protea/docker-compose.yml` runs both on one GPU box; the facade waits for the engine's health check.

## Facade endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | no | liveness |
| GET | `/readyz` | no | 200 only when the backend answered a probe within `ready_ttl_s`; 503 while draining |
| GET | `/metrics` | no | Prometheus text: requests, latency histogram, tokens, backend errors, gate outcomes, in-flight |
| GET | `/v1/models` | bearer | served model name and aliases |
| POST | `/v1/chat/completions` | bearer | OpenAI wire format: messages, tools, `response_format`, `stream` |
| POST | `/v1/generate` | bearer | native `GenerationRequest` → `GenerationResponse` |
| POST | `/v1/generate/structured` | bearer | `{request, schema, max_repairs?}` → `{valid, output, errors, repairs}`; 422 when still invalid |
| POST | `/v1/route/generate`, `/v1/route/structured` | bearer | routed variants (Phase 7): the policy picks the model, falls back on invalid output, returns the decision and confidence; mounted when `routing_policy` is set — see `routing.md` |

Headers: `Authorization: Bearer <PROTEA_FACADE_TOKEN>`, optional `x-protea-tenant` (hashed before it reaches usage events), optional `x-request-id`.

## Wiring aria

TypeScript runtime (agent_runtime): set

```
MIAI_MODEL_MODE=gateway
MIAI_MODEL_GATEWAY_URL=https://<facade-host>/v1
MIAI_MODEL_GATEWAY_KEY=<PROTEA_FACADE_TOKEN>
```

Python layer (`llm/`, the Phase 6 compiler): `PROTEA_INFERENCE_URL=https://<facade-host>/v1`, `PROTEA_INFERENCE_TOKEN=<PROTEA_FACADE_TOKEN>`, then `build_provider("protea")`. Pointing straight at the engine (`http://<gpu-host>:8000/v1` with the engine token) also works but bypasses the validation gate and the facade metrics.

Use the facade's `aliases` list to accept the model names aria already sends (for example `gpt-4o-mini`) during migration; the facade reports `protea-agent` back.

## Local check without a GPU

```bash
export PROTEA_FACADE_TOKEN=dev-token
protea serve facade --backend mock --check          # builds the app, probes the backend, prints readiness
protea serve facade --backend mock --port 8080      # serves; try: curl -H "Authorization: Bearer $PROTEA_FACADE_TOKEN" localhost:8080/v1/models
protea serve vllm                                    # prints the engine command for configs/inference/vllm-qwen3-8b.yaml
```

## GPU host (execution boundary)

Qwen3-8B at bf16 with an 8k context fits a 24 GB GPU (L4, A10G, RTX 4090); use `quantization: fp8` or `awq` for 16 GB. The first start downloads about 16 GB of weights into the HF cache volume. Steps:

1. `docker compose -f deployment/protea/docker-compose.yml up -d` with `PROTEA_INFERENCE_TOKEN`, `PROTEA_FACADE_TOKEN` and `HF_TOKEN` in the environment; `PROTEA_ADAPTER_PATH=/adapters/<run>/adapter` once a Protea adapter exists.
2. `curl localhost:8080/readyz` until it returns 200.
3. `protea evaluate run --provider protea --model protea-agent --confirm` for the Phase 3 baseline (or post-train score) through the facade.
4. Point aria at the facade as above; watch `/metrics` and the usage events.

Shutdown: `docker stop` (60 s grace) or a Kubernetes preStop; both processes drain in-flight requests first.

## Not in this phase

The Zara-specific routes (`/v1/agent/generate|repair|optimize`, `/v1/workflow/generate`, `/v1/tools/select`) wrap the Agent Compiler and are built in aria in Phase 6 on top of `/v1/generate/structured`.
