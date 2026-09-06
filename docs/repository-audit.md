# Protea Phase 0 — Repository Audit

**Date:** 2026-09-06
**Scope:** Four repositories inspected read-only at the commits below. No training, cloud resources, or production changes were made.

| Repository | Commit | Role in the Protea programme |
|---|---|---|
| `MalcolmGov/aria` | `c22c31b` | **The Zara Agent Platform.** Production FastAPI monolith serving zaraai.digital, the 500-SKU agent marketplace, partner console, embed runtime and Copilot. This is where Protea integrates. |
| `MalcolmGov/miai-agent-marketplace` | `f46ca46` | Predecessor / white-label marketplace (MyInstantAI). Source of the imported catalogue and of the TypeScript agent runtime vendored into `aria/agent_runtime`. |
| `MalcolmGov/miai-agents` | `9f29405` | 69 hand-authored source agent packages, the canonical package spec, and a Python live-eval harness. Highest-quality authored data. |
| `MalcolmGov/Gaslite` | `5ccea6a` | This repository. An LPG delivery app plus a white-label prompts.chat fork. It **consumes** a Zara agent via the embed widget but is not the Zara platform. Phase 0 documents live here because this is the designated working branch. |

> **Correction to the brief.** The build specification assumes the working repository *is* the Zara platform. It is not. `Gaslite` contains no agent definitions, prompt libraries, tool schemas or model-provider code. Everything the specification asks for exists in `aria`. The recommendation in `architecture-proposal.md` is that Protea code is built inside `aria` (or a sibling repository it imports), and that these Phase 0 documents are ported there when Phase 1 begins.

---

## 1. `aria` — the Zara Agent Platform

### 1.1 Size and languages

| Metric | Value |
|---|---|
| Working tree (excluding `.git`, `node_modules`) | 84 MB |
| Python files | 1,136 |
| TypeScript/TSX files | 281 (React console in `assistant-ui/`, Node runtime in `agent_runtime/`) |
| JSON files | 551 (504 are agent packages) |
| Lines across `.py .ts .tsx .js .mjs` | ≈464,000 (inflated by vendored UI assets; `server.py` alone is 696 KB) |
| Test files | 299 (`tests/`, `assistant-ui`, `e2e`), ≈3,314 `test_*` functions |
| Markdown docs | 79 (`docs/marketplace/`, `docs/platform-v2/`, `docs/runbooks/`) |

### 1.2 Backend architecture

- **Framework:** FastAPI + uvicorn (`server.py`, 299 route decorators on the root app) with sub-routers: `agent_platform/router.py` (`/agentplatform/*`, 7,364 lines, partner API + console), `embed/router.py` (`/v1/embed`, `/v1/chat`, WhatsApp webhooks), `developer/router.py` (developer portal), `social/router.py`, and ~70 vertical modules (`kasi/`, `spar/`, `travel/`, `health/`, …) for the consumer assistant.
- **Background jobs:** APScheduler inside the single uvicorn process (briefings, proactive twin, reminders). One worker is mandatory (`docs/runbooks/runtime-ceiling.md`).
- **Agent runtime sidecar:** `agent_runtime/` — Node 22 + pnpm workspace with `@zara/agent-protocol`, `@zara/runtime`, `@zara/connectors`, `@zara/presets`, `@zara/wallet-adapter` (ported from `miai-agent-marketplace`). `src/server.mjs` exposes `runTurn` over HTTP; `embed/runtime_client.py` routes embed turns to it when `ZARA_RUNTIME_URL` is set.
- **Consumer assistant:** `agent_runner.py`, `agent_tools.py` (133 KB, 25 tool functions), `memory.py` (80 KB), `contacts.py`, voice/WebRTC (`Simli`, ElevenLabs), WhatsApp bridge (`whatsapp-service/`, whatsapp-web.js).

### 1.3 Frontend architecture

- `assistant-ui/` — React + Vite + TypeScript (partner console `PartnerConsoleApp.tsx`, `AuroraAgents.tsx` marketplace storefront, `MeetingCopilot.tsx`). Vitest configured.
- `agent_platform/static/`, `developer/portal/*.html`, `embed/static/` — server-rendered HTML consoles and the embeddable widget (`agent.js`).
- `android-agent-demo/`, `sdks/node`, `sdks/python` — client SDK surfaces.

