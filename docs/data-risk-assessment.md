# Protea Phase 0 — Data Risk Assessment

**Date:** 2026-09-06
**Purpose:** classify every candidate training source in the four inspected repositories before any extractor runs, and record the security findings that must be handled regardless of Protea.

Categories follow §5 of the build specification:
`SAFE_FOR_TRAINING · REQUIRES_REVIEW · RAG_ONLY · DO_NOT_TRAIN · SECRET · CUSTOMER_DATA · PII · LICENSE_RESTRICTED · UNKNOWN`.
Only `SAFE_FOR_TRAINING` enters a dataset automatically. `REQUIRES_REVIEW` needs a human decision per source (recorded in the dataset manifest). Everything else is excluded from the extractor allowlist.

---

## 1. Source classification

### 1.1 `aria` (Zara platform)

| Source | Category | Rationale / handling |
|---|---|---|
| `data/agents/*.agent.json` — `manifest`, `system_prompt`, `tools[]`, `guardrails` | **SAFE_FOR_TRAINING** | Authored, owned by Moove Digital, no tenant data. Brand-scrubbed on import (`docs/marketplace/BRAND_SCRUB.md`). Must be deduplicated by family: 5 market variants share most text. |
| `data/agents/*.agent.json` — `knowledge` | **REQUIRES_REVIEW** | Fictional/template business knowledge; the upstream `heal:evals` script injected eval phrases into 352 of 551 knowledge files. Use as **RAG_ONLY** context inside examples, never as facts to memorise. Detect injected filler before use. |
| `data/agents/*.agent.json` — `evals[]` | **REQUIRES_REVIEW** | Rich tool-calling signal (`expect.tool`, `no_tool`, `tool_none`, `refuses`, `lang`), but upstream expectations were rewritten to match a mock model. Prefer `miai-agents` source evals; accept catalogue evals only after rule checks (tool names exist in package, no contradictory expectations). |
| `data/agents/*.agent.json` — `manifest.compliance`, `market`, `languages` | SAFE_FOR_TRAINING | Metadata for domain/language tagging (§60). |
| `embed/flagship_agents.py`, `embed/workforces.py` | SAFE_FOR_TRAINING | 15 AgentSpec v2 objects + 6 suites in code; the only native v2 specs with skills/tools/connectors/ROI. |
| `embed/tool_registry.py`, `embed/skills_registry.py`, `embed/connectors_v2.py`, `agent_runtime/packages/presets`, `agent_runtime/packages/connectors` | SAFE_FOR_TRAINING | Machine-readable registries → tool/connector selection datasets and the context builder (§47–§49). |
| `agent_platform/dependencies.py`, `agent_platform/connectors.py` (archetypes), `agent_platform/dna.py`, `agent_platform/requirements.py`, `agent_platform/nbq.py` | SAFE_FOR_TRAINING (as rules) | Capability graph, archetype detector, confidence matrix. Use to *generate and validate* synthetic examples, not as text to memorise. |
| `agent_platform/copilot.py` SOP templates and system directives | SAFE_FOR_TRAINING | Authored prompt text. |
| `tests/routing/corpus.py` (887 utterance lines, must_claim / must_not_claim) | SAFE_FOR_TRAINING | Task-classification / intent data for the model router's classifier. |
| `docs/**`, `AGENTS.md`, `*.md` | REQUIRES_REVIEW | Product/architecture docs contain server IPs, hostnames, operational detail. Useful for RAG on "how Zara works"; strip infra identifiers. |
| `support_kb.md` | RAG_ONLY | Support knowledge base; product facts change. |
| `seed_malcolm_data.py`, `seed_demo_users.py`, `seed_intent_demo.py` | **PII** | Real personal facts (full name, birthday, home address, family, companies). Exclude by path. |
| `memory.py`, `contacts.py`, `whatsapp_history.py`, `gmail_history.py`, `life_graph.py`, `health*.py` | **CUSTOMER_DATA / PII** (data they manage) | Code is fine; the SQLite databases and any fixture dumps are production personal data. Exclude all `*.db`, `data/` runtime volumes, backups. |
| `embed.db` tables `messages`, `leads`, `bizdata_*`, `knowledge_docs/chunks`, `prospect_drafts`, `quotes`, `invoices` | **CUSTOMER_DATA** | Tenant conversations, uploaded knowledge, financial lines. Never on a training host. |
| `kazang_*`, `spar*`, `dischem`, `pnp_*`, `nedbank`, `bluelabel`, `easypay`, `partner_menu/partners/*` | **REQUIRES_REVIEW** | Partner catalogues and phone numbers (155 hard-coded SA mobile numbers in `.py` files). Business contact numbers of third parties; treat as PII by default. |
| `auth/demo_users.py` | PII-shaped | Synthetic demo identities with Gmail addresses; exclude to avoid teaching realistic email/phone patterns. |
| `.github/workflows/*.yml`, `deploy*.sh`, `docker-compose.yml`, `.env.example` | **SECRET-adjacent / DO_NOT_TRAIN** | Contain production IP, paths, env variable names. Never ingest. |
| `investor_pitch/`, `spar_pitch/`, `bluelabel_pitch/`, `*.pdf`, `*.pptx`, `*.docx` | LICENSE_RESTRICTED / DO_NOT_TRAIN | Commercial documents with third-party names and pricing. |
| `assistant-ui/`, `embed/static`, images | DO_NOT_TRAIN | UI code, no linguistic value for v0. |
| `agent_runtime/packages/runtime/src/index.ts`, `guardrails.ts` | SAFE_FOR_TRAINING (as behaviour reference) | Guardrail regexes define what the model must refuse; use to author safety evals. |

