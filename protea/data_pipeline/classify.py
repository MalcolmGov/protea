"""Training-data classification (spec §5). Everything starts UNKNOWN; only allowlisted, scanned, licensed artefacts become SAFE."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from protea.data_pipeline.scanners.pii import PiiHit
from protea.data_pipeline.scanners.secrets import SecretHit


class Classification(StrEnum):
    SAFE_FOR_TRAINING = "SAFE_FOR_TRAINING"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    RAG_ONLY = "RAG_ONLY"
    DO_NOT_TRAIN = "DO_NOT_TRAIN"
    SECRET = "SECRET"
    CUSTOMER_DATA = "CUSTOMER_DATA"
    PII = "PII"
    LICENSE_RESTRICTED = "LICENSE_RESTRICTED"
    UNKNOWN = "UNKNOWN"


EXCLUDED = {
    Classification.SECRET,
    Classification.CUSTOMER_DATA,
    Classification.PII,
    Classification.LICENSE_RESTRICTED,
    Classification.UNKNOWN,
    Classification.DO_NOT_TRAIN,
}

# Field-level defaults for agent packages (data-risk-assessment §1.1 / §1.2)
FIELD_DEFAULTS = {
    "manifest": Classification.SAFE_FOR_TRAINING,
    "system_prompt": Classification.SAFE_FOR_TRAINING,
    "tools": Classification.SAFE_FOR_TRAINING,
    "guardrails": Classification.SAFE_FOR_TRAINING,
    "knowledge": Classification.RAG_ONLY,
    "evals": Classification.REQUIRES_REVIEW,  # authored sources upgrade this to SAFE
}


class Decision(BaseModel):
    classification: Classification
    reasons: list[str] = Field(default_factory=list)

    @property
    def trainable(self) -> bool:
        return self.classification == Classification.SAFE_FOR_TRAINING


def classify_field(
    field: str,
    *,
    license_status: str,
    commit_known: bool,
    authored: bool,
    secrets: list[SecretHit],
    pii: list[PiiHit],
    pii_mode: str,
    contaminated: bool = False,
) -> Decision:
    reasons: list[str] = []
    if license_status == "restricted":
        return Decision(classification=Classification.LICENSE_RESTRICTED, reasons=["source licence restricted"])
    if license_status != "approved":
        return Decision(classification=Classification.UNKNOWN, reasons=["source licence unknown"])
    if not commit_known:
        return Decision(classification=Classification.UNKNOWN, reasons=["source commit could not be resolved"])
    if secrets:
        return Decision(classification=Classification.SECRET, reasons=[f"secret pattern {h.rule}" for h in secrets])
    if pii and pii_mode == "block":
        return Decision(classification=Classification.PII, reasons=[f"pii {h.kind}" for h in pii])
    base = FIELD_DEFAULTS.get(field, Classification.UNKNOWN)
    if field == "evals" and authored and not contaminated:
        base = Classification.SAFE_FOR_TRAINING
        reasons.append("authored source: evals trusted")
    if contaminated:
        base = Classification.REQUIRES_REVIEW
        reasons.append("knowledge/eval contamination detected")
    if pii:
        reasons.append(f"pii redacted ({pii_mode}): " + ", ".join(sorted({h.kind for h in pii})))
    return Decision(classification=base, reasons=reasons)
