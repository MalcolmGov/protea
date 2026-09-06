from pathlib import Path

import pytest
import yaml

from protea.config import config_hash, load_config
from protea.config.models import EvaluationConfig, TrainingConfig

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("path", sorted((REPO / "configs").rglob("*.yaml")))
def test_repo_configs_are_valid(path):
    kind = path.parent.name.rstrip("s")
    cfg = load_config(path, kind)  # type: ignore[arg-type]
    assert len(config_hash(cfg)) == 64


def test_hash_is_stable_and_content_sensitive():
    a = load_config(REPO / "configs/training/dev-tiny.yaml", "training")
    b = load_config(REPO / "configs/training/dev-tiny.yaml", "training")
    assert config_hash(a) == config_hash(b)
    c = a.model_copy(deep=True)
    c.training.seed = 7
    assert config_hash(c) != config_hash(a)


def _training(**over) -> dict:
    base = yaml.safe_load((REPO / "configs/training/protea-agent-8b-qlora.yaml").read_text())
    for k, v in over.items():
        sect, key = k.split(".")
        base[sect][key] = v
    return base


def test_qlora_requires_4bit():
    with pytest.raises(ValueError, match="load_in_4bit"):
        TrainingConfig.model_validate(_training(**{"model.load_in_4bit": False}))


def test_golden_never_trains():
    with pytest.raises(ValueError, match="golden"):
        TrainingConfig.model_validate(_training(**{"dataset.validation": "protea_data/x/golden.jsonl"}))


def test_train_and_validation_must_differ():
    data = _training()
    data["dataset"]["validation"] = data["dataset"]["train"]
    with pytest.raises(ValueError, match="different"):
        TrainingConfig.model_validate(data)


def test_evaluation_weights_and_gates():
    cfg = load_config(REPO / "configs/evaluation/zarabench-0.1.yaml", "evaluation")
    assert isinstance(cfg, EvaluationConfig)
    scores = {c.name: 0.5 for c in cfg.categories}
    assert abs(cfg.zarascore(scores) - 0.5) < 1e-9
    strict = cfg.model_copy(deep=True)
    strict.categories[0].min_score = 0.9
    assert strict.failed_gates(scores) == [strict.categories[0].name]
    with pytest.raises(ValueError, match="sum to 1.0"):
        EvaluationConfig(suite="x", version="0", categories=[{"name": "a", "weight": 0.5}])  # type: ignore[list-item]
