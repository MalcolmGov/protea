"""File-backed registries (JSON in git). MLflow can mirror these later; the file stays the source of truth."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from protea.schemas.registry import DatasetEntry, ModelEntry, ModelStatus

T = TypeVar("T", bound=BaseModel)


class RegistryError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _JsonRegistry(Generic[T]):
    entry_type: type[T]

    def __init__(self, path: Path):
        self.path = path
        self._entries: list[T] = []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8") or "[]")
            self._entries = [self.entry_type.model_validate(d) for d in data]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([e.model_dump(mode="json") for e in self._entries], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def list(self) -> list[T]:
        return list(self._entries)

    def get(self, key: str) -> T | None:
        return next((e for e in self._entries if e.key == key), None)  # type: ignore[attr-defined]

    def add(self, entry: T) -> T:
        if self.get(entry.key):  # type: ignore[attr-defined]
            raise RegistryError(f"{entry.key} already registered; bump the version")  # type: ignore[attr-defined]
        self._entries.append(entry)
        self.save()
        return entry


class DatasetRegistry(_JsonRegistry[DatasetEntry]):
    entry_type = DatasetEntry

    def verify(self, key: str, root: Path) -> bool:
        entry = self.get(key)
        if not entry:
            raise RegistryError(f"unknown dataset {key}")
        return sha256_file(root / entry.path) == entry.sha256


class ModelRegistry(_JsonRegistry[ModelEntry]):
    entry_type = ModelEntry

    def transition(self, key: str, to: ModelStatus, reason: str = "") -> ModelEntry:
        entry = self.get(key)
        if not entry:
            raise RegistryError(f"unknown model {key}")
        if not entry.can_transition(to):
            raise RegistryError(f"{key}: cannot move from {entry.deployment_status.value} to {to.value}")
        if to == ModelStatus.PRODUCTION:
            for other in self._entries:
                if (
                    other.family == entry.family
                    and other.key != key
                    and other.deployment_status == ModelStatus.PRODUCTION
                ):
                    raise RegistryError(f"{other.key} is already production for {entry.family}; deprecate it first")
        entry.history.append(
            {
                "from": entry.deployment_status.value,
                "to": to.value,
                "at": datetime.now(UTC).isoformat(),
                "reason": reason,
            }
        )
        entry.deployment_status = to
        self.save()
        return entry

    def production(self, family: str) -> ModelEntry | None:
        return next(
            (e for e in self._entries if e.family == family and e.deployment_status == ModelStatus.PRODUCTION), None
        )
