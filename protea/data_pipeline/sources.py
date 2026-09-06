"""Pinned sources and the dataset build configuration (spec §5, §8, §17)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ExtractorKind = Literal[
    "agent_package_json",  # aria data/agents/*.agent.json (zara.agent-package/v1)
    "agent_package_dir",  # miai-agents agents/<id>/manifest.json + sibling files
    "presets_ts",  # generated-presets.ts tool→connector bindings
    "python_registry",  # dict-of-dataclass registries (tools / skills / connectors)
    "routing_corpus",  # tests/routing/corpus.py CORPUS literal
    "flagship_specs",  # embed/flagship_agents.py AgentSpec(...) literals
]


class ExtractorSpec(BaseModel):
    kind: ExtractorKind
    glob: str | None = None  # for file-per-artifact kinds
    path: str | None = None  # for single-file kinds
    variable: str | None = None  # python_registry: the module-level dict name
    entity: str | None = None  # python_registry: tool | skill | connector

    @model_validator(mode="after")
    def _one_of(self) -> ExtractorSpec:
        if bool(self.glob) == bool(self.path):
            raise ValueError(f"extractor {self.kind}: exactly one of glob or path is required")
        if self.kind == "python_registry" and not (self.variable and self.entity):
            raise ValueError("python_registry extractors need variable and entity")
        return self


class SourceSpec(BaseModel):
    name: str
    repo: str  # owner/name, recorded in provenance
    root: str  # local path (relative to the protea root or absolute); env PROTEA_SOURCE_<NAME> overrides
    commit: str | None = None  # pin explicitly (exports without .git); must match git when the root is a repository
    license_status: Literal["approved", "restricted", "unknown"] = "unknown"
    authored: bool = False  # hand-authored source (miai-agents) → evals trusted without contamination review
    extractors: list[ExtractorSpec] = Field(default_factory=list)


class SplitRatios(BaseModel):
    train: float = 0.8
    validation: float = 0.1
    test: float = 0.1

    @model_validator(mode="after")
    def _sum(self) -> SplitRatios:
        if abs(self.train + self.validation + self.test - 1.0) > 1e-6:
            raise ValueError("split ratios must sum to 1.0")
        return self


class GoldenSpec(BaseModel):
    per_task_type: int = 15
    seed: int = 42


class RecipeSpec(BaseModel):
    enabled: bool = True
    max_per_source_item: int | None = None


class ScrubSpec(BaseModel):
    brand_rules_path: str | None = None  # e.g. aria:scripts/marketplace/brand_scrub.json
    infra_patterns: dict[str, str] = Field(default_factory=dict)  # regex → replacement, applied to all text
    pii_mode: Literal["synthetic", "placeholder", "block"] = "synthetic"


class DatasetBuildConfig(BaseModel):
    name: str
    version: str
    output_dir: str
    sources: list[SourceSpec]
    recipes: dict[str, RecipeSpec] = Field(default_factory=dict)
    splits: SplitRatios = Field(default_factory=SplitRatios)
    golden: GoldenSpec = Field(default_factory=GoldenSpec)
    scrub: ScrubSpec = Field(default_factory=ScrubSpec)
    split_seed: int = 42
    dedup_threshold: float = 0.92
    contamination_threshold: float = 0.5
    prompts: dict[str, str] = Field(default_factory=dict)  # per task-type system prompt overrides

    @property
    def key(self) -> str:
        return f"{self.name}-{self.version}"


def resolve_root(spec: SourceSpec, protea_root: Path, env: dict[str, str]) -> Path:
    override = env.get(f"PROTEA_SOURCE_{spec.name.upper().replace('-', '_')}")
    raw = Path(override) if override else Path(spec.root)
    return raw if raw.is_absolute() else (protea_root / raw).resolve()


def git_commit(root: Path) -> str | None:
    """HEAD of the repository whose top level is exactly `root`; None for nested directories or non-repositories."""
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def resolve_commit(spec: SourceSpec, root: Path) -> str | None:
    """Git HEAD of the root, cross-checked against an explicit pin. A mismatch is an error, not a silent override."""
    actual = git_commit(root)
    if spec.commit and actual and actual != spec.commit:
        raise ValueError(f"source {spec.name}: pinned commit {spec.commit} but {root} is at {actual}")
    return actual or spec.commit
