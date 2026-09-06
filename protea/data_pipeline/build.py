"""Dataset build orchestration: discover → classify → scan → extract → normalise → dedup → split → write manifest, card, stats."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from protea.data_pipeline.classify import Classification, Decision, classify_field
from protea.data_pipeline.dedup import mark_duplicates
from protea.data_pipeline.discovery import Artifact, ResolvedSource, discover, resolve_sources
from protea.data_pipeline.extractors.agent_packages import AgentPackage, load_package_dir, load_package_json
from protea.data_pipeline.extractors.presets import load_presets
from protea.data_pipeline.extractors.python_literals import load_literal, registry_entries
from protea.data_pipeline.normalize.packages import (
    ToolCallingSeed,
    agent_generation_example,
    manifest_example,
    tool_calling_seeds,
)
from protea.data_pipeline.normalize.registries import connector_selection_example, flagship_examples, routing_examples
from protea.data_pipeline.scanners.brand import ScrubRules
from protea.data_pipeline.scanners.contamination import detect_contamination
from protea.data_pipeline.scanners.pii import redact, scan_pii
from protea.data_pipeline.scanners.secrets import scan_secrets
from protea.data_pipeline.sources import DatasetBuildConfig
from protea.data_pipeline.splits import assign_splits, select_golden
from protea.registry.store import DatasetRegistry, sha256_file
from protea.schemas.examples import Split, TrainingExample, validate_jsonl
from protea.schemas.registry import DatasetEntry, DatasetStatus, SourceRef


class BuildReport(BaseModel):
    dataset: str
    dry_run: bool
    sources: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: int = 0
    packages: int = 0
    packages_by_source: dict[str, int] = Field(default_factory=dict)
    classification: dict[str, int] = Field(default_factory=dict)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    examples_by_task: dict[str, int] = Field(default_factory=dict)
    examples_by_split: dict[str, int] = Field(default_factory=dict)
    duplicates: int = 0
    golden: int = 0
    seeds: int = 0
    seeds_contaminated: int = 0
    seeds_rejected: int = 0
    redactions: dict[str, int] = Field(default_factory=dict)
    scrubbed: int = 0
    secret_findings: int = 0
    pii_findings: int = 0
    approx_tokens: int = 0
    families: int = 0
    domains: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    output_dir: str | None = None
    files: dict[str, str] = Field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        return [
            f"dataset          {self.dataset}{' (dry run)' if self.dry_run else ''}",
            "sources          " + ", ".join(f"{s['name']}@{(s['commit'] or 'unresolved')[:7]}" for s in self.sources),
            f"artifacts        {self.artifacts}   packages {self.packages} {self.packages_by_source}",
            f"classification   {self.classification}",
            f"examples         {sum(self.examples_by_task.values())} {self.examples_by_task}",
            f"splits           {self.examples_by_split}   golden {self.golden}   duplicates {self.duplicates}",
            f"tool seeds       {self.seeds} (contaminated {self.seeds_contaminated}, rejected {self.seeds_rejected})",
            f"families/domains {self.families}/{self.domains}   languages {self.languages}",
            f"scrub            brand/infra {self.scrubbed}   pii redactions {self.redactions}   secrets {self.secret_findings}",
            f"approx tokens    {self.approx_tokens}",
            f"rejected         {len(self.rejected)}",
        ]


class _PackageScan(BaseModel):
    pkg: AgentPackage
    decisions: dict[str, Decision]
    redactions: dict[str, int]
    contaminated_ids: set[str]
    scrubbed: int


def _scrub_package(
    pkg: AgentPackage, rules: ScrubRules, art: Artifact, cfg: DatasetBuildConfig, report: BuildReport
) -> _PackageScan:
    """Scan every text field, scrub brand/infra, redact PII, classify per field."""
    decisions: dict[str, Decision] = {}
    redactions: Counter[str] = Counter()
    scrubbed = 0
    contamination = detect_contamination(pkg.knowledge, pkg.evals) if not art.authored else detect_contamination("", [])
    contaminated = contamination.ratio >= cfg.contamination_threshold
    fields: dict[str, str] = {
        "manifest": json.dumps({"summary": pkg.summary, "name": pkg.name, "handoff": pkg.handoff}),
        "system_prompt": pkg.system_prompt,
        "tools": json.dumps(pkg.tools),
        "guardrails": pkg.guardrails,
        "knowledge": pkg.knowledge,
        "evals": json.dumps(pkg.evals),
    }
    cleaned: dict[str, str] = {}
    for field, text in fields.items():
        text, n = rules.apply(text)
        scrubbed += n
        secrets = scan_secrets(text)
        pii = scan_pii(text)
        report.secret_findings += len(secrets)
        report.pii_findings += len(pii)
        if pii and cfg.scrub.pii_mode != "block":
            text, counts = redact(text, cfg.scrub.pii_mode)
            redactions.update(counts)
        cleaned[field] = text
        decisions[field] = classify_field(
            field,
            license_status=art.license_status,
            commit_known=art.commit is not None,
            authored=art.authored,
            secrets=secrets,
            pii=pii,
            pii_mode=cfg.scrub.pii_mode,
            contaminated=contaminated and field == "evals",
        )
    pkg = pkg.model_copy(
        update={
            "system_prompt": cleaned["system_prompt"],
            "guardrails": cleaned["guardrails"],
            "knowledge": cleaned["knowledge"],
            "tools": json.loads(cleaned["tools"]),
            "evals": json.loads(cleaned["evals"]),
            "summary": json.loads(cleaned["manifest"])["summary"],
        }
    )
    return _PackageScan(
        pkg=pkg,
        decisions=decisions,
        redactions=dict(redactions),
        contaminated_ids=set(contamination.contaminated_eval_ids),
        scrubbed=scrubbed,
    )


def _load_package(art: Artifact) -> AgentPackage:
    p = Path(art.abspath)
    return load_package_json(p, art.relpath) if art.kind == "agent_package_json" else load_package_dir(p, art.relpath)


class _State:
    def __init__(self) -> None:
        self.examples: list[TrainingExample] = []
        self.seeds: list[ToolCallingSeed] = []
        self.classification: Counter[str] = Counter()
        self.redactions: Counter[str] = Counter()
        self.presets: dict[str, list[dict[str, str]]] = {}
        self.connectors: list[dict[str, Any]] = []
        self.packages: list[tuple[Artifact, _PackageScan]] = []


def _enabled(cfg: DatasetBuildConfig, recipe: str) -> bool:
    return recipe not in cfg.recipes or cfg.recipes[recipe].enabled


def _scrub_rules(cfg: DatasetBuildConfig, sources: list[ResolvedSource]) -> ScrubRules:
    brand_path = None
    if cfg.scrub.brand_rules_path and ":" in cfg.scrub.brand_rules_path:
        src_name, rel = cfg.scrub.brand_rules_path.split(":", 1)
        src = next((s for s in sources if s.spec.name == src_name), None)
        brand_path = Path(src.root) / rel if src else None
    return ScrubRules.load(brand_path, cfg.scrub.infra_patterns)


def _collect_registry_artifacts(
    artifacts: list[Artifact], cfg: DatasetBuildConfig, state: _State, report: BuildReport
) -> None:
    """Presets, connector catalogue, routing corpus and flagship specs. Registries must load before packages."""
    for art in artifacts:
        try:
            if art.kind == "presets_ts":
                state.presets.update(load_presets(Path(art.abspath)))
            elif art.kind == "python_registry" and art.extractor.entity == "connector":
                state.connectors.extend(registry_entries(Path(art.abspath), art.extractor.variable or ""))
            elif art.kind == "routing_corpus" and _enabled(cfg, "routing"):
                corpus = load_literal(Path(art.abspath), art.extractor.variable or "CORPUS")
                state.examples.extend(routing_examples(art, corpus, cfg.version, cfg.prompts))
            elif art.kind == "flagship_specs" and _enabled(cfg, "structured_output"):
                specs = load_literal(Path(art.abspath), art.extractor.variable or "FLAGSHIP_AGENTS")
                state.examples.extend(flagship_examples(art, specs, cfg.version, cfg.prompts))
        except (ValueError, OSError, SyntaxError) as exc:
            report.rejected.append({"artifact": art.relpath, "reason": f"extract failed: {exc}"})


def _collect_packages(
    artifacts: list[Artifact], rules: ScrubRules, cfg: DatasetBuildConfig, state: _State, report: BuildReport
) -> None:
    for art in artifacts:
        if art.kind not in ("agent_package_json", "agent_package_dir"):
            continue
        try:
            pkg = _load_package(art)
        except (ValueError, OSError) as exc:
            report.rejected.append({"artifact": art.relpath, "reason": f"load failed: {exc}"})
            continue
        scan = _scrub_package(pkg, rules, art, cfg, report)
        report.scrubbed += scan.scrubbed
        state.redactions.update(scan.redactions)
        for field, d in scan.decisions.items():
            state.classification[f"{field}:{d.classification.value}"] += 1
        state.packages.append((art, scan))
    report.packages = len(state.packages)
    report.packages_by_source = dict(Counter(a.source for a, _ in state.packages))


def _emit_package(
    art: Artifact, scan: _PackageScan, cfg: DatasetBuildConfig, state: _State, report: BuildReport
) -> None:
    pkg = scan.pkg
    core = ("manifest", "system_prompt", "tools", "guardrails")
    if not all(scan.decisions[f].trainable for f in core):
        blocked = {f: scan.decisions[f].classification.value for f in core if not scan.decisions[f].trainable}
        report.rejected.append({"artifact": art.relpath, "reason": f"core fields not trainable: {blocked}"})
        return
    if _enabled(cfg, "agent_generation"):
        state.examples.append(agent_generation_example(art, pkg, cfg.version, cfg.prompts, scan.redactions))
    if _enabled(cfg, "structured_output"):
        state.examples.append(manifest_example(art, pkg, cfg.version, cfg.prompts, scan.redactions))
    if _enabled(cfg, "connector_selection") and state.presets:
        bindings = state.presets.get(pkg.id) or state.presets.get(pkg.family) or []
        ex = connector_selection_example(art, pkg, bindings, state.connectors, cfg.version, cfg.prompts)
        if ex:
            state.examples.append(ex)
    if _enabled(cfg, "tool_calling") and scan.decisions["evals"].classification in (
        Classification.SAFE_FOR_TRAINING,
        Classification.REQUIRES_REVIEW,
    ):
        limit = cfg.recipes["tool_calling"].max_per_source_item if "tool_calling" in cfg.recipes else None
        new_seeds, rejected = tool_calling_seeds(art, pkg, scan.contaminated_ids, limit)
        state.seeds.extend(new_seeds)
        report.seeds_rejected += len(rejected)
        report.rejected.extend(
            {"artifact": f"{art.relpath}#{eid}", "reason": "; ".join(problems)} for eid, problems in rejected[:5]
        )


def _finalise(cfg: DatasetBuildConfig, state: _State, report: BuildReport) -> list[str]:
    examples = state.examples
    report.duplicates = mark_duplicates(examples, cfg.dedup_threshold)
    assign_splits(examples, cfg.splits, cfg.split_seed)
    golden_ids = select_golden(examples, cfg.golden)
    report.golden = len(golden_ids)
    report.examples_by_split = dict(Counter(e.metadata.split.value for e in examples if e.metadata.split))
    report.examples_by_task = dict(Counter(e.metadata.task_type.value for e in examples))
    report.classification = dict(state.classification)
    report.redactions = dict(state.redactions)
    report.seeds = len(state.seeds)
    report.seeds_contaminated = sum(1 for s in state.seeds if s.contaminated)
    report.approx_tokens = sum(e.approx_tokens() for e in examples)
    report.families = len({e.metadata.family for e in examples if e.metadata.family})
    report.domains = len({e.metadata.domain for e in examples})
    report.languages = dict(Counter(e.metadata.language for e in examples))
    return golden_ids


def _write_outputs(
    cfg: DatasetBuildConfig, protea_root: Path, state: _State, report: BuildReport, golden_ids: list[str]
) -> None:
    out = (protea_root / cfg.output_dir).resolve()
    (out / "seeds").mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {s.value: out / f"{s.value}.jsonl" for s in Split}
    handles = {k: p.open("w", encoding="utf-8") for k, p in files.items()}
    try:
        for ex in state.examples:
            if not ex.metadata.duplicate_of:
                handles[ex.metadata.split.value].write(ex.model_dump_json() + "\n")  # type: ignore[union-attr]
    finally:
        for h in handles.values():
            h.close()
    seeds_path = out / "seeds" / "tool_calling_seeds.jsonl"
    seeds_path.write_text("".join(s.model_dump_json() + "\n" for s in state.seeds), encoding="utf-8")
    (out / "rejected.jsonl").write_text("".join(json.dumps(r) + "\n" for r in report.rejected), encoding="utf-8")

    stats = {name: validate_jsonl(p).model_dump(mode="json") for name, p in files.items()}
    manifest = {
        "name": cfg.name,
        "version": cfg.version,
        "created_at": datetime.now(UTC).isoformat(),
        "sources": report.sources,
        "files": {
            name: {"path": str(p.relative_to(out)), "sha256": sha256_file(p), "examples": stats[name]["valid"]}
            for name, p in files.items()
        },
        "seeds": {
            "path": "seeds/tool_calling_seeds.jsonl",
            "count": len(state.seeds),
            "contaminated": report.seeds_contaminated,
        },
        "golden_ids": golden_ids,
        "report": report.model_dump(mode="json", exclude={"files"}),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    card = dataset_card(cfg, report, manifest, stats)
    (out / "DATASET_CARD.md").write_text(card, encoding="utf-8")
    card_path = out / "DATASET_CARD.md"
    if (protea_root / "docs").is_dir():  # version the card with the code; the data files themselves stay out of git
        card_path = protea_root / "docs" / "datasets" / f"{cfg.key}.md"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(card, encoding="utf-8")
    report.output_dir = str(out)
    report.files = {k: str(v) for k, v in files.items()} | {
        "seeds": str(seeds_path),
        "manifest": str(out / "manifest.json"),
    }

    registry = DatasetRegistry(protea_root / "registry" / "datasets.json")
    if registry.get(cfg.key) is None:
        registry.add(
            DatasetEntry(
                name=cfg.name,
                version=cfg.version,
                task_types=sorted(report.examples_by_task),
                splits={name: stats[name]["valid"] for name in files},
                path=str((out / "train.jsonl").relative_to(protea_root)),
                sha256=sha256_file(out / "train.jsonl"),
                card_path=str(card_path.relative_to(protea_root)),
                sources=[
                    SourceRef(repo=s["repo"], commit=s["commit"] or "unresolved") for s in report.sources if s["exists"]
                ],
                status=DatasetStatus.DRAFT,
            )
        )


def build_dataset(
    cfg: DatasetBuildConfig, protea_root: Path, *, dry_run: bool = False, env: dict[str, str] | None = None
) -> BuildReport:
    report = BuildReport(dataset=cfg.key, dry_run=dry_run)
    sources = resolve_sources(cfg, protea_root, env)
    report.sources = [
        {"name": s.spec.name, "repo": s.spec.repo, "root": s.root, "commit": s.commit, "exists": s.exists}
        for s in sources
    ]
    artifacts = discover(sources)
    report.artifacts = len(artifacts)
    state = _State()
    _collect_registry_artifacts(artifacts, cfg, state, report)
    _collect_packages(artifacts, _scrub_rules(cfg, sources), cfg, state, report)
    for art, scan in state.packages:
        _emit_package(art, scan, cfg, state, report)
    golden_ids = _finalise(cfg, state, report)
    if not dry_run:
        _write_outputs(cfg, protea_root, state, report, golden_ids)
    return report


def dataset_card(cfg: DatasetBuildConfig, report: BuildReport, manifest: dict[str, Any], stats: dict[str, Any]) -> str:
    lines = [
        f"# Dataset card — {cfg.key}",
        "",
        f"**Created:** {manifest['created_at']}  ",
        "**Purpose:** supervised fine-tuning data for Protea agent models (agent generation, structured output, "
        "connector selection, routing) and validated seeds for eval-checked synthetic tool-calling data.",
        "",
        "## Sources (pinned)",
        "",
        "| Source | Repository | Commit | Packages |",
        "|---|---|---|---|",
    ]
    for s in report.sources:
        lines.append(
            f"| {s['name']} | {s['repo']} | `{(s['commit'] or 'unresolved')[:12]}` | {report.packages_by_source.get(s['name'], 0)} |"
        )
    lines += [
        "",
        "## Licensing",
        "",
        "All sources are Moove Digital-authored artefacts with licence status recorded per example. No synthetic "
        "examples are included in this file set; tool-calling seeds carry the eval expectations that validate any "
        "future synthetic completions and must record their generator model.",
        "",
        "## Handling",
        "",
        f"- Secret scan: {report.secret_findings} findings (any finding blocks the artefact)",
        f"- PII: {report.pii_findings} findings, redaction mode `{cfg.scrub.pii_mode}`, redactions {report.redactions}",
        f"- Brand/infrastructure scrub replacements: {report.scrubbed}",
        "- Knowledge fields are RAG-only and never appear in targets; catalogue evals are review-lane "
        f"(contaminated seeds: {report.seeds_contaminated})",
        f"- Near-duplicate targets removed: {report.duplicates}; splits are assigned per family so market variants "
        "never straddle splits",
        "",
        "## Statistics",
        "",
        "| Split | Examples | Approx tokens |",
        "|---|---|---|",
    ]
    for name, st in stats.items():
        lines.append(f"| {name} | {st['valid']} | {st['approx_tokens']} |")
    lines += [
        "",
        f"Task types: {report.examples_by_task}  ",
        f"Languages: {report.languages}  ",
        f"Families: {report.families} · Domains: {report.domains}  ",
        f"Tool-calling seeds: {report.seeds}",
        "",
        "## Known biases and limitations",
        "",
        "- Business briefs are synthesised from package summaries, so requirement phrasing is narrower than real "
        "customer language.",
        "- Five market variants per family share most text; splits are family-level but variants still weight the "
        "training mix toward the catalogue's 100 families.",
        "- Tool-calling examples require the eval-seeded synthetic step; this file set contains seeds only.",
        "- Languages other than English are represented through routing utterances (South African English) and "
        "package language tags, not by translated targets.",
        "",
    ]
    return "\n".join(lines)
