# Protea v0 — Implementation Roadmap

**Date:** 2026-09-06 · **Status:** Phases 0–5 implemented (see `README.md` status table); Phases 6–10 planned. Baselines (Phase 3 step 5), the first QLoRA launch (Phase 4 step 4) and the first GPU engine host (Phase 5) await confirmation.
Status vocabulary used throughout: `implemented` · `partially implemented` · `planned` · `blocked`.

## 0. Phase 0 outcome

| Deliverable | Status |
|---|---|
| Repository audit | implemented — `repository-audit.md` |
| Architecture mapping + proposal | implemented — `architecture-proposal.md` |
| Security / data review | implemented — `data-risk-assessment.md` |
| Model provider analysis | implemented — audit §1.9 |
| Protea integration plan | implemented — this document |
| Training | not started (by design) |

## 1. Data opportunity (measured, not invented)

| Source | Raw units | Usable after dedup/review (estimate) | Dataset |
|---|---|---|---|
| `aria/data/agents` 504 packages | 504 specs, 2.57 M tokens | ≈104 family-unique specs + 400 localized variants (kept as augmentation, split by family) | agent_generation, structured_output |
| `miai-agents` 69 authored packages | 69 specs, 273 tools, 1,110 evals | ≈65 unique (4 overlap families), ≈1,000 evals | agent_generation, tool_calling |
| Catalogue evals | 7,687 | ≈2,500–4,000 tool-related after contamination filter (`tool`, `no_tool`, `tool_none`, `tool_any` ≈ 43% of authored evals) | tool_calling, safety, hallucination |
| Presets + connector registries | 221 bindings, 14 + ~15 connector definitions | ≈220 connector-selection examples + synthetic variants | connector_selection |
| Flagship AgentSpecs + workforces | 15 + 6 | 21 native v2 examples with skills/tools/connectors/workflow/ROI | agent_generation, workflow_generation |
| Capability dependency graph, archetypes | ~10 capabilities × prerequisites | rule-driven synthetic seeds (hundreds) | business_reasoning, tool_calling (when NOT to call) |
| Routing corpus | 887 utterance lines | ≈800 labelled intents | router task classifier |
| Workflow generation | thin: 2 template workflows + 15 flagship | needs synthetic generation | workflow_generation |
| Agent repair / optimization | **none exists** | must be synthesised from packages + fault injection (trace + broken config → fix) | recovery |
| Business reasoning (§11 domains) | catalogue covers 20+ sectors × 5 markets | seeds exist; synthetic expansion required for outcome-level reasoning | business_reasoning |

Rough seed volume before synthetic augmentation: **≈1,500–2,500 traceable examples**, ≈1.5–2 M training tokens after formatting. Quality and coverage of tool-failure, repair and workflow cases matter more than volume; synthetic generation targets those gaps first.

## 2. Training readiness — what must happen before the first run

1. AgentSpec v3 schema + JSON Schema export + converters (Phase 1).
2. Extractors with allowlist paths, classifier, secret/PII/brand scrub, contamination check, family-aware dedup, manifests, dataset card (Phase 2).
3. Golden set (≥150 tasks across the 10 ZaraBench categories) sealed and hash-pinned before any training data is finalised (Phase 3). — implemented: 206 tasks, `evaluation/zarabench/0.1/golden.lock`, verified in CI.
4. ZaraBench harness with mock provider tests passing; baseline runs of the unmodified candidate base model and at least one frontier model, stored in `evaluation/reports` (Phase 3). — harness implemented (ADR-007); baselines pending confirmation (`docs/zarabench.md`).
5. Training configs (YAML), tiny-model dry run on CPU, remote job adapter with `--dry-run` cost display and explicit confirmation (Phase 4). — implemented (ADR-008): offline smoke in CI, four remote adapters, `--confirm` boundary.
6. Experiment tracking + model registry entries created for the run (Phase 4). — implemented: `metrics.jsonl` + optional MLflow, `protea train register`.

## 3. Infrastructure requirement

| Runs locally / in CI (no GPU) | Requires cloud GPU |
|---|---|
| Dataset pipeline, classifiers, scanners, dedup, validators, manifests, cards | Baseline inference of an 8B–14B model at bf16 (needs ≥20 GB VRAM; 24 GB L4/A10G/L40S class is enough with FP8/AWQ) |
| ZaraBench harness against frontier APIs and the mock provider | QLoRA fine-tune of 8B (fits 24 GB; 48–80 GB A6000/A100 is comfortable and faster) |
| Tiny-model smoke training (`Qwen2.5-0.5B-Instruct` or `SmolLM2-360M`) on CPU to prove the trainer, checkpoint/resume, config immutability | vLLM serving for ZaraBench post-train and the staging endpoint (L4 24 GB for 8B quantized) |
| Router, compiler, validation, provider adapters, API tests | Frontier-model synthetic generation is API-only (no GPU) but costs tokens; needs explicit budget approval |
| Terraform/Bicep generation, Dockerfiles, compose files | Nothing on the Hetzner CPX22 (4 GB RAM) |

