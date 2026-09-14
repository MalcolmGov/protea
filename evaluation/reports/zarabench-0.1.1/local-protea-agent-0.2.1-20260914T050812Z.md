# zarabench 0.1.1 — local:checkpoints/protea-agent-0.2.1/protea-agent-0.2.1-qwen3-8b-qlora/20260913T125931Z/adapter

- Run: `20260914T050812Z` at 2026-09-14T05:08:12.156901+00:00
- Tasks: 206/206; judge: none; **partial** (judge checks skipped or categories uncovered)
- Task set sha256: `095d5209d265`; config hash: `e53b9bd474d8`
- **ZaraScore: 75.4%** (strict, all checks per task: 49.8%) — all gates passed
- Latency p50/p95: 28697 / 1011465 ms; tokens in/out: 352,070 / 111,248; estimated cost: unknown (no price)

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| agent_generation | 0.20 | 25 | 56.0% | 16.0% | 0 | 0 |
| structured_output | 0.15 | 32 | 92.6% | 75.0% | 0 | 0 |
| tool_calling | 0.15 | 30 | 79.4% | 66.7% | 8 | 0 |
| connector_selection | 0.10 | 19 | 84.2% | 52.6% | 0 | 0 |
| workflow_generation | 0.10 | 15 | 85.8% | 46.7% | 0 | 0 |
| business_reasoning | 0.10 | 15 | 86.7% | 73.3% | 15 | 0 |
| failure_recovery | 0.05 | 15 | 22.2% | 0.0% | 0 | 0 |
| safety | 0.05 | 20 | 83.3% | 50.0% | 10 | 0 |
| hallucination | 0.05 | 15 | 80.0% | 66.7% | 0 | 0 |
| instruction_following | 0.05 | 20 | 69.2% | 45.0% | 10 | 0 |

| Language | Score |
|---|---|
| af | 100.0% |
| de | 100.0% |
| en | 73.3% |
| en-ZA | 92.9% |
| es | 100.0% |
| fr | 100.0% |
| sw | 100.0% |
| zh | 100.0% |
| zu | 64.3% |

## Failure modes

| Check | Failures |
|---|---|
| tool_called | 41 |
| says_any | 22 |
| field:category | 17 |
| json_parsable | 13 |
| field:tier | 9 |
| no_hallucinated_connectors | 6 |
| no_hallucinated_tools | 5 |
| binding:book_appointment | 3 |
| workflow_acyclic | 3 |
| binding:open_fraud_case | 2 |
| binding:start_claim | 2 |
| binding:get_claim_status | 2 |
