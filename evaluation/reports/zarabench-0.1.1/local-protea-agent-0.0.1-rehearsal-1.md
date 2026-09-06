# zarabench 0.1.1 — local:protea-agent-0.0.1

- Run: `rehearsal-1` at 2026-09-06T21:00:29.631768+00:00
- Tasks: 206/206; judge: none; **partial** (judge checks skipped or categories uncovered)
- Task set sha256: `095d5209d265`; config hash: `2da81f92bcfa`
- **ZaraScore: 51.1%** (strict, all checks per task: 26.0%) — all gates passed
- Latency p50/p95: 263388 / 1057199 ms; tokens in/out: 483,254 / 75,370; estimated cost: unknown (no price)

| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |
|---|---|---|---|---|---|---|
| agent_generation | 0.20 | 25 | 12.0% | 0.0% | 0 | 0 |
| structured_output | 0.15 | 32 | 83.3% | 62.5% | 0 | 0 |
| tool_calling | 0.15 | 30 | 62.8% | 46.7% | 8 | 0 |
| connector_selection | 0.10 | 19 | 42.0% | 0.0% | 0 | 0 |
| workflow_generation | 0.10 | 15 | 50.8% | 0.0% | 0 | 0 |
| business_reasoning | 0.10 | 15 | 81.1% | 66.7% | 15 | 0 |
| failure_recovery | 0.05 | 15 | 52.2% | 33.3% | 0 | 0 |
| safety | 0.05 | 20 | 59.2% | 5.0% | 10 | 0 |
| hallucination | 0.05 | 15 | 33.3% | 6.7% | 0 | 0 |
| instruction_following | 0.05 | 20 | 42.5% | 15.0% | 10 | 0 |

| Language | Score |
|---|---|
| af | 58.3% |
| de | 100.0% |
| en | 53.0% |
| en-ZA | 64.3% |
| es | 75.0% |
| fr | 50.0% |
| sw | 0.0% |
| zh | 100.0% |
| zu | 28.6% |

## Failure modes

| Check | Failures |
|---|---|
| says_any | 56 |
| json_parsable | 27 |
| tool_called | 21 |
| no_hallucinated_connectors | 19 |
| binding:handoff_to_human | 18 |
| workflow_edges_resolve | 13 |
| workflow_acyclic | 13 |
| workflow_required_types | 13 |
| workflow_node_types | 10 |
| no_tool | 9 |
| workflow_uses_tools | 8 |
| declared_tools_only | 7 |
