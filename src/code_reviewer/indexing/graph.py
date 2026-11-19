"""Анализ графа зависимостей: циклы, поиск символов и экспорт в DOT."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ..core.models import EdgeStatus, RepoMap


@dataclass(slots=True)
class Cycle:
    files: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class SymbolHit:
    file: str
    line: int
    name: str
    kind: str
    signature: str
    doc: str | None = None


def internal_edges(repo_map: RepoMap) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {path: set() for path in repo_map.files}
    for edge in repo_map.edges:
        if edge.status != EdgeStatus.INTERNAL or edge.dst is None:
            continue
        adjacency.setdefault(edge.src, set()).add(edge.dst)
    return adjacency


def find_cycles(repo_map: RepoMap) -> list[Cycle]:
    """Найти циклические зависимости (компоненты сильной связности Косараджу)."""
    adjacency = internal_edges(repo_map)
    cycles: list[Cycle] = []

    for component in _strongly_connected(adjacency):
        members = set(component)
        if len(members) == 1:
            only = component[0]
            if only not in adjacency.get(only, set()):
                continue
        pairs = sorted(
            (src, dst)
            for src in members
            for dst in adjacency.get(src, set())
            if dst in members
        )
        cycles.append(Cycle(files=sorted(members), edges=pairs))

    cycles.sort(key=lambda cycle: (-len(cycle.files), cycle.files[0]))
    return cycles


def search_symbols(
    repo_map: RepoMap,
    query: str,
    *,
    limit: int = 50,
) -> list[SymbolHit]:
    needle = query.strip().lower()
    if not needle or limit <= 0:
        return []

    hits: list[SymbolHit] = []
    for path in sorted(repo_map.files):
        info = repo_map.files[path]
        for symbol in info.symbols:
            haystack = f"{symbol.name} {symbol.signature}".lower()
            if needle not in haystack:
                continue
            hits.append(
                SymbolHit(
                    file=path,
                    line=symbol.line,
                    name=symbol.name,
                    kind=symbol.kind,
                    signature=symbol.signature or symbol.name,
                    doc=symbol.doc,
                )
            )
            if len(hits) >= limit:
                return hits
    return hits


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def to_dot(
    repo_map: RepoMap,
    *,
    focus: str | None = None,
    depth: int = 1,
) -> str:
    """Экспорт графа в формат Graphviz DOT."""
    nodes = (
        {focus} | repo_map.related(focus, depth=max(1, depth))
        if focus
        else set(repo_map.files)
    )

    lines = [
        "digraph repo {",
        '  rankdir="LR";',
        '  node [shape="box", fontsize=10];',
    ]
    for node in sorted(nodes):
        lines.append(f'  "{_dot_escape(node)}";')

    seen: set[tuple[str, str]] = set()
    for edge in repo_map.edges:
        if edge.status != EdgeStatus.INTERNAL or edge.dst is None:
            continue
        if edge.src not in nodes or edge.dst not in nodes:
            continue
        pair = (edge.src, edge.dst)
        if pair in seen:
            continue
        seen.add(pair)
        lines.append(f'  "{_dot_escape(edge.src)}" -> "{_dot_escape(edge.dst)}";')

    lines.append("}")
    return "\n".join(lines)


def _strongly_connected(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Итеративный алгоритм Косараджу без рекурсии."""
    visited: set[str] = set()
    order: list[str] = []

    for start in sorted(adjacency):
        if start in visited:
            continue
        visited.add(start)
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(sorted(adjacency[start])))]
        while stack:
            node, iterator = stack[-1]
            for neighbour in iterator:
                if neighbour in visited:
                    continue
                visited.add(neighbour)
                stack.append((neighbour, iter(sorted(adjacency.get(neighbour, set())))))
                break
            else:
                order.append(node)
                stack.pop()

    reversed_graph: dict[str, set[str]] = {node: set() for node in adjacency}
    for src, targets in adjacency.items():
        for dst in targets:
            reversed_graph.setdefault(dst, set()).add(src)

    visited.clear()
    components: list[list[str]] = []
    for node in reversed(order):
        if node in visited:
            continue
        component: list[str] = []
        dfs_stack = [node]
        visited.add(node)
        while dfs_stack:
            current = dfs_stack.pop()
            component.append(current)
            for neighbour in reversed_graph.get(current, set()):
                if neighbour not in visited:
                    visited.add(neighbour)
                    dfs_stack.append(neighbour)
        components.append(component)

    return components
