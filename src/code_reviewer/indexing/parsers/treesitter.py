"""Tier B парсер для JS/TS/Go/Rust/Java/C/C++ и др. на базе tree-sitter-language-pack."""

from __future__ import annotations

import posixpath
import re
from typing import Any

from ...core.models import FileInfo, ImportRef, Symbol, SymbolKind
from .base import LanguageAdapter, ParseOptions, collapse, leading_comment
from .plain import PlainAdapter

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
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".scala": "scala",
    ".swift": "swift",
    ".lua": "lua",
    ".dart": "dart",
}

DISPLAY_LANGUAGE: dict[str, str] = {
    "c_sharp": "csharp",
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

JS_LANGUAGES = frozenset({"javascript", "typescript", "tsx"})
_JS_FUNC_VALUE_NODES = frozenset({"arrow_function", "function_expression", "function"})

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
        if language in JS_LANGUAGES:
            _augment_js_ast(source, language, pack, info.symbols, info.imports)
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


def _augment_js_ast(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь CommonJS `require(...)` и функциональные присваивания через реальный AST."""
    try:
        parser = pack.get_parser(language)
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
    except Exception:  # noqa: BLE001
        symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
        return

    by_line: dict[int, Symbol] = {}
    for sym in symbols:
        by_line.setdefault(sym.line, sym)

    seen_imports: set[tuple[int, str]] = {
        (ref.line, ref.dotted) for ref in imports
    }

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        node_type = node.type

        if node_type == "call_expression":
            _maybe_add_commonjs_require(node, source_bytes, imports, seen_imports)
        elif node_type == "variable_declarator":
            _maybe_add_js_var_func(node, source_bytes, symbols, by_line)
        elif node_type == "assignment_expression":
            _maybe_add_js_prop_func(node, source_bytes, symbols, by_line)

        for child in reversed(node.children):
            stack.append(child)

    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
    imports.sort(key=lambda ref: ref.line)


def _node_text(node: Any, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _extract_js_string(node: Any, source_bytes: bytes) -> str | None:
    if node.type != "string":
        return None
    raw = _node_text(node, source_bytes).strip()
    if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
        return raw[1:-1].strip()
    return None


def _signature_prefix(node: Any, source_bytes: bytes) -> str:
    text = _node_text(node, source_bytes)
    head = text.split("{", 1)[0].strip()
    return collapse(head, MAX_SIGNATURE) or ""


def _maybe_add_commonjs_require(
    node: Any,
    source_bytes: bytes,
    imports: list[ImportRef],
    seen: set[tuple[int, str]],
) -> None:
    func = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if func is None or args is None or func.type != "identifier":
        return
    if _node_text(func, source_bytes) != "require":
        return
    named_args = args.named_children
    if len(named_args) != 1:
        return
    module = _extract_js_string(named_args[0], source_bytes)
    if not module:
        return

    line = node.start_point.row + 1
    level = _relative_level(module)
    relative = _strip_relative(module) if level else module
    dotted = "." * level + relative
    if (line, dotted) in seen:
        return
    seen.add((line, dotted))
    imports.append(
        ImportRef(
            raw=collapse(_node_text(node, source_bytes), 200) or module,
            line=line,
            module=relative,
            names=[],
            level=level,
            is_from=False,
        )
    )


def _record_js_symbol(
    *,
    name: str,
    parent: str | None,
    line: int,
    end_line: int,
    signature: str,
    symbols: list[Symbol],
    by_line: dict[int, Symbol],
) -> None:
    kind = SymbolKind.METHOD if parent else SymbolKind.FUNCTION
    existing = by_line.get(line)
    if existing is not None:
        if existing.name == "<anonymous>":
            existing.name = name
            existing.parent = parent
            existing.kind = kind
            if not existing.signature or existing.signature == "<anonymous>":
                existing.signature = signature
        return

    symbol = Symbol(
        name=name,
        kind=kind,
        line=line,
        end_line=end_line,
        signature=signature,
        parent=parent,
    )
    symbols.append(symbol)
    by_line[line] = symbol


def _maybe_add_js_var_func(
    node: Any,
    source_bytes: bytes,
    symbols: list[Symbol],
    by_line: dict[int, Symbol],
) -> None:
    name_node = node.child_by_field_name("name")
    val_node = node.child_by_field_name("value")
    if name_node is None or val_node is None:
        return
    if name_node.type != "identifier" or val_node.type not in _JS_FUNC_VALUE_NODES:
        return
    name = _node_text(name_node, source_bytes).strip()
    if not name:
        return
    _record_js_symbol(
        name=name,
        parent=None,
        line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        signature=_signature_prefix(node, source_bytes),
        symbols=symbols,
        by_line=by_line,
    )


def _maybe_add_js_prop_func(
    node: Any,
    source_bytes: bytes,
    symbols: list[Symbol],
    by_line: dict[int, Symbol],
) -> None:
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None:
        return
    if left.type != "member_expression" or right.type not in _JS_FUNC_VALUE_NODES:
        return
    prop_text = _node_text(left, source_bytes).strip()
    parent, _, name = prop_text.rpartition(".")
    if not parent or not name or (parent == "module" and name == "exports"):
        return
    _record_js_symbol(
        name=name,
        parent=parent,
        line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        signature=_signature_prefix(node, source_bytes),
        symbols=symbols,
        by_line=by_line,
    )


def _relative_level(module: str) -> int:
    if module == "." or module.startswith("./"):
        return 1
    depth = 0
    rest = module
    while rest.startswith("../"):
        depth += 1
        rest = rest[3:]
    if rest == "..":
        depth += 1
    return depth + 1 if depth else 0


def _strip_relative(module: str) -> str:
    while module.startswith("./"):
        module = module[2:]
    while module.startswith("../"):
        module = module[3:]
    return "" if module in (".", "..") else module


def _convert_imports(result: Any) -> list[ImportRef]:
    refs: list[ImportRef] = []
    for item in result.imports:
        raw = (getattr(item, "source", "") or "").strip()
        module = _module_of(raw)
        if not module:
            continue
        level = _relative_level(module)
        relative = _strip_relative(module) if level else module
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
) -> str | None:
    """Разрешить относительный импорт `./x` или `../x` в конкретный файл репозитория."""
    if not module:
        module = "index"
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
        variants.extend(candidate + suffix for suffix in PATH_SUFFIXES)
        variants.extend(posixpath.join(candidate, "index" + suffix) for suffix in PATH_SUFFIXES)
    else:
        variants.extend(stem + suffix for suffix in PATH_SUFFIXES)

    for variant in variants:
        if variant in known:
            return variant
    return None
