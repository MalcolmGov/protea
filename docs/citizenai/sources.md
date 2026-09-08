# CitizenAI source register (Pillar 11 · build-spec Phase 1)

CitizenAI answers are **retrieval-grounded**: the model phrases, but every fact is retrieved from a named source
and cited with an as-of date — never memorised (ADR-012 decision 2). This register is the correctness contract for
that retrieval layer. Each domain names its authoritative source, its format, how often it must be refreshed, the
human who verifies it, and whether what we serve today is real or **sample** data.

**Status today:** every row is `sample` — the seeded facts in `protea/citizenai/retrieval.py` (`StubRetrieval`)
are illustrative values, the same ones CitizenBench grades grounding against. A domain only moves to `live` once a
real connector is wired **and** a named owner has verified the source. Sample data is never served as live.

## Register

| Domain | Retrieval intent | Authoritative source | Format | Refresh cadence | Owner (verifier) | Status |
|---|---|---|---|---|---|---|
| SASSA grants | `get_grant_schedule` | SASSA payment schedule + grant amounts | published schedule / gazette | monthly (amounts: on each adjustment) | _unassigned_ | sample |
| SARS tax | `get_filing_deadline` | SARS filing-season calendar | annual notice | per filing season | _unassigned_ | sample |
| UIF | `get_uif_claim_steps` | Dept. of Employment & Labour (uFiling) | process guide | on process change | _unassigned_ | sample |
| Home Affairs | `get_id_requirements` | Dept. of Home Affairs (eHomeAffairs) | fees & requirements page | on fee change | _unassigned_ | sample |
| Eskom / municipal | `get_loadshedding_stage` | Eskom + municipal block schedules | live status feed | near-real-time | _unassigned_ | sample |
| Municipal rates | `get_rates_info` | Municipal rates & tariffs | tariff schedule | annual (per budget) | _unassigned_ | sample |

## Correctness policy

1. **Retrieval is the source of truth.** The model never states a fact it did not retrieve; the served answer
   carries the fact's values, its source, and its as-of date (`Fact.citation()`).
2. **Human verification before `live`.** A source feeds real answers only after a named owner has verified it and
   flipped its row from `sample` to `live`. Until then the connector is stubbed by `StubRetrieval`.
3. **Staleness is a failure.** A fact past its refresh cadence must not be served as current — `Fact.is_stale()`
   enforces the window, and CitizenBench gains a staleness family as live sources land (build-spec Phase 1.4).
4. **No fabrication.** The commercial figures in the pillar strategy (per-query SLA, CSI, grant amounts as targets)
   are validated with counterparties, never asserted by this layer.

## How the layer is used

`RetrievalService.retrieve(intent)` returns the `Fact` for a domain intent (or `None`). The serving path phrases an
answer and appends `fact.citation()`; CitizenBench checks — deterministically, no judge — that the answer surfaces
the fact's language-invariant anchors (`R370`, `20 October`, `uFiling`, `R140`, `Stage 2`) and refuses to invent.
Real connectors replace `StubRetrieval` behind the same interface, one verified source at a time.

The tools the agent actually calls are wired in `protea/citizenai/connectors.py`: `build_registry()` maps each
tool to a connector, `dispatch(registry, call)` answers a `ToolCall` from retrieval, and every result carries a
`_status` of `sample` / `blocked` / `live`. `statuses(registry)` is the deployment gate's view — a domain must be
`live` (a verified real source) before it is served; today only the operational `handoff_to_official` is `live`,
the six factual domains are `sample`, and `lookup_my_payment` is `blocked` (needs a verified-records bridge). The
personal tool never returns an invented payment — it asks for a 13-digit ID or hands off.
