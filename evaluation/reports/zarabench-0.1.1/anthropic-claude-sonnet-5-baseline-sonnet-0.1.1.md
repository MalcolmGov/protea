# zarabench 0.1.1 — anthropic:claude-sonnet-5

- Run: `baseline-sonnet-0.1.1` at 2026-09-08T05:59:20.062373+00:00
- Tasks: 206/206; judge: anthropic:claude-opus-5; **partial** (judge checks skipped or categories uncovered)
- Task set sha256: `095d5209d265`; config hash: `e53b9bd474d8`
- **ZaraScore: 87.8%** (strict, all checks per task: 66.9%) — all gates passed
- Latency p50/p95: 3975 / 24249 ms; tokens in/out: 709,612 / 114,998; estimated cost: USD 3.85

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| agent_generation | 0.20 | 25 | 89.1% | 36.0% | 0 | 0 |
| structured_output | 0.15 | 32 | 96.9% | 93.8% | 0 | 0 |
| tool_calling | 0.15 | 30 | 87.2% | 80.0% | 0 | 0 |
| connector_selection | 0.10 | 19 | 86.7% | 52.6% | 0 | 0 |
| workflow_generation | 0.10 | 15 | 99.2% | 93.3% | 0 | 0 |
| business_reasoning | 0.10 | 15 | 86.1% | 73.3% | 2 | 0 |
| failure_recovery | 0.05 | 15 | 46.1% | 26.7% | 0 | 0 |
| safety | 0.05 | 20 | 88.8% | 65.0% | 0 | 0 |
| hallucination | 0.05 | 15 | 78.9% | 66.7% | 0 | 0 |
| instruction_following | 0.05 | 20 | 90.0% | 75.0% | 0 | 0 |

| Language | Score |
|---|---|
| af | 94.4% |
| de | 100.0% |
| en | 85.7% |
| en-ZA | 85.7% |
| es | 100.0% |
| fr | 100.0% |
| sw | 100.0% |
| zh | 100.0% |
| zu | 88.1% |

## Failure modes

| Check | Failures |
|---|---|
| tool_called | 24 |
| says_any | 19 |
| field:tier | 12 |
| binding:handoff_to_human | 9 |
| field:category | 7 |
| rubric | 3 |
| json_equals | 2 |
| binding:check_availability | 2 |
| binding:book_appointment | 2 |
| binding:reschedule_or_cancel | 2 |
| binding:request_freeze_or_cancel | 2 |
| declared_tools_only | 2 |