### 1.4 Database

- **SQLite files** under `/app/data` on the VPS: `embed.db` (partners, tenants, rentals, deployments, keys, usage, audit_events, knowledge_chunks, mcp_connections … 60+ tables in `agent_platform/store.py` and `embed/store.py`), `memory.db` (consumer facts, life graph, contacts, chat), `api_usage` (`commands/admin.py`), `rate_limits.db`, ~20 domain DBs.
- **Postgres seam:** `dbx.py` (`pg_conn()`, per-domain `ZARA_PG_*` flags), provider decided (Neon EU). Migration plan: `docs/platform-v2/00a-postgres-migration-plan.md`. Production is still SQLite.
- **Encryption at rest:** plaintext SQLite on Hetzner disk; GPG-encrypted off-site backups (`docs/runbooks/encryption-at-rest.md`).

### 1.5 Agent storage format and count

| Item | Value |
|---|---|
| Format | `zara.agent-package/v1` (loaders accept `miai.agent-package/v1`) — `docs/marketplace/ZARA_AGENT_PACKAGE.md` |
| Location | `data/agents/*.agent.json` + `index.json`, `families.json`, `market-packs.json`, `IMPORT_META.json` |
| Packages | **504** (100 families × 5 markets: `africa` 104, `asia` 100, `eu` 100, `oceania` 100, `us` 100) |
| Categories | operations 229, vertical 215, commerce 40, front-office 10, sales 10 |
| Shape | `manifest`, `system_prompt`, `knowledge`, `tools[]`, `guardrails`, `evals[]` |
| Tool definitions | 1,916 (246 unique names; `handoff_to_human` in 551/552 upstream) |
| Evals | 7,687 (`expect`: `says_any`, `says_none`, `tool`, `no_tool`, `tool_none`, `tool_any`, `refuses`, `lang`) |
| Size | 10.3 M chars ≈ **2.57 M tokens** raw |
| Readiness | every SKU is `catalogue-ready` in `index.json` (no `connector-live` / `showroom-ready` SKUs recorded) |
| Primary model alias | `claude-sonnet` (all), fallback `gpt-4o-mini` |

Plus 15 flagship `AgentSpec` objects (`embed/flagship_agents.py`) and 6 workforce suites (`embed/workforces.py`) authored in code.

### 1.6 Agent schemas (two generations coexist)

1. **Package v1** (`agent_runtime/packages/agent-protocol/src/index.ts`): `AgentManifest` + prompt/knowledge/tools/guardrails/evals. JSON Schema for the manifest lives in `miai-agents/spec/manifest.schema.json`.
2. **AgentSpec v2** (`embed/agentspec.py`, dataclasses): identity, objective, category, industry, tier, autonomy_level, market, languages, channels, `model_policy`, `risk_policy`, skills, tools, connectors, knowledge_sources, system_prompt, guardrails, `workflow[WorkflowStep]`, `roi_model`, metadata. `migrate_legacy_package_to_agentspec()` converts v1 → v2.
3. **17-facet `AgentSpecification`** (`agent_platform/compiler.py`, Pydantic v2): persona, channels, voice, SOP, knowledge, connectors (`ConnectorRef`), tools (`ToolDefinition` with `side_effects` read-only/write/financial/auth), workflow DAG (`WorkflowNode` trigger/lookup/retrieve/reasoning/action/handoff), `PolicyRule` (autonomy 1–5, HITL threshold, financial authority, POPIA), `TestCase` (golden/edge_case/red_team_injection), `ReadinessScore`. This is the closest existing artefact to the Protea target schema in §45 of the brief.

No single canonical schema is enforced at write time; the three are bridged by hand-written converters.

### 1.7 Agent generation today ("Zara Copilot AI Architect")