### 1.2 `miai-agents`

| Source | Category | Rationale |
|---|---|---|
| `agents/*/manifest.json`, `system_prompt.md`, `tools.json`, `guardrails.md` | **SAFE_FOR_TRAINING** | Hand-authored by Moove Digital; consistent bar; 172 unique tools with `side_effects`. |
| `agents/*/knowledge.md` | RAG_ONLY / REQUIRES_REVIEW | Contains real public facts (SASSA payout calendar, fee tables). Do not memorise; use as context. |
| `agents/*/evals.jsonl` (1,110) | **SAFE_FOR_TRAINING** after rule validation | Never healed by the mock pipeline; 973 en / 68 af / 68 zu. |
| `spec/agent-package.md`, `manifest.schema.json`, `tenant-config.schema.json` | SAFE_FOR_TRAINING | Schema text for structured-output training and validators. |
| `configs/*.json`, `examples/*.json` | REQUIRES_REVIEW | Tenant configs for named businesses (`umoya-home`, `move-digital`). Own businesses; anonymise business names. |
| `tools/*.py` | DO_NOT_TRAIN | Harness code, reused directly instead. |

### 1.3 `miai-agent-marketplace`

| Source | Category | Rationale |
|---|---|---|
| `data/catalog/*.agent.json` | REQUIRES_REVIEW | Superset of `aria/data/agents` plus 48 unprefixed ZA aliases and 2 insurance packages; same `heal:evals` caveat. Use `aria` copy (brand-scrubbed) as canonical. |
| `data/catalog-consumer/*.json` (18) | REQUIRES_REVIEW | Consumer agents; check for MyInstantAI branding. |
| `packages/presets/src/generated-presets.ts` | SAFE_FOR_TRAINING | 221 tool→connector bindings. |
| `docs/**`, `data/reports/**` | DO_NOT_TRAIN | Partner-confidential commercial and audit material; staging URLs. |
| `e2e/**`, `scripts/**` | DO_NOT_TRAIN | |

### 1.4 `Gaslite`

| Source | Category | Rationale |
|---|---|---|
| `.replit` | **SECRET** | Committed `VAPID_PRIVATE_KEY`; see §3. |
| `server/seed-admin.ts` | **SECRET + PII** | Hard-coded admin password and personal phone. |
| `attached_assets/*` (WhatsApp screenshots, pasted Gemini/Replit conversations, contracts `.docx`, product brief) | PII / CUSTOMER_DATA / DO_NOT_TRAIN | Personal images, third-party conversations, commercial contracts. |
| `GASLITE_PRODUCT_DOCUMENT.md`, `replit.md` | REQUIRES_REVIEW | Good *business-problem seed* (roles, order flow, settlements) for synthetic agent-generation prompts once emails/phones are stripped. |
| `MYINSTANTAI_MIGRATION_AUDIT.md` | DO_NOT_TRAIN | Partner-confidential audit. |
| `docs/marlin-moodley/*` | LICENSE_RESTRICTED / CUSTOMER_DATA | Client proposal documents. |
| `moove-prompts/src/content/book/**` (797 MDX, 11 locales) | LICENSE_RESTRICTED (MIT upstream, third-party authored) | General prompt-engineering tutorial content; not Zara-specific; leave out of v0. |
| `moove-prompts` prompt data | RAG_ONLY | Seeded remotely from prompts.chat (CC0); nothing stored in git. |
| `moove-prompts/src/lib/ai/*.prompt.yml`, `prompt-builder-tools.ts` | SAFE_FOR_TRAINING | Authored tool schemas + prompt templates; a small tool-calling example source. |
| App source (`server/`, `client/`, `shared/`) | DO_NOT_TRAIN | Not relevant to agent generation. |

---

## 2. Extractor allowlist (Phase 2 input)

