Configuration lives here as YAML, one directory per concern:

- `models/` — base-model candidates and their serving/quantization settings (Phase 1)
- `training/` — SFT / LoRA / QLoRA run definitions; every hyperparameter is configurable, nothing is hard-coded (Phase 4)
- `inference/` — vLLM launch settings, guided-JSON options, health probes (Phase 5)
- `evaluation/` — benchmark suites and ZaraScore weights (Phase 3)
