# The Protea capability spec

`docs/capability-spec.yaml` is the behavioural contract for Protea: **what the model must be able to do**,
stated independently of the base model beneath it. It is the first of the five things CLAUDE.md says Protea
*is* — "capability spec + datasets + ZaraBench + training recipes + versioned adapters" — written down
explicitly rather than left implicit in the benchmark. The binding decision is ADR-014.

## Why it exists

The base model can change (Qwen3-8B → 14B → another open-weight base) without changing what Protea must do.
The capability spec is the part that survives that swap. It also gives the release gates their meaning: the
per-category `min_score` values in `configs/evaluation/zarabench-0.1.yaml` are `0.0` placeholders today
(ADR-013 holds them there until a frozen baseline exists), and this spec records the *rule* each one will
follow and *why the capability matters commercially* — so setting the numbers later is derivation, not
invention.

## How the three artifacts divide the work

| Artifact | Role | Owns |
|---|---|---|
| `configs/evaluation/zarabench-0.1.yaml` | **Instrument** | category weights, the ZaraScore formula, judge discipline, the numeric gates |
| `configs/release/zara-v0.yaml` | **Pipeline** | the release rules that read those gates, the security suite, canary/rollback |
| `docs/capability-spec.yaml` | **Contract** | what each capability means, why it matters, its tier and its rule-in-words |

Each fact lives in exactly one place. The spec never restates a weight or a gate number — it points at the
category that carries them. `tests/test_capability_spec.py` binds the three: every capability must be a real
ZaraBench category (a bijection — no gaps, no invented categories), and the tiers must agree with the eval
config's `priority_categories` and `frontier_gate_categories`.

## Reading an entry

Each capability carries: `statement` (what Protea must do), `why` (the commercial rationale), `tier`,
`evidence` (the ZaraBench category and its checks), `release_rule` (in words), `gate_status`, and sometimes
`known_gaps`. The four tiers, by how hard a regression bites a release:

- **frontier_gate** — must *reach* the frontier model, not merely beat the base (`structured_output`). This is
  where a buyer compares Protea head-to-head; a regression blocks release outright.
- **priority** — the load-bearing agent behaviours (`agent_generation`, `tool_calling`,
  `connector_selection`); must be ≥ the frozen base and ≥ current production.
- **guardrail** — `safety` and `hallucination`; graded against an **absolute** floor, never "≥ base".
- **supporting** — the rest; must not regress against production.

## Setting the gates (the deferred step)

When a clean frozen baseline exists (vanilla Qwen3-8B and a true v0.1 adapter), each numeric gate is read off
this contract: frontier_gate → the frontier score; priority → the frozen-base score, tightened toward
production; guardrail → an argued absolute floor (safety and hallucination should *not* stay at 0.0 once real
numbers exist); supporting → the production score. Until then the gates stay `0.0` and a release is blocked by
"report not partial" plus the security suite — never by training loss.

## Changing the spec

A new capability or a tier change is a reviewed edit; the consistency test gates it, the same way
`evaluate seal` gates a change to the sealed task set. If gate floors are ever wired into
`protea evaluate compare` directly, the spec graduates into a schema-validated config kind under `configs/`
(ADR-014, "Graduation path"). It is versioned (`0.1.0`) alongside ZaraBench `0.1.x`.
