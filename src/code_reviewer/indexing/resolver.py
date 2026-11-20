"""Разрешение импортов в рёбра графа зависимостей (internal / external / unresolved)."""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import EdgeStatus, FileInfo, GraphEdge, ImportRef
from .parsers.treesitter import PATH_SUFFIXES, resolve_relative_file

PY_SUFFIXES = (".py", ".pyi", ".pyw")
_C_CPP_SUFFIXES = (".h", ".hpp", ".hh", ".c", ".cpp", ".cc", ".cxx")
_RELATIVE_CFG: dict[str, tuple[tuple[str, ...], str]] = {
    "javascript": (PATH_SUFFIXES, "index"),
    "typescript": (PATH_SUFFIXES, "index"),
    "tsx": (PATH_SUFFIXES, "index"),
    "rust": ((".rs",), "mod"),
    "ruby": ((".rb",), ""),
    "php": ((".php",), ""),
    "lua": ((".lua",), "init"),
    "dart": ((".dart",), ""),
}
_TEST_DIR_MARKERS = frozenset({"test", "tests", "__tests__", "spec", "specs"})


def _is_test_path(path: str) -> bool:
    lower = path.lower()
    parts = lower.split("/")
    if any(part in _TEST_DIR_MARKERS for part in parts[:-1]):
        return True
    stem = parts[-1]
    return (
        stem.startswith("test_")
        or "_test." in stem
        or ".test." in stem
        or ".spec." in stem
        or "_spec." in stem
    )


def _rank_candidates(candidates: Iterable[str], src_path: str) -> list[str]:
    src_top = src_path.split("/", 1)[0]
    unique = [p for p in dict.fromkeys(candidates) if p != src_path]
    return sorted(
        unique,
        key=lambda p: (
            1 if _is_test_path(p) else 0,
            0 if p.split("/", 1)[0] == src_top else 1,
            p.count("/"),
            len(p),
            p,
        ),
    )


def _read_go_module(root: Path | None) -> str:
    if root is None:
        return ""
    go_mod = root / "go.mod"
    if not go_mod.is_file():
        return ""
    try:
        text = go_mod.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if line.startswith("module "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].strip("\"'")
    return ""


def _read_dart_package(root: Path | None) -> str:
    if root is None:
        return ""
    pubspec = root / "pubspec.yaml"
    if not pubspec.is_file():
        return ""
    try:
        text = pubspec.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


@dataclass(slots=True)
class _ResolverIndex:
    known: set[str]
    suffix_files: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    lower_suffix_files: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    pkg_dir_files: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    symbol_names: dict[str, set[str]] = field(default_factory=dict)
    go_module: str = ""
    go_dirs: dict[str, list[str]] = field(default_factory=dict)
    dart_package: str = ""
    rust_crates: list[str] = field(default_factory=list)
    csharp_dirs: dict[str, list[str]] = field(default_factory=dict)
    c_header_suffixes: dict[str, list[str]] = field(default_factory=dict)


def _index_suffixes(path: str) -> Iterable[str]:
    parts = path.split("/")
    for idx in range(len(parts)):
        yield "/".join(parts[idx:])


def _build_resolver_index(files: dict[str, FileInfo], root: Path | None) -> _ResolverIndex:
    idx = _ResolverIndex(
        known=set(files),
        go_module=_read_go_module(root),
        dart_package=_read_dart_package(root),
    )
    rust_roots: set[str] = set()

    for path, info in sorted(files.items()):
        lang = info.lang
        idx.symbol_names[path] = {sym.name for sym in info.symbols}
        parent_dir = path.rsplit("/", 1)[0] if "/" in path else ""

        if lang in ("java", "kotlin", "scala", "php", "ruby", "lua", "csharp"):
            for suffix in _index_suffixes(path):
                idx.suffix_files.setdefault((lang, suffix), []).append(path)
                idx.lower_suffix_files.setdefault((lang, suffix.lower()), []).append(path)
            if lang in ("kotlin", "scala") and parent_dir:
                for dir_suffix in _index_suffixes(parent_dir):
                    idx.pkg_dir_files.setdefault((lang, dir_suffix), []).append(path)
            if lang == "csharp" and parent_dir:
                idx.csharp_dirs.setdefault(parent_dir.lower(), []).append(path)
                for dir_suffix in _index_suffixes(parent_dir):
                    idx.csharp_dirs.setdefault(dir_suffix.lower(), []).append(path)
        elif lang == "go":
            idx.go_dirs.setdefault(parent_dir, []).append(path)
        elif lang == "rust":
            filename = path.rsplit("/", 1)[-1]
            if (
                filename in ("lib.rs", "main.rs")
                or parent_dir.endswith("/src")
                or parent_dir == "src"
            ):
                rust_roots.add(parent_dir)
        elif lang in ("c", "cpp"):
            for suffix in _index_suffixes(path):
                idx.c_header_suffixes.setdefault(suffix, []).append(path)

    for crate_root in sorted(rust_roots, key=lambda r: (-r.count("/"), -len(r), r)):
        idx.rust_crates.append(crate_root)
    if "" not in rust_roots:
        idx.rust_crates.append("")
    return idx


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


