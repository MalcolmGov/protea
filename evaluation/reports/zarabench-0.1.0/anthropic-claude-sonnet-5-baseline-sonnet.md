# zarabench 0.1.0 — anthropic:claude-sonnet-5

- Run: `baseline-sonnet` at 2026-09-06T13:07:04.342203+00:00
- Tasks: 206/206; judge: anthropic:claude-opus-5; **partial** (judge checks skipped or categories uncovered)
- Task set sha256: `98b4725f1840`; config hash: `072adc4a7e93`
- **ZaraScore: 58.0%** (strict, all checks per task: 30.1%) — all gates passed
- Latency p50/p95: 3895 / 9020 ms; tokens in/out: 739,107 / 74,441; estimated cost: USD 3.33

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| agent_generation | 0.20 | 25 | 0.0% | 0.0% | 0 | 0 |
| structured_output | 0.15 | 32 | 85.3% | 50.0% | 0 | 0 |
| tool_calling | 0.15 | 30 | 76.7% | 53.3% | 8 | 0 |
| connector_selection | 0.10 | 19 | 50.6% | 0.0% | 0 | 0 |
| workflow_generation | 0.10 | 15 | 85.8% | 80.0% | 0 | 0 |
| business_reasoning | 0.10 | 15 | 61.7% | 0.0% | 15 | 0 |
| failure_recovery | 0.05 | 15 | 65.0% | 33.3% | 0 | 0 |
| safety | 0.05 | 20 | 70.8% | 10.0% | 10 | 0 |
| hallucination | 0.05 | 15 | 71.1% | 53.3% | 0 | 0 |
| instruction_following | 0.05 | 20 | 70.0% | 35.0% | 10 | 0 |

| Language | Score |
|---|---|
| af | 61.1% |
| de | 50.0% |
| en | 63.1% |
| en-ZA | 85.7% |
| es | 100.0% |
| fr | 50.0% |
| sw | 50.0% |
| zh | 50.0% |
| zu | 50.0% |

## Failure modes

| Check | Failures |
|---|---|
| judge_error | 43 |
| json_parsable | 29 |
| tool_called | 22 |
| says_any | 15 |
| field:category | 12 |
| says_none | 12 |
| binding:handoff_to_human | 10 |
| binding:list_services | 3 |
| json_equals | 2 |
| binding:get_filing_deadlines | 2 |
| binding:log_tax_enquiry | 2 |
| binding:submit_meter_reading | 2 |
