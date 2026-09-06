# ADR-009 — Inference: vLLM engine, facade, validation gate

**Status:** accepted · **Date:** 2026-09-06 · **Phase:** 5

## Context

The specification wants a production inference service with an OpenAI-compatible surface, health, readiness and metrics endpoints, graceful shutdown, and a validation gate in front of every structured answer (§28–§31, §33). aria's TypeScript runtime already calls any OpenAI-compatible gateway (`MIAI_MODEL_GATEWAY_URL` + `MIAI_MODEL_GATEWAY_KEY`), and the Python layer uses the `protea` provider from Phase 1. The Zara-specific routes (`/v1/agent/generate|repair|optimize`, `/v1/workflow/generate`, `/v1/tools/select`) wrap the Agent Compiler, which is Phase 6 and lives in aria (ADR-001).

## Decisions

1. **Two processes, one contract.** The engine is vLLM's OpenAI server (`Dockerfile.infer`), rendered from `InferenceConfig` by `protea serve vllm` so the container and the docs never disagree on flags: served model name, dtype, context length, quantization, the Hermes tool-call parser, xgrammar guided decoding, an optional LoRA adapter mounted at `/adapters`, and an API key from the environment. The facade (`Dockerfile.facade`, CPU) sits in front and is what callers see.
2. **The facade speaks both dialects.** `/v1/chat/completions` (OpenAI wire format, streaming included) so aria's runtime, gateways and SDKs can switch by changing a URL; `/v1/generate` and `/v1/generate/structured` (the native contract) for the Python layer and the future Zara routes. The backend is any `ModelProvider`, so the same facade can front vLLM, a frontier provider during migration, or the mock in tests.
3. **The validation gate is a first-class endpoint.** `/v1/generate/structured` validates the model's JSON against the caller's schema and sends the validator's messages back for one repair round by default. A still-invalid answer is a 422 with the errors and the raw text; the facade never returns unvalidated structure as if it were valid. Gate outcomes and repair counts are metrics.
4. **Operations endpoints are honest.** `/healthz` is liveness. `/readyz` probes the backend (cached for `ready_ttl_s`) and returns 503 until the engine answers; it also flips to 503 while draining. `/metrics` is Prometheus text: requests by route and status, latency histogram per route, tokens in/out, backend errors, gate outcomes, in-flight gauge, backend readiness. No metrics library dependency.
5. **Graceful shutdown end to end.** The engine entrypoint forwards SIGTERM to vLLM and waits; the facade's lifespan marks itself draining, refuses new requests with 503 and `Connection: close`, and waits up to `drain_timeout_s` for in-flight requests; uvicorn's graceful timeout matches. Compose and the Kubernetes Job carry matching stop grace periods.
6. **Auth and tenancy.** The facade requires a bearer token (`PROTEA_FACADE_TOKEN`, constant-time compare) unless a deployment opts out; the engine has its own token. A caller's `x-protea-tenant` header is hashed before it enters request metadata, so usage events never carry raw tenant identifiers (ADR-002).
7. **Storage helper ships with the images.** `protea-storage push|pull` wraps `aws s3 sync`, `azcopy sync` and `rsync`, which is what the Phase 4 job scripts call; the training image (`Dockerfile.train`) installs the AWS CLI and rsync so those scripts work unchanged.

## Consequences

- aria needs three environment variables to route traffic through Protea: `MIAI_MODEL_MODE=gateway`, `MIAI_MODEL_GATEWAY_URL=https://<facade>/v1`, `MIAI_MODEL_GATEWAY_KEY=<facade token>`; nothing in its code changes. The `protea` provider on the Python side points `PROTEA_INFERENCE_URL` at the facade or straight at the engine.
- Running the engine is an execution boundary: it needs a 24 GB-class GPU for Qwen3-8B at bf16 (or FP8/AWQ on 16 GB), downloads the base weights, and costs GPU time. `protea serve vllm` prints the command; `--run` executes it only on a host with vllm installed.
- The facade adds a hop and a JSON re-encode per request; measured overhead is sub-millisecond on the mock backend and will be reported against the engine when the first GPU host exists.
- Tests prove the whole chain without a GPU: the `protea` provider against a mock vLLM server that speaks the OpenAI wire format (tools, json_schema, streaming, auth), and the facade in front of that provider.
