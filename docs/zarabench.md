# ZaraBench — running and extending the evaluation suite

ZaraBench is the Zara task suite inside Protea's generic evaluation framework (`protea/evaluation/`). Version 0.1 is sealed in `evaluation/zarabench/0.1/` and configured by `configs/evaluation/zarabench-0.1.yaml`. Design decisions are in `adr/ADR-007-evaluation-framework.md`.

## Commands

| Command | What it does | Spends money |
|---|---|---|
| `protea evaluate tasks` | counts per category and language, judge-dependent tasks, held-out families | no |
| `protea evaluate author <build_dir>` | regenerate the task set from a `dataset build` output; refuses if any reference fails its own checks | no |
| `protea evaluate seal` / `verify` | hash-pin the task set and record held-out families / fail on drift (CI) | no |
| `protea evaluate run --provider reference` | prove the evaluators are satisfiable (must be 100%) | no |
| `protea evaluate run --provider mock` | harness smoke run | no |
| `protea evaluate run --provider <name> --confirm` | baseline of a real model; prints the pre-flight first | **yes** |
| `protea evaluate compare candidate.json --base base.json --frontier frontier.json` | category table, release-gate decision, ADR-016 floors, kill criterion | no |
| `protea evaluate audit` | score a **content-free stub** on the sealed suite: the floor of the instrument | no |
| `protea evaluate harden` | derive content floors from a suite's references and write the next version (deterministic, refuses an unsatisfiable suite) | no |

Reports land in `evaluation/reports/<suite>-<version>/<provider>-<model>-<run>.json|md`. Mock and reference reports are git-ignored; real baselines are committed.

## Categories and evaluators

| Category | Weight | Source of tasks | Checks |
|---|---|---|---|
| agent_generation | 0.20 | held-out packages → brief | bare JSON, AgentSpecLite schema, category/tier/channels/languages equal, no invented tools |
| structured_output | 0.15 | manifests, flagship specs, routing lanes | bare JSON, manifest schema or required keys, field equality, exact lane |
| tool_calling | 0.15 | held-out evals | tool grammar (`tool`, `tool_any`, `tool_none`, `no_tool`), declared tools only, `says_any`/`says_none`, arguments |
| connector_selection | 0.10 | presets | one check per expected binding, no connector outside the catalogue |
| workflow_generation | 0.10 | evals → design prompt | nodes/edges shape, known node types, edges resolve, acyclic, trigger + action present, required tool used, no invented tools |
| business_reasoning | 0.10 | evals answered from knowledge | facts present, no tool call, rubric (judge) |
| failure_recovery | 0.05 | evals with an erroring tool | tool called, apology/next step, no fabricated facts |
| safety | 0.05 | refusal evals; injected tool results | refusal (judge), no card capture tool, forbidden write tool avoided, canary and system prompt never echoed |
| hallucination | 0.05 | evals with the needed tool removed | hedge phrases present, tool-derived facts absent, no undeclared tool call |
| instruction_following | 0.05 | language evals; format constraints | reply language (judge), word limits, exact JSON keys |

Score per task = checks passed / checks. Category score = mean task score. `ZaraScore` = Σ weight × category score. The strict variant uses pass rates. A report is `partial` when judge checks were skipped or a category has no tasks.

### The instrument's floor — run `evaluate audit` before quoting a score

A suite is only informative when its floor is low. `protea evaluate audit` scores a **content-free stub** — schema-shaped JSON with `"x"` for every string, the one declared tool call, no hedging, no facts — through the same evaluators. It needs no model, no GPU and no judge, so it is free to run and cheap to re-run after any evaluator change.

Measured on the sealed 0.1.1 set (2026-09-16):

| Category | Weight | Stub mean | Stub full marks |
|---|---|---|---|
| agent_generation | 0.20 | **1.00** | 25/25 |
| structured_output | 0.15 | 0.89 | 25/32 |
| safety | 0.05 | 0.82 | 12/20 |
| failure_recovery | 0.05 | 0.68 | 0/15 |
| instruction_following | 0.05 | 0.57 | 7/20 |
| business_reasoning | 0.10 | 0.51 | 0/15 |
| workflow_generation | 0.10 | 0.50 | 0/15 |
| connector_selection | 0.10 | 0.35 | 0/19 |
| tool_calling | 0.15 | 0.28 | 2/30 |
| hallucination | 0.05 | 0.10 | 0/15 |

**Stub ZaraScore: 62.1% (strict 37.5%)** against a frozen base of 80.7%. `agent_generation` — the highest-weighted category, and the one the P0/P0.1/P0.2 adapters were optimised against — is satisfied outright by the stub, because its checks are structural: a 70-token object with `"x"` for every string scores 1.00. `failure_recovery` and `safety` are the same class of hole.

What this means for interpretation: a ZaraScore above ~62% is not by itself evidence of capability, and a *small* score delta between two models is not evidence of anything until each category's checks require content. The audit exists so this number is quoted **next to** every ZaraScore rather than being discovered later. Use `--json` for the machine form and `--max-zarascore <share>` to make CI fail when the floor creeps back up (see the note at the end of this document).

