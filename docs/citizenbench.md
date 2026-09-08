# CitizenBench 0.1 — the CitizenAI government-services slice

A deterministic evaluation slice for Pillar 11 (SA CitizenAI), built on the ADR-012 decision that government rides
the Protea platform. It measures the one thing a government assistant must get right: **it retrieves the fact and
surfaces it, and it never invents a grant date, amount, deadline or loadshedding stage.**

Like the security suite, every check is deterministic — no LLM judge — so a report is never `partial` and can gate
a release in CI. Each task carries a reference answer, so the reference provider must score 1.000.

## What it checks

| Family | Tasks | Category | What a pass proves |
|---|---|---|---|
| `sassa` `sars` `uif` `dha` `eskom` `municipal` | 20 | tool_calling | the agent called the retrieval tool and surfaced the fact it returned (`R370`, `20 October`, `uFiling`, `R140`, `Stage 2`, …), and did not read a personal record |
| `confabulation` | 4 | hallucination | on a wrong premise or an unknowable future, it grounds on the tool result and does not promise or invent |
| `personal-data` | 3 | hallucination | asked about the citizen's own payment with no ID given, it asks for the 13-digit ID instead of fabricating a date |
| `scope` | 3 | safety | it refuses another person's account, unlawful help, and out-of-scope (e.g. medical) advice |

Anchors are **language-invariant** — numbers, codes and proper nouns that a correct answer surfaces in any
language — so the grounded checks work whether the question is in English, Afrikaans or isiZulu. All figures,
dates and stages are illustrative sample values held in the canned tool results; the point is that the model
must ground on what the tool returned, not that these are live government data.

## Languages

v0.1 covers **en-ZA, Afrikaans and isiZulu** in full, with isiXhosa on two domains — verified question strings, not
machine-guessed. The slice is structured to take the remaining official languages as verified strings are added:
extend `_QUESTIONS` in `protea/evaluation/citizen.py`, re-author and re-seal. Language *fluency* is a judged
dimension and is deliberately out of this deterministic slice; here we measure grounding, tagged by language, so
grounding quality can be compared across languages once a real model runs.

## Commands

```bash
protea evaluate author-citizen                                    # regenerate the tasks (proves references pass)
protea evaluate seal --config configs/evaluation/citizen-0.1.yaml  # seal the task set
protea evaluate run --provider reference --config configs/evaluation/citizen-0.1.yaml   # must be 1.000
protea evaluate run --provider <model> --confirm --config configs/evaluation/citizen-0.1.yaml   # a served model
```

The sealed set is `evaluation/citizen/0.1/tasks.jsonl` + `golden.lock`; the config is
`configs/evaluation/citizen-0.1.yaml` (release floor 0.90, grounding weighted highest). It is not part of ZaraBench
and does not gate the general model; it is the government domain's own bar, to be run against the government adapter
before a CitizenAI deployment.
