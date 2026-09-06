"""Brand and infrastructure scrub: vendor names, hosts, IPs and paths never reach the weights."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

# Built-in twin of aria/scripts/marketplace/brand_scrub.json so the pipeline works without the file.
_DEFAULT_BRAND = [
    (r"(?i)my\s*instant\s*ai", "Zara"),
    (r"(?i)app\.myinstantai\.com", "zaraai.digital"),
    (r"(?i)myinstantai\.com", "zaraai.digital"),
    (r"\bMIAI\b", "Zara"),
    (r"miai\.agent-package", "zara.agent-package"),
    (r"@miai/", "@zara/"),
]
_DEFAULT_INFRA = [
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP]"),
    (r"\b[\w.-]+\.up\.railway\.app\b", "[HOST]"),
    (r"\b[\w.-]+\.duckdns\.org\b", "[HOST]"),
    (r"/opt/aria\b", "/opt/app"),
    (r"\bssh\s+root@\S+", "ssh [HOST]"),
]


class ScrubRules(BaseModel):
    brand: list[tuple[str, str]] = Field(default_factory=lambda: list(_DEFAULT_BRAND))
    infra: list[tuple[str, str]] = Field(default_factory=lambda: list(_DEFAULT_INFRA))

    @classmethod
    def load(cls, brand_rules_path: Path | None = None, extra_infra: dict[str, str] | None = None) -> ScrubRules:
        rules = cls()
        if brand_rules_path and brand_rules_path.exists():
            data = json.loads(brand_rules_path.read_text(encoding="utf-8"))
            loaded = [(p["pattern"], p["replace"]) for p in data.get("forbidden_patterns", []) if "pattern" in p]
            if loaded:
                rules.brand = loaded
        for pat, rep in (extra_infra or {}).items():
            rules.infra.append((pat, rep))
        return rules

    def apply(self, text: str) -> tuple[str, int]:
        if not text:
            return text, 0
        n = 0
        for pat, rep in self.brand + self.infra:
            text, k = re.subn(pat, rep, text)
            n += k
        return text, n
