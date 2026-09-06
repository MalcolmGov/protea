# Model selection — v0 base-model candidates

**Status:** desk assessment (Phase 1). Every capability score below is an estimate from public reporting and Hub metadata verified on 2026-09-06; it is replaced by measured ZaraBench baselines in Phase 3. The architecture allows the base model to change without redesign (config `configs/models/*.yaml`, adapters hot-loaded in vLLM).

## Candidates

| Model | Params | License | Gated | Context | Tool use | Multilingual | vLLM | QLoRA on 24 GB |
|---|---|---|---|---|---|---|---|---|
| Qwen/Qwen3-8B | 8.2 B | Apache-2.0 | no | 32k (128k YaRN) | native Hermes-style | 119 languages claimed | yes | yes |
| Qwen/Qwen3-14B | 14.8 B | Apache-2.0 | no | 32k | native | same | yes | yes (tight) |
| Qwen/Qwen3-4B | 4.0 B | Apache-2.0 | no | 32k | native | same | yes | yes |
| openai/gpt-oss-20b | 20.9 B (3.6 B active) | Apache-2.0 | no | 128k | native, strong | English-centric | yes | partial (MXFP4; PEFT support maturing) |
| mistralai/Mistral-Small-3.1-24B-Instruct | 24 B | Apache-2.0 | no | 128k | native | 24 languages | yes | no (48 GB+) |
| meta-llama/Llama-3.1-8B-Instruct | 8.0 B | Llama 3.1 community | yes | 128k | native | 8 languages | yes | yes |
| google/gemma-3-12b-it | 12.2 B | Gemma terms | yes | 128k | prompt-based | 140 languages claimed | yes | yes |
| google/gemma-3-4b-it | 4.3 B | Gemma terms | yes | 128k | prompt-based | same | yes | yes |
| microsoft/phi-4 | 14.7 B | MIT | no | 16k | weak | English | yes | yes |
| Qwen/Qwen2.5-7B-Instruct | 7.6 B | Apache-2.0 | no | 128k | native | 29 languages | yes | yes |

## Scoring (0–5 per criterion, desk estimates)

Capability: instruction following (IF), reasoning (R), coding (C), JSON (J), function calling (FC), long context (LC), multilingual (ML), enterprise knowledge (EK).
Operational: licence (Lic), size/VRAM (VR), fine-tune ecosystem (FT), throughput (TP), quantization (Q), vLLM (V), ecosystem (E), cloud availability (CA).
Zara-specific (Z): expected fit for agent generation, workflow generation, schema adherence, connector/tool selection.

| Model | IF | R | C | J | FC | LC | ML | EK | Lic | VR | FT | TP | Q | V | E | CA | Z | Total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3-8B | 4 | 4 | 4 | 5 | 5 | 4 | 4 | 3 | 5 | 5 | 5 | 4 | 5 | 5 | 5 | 5 | 4 | **76** |
| Qwen3-14B | 4 | 5 | 4 | 5 | 5 | 4 | 4 | 4 | 5 | 4 | 5 | 3 | 5 | 5 | 5 | 5 | 5 | **77** |
| gpt-oss-20b | 4 | 5 | 4 | 5 | 5 | 5 | 2 | 4 | 5 | 4 | 3 | 5 | 4 | 5 | 4 | 5 | 4 | 73 |
| Mistral-Small-3.1-24B | 4 | 4 | 4 | 4 | 4 | 5 | 4 | 4 | 5 | 2 | 4 | 3 | 4 | 5 | 4 | 5 | 4 | 69 |
| Llama-3.1-8B | 4 | 3 | 3 | 4 | 4 | 5 | 3 | 3 | 3 | 5 | 5 | 4 | 5 | 5 | 5 | 5 | 3 | 69 |
| Gemma-3-12b | 4 | 4 | 3 | 4 | 3 | 5 | 5 | 3 | 3 | 4 | 4 | 3 | 4 | 5 | 4 | 5 | 3 | 66 |
| Qwen2.5-7B | 4 | 3 | 4 | 4 | 4 | 5 | 4 | 3 | 5 | 5 | 5 | 4 | 5 | 5 | 5 | 5 | 3 | 73 |
| Qwen3-4B | 3 | 3 | 3 | 4 | 4 | 4 | 3 | 2 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 3 | 69 |
| Gemma-3-4b | 3 | 3 | 2 | 3 | 2 | 5 | 4 | 2 | 3 | 5 | 4 | 5 | 4 | 5 | 4 | 5 | 2 | 61 |
| phi-4 | 4 | 4 | 4 | 3 | 2 | 3 | 1 | 3 | 5 | 4 | 4 | 3 | 4 | 5 | 4 | 5 | 2 | 60 |

## Ranked recommendation

1. **Qwen3-8B** — first experiment. Best balance of tool calling, JSON adherence, permissive licence, cheap QLoRA and serving, and a `-Base` checkpoint for later continued pretraining. Configure `tool_call_parser: hermes` in vLLM and train in the same format (ADR-003).
2. **Qwen3-14B** — scale-up if the 8B plateaus on ZaraBench; same family, same format, ~1.5× serving cost.
3. **gpt-oss-20b** — benchmark as a baseline; its tool use is strong and inference is cheap for its capability, but its fine-tuning path is less proven and it is English-centric, which matters for §60.
4. Llama 3.1 8B and Gemma 3 12B — include in the baseline comparison only; licence obligations (naming, redistribution) and weaker structured output make them poor fine-tuning targets here.

## What still has to be measured (Phase 3)

- ZaraBench category scores for the unmodified candidates and at least one frontier model.
- Language slices for `af`, `zu`, `sw`, `xh`, `st`, `tn`: the Hub language claims are not evidence.
- Schema validity with and without guided decoding, to separate the compiler's contribution from the weights'.
- Newer releases since the 2026-06 knowledge cutoff: re-run the Hub search before the first training run.