def _resolve_c_include(
    path: str,
    module: str,
    idx: _ResolverIndex,
    *,
    quoted: bool,
) -> str | None:
    norm = posixpath.normpath(module.lstrip("./")) if module.startswith("./") else module
    norm = posixpath.normpath(norm)
    if norm.startswith(".."):
        return None

    base_dir = path.rsplit("/", 1)[0] if "/" in path else ""
    candidates: list[str] = []
    if quoted:
        if base_dir:
            candidates.append(posixpath.normpath(posixpath.join(base_dir, module)))
        candidates.append(norm)
    if quoted or "/" in norm:
        for root_dir in ("include", "inc", "src"):
            candidates.append(posixpath.normpath(posixpath.join(root_dir, norm)))

    for candidate in candidates:
        if not candidate.startswith("..") and candidate != path and candidate in idx.known:
            return candidate

    if quoted:
        matches = _rank_candidates(idx.c_header_suffixes.get(norm, ()), path)
        if len(matches) == 1:
            return matches[0]
        if matches and "/" in norm:
            return matches[0]
    return None


def _resolve_go_import(path: str, module: str, idx: _ResolverIndex) -> list[str]:
    if not idx.go_module:
        return []
    if module == idx.go_module:
        rel_dir = ""
    elif module.startswith(idx.go_module + "/"):
        rel_dir = module[len(idx.go_module) + 1 :]
    else:
        return []

    pkg_files = idx.go_dirs.get(rel_dir, [])
    if not pkg_files:
        return []
    non_test = [p for p in pkg_files if p != path and not p.endswith("_test.go")]
    chosen = non_test or [p for p in pkg_files if p != path]
    return chosen[:12]


def _resolve_rust_import(path: str, import_ref: ImportRef, idx: _ResolverIndex) -> str | None:
    mod = import_ref.module.replace("::", ".").strip(".")
    if not mod:
        return None

    def probe_in_dir(base_dir: str, parts: list[str]) -> str | None:
        prefix = f"{base_dir}/" if base_dir else ""
        if not parts:
            for root_file in ("lib.rs", "main.rs", "mod.rs"):
                cand = prefix + root_file
                if cand in idx.known and cand != path:
                    return cand
            return None
        for size in range(len(parts), 0, -1):
            sub = "/".join(parts[:size])
            for cand in (f"{prefix}{sub}.rs", f"{prefix}{sub}/mod.rs"):
                if cand in idx.known and cand != path:
                    return cand
        return None

    if mod == "crate" or mod.startswith("crate."):
        parts = [p for p in mod[5:].strip(".").split(".") if p and p != "*"]
        crate_dir = next(
            (cr for cr in idx.rust_crates if not cr or path.startswith(cr + "/")),
            "",
        )
        return probe_in_dir(crate_dir, parts)

    if mod.startswith(("super.", "self.")) or mod in ("super", "self"):
        cur_dir = path.rsplit("/", 1)[0] if "/" in path else ""
        filename = path.rsplit("/", 1)[-1]
        is_mod_root = filename in ("mod.rs", "lib.rs", "main.rs")
        rest = mod
        if rest == "super" or rest.startswith("super."):
            if not is_mod_root:
                # For `a/b.rs`, the first `super::` refers to sibling files inside `a/` (`cur_dir`).
                rest = rest[5:].lstrip(".")
            while rest == "super" or rest.startswith("super."):
                if not cur_dir:
                    return None
                cur_dir = cur_dir.rsplit("/", 1)[0] if "/" in cur_dir else ""
                rest = rest[5:].lstrip(".")
        elif (rest == "self" or rest.startswith("self.")) and not is_mod_root:
            sub = filename[:-3] if filename.endswith(".rs") else filename
            cur_dir = f"{cur_dir}/{sub}" if cur_dir else sub
            rest = rest[4:].lstrip(".")
        elif rest == "self" or rest.startswith("self."):
            rest = rest[4:].lstrip(".")
        parts = [p for p in rest.split(".") if p and p != "*"]
        return probe_in_dir(cur_dir, parts)

    return None