- `POST /agentplatform/v1/workforce/builder/compile` → `embed/agent_builder_v2.generate_agentspec_from_intent()` — **keyword matching**, five hard-coded archetypes, fixed workflow template. No model call.
- `agent_platform/copilot.py` (`OPENAI_TOOLS`, `_build_agent_from_issue_exec`, `_diagnose_and_architect_solution`) — the console Copilot. Uses OpenAI tool-calling directly over `httpx` (bypasses `llm/` metering). The specification itself is compiled **deterministically** from: `connectors.detect_archetype`, `connectors.match_connectors`, `org_intel`, `dna.match_agent_dna` (keyword index over the 504 packages), `dependencies.resolve_capability_dependencies` (capability → prerequisites/connectors/tools/HITL graph), `requirements.RequirementsGraph` (confidence matrix + `nbq` next-best-question), then `compiler.compile_agent_specification`.
- `POST /v1/workforce/evaluations/run` → `embed/evals_v2.run_agent_evaluations` — static checks; several results are hard-coded passes (e.g. prompt-injection `score=0.98`, `passed=True` unconditionally). Not a real evaluation.
- Voice: `/v1/copilot/chat`, `/v1/copilot/speak`, `voice_service_enhanced.py`, STT/TTS in `embed/stt.py`, `embed/tts.py`.

**Implication:** the platform already has the *deterministic* half of the Agent Compiler (registries, dependency graph, validation, readiness scoring). The *understanding* half (business problem → objective, capabilities, workflow) is rule-based. That is exactly the slot Protea fills.

### 1.8 Tool, skill, connector and workflow registries

| Registry | File | Contents | Notes |
|---|---|---|---|
| Tools | `embed/tool_registry.py` | 10 tools (`crm.*`, `accounting.*`, `comm.*`, `support.*`) with `risk_level` green/yellow/orange/red, `requires_approval`, `supports_dry_run`, `tenant_scoped`, `audit_required`, input/output schemas | Matches §41/§48 of the brief in shape; small |
| Skills | `embed/skills_registry.py` | ~20 skills (`comm.*`, `data.*`, `docs.*`, `business.*`, `finance.*`, `ai.*`) with `required_connectors` | |
| Connectors v2 | `embed/connectors_v2.py` | Catalog (`microsoft.365`, `google.workspace`, `crm.hubspot`, `crm.salesforce`, `erp.xero`, `erp.sage`, `db.postgres`, `payments.paystack`, `it.zendesk`, `it.jira`, `meta.whatsapp`, …) with `auth_type`, scopes, `risk_level`, tool list | |
| Connectors (runtime) | `agent_runtime/packages/connectors` | 14 live/OAuth connectors: calendly, email, hubspot, mcp, quickbooks, shopify, slack, stripe, teams, webhook, whatsapp, woocommerce, xero, zendesk | Real OAuth flows, SSRF guard, webhook signatures |
| Presets | `agent_runtime/packages/presets` | tool → connector bindings per catalogue agent (~221 upstream) | Direct connector-selection training signal |
| Capability graph | `agent_platform/dependencies.py` | capability → prerequisites, connectors, recommended tools, risk tier, HITL | Rule-derived; good synthetic seed |
| Workflow engine | `embed/workflow_engine.py` | `WorkflowStep` state machine: trigger / agent / tool / condition / approval / wait / end; pause/resume on approval | Simulated tool execution |
| Approvals / audit | `embed/approval_center.py`, `embed/audit_v2.py` | in-memory + persisted approval requests; audit events | |
| MCP | `agent_platform/mcp_server.py`, `embed/mcp.py`, `embed/mcp_oauth.py`, `movedigital_mcp.py`, `mcp_connections` table | Zara is both an MCP server and an MCP client (Streamable HTTP / SSE JSON-RPC) | |

### 1.9 Model providers and provider abstraction (four layers, none unified)

| Layer | File(s) | Providers | Notes |
|---|---|---|---|
| AI Hub | `llm/router.py`, `llm/{claude,openai,gemini}_provider.py`, `llm/capabilities.py`, `llm/intent_classifier.py` | Claude, OpenAI (chat/vision/images), Gemini | Provider order + fallback on error strings; usage logged via `log_api_usage` |
| Consumer chat | `server.py::_cost_aware_complete` | Ollama (local), OpenAI, Claude | Tiers `simple` / `complex` / `vision`; `LLM_COST_MODE=economy` prefers Ollama; env-driven fallback order — the seed of a model router |
| Embed runtime | `embed/llm.py` | Claude, OpenAI, Gemini | Honours an agent's fixed model; fills `usage_out` with real token counts for wallet billing |
| Catalogue runtime (TS) | `agent_runtime/packages/runtime/src/index.ts` | `MockModelAdapter`, `OpenAIModelAdapter`, `AnthropicModelAdapter`, `GatewayModelAdapter` (any OpenAI-compatible URL), `AzureOpenAIModelAdapter` | Streaming, tool calls, streaming guardrail scrub. **`GatewayModelAdapter` can point at a Protea vLLM endpoint with zero code change.** |
| Copilot | `agent_platform/copilot.py` | OpenAI via raw `httpx` | Bypasses metering and fallback |

