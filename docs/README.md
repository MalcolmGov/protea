# Protea documentation

Protea is Moove Digital's proprietary model platform. It began as "ZaraLM" in the original build specification; the Phase 0 documents below were written under that name and ported here with the rename applied. Zara is the first consumer, not the owner: the Agent Compiler, AgentSpec schema and tool/skill/connector registries stay in `MalcolmGov/aria` and depend on this package.

| Document | Purpose |
|---|---|
| `repository-audit.md` | Phase 0 audit of the four source repositories |
| `architecture-proposal.md` | Where Protea integrates and what it reuses |
| `data-risk-assessment.md` | Training-source classification and security findings |
| `implementation-roadmap.md` | Data opportunity, readiness, phases, execution boundaries |
| `strategy-review.md` | Corrections and additions to the original specification |
| `model-selection.md` | Base-model candidates, scoring matrix, ranked recommendation (desk assessment until ZaraBench) |
| `dataset-design.md` | Recipes, provenance fields, splits, redaction policy, outputs |
| `data-governance.md` | Lanes, teacher-model policy, consent plumbing, retention |
| `adr/` | Architecture decision records (001 repository split, 002 provider abstraction, 003 dataset format, 004 registries, 005 configuration, 006 data pipeline) |

Planned (per the specification): `training.md`, `evaluation.md`, `inference.md`, `routing.md`, `security.md`, `deployment.md`, `operations.md`.
