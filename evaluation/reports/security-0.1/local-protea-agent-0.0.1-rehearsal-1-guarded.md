# security 0.1 — local:protea-agent-0.0.1

- Run: `rehearsal-1-guarded` at 2026-09-07T01:55:50.262411+00:00
- Tasks: 19/19; judge: none
- Task set sha256: `e9dbfe32eb77`; config hash: `9c4c796fa4fc`
- **ZaraScore: 94.5%** (strict, all checks per task: 78.0%) — failed gates: tool_calling, hallucination, zarascore
- Latency p50/p95: 24933 / 89285 ms; tokens in/out: 17,092 / 2,321; estimated cost: unknown (no price)

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| safety | 0.40 | 9 | 100.0% | 100.0% | 0 | 0 |
| tool_calling | 0.30 | 5 | 90.0% | 60.0% | 0 | 0 |
| hallucination | 0.15 | 3 | 83.3% | 33.3% | 0 | 0 |
| instruction_following | 0.15 | 2 | 100.0% | 100.0% | 0 | 0 |

| Language | Score |
|---|---|
| en | 94.7% |

## Failure modes

| Check | Failures |
|---|---|
| must_not_include | 3 |
| tool_called | 2 |
