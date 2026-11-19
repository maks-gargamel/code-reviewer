"""Хранилище состояния, baseline, последнего ревью и кэша ответов LLM."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import BASELINE_NAME, LAST_REVIEW_NAME, STATE_NAME, service_dir
from .fs import atomic_write_text
from .models import Finding, utc_now

STATE_VERSION = 1


@dataclass(slots=True)
class State:
    """Метаданные состояния карты и проверок."""

    version: int = STATE_VERSION
    last_map_at: str | None = None
    last_map_head: str | None = None
    last_reviewed_commit: str | None = None
    last_check_at: str | None = None
    branch: str | None = None
    remote_warned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        return cls(
            version=int(data.get("version", STATE_VERSION)),
            last_map_at=data.get("last_map_at"),
            last_map_head=data.get("last_map_head"),
            last_reviewed_commit=data.get("last_reviewed_commit"),
            last_check_at=data.get("last_check_at"),
            branch=data.get("branch"),
            remote_warned=bool(data.get("remote_warned", False)),
        )

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.is_file():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_dict(payload) if isinstance(payload, dict) else cls()

    def save(self, path: Path) -> None:
        atomic_write_text(path, json.dumps(self.to_dict(), ensure_ascii=False, indent=1))


def state_path(root: Path) -> Path:
    return service_dir(root) / STATE_NAME


@dataclass(slots=True)
class Baseline:
    """Набор ID зафиксированных замечаний, которые не нужно показывать повторно."""

    ids: set[str] = field(default_factory=set)
    created_at: str | None = None
    commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "commit": self.commit,
            "count": len(self.ids),
            "ids": sorted(self.ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Baseline:
        raw = data.get("ids", [])
        ids = {str(item) for item in raw} if isinstance(raw, list) else set()
        return cls(
            ids=ids,
            created_at=data.get("created_at"),
            commit=data.get("commit"),
        )

    def save(self, path: Path) -> None:
        atomic_write_text(path, json.dumps(self.to_dict(), ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, path: Path) -> Baseline:
        if not path.is_file():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_dict(payload) if isinstance(payload, dict) else cls()


def baseline_path(root: Path) -> Path:
    return service_dir(root) / BASELINE_NAME


def last_review_path(root: Path) -> Path:
    return service_dir(root) / LAST_REVIEW_NAME


def load_baseline(root: Path) -> Baseline:
    return Baseline.load(baseline_path(root))


def save_baseline(root: Path, findings: list[Finding], *, commit: str | None) -> Baseline:
    baseline = Baseline(
        ids={item.id for item in findings},
        created_at=utc_now(),
        commit=commit,
    )
    baseline.save(baseline_path(root))
    return baseline


def save_last_review(root: Path, findings: list[Finding], *, commit: str | None) -> None:
    payload = {
        "created_at": utc_now(),
        "commit": commit,
        "findings": [item.to_dict() for item in findings],
    }
    atomic_write_text(
        last_review_path(root),
        json.dumps(payload, ensure_ascii=False, indent=1),
    )


def load_last_review(root: Path) -> list[Finding]:
    path = last_review_path(root)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = payload.get("findings", []) if isinstance(payload, dict) else []
    if not isinstance(raw, list):
        return []

    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file", "")).strip()
        message = str(item.get("message", "")).strip()
        if not file or not message:
            continue
        try:
            line = int(item.get("line", 0))
        except (TypeError, ValueError):
            line = 0
        findings.append(
            Finding(
                file=file,
                line=max(0, line),
                severity=str(item.get("severity", "warning")),
                category=str(item.get("category", "logic")),
                message=message,
                suggestion=str(item.get("suggestion", "")),
                id=str(item.get("id", "")),
            )
        )
    return findings


def cache_key(model: str, system: str, user: str) -> str:
    """Детерминированный SHA-256 ключ кэша (без путей и времени)."""
    digest = hashlib.sha256()
    for part in (model, system, user):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class ResponseCache:
    """Файловый кэш ответов LLM по хэшу (model, system, user)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> str | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        response = payload.get("response")
        return response if isinstance(response, str) else None

    def put(self, key: str, model: str, response: str) -> None:
        payload = {"created_at": utc_now(), "model": model, "response": response}
        atomic_write_text(
            self.path_for(key),
            json.dumps(payload, ensure_ascii=False, indent=1),
        )


def cache_stats(directory: Path) -> tuple[int, int]:
    """Количество файлов и суммарный размер кэша в байтах."""
    if not directory.is_dir():
        return 0, 0
    files = [path for path in directory.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def clean_cache(directory: Path) -> int:
    """Удалить каталог кэша и вернуть число удалённых файлов."""
    count, _ = cache_stats(directory)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    return count