The repository extractor must be **allowlist-based by path**, not blocklist-based:

```
aria/data/agents/*.agent.json            (fields: manifest, system_prompt, tools, guardrails; evals→review lane; knowledge→rag lane)
aria/embed/flagship_agents.py            (parsed via import, not text)
aria/embed/workforces.py
aria/embed/tool_registry.py
aria/embed/skills_registry.py
aria/embed/connectors_v2.py
aria/agent_platform/dependencies.py
aria/agent_platform/connectors.py        (archetype table only)
aria/tests/routing/corpus.py
aria/agent_runtime/packages/presets/src/generated-presets.ts
aria/agent_runtime/packages/connectors/src/index.ts   (connector metadata only)
miai-agents/agents/*/{manifest.json,system_prompt.md,tools.json,guardrails.md,evals.jsonl}
miai-agents/spec/*.json
moove-prompts/src/lib/ai/*.prompt.yml, prompt-builder-tools.ts
```

Every extracted example carries `source_repo`, `source_commit`, `source_path`, `source_id`, `license_status`, `pii_scan`, `secret_scan`, `review_status` (§8). Family-level grouping keys (`family = id minus market prefix`) are mandatory so train/validation/test/golden splits never put two market variants of the same family on different sides.

## 3. Security findings requiring action now (independent of Protea)

| # | Severity | Finding | Location | Recommended action |
|---|---|---|---|---|
| S1 | **High** | Web Push VAPID private key committed to a public repository | `Gaslite/.replit` `[userenv.shared]` | Rotate VAPID keys, move to env/secrets, strip from git history if the repo stays public, add secret scanning (gitleaks) to Gaslite. |
| S2 | High | Hard-coded admin password `Admin123!` and a personal phone number seeded on every boot | `Gaslite/server/seed-admin.ts` | Read from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env (as `moove-prompts` already does); remove personal number. |
| S3 | Medium | Copilot provisions live tenants with `domains=["*"]` and `status='live'` from one chat turn | `aria/agent_platform/copilot.py::_build_agent_from_issue_exec` | Protea Agent Compiler must terminate in a **validation + deployment-eligibility** gate; generated specs land as `draft`, never `live`. |
| S4 | Medium | Evaluation endpoint returns hard-coded passes | `aria/embed/evals_v2.py` | Replace with ZaraBench-backed evaluators; label as `planned` until then (§74). |
| S5 | Medium | Copilot calls OpenAI via raw `httpx`, outside `llm/` metering and fallback | `aria/agent_platform/copilot.py:1295–1360` | Route through the unified `ModelProvider` in Phase 1. |
| S6 | Medium | Production personal data seeded from code; SQLite plaintext on host | `aria/seed_malcolm_data.py`, `docs/runbooks/encryption-at-rest.md` | Out of Protea scope, but the training host must never receive `/app/data` volumes or backups. |
| S7 | Low | 155 hard-coded SA mobile numbers in partner modules | `aria/kazang_*`, `spar/*`, … | Move to config; extractor excludes these paths. |
| S8 | Low | Public embed key present in Gaslite HTML | `Gaslite/client/index.html:129` | By design (domain-locked); confirm domain lock is enforced server-side in `aria/embed` (the MIAI audit B8 said it was not). |

## 4. Multi-tenancy and RAG-versus-training rules adopted

- Protea weights are trained only on Moove-authored artefacts (agent packages, registries, synthetic data derived from them). No tenant knowledge, conversations, leads, invoices or uploaded documents — those stay in `embed/rag.py` retrieval (§42–§43).
- Fallback events and production turns become training candidates only after: redaction (`embed/textsafe.py`, `pii-redact` patterns from the runtime), tenant consent flag, human review, and a dataset-manifest entry. No automatic path exists or will be built in v0.
- Language tags follow the manifest `languages` field plus locale tags `en-ZA`, `af`, `zu`, `xh`, `st`, `tn`, `sw`, `fr`, `pt` reserved in the dataset schema for §60.

## 5. Automated controls to implement in Phase 2

1. Secret detection (gitleaks rules + custom regexes for `pak_`, `aps_`, `mia_pk_`, VAPID, Paystack/Yoco keys) on every extracted example — blocking.
2. PII detection (SA ID numbers `\d{13}` with Luhn, SA mobile numbers, emails, street addresses, IBAN/bank account patterns) — blocking unless the example is `synthetic=true` with placeholder markers.
3. Brand and infrastructure scrub (`docs/marketplace/BRAND_SCRUB.md` rules, server IPs, hostnames, file paths).
4. Eval-contamination check for catalogue packages: flag knowledge paragraphs that verbatim match `says_any` strings of the same package.
5. Family-aware dedup (MinHash over `system_prompt` + tool names) with a manifest field `duplicate_of`.
