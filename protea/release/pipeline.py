"""Release pipeline (roadmap Phase 10): train → validate → ZaraBench → security → compare production → candidate →
staging → canary → production, with rollback.

Every stage is a check with evidence on disk (registry entries, dataset hashes, committed reports, the routing
policy). Promotion moves the model registry one lifecycle step and only when every earlier stage passes; the canary
is the routing policy's tenant share for the Protea route (ADR-010), raised step by step and reset to zero on
rollback. Nothing here trains, serves or spends: it reads evidence and edits two files (registry, routing policy)."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from protea.config.loader import load_config
from protea.evaluation.report import BenchmarkReport, load_report, release_decision
from protea.registry import DatasetRegistry, ModelRegistry, RegistryError
from protea.registry.store import sha256_file
from protea.release.config import ReleaseConfig, RollbackTriggers
from protea.schemas.registry import ModelEntry, ModelStatus

STAGES = (
    "train",
    "validate",
    "zarabench",
    "security",
    "compare_production",
    "candidate",
    "staging",
    "canary",
    "production",
)
_LIFECYCLE = ("experimental", "candidate", "staging", "production")
_NOT_EVIDENCE = ("mock", "reference")


class StageCheck(BaseModel):
    stage: str
    ok: bool
    detail: str
    evidence: str | None = None


class ReleaseReport(BaseModel):
    model: str
    status: str
    canary_percent: float
    checks: list[StageCheck] = Field(default_factory=list)
    next_step: str | None = None  # the promotion the evidence supports now
    blockers: list[str] = Field(default_factory=list)

    def ok(self, stage: str) -> bool:
        return any(c.stage == stage and c.ok for c in self.checks)


class Evidence(BaseModel):
    """Explicit report paths override the reports_dir lookup."""

    zarabench: Path | None = None
    security: Path | None = None
    base: Path | None = None
    frontier: Path | None = None


def _age_days(report: BenchmarkReport) -> float:
    try:
        created = datetime.fromisoformat(report.created_at)
    except ValueError:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created).total_seconds() / 86400


def _reports(dir_: Path) -> list[tuple[Path, BenchmarkReport]]:
    out = []
    for path in sorted(dir_.glob("*.json")) if dir_.exists() else []:
        try:
            out.append((path, load_report(path)))
        except (OSError, ValueError):
            continue
    return out


def _latest(reports: list[tuple[Path, BenchmarkReport]], pred) -> tuple[Path, BenchmarkReport] | None:
    matches = [(p, r) for p, r in reports if r.provider not in _NOT_EVIDENCE and pred(r)]
    return max(matches, key=lambda pr: pr[1].created_at) if matches else None


def _matches_entry(report: BenchmarkReport, entry: ModelEntry) -> bool:
    keys = {entry.key, entry.family, f"{entry.family}:{entry.version}", f"{entry.family}@{entry.version}"}
    return report.model in keys or (report.provider == "protea" and entry.version in report.model)


class ReleasePipeline:
    def __init__(self, cfg: ReleaseConfig, root: Path = Path(".")):
        self.cfg = cfg
        self.root = root
        self.datasets = DatasetRegistry(root / cfg.registry_dir / "datasets.json")
        self.models = ModelRegistry(root / cfg.registry_dir / "models.json")
        self.eval_cfg = load_config(root / cfg.evaluation_config, "evaluation")
        self.sec_cfg = load_config(root / cfg.security_config, "evaluation")

    # ---- evidence -----------------------------------------------------------------------------------------------
    def _suite_dir(self, cfg) -> Path:
        return self.root / self.cfg.reports_dir / f"{cfg.suite}-{cfg.version}"

    def _report(self, override: Path | None, suite_cfg, pred) -> tuple[Path, BenchmarkReport] | None:
        if override is not None:
            return override, load_report(override)
        return _latest(_reports(self._suite_dir(suite_cfg)), pred)

    def canary_percent(self) -> float:
        policy = load_config(self.root / self.cfg.canary.routing_policy, "routing")
        return float(policy.canary_percent)

    # ---- checks -------------------------------------------------------------------------------------------------
    def _check_train(self, entry: ModelEntry) -> StageCheck:
        missing = [f for f in ("checkpoint_uri", "artifact_sha256", "git_commit") if not getattr(entry, f)]
        if missing:
            return StageCheck(stage="train", ok=False, detail=f"registry entry lacks {', '.join(missing)}")
        return StageCheck(
            stage="train",
            ok=True,
            detail=f"checkpoint {entry.checkpoint_uri} sha {entry.artifact_sha256[:12]}",
            evidence=str(self.models.path),
        )

    def _check_validate(self, entry: ModelEntry) -> StageCheck:
        problems = []
        gates = self.cfg.gates
        if gates.require_model_card and not (entry.model_card_path and (self.root / entry.model_card_path).exists()):
            problems.append("model card missing")
        ds = self.datasets.get(entry.training_dataset)
        if ds is None:
            problems.append(f"training dataset {entry.training_dataset} not registered")
        elif gates.require_dataset_verified:
            path = self.root / ds.path
            if not path.exists() or sha256_file(path) != ds.sha256:
                problems.append(f"training dataset {ds.key} hash mismatch or missing")
        if problems:
            return StageCheck(stage="validate", ok=False, detail="; ".join(problems))
        return StageCheck(
            stage="validate",
            ok=True,
            detail=f"card {entry.model_card_path}; dataset {entry.training_dataset} verified",
            evidence=entry.model_card_path,
        )

    def _check_zarabench(self, entry: ModelEntry, ev: Evidence) -> tuple[StageCheck, BenchmarkReport | None]:
        found = self._report(ev.zarabench, self.eval_cfg, lambda r: _matches_entry(r, entry))
        if found is None:
            return StageCheck(
                stage="zarabench",
                ok=False,
                detail=f"no ZaraBench report for {entry.key} under {self._suite_dir(self.eval_cfg)}",
            ), None
        path, report = found
        problems = []
        if self.cfg.gates.zarabench_not_partial and report.partial:
            problems.append("report is partial (judge checks skipped)")
        if _age_days(report) > self.cfg.gates.max_report_age_days:
            problems.append(f"report older than {self.cfg.gates.max_report_age_days} days")
        if self.cfg.gates.zarabench_release_gate:
            base = self._report(ev.base, self.eval_cfg, lambda r: r.model == entry.base_model)
            frontier = self._report(
                ev.frontier, self.eval_cfg, lambda r: r.provider in ("anthropic", "openai", "google", "azure_openai")
            )
            decision = release_decision(
                self.eval_cfg, report, base[1] if base else None, frontier[1] if frontier else None
            )
            problems.extend(decision.reasons)
            if decision.kill_recommended:
                problems.append(decision.kill_reason)
        detail = f"ZaraScore {report.zarascore:.3f} (strict {report.zarascore_strict:.3f}) run {report.run_id}"
        if problems:
            return StageCheck(
                stage="zarabench", ok=False, detail=detail + " — " + "; ".join(problems), evidence=str(path)
            ), report
        return StageCheck(stage="zarabench", ok=True, detail=detail, evidence=str(path)), report

    def _check_security(self, entry: ModelEntry, ev: Evidence) -> StageCheck:
        found = self._report(ev.security, self.sec_cfg, lambda r: _matches_entry(r, entry))
        if found is None:
            return StageCheck(
                stage="security",
                ok=False,
                detail=f"no security report for {entry.key} under {self._suite_dir(self.sec_cfg)}",
            )
        path, report = found
        gates = self.cfg.gates
        problems = []
        if report.partial:
            problems.append("security report is partial")
        if report.zarascore_strict < gates.security_min_score:
            problems.append(f"strict score {report.zarascore_strict:.3f} < {gates.security_min_score:.2f}")
        weak = _weak_families(report, gates.security_min_family_pass)
        if weak:
            problems.append("families below the pass floor: " + ", ".join(weak))
        detail = f"security strict {report.zarascore_strict:.3f} run {report.run_id}"
        return StageCheck(
            stage="security",
            ok=not problems,
            detail=detail + (" — " + "; ".join(problems) if problems else ""),
            evidence=str(path),
        )

    def _check_compare(self, entry: ModelEntry, candidate: BenchmarkReport | None) -> StageCheck:
        if not self.cfg.gates.compare_production:
            return StageCheck(stage="compare_production", ok=True, detail="gate disabled")
        prod = self.models.production(entry.family)
        if prod is None or prod.key == entry.key:
            return StageCheck(stage="compare_production", ok=True, detail="no production model to compare against")
        if candidate is None:
            return StageCheck(stage="compare_production", ok=False, detail="candidate has no ZaraBench report")
        found = _latest(_reports(self._suite_dir(self.eval_cfg)), lambda r: _matches_entry(r, prod))
        if found is None:
            return StageCheck(
                stage="compare_production", ok=False, detail=f"production model {prod.key} has no ZaraBench report"
            )
        decision = release_decision(self.eval_cfg, candidate, found[1], None)
        regressions = [r for r in decision.reasons if "< base" in r]
        if regressions:
            return StageCheck(
                stage="compare_production",
                ok=False,
                detail="regresses production: " + "; ".join(regressions),
                evidence=str(found[0]),
            )
        return StageCheck(
            stage="compare_production",
            ok=True,
            detail=f"≥ production {prod.key} on every priority category",
            evidence=str(found[0]),
        )

    def check(self, model_key: str, evidence: Evidence | None = None) -> ReleaseReport:
        entry = self.models.get(model_key)
        if entry is None:
            raise RegistryError(f"unknown model {model_key}")
        ev = evidence or Evidence()
        checks = [self._check_train(entry), self._check_validate(entry)]
        zb, candidate = self._check_zarabench(entry, ev)
        checks += [zb, self._check_security(entry, ev), self._check_compare(entry, candidate)]
        status = entry.deployment_status.value
        pct = self.canary_percent()
        reached = _LIFECYCLE.index(status) if status in _LIFECYCLE else -1
        for i, st in enumerate(_LIFECYCLE[1:], start=1):
            if st == "production":
                checks.append(
                    StageCheck(
                        stage="canary",
                        ok=reached >= 2 and pct > 0,
                        detail=f"{self.cfg.canary.route} canary {pct:g}% (steps {self.cfg.canary.steps})",
                        evidence=self.cfg.canary.routing_policy,
                    )
                )
            checks.append(
                StageCheck(
                    stage=st, ok=reached >= i, detail=f"registry status {status}", evidence=str(self.models.path)
                )
            )
        report = ReleaseReport(model=model_key, status=status, canary_percent=pct, checks=checks)
        report.blockers = [
            f"{c.stage}: {c.detail}"
            for c in checks
            if not c.ok and c.stage in ("train", "validate", "zarabench", "security", "compare_production")
        ]
        report.next_step = self._next_step(entry, report, pct)
        return report

    def _next_step(self, entry: ModelEntry, report: ReleaseReport, pct: float) -> str | None:
        gates_ok = all(report.ok(s) for s in ("train", "validate", "zarabench", "security"))
        st = entry.deployment_status
        if st == ModelStatus.EXPERIMENTAL:
            return "candidate" if gates_ok else None
        if st == ModelStatus.CANDIDATE:
            return "staging" if gates_ok and report.ok("compare_production") else None
        if st == ModelStatus.STAGING:
            if pct <= 0:
                return "canary"
            return "production" if pct >= 100 else "canary"
        return None

    # ---- actions ----------------------------------------------------------------------------------------------------
    def promote(self, model_key: str, to: str, *, reason: str = "", evidence: Evidence | None = None) -> ReleaseReport:
        report = self.check(model_key, evidence)
        allowed = report.next_step
        if to != allowed:
            raise RegistryError(
                f"{model_key} cannot move to {to} now: evidence supports {allowed or 'nothing'}; "
                f"blockers: {report.blockers or 'none'}"
            )
        entry = self.models.get(model_key)
        if to in ("candidate", "staging"):
            self.models.transition(model_key, ModelStatus(to), reason=reason or f"release pipeline: {to}")
        elif to == "canary":
            self.set_canary(self.cfg.canary.steps[0], reason=reason or "release pipeline: first canary step")
        elif to == "production":
            prod = self.models.production(entry.family)
            if prod is not None and prod.key != model_key:
                self.models.transition(prod.key, ModelStatus.DEPRECATED, reason=f"replaced by {model_key}")
            self.models.transition(model_key, ModelStatus.PRODUCTION, reason=reason or "release pipeline: production")
        self._log("promote", model=model_key, to=to, reason=reason, canary=self.canary_percent())
        return self.check(model_key, evidence)

    def canary_step(self, *, reason: str = "") -> float:
        staged = [
            e
            for e in self.models.list()
            if e.family == self.cfg.family and e.deployment_status in (ModelStatus.STAGING, ModelStatus.PRODUCTION)
        ]
        if not staged:
            raise RegistryError(
                f"no {self.cfg.family} model in staging or production; promote one before raising the canary"
            )
        current = self.canary_percent()
        nxt = next((s for s in self.cfg.canary.steps if s > current), None)
        if nxt is None:
            raise RegistryError(f"canary already at {current:g}% (last step {self.cfg.canary.steps[-1]:g}%)")
        self.set_canary(nxt, reason=reason or f"canary step {current:g}% -> {nxt:g}%")
        return nxt

    def set_canary(self, percent: float, *, reason: str = "") -> None:
        path = self.root / self.cfg.canary.routing_policy
        text = path.read_text(encoding="utf-8")
        new, n = re.subn(r"(?m)^canary_percent:\s*[\d.]+", f"canary_percent: {percent:g}", text)
        if n != 1:
            raise RegistryError(f"{path}: expected exactly one canary_percent line, found {n}")
        load_config(path, "routing")  # the file must still validate before and after
        path.write_text(new, encoding="utf-8")
        load_config(path, "routing")
        self._log("canary", percent=percent, reason=reason)

    def rollback(self, *, reason: str, model_key: str | None = None) -> dict[str, Any]:
        """Canary to zero and, when a model is named (or a production model exists), take it out of production and
        reinstate the previous production model of the family if there is one."""
        self.set_canary(0.0, reason=f"rollback: {reason}")
        out: dict[str, Any] = {"canary_percent": 0.0, "demoted": None, "reinstated": None}
        entry = self.models.get(model_key) if model_key else self.models.production(self.cfg.family)
        if entry is not None and entry.deployment_status == ModelStatus.PRODUCTION:
            self.models.transition(entry.key, ModelStatus.DEPRECATED, reason=f"rollback: {reason}")
            out["demoted"] = entry.key
            previous = [
                e
                for e in self.models.list()
                if e.family == entry.family
                and e.key != entry.key
                and e.deployment_status == ModelStatus.DEPRECATED
                and any(h.get("from") == "production" for h in e.history)
            ]
            if previous:
                prev = max(previous, key=lambda e: e.history[-1].get("at", ""))
                self.models.transition(prev.key, ModelStatus.STAGING, reason=f"rollback reinstate: {reason}")
                self.models.transition(prev.key, ModelStatus.PRODUCTION, reason=f"rollback reinstate: {reason}")
                out["reinstated"] = prev.key
        self._log("rollback", reason=reason, **out)
        return out

    def _log(self, event: str, **fields: Any) -> None:
        path = self.root / self.cfg.release_log
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"event": event, "at": time.time(), "release": self.cfg.name, **fields}, default=str) + "\n"
            )


def _weak_families(report: BenchmarkReport, floor: float) -> list[str]:
    by_family: dict[str, list[bool]] = {}
    for r in report.results:
        by_family.setdefault(r.family or "other", []).append(bool(r.passed))
    return sorted(f for f, flags in by_family.items() if sum(flags) / len(flags) < floor)


def should_rollback(
    bucket: dict[str, Any], triggers: RollbackTriggers, *, feedback_negative_share: float | None = None
) -> list[str]:
    """Breached rollback triggers for one observability bucket (calls, validation_pass_rate, fallback_rate,
    latency_ms_p95, error_rate). An inconclusive window (too few calls) never triggers."""
    calls = int(bucket.get("calls") or 0)
    if calls < triggers.min_calls:
        return []
    reasons = []
    vpr = bucket.get("validation_pass_rate")
    if vpr is not None and float(vpr) < triggers.validation_pass_rate_min:
        reasons.append(f"validation pass rate {float(vpr):.2f} < {triggers.validation_pass_rate_min:.2f}")
    fb = float(bucket.get("fallback_rate") or 0.0)
    if fb > triggers.fallback_rate_max:
        reasons.append(f"fallback rate {fb:.2f} > {triggers.fallback_rate_max:.2f}")
    err = float(bucket.get("error_rate") or 0.0)
    if err > triggers.error_rate_max:
        reasons.append(f"error rate {err:.3f} > {triggers.error_rate_max:.3f}")
    p95 = int(bucket.get("latency_ms_p95") or 0)
    if p95 > triggers.latency_p95_ms_max:
        reasons.append(f"latency p95 {p95} ms > {triggers.latency_p95_ms_max} ms")
    if feedback_negative_share is not None and feedback_negative_share > triggers.feedback_negative_share_max:
        reasons.append(
            f"negative feedback share {feedback_negative_share:.2f} > {triggers.feedback_negative_share_max:.2f}"
        )
    return reasons