Indicative v0 experiment cost (estimates, to be confirmed by `protea train remote --dry-run`): one QLoRA run of ≈2–3 k examples × 3 epochs on a rented A100-80GB ≈ 2–4 GPU-hours; baseline + post-train ZaraBench ≈ 2 GPU-hours; total in the order of **USD 10–40** per experiment at 2026 marketplace rates, plus frontier API spend for baselines and synthetic data.

## 4. Recommended first model experiment

| Item | Recommendation | Rationale |
|---|---|---|
| Category | 7B–9B dense, open weights | Cheapest credible tool-calling/JSON model; fits 24 GB for QLoRA and serving |
| Primary candidate | `Qwen/Qwen3-8B` (Apache-2.0, 8.2 B, vLLM-native, strong function calling and JSON, 100+ languages incl. Swahili/Afrikaans coverage to be tested) | Verified on the Hub 2026-09-06; permissive licence; `-Base` checkpoint available for later continued pretraining |
| Scale-up candidate | `Qwen/Qwen3-14B` (Apache-2.0) | Same family; run only if the 8B ZaraScore plateaus |
| Alternatives to benchmark | `openai/gpt-oss-20b` (Apache-2.0, MoE 3.6 B active, strong tool use; fine-tuning stack less mature), `mistralai/Mistral-Small-3.1-24B-Instruct` (Apache-2.0, multilingual, larger), `meta-llama/Llama-3.1-8B-Instruct` (Llama licence, gated, naming obligations), `google/gemma-3-12b-it` (Gemma licence, gated), `microsoft/phi-4` 14B (MIT, English-centric, weaker tool calling) | Scored in `model-selection.md` (Phase 1) against §7 criteria; newer releases since the 2026-06 knowledge cutoff must be re-checked on the Hub at that time |
| Method | QLoRA (4-bit NF4, rank 16–32, alpha 32–64, dropout 0.05, all linear projections), 2–3 epochs, seq len 4–8 k, cosine LR ≈1e-4 — all from YAML | Cheapest reproducible PEFT; adapters hot-loadable in vLLM |
| Dataset | `agent-training-0.1.x` = agent_generation + structured_output + tool_calling + connector_selection; workflow_generation and recovery once synthetic pipeline is validated | Matches §64 priorities |
| Gate | ZaraBench golden set; release only if ZaraScore ≥ base model on every priority category and ≥ frontier baseline on structured-output validity | §23, §67, §70 |

## 5. Build plan — exact sequence

Each phase begins with inspect → findings → ADR → file list → incremental implementation → tests → docs → summary (§72).

### Phase 1 — Foundation (`aria`)
1. Port `docs/*` from this branch; add `docs/README.md`, `adr/ADR-006-zaralm-location.md`, `adr/ADR-007-agentspec-v3.md`.
2. `protea/` package skeleton, `pyproject.toml` extras (`[zaralm]`, `[zaralm-train]`, `[zaralm-serve]`) so the VPS image does not pull torch.
3. `protea/schemas/agentspec_v3.py` + converters + JSON Schema export; tests round-tripping 504 packages, 15 flagship specs and the 17-facet spec.
4. `protea/providers/` interface, `MockProvider`, `AnthropicProvider`, `OpenAIProvider`, `GoogleProvider`, `AzureOpenAIProvider`, `OpenAICompatibleProvider`, `ProteaProvider` (client only); usage logging via `log_api_usage` with `model_version`.
5. `protea/cli.py`: `protea doctor`, `dataset build|validate|stats`, `train --dry-run`, `evaluate`, `serve` (stubs return `planned` explicitly, no fake results).
6. Dataset registry (`protea_data/registry.json`) and model registry (`protea/registry/models.json`) with the §21 status lifecycle; MLflow optional via config.
7. `docs/model-selection.md` with the §7 scoring matrix (re-verified on the Hub).

### Phase 2 — Data pipeline
1. Discovery + allowlist extractors for the paths in `data-risk-assessment.md` §2 (aria packages, miai-agents packages, registries, presets, routing corpus, flagship specs).
2. Classifier, secret scanner, PII scanner, brand/infra scrub, contamination check, family-aware dedup.
3. Normalizers → `agent_generation`, `tool_calling`, `connector_selection`, `structured_output` JSONL in chat-messages format with tool-call blocks and the §8 metadata envelope.
4. Splits by family: train / validation / test / golden; golden hash-pinned; CI test fails if training manifests reference golden ids.
5. `protea dataset stats` producing §66 statistics; dataset cards (§79).
6. Synthetic pipeline skeleton (frontier provider → schema → safety → dedup → quality → judge → rules → review queue) — generation itself gated behind budget confirmation.

### Phase 3 — ZaraBench (before any training)
1. Task schemas for the 10 categories; evaluators (schema validity, tool selection/argument accuracy, connector accuracy, workflow validity, hallucination of unavailable tools/connectors, safety/permission violations, instruction following, business-goal alignment via LLM judge with rubric).
2. `ZaraScore` weights in `configs/evaluation/zarabench-0.1.yaml`.
3. Golden set authored (≥150 tasks) using catalogue families held out from training.
4. Runner supports any `ModelProvider`; reports (JSON + Markdown) with cost, latency, failure modes.
5. **Execution boundary:** running the base-model baseline needs a rented GPU (or a hosted inference provider for the candidate); running frontier baselines spends API tokens. Both stop for confirmation with the §52 summary.

