"""PII detection and format-preserving redaction (spec §5, §77).

Catalogue prompts and evals contain *example* contact details. We cannot tell a real number from a fictional one,
so every phone/email/ID is replaced. `synthetic` mode keeps examples realistic with deterministic fictional values;
`placeholder` mode uses [PHONE]/[EMAIL]/[SA_ID]; `block` mode rejects the artefact.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable

from pydantic import BaseModel

# Digit-count based so unusual grouping ("0 108 800 011") still matches; separators optional between digits.
_PHONE_PATTERNS = [
    re.compile(r"(?<![\w.])(?:\+?27|0)(?:[\s-]?\d){9}(?!\w)"),  # South Africa: 0 / +27 followed by nine digits
    re.compile(r"(?<![\w.])\+?2(?:54|55|56|34)(?:[\s-]?\d){8,9}(?!\w)"),  # KE, TZ, UG, NG
]
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SA_ID = re.compile(r"(?<!\d)\d{13}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
# Two explicit forms (one- and two-word street names) so a non-street trailing word cannot swallow a valid match.
_ADDRESS_FORMS = [
    re.compile(r"\b\d{1,5}\s+[A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+\s+([A-Z][a-z]+)\b"),
    re.compile(r"\b\d{1,5}\s+[A-Z][a-zA-Z]+\s+([A-Z][a-z]+)\b"),
]
_STREET_WORDS = {"Road", "Rd", "Street", "St", "Avenue", "Ave", "Drive", "Dr", "Lane", "Crescent", "Close", "Way"}
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


def _stable(seed: str, n: int) -> str:
    return str(int(hashlib.sha256(seed.encode()).hexdigest(), 16) % (10**n)).zfill(n)


def _fake_phone(original: str) -> str:
    return f"+27 60 555 {_stable(original, 4)}"


def _fake_email(original: str) -> str:
    return f"person{_stable(original, 4)}@example.com"


# kind, pattern, acceptance test on the match — order matters: emails and IDs before cards before phones.
_DETECTORS: list[tuple[str, re.Pattern[str], Callable[[re.Match[str]], bool]]] = [
    ("email", _EMAIL, lambda m: not m.group(0).lower().endswith(_PLACEHOLDER_DOMAINS)),
    ("sa_id", _SA_ID, lambda m: _sa_id_plausible(m.group(0))),
    ("card", _CARD, lambda m: _card_plausible(m.group(0))),
    *[("phone", pat, lambda m: True) for pat in _PHONE_PATTERNS],
    *[("address", pat, lambda m: m.group(1) in _STREET_WORDS) for pat in _ADDRESS_FORMS],
]

_PREVIEW_CHARS = {"email": 3, "sa_id": 2, "card": 0, "phone": 4, "address": 8}

# Replacement per kind; `synthetic` keeps examples realistic, `placeholder` uses tokens.
_REPLACERS: dict[str, Callable[[str, str], str]] = {
    "email": lambda s, mode: "[EMAIL]" if mode == "placeholder" else _fake_email(s),
    "sa_id": lambda s, mode: "[SA_ID]",
    "card": lambda s, mode: "[CARD]",
    "phone": lambda s, mode: "[PHONE]" if mode == "placeholder" else _fake_phone(s),
    "address": lambda s, mode: "[ADDRESS]" if mode == "placeholder" else f"{_stable(s, 2)} Example Road",
}


def scan_pii(text: str) -> list[PiiHit]:
    hits: list[PiiHit] = []
    seen: set[tuple[str, int]] = set()
    for kind, pat, accept in _DETECTORS:
        for m in pat.finditer(text or ""):
            if accept(m) and (kind, m.start()) not in seen:
                seen.add((kind, m.start()))
                n = _PREVIEW_CHARS[kind]
                hits.append(PiiHit(kind=kind, preview=(m.group(0)[:n] + "…") if n else "card…"))
    return hits


def redact(text: str, mode: str = "synthetic") -> tuple[str, dict[str, int]]:
    """Replace PII in text. Returns (new_text, counts). Deterministic so the same value is replaced identically everywhere."""
    if not text or mode == "block":
        return text, {}
    counts: Counter[str] = Counter()
    out = text
    for kind, pat, accept in _DETECTORS:

        def sub(m: re.Match[str], kind: str = kind, accept: Callable[[re.Match[str]], bool] = accept) -> str:
            if not accept(m):
                return m.group(0)
            counts[kind] += 1
            return _REPLACERS[kind](m.group(0), mode)

        out = pat.sub(sub, out)
    return out, dict(counts)