> The related claim in `experiments/agent-generation-rootcause.md` — that six `agent_generation` tasks are unsatisfiable because their reference answers exceed the 4000-token budget — is **wrong** and carries a dated correction there. Those tasks are satisfied by a concise answer; the defect is that nothing in the category requires one.

### ZaraBench 0.2 — content floors (2026-09-16)

0.2.0 is 0.1.1 plus content floors derived from each task's own reference by `protea evaluate harden`, which is deterministic and refuses to write a suite whose references no longer pass their own harder checks. The sealed 0.1.1 set is untouched, so the P0–P0.2 lineage stays comparable.

The floors (all derived, none hand-typed):

| Check | Rule | Why that shape |
|---|---|---|
| `field_len:<path>` | a string field whose reference is ≥ 40 chars must reach `max(80, min(400, 10% of the reference))` | the absolute floor kills `"x"`; the cap stops a long reference demanding a long answer and rebuilding the truncation trap behind the P0 collapse |
| `field_items:<path>` | a list field with ≥ 1 reference item needs ≥ 1 item | an empty `tools: []` is not a plan |
| `min_words` | a prose reference ≥ 24 words sets ≥ 12 words (never more than the reference itself) | a two-word refusal reference keeps a two-word floor, so a correct terse refusal is not punished |

**Result: the stub floor falls from 62.1% to 47.9%, and the strict floor from 37.5% to 5.8%.** `agent_generation` goes from 25/25 full marks to 0/25 (mean 1.00 → 0.61), `structured_output` from 25/32 to 0/32, and no category is fully satisfied by a stub any more.

Two honest limits:

- **These are floors, not quality bars.** A padding model satisfies every one of them, and none reads for truth. Their job is to shrink the free band so a score difference means something; the judge (ADR-007) is still the path to grading content.
- **The remaining 47.9% is not a derivation problem.** It sits in the categories whose deterministic checks are "did not do the forbidden thing" (a useless reply passes those vacuously) and whose judge checks are skipped: `safety` 0.82, `instruction_following` 0.57, `business_reasoning` 0.51. Closing those needs authored expectations (a refusal vocabulary per language, realistic answers) or a judge — see `evaluation-review.md` F10.

Because partial credit is still shape-dominated on 0.2, **quote the strict pass rate** (`zarascore_strict`) alongside the score for any 0.2 comparison, and note it next to the run's floor (see `evaluate audit`).

### The release gate

The gate is `protea evaluate compare`. Blocking checks:

1. **Comparability** — same suite, version and task-set hash (a cross-version comparison is refused, not scored).
2. **Priority categories** (`priority_categories`) — candidate ≥ base, strictly.
3. **ADR-016 budgets** — every relative tier must be within `base − tier budget`, with `tier_budgets` in the config kept equal to `docs/capability-spec.yaml::regression_budgets` by a test (`priority 0.02`, `supporting 0.05`, `frontier_gate 0.00`). Floors are *derived* from whichever baseline is passed, so moving to a different base model needs no edit here; `compare` prints each floor with its arithmetic.
4. **Absolute floors** (ADR-014) — the tiers listed in `absolute_tiers` (today `guardrail`) are **never** graded against the base: "a base that was already unsafe is not a licence to ship unsafe". They take their floor from an explicit `min_score`, and while none is set the comparison prints `not set — this tier is unfloored` (the current state: safety and hallucination have an open product decision).
5. **Frontier gate** — candidate ≥ frontier on `frontier_gate_categories`, when a *comparable* frontier report is supplied (same suite, version and task-set hash).
6. **Completeness** — a `partial` report cannot release while `require_complete_report_for_release: true` (today's default, because `judge_provider` is null and 43/206 tasks carry a judge-dependent check). Relaxing that flag is a decision, and the uncovered weight is then reported as an advisory.

Advisory, never blocking: a **strict pass rate** below base's. A rising mean can hide growing hard failures — that was the P0.1 anomaly (pass rate 8% → 16% while the mean fell 72 → 56) — so it is surfaced next to the decision.

**Which number the gate judges is config**: `gate_metric: score | strict`. 0.1.1 uses `score` so its committed
reports keep their meaning; **0.2 uses `strict`**, because partial credit stays shape-dominated even with content
floors (a stub takes 47.9% of the score but 5.8% strict). Under `strict`, the priority checks, the derived floors
and the kill criterion all use pass rates, and the pass-rate advisory is dropped (it *is* the gate). `compare`
names the metric in its verdict line.

## Task file format

One `EvalTask` per line (`protea/evaluation/tasks.py`): `id`, `category`, `language`, `family`, `source` (repo, commit, path, id), `messages` (system + first user turn), `followups`, `tools`, `tool_results` (canned results per tool, cycled), `expect`, `reference`. Every task in 0.1 has a reference; 43 also need a judge.

## Running the baselines (execution boundary)

The roadmap requires two baselines before training: the unmodified candidate (Qwen3-8B) and one frontier model. Neither is run automatically.

- **Frontier.** `protea evaluate run --provider anthropic --model claude-opus-5 --judge anthropic:claude-sonnet-5 --confirm`. Pre-flight estimate for the full suite: ≈640 k input tokens, ≈85 k output tokens, ≈USD 5 at the prices in the config (update them first). The judge adds roughly the same input volume again. Task prompts contain catalogue system prompts and business knowledge; that content leaves the estate.
- **Candidate.** Serve `Qwen/Qwen3-8B` with vLLM (Phase 5 container or any OpenAI-compatible host), set `PROTEA_INFERENCE_URL`, then `protea evaluate run --provider protea --model Qwen/Qwen3-8B --confirm`. Needs a 24 GB-class GPU for ≈1–2 hours, or a hosted endpoint.
- **Judge independence.** The judge must differ from the model under test and from any `generator_models` in `registry/datasets.json`.

Commit the resulting JSON and Markdown under `evaluation/reports/` and reference them from `docs/model-selection.md`.

## Kill criterion

Strategy-review A1: a candidate ZaraScore below `kill_fraction_of_frontier` × the frontier ZaraScore recommends
stopping training. `evaluate compare` exits 2 when the gate fails, so a pipeline can stop on it.

> **Known defect (2026-09-16):** this criterion cannot fire while the frontier baseline is not materially better
> than the base. The committed frontier baseline (Sonnet 5, 80.9% on 0.1.0) sits 0.2 points above the base
> (80.7%), so the configured floor computes to 64.7% and every rung clears it — and a content-free stub at 62.1%
> very nearly does too. Re-anchor the criterion once a same-version frontier baseline exists: `evaluation-review.md` F1.

## Wiring the instrument floor into CI

`evaluate audit` is free and deterministic, so it belongs beside `evaluate verify`. Report-only until the
content holes above are closed, then make the ceiling explicit so the floor cannot creep back up:

```yaml
- run: protea evaluate audit --max-zarascore 0.40   # a ZaraScore that a stub can reach proves nothing
```

## Extending the suite

- Add tasks by hand in the same JSONL format, or re-run `evaluate author` against a newer dataset build; then `evaluate seal` (a reviewed change: the hash and the family list change).
- New evaluators go in `protea/evaluation/evaluators.py` as named checks; add a field to `Expect` and a test in `tests/test_evaluators.py`.
- Non-English coverage is the first gap: author af/zu/xh/st/tn/sw tasks under `instruction_following` and `tool_calling` for 0.2.

## Progress on long runs

`protea evaluate run` and `protea security run` print one line per finished task to stderr:

```
[ 12/206] 0:04:10 eta 1:07:20  agent-gen-en-0007  0.75
```

The columns are the running count, elapsed time, a linear ETA from the average time per task so far, the
task id and the deterministic score (`ERR` when the provider failed). The lines go to stderr only; the JSON and
Markdown reports are unchanged.

> **Note on the 4000-token budget.** Six sealed `agent_generation` references exceed `max_tokens` when estimated
> at chars/4. They are *not* unsatisfiable — a ~70-token answer scores 1.00 on all six — so this is a question
> about what the category demands, not a broken task. Raising `max_tokens` would change the config hash and
> require re-baselining; treat it as a decision, not a fix.

## Credentials in cloud sandboxes

Claude Code cloud environments reserve `ANTHROPIC_API_KEY` for the session's own account and drop it from the
sandbox. Set `PROTEA_ANTHROPIC_API_KEY` (or `PROTEA_OPENAI_API_KEY`) there instead; Protea reads either name.

## Changelog

- **0.2.0 (2026-09-16)** — content floors derived from the references by `evaluate harden` (`field_len:*`,
  `field_items:*`, `min_words`), added to close the stub hole measured on 0.1.1 (62.1% → 47.9%; strict 37.5% →
  5.8%). The task text, prompts and expectations are otherwise identical to 0.1.1; only the floors are new, and
  0.1.1 stays sealed for the P0–P0.2 lineage. Reports from the two versions are **not** comparable.
- **gate and instrument (2026-09-16, no task-set change)** — the gate now executes ADR-016: `tier_budgets` in the
  config are the single source, per-category floors are derived from whichever baseline is passed, the strict
  pass rate is reported as a non-blocking advisory, and a `partial` report needs
  `require_complete_report_for_release: false` to release. `protea evaluate audit` was added and measured
  (stub ZaraScore 62.1%). See `docs/evaluation-review.md`.
- **0.1.1** — after the first frontier baseline (Claude Sonnet 5, 81% ZaraScore) three expectation families were
  found stricter than the contract they test, and were tightened: spec-shaped prompts now state the catalogue's
  allowed `category` and `tier` values (the check is exact, so the prompt must say what is allowed); connector
  bindings accept the catalogue's binding, any offered connector of the same category, or anything for the
  generic `webhook` fallback; "facts" a model must not invent are only numbers, amounts and names, never generic
  words or weekdays; and the injection probe no longer treats the words "system prompt" as leakage (the canary is).
  Reports from 0.1.0 (`evaluation/reports/zarabench-0.1.0/`) are kept but are not comparable.
- **0.1.0** — sealed set of 206 tasks across 10 categories (Phase 3).
