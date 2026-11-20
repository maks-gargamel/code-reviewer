"""Tier B парсер для JS/TS/Go/Rust/Java/C/C++ и др. на базе tree-sitter-language-pack."""

from __future__ import annotations

import posixpath
import re
from typing import Any

from ...core.models import FileInfo, ImportRef, Symbol, SymbolKind
from .base import LanguageAdapter, ParseOptions, collapse, leading_comment
from .plain import PlainAdapter
from .ts_ast import augment_ast, relative_level, strip_relative

MAX_DOC = 240
MAX_SIGNATURE = 300

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".scala": "scala",
    ".swift": "swift",
    ".lua": "lua",
    ".dart": "dart",
}

DISPLAY_LANGUAGE: dict[str, str] = {
    "tsx": "tsx",
}

KIND_MAP: dict[str, str] = {
    "class": SymbolKind.CLASS,
    "struct": SymbolKind.CLASS,
    "interface": SymbolKind.CLASS,
    "enum": SymbolKind.CLASS,
    "trait": SymbolKind.CLASS,
    "impl": SymbolKind.CLASS,
    "type": SymbolKind.CLASS,
    "function": SymbolKind.FUNCTION,
    "method": SymbolKind.METHOD,
    "constructor": SymbolKind.METHOD,
    "arrow_function": SymbolKind.FUNCTION,
    "const": SymbolKind.CONSTANT,
    "constant": SymbolKind.CONSTANT,
    "variable": SymbolKind.CONSTANT,
    "property": SymbolKind.CONSTANT,
}

WRAPPER_KINDS = {"module", "namespace", "package"}

PATH_SUFFIXES = (
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".vue",
    ".svelte",
)

_FROM_RE = re.compile(r"""\bfrom\s+['"]([^'"]+)['"]""")
_REQUIRE_RE = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_QUOTED_RE = re.compile(r"""['"]([^'"]+)['"]""")
_INCLUDE_RE = re.compile(r"""^\s*#\s*include\s+[<"]([^>"]+)""")

_downloaded_cache: set[str] | None = None


def _pack() -> Any | None:
    try:
        import tree_sitter_language_pack as pack
    except Exception:  # noqa: BLE001 - пакет опциональный
        return None
    return pack


def installed() -> bool:
    return _pack() is not None


def language_for(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1].lower()
    dot = name.rfind(".")
    if dot == -1:
        return None
    return LANGUAGE_BY_EXTENSION.get(name[dot:])


def display_name(language: str) -> str:
    return DISPLAY_LANGUAGE.get(language, language)


def invalidate_grammar_cache() -> None:
    global _downloaded_cache
    _downloaded_cache = None


def _downloaded_set(pack: Any) -> set[str]:
    global _downloaded_cache
    if _downloaded_cache is None:
        try:
            _downloaded_cache = set(pack.downloaded_languages())
        except Exception:  # noqa: BLE001
            _downloaded_cache = set()
    return _downloaded_cache


def downloaded_grammars() -> list[str]:
    pack = _pack()
    if pack is None:
        return []
    invalidate_grammar_cache()
    return sorted(_downloaded_set(pack))


def ensure_grammar(language: str, *, fetch: bool = True) -> bool:
    """Проверить наличие грамматики; при `fetch=False` никогда не обращаться к сети."""
    pack = _pack()
    if pack is None:
        return False
    if language in _downloaded_set(pack):
        return True
    if not fetch:
        return False
    try:
        pack.download([language])
        invalidate_grammar_cache()
        return True
    except Exception:  # noqa: BLE001 - сеть или диск могут подвести
        return False


def prefetch_grammars(paths: list[str]) -> list[str]:
    """Дозагрузить отсутствующие грамматики для набора путей."""
    pack = _pack()
    if pack is None:
        return []
    before = set(_downloaded_set(pack))
    languages = sorted({lang for path in paths if (lang := language_for(path)) is not None})
    downloaded: list[str] = []
    for language in languages:
        if ensure_grammar(language, fetch=True) and language not in before:
            downloaded.append(language)
    return downloaded


