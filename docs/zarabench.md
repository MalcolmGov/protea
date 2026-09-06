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
| `protea evaluate compare candidate.json --base base.json --frontier frontier.json` | category table, release-gate decision, kill criterion | no |

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

## Task file format

One `EvalTask` per line (`protea/evaluation/tasks.py`): `id`, `category`, `language`, `family`, `source` (repo, commit, path, id), `messages` (system + first user turn), `followups`, `tools`, `tool_results` (canned results per tool, cycled), `expect`, `reference`. Every task in 0.1 has a reference; 43 also need a judge.

## Running the baselines (execution boundary)

The roadmap requires two baselines before training: the unmodified candidate (Qwen3-8B) and one frontier model. Neither is run automatically.

- **Frontier.** `protea evaluate run --provider anthropic --model claude-opus-5 --judge-provider anthropic --judge-model claude-sonnet-5 --confirm`. Pre-flight estimate for the full suite: ≈640 k input tokens, ≈85 k output tokens, ≈USD 5 at the prices in the config (update them first). The judge adds roughly the same input volume again. Task prompts contain catalogue system prompts and business knowledge; that content leaves the estate.
- **Candidate.** Serve `Qwen/Qwen3-8B` with vLLM (Phase 5 container or any OpenAI-compatible host), set `PROTEA_INFERENCE_URL`, then `protea evaluate run --provider protea --model Qwen/Qwen3-8B --confirm`. Needs a 24 GB-class GPU for ≈1–2 hours, or a hosted endpoint.
- **Judge independence.** The judge must differ from the model under test and from any `generator_models` in `registry/datasets.json`.

Commit the resulting JSON and Markdown under `evaluation/reports/` and reference them from `docs/model-selection.md`.

## Release gate and kill criterion

`evaluate compare` applies: candidate ≥ base on every `priority_categories` entry; candidate ≥ frontier on `frontier_gate_categories` (structured_output); per-category `min_score` and `release_min_zarascore` from the config; the candidate report must not be partial. It also flags the kill criterion (strategy review A1): candidate ZaraScore below `kill_fraction_of_frontier` × frontier ZaraScore recommends stopping training. Exit code 2 when the gate fails so a pipeline can stop on it.

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

## Credentials in cloud sandboxes

Claude Code cloud environments reserve `ANTHROPIC_API_KEY` for the session's own account and drop it from the
sandbox. Set `PROTEA_ANTHROPIC_API_KEY` (or `PROTEA_OPENAI_API_KEY`) there instead; Protea reads either name.

## Changelog

- **0.1.1** — after the first frontier baseline (Claude Sonnet 5, 81% ZaraScore) three expectation families were
  found stricter than the contract they test, and were tightened: spec-shaped prompts now state the catalogue's
  allowed `category` and `tier` values (the check is exact, so the prompt must say what is allowed); connector
  bindings accept the catalogue's binding, any offered connector of the same category, or anything for the
  generic `webhook` fallback; "facts" a model must not invent are only numbers, amounts and names, never generic
  words or weekdays; and the injection probe no longer treats the words "system prompt" as leakage (the canary is).
  Reports from 0.1.0 (`evaluation/reports/zarabench-0.1.0/`) are kept but are not comparable.
- **0.1.0** — sealed set of 206 tasks across 10 categories (Phase 3).
