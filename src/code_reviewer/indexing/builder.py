"""Построение и инкрементальное обновление карты репозитория."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import MAP_NAME, Config, service_dir
from ..core.models import MAP_VERSION, EdgeStatus, FileInfo, RepoMap, utc_now
from .discovery import discover_files
from .parsers import ParseOptions, adapter_for, prefetch_grammars
from .resolver import PY_SUFFIXES, resolve_edges

SOURCE_ROOT_CANDIDATES = ("src", "lib", "app", "python")
MTIME_TOLERANCE = 0.001


@dataclass(slots=True)
class BuildStats:
    files_total: int = 0
    files_parsed: int = 0
    files_reused: int = 0
    files_removed: int = 0
    skipped_binary: int = 0
    skipped_large: int = 0
    skipped_ignored: int = 0
    symbols: int = 0
    edges_internal: int = 0
    edges_external: int = 0
    edges_unresolved: int = 0
    parse_errors: int = 0
    grammars: list[str] = field(default_factory=list)
    duration: float = 0.0
    used_git: bool = False


@dataclass(slots=True)
class BuildResult:
    repo_map: RepoMap
    stats: BuildStats


def map_path(root: Path) -> Path:
    return service_dir(root) / MAP_NAME


def load_map(root: Path) -> RepoMap | None:
    path = map_path(root)
    if not path.is_file():
        return None
    try:
        return RepoMap.load(path)
    except (OSError, ValueError):
        return None


def detect_source_roots(paths: Iterable[str]) -> list[str]:
    """Определить source roots только по уже отфильтрованным путям репозитория."""
    py_paths = [p for p in paths if p.endswith(PY_SUFFIXES)]
    roots = [""]
    for name in SOURCE_ROOT_CANDIDATES:
        prefix = f"{name}/"
        if any(path.startswith(prefix) for path in py_paths):
            roots.append(name)
    return roots


def _read_source(full_path: Path) -> tuple[str, str]:
    data = full_path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace"), hashlib.sha256(data).hexdigest()


def _unchanged(info: FileInfo, size: int, mtime: float) -> bool:
    return (
        info.size == size
        and info.mtime > 0
        and abs(info.mtime - mtime) < MTIME_TOLERANCE
        and bool(info.digest)
    )


def _signature_fingerprint(info: FileInfo) -> tuple[str | None, tuple[str, ...]]:
    return info.doc, tuple(s.signature or s.name for s in info.symbols)


def build_map(
    root: Path,
    config: Config,
    *,
    previous: RepoMap | None = None,
    head: str | None = None,
    force: bool = False,
) -> BuildResult:
    """Построить или инкрементально обновить карту репозитория без обращений к LLM."""
    started = time.perf_counter()
    stats = BuildStats()

    discovery = discover_files(
        root,
        ignore=config.map.ignore,
        max_bytes=config.map.max_file_bytes,
    )
    stats.used_git = discovery.used_git
    stats.skipped_binary = discovery.skipped_binary
    stats.skipped_large = discovery.skipped_large
    stats.skipped_ignored = discovery.skipped_ignored

    if config.map.fetch_grammars:
        stats.grammars = prefetch_grammars([item.path for item in discovery.files])

    if previous is not None and (force or previous.version != MAP_VERSION):
        previous = None
    previous_files = previous.files if previous is not None else {}

    parse_options = ParseOptions(fetch_grammars=config.map.fetch_grammars)
    files: dict[str, FileInfo] = {}
    for item in discovery.files:
        cached = previous_files.get(item.path)
        if cached is not None and _unchanged(cached, item.size, item.mtime):
            files[item.path] = cached
            stats.files_reused += 1
            continue

        source, digest = _read_source(root / item.path)
        adapter = adapter_for(item.path)
        parsed = adapter.extract(
            item.path,
            source,
            size=item.size,
            mtime=item.mtime,
            digest=digest,
            options=parse_options,
        )
        if (
            cached is not None
            and cached.summary
            and _signature_fingerprint(cached) == _signature_fingerprint(parsed)
        ):
            parsed.summary = cached.summary
        files[item.path] = parsed
        stats.files_parsed += 1

    stats.files_total = len(files)
    stats.files_removed = len(set(previous_files) - set(files))

    source_roots = detect_source_roots(files)
    edges = resolve_edges(files, source_roots)

    stats.symbols = sum(len(info.symbols) for info in files.values())
    stats.parse_errors = sum(len(info.parse_errors) for info in files.values())
    stats.edges_internal = sum(1 for e in edges if e.status == EdgeStatus.INTERNAL)
    stats.edges_external = sum(1 for e in edges if e.status == EdgeStatus.EXTERNAL)
    stats.edges_unresolved = sum(1 for e in edges if e.status == EdgeStatus.UNRESOLVED)

    repo_map = RepoMap(
        files=files,
        edges=edges,
        generated_at=utc_now(),
        head=head,
        root=str(root),
        source_roots=source_roots,
    )
    repo_map.stats = {
        "files": stats.files_total,
        "symbols": stats.symbols,
        "edges_internal": stats.edges_internal,
        "edges_external": stats.edges_external,
        "edges_unresolved": stats.edges_unresolved,
        "by_lang": repo_map.by_lang(),
    }

    stats.duration = time.perf_counter() - started
    return BuildResult(repo_map=repo_map, stats=stats)


def stale_files(root: Path, config: Config, repo_map: RepoMap) -> list[str]:
    """Вернуть список файлов, изменившихся или удалённых после построения карты."""
    discovery = discover_files(
        root,
        ignore=config.map.ignore,
        max_bytes=config.map.max_file_bytes,
    )
    current = {item.path: item for item in discovery.files}

    if repo_map.version != MAP_VERSION:
        return sorted(current)

    stale: list[str] = []
    for path, item in current.items():
        known = repo_map.files.get(path)
        if known is None or not _unchanged(known, item.size, item.mtime):
            stale.append(path)
    for path in repo_map.files:
        if path not in current:
            stale.append(path)
    return sorted(set(stale))
