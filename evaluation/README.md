# evaluation/

- `zarabench/<version>/tasks.jsonl` — the sealed task set (one `EvalTask` per line). Do not edit by hand without re-sealing.
- `zarabench/<version>/golden.lock` — sha256, counts and held-out families; verified in CI by `protea evaluate verify`.
- `reports/` — benchmark reports. Real baselines are committed; `mock-*` and `reference-*` runs are ignored.

See `docs/zarabench.md` for the commands and `docs/adr/ADR-007-evaluation-framework.md` for the design.
