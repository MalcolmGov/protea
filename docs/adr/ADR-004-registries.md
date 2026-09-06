# ADR-004 — Dataset and model registries are JSON files in git; MLflow mirrors later

**Status:** accepted · **Date:** 2026-09-06

## Context
Spec §20–§21 require versioned datasets, a model registry with a lifecycle, and experiment tracking. Nothing in the estate provides these. A hosted MLflow needs infrastructure the programme does not have yet.

## Decision
- `registry/datasets.json` and `registry/models.json` hold `DatasetEntry` / `ModelEntry` records (`protea.schemas.registry`), versioned with the code so every commit pins what existed.
- Datasets record sha256, splits, sources (repo + commit + paths), generator models, golden flag and a draft → review → approved → deprecated status.
- Models record family (`protea-agent`, `protea-runtime`, `protea-embed`, `protea-guard`, `protea-voice`, `protea-code`), version, base model, training dataset key, evaluation suite, checkpoint URI, artifact hash, git commit, experiment id, metrics, and the lifecycle `experimental → candidate → staging → production → deprecated | rejected`, enforced by `ModelRegistry.transition` (one production model per family).
- MLflow tracking is optional via `MLFLOW_TRACKING_URI`; when present the trainer logs runs there too, but the JSON file stays the source of truth for release decisions.

## Consequences
- Registry edits are code-reviewed like any change.
- Large artefacts never enter git; entries reference object storage by URI and hash.
