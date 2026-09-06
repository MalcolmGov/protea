# Protea

Moove Digital's proprietary model platform: dataset construction, fine-tuning, evaluation, serving and model routing for agentic enterprise models. The first consumer is the Zara agent platform (`MalcolmGov/aria`); the platform is project-agnostic by design.

The programme was specified under the working name "ZaraLM". Phase 0 discovery is complete and documented under `docs/`; see `docs/adr/ADR-001-repository-split.md` for why this is its own repository.

## Status

| Area | Status |
|---|---|
| Phase 0 discovery documents | implemented — `docs/` |
| `protea doctor`, `protea version`, `protea roadmap` | implemented |
| Schemas, providers, registries, CLI for datasets | planned — Phase 1/2 |
| Data pipeline | planned — Phase 2 |
| Evaluation framework + ZaraBench suite | planned — Phase 3 |
| Training (SFT / LoRA / QLoRA, remote GPU) | planned — Phase 4 |
| Inference (vLLM, OpenAI-compatible) | planned — Phase 5 |
| Router, fallback, confidence | planned — Phase 7 |

Nothing in this repository starts paid infrastructure, downloads large models or trains anything. Those steps are explicit, confirmed actions when they arrive.

## Layout

```
protea/            Python package (CLI, and — as phases land — schemas, providers, data_pipeline, evaluation, training, inference, router, registry, observability, security)
configs/           YAML: models/, training/, inference/, evaluation/
docs/              Phase 0 documents, ADRs, and the planned reference docs
tests/             pytest; everything here runs without a GPU
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
protea doctor
pytest
```

GPU-only extras (`[train]`, `[serve]`) are installed on the machine that has the GPU, never on the Zara VPS.

## Sources and provenance

Training data is extracted from pinned commits of `MalcolmGov/aria` (agent packages, registries) and `MalcolmGov/miai-agents` (authored packages) through an allowlist described in `docs/data-risk-assessment.md`. Production conversations and tenant data never enter this repository.
