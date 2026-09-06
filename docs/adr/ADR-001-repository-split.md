# ADR-001 — Protea lives in its own repository; Zara-specific logic stays in `aria`

**Status:** accepted · **Date:** 2026-09-06

## Context
The Phase 0 architecture proposal placed the model platform inside `MalcolmGov/aria` as a `zaralm/` package. The owner then confirmed the model will also serve projects outside Zara (for example Gaslite and the CRM). Keeping GPU tooling inside a 4 GB VPS deploy pipeline, and forcing other projects to import the Zara monolith to call a model, are both unacceptable.

## Decision
- `MalcolmGov/protea` owns everything project-agnostic: data pipeline, dataset and model registries, evaluation framework, training, serving container, `ModelProvider` interface and adapters, router and confidence engine, model cards, client SDK.
- `MalcolmGov/aria` keeps the Agent Compiler, AgentSpec v3, tool/skill/connector registries, the requirements engine and Zara facade endpoints, and depends on the `protea` package.
- Datasets reference `aria` and `miai-agents` as pinned sources (repository + commit hash); the catalogue is never a runtime dependency of Protea.
- The evaluation core is generic; **ZaraBench** is the Zara task suite inside it, and other consumers add their own suites.
- The model registry is namespaced by family from day one: `protea-agent`, `protea-runtime`, and future `protea-embed`, `protea-guard`, `protea-voice`.

## Alternatives
1. Package inside `aria` (original proposal): rejected for the reasons above.
2. Everything, including the compiler, in the new repo: rejected because Zara's product schema would be versioned in a repository Zara does not own and would lose its tests, callers and deploy gate.

## Consequences
- Two repositories to version; `protea` publishes a Python package and `aria` pins it.
- Cross-repo schema changes need a compatibility test in `protea` against the pinned `aria` schema export.
- The ZaraBench suite and Zara datasets carry `source_repo`/`source_commit` provenance.
