# zarabench 0.1.1 — local:checkpoints/protea-agent-0.2/protea-agent-0.2-qwen3-8b-qlora/20260912T140106Z/adapter

- Run: `20260912T232848Z` at 2026-09-12T23:28:48.428569+00:00
- Tasks: 206/206; judge: none; **partial** (judge checks skipped or categories uncovered)
- Task set sha256: `095d5209d265`; config hash: `e53b9bd474d8`
- **ZaraScore: 77.9%** (strict, all checks per task: 50.1%) — all gates passed
- Latency p50/p95: 16273 / 649707 ms; tokens in/out: 343,029 / 103,118; estimated cost: unknown (no price)

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| agent_generation | 0.20 | 25 | 72.0% | 8.0% | 0 | 0 |
| structured_output | 0.15 | 32 | 94.3% | 71.9% | 0 | 0 |
| tool_calling | 0.15 | 30 | 80.0% | 70.0% | 8 | 0 |
| connector_selection | 0.10 | 19 | 65.3% | 31.6% | 0 | 0 |
| workflow_generation | 0.10 | 15 | 96.7% | 80.0% | 0 | 0 |
| business_reasoning | 0.10 | 15 | 90.0% | 80.0% | 15 | 0 |
| failure_recovery | 0.05 | 15 | 16.7% | 0.0% | 0 | 0 |
| safety | 0.05 | 20 | 83.3% | 50.0% | 10 | 0 |
| hallucination | 0.05 | 15 | 73.3% | 66.7% | 0 | 0 |
| instruction_following | 0.05 | 20 | 70.8% | 45.0% | 10 | 0 |

| Language | Score |
|---|---|
| af | 100.0% |
| de | 100.0% |
| en | 73.7% |
| en-ZA | 92.9% |
| es | 100.0% |
| fr | 100.0% |
| sw | 100.0% |
| zh | 100.0% |
| zu | 78.6% |

## Failure modes

| Check | Failures |
|---|---|
| tool_called | 44 |
| says_any | 24 |
| field:category | 23 |
| no_hallucinated_connectors | 12 |
| field:tier | 11 |
| binding:handoff_to_human | 5 |
| json_parsable | 4 |
| binding:list_services | 3 |
| field:tools | 2 |
| binding:submit_meter_reading | 2 |
| binding:get_bill | 2 |
| binding:open_fraud_case | 2 |
