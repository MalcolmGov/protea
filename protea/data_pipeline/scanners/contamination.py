"""Detect eval phrases injected into knowledge text (the upstream `heal:evals` problem, data-risk-assessment §5).

A grounded fact ("Delivery R60") legitimately appears in both knowledge and an eval's says_any. Injected filler looks
different: a knowledge line that is built from several distinct eval phrases. We score each eval by whether its
says_any phrases (3+ words) land on such lines.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContaminationReport(BaseModel):
    evals_total: int = 0
    evals_contaminated: int = 0
    contaminated_eval_ids: list[str] = Field(default_factory=list)
    suspicious_lines: int = 0

    @property
    def ratio(self) -> float:
        return self.evals_contaminated / self.evals_total if self.evals_total else 0.0


def _phrases(expect: dict) -> list[str]:
    out = []
    for key in ("says_any", "says_none"):
        for s in expect.get(key) or []:
            if isinstance(s, str) and len(s.split()) >= 3:
                out.append(s.strip().lower())
    return out


def detect_contamination(knowledge: str, evals: list[dict]) -> ContaminationReport:
    report = ContaminationReport(evals_total=len(evals))
    lines = [ln.strip().lower() for ln in (knowledge or "").splitlines() if ln.strip()]
    all_phrases = {p for e in evals for p in _phrases(e.get("expect") or {})}
    if not lines or not all_phrases:
        return report
    line_hits = {i: {p for p in all_phrases if p in ln} for i, ln in enumerate(lines)}
    suspicious = {i for i, hits in line_hits.items() if len(hits) >= 2 or (hits and lines[i] in hits)}
    report.suspicious_lines = len(suspicious)
    for e in evals:
        mine = set(_phrases(e.get("expect") or {}))
        if any(mine & line_hits[i] for i in suspicious):
            report.evals_contaminated += 1
            report.contaminated_eval_ids.append(str(e.get("id")))
    return report
