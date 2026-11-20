"""Доменные модели карты репозитория, графа зависимостей и находок ревью."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .fs import atomic_write_text

MAP_VERSION = 2
TOOL_NAME = "code-reviewer"


class EdgeStatus(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNRESOLVED = "unresolved"


class SymbolKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    NESTED_FUNCTION = "nested_function"
    CONSTANT = "constant"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    MD = "md"
    SARIF = "sarif"


class FailOn(StrEnum):
    NEVER = "never"
    WARNING = "warning"
    ERROR = "error"


class Strictness(StrEnum):
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"


class ReviewLanguage(StrEnum):
    RU = "ru"
    EN = "en"


SEVERITY_ORDER: dict[str, int] = {
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.ERROR: 3,
}

SEVERITY_ALIASES: dict[str, str] = {
    "critical": Severity.ERROR,
    "blocker": Severity.ERROR,
    "major": Severity.ERROR,
    "high": Severity.ERROR,
    "bug": Severity.ERROR,
    "err": Severity.ERROR,
    "medium": Severity.WARNING,
    "moderate": Severity.WARNING,
    "warn": Severity.WARNING,
    "minor": Severity.INFO,
    "low": Severity.INFO,
    "trivial": Severity.INFO,
    "nit": Severity.INFO,
    "note": Severity.INFO,
    "suggestion": Severity.INFO,
}


def utc_now() -> str:
    """Текущее время UTC в формате ISO-8601 без микросекунд."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_severity(value: object) -> str:
    """Привести произвольный уровень важности к info | warning | error."""
    text = str(value or "").strip().lower()
    mapped = SEVERITY_ALIASES.get(text, text)
    return mapped if mapped in SEVERITY_ORDER else Severity.WARNING


def finding_id(file: str, line: int, message: str) -> str:
    """Стабильный 8-символьный идентификатор находки для baseline и --verify."""
    digest = hashlib.sha256(f"{file}:{line}:{message}".encode()).hexdigest()
    return digest[:8]


@dataclass(slots=True)
class Symbol:
    """Класс, функция, метод, вложенная функция или константа."""

    name: str
    kind: str
    line: int
    end_line: int
    signature: str = ""
    doc: str | None = None
    parent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "line": self.line,
            "end_line": self.end_line,
        }
        if self.signature:
            data["signature"] = self.signature
        if self.doc:
            data["doc"] = self.doc
        if self.parent:
            data["parent"] = self.parent
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Symbol:
        line = int(data.get("line", 0))
        return cls(
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "unknown")),
            line=line,
            end_line=int(data.get("end_line", line)),
            signature=str(data.get("signature", "")),
            doc=data.get("doc"),
            parent=data.get("parent"),
        )


@dataclass(slots=True)
class ImportRef:
    """Один оператор импорта / подключения в исходном файле."""

    raw: str
    line: int
    module: str = ""
    names: list[str] = field(default_factory=list)
    level: int = 0
    is_from: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "line": self.line,
            "module": self.module,
            "names": list(self.names),
            "level": self.level,
            "is_from": self.is_from,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImportRef:
        return cls(
            raw=str(data.get("raw", "")),
            line=int(data.get("line", 0)),
            module=str(data.get("module", "")),
            names=[str(n) for n in data.get("names", [])],
            level=int(data.get("level", 0)),
            is_from=bool(data.get("is_from", False)),
        )

    @property
    def dotted(self) -> str:
        """Представление модуля с точками относительности (например `..utils`)."""
        return "." * self.level + self.module


@dataclass(slots=True)
class GraphEdge:
    """Ребро графа зависимостей между файлами или к внешнему модулю."""

    src: str
    dst: str | None
    name: str
    kind: str  # import | from | include
    status: str  # internal | external | unresolved
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "line": self.line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphEdge:
        return cls(
            src=str(data.get("src", "")),
            dst=data.get("dst"),
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "import")),
            status=str(data.get("status", EdgeStatus.EXTERNAL)),
            line=int(data.get("line", 0)),
        )


@dataclass(slots=True)
class FileInfo:
    """Метаданные и извлечённая структура одного файла репозитория."""

    path: str
    lang: str
    size: int = 0
    mtime: float = 0.0
    loc: int = 0
    digest: str = ""
    doc: str | None = None
    summary: str | None = None
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "lang": self.lang,
            "size": self.size,
            "mtime": round(self.mtime, 3),
            "loc": self.loc,
            "digest": self.digest,
        }
        if self.doc:
            data["doc"] = self.doc
        if self.summary:
            data["summary"] = self.summary
        if self.parse_errors:
            data["parse_errors"] = list(self.parse_errors)
        if self.symbols:
            data["symbols"] = [s.to_dict() for s in self.symbols]
        if self.imports:
            data["imports"] = [i.to_dict() for i in self.imports]
        return data

    @classmethod
    def from_dict(cls, path: str, data: dict[str, Any]) -> FileInfo:
        return cls(
            path=path,
            lang=str(data.get("lang", "text")),
            size=int(data.get("size", 0)),
            mtime=float(data.get("mtime", 0.0)),
            loc=int(data.get("loc", 0)),
            digest=str(data.get("digest", "")),
            doc=data.get("doc"),
            summary=data.get("summary"),
            symbols=[Symbol.from_dict(s) for s in data.get("symbols", [])],
            imports=[ImportRef.from_dict(i) for i in data.get("imports", [])],
            parse_errors=[str(e) for e in data.get("parse_errors", [])],
        )


