"""PII detection and format-preserving redaction (spec §5, §77).

Catalogue prompts and evals contain *example* contact details. We cannot tell a real number from a fictional one,
so every phone/email/ID is replaced. `synthetic` mode keeps examples realistic with deterministic fictional values;
`placeholder` mode uses [PHONE]/[EMAIL]/[SA_ID]; `block` mode rejects the artefact.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from pydantic import BaseModel

_PHONE = re.compile(
    r"(?<![\w.])(?:\+?27|0|\+?254|\+?255|\+?256|\+?234)[\s-]?\(?\d{2,3}\)?[\s-]?\d{3}[\s-]?\d{3,4}(?![\w])"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SA_ID = re.compile(r"(?<!\d)\d{13}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?\s+(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Lane|Crescent|Close|Way)\b"
)
_PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net", "zaraai.digital")
# Canonical payment-gateway test cards: fictional by definition, kept so guardrail text stays readable.
_TEST_CARDS = {
    "4111111111111111",
    "4242424242424242",
    "5555555555554444",
    "5105105105105100",
    "378282246310005",
    "4000000000000002",
}


class PiiHit(BaseModel):
    kind: str
    preview: str


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _sa_id_plausible(s: str) -> bool:
    mm, dd = int(s[2:4]), int(s[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31 and _luhn_ok(s)


def _card_plausible(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if digits in _TEST_CARDS or _SA_ID.fullmatch(digits):
        return False
    return 13 <= len(digits) <= 19 and _luhn_ok(digits)


def scan_pii(text: str) -> list[PiiHit]:
    hits: list[PiiHit] = []
    t = text or ""
    for m in _EMAIL.finditer(t):
        if not m.group(0).lower().endswith(_PLACEHOLDER_DOMAINS):
            hits.append(PiiHit(kind="email", preview=m.group(0)[:3] + "…"))
    for m in _SA_ID.finditer(t):
        if _sa_id_plausible(m.group(0)):
            hits.append(PiiHit(kind="sa_id", preview=m.group(0)[:2] + "…"))
    for m in _PHONE.finditer(t):
        hits.append(PiiHit(kind="phone", preview=m.group(0)[:4] + "…"))
    for m in _CARD.finditer(t):
        if _card_plausible(m.group(0)):
            hits.append(PiiHit(kind="card", preview="card…"))
    for m in _ADDRESS.finditer(t):
        hits.append(PiiHit(kind="address", preview=m.group(0)[:8] + "…"))
    return hits


def _stable(seed: str, n: int) -> str:
    return str(int(hashlib.sha256(seed.encode()).hexdigest(), 16) % (10**n)).zfill(n)


def _fake_phone(original: str) -> str:
    return f"+27 60 555 {_stable(original, 4)}"


def _fake_email(original: str) -> str:
    return f"person{_stable(original, 4)}@example.com"


def redact(text: str, mode: str = "synthetic") -> tuple[str, dict[str, int]]:
    """Replace PII in text. Returns (new_text, counts). Deterministic so the same value is replaced identically everywhere."""
    counts: Counter[str] = Counter()
    if not text or mode == "block":
        return text, {}

    def sub_email(m: re.Match[str]) -> str:
        if m.group(0).lower().endswith(_PLACEHOLDER_DOMAINS):
            return m.group(0)
        counts["email"] += 1
        return "[EMAIL]" if mode == "placeholder" else _fake_email(m.group(0))

    def sub_id(m: re.Match[str]) -> str:
        if not _sa_id_plausible(m.group(0)):
            return m.group(0)
        counts["sa_id"] += 1
        return "[SA_ID]"

    def sub_card(m: re.Match[str]) -> str:
        if not _card_plausible(m.group(0)):
            return m.group(0)
        counts["card"] += 1
        return "[CARD]"

    def sub_phone(m: re.Match[str]) -> str:
        counts["phone"] += 1
        return "[PHONE]" if mode == "placeholder" else _fake_phone(m.group(0))

    def sub_addr(m: re.Match[str]) -> str:
        counts["address"] += 1
        return "[ADDRESS]" if mode == "placeholder" else f"{_stable(m.group(0), 2)} Example Road"

    out = _EMAIL.sub(sub_email, text)
    out = _SA_ID.sub(sub_id, out)
    out = _CARD.sub(sub_card, out)
    out = _PHONE.sub(sub_phone, out)
    out = _ADDRESS.sub(sub_addr, out)
    return out, dict(counts)
