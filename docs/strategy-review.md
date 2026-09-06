# Protea v0 — Strategy Review: Corrections and Additions to the Build Specification

**Date:** 2026-09-06 · **Status:** proposed amendments to the Protea v0 master build specification, based on the Phase 0 audit of `aria`, `miai-agents`, `miai-agent-marketplace` and `Gaslite`.

The specification is a sound generic playbook for a proprietary-model programme. Its weaknesses are the assumptions it makes about *this* estate: the amount of data, the request volume, the team size, the infrastructure, and the legal terms of the frontier providers it plans to learn from. This document keeps what is right, corrects what is wrong, adds what is missing, and proposes a revised v0 definition with a kill criterion.

---

## 1. What the specification gets right (keep unchanged)

- Baseline before training, golden set never touched by training, release gated by a benchmark rather than loss (§22–§25, §67).
- Provider-agnostic abstraction; Zara must never depend on Protea (§3.2, §31–§33).
- Weights for behaviour, RAG for tenant knowledge; production conversations never auto-train (§35, §42–§43, §77).
- Deterministic authorization; model output is data, not permission (§39–§41).
- Dry-run, cost controls, explicit confirmation at every billable boundary (§52, §63, §82).
- No fake implementations, ADRs, model and dataset cards (§73–§74, §78–§79).
- 7B–14B open-weight starting point; no frontier-scale pretraining in v0 (§1, §65).

---

## 2. Corrections

### C1. The working repository is not the platform
Handled in `repository-audit.md`. Protea code belongs in `aria` (or a sibling it imports). Nothing in the specification's "hundreds of agents, prompts, skills, tools, connectors, workflows" description exists in `Gaslite`.

### C2. The data is roughly one-tenth of what the specification assumes
The estate holds ≈104 unique agent families (500 SKUs are 5 localized copies each), 65 authored source packages, 221 connector bindings, and almost no workflow, repair or optimization examples. That means **synthetic data will be the majority of the training set**, and the model that generates it becomes the real teacher. The plan must say so and choose that teacher deliberately (see C3).

### C3. Distilling from Claude, OpenAI or Gemini into a competing model is a contractual problem
The specification's synthetic pipeline (§14), frontier fallback events as training data (§33, §35) and "approved frontier models" as generators all produce outputs from providers whose standard commercial terms restrict using outputs to develop competing models. This is the single most important omission. Options, in order of preference:

1. Use **open-weight teachers** for synthetic generation and judging (larger Qwen3 / gpt-oss-120b / Llama 3.3 70B / DeepSeek-class models via a hosted inference provider or a rented GPU). Their licences permit derivative training.
2. Obtain written legal review or a negotiated agreement before any frontier-generated example enters `protea_data/`.
3. Restrict frontier models to **evaluation baselines and judging only**, which does not train anything.

Until one of these is decided, the synthetic pipeline should default to open-weight teachers and record `generator_model` on every example so anything generated under an unresolved licence can be quarantined later.

### C4. Agent generation is the wrong first economic target for a small model
The specification aims Protea v0 at business problem → agent specification. That workload is **low-volume and high-stakes**: a partner compiles a handful of agents, each of which then runs thousands of customer turns. A fine-tuned 8B model earns its keep where volume is high and the task is narrow. In this estate that is the **runtime turn** of the 504 catalogue agents (WhatsApp/web conversations with tool calls, guardrails and handoff), currently routed to a `claude-sonnet` alias. Recommended reframing:

- **Protea Agent** (compiler model) stays the *strategic* capability and the flagship for the data flywheel, but v0 acceptance does not depend on it beating frontier models.
- **Protea Runtime** (catalogue turn model) is added as the *first economic* candidate. It has the most natural data (system prompts, tools, 1,110 authored evals with tool expectations), a runtime that can already point at it (`GatewayModelAdapter`), and a clear cost comparison per turn.
- ZaraBench measures both; the economic model (§69) decides which ships first.

### C5. Phase order is backwards for value and for data
The specification trains (Phase 4) before the compiler (Phase 6), router (Phase 7) and observability (Phase 9) exist. Reordered, the compiler, validation gate, router and observability ship first **with frontier models behind them**. That delivers a validated agent-building pipeline to partners in weeks, starts logging the traces that the repair and optimization datasets need, and gives the router a place to put Protea later. Revised order in §5 below.

### C6. Agent repair needs production traces that do not exist yet
§12 calls repair "extremely important" and it is, but repair examples require execution traces, tool errors and outcomes. Observability (spec §37) must precede the repair dataset, and fault-injection over existing packages is the only v0 source. Repair moves to dataset 0.2.

### C7. Infrastructure breadth is over-specified for one team
Four remote-GPU adapters (SSH, Azure, RunPod, Kubernetes), Terraform, MLflow, dashboards and canary are more platform than v0 needs. Pick **one** training provider (RunPod-class marketplace for bursty jobs) and **one** serving location. Because Zara already runs in Hetzner's EU region for POPIA reasons, a Hetzner dedicated GPU server (GEX line, RTX 4000/6000 Ada class; verify current pricing) is the natural serving host and keeps data residency in one place. Azure Bicep stays generated-only until a partner requires Azure.

### C8. Multilingual and African-language claims must be measured, not promised
Candidate 8B models have thin isiZulu/isiXhosa/Sesotho/Setswana coverage; the estate's evals have only 68 Afrikaans and 68 isiZulu cases. Language-tagged ZaraBench slices come first; the existing `llm/translate.py` layer remains the runtime answer until a model proves otherwise.

