# Dataset card — agent-training-0.1.0

**Created:** 2026-09-06T07:52:05.652276+00:00  
**Purpose:** supervised fine-tuning data for Protea agent models (agent generation, structured output, connector selection, routing) and validated seeds for eval-checked synthetic tool-calling data.

## Sources (pinned)

| Source | Repository | Commit | Packages |
|---|---|---|---|
| aria | MalcolmGov/aria | `c22c31b44b73` | 504 |
| miai-agents | MalcolmGov/miai-agents | `9f29405dfd27` | 69 |

## Licensing

All sources are Moove Digital-authored artefacts with licence status recorded per example. No synthetic examples are included in this file set; tool-calling seeds carry the eval expectations that validate any future synthetic completions and must record their generator model.

## Handling

- Secret scan: 0 findings (any finding blocks the artefact)
- PII: 613 findings, redaction mode `synthetic`, redactions {'phone': 411, 'address': 136, 'email': 73, 'card': 1}
- Brand/infrastructure scrub replacements: 0
- Knowledge fields are RAG-only and never appear in targets; catalogue evals are review-lane (contaminated seeds: 224)
- Near-duplicate targets removed: 600; splits are assigned per family so market variants never straddle splits

## Statistics

| Split | Examples | Approx tokens |
|---|---|---|
| train | 1214 | 2330737 |
| validation | 89 | 165390 |
| test | 86 | 182310 |
| golden | 52 | 78284 |

Task types: {'routing': 329, 'structured_output': 588, 'agent_generation': 573, 'connector_selection': 551}  
Languages: {'en-ZA': 329, 'en': 1712}  
Families: 232 · Domains: 14  
Tool-calling seeds: 8782

## Known biases and limitations

- Business briefs are synthesised from package summaries, so requirement phrasing is narrower than real customer language.
- Five market variants per family share most text; splits are family-level but variants still weight the training mix toward the catalogue's 100 families.
- Tool-calling examples require the eval-seeded synthetic step; this file set contains seeds only.
- Languages other than English are represented through routing utterances (South African English) and package language tags, not by translated targets.
