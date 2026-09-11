# ADR-015 — Trajectory scoring: grade the process, not only the outcome

**Status:** accepted · **Date:** 2026-09-11 · **Scope:** evaluation (ADR-007), capability spec (ADR-014)

## Context

ZaraBench scores the **final state** of a task: which tools ended up called (as a *set* — `tool`, `tool_any`,
`tool_none`, `no_tool`), the final assistant text, and the structured output. That is the right grade for a
single-shot answer, but an agent's value is in the *sequence* it takes to get there, and the harness already
records that sequence and throws most of it away at scoring time.

`protea/evaluation/driver.py` builds a `Transcript` for every task that captures the whole trajectory:

- `tool_calls` — **every call, in order** (not a set).
- `rounds` — how many model turns it took.
- `truncated` — whether it was still calling tools at the round limit.

Today `evaluate()` reads only the set of names (`t.called`), the final text, and one `turn_completed` check off
`truncated`. Nothing grades:

- **Order** — a "write-after-confirm" task is only correct if the confirm/lookup happens *before* the write; a
  workflow is only right if the trigger precedes the action. A model that books before it looks up scores the
  same as one that does it right.
- **Efficiency** — taking three rounds and five tool calls to do a one-call job is a worse agent, and a more
  expensive one to serve, but scores identically.
- **Looping** — calling the same tool with the same arguments twice (a thrash the round limit hides) is a real
  failure mode with no name today.

This is the gap the strategy review's "trajectory eval" names, and closing it needs **no new data** — only new
checks over the `Transcript` the driver already produces.

## Decision

1. **Add an optional trajectory grammar to `Expect`; grade it from the existing `Transcript`.** Three checks,
   all computable from data already captured, all opt-in (a task that sets none behaves exactly as today):

   | Field | Check name | Semantics (over `t.tool_calls` / `t.rounds`) |
   |---|---|---|
   | `tool_order: list[str]` | `tool_order` | the listed tools appear as an **ordered subsequence** of the calls made (others may interleave; each listed tool must occur, in this relative order) |
   | `max_rounds: int` | `within_round_budget` | `t.rounds <= max_rounds` — a per-task efficiency ceiling, distinct from the driver's global `MAX_TOOL_ROUNDS` safety cap |
   | `no_repeat_calls: bool` | `no_repeat_calls` | no two calls share the same `(name, arguments)` pair — catches loops/thrash |

2. **Trajectory checks live inside the existing categories — no new ZaraBench category.** They attach to
   `tool_calling` (order, no-loop, budget), `failure_recovery` (recover with a *different* next step, not a
   repeat of the failed call), and `workflow_generation` (trigger-before-action at execution time, not only in
   the emitted DAG). Because no category is added, the category weights, the release gates, and the
   capability-spec ↔ eval-config bijection (`test_capability_spec.py`) are all untouched. The capability
   contract gains a *trajectory dimension* on those three capabilities, recorded in `docs/capability-spec.yaml`.

3. **Rollout is additive and non-breaking; the sealed 0.1 suite does not move.** The new `Expect` fields are
   optional and the sealed 0.1 tasks set none, so:
   - the sealed hash is unchanged — `task_set_hash` hashes the tasks *file*, and no task file changes;
   - every existing category score and ZaraScore is unchanged — a task with no trajectory field gets no
     trajectory check;
   - the reference provider still scores 100% — it emits `reference.tool_calls` in listed order, so a correctly
     authored reference satisfies `tool_order` / `no_repeat_calls` and completes within any sane `max_rounds`
     (ADR-007 §4 preserved).
   Trajectory expectations are **authored into dataset/benchmark 0.2** on the categories above and the set is
   re-sealed with review (`evaluate seal`), exactly as any task-set change.

4. **Implementation is deferred to the 0.2 authoring pass (post-pilot).** This ADR fixes the grammar and the
   semantics so authoring 0.2 tasks and writing the evaluators is mechanical; it does **not** change
   `evaluators.py` / `tasks.py` yet. Shipping evaluators with no task that exercises them beyond unit tests
   would be motion without signal — the checks land in the same change that authors the tasks that use them,
   the way ADR-007 specified the grammar before the tasks existed.

## Consequences

- **When implemented**, each new field is added to `Expect` (which is `extra="forbid"`, so this is a reviewed
  schema change), graded by a new `_trajectory_checks(e, t)` appended in `evaluate()`, and covered in
  `tests/test_evaluators.py` alongside a reference-satisfiability case (`--provider reference` = 100%). The
  capability-spec entries for `tool_calling`, `failure_recovery` and `workflow_generation` gain a `trajectory`
  note; the consistency test is unaffected (it does not key on trajectory).
- **Efficiency becomes visible without becoming a hidden gate.** `within_round_budget` is a per-task check that
  contributes to the category's partial-credit score like any other; it is *not* wired into serving cost or the
  release gates here. Turning trajectory quality into a release floor is a later, baseline-dependent step
  (ADR-013), not this one.
- **Failure-recovery gets teeth.** Today `failure_recovery` checks that a tool was called and no fact was
  fabricated; with `no_repeat_calls` (and an authored `tool_order` where a fallback tool is expected) it can
  distinguish "saw the error and tried something else" from "called the failing tool again".
- **No base-model coupling.** Order, efficiency and non-looping are properties of the agent behaviour Protea
  encodes, not of Qwen3-8B; the grammar carries across a base-model swap like the rest of the capability spec.