### C9. The security pack already claims "self-hosted open-weight fallbacks"
`agent_platform/security_pack.py` advertises self-hosted open-weight models. Production is a 4 GB VPS with no GPU; the Ollama service exists only in Compose. Either soften the claim now or make it true with Protea. Do not market "proprietary model" to partners until ZaraBench shows parity on the routed task types.

### C10. Unifying four provider layers is a production refactor, not a foundation task
`llm/`, `embed/llm.py`, `server.py::_cost_aware_complete` and `copilot.py` are all on live paths. Migrate behind feature flags with contract tests, one caller at a time, starting with the Copilot (which also bypasses cost metering today).

---

## 3. Additions

### A1. Kill criterion (the specification has none)
Define before spending on GPUs: after dataset 0.2 and two training iterations, if Protea's ZaraScore on its target task type is below an agreed fraction of the frontier baseline, **or** the economic model's break-even utilization exceeds forecast volume by a wide margin, stop training. The compiler, validation gate, router, ZaraBench and observability keep delivering value with frontier models. The programme's downside is therefore bounded to a few weeks of pipeline work plus tens of dollars of GPU time per experiment.

### A2. Preference optimization from the validator, in v0 not "future"
The validation gate produces automatic preference pairs: a spec that passes schema, business, security and connector checks versus one that fails. DPO (or ORPO) on those pairs is cheap, needs no human labels, and targets exactly the structured-output failures a small model makes. Add it as step two after SFT.

### A3. Train and serve in the same tool-call format
Choose the base model's native chat template and tool-call format (Hermes-style JSON for the Qwen family) and configure vLLM's matching tool parser. Format drift between training data and serving is the most common silent failure in fine-tuned tool-calling models.

### A4. Constrained decoding before fine-tuning
Guided JSON (JSON Schema grammar at decode time) plus the context builder may already give an unmodified base model acceptable schema validity. Measure that in the baseline; it tells you how much of the win comes from the compiler versus the weights, and it de-risks the training budget.

### A5. Regulated-partner private deployment is a first-class reason for Protea
The estate targets banks, insurers, mobile money and public sector (`nedbank/`, `kazang_*`, `insurance-*`, `citizen-services`). A model that runs inside a partner's VPC with no third-party API is a commercial differentiator the specification only lists as "future". Keep the architecture single-tenant-deployable from day one (one container, one adapter, no external calls).

### A6. Judge independence
The LLM judge in ZaraBench must not be the teacher that generated the synthetic data, and business-goal-alignment scores need a rubric with a human-rated sample of at least 10% per release.

### A7. Data flywheel needs consent plumbing, not just a policy
Add a per-tenant `training_consent` flag and a review queue table in `aria` during the observability phase, so fallback events and rated turns can enter the dataset legally later. Without the plumbing the flywheel in §35 never starts.

### A8. Honest timeline for one engineer with an AI pair
Foundation, data pipeline and ZaraBench: roughly three to five weeks of focused work. Compiler and router live on frontier models: within that window if reordered. First QLoRA experiment: week five or six. First Protea traffic behind the router on a canary task type: quarter two of the programme, contingent on ZaraBench.

---

## 4. Revised v0 definition

Protea v0 is done when:

1. The Agent Compiler produces validated `AgentSpec v3` drafts from a business problem through any `ModelProvider`, and the Voice Copilot and console both use it.
2. ZaraBench runs with a sealed golden set; baselines exist for the chosen base model and at least one frontier model; reports include cost and latency.
3. Datasets `agent-training-0.1` (agent generation, structured output, tool calling, connector selection) and `runtime-training-0.1` (catalogue turns) are versioned, scanned, carded and family-split.
4. One reproducible QLoRA run has produced a registered checkpoint with a model card and a base-versus-Protea comparison, trained only on data whose licence status is `approved`.
5. The router can route one task type to Protea with validation and frontier fallback, recording fallback events, behind a feature flag defaulting to off.
6. Observability captures the §37 fields and feedback links to model version.
7. The kill criterion has been evaluated and recorded either way.

Explicitly **out** of v0: multi-cloud adapters, canary automation, dashboards beyond data APIs, skill generation, continued pretraining, multilingual specialization, on-prem packaging (designed for, not built).

## 5. Revised phase order

| Order | Phase | Why here |
|---|---|---|
| 1 | Foundation: schemas, provider abstraction, CLI, registries | unchanged |
| 2 | Agent Compiler + validation gate on frontier models | value first; produces drafts, never live tenants |
| 3 | Observability + consent plumbing | traces feed repair/optimization data and the router |
| 4 | Model Router + fallback + confidence (frontier only at first) | the slot Protea will occupy |
| 5 | Data pipeline + dataset 0.1 + open-weight synthetic pipeline | licence-safe by construction |
| 6 | ZaraBench + baselines (base model, frontier) | decides whether training is worth it |
| 7 | Training: QLoRA SFT → DPO from validator pairs → checkpoint → compare | first GPU spend, after evidence |
| 8 | Inference: vLLM container, facade endpoints, Protea behind the router flag | |
| 9 | Voice Copilot wiring (mostly config once the compiler exists) | |
| 10 | Hardening, economic report, kill-or-continue decision | |

## 6. Decisions requested

1. Approve `aria` as the home of `protea/` (or name a sibling repository).
2. Choose the synthetic-data teacher policy: open-weight teachers by default (recommended), or legal review for frontier outputs.
3. Confirm the reframing: compiler and router ship first on frontier models; Protea Runtime is evaluated alongside Protea Agent as the first economic target.
4. Approve fixing the two Gaslite secret findings in a separate PR now.
