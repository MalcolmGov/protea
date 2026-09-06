# zarabench 0.1.0 — anthropic:claude-sonnet-5

- Run: `baseline-sonnet-v2` at 2026-09-06T13:32:19.050327+00:00
- Tasks: 206/206; judge: anthropic:claude-opus-5; **partial** (judge checks skipped or categories uncovered)
- Task set sha256: `98b4725f1840`; config hash: `3f6ef15e47f0`
- **ZaraScore: 80.9%** (strict, all checks per task: 46.5%) — all gates passed
- Latency p50/p95: 3812 / 26350 ms; tokens in/out: 736,688 / 120,665; estimated cost: USD 4.02

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| agent_generation | 0.20 | 25 | 77.7% | 0.0% | 0 | 0 |
| structured_output | 0.15 | 32 | 89.9% | 53.1% | 0 | 0 |
| tool_calling | 0.15 | 30 | 80.6% | 70.0% | 1 | 0 |
| connector_selection | 0.10 | 19 | 51.6% | 0.0% | 0 | 0 |
| workflow_generation | 0.10 | 15 | 99.2% | 93.3% | 0 | 0 |
| business_reasoning | 0.10 | 15 | 91.7% | 73.3% | 0 | 0 |
| failure_recovery | 0.05 | 15 | 61.1% | 33.3% | 0 | 0 |
| safety | 0.05 | 20 | 87.5% | 60.0% | 0 | 0 |
| hallucination | 0.05 | 15 | 71.1% | 53.3% | 0 | 0 |
| instruction_following | 0.05 | 20 | 91.7% | 80.0% | 0 | 0 |

| Language | Score |
|---|---|
| af | 94.4% |
| de | 100.0% |
| en | 79.4% |
| en-ZA | 85.7% |
| es | 100.0% |
| fr | 100.0% |
| sw | 50.0% |
| zh | 100.0% |
| zu | 88.1% |

## Failure modes

| Check | Failures |
|---|---|
| field:category | 37 |
| tool_called | 20 |
| says_any | 17 |
| says_none | 14 |
| binding:handoff_to_human | 9 |
| field:tier | 8 |
| rubric | 3 |
| json_equals | 2 |
| binding:list_services | 2 |
| binding:check_availability | 2 |
| binding:book_appointment | 2 |
| binding:reschedule_or_cancel | 2 |
