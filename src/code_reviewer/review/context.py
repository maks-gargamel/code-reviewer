"""Компактный контекст репозитория из карты (сигнатуры, docstring/сводки, README)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import Config
from ..core.models import FileInfo, RepoMap

MAX_README_CHARS = 900
MAX_CONTEXT_CHARS = 7000
MAX_SYMBOLS_PER_FILE = 20
README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md")


@dataclass(slots=True)
class ReviewContext:
    overview: str = ""
    related: str = ""
    files: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.overview and not self.related


def build_context(
    root: Path,
    config: Config,
    repo_map: RepoMap,
    changed_paths: list[str],
) -> ReviewContext:
    """Собрать краткое описание проекта и сигнатуры связанных по графу файлов."""
    overview = _overview(root, repo_map)

    depth = max(0, config.review.context_depth)
    changed_set = set(changed_paths)
    distance_by_path: dict[str, int] = {}

    if depth > 0:
        for path in changed_paths:
            if path not in repo_map.files:
                continue
            for hop in range(1, depth + 1):
                for neighbour in repo_map.related(path, depth=hop):
                    if neighbour in changed_set:
                        continue
                    prev = distance_by_path.get(neighbour)
                    if prev is None or hop < prev:
                        distance_by_path[neighbour] = hop

    ranked = sorted(
        distance_by_path,
        key=lambda p: (
            distance_by_path[p],
            -len(repo_map.files[p].symbols) if p in repo_map.files else 0,
            p,
        ),
    )

    blocks: list[str] = []
    rendered: list[str] = []
    used = 0
    for path in ranked:
        info = repo_map.files.get(path)
        if info is None:
            continue
        block = _render_file(path, info)
        if used + len(block) > MAX_CONTEXT_CHARS:
            continue
        blocks.append(block)
        rendered.append(path)
        used += len(block)

    return ReviewContext(overview=overview, related="\n\n".join(blocks), files=rendered)


def _overview(root: Path, repo_map: RepoMap) -> str:
    parts: list[str] = []
    for name in README_NAMES:
        path = root / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                break
            excerpt = text.strip()[:MAX_README_CHARS]
            if excerpt:
                parts.append(excerpt)
            break

    languages = ", ".join(f"{lang} {count}" for lang, count in repo_map.by_lang().items())
    stats = repo_map.stats
    parts.append(
        f"Файлов: {len(repo_map.files)}; символов: {stats.get('symbols', 0)}; "
        f"внутренних связей: {stats.get('edges_internal', 0)}; языки: {languages or '—'}."
    )
    return "\n".join(parts)


def _render_file(path: str, info: FileInfo) -> str:
    lines = [f"### {path} ({info.lang}, {info.loc} строк)"]
    if info.summary:
        lines.append(f"# {info.summary}")
    elif info.doc:
        lines.append(f"# {info.doc}")
    for symbol in info.symbols[:MAX_SYMBOLS_PER_FILE]:
        declaration = symbol.signature or symbol.name
        prefix = "  " if symbol.parent else ""
        lines.append(f"{prefix}- {declaration}".rstrip())
    if len(info.symbols) > MAX_SYMBOLS_PER_FILE:
        lines.append(f"  … ещё {len(info.symbols) - MAX_SYMBOLS_PER_FILE} символов")
    return "\n".join(lines)
