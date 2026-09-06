# ADR-011 — Production hardening: security suite, release pipeline, canary and rollback, load test, economics

**Status:** accepted · **Date:** 2026-09-06 · **Phase:** 10

## Context

Phases 1–9 give the platform a data pipeline, a sealed benchmark, a trainer, a facade, a router and observability. What
was missing before any Protea model can carry production traffic: proof that a served model does not do the harmful
thing under adversarial input, a promotion path whose every step is backed by evidence on disk, a way to move traffic
gradually and to move it back in one command, a measured latency baseline for the facade, and the economic model
(spec §69) that decides whether self-hosting is worth it at all (strategy review A1).

## Decisions

1. **The security suite is deterministic and sealed.** `protea/evaluation/security.py` generates 19 probes in six
   families — prompt injection in the user turn, injection through tool results, unauthorised tools, cross-tenant
   requests, secret exfiltration, PII read-back — with planted secrets and only deterministic checks (`tool_none`,
   `tool`, `must_not_include`, `no_tool`). No LLM judge, so a security report is never partial and can gate a release
   in CI. The suite is sealed (`evaluation/security/0.1/golden.lock`) and every probe carries a reference answer; the
   reference run must score 1.0. It measures "does not do the harmful thing", not tone: a model that answers with a
   bare "OK" passes probes that only forbid actions and fails the ones that require the legitimate tool call.
2. **Release is a pipeline of checks with evidence paths.** `protea/release/pipeline.py` evaluates train (registry
   entry with checkpoint, artifact hash, commit), validate (model card present, training dataset registered and
   hash-verified), ZaraBench (latest non-mock report for the model, not partial, not stale, ADR-007 release decision
   against base and frontier reports when present, kill criterion), security (strict score and per-family floors),
   compare production (no priority-category regression against the family's production model). Promotion moves the
   registry one step — experimental → candidate → staging → production — and only to the step the evidence supports.
   `configs/release/*.yaml` (kind `release`) holds the gates, canary steps and rollback triggers.
3. **Canary is the routing policy's tenant share.** "Canary" is not a registry status: it is `canary_percent` on the
   routing policy (ADR-010), raised through configured steps (5 → 25 → 100) once a model is in staging, with a minimum
   soak per step. The pipeline edits exactly that line of the YAML and re-validates the file, so comments and the rest
   of the policy survive. Production is granted only at 100 %.
4. **Rollback is one command and reversible history.** `release rollback` sets the canary to zero, deprecates the
   production model and reinstates the previous production model of the family when one exists (deprecated → staging →
   production, the transition the registry already allows). `release watch` evaluates the rollback triggers
   (validation pass rate, fallback rate, error rate, p95 latency, negative feedback share, with a minimum call count so
   a quiet window never triggers) against an aria observability summary and can roll back automatically. Every action
   appends to `registry/release-log.jsonl`.
5. **Load test in the repo, not in a vendor tool.** `protea serve loadtest` fires concurrent `/v1/generate` calls at a
   facade and reports p50/p95/p99, throughput, output tokens per second and error rate; a non-local URL needs
   `--confirm` because the backend may bill per token. Tests run it in-process against the facade with the mock backend.
6. **The economic model is a config and a command.** `configs/economics/*.yaml` (kind `economics`) holds the request
   forecast per task type, the frontier price mix, the serving GPU with an assumed or measured throughput, and the
   overheads (training iterations, engineering, operations, fallback share). `protea economics report` prints both
   monthly costs, the savings, break-even volume and utilisation, and the kill signal from the strategy review: when
   break-even needs more than a configured multiple of the forecast, the verdict says so and the command exits 2.
   The committed inputs are honest: at today's forecast the GPU is 2 % utilised and frontier tokens are cheaper.

## Consequences

- CI runs the security suite against the reference provider and the gate on that report, so a broken probe or a
  regression in the evaluators fails the build; a real model's security report is produced the same way and committed
  alongside its ZaraBench report before promotion.
- The first promotion of a Protea model needs a non-partial ZaraBench report, which needs the LLM judge (an execution
  boundary: tokens). Until then `release check` says exactly what is missing.
- Rollback triggers read aria's summary payload (Phase 9); the loop is closed by an operator running `release watch`
  on a schedule or by the console posting the summary — automation of that schedule is out of v0 (strategy review).
- `docs/security.md`, `docs/deployment.md` and `docs/operations.md` are the runbooks; they replace the planned entries
  in the documentation index.
