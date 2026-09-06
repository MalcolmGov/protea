# ADR-007 — Evaluation framework and the ZaraBench suite

**Status:** accepted · **Date:** 2026-09-06 · **Phase:** 3

## Context

Nothing in the estate measures a model against Zara's own work (repository audit §1.11). The specification asks for a ten-category benchmark with a weighted `ZaraScore`, a sealed golden set, baselines of the unmodified base model and a frontier model before any training, and reports that carry cost and latency (§22–§25, §67). The strategy review adds a kill criterion (A1) and judge independence (A6).

## Decisions

1. **One task contract, one expectation grammar.** `EvalTask` = messages to play + tools + canned tool results + `Expect`. `Expect` is a superset of the estate's `evals.jsonl` grammar (`tool`, `tool_any`, `tool_none`, `no_tool`, `says_any`, `says_none`, `refuses`, `lang`) so the 8,782 seeds translate without loss, plus the checks ZaraBench needs: JSON strictness, JSON Schema validity, field equality, required keys, tool/connector allowlists, expected bindings, workflow DAG rules, argument subsets, word limits, rubric.
2. **Every check is a named pass/fail; two scores.** A task's score is the fraction of checks passed, so a partially right answer earns partial credit and every failure has a name that aggregates into the failure-mode histogram. `ZaraScore` is the weighted mean of category scores; `ZaraScore (strict)` uses pass rates, where a task passes only if every check passes. Gates read the partial-credit score; both are reported so an empty reply cannot hide behind negative checks.
3. **Rules first, judge second, never silently.** Refusal, reply language and rubric alignment need an LLM judge. Without one those checks are reported as *skipped* and the report is marked `partial`; they are never scored as failures or successes. The judge may not be the model under test and, when the config demands it, may not be any model recorded as a synthetic-data generator in the dataset registry.
4. **The reference provider proves the benchmark.** Every rule-checkable task carries a reference answer. `protea evaluate run --provider reference` must score 100%; `evaluate author` refuses to write a task set that fails this, and CI runs it on the committed set. A benchmark whose own answers fail its evaluators is a bug, not a hard benchmark.
5. **Sealed and fed back into the data pipeline.** `evaluate seal` writes `golden.lock` (sha256, counts, held-out families); `evaluate verify` runs in CI and fails on drift or fewer than 150 tasks. The lock's families are honoured by `dataset synthesize` (seeds skipped) and by `dataset build` when `holdout_lock` is set (forced to the test split), so the hold-out survives future dataset versions.
6. **Derived categories come from the catalogue, not from prose.** Hallucination removes the tool an eval expects and forbids the facts that tool would have supplied; failure recovery returns an error from that tool; safety injects an override into a tool result and forbids the write tool it names; instruction following adds format constraints to real requests; workflows are designed for real requests with real tool lists; business reasoning keeps the knowledge excerpt and asks the judge to grade grounding. Each carries provenance to the package and eval it came from.
7. **Execution boundary at the provider.** Any provider that spends tokens prints the §52 pre-flight (tasks, estimated tokens and cost, families exposed) and refuses without `--confirm`. The base-model baseline additionally needs a GPU host or a hosted endpoint; that decision is the user's.

## Consequences

- ZaraBench 0.1 has 206 tasks over 37 held-out families (roadmap minimum 150), all with references; 43 need a judge.
- The suite is English-heavy (179 of 206). Language slices exist and are reported, but Africa-track claims need authored non-English tasks in 0.2.
- References are minimal correct answers, not gold-standard prose; a model can beat the reference on quality and still score the same. Quality differences show up only through the judge and the rubric tasks.
- Family hold-out is per task type in dataset 0.1.0 (splits hash `task_type:family`); a family can be in training for `agent_generation` and in the benchmark for `tool_calling`. The lock closes this for dataset 0.2 onward via `holdout_lock`.
- Data finding for the Phase 2 backlog: every connector-selection preset in the held-out set binds at least one connector that the catalogue text does not list (`slack` in all 19 cases). The task prompts append those connectors so the expected answer is reachable; the presets or the catalogue should be reconciled in `aria`.
