# zarabench 0.1.1 — local:qwen2.5-0.5b-instruct-base

- Run: `base-sample3` at 2026-09-07T01:52:00.877772+00:00
- Tasks: 30/30; judge: none; **partial** (judge checks skipped or categories uncovered)
- Task set sha256: `095d5209d265`; config hash: `2da81f92bcfa`
- **ZaraScore: 42.6%** (strict, all checks per task: 1.7%) — all gates passed
- Latency p50/p95: 43998 / 326080 ms; tokens in/out: 85,172 / 6,947; estimated cost: unknown (no price)

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| agent_generation | 0.20 | 3 | 52.4% | 0.0% | 0 | 0 |
| structured_output | 0.15 | 3 | 38.1% | 0.0% | 0 | 0 |
| tool_calling | 0.15 | 3 | 38.9% | 0.0% | 2 | 0 |
| connector_selection | 0.10 | 3 | 28.3% | 0.0% | 0 | 0 |
| workflow_generation | 0.10 | 3 | 37.5% | 0.0% | 0 | 0 |
| business_reasoning | 0.10 | 3 | 50.0% | 0.0% | 3 | 0 |
| failure_recovery | 0.05 | 3 | 22.2% | 0.0% | 0 | 0 |
| safety | 0.05 | 3 | 52.8% | 0.0% | 2 | 0 |
| hallucination | 0.05 | 3 | 33.3% | 0.0% | 0 | 0 |
| instruction_following | 0.05 | 3 | 72.2% | 33.3% | 2 | 0 |

| Language | Score |
|---|---|
| af | 75.0% |
| en | 41.4% |
| zu | 25.0% |

## Failure modes

| Check | Failures |
|---|---|
| says_any | 14 |
| field:category | 5 |
| schema_valid | 4 |
| field:tier | 4 |
| tool_called | 4 |
| json_parsable | 3 |
| binding:handoff_to_human | 3 |
| field:languages | 2 |
| no_tool | 2 |
| field:tools | 1 |
| binding:list_services | 1 |
| binding:check_availability | 1 |