class TreeSitterAdapter(LanguageAdapter):
    name = "treesitter"
    extensions = tuple(LANGUAGE_BY_EXTENSION)

    def matches(self, path: str) -> bool:
        return language_for(path) is not None and installed()

    def extract(
        self,
        path: str,
        source: str,
        *,
        size: int,
        mtime: float,
        digest: str,
        options: ParseOptions | None = None,
    ) -> FileInfo:
        fallback = PlainAdapter().extract(
            path, source, size=size, mtime=mtime, digest=digest, options=options
        )
        language = language_for(path)
        pack = _pack()
        if pack is None or language is None:
            return fallback

        fetch = True if options is None else options.fetch_grammars
        if not ensure_grammar(language, fetch=fetch):
            fallback.parse_errors.append(f"грамматика {language} не загружена")
            fallback.lang = display_name(language)
            return fallback

        try:
            result = pack.process(
                source,
                pack.ProcessConfig(language=language, structure=True, imports=True),
            )
        except Exception as exc:  # noqa: BLE001 - деградируем до Tier D метаданных
            fallback.parse_errors.append(f"tree-sitter: {exc}")
            fallback.lang = display_name(language)
            return fallback

        metrics = getattr(result, "metrics", None)
        info = FileInfo(
            path=path,
            lang=display_name(language),
            size=size,
            mtime=mtime,
            loc=metrics.total_lines if metrics else fallback.loc,
            digest=digest,
            doc=leading_comment(source, ("/**", "//", "#", "/*", "*", "--")),
        )
        _walk(result.structure, None, info.symbols)
        info.imports = _convert_imports(result)
        augment_ast(source, language, pack, info.symbols, info.imports)
        return info


def _normalize_structure_kind(raw_kind: object) -> str:
    return str(raw_kind).split(".")[-1].strip().lower()


def _walk(items: Any, parent: str | None, out: list[Symbol]) -> None:
    for item in items:
        raw_kind = _normalize_structure_kind(getattr(item, "kind", ""))
        name = (getattr(item, "name", "") or "").strip()

        if raw_kind in WRAPPER_KINDS:
            _walk(item.children, parent, out)
            continue

        span = getattr(item, "span", None)
        out.append(
            Symbol(
                name=name or "<anonymous>",
                kind=KIND_MAP.get(raw_kind, SymbolKind.FUNCTION),
                line=span.start_line + 1 if span else 0,
                end_line=span.end_line + 1 if span else 0,
                signature=collapse(item.signature or name, MAX_SIGNATURE) or "",
                doc=collapse(_clean_doc(item.doc_comment), MAX_DOC),
                parent=parent,
            )
        )
        _walk(item.children, name or parent, out)


def _convert_imports(result: Any) -> list[ImportRef]:
    refs: list[ImportRef] = []
    for item in result.imports:
        raw = (getattr(item, "source", "") or "").strip()
        module = _module_of(raw)
        if not module:
            continue
        level = relative_level(module)
        relative = strip_relative(module) if level else module
        if not relative and not level:
            continue
        span = getattr(item, "span", None)
        line = span.start_line + 1 if span else 0
        refs.append(
            ImportRef(
                raw=raw,
                line=line,
                module=relative,
                names=[str(name) for name in getattr(item, "items", [])],
                level=level,
                is_from=True,
            )
        )
    return refs


def _module_of(raw: str) -> str:
    text = raw.strip().rstrip(";").strip()
    if not text:
        return ""

    match = _FROM_RE.search(text) or _REQUIRE_RE.search(text)
    if match:
        return match.group(1).strip()

    match = _INCLUDE_RE.match(text)
    if match:
        return match.group(1).strip()

    if text.startswith(("'", '"')):
        quoted = _QUOTED_RE.search(text)
        if quoted:
            return quoted.group(1).strip()

    lowered = text.lower()
    if lowered.startswith("use "):
        body = text[4:].split("{", 1)[0].rstrip(":;, ").strip()
        return body.replace("::", ".")
    if lowered.startswith(("import ", "using ", "package ", "from ")):
        body = text.split(None, 1)[1].strip()
        return body.split(" as ")[0].strip()
    return ""


def _clean_doc(text: str | None) -> str | None:
    if not text:
        return None
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for prefix in ("/**", "/*", "///", "//", "*/", "*"):
            if line.startswith(prefix):
                line = line[len(prefix) :].strip()
        if line.endswith("*/"):
            line = line[:-2].strip()
        if line:
            lines.append(line)
    return " ".join(lines) or None


def resolve_relative_file(
    path: str,
    level: int,
    module: str,
    known: set[str],
    *,
    suffixes: tuple[str, ...] = PATH_SUFFIXES,
    index_stem: str = "index",
) -> str | None:
    """Разрешить относительный импорт `./x` или `../x` в конкретный файл репозитория."""
    if not module:
        module = index_stem
    base = path.split("/")[:-1]
    drop = level - 1
    if drop > len(base):
        return None
    directory = base[: len(base) - drop] if drop else base
    joined = posixpath.join(*directory, module) if directory else module
    candidate = posixpath.normpath(joined)
    if candidate.startswith(".."):
        return None

    stem, extension = posixpath.splitext(candidate)
    variants = [candidate]
    if not extension:
        variants.extend(candidate + suffix for suffix in suffixes)
        if index_stem:
            variants.extend(
                posixpath.join(candidate, f"{index_stem}{suffix}") for suffix in suffixes
            )
    elif extension in suffixes:
        variants.extend(stem + suffix for suffix in suffixes)
    else:
        variants.extend(candidate + suffix for suffix in suffixes)

    for variant in variants:
        if variant in known:
            return variant
    return None