@dataclass(slots=True)
class RepoMap:
    """Карта репозитория с ленивыми O(1) индексами графа зависимостей."""

    files: dict[str, FileInfo] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    version: int = MAP_VERSION
    tool: str = TOOL_NAME
    generated_at: str = field(default_factory=utc_now)
    head: str | None = None
    root: str = ""
    source_roots: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    _out_index: dict[str, list[GraphEdge]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _in_index: dict[str, list[GraphEdge]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _ext_index: dict[str, list[GraphEdge]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _indexed_edge_count: int = field(default=-1, init=False, repr=False, compare=False)

    def invalidate_index(self) -> None:
        """Сбросить кэш индексов смежности после ручного изменения edges."""
        self._out_index = {}
        self._in_index = {}
        self._ext_index = {}
        self._indexed_edge_count = -1

    def _ensure_index(self) -> None:
        if self._indexed_edge_count == len(self.edges):
            return
        out_idx: dict[str, list[GraphEdge]] = {}
        in_idx: dict[str, list[GraphEdge]] = {}
        ext_idx: dict[str, list[GraphEdge]] = {}
        for edge in self.edges:
            if edge.status == EdgeStatus.INTERNAL and edge.dst is not None:
                out_idx.setdefault(edge.src, []).append(edge)
                in_idx.setdefault(edge.dst, []).append(edge)
            else:
                ext_idx.setdefault(edge.src, []).append(edge)
        self._out_index = out_idx
        self._in_index = in_idx
        self._ext_index = ext_idx
        self._indexed_edge_count = len(self.edges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tool": self.tool,
            "generated_at": self.generated_at,
            "head": self.head,
            "root": self.root,
            "source_roots": list(self.source_roots),
            "stats": dict(self.stats),
            "files": {path: info.to_dict() for path, info in sorted(self.files.items())},
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepoMap:
        return cls(
            files={
                path: FileInfo.from_dict(path, info)
                for path, info in data.get("files", {}).items()
            },
            edges=[GraphEdge.from_dict(edge) for edge in data.get("edges", [])],
            version=int(data.get("version", MAP_VERSION)),
            tool=str(data.get("tool", TOOL_NAME)),
            generated_at=str(data.get("generated_at", utc_now())),
            head=data.get("head"),
            root=str(data.get("root", "")),
            source_roots=[str(r) for r in data.get("source_roots", [])],
            stats=dict(data.get("stats", {})),
        )

    def save(self, path: Path) -> None:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=1)
        atomic_write_text(path, payload)

    @classmethod
    def load(cls, path: Path) -> RepoMap:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def by_lang(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for info in self.files.values():
            counts[info.lang] = counts.get(info.lang, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def dependencies(self, path: str) -> list[GraphEdge]:
        """Внутренние импорты, исходящие из указанного файла."""
        self._ensure_index()
        return list(self._out_index.get(path, ()))

    def dependents(self, path: str) -> list[GraphEdge]:
        """Файлы, импортирующие указанный файл."""
        self._ensure_index()
        return list(self._in_index.get(path, ()))

    def externals(self, path: str) -> list[GraphEdge]:
        """Внешние и неразрешённые импорты файла."""
        self._ensure_index()
        return list(self._ext_index.get(path, ()))

    def related(self, path: str, depth: int = 1) -> set[str]:
        """Связанные файлы в обе стороны графа на глубину до `depth` шагов."""
        if depth <= 0:
            return set()
        self._ensure_index()

        seen: set[str] = {path}
        frontier: set[str] = {path}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for current in frontier:
                for edge in self._out_index.get(current, ()):
                    if edge.dst is not None and edge.dst not in seen:
                        next_frontier.add(edge.dst)
                for edge in self._in_index.get(current, ()):
                    if edge.src not in seen:
                        next_frontier.add(edge.src)
            if not next_frontier:
                break
            seen |= next_frontier
            frontier = next_frontier

        seen.discard(path)
        return seen

    def find(self, needle: str) -> list[str]:
        """Найти файлы в карте по точному совпадению, суффиксу пути или подстроке."""
        cleaned = needle.replace("\\", "/").strip("/")
        if not cleaned:
            return []
        if cleaned in self.files:
            return [cleaned]
        suffix = [p for p in self.files if p.endswith("/" + cleaned)]
        if len(suffix) == 1:
            return suffix
        if suffix:
            return sorted(suffix)
        return sorted(p for p in self.files if cleaned in p)


@dataclass(slots=True)
class Finding:
    """Одно замечание код-ревью."""

    file: str
    line: int
    severity: str
    category: str
    message: str
    suggestion: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        self.severity = normalize_severity(self.severity)
        self.category = (self.category or "logic").strip() or "logic"
        self.message = " ".join(self.message.split())
        self.suggestion = " ".join(self.suggestion.split())
        if not self.id:
            self.id = finding_id(self.file, self.line, self.message)

    def with_file(self, new_file: str) -> Finding:
        """Вернуть копию замечания с каноническим путём и пересчитанным ID."""
        return Finding(
            file=new_file,
            line=self.line,
            severity=self.severity,
            category=self.category,
            message=self.message,
            suggestion=self.suggestion,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "suggestion": self.suggestion,
        }
