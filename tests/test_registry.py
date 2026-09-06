import pytest

from protea.registry import DatasetRegistry, ModelRegistry, RegistryError
from protea.registry.store import sha256_file
from protea.schemas.registry import DatasetEntry, ModelEntry, ModelStatus, SourceRef


def _model(version="0.1.0", **kw) -> ModelEntry:
    return ModelEntry(
        family="protea-agent",
        version=version,
        base_model="Qwen/Qwen3-8B",
        training_dataset="agent-training-0.1.0",
        **kw,
    )


def test_dataset_registry_roundtrip_and_verify(tmp_path):
    data = tmp_path / "train.jsonl"
    data.write_text('{"x": 1}\n')
    reg = DatasetRegistry(tmp_path / "datasets.json")
    reg.add(
        DatasetEntry(
            name="agent-training",
            version="0.1.0",
            path="train.jsonl",
            sha256=sha256_file(data),
            splits={"train": 1},
            sources=[SourceRef(repo="MalcolmGov/aria", commit="c22c31b")],
        )
    )
    again = DatasetRegistry(tmp_path / "datasets.json")
    assert again.get("agent-training-0.1.0") is not None
    assert again.verify("agent-training-0.1.0", tmp_path)
    data.write_text("tampered")
    assert not again.verify("agent-training-0.1.0", tmp_path)
    duplicate = DatasetEntry(name="agent-training", version="0.1.0", path="x", sha256="0")
    with pytest.raises(RegistryError, match="already registered"):
        again.add(duplicate)


def test_model_lifecycle_transitions(tmp_path):
    reg = ModelRegistry(tmp_path / "models.json")
    reg.add(_model())
    with pytest.raises(RegistryError, match="cannot move"):
        reg.transition("protea-agent-0.1.0", ModelStatus.PRODUCTION)
    reg.transition("protea-agent-0.1.0", ModelStatus.CANDIDATE, reason="zarabench passed")
    reg.transition("protea-agent-0.1.0", ModelStatus.STAGING)
    reg.transition("protea-agent-0.1.0", ModelStatus.PRODUCTION)
    assert reg.production("protea-agent").version == "0.1.0"
    reg.add(_model("0.2.0", deployment_status=ModelStatus.STAGING))
    with pytest.raises(RegistryError, match="already production"):
        reg.transition("protea-agent-0.2.0", ModelStatus.PRODUCTION)
    reg.transition("protea-agent-0.1.0", ModelStatus.DEPRECATED)
    reg.transition("protea-agent-0.2.0", ModelStatus.PRODUCTION)
    reloaded = ModelRegistry(tmp_path / "models.json")
    assert reloaded.production("protea-agent").version == "0.2.0"
    assert len(reloaded.get("protea-agent-0.1.0").history) == 4
    with pytest.raises(RegistryError, match="cannot move"):
        reloaded.transition("protea-agent-0.2.0", ModelStatus.REJECTED)