Model aliases (`claude-sonnet`, `gpt-4o-mini`, `gemini-flash`) are resolved per layer (`OPENAI_MODEL_MAP`, `ANTHROPIC_MODEL_MAP`, `DEFAULT_MODEL_MAP` in `miai-agents/tools/run_evals.py`). There is no `ModelProvider` interface with `generateStructured`, no capability matrix, and no confidence engine. `docker-compose.yml` ships an `ollama` service; the security pack advertises "self-hosted open-weight fallbacks".

### 1.10 Observability

- **Errors:** Sentry (`sentry_sdk.init` in `server.py`, `SENTRY_DSN`).
- **Cost/usage:** `commands/admin.py::log_api_usage` → `api_usage` table (provider, operation, channel, model, input/output tokens, `cost_usd` from a rate table, user_id, latency). `embed/store.add_usage`, `agent_platform/store.record_usage`, `usage_events`, `wallet_ledger`. Admin `/api/admin/costs`.
- **Routing telemetry:** `ROUTE` log lines + golden routing corpus (`tests/routing/corpus.py`, 1,256 lines) — `docs/runbooks/routing.md`.
- **Structured logging:** stdlib `logging`; no OpenTelemetry, no Prometheus, no request-id propagation.
- **Feedback:** no thumbs-up/down or task-outcome capture on agent turns found in `embed/` or `agent_platform/`.

### 1.11 Authentication, authorization, secrets

- Root `/api/*`: Bearer `API_SECRET_TOKEN`, fail-closed when unset (`server.py:1484–1660`).
- Partner API: `pak_` keys (hashed, prefix-display, revocable), `aps_` sessions, email/magic-link, OIDC SSO (`agent_platform/sso.py`), admin `X-Zara-Admin-Token` fail-closed in prod (`agent_platform/router.py`).
- Tenant isolation: row-level `partner_id` (718 references in `agent_platform/store.py`) and `tenant_id` in `embed/store.py`; `security_pack.py` publishes the posture.
- Secrets: env vars on the VPS (`.env` git-ignored), `ZARA_SECRETS_KEY` + `cryptox.py` for encrypted OAuth tokens, GitHub Actions workflows to set connector credentials. No committed `.env`; secret-pattern scan of the tree found nothing.
- Weak spots relevant to Protea: `copilot._build_agent_from_issue_exec` creates tenants with `domains=["*"]` and `status='live'` straight from a chat turn; `evals_v2` hard-codes passes; mock auth modes exist in the runtime sidecar (`MIAI_AUTH_MODE`); single global `API_SECRET_TOKEN`.

### 1.12 Deployment, CI/CD, tests

- **Production:** Hetzner Cloud CPX22 (2 vCPU / 4 GB / 80 GB, Falkenstein) at `/opt/aria`, Docker Compose (`ui`, `agent`, `agent-runtime`, `voice-service`, `whatsapp-service`, `ollama`), nginx + Let's Encrypt, Neon EU Postgres planned. Railway (`railway.toml`) for the UI/runtime. **No GPU anywhere.**
- **CI:** `.github/workflows/ci-tests.yml` (pytest-xdist, routing corpus first, quarantine list), `deploy.yml` (test gate → rsync/rebuild on the VPS), `agent-evals.yml` (live Anthropic evals for 3 compliance agents, weekdays 05:30 UTC), `marketplace-smoke.yml`, `uptime.yml`, WhatsApp signature enforcement.
- **Tests:** ≈3,314 pytest functions; 9 cover the compiler pipeline (`tests/test_agent_compiler_pipeline.py`, `test_agent_evals.py`). No tests for `agent_builder_v2`, `workflow_engine`, `tool_registry`, `connectors_v2`.

