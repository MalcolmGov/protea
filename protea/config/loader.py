from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from protea.config.models import CONFIG_TYPES
from protea.data_pipeline.sources import DatasetBuildConfig

ConfigKind = Literal["model", "training", "inference", "evaluation", "dataset", "remote", "pricing", "serve", "routing"]
CONFIG_TYPES = {**CONFIG_TYPES, "dataset": DatasetBuildConfig}


def load_config(path: str | Path, kind: ConfigKind) -> BaseModel:
    """Load and validate a YAML config. Unknown top-level keys are rejected by the schema."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: top level must be a mapping")
    return CONFIG_TYPES[kind].model_validate(raw, strict=False)


def config_hash(cfg: BaseModel) -> str:
    """Stable content hash so a training run can pin the exact config it used."""
    payload = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