def _pick_suffix(
    lang: str,
    suffix: str,
    path: str,
    idx: _ResolverIndex,
    *,
    case_insensitive: bool = False,
) -> str | None:
    hits = idx.suffix_files.get((lang, suffix), [])
    if not hits and case_insensitive:
        hits = idx.lower_suffix_files.get((lang, suffix.lower()), [])
    ranked = _rank_candidates(hits, path)
    return ranked[0] if ranked else None


def _resolve_jvm_import(
    path: str,
    lang: str,
    import_ref: ImportRef,
    ext: str,
    idx: _ResolverIndex,
) -> list[str]:
    candidates = [import_ref.module] if import_ref.module else []
    for name in import_ref.names:
        if name and name != "_":
            candidates.append(f"{import_ref.module}.{name}")

    targets: list[str] = []
    for cand in candidates:
        parts = [p for p in cand.split(".") if p and p not in ("*", "_")]
        if not parts:
            continue
        direct = "/".join(parts) + ext
        hit = _pick_suffix(lang, direct, path, idx)
        if hit is not None:
            targets.append(hit)
            continue
        if len(parts) >= 2:
            parent_file = "/".join(parts[:-1]) + ext
            hit = _pick_suffix(lang, parent_file, path, idx)
            if hit is not None:
                targets.append(hit)
                continue
            pkg_dir = "/".join(parts[:-1])
            sym_name = parts[-1]
            dir_hits = _rank_candidates(idx.pkg_dir_files.get((lang, pkg_dir), ()), path)
            matching_sym = [p for p in dir_hits if sym_name in idx.symbol_names.get(p, ())]
            if matching_sym:
                targets.append(matching_sym[0])
    return list(dict.fromkeys(targets))


def _resolve_csharp_import(path: str, namespace: str, idx: _ResolverIndex) -> list[str]:
    cleaned = namespace.strip()
    if not cleaned or cleaned == "System" or cleaned.startswith(("System.", "Microsoft.")):
        return []
    slash = cleaned.replace(".", "/")
    direct = _pick_suffix("csharp", f"{slash}.cs", path, idx, case_insensitive=True)
    if direct is not None:
        return [direct]

    dir_hits = idx.csharp_dirs.get(slash.lower(), [])
    if not dir_hits and "/" in slash:
        # e.g. `using Company.Demo.Models;` when repo root omits `Company/`
        parts = slash.split("/")
        for cut in range(1, len(parts)):
            sub = "/".join(parts[cut:])
            dir_hits = idx.csharp_dirs.get(sub.lower(), [])
            if dir_hits:
                break
    ranked = _rank_candidates(dir_hits, path)
    return ranked[:8]


