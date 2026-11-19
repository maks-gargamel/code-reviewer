"""Разрешение импортов в рёбра графа зависимостей (internal / external / unresolved)."""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Sequence

from ..core.models import EdgeStatus, FileInfo, GraphEdge, ImportRef
from .parsers.treesitter import resolve_relative_file

PY_SUFFIXES = (".py", ".pyi", ".pyw")


def _iter_modules(
    files: dict[str, FileInfo],
    source_roots: Sequence[str],
) -> Iterable[tuple[str, str]]:
    for root in reversed(source_roots):
        prefix = f"{root}/" if root else ""
        for path, info in files.items():
            if info.lang != "python":
                continue
            if prefix:
                if not path.startswith(prefix):
                    continue
                relative = path[len(prefix) :]
            else:
                relative = path
            module = _module_name(relative)
            if module:
                yield module, path


def module_index(files: dict[str, FileInfo], source_roots: Sequence[str]) -> dict[str, str]:
    """Карта `имя_модуля -> путь_к_файлу` для flat и src-layout."""
    index: dict[str, str] = {}
    for module, path in _iter_modules(files, source_roots):
        index.setdefault(module, path)
    return index


def module_by_path(
    files: dict[str, FileInfo],
    source_roots: Sequence[str],
) -> dict[str, str]:
    """Каноническое имя модуля для каждого Python-файла (от самого глубокого source root)."""
    result: dict[str, str] = {}
    for module, path in _iter_modules(files, source_roots):
        result.setdefault(path, module)
    return result


def _module_name(relative_path: str) -> str:
    for suffix in PY_SUFFIXES:
        if relative_path.endswith(suffix):
            relative_path = relative_path[: -len(suffix)]
            break
    else:
        return ""
    parts = relative_path.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(part for part in parts if part)


def _is_package_init(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("__init__.") and name.endswith(PY_SUFFIXES)


def _relative_target(
    path: str,
    level: int,
    module: str,
    canonical: dict[str, str],
) -> str | None:
    if level <= 0:
        return module

    current = canonical.get(path)
    if current:
        parts = current.split(".")
        base = parts if _is_package_init(path) else parts[:-1]
    else:
        base = path.split("/")[:-1]

    drop = level - 1
    if drop > len(base):
        return None
    kept = base[: len(base) - drop] if drop else base
    if not kept:
        return None
    joined = kept + ([module] if module else [])
    return ".".join(joined)


def _prefixes(module: str) -> Iterable[str]:
    parts = module.split(".") if module else []
    for size in range(len(parts), 0, -1):
        yield ".".join(parts[:size])


def _resolve_python_one(
    path: str,
    import_ref: ImportRef,
    index: dict[str, str],
    canonical: dict[str, str],
) -> list[GraphEdge]:
    if import_ref.level:
        target = _relative_target(path, import_ref.level, import_ref.module, canonical)
        if target is None:
            return [
                GraphEdge(
                    src=path,
                    dst=None,
                    name=import_ref.dotted,
                    kind="from" if import_ref.is_from else "import",
                    status=EdgeStatus.UNRESOLVED,
                    line=import_ref.line,
                )
            ]
    else:
        target = import_ref.module

    edges: list[GraphEdge] = []

    if import_ref.is_from:
        for name in import_ref.names:
            if name == "*":
                continue
            candidate = f"{target}.{name}" if target else name
            resolved = index.get(candidate)
            if resolved is not None:
                edges.append(
                    GraphEdge(
                        src=path,
                        dst=resolved,
                        name=candidate,
                        kind="from",
                        status=EdgeStatus.INTERNAL,
                        line=import_ref.line,
                    )
                )
        if not edges and target:
            resolved = index.get(target)
            if resolved is not None:
                edges.append(
                    GraphEdge(
                        src=path,
                        dst=resolved,
                        name=target,
                        kind="from",
                        status=EdgeStatus.INTERNAL,
                        line=import_ref.line,
                    )
                )
    elif target:
        for candidate in _prefixes(target):
            resolved = index.get(candidate)
            if resolved is not None:
                edges.append(
                    GraphEdge(
                        src=path,
                        dst=resolved,
                        name=candidate,
                        kind="import",
                        status=EdgeStatus.INTERNAL,
                        line=import_ref.line,
                    )
                )
                break

    if edges:
        return edges

    return [
        GraphEdge(
            src=path,
            dst=None,
            name=import_ref.dotted or target or import_ref.raw,
            kind="from" if import_ref.is_from else "import",
            status=EdgeStatus.EXTERNAL,
            line=import_ref.line,
        )
    ]


def _resolve_c_include(path: str, module: str, known: set[str]) -> str | None:
    base_dir = path.rsplit("/", 1)[0] if "/" in path else ""
    candidates = []
    if base_dir:
        candidates.append(posixpath.normpath(posixpath.join(base_dir, module)))
    candidates.append(posixpath.normpath(module))
    for candidate in candidates:
        if not candidate.startswith("..") and candidate in known:
            return candidate
    return None


def resolve_edges(
    files: dict[str, FileInfo],
    source_roots: Sequence[str],
) -> list[GraphEdge]:
    """Построить список уникальных рёбер графа зависимостей."""
    index = module_index(files, source_roots)
    canonical = module_by_path(files, source_roots)
    known = set(files)
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str | None, str, str]] = set()

    def add(edge: GraphEdge) -> None:
        key = (edge.src, edge.dst, edge.name, edge.status)
        if key in seen:
            return
        seen.add(key)
        edges.append(edge)

    for path in sorted(files):
        info = files[path]

        if info.lang == "python":
            for import_ref in info.imports:
                for edge in _resolve_python_one(path, import_ref, index, canonical):
                    add(edge)
            continue

        for import_ref in info.imports:
            if import_ref.level:
                target = resolve_relative_file(
                    path, import_ref.level, import_ref.module, known
                )
                if target is not None:
                    add(
                        GraphEdge(
                            src=path,
                            dst=target,
                            name=import_ref.module,
                            kind="from" if import_ref.is_from else "import",
                            status=EdgeStatus.INTERNAL,
                            line=import_ref.line,
                        )
                    )
                else:
                    add(
                        GraphEdge(
                            src=path,
                            dst=None,
                            name=import_ref.dotted,
                            kind="from" if import_ref.is_from else "import",
                            status=EdgeStatus.UNRESOLVED,
                            line=import_ref.line,
                        )
                    )
                continue

            if (
                info.lang in {"c", "cpp"}
                and import_ref.raw.lstrip().startswith("#")
                and '"' in import_ref.raw
            ):
                header = _resolve_c_include(path, import_ref.module, known)
                if header is not None:
                    add(
                        GraphEdge(
                            src=path,
                            dst=header,
                            name=import_ref.module,
                            kind="include",
                            status=EdgeStatus.INTERNAL,
                            line=import_ref.line,
                        )
                    )
                    continue

            add(
                GraphEdge(
                    src=path,
                    dst=None,
                    name=import_ref.module or import_ref.raw,
                    kind="from" if import_ref.is_from else "import",
                    status=EdgeStatus.EXTERNAL,
                    line=import_ref.line,
                )
            )

    return edges
