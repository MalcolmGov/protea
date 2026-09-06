# ADR-005 — YAML configs validated by Pydantic; a content hash pins every run

**Status:** accepted · **Date:** 2026-09-06

## Context
Spec §16 and §19 forbid hard-coded hyperparameters and require reproducible, immutable run configuration.

## Decision
- Four config kinds under `configs/`: `models/` (base-model candidates and serving needs), `training/`, `inference/`, `evaluation/`; each validated by a schema in `protea.config.models`.
- Validation encodes the rules that protect the programme: QLoRA requires 4-bit loading, LoRA methods require a `lora` block, train and validation must differ, golden sets can never be training inputs, evaluation weights must sum to one.
- `config_hash()` returns a sha256 of the canonical JSON form; trainers and evaluators record it alongside dataset hash and git commit.
- Secrets live in environment variables via `ProteaSettings`, never in YAML.

## Consequences
- `protea config validate` runs in CI on every config in the repo.
- Changing a config changes its hash, so an experiment can never silently drift from what was reviewed.
