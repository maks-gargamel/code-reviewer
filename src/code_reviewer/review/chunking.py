"""Планирование чанков для больших diff по компонентам графа зависимостей."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import EdgeStatus, RepoMap
from ..vcs.diff import DiffSet, FileDiff
from .prompt import estimate_tokens

DEFAULT_BUDGET = 8000
DEFAULT_MAX_CHUNKS = 3
PROMPT_OVERHEAD = 1200


@dataclass(slots=True)
class ChunkPlan:
    chunks: list[list[FileDiff]] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    total_tokens: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    def __len__(self) -> int:
        return len(self.chunks)

    @property
    def files(self) -> int:
        return sum(len(chunk) for chunk in self.chunks)


def file_tokens(item: FileDiff) -> int:
    return estimate_tokens(item.patch)


def plan_chunks(
    diffset: DiffSet,
    repo_map: RepoMap,
    *,
    budget: int = DEFAULT_BUDGET,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> ChunkPlan:
    """Разложить изменённые файлы по чанкам в рамках токенового бюджета."""
    if not diffset.files:
        return ChunkPlan()

    total = sum(file_tokens(item) for item in diffset.files) + PROMPT_OVERHEAD
    if total <= budget or len(diffset.files) == 1:
        return ChunkPlan(chunks=[list(diffset.files)], total_tokens=total)

    limit = max(1, budget - PROMPT_OVERHEAD)
    groups = _components(diffset.files, repo_map)

    # Если связная компонента превышает бюджет целиком и содержит >1 файла,
    # дробим её по отдельным файлам, чтобы каждый чанк помещался в бюджет.
    units: list[list[FileDiff]] = []
    for group in groups:
        group_tokens = sum(file_tokens(item) for item in group)
        if len(group) > 1 and group_tokens > limit:
            for item in sorted(group, key=lambda f: (-file_tokens(f), f.path)):
                units.append([item])
        else:
            units.append(sorted(group, key=lambda f: f.path))

    sized = sorted(
        ((sum(file_tokens(item) for item in unit), unit) for unit in units),
        key=lambda pair: (-pair[0], pair[1][0].path),
    )

    chunks: list[list[FileDiff]] = []
    current: list[FileDiff] = []
    current_tokens = 0
    omitted: list[str] = []

    for tokens, unit in sized:
        if len(chunks) >= max_chunks:
            omitted.extend(item.path for item in unit)
            continue
        if current and current_tokens + tokens > limit:
            chunks.append(sorted(current, key=lambda item: item.path))
            current, current_tokens = [], 0
            if len(chunks) >= max_chunks:
                omitted.extend(item.path for item in unit)
                continue
        current.extend(unit)
        current_tokens += tokens

    if current:
        chunks.append(sorted(current, key=lambda item: item.path))

    if len(chunks) > max_chunks:
        for chunk in chunks[max_chunks:]:
            omitted.extend(item.path for item in chunk)
        chunks = chunks[:max_chunks]

    return ChunkPlan(
        chunks=chunks,
        omitted=sorted(dict.fromkeys(omitted)),
        total_tokens=total,
    )


def _components(files: list[FileDiff], repo_map: RepoMap) -> list[list[FileDiff]]:
    by_path = {item.path: item for item in files}
    parent = {path: path for path in by_path}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[left_root] = right_root

    for edge in repo_map.edges:
        if edge.status != EdgeStatus.INTERNAL or edge.dst is None:
            continue
        if edge.src in by_path and edge.dst in by_path:
            union(edge.src, edge.dst)

    grouped: dict[str, list[FileDiff]] = {}
    for path, item in by_path.items():
        grouped.setdefault(find(path), []).append(item)
    return list(grouped.values())
