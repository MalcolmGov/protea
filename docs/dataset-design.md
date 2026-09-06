# Dataset design

Protea datasets are JSONL files of `TrainingExample` records (ADR-003) built by `protea dataset build` from pinned local clones (ADR-006).

## Recipes in `agent-training-0.1`

| Task type | Source | Input | Target | Count driver |
|---|---|---|---|---|
| `agent_generation` | catalogue + authored packages | synthesised business brief (summary, market, channels, languages, compliance, handoff triggers, tool capabilities) plus the target JSON schema | `AgentSpecLite` JSON: identity, objective, tools with schemas, system prompt, guardrails, model policy, handoff | one per package |
| `structured_output` | packages; flagship specs | prose description of the agent; or objective + summary | manifest JSON; AgentSpec v2 JSON | one per package + 15 |
| `connector_selection` | presets + connector catalogue | agent summary, connector catalogue, tools to bind | `{bindings: [{tool, connector}]}` | one per package with a preset |
| `routing` | routing corpus `must_claim` | utterance | `{lane}` with the lane list in the system prompt | one per utterance |
| `tool_calling` | **seeds only** — eval-seeded synthetic (ADR-006 §4) | eval input (+ follow-ups) | teacher completion validated by the eval grammar | one per eval passing rule checks |

Not yet covered: `workflow_generation`, `business_reasoning`, `recovery`, `optimization`, `multilingual`. These need the synthetic pipeline with a decided teacher policy and, for recovery, production traces (roadmap C6).

## Provenance and quality fields

Every example carries `source_repo`, `source_commit`, `source_path`, `source_id`, `family`, `license_status`, `pii_scan`, `secret_scan`, `rule_checks` (automated), `review_status` (human), `redactions`, `split`, and `duplicate_of`.

## Splits

`train / validation / test` by a seeded hash of `task_type:family`; `golden` is promoted from `test` (N per task type) and hash-pinned in the manifest. Golden never trains; CI runs `dataset golden-check`.

## Redaction policy

PII in prompts, guardrails and evals is treated as real even when it is clearly fictional. Default mode `synthetic` replaces phones with `+27 60 555 NNNN`, emails with `personNNNN@example.com`, SA ID numbers and card numbers with placeholders, and street addresses with `NN Example Road`, deterministically so the same value is replaced identically everywhere. Placeholder domains (`example.com`, `zaraai.digital`) are kept.

## Outputs

```
protea_data/<name>/<version>/
  train.jsonl validation.jsonl test.jsonl golden.jsonl
  seeds/tool_calling_seeds.jsonl
  rejected.jsonl        # ids and reasons only
  manifest.json         # sources, commits, file hashes, golden ids, build report
  DATASET_CARD.md
```

Only the manifest, card and registry entry are versioned in git; data files are ignored.
