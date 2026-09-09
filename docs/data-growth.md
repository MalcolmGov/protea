# Growing the agent training data

How to grow the training set behind the Protea commercial agent adapter (`protea-agent`), and the guardrails
that keep the growth honest. Companion to `dataset-design.md` (how the dataset is built) and `training.md` (how
it is trained). CitizenAI's data has its own deterministic generator (`dataset author-citizen-train`); this
document is about the **mined + synthesised** agent set.

## What the set is today (0.1.0)

Mined from the pinned source catalogues — **aria** (504 agent packages) and **miai-agents** (69) — by
`protea dataset build`:

| | |
|---|---|
| Examples | 2,041 → **train 1,740 · validation 115 · test 134 · golden 52** |
| Families / domains | 232 / 14 · languages en 1,712, en-ZA 329 · ~3.1M tokens |
| Recipes | structured_output 588 · agent_generation 573 · connector_selection 551 · routing 329 |
| Tool-calling seeds | **8,782** (for eval-checked synthesis; not training rows themselves) |
| Dropped | 600 near-duplicates · 224 contaminated seeds · 15 rejected ("missing expect") |

Two things to notice: there are **no `tool_calling` training rows** in the mined set (that family is created by
*synthesis* from the seeds), and PII scrubbing is active (411 phone / 136 address / 73 email redactions).

## The levers, cheapest first

### 1. Recover the 15 rejects (free — a data fix in `aria`)
Fifteen agent turns are dropped because their `*.agent.json` entries have no `expect` block
(e.g. `data/agents/*-accounting-practice.agent.json#onboarding-executes`). Adding an `expect` to each recovers
them as clean examples. This is an edit in the **aria** repo, not here.

### 2. Enable the holdout lock — `agent-training-0.2.yaml` (free, in this repo)
0.2 turns on `holdout_lock: evaluation/zarabench/0.1/golden.lock`. Any example whose family is sealed in
ZaraBench is then forced **out of train/validation and into test**, so the benchmark can never leak into
training. Measured effect vs 0.1: 256 sealed-family rows move to test (**train 1,740 → 1,490**, test 134 → 390).
That is the deliberate trade — ~250 fewer training rows for an eval you can trust. Use 0.2 for any run whose
score you intend to quote.

### 3. Add source content (grows with the catalogue, in `aria`)
Each agent package yields ~4 examples (manifest / system prompt / tools / connector binding). Growth here is
linear in the catalogue: more agents, presets, or registry entries in aria → more rows on the next build.

### 4. Synthesis — the big lever (**paid**, teacher-policy C3)
The 8,782 seeds are eval-checked prompts: a **teacher model** completes each one, and only completions that
satisfy the seed's own `expect` (the right tool called, the right thing said) are kept. This is what creates
`tool_calling` volume at scale. It spends teacher tokens, so it is gated on the C3 decision (which teacher, what
budget).

## Commands

Build the dataset (writes to `protea_data/…`, which is gitignored — proprietary data stays out of the repo):

```bash
PROTEA_SOURCE_ARIA=../aria PROTEA_SOURCE_MIAI_AGENTS=../miai-agents \
  protea dataset build --config configs/datasets/agent-training-0.2.yaml     # --dry-run for counts only
```

Synthesise `tool_calling` rows from the seeds it produced:

```bash
protea dataset synthesize protea_data/agent-training/0.2.0/seeds/tool_calling_seeds.jsonl \
  --provider anthropic --model claude-sonnet-5 \
  --golden-lock evaluation/zarabench/0.1/golden.lock \
  --limit 300 \
  --out protea_data/agent-training/0.2.0/synthetic_tool_calling.jsonl \
  --confirm                     # --confirm is required for any provider that spends tokens
```

Kept completions carry the teacher as their `generator_model` and go through the normal review lane before they
train. Merge them into the training split once reviewed.

### Reviewing what you synthesised (free, before it trains)

Synthesis only gates *correctness* (the seed's `expect`). The softer checks `dataset build` applies to mined
rows — secret scan, PII scan, golden-lock leakage, dedup, degenerate/refusal targets — have **not** run on the
kept rows yet, because they never pass through a build until you blend them in. Run the review gate first:

```bash
protea dataset review protea_data/agent-training/0.2.0/synthetic_tool_calling.jsonl \
  --reference protea_data/agent-training/0.2.0/train.jsonl \
  --golden-lock evaluation/zarabench/0.1/golden.lock \
  --out reviewed.jsonl                 # --approve-clean stamps clean rows 'approved'; a human still signs off
```

It sorts every row into **blocked** (a secret, a golden-lock family, broken provenance — never train these),
**flagged** (PII, a near-duplicate of another kept row or an existing train row, a degenerate/refusal target —
a human looks), or **clean**. It never trains or approves on its own; it exits non-zero if anything is blocked.
`.github/workflows/review.yml` runs the same gate one-click against the R2 files (spends nothing) and writes the
annotated copy back to `s3://<bucket>/reviewed/`.

### Teacher choice (C3)
- **Bulk → `claude-sonnet-5`.** Strong at tool-calling / structured output (well above the 8B student) and much
  cheaper per seed than Opus. Every completion is `expect`-gated, so quality is filtered regardless of teacher.
- **Reserve `claude-opus-5`** for a small premium slice if Sonnet's keep-rate on the hardest families is low.
- **Never** the model under test, and (per the eval config's `judge_must_differ_from_generator`) never the same
  model that later judges it.
- **Pilot before scale:** run a few hundred seeds with `--limit`, measure keep-rate and cost per kept example,
  then decide how far to scale the remaining ~8.5k. Do not fire all 8,782 blind.

## Guardrails (always on)
- **Golden lock** on both build and synthesis keeps sealed ZaraBench families out of training — no eval leakage.
- **Eval-gated synthesis:** a completion that does not satisfy its seed's `expect` is discarded (validated with
  the mock provider: 0/5 kept, since mock never calls the real tools — the gate works).
- **Review lane:** synthesised rows are `review_status: pending` until a human signs off — run `dataset review`
  (see above) to sort them into blocked / flagged / clean first; it applies the build's secret + PII scan to
  rows that have not yet been through a build.
- **PII scrub + secret scan** run on every build; any secret finding blocks the artefact.
