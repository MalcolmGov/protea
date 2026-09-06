# Protea

Moove Digital's proprietary model platform: dataset construction, fine-tuning, evaluation, serving and model routing for agentic enterprise models. The first consumer is the Zara agent platform (`MalcolmGov/aria`); the platform is project-agnostic by design.

The programme was specified under the working name "ZaraLM". Phase 0 discovery is complete and documented under `docs/`; see `docs/adr/ADR-001-repository-split.md` for why this is its own repository.

## Status

| Area | Status |
|---|---|
| Phase 0 discovery documents | implemented — `docs/` |
| CLI: `doctor`, `version`, `roadmap`, `config validate|show`, `providers list|health`, `dataset validate|stats|build|golden-check|synthesize`, `registry datasets|models|promote`, `evaluate tasks|author|seal|verify|run|compare` | implemented |
| Generation contract + `ModelProvider` with Anthropic, OpenAI, Google, Azure OpenAI, Ollama, Protea (vLLM) and mock adapters | implemented — Phase 1 (ADR-002) |
| Training-example schema with provenance envelope and JSONL validation | implemented — Phase 1 (ADR-003) |
| Dataset and model registries with release lifecycle | implemented — Phase 1 (ADR-004) |
| YAML config schemas for models, training, inference, evaluation + content hashes | implemented — Phase 1 (ADR-005) |
| Model selection (desk assessment) | implemented — `docs/model-selection.md`; measured in Phase 3 |
| Data pipeline: allowlist discovery, classification, secret/PII/brand/contamination scanners, extractors, normalisers, family splits, golden guard, manifests and cards (`dataset build|golden-check`) | implemented — Phase 2 (ADR-006) |
| Eval-seeded synthetic tool-calling (`dataset synthesize`, gated) | implemented — Phase 2; teacher policy pending |
| Evaluation framework: task contract, evaluators, LLM judge, runner, reports, release gate + kill criterion; ZaraBench 0.1 sealed (206 tasks, 10 categories) (`evaluate author|seal|verify|run|compare`) | implemented — Phase 3 (ADR-007); base-model and frontier baselines await confirmation |
| Training (SFT / LoRA / QLoRA, remote GPU) | planned — Phase 4 |
| Inference (vLLM, OpenAI-compatible) | planned — Phase 5 |
| Router, fallback, confidence | planned — Phase 7 |

Nothing in this repository starts paid infrastructure, downloads large models or trains anything. Those steps are explicit, confirmed actions when they arrive.

## Layout

```
protea/
  cli.py, doctor.py      commands
  schemas/               generation contract, training-example format, registry entries
  providers/             ModelProvider + adapters (anthropic, openai_compatible, google, mock) and build_provider()
  config/                environment settings, YAML schemas, loader + content hash
  registry/              file-backed dataset and model registries
  data_pipeline/         sources, discovery, classify, scanners/, extractors/, normalize/, dedup, splits, build, synthetic
  evaluation/            tasks, driver, evaluators, judge, runner, report, golden, reference, authoring (ZaraBench)
configs/                 models/, training/, inference/, evaluation/, datasets/ (validated in CI)
evaluation/              sealed ZaraBench task sets + golden locks, committed baseline reports
registry/                datasets.json, models.json (source of truth for releases)
docs/                    Phase 0 documents, ADRs, model selection
tests/                   pytest; everything here runs without a GPU
```

## Using a provider from another project

```python
from protea.providers import build_provider
from protea.schemas.generation import GenerationRequest, Message

provider = build_provider("anthropic")  # or "protea", "openai", "google", "azure_openai", "ollama"
resp = await provider.generate(GenerationRequest(messages=[Message(role="user", content="…")]))
spec = await provider.generate_structured(request, MyPydanticModel)  # validated or StructuredOutputError
```

Every call emits a `UsageEvent` (tokens, latency, model, task type; never prompt text) to a `UsageSink` you can replace.

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
