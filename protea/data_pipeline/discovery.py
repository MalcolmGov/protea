"""Allowlist-driven discovery: only paths named by an ExtractorSpec are ever read (spec §5)."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from protea.data_pipeline.sources import DatasetBuildConfig, ExtractorSpec, SourceSpec, resolve_commit, resolve_root

# Paths that must never be read even if an allowlist glob would match them.
DENY_SUBSTRINGS = (
    ".env",
    "/.git/",
    "seed_malcolm",
    "/node_modules/",
    "/.venv/",
    "credentials",
    "id_rsa",
    ".pem",
    ".db",
    ".sqlite",
)


class Artifact(BaseModel):
    source: str
    repo: str
    commit: str | None
    kind: str
    relpath: str
    abspath: str
    extractor: ExtractorSpec
    license_status: str
    authored: bool


class ResolvedSource(BaseModel):
    spec: SourceSpec
    root: str
    commit: str | None
    exists: bool


def resolve_sources(
    cfg: DatasetBuildConfig, protea_root: Path, env: dict[str, str] | None = None
) -> list[ResolvedSource]:
    env = dict(os.environ if env is None else env)
    out = []
    for spec in cfg.sources:
        root = resolve_root(spec, protea_root, env)
        out.append(
            ResolvedSource(
                spec=spec,
                root=str(root),
                commit=resolve_commit(spec, root) if root.exists() else None,
                exists=root.exists(),
            )
        )
    return out


def denied(path: str) -> bool:
    p = "/" + path.replace("\\", "/")
    return any(s in p for s in DENY_SUBSTRINGS)


def _paths_for(root: Path, ex: ExtractorSpec) -> list[Path]:
    return sorted(root.glob(ex.glob)) if ex.glob else [root / ex.path]  # type: ignore[arg-type]


def _artifacts_for(rs: ResolvedSource, ex: ExtractorSpec) -> list[Artifact]:
    root = Path(rs.root)
    out = []
    for p in _paths_for(root, ex):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if denied(rel):
            continue
        out.append(
            Artifact(
                source=rs.spec.name,
                repo=rs.spec.repo,
                commit=rs.commit,
                kind=ex.kind,
                relpath=rel,
                abspath=str(p),
                extractor=ex,
                license_status=rs.spec.license_status,
                authored=rs.spec.authored,
            )
        )
    return out


def discover(sources: list[ResolvedSource]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for rs in sources:
        if not rs.exists:
            continue
        for ex in rs.spec.extractors:
            artifacts.extend(_artifacts_for(rs, ex))
    return artifacts
