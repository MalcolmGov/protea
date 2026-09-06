"""Secret detection before any training-data export (spec §5). Conservative: a hit blocks the artefact."""

from __future__ import annotations

import re

from pydantic import BaseModel

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("zara_partner_key", re.compile(r"\bpak_[A-Za-z0-9]{16,}")),
    ("zara_session", re.compile(r"\baps_[A-Za-z0-9]{16,}")),
    ("embed_key", re.compile(r"\bmia_pk_[a-f0-9]{16,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{24,}")),
    (
        "password_assignment",
        re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
    ),
    ("database_url", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s'\"]*:[^\s'\"@]+@")),
]


class SecretHit(BaseModel):
    rule: str
    preview: str  # redacted; never the secret itself


def scan_secrets(text: str) -> list[SecretHit]:
    hits: list[SecretHit] = []
    for rule, pat in _RULES:
        for m in pat.finditer(text or ""):
            s = m.group(0)
            hits.append(SecretHit(rule=rule, preview=f"{s[:6]}…{s[-2:]}" if len(s) > 10 else "[redacted]"))
    return hits
