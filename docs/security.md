# Security

What the platform defends against, how it is tested, and what an operator must keep true. Decisions in ADR-009,
ADR-010 and ADR-011.

## Threat model (v0)

| Threat | Control | Test |
|---|---|---|
| Prompt injection in a customer message | system rules stated as non-overridable; validation gate on structured output; router confidence | security suite `injection-user` |
| Injection through tool results (poisoned order notes, web pages) | tool results are data, never instructions; forbidden tools never declared to chat agents in aria | `injection-tool` |
| Unauthorised or over-limit tool use | tool side-effect tiers and financial limits compiled into the AgentSpec (aria compiler v3); `tool_none` probes | `unauthorised-tool` |
| Cross-tenant data access | tenant reference hashed before it reaches any event; drafts, feedback and observability scoped per partner in aria | `cross-tenant`; aria `tests/test_observability.py` |
| Secret exfiltration from the system prompt | planted-secret probes; no prompt text in any store (Phase 9) | `exfiltration` |
| PII read-back from tool results | redaction in observability; probes forbid ID numbers and phone numbers in answers | `pii` |
| Credential leakage in the repository | gitleaks and the forbidden-file check in CI; `.gitleaksignore` for documented placeholders | CI `secrets` job |
| Unauthenticated facade access | bearer token with constant-time compare; engine token separate | `tests/test_serving.py` |
| Training on customer data | data pipeline lanes and scanners (ADR-006); golden guard; customer tables never on a training host | `tests/test_scanners.py`, `train golden-check` |

## The security suite

```bash
protea security verify                       # sealed set intact, no judge dependency, references pass
protea security run --provider reference     # must be 1.000
protea security run --provider protea --confirm --label <run>   # a served model
protea security gate evaluation/reports/security-0.1/protea-<model>-<run>.json
```

Gate defaults: strict score ≥ 0.95 and every family ≥ 0.9 (release config `gates.security_min_score`,
`gates.security_min_family_pass`). Add probes in `protea/evaluation/security.py`, regenerate with `security author`,
re-seal with `evaluate seal --config configs/evaluation/security-0.1.yaml` in a reviewed change.

## Operator invariants

- `PROTEA_FACADE_TOKEN` and `PROTEA_INFERENCE_TOKEN` are distinct, rotated per `docs/operations.md`, never in YAML.
- The facade is the only public surface; the engine port is reachable from the facade network only.
- `ZARA_LOG_PROMPTS` stays unset in production (aria); route and usage events carry ids and outcomes, not text.
- Benchmark-gated routes stay off (`canary_percent: 0`) until a non-partial ZaraBench report and a passing security
  report are committed for the exact served model.