### 1.13 Existing AI evaluation mechanisms

| Mechanism | Real model? | Coverage |
|---|---|---|
| `agent_runtime/scripts/run-evals.mjs` | Yes (Anthropic/OpenAI) or offline | 3 compliance agents in CI; any agent by flag |
| `miai-agents/tools/run_evals.py` | Yes (Anthropic/OpenAI/Gemini) + LLM judge, `--repeat` voting | 69 source packages, 1,110 evals — never run to completion per the audit |
| `miai-agent-marketplace` `eval:suite` | Mock model (echoes knowledge) | 552 packages; `heal:evals` rewrote expectations and injected phrases into knowledge files (see `MYINSTANTAI_MIGRATION_AUDIT.md` §3.1) |
| `embed/evals_v2.py` | No | static shape checks with hard-coded passes |
| `tests/routing/` | No | 887-line golden routing corpus |

No baseline of any model against a Zara-specific benchmark exists. ZaraBench must be built before training (Phase 3).

---

## 2. `miai-agents` — authored source packages

| Metric | Value |
|---|---|
| Agent folders | 69 (README says 52 authored; 17 newer consumer/education agents added since) |
| Files per agent | `manifest.json`, `system_prompt.md`, `knowledge.md`, `tools.json`, `guardrails.md`, `evals.jsonl` |
| Words | system prompts 64,198 · knowledge 57,247 · guardrails 47,331 |
| Tools | 273 (172 unique; side effects: write 149, read-only 119, financial 5) |
| Evals | 1,110 (en 973, af 68, zu 68, es 1) |
| Languages declared | en 69, af 68, zu 68 |
| Tooling | `validate.py` (JSON Schema), `bundle.py`, `configure.py` (tenant config → package), `run_evals.py`, `playground.py`, `studio.py`; CI `validate.yml` |

This is the cleanest, most traceable authored data in the estate and should be the first extractor target.

## 3. `miai-agent-marketplace` — lineage platform

Next.js 15 + pnpm monorepo, 552 catalogue packages (2.96 M tokens), 79 API routes, 14 connectors, 5 ADRs, Bicep for Azure Container Apps, gitleaks in CI, GHCR image publish. Its runtime and connector packages are the ancestors of `aria/agent_runtime`. The August 2026 audit in `Gaslite/MYINSTANTAI_MIGRATION_AUDIT.md` remains accurate on eval validity and should be treated as a data-quality warning for the 500-SKU catalogue.

## 4. `Gaslite` — this repository

| Item | Finding |
|---|---|
| Stack | Express 5 + React 18 + Vite + Drizzle ORM + PostgreSQL, TypeScript, `tsx`/`esbuild` build, Replit/Railway/Vercel deploy targets |
| Size | ≈25 k lines of app TS/TSX; 58 commits since 2026-03-02 |
| AI usage | None in the app. `client/index.html` loads the Zara Customer Support widget (`https://zaraai.digital/embed/v1/agent.js`, public `mia_pk_…` key) |
| `moove-prompts/` | White-label prompts.chat (Next.js 16, Prisma, NextAuth, next-intl, OpenAI SDK for prompt-builder tool-calling, MCP server at `/api/mcp`, 45 vitest files). Prompt data is seeded from the remote prompts.chat API (CC0), not stored in git |
| Tests / CI | No tests and no CI for the Gaslite app; `moove-prompts` has vitest |
| Secrets | `.replit` commits `VAPID_PRIVATE_KEY` and `VAPID_PUBLIC_KEY` in a **public** repository; `server/seed-admin.ts` hard-codes admin password `Admin123!` and a personal mobile number; `attached_assets/` holds WhatsApp screenshots and pasted conversations |
| Training data | None. One real business use case (on-demand LPG delivery, driver onboarding, settlements) usable as a synthetic business-problem seed |

## 5. Toolchain available in this session

Node 22.22, npm 10.9, Python 3.11, no `nvidia-smi`, no `node_modules` installed. Everything in Phase 1–3 that is CPU-only (dataset pipeline, validators, ZaraBench harness with mocks, tiny-model smoke tests) can run here or on a laptop.