### Phase 4 — Training
1. TRL/PEFT trainer with YAML config, immutable run snapshot, seeded, checkpoint/resume, metrics logging, dataset hash + git commit captured.
2. Tiny-model CPU dry run in CI (`Qwen2.5-0.5B-Instruct`, 20 steps).
3. Remote adapters: generic SSH, RunPod, Azure (Bicep generated), Kubernetes job spec; `--dry-run` prints provider/GPU/count/duration/cost/dataset/base model/method; idle shutdown, max runtime, checkpoint-before-stop, storage separated from the instance.
4. Model card generation (§78). **Execution boundary:** launching the first QLoRA run.

### Phase 5 — Inference — implemented (ADR-009); Zara routes deferred to Phase 6 in aria
1. `deployment/protea/Dockerfile.infer` (vLLM, adapter mount, AWQ/FP8 option), health/readiness/metrics endpoints, graceful shutdown.
2. Zara-specific facade routes in `aria` (`/v1/agent/generate|repair|optimize`, `/v1/workflow/generate`, `/v1/tools/select`) wrapping compiler + validation.
3. `ProteaProvider` end-to-end tests against a mock vLLM server; `agent_runtime` gateway config documented.

### Phase 6 — Agent Compiler
1. `protea/compiler/` pipeline: requirement understanding → intent → objective → capability planning → tool retrieval → connector retrieval → workflow → guardrails → schema compile → validate → **draft** creation via `agent_platform.store`.
2. Context builder retrieving only relevant registries/schemas/policies.
3. Replace `agent_builder_v2` keyword path behind a flag; keep `dependencies`/`dna`/`requirements` deterministic stages.
4. Completeness engine extension (§30) and `can_generate` logic.

### Phase 7 — Model Router (implemented, ADR-010)
Task classifier (routing corpus + task types), complexity estimator, privacy/cost policies, capability matrix from ZaraBench reports, fallback with validation, confidence engine, version pinning, fallback-event recording.

### Phase 8 — Voice Copilot
Wire `/v1/copilot/*` (chat + speak) → Requirement Collector → Agent Compiler → validation → draft agent in "My Agents"; no direct configuration generation from voice.

### Phase 9 — Observability
Request/model/validation/fallback/cost fields on `api_usage` and `audit_events`; feedback table (§36) linked to model version, agent, task, prompt template, tools; data APIs for the §38 dashboards; privacy-safe logging default.

### Phase 10 — Production hardening
Security tests (injection, unauthorized tool, cross-tenant), canary + rollback config, release pipeline (train → validate → ZaraBench → security eval → compare production → candidate → staging → canary → production), load test of the inference facade, operations docs, economic model report (§69).

## 6. Execution boundaries (stop-and-confirm points)

| Action | Cost / exposure | Boundary |
|---|---|---|
| Frontier-API baseline and synthetic generation | API tokens; catalogue text sent to a third party | confirm provider + budget |
| Renting a GPU for baseline/training/serving | hourly billing | `--dry-run` summary, explicit confirmation |
| Downloading a base model (16 GB+) | bandwidth/disk on the GPU host only | confirm host |
| Applying Bicep/Terraform | real Azure resources | never applied automatically |
| Changing `aria` production env (`MIAI_MODEL_MODE`, provider flags) | production behaviour | separate PR + user approval |

## 7. Risks (technical, security, data, architectural)

| Risk | Impact | Mitigation |
|---|---|---|
| Catalogue evals/knowledge contaminated by the mock `heal:evals` pipeline | trains on self-fulfilling expectations | prefer `miai-agents` evals; contamination filter; human sample review |
| 500 SKUs are 100 families × 5 near-duplicate variants | leakage across splits inflates scores | family-level splits and dedup keys |
| Thin workflow / repair / optimization data | v0 weak on §10, §12, §13 | synthetic pipeline with fault injection; prioritise in dataset 0.2 |
| Four un-unified provider layers; Copilot bypasses metering | inconsistent routing, cost blind spots | provider abstraction + flagged migration; contract tests |
| `evals_v2` hard-coded passes could be mistaken for a release gate | false confidence | mark `planned`, replace with ZaraBench |
| Copilot creates live tenants from one turn | unsafe deployment of generated configs | compiler ends in draft + eligibility gate |
| SQLite single-worker VPS with 4 GB RAM | cannot host inference or heavy pipelines | GPU services external; pipeline runs in CI/laptop; Postgres migration continues independently |
| Public-repo secret exposure in Gaslite (`.replit`) | credential compromise | rotate now (S1/S2) |
| Multilingual (af/zu/xh/st/tn/sw) quality unknown for candidate models | Africa specialization claims unproven | language-tagged ZaraBench slices; test independently |
| Licence constraints (Llama/Gemma) | redistribution and naming obligations | prefer Apache-2.0 candidates; record in model card |