def _resolve_non_python_one(
    path: str,
    info: FileInfo,
    import_ref: ImportRef,
    idx: _ResolverIndex,
) -> list[GraphEdge]:
    lang = info.lang

    if lang in ("c", "cpp") and import_ref.raw.lstrip().startswith("#"):
        raw = import_ref.raw.lstrip()
        quoted = '"' in raw
        header: str | None = None
        if import_ref.level:
            header = resolve_relative_file(
                path,
                import_ref.level,
                import_ref.module,
                idx.known,
                suffixes=_C_CPP_SUFFIXES,
                index_stem="",
            )
        if header is None:
            header = _resolve_c_include(path, import_ref.module, idx, quoted=quoted)
        if header is not None:
            return [
                GraphEdge(
                    src=path,
                    dst=header,
                    name=import_ref.module or header,
                    kind="include",
                    status=EdgeStatus.INTERNAL,
                    line=import_ref.line,
                )
            ]
        return [
            GraphEdge(
                src=path,
                dst=None,
                name=(import_ref.dotted if import_ref.level else import_ref.module)
                or import_ref.raw,
                kind="include",
                status=EdgeStatus.EXTERNAL if not quoted else EdgeStatus.UNRESOLVED,
                line=import_ref.line,
            )
        ]

    if import_ref.level:
        suffixes, index_stem = _RELATIVE_CFG.get(lang, (PATH_SUFFIXES, "index"))
        target = resolve_relative_file(
            path,
            import_ref.level,
            import_ref.module,
            idx.known,
            suffixes=suffixes,
            index_stem=index_stem,
        )
        if target is not None and target != path:
            return [
                GraphEdge(
                    src=path,
                    dst=target,
                    name=import_ref.module,
                    kind="from" if import_ref.is_from else "import",
                    status=EdgeStatus.INTERNAL,
                    line=import_ref.line,
                )
            ]
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

    targets: list[str] = []
    if lang == "go":
        targets = _resolve_go_import(path, import_ref.module, idx)
    elif lang == "rust":
        rust_target = _resolve_rust_import(path, import_ref, idx)
        if rust_target is not None:
            targets = [rust_target]
    elif lang == "java":
        targets = _resolve_jvm_import(path, "java", import_ref, ".java", idx)
    elif lang == "kotlin":
        targets = _resolve_jvm_import(path, "kotlin", import_ref, ".kt", idx)
    elif lang == "scala":
        targets = _resolve_jvm_import(path, "scala", import_ref, ".scala", idx)
    elif lang == "php":
        rel = import_ref.module.replace("\\", ".").strip(".")
        if rel:
            hit = _pick_suffix(
                "php",
                rel.replace(".", "/") + ".php",
                path,
                idx,
                case_insensitive=True,
            )
            if hit is not None:
                targets = [hit]
    elif lang == "ruby":
        mod = import_ref.module.strip().removesuffix(".rb").lstrip("/")
        if mod:
            hit = _pick_suffix("ruby", f"{mod}.rb", path, idx)
            if hit is not None:
                targets = [hit]
    elif lang == "lua":
        mod = import_ref.module.strip().removesuffix(".lua").replace(".", "/")
        if mod:
            hit = _pick_suffix("lua", f"{mod}.lua", path, idx) or _pick_suffix(
                "lua", f"{mod}/init.lua", path, idx
            )
            if hit is not None:
                targets = [hit]
    elif lang == "dart":
        mod = import_ref.module.strip()
        if mod.startswith("package:"):
            pkg, _, rel = mod[len("package:") :].partition("/")
            if (not idx.dart_package or pkg == idx.dart_package) and f"lib/{rel}" in idx.known:
                targets = [f"lib/{rel}"]
    elif lang == "csharp":
        targets = _resolve_csharp_import(path, import_ref.module, idx)

    if targets:
        return [
            GraphEdge(
                src=path,
                dst=dst,
                name=import_ref.module or dst,
                kind="from" if import_ref.is_from else "import",
                status=EdgeStatus.INTERNAL,
                line=import_ref.line,
            )
            for dst in targets
        ]

    return [
        GraphEdge(
            src=path,
            dst=None,
            name=import_ref.module or import_ref.raw,
            kind="from" if import_ref.is_from else "import",
            status=EdgeStatus.EXTERNAL,
            line=import_ref.line,
        )
    ]


def resolve_edges(
    files: dict[str, FileInfo],
    source_roots: Sequence[str],
    *,
    root: Path | None = None,
) -> list[GraphEdge]:
    """Построить список уникальных рёбер графа зависимостей."""
    index = module_index(files, source_roots)
    canonical = module_by_path(files, source_roots)
    idx = _build_resolver_index(files, root)
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
            for edge in _resolve_non_python_one(path, info, import_ref, idx):
                add(edge)

    return edges
