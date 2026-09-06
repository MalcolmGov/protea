# Data governance

## Principles (spec §5, §35, §42–§43, §77)

1. Repositories are not training data. Extraction is allowlist-only and pinned to commits.
2. Weights learn behaviour: agent design, tool use, structured output, routing. Tenant knowledge, conversations, leads, invoices and uploaded documents stay in retrieval and never enter `protea_data/`.
3. Every example is traceable to a source path and commit, or to a named generator model.
4. Automated checks (`rule_checks`) are not human review (`review_status`). Datasets register as `draft` and become `approved` only after a sampled human review is recorded in the card.
5. Secrets block; PII is redacted or blocks; brand and infrastructure identifiers are scrubbed.

## Classification lanes

| Lane | Enters training automatically | Example |
|---|---|---|
| `SAFE_FOR_TRAINING` | yes | package manifest, system prompt, tools, guardrails |
| `REQUIRES_REVIEW` | as seeds / after review | catalogue evals |
| `RAG_ONLY` | never as target; context only | package knowledge |
| `DO_NOT_TRAIN`, `SECRET`, `CUSTOMER_DATA`, `PII`, `LICENSE_RESTRICTED`, `UNKNOWN` | never | `.env`, databases, seeded personal facts, partner documents, unresolved commits |

## Teacher-model policy (open decision, strategy-review C3)

Synthetic completions record `generator_model` and keep `license_status: unknown` until the policy is set. `protea dataset synthesize` refuses paid providers without `--confirm` and prints the licensing caveat. The recommended default is an open-weight teacher.

## Consent plumbing for future production signals

Production turns, fallback events and ratings may become candidates only through: tenant `training_consent`, redaction with the same scanners, human review, and a manifest entry. No automatic path exists.

## Retention and deletion

Datasets are immutable once registered; a correction is a new version. Removing a source example means rebuilding a new version without it and deprecating the old entry; adapters trained on a deprecated dataset are marked in the model registry.
