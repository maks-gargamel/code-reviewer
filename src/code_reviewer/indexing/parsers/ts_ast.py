"""AST-дополнение извлечения символов и импортов поверх tree-sitter-language-pack."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.models import ImportRef, Symbol, SymbolKind
from .base import collapse

MAX_SIGNATURE = 300
MAX_RAW_IMPORT = 200

_JS_FUNC_VALUE_NODES = frozenset({"arrow_function", "function_expression", "function"})
_DART_METHOD_SIG_TYPES = frozenset(
    {
        "function_signature",
        "getter_signature",
        "setter_signature",
        "constructor_signature",
        "factory_constructor_signature",
        "operator_signature",
    }
)
_CPP_TYPE_SPECIFIERS = frozenset(
    {
        "struct_specifier",
        "class_specifier",
        "enum_specifier",
        "union_specifier",
    }
)
_CPP_BODY_TYPES = frozenset({"field_declaration_list", "enumerator_list"})
_CPP_NAME_TYPES = frozenset(
    {
        "identifier",
        "field_identifier",
        "destructor_name",
        "operator_name",
        "qualified_identifier",
        "template_function",
    }
)
_CPP_WRAPPER_TYPES = frozenset(
    {
        "function_declarator",
        "pointer_declarator",
        "reference_declarator",
        "parenthesized_declarator",
        "attributed_declarator",
    }
)


def relative_level(module: str) -> int:
    """Вычислить уровень относительности пути `./` (1), `../` (2), `../../` (3)..."""
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


def strip_relative(module: str) -> str:
    """Убрать ведущие `./` и `../` из относительного пути."""
    while module.startswith("./"):
        module = module[2:]
    while module.startswith("../"):
        module = module[3:]
    return "" if module in (".", "..") else module


def _node_text(node: Any, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _strip_quotes(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and (
        (cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'))
        or (cleaned[0] == "<" and cleaned[-1] == ">")
    ):
        return cleaned[1:-1].strip()
    return cleaned


def _header_before_brace(node: Any, source_bytes: bytes) -> str:
    text = _node_text(node, source_bytes)
    head = text.split("{", 1)[0].strip().rstrip(";").strip()
    return collapse(head, MAX_SIGNATURE) or ""


def _slice_before_node(node: Any, stop_node: Any | None, source_bytes: bytes) -> str:
    end_byte = stop_node.start_byte if stop_node is not None else node.end_byte
    raw = source_bytes[node.start_byte : end_byte].decode("utf-8", errors="replace")
    return collapse(raw.strip(), MAX_SIGNATURE) or ""


def _parse_tree(source: str, language: str, pack: Any) -> tuple[Any, bytes] | None:
    try:
        parser = pack.get_parser(language)
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
    except Exception:  # noqa: BLE001 - грамматика или исходник не обязаны парситься идеально
        return None
    return tree, source_bytes


def _walk_nodes(root: Any) -> list[Any]:
    nodes: list[Any] = []
    stack = [root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        for child in reversed(node.children):
            stack.append(child)
    return nodes


def _add_import_unique(
    imports: list[ImportRef],
    seen: set[tuple[int, str, str]],
    ref: ImportRef,
) -> None:
    key = (ref.line, ref.dotted, ref.raw)
    if key in seen:
        return
    seen.add(key)
    imports.append(ref)


def _record_named_symbol(
    *,
    name: str,
    kind: str,
    parent: str | None,
    line: int,
    end_line: int,
    signature: str,
    symbols: list[Symbol],
    by_line: dict[int, Symbol],
) -> None:
    existing = by_line.get(line)
    if existing is not None:
        if existing.name == "<anonymous>":
            existing.name = name
            existing.parent = parent
            existing.kind = kind
            if not existing.signature or existing.signature == "<anonymous>":
                existing.signature = signature
        elif parent and existing.parent is None:
            existing.parent = parent
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


# ---------------------------------------------------------------------------
# JavaScript / TypeScript / TSX
# ---------------------------------------------------------------------------


def _extract_js_string(node: Any, source_bytes: bytes) -> str | None:
    if node.type != "string":
        return None
    raw = _node_text(node, source_bytes).strip()
    if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
        return raw[1:-1].strip()
    return None


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
    level = relative_level(module)
    relative = strip_relative(module) if level else module
    dotted = "." * level + relative
    if (line, dotted) in seen:
        return
    seen.add((line, dotted))
    imports.append(
        ImportRef(
            raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or module,
            line=line,
            module=relative,
            names=[],
            level=level,
            is_from=False,
        )
    )


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
    _record_named_symbol(
        name=name,
        kind=SymbolKind.FUNCTION,
        parent=None,
        line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        signature=_header_before_brace(node, source_bytes),
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
    _record_named_symbol(
        name=name,
        kind=SymbolKind.METHOD,
        parent=parent,
        line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        signature=_header_before_brace(node, source_bytes),
        symbols=symbols,
        by_line=by_line,
    )


def _augment_js(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь CommonJS `require(...)` и функциональные присваивания через реальный AST."""
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
        return
    tree, source_bytes = parsed

    by_line: dict[int, Symbol] = {}
    for sym in symbols:
        by_line.setdefault(sym.line, sym)
    seen_imports: set[tuple[int, str]] = {(ref.line, ref.dotted) for ref in imports}

    for node in _walk_nodes(tree.root_node):
        if node.type == "call_expression":
            _maybe_add_commonjs_require(node, source_bytes, imports, seen_imports)
        elif node.type == "variable_declarator":
            _maybe_add_js_var_func(node, source_bytes, symbols, by_line)
        elif node.type == "assignment_expression":
            _maybe_add_js_prop_func(node, source_bytes, symbols, by_line)

    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]


# ---------------------------------------------------------------------------
# C / C++
# ---------------------------------------------------------------------------


def _cpp_has_function_declarator(node: Any) -> Any | None:
    if node.type == "function_declarator":
        return node
    for child in node.named_children:
        if child.type in _CPP_WRAPPER_TYPES:
            found = _cpp_has_function_declarator(child)
            if found is not None:
                return found
    return None


def _cpp_declarator_name(declarator: Any, source_bytes: bytes) -> str:
    current = declarator
    for _ in range(8):
        if current is None:
            return ""
        if current.type in _CPP_NAME_TYPES:
            if current.type == "template_function":
                name_child = current.child_by_field_name("name")
                if name_child is not None:
                    return _node_text(name_child, source_bytes).strip()
            return _node_text(current, source_bytes).strip()
        next_node = current.child_by_field_name("declarator")
        if next_node is None:
            for child in current.named_children:
                if child.type in _CPP_WRAPPER_TYPES or child.type in _CPP_NAME_TYPES:
                    next_node = child
                    break
        current = next_node
    return ""


def _cpp_type_name(node: Any, source_bytes: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes).strip()
    parent = node.parent
    if parent is not None and parent.type == "type_definition":
        decl = parent.child_by_field_name("declarator")
        if decl is not None:
            return (
                _cpp_declarator_name(decl, source_bytes)
                or _node_text(decl, source_bytes).strip()
            )
    return ""


def _cpp_body_child(node: Any) -> Any | None:
    body = node.child_by_field_name("body")
    if body is not None:
        return body
    for child in node.named_children:
        if child.type in _CPP_BODY_TYPES:
            return child
    return None


def _augment_c_cpp(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь `#include` и типизированные символы/методы C и C++ из AST."""
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    extracted: list[Symbol] = []
    seen_syms: set[tuple[int, str]] = set()

    def emit(sym: Symbol) -> None:
        key = (sym.line, sym.name)
        if key in seen_syms:
            return
        seen_syms.add(key)
        extracted.append(sym)

    def walk(node: Any, parent: str | None) -> None:
        ntype = node.type
        if ntype == "preproc_include":
            path_node = node.child_by_field_name("path")
            if path_node is None:
                for child in node.named_children:
                    if child.type in ("string_literal", "system_lib_string"):
                        path_node = child
                        break
            if path_node is not None:
                target = _strip_quotes(_node_text(path_node, source_bytes))
                if target:
                    is_system = path_node.type == "system_lib_string"
                    level = 0 if is_system else relative_level(target)
                    module = strip_relative(target) if level else target
                    _add_import_unique(
                        imports,
                        seen_imports,
                        ImportRef(
                            raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or target,
                            line=node.start_point.row + 1,
                            module=module,
                            names=[],
                            level=level,
                            is_from=False,
                        ),
                    )
            return

        if ntype == "preproc_def":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node is not None and value_node is not None:
                macro = _node_text(name_node, source_bytes).strip()
                if macro:
                    emit(
                        Symbol(
                            name=macro,
                            kind=SymbolKind.CONSTANT,
                            line=node.start_point.row + 1,
                            end_line=node.end_point.row + 1,
                            signature=collapse(_node_text(node, source_bytes), 120) or macro,
                            parent=parent,
                        )
                    )
            return

        if ntype == "namespace_definition":
            ns_node = node.child_by_field_name("name")
            ns_name = _node_text(ns_node, source_bytes).strip() if ns_node else ""
            body = node.child_by_field_name("body")
            next_parent = f"{parent}::{ns_name}" if (parent and ns_name) else (ns_name or parent)
            if body is not None:
                for child in body.named_children:
                    walk(child, next_parent)
            return

        if ntype in _CPP_TYPE_SPECIFIERS:
            body = _cpp_body_child(node)
            if body is None:
                return
            tname = _cpp_type_name(node, source_bytes)
            if tname:
                parent_node = node.parent
                sig_node = (
                    parent_node
                    if (parent_node is not None and parent_node.type == "template_declaration")
                    else node
                )
                sig = _header_before_brace(sig_node, source_bytes) or tname
                if node.child_by_field_name("name") is None and sig in (
                    "struct",
                    "class",
                    "enum",
                    "union",
                ):
                    sig = f"{sig} {tname}"
                emit(
                    Symbol(
                        name=tname,
                        kind=SymbolKind.CLASS,
                        line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        signature=sig,
                        parent=parent,
                    )
                )
            for child in body.named_children:
                walk(child, tname or parent)
            return

        if ntype in ("function_definition", "declaration", "field_declaration"):
            decl = node.child_by_field_name("declarator")
            if decl is None:
                for child in node.named_children:
                    if child.type in _CPP_WRAPPER_TYPES:
                        decl = child
                        break
            func_decl = _cpp_has_function_declarator(decl) if decl is not None else None
            if func_decl is not None:
                raw_name = _cpp_declarator_name(func_decl, source_bytes)
                if raw_name:
                    sym_parent = parent
                    sym_name = raw_name
                    if "::" in raw_name:
                        cls_part, _, method_part = raw_name.rpartition("::")
                        sym_name = method_part.strip() or raw_name
                        cls_clean = cls_part.strip()
                        if cls_clean:
                            sym_parent = cls_clean
                    sig_node = (
                        node.parent
                        if (node.parent and node.parent.type == "template_declaration")
                        else node
                    )
                    emit(
                        Symbol(
                            name=sym_name,
                            kind=SymbolKind.METHOD if sym_parent else SymbolKind.FUNCTION,
                            line=node.start_point.row + 1,
                            end_line=node.end_point.row + 1,
                            signature=_header_before_brace(sig_node, source_bytes) or sym_name,
                            parent=sym_parent,
                        )
                    )
                return

        for child in node.named_children:
            walk(child, parent)

    walk(tree.root_node, None)
    symbols[:] = extracted


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def _go_receiver_type(node: Any, source_bytes: bytes) -> str | None:
    recv = node.child_by_field_name("receiver")
    if recv is None:
        return None
    text = _node_text(recv, source_bytes).strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if not text:
        return None
    type_part = text.split()[-1].lstrip("*")
    type_part = type_part.split("[", 1)[0].strip()
    return type_part or None


def _augment_go(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Дополнить Go-карту объявлениями struct/interface и привязкой методов к типу-получателю."""
    del imports
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
        return
    tree, source_bytes = parsed

    by_line: dict[int, Symbol] = {}
    for sym in symbols:
        by_line.setdefault(sym.line, sym)

    for node in _walk_nodes(tree.root_node):
        if node.type == "method_declaration":
            line = node.start_point.row + 1
            receiver = _go_receiver_type(node, source_bytes)
            existing = by_line.get(line)
            if existing is not None and receiver and not existing.parent:
                existing.parent = receiver
        elif node.type == "type_spec":
            name_node = node.child_by_field_name("name")
            type_node = node.child_by_field_name("type")
            if name_node is None or type_node is None:
                continue
            name = _node_text(name_node, source_bytes).strip()
            if not name:
                continue
            if type_node.type == "struct_type":
                sig = f"type {name} struct"
            elif type_node.type == "interface_type":
                sig = f"type {name} interface"
            else:
                sig = _header_before_brace(node, source_bytes) or f"type {name}"
                sig = f"type {sig}" if not sig.startswith("type ") else sig
            line = node.start_point.row + 1
            if line not in by_line:
                _record_named_symbol(
                    name=name,
                    kind=SymbolKind.CLASS,
                    parent=None,
                    line=line,
                    end_line=node.end_point.row + 1,
                    signature=sig,
                    symbols=symbols,
                    by_line=by_line,
                )

    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


def _augment_rust(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Добавить внешние `mod foo;` в импорты Rust и отфильтровать анонимные заглушки."""
    parsed = _parse_tree(source, language, pack)
    if parsed is not None:
        tree, source_bytes = parsed
        seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
        for node in _walk_nodes(tree.root_node):
            if node.type != "mod_item":
                continue
            if node.child_by_field_name("body") is not None or any(
                c.type == "declaration_list" for c in node.named_children
            ):
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            mod_name = _node_text(name_node, source_bytes).strip()
            if not mod_name:
                continue
            _add_import_unique(
                imports,
                seen_imports,
                ImportRef(
                    raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or mod_name,
                    line=node.start_point.row + 1,
                    module=mod_name,
                    names=[],
                    level=1,
                    is_from=False,
                ),
            )
    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


def _augment_java(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Убрать ложные узлы `.class` литералов Java (`name=None, sig='class'`)."""
    del source, language, pack, imports
    symbols[:] = [
        s
        for s in symbols
        if not (s.name == "<anonymous>" and s.signature in ("", "class", "<anonymous>"))
    ]


# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------


def _kotlin_type_name(node: Any, source_bytes: bytes) -> str:
    if node.type == "companion_object":
        for child in node.named_children:
            if child.type in ("type_identifier", "simple_identifier"):
                return _node_text(child, source_bytes).strip()
        return "Companion"
    for child in node.named_children:
        if child.type in ("type_identifier", "simple_identifier"):
            return _node_text(child, source_bytes).strip()
    return ""


def _kotlin_body_node(node: Any) -> Any | None:
    for child in node.named_children:
        if child.type in ("class_body", "enum_class_body", "function_body"):
            return child
    return None


def _augment_kotlin(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь импорты Kotlin и чистые сигнатуры классов/методов/функций без тела."""
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    extracted: list[Symbol] = []

    def walk(node: Any, parent: str | None) -> None:
        ntype = node.type
        if ntype == "import_header":
            id_node = next((c for c in node.named_children if c.type == "identifier"), None)
            if id_node is not None:
                mod = _node_text(id_node, source_bytes).strip()
                if mod:
                    _add_import_unique(
                        imports,
                        seen_imports,
                        ImportRef(
                            raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or mod,
                            line=node.start_point.row + 1,
                            module=mod,
                            names=[],
                            level=0,
                            is_from=False,
                        ),
                    )
            return

        if ntype in ("class_declaration", "object_declaration", "companion_object"):
            name = _kotlin_type_name(node, source_bytes)
            body = _kotlin_body_node(node)
            if name:
                extracted.append(
                    Symbol(
                        name=name,
                        kind=SymbolKind.CLASS,
                        line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        signature=_slice_before_node(node, body, source_bytes) or name,
                        parent=parent,
                    )
                )
            if body is not None:
                for child in body.named_children:
                    walk(child, name or parent)
            return

        if ntype == "function_declaration":
            name_node = next(
                (c for c in node.named_children if c.type == "simple_identifier"),
                None,
            )
            if name_node is not None:
                name = _node_text(name_node, source_bytes).strip()
                body = _kotlin_body_node(node)
                if name:
                    extracted.append(
                        Symbol(
                            name=name,
                            kind=SymbolKind.METHOD if parent else SymbolKind.FUNCTION,
                            line=node.start_point.row + 1,
                            end_line=node.end_point.row + 1,
                            signature=_slice_before_node(node, body, source_bytes) or name,
                            parent=parent,
                        )
                    )
            return

        for child in node.named_children:
            walk(child, parent)

    walk(tree.root_node, None)
    symbols[:] = extracted


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------


def _augment_csharp(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь `using` директивы и недостающие record/interface/enum в C#."""
    symbols[:] = [
        s
        for s in symbols
        if not (s.name == "<anonymous>" and s.signature in ("", "class", "<anonymous>"))
    ]
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    by_line: dict[int, Symbol] = {sym.line: sym for sym in symbols}

    for node in _walk_nodes(tree.root_node):
        if node.type == "using_directive":
            target_node = next(
                (
                    c
                    for c in reversed(node.named_children)
                    if c.type in ("qualified_name", "identifier")
                ),
                None,
            )
            if target_node is None:
                continue
            ns = _node_text(target_node, source_bytes).strip()
            if ns:
                _add_import_unique(
                    imports,
                    seen_imports,
                    ImportRef(
                        raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or ns,
                        line=node.start_point.row + 1,
                        module=ns,
                        names=[],
                        level=0,
                        is_from=False,
                    ),
                )
        elif node.type in (
            "record_declaration",
            "interface_declaration",
            "struct_declaration",
            "enum_declaration",
        ):
            line = node.start_point.row + 1
            if line in by_line:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = _node_text(name_node, source_bytes).strip()
            if name:
                _record_named_symbol(
                    name=name,
                    kind=SymbolKind.CLASS,
                    parent=None,
                    line=line,
                    end_line=node.end_point.row + 1,
                    signature=_header_before_brace(node, source_bytes) or name,
                    symbols=symbols,
                    by_line=by_line,
                )


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------


def _ruby_literal_string(node: Any, source_bytes: bytes) -> str | None:
    if node.type != "string":
        return None
    if any(c.type == "interpolation" for c in node.named_children):
        return None
    val = _strip_quotes(_node_text(node, source_bytes))
    return val or None


def _augment_ruby(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь `require` и `require_relative` в Ruby."""
    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    for node in _walk_nodes(tree.root_node):
        if node.type != "call":
            continue
        method_node = node.child_by_field_name("method")
        args_node = node.child_by_field_name("arguments")
        if method_node is None or args_node is None:
            continue
        method_name = _node_text(method_node, source_bytes).strip()
        if method_name not in ("require", "require_relative"):
            continue
        if not args_node.named_children:
            continue
        target = _ruby_literal_string(args_node.named_children[0], source_bytes)
        if not target:
            continue
        rel_lvl = relative_level(target)
        if method_name == "require_relative":
            level = rel_lvl or 1
            module = strip_relative(target) if rel_lvl else target
        else:
            level = rel_lvl
            module = strip_relative(target) if level else target
        _add_import_unique(
            imports,
            seen_imports,
            ImportRef(
                raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or target,
                line=node.start_point.row + 1,
                module=module,
                names=[],
                level=level,
                is_from=False,
            ),
        )


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------


def _php_extract_path(node: Any, source_bytes: bytes) -> tuple[int, str] | None:
    if node.type in ("string", "encapsed_string"):
        if any(c.type == "variable_name" for c in node.named_children):
            return None
        val = _strip_quotes(_node_text(node, source_bytes))
        if not val:
            return None
        lvl = relative_level(val)
        if lvl:
            return lvl, strip_relative(val)
        return (1, val) if val.endswith(".php") else (0, val)

    if node.type == "binary_expression":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if (
            left is not None
            and right is not None
            and _node_text(left, source_bytes).strip() == "__DIR__"
        ):
            inner = _php_extract_path(right, source_bytes)
            if inner is not None:
                return 1, inner[1].lstrip("/")
    return None


def _augment_php(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь `use` (включая группы) и `require`/`include` в PHP."""
    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    for node in _walk_nodes(tree.root_node):
        if node.type == "namespace_use_declaration":
            line = node.start_point.row + 1
            raw = collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or "use"
            group = next(
                (c for c in node.named_children if c.type == "namespace_use_group"),
                None,
            )
            prefix_node = next(
                (c for c in node.named_children if c.type == "namespace_name"),
                None,
            )
            if group is not None:
                prefix = (
                    _node_text(prefix_node, source_bytes).strip().replace("\\", ".").strip(".")
                    if prefix_node is not None
                    else ""
                )
                for clause in group.named_children:
                    if clause.type != "namespace_use_clause":
                        continue
                    target = next(
                        (
                            c
                            for c in clause.named_children
                            if c.type in ("qualified_name", "name")
                        ),
                        None,
                    )
                    if target is None:
                        continue
                    part = _node_text(target, source_bytes).strip().replace("\\", ".").strip(".")
                    fqn = f"{prefix}.{part}" if prefix else part
                    if fqn:
                        _add_import_unique(
                            imports,
                            seen_imports,
                            ImportRef(raw=raw, line=line, module=fqn, level=0, is_from=True),
                        )
            else:
                for clause in node.named_children:
                    if clause.type != "namespace_use_clause":
                        continue
                    target = next(
                        (
                            c
                            for c in clause.named_children
                            if c.type in ("qualified_name", "name")
                        ),
                        None,
                    )
                    if target is None:
                        continue
                    fqn = _node_text(target, source_bytes).strip().replace("\\", ".").strip(".")
                    if fqn:
                        _add_import_unique(
                            imports,
                            seen_imports,
                            ImportRef(raw=raw, line=line, module=fqn, level=0, is_from=True),
                        )
        elif node.type in (
            "require_expression",
            "require_once_expression",
            "include_expression",
            "include_once_expression",
        ):
            if not node.named_children:
                continue
            resolved = _php_extract_path(node.named_children[0], source_bytes)
            if resolved is None:
                continue
            level, module = resolved
            if not module:
                continue
            _add_import_unique(
                imports,
                seen_imports,
                ImportRef(
                    raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or module,
                    line=node.start_point.row + 1,
                    module=module,
                    names=[],
                    level=level,
                    is_from=False,
                ),
            )


# ---------------------------------------------------------------------------
# Scala
# ---------------------------------------------------------------------------


def _augment_scala(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь `import` в Scala и очистить завершающее `=` из сигнатур."""
    for sym in symbols:
        if sym.signature.endswith(" ="):
            sym.signature = sym.signature[:-2].rstrip()

    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    for node in _walk_nodes(tree.root_node):
        if node.type != "import_declaration":
            continue
        path_parts = [
            _node_text(node.children[idx], source_bytes).strip()
            for idx in range(node.child_count)
            if node.children[idx].is_named and node.field_name_for_child(idx) == "path"
        ]
        selectors: list[str] = []
        for child in node.named_children:
            if child.type == "namespace_selectors":
                selectors.extend(
                    _node_text(c, source_bytes).strip()
                    for c in child.named_children
                    if c.type in ("identifier", "operator_identifier", "type_identifier")
                )
            elif child.type == "namespace_wildcard":
                selectors.append("_")

        base_mod = ".".join(p for p in path_parts if p and p != ".")
        if not base_mod:
            continue
        _add_import_unique(
            imports,
            seen_imports,
            ImportRef(
                raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or base_mod,
                line=node.start_point.row + 1,
                module=base_mod,
                names=selectors,
                level=0,
                is_from=bool(selectors),
            ),
        )


# ---------------------------------------------------------------------------
# Swift
# ---------------------------------------------------------------------------


def _augment_swift(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь `import ...` директивы в Swift."""
    del symbols
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    for node in _walk_nodes(tree.root_node):
        if node.type != "import_declaration":
            continue
        id_node = next((c for c in node.named_children if c.type == "identifier"), None)
        if id_node is None:
            continue
        mod = _node_text(id_node, source_bytes).strip()
        if not mod:
            continue
        _add_import_unique(
            imports,
            seen_imports,
            ImportRef(
                raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or mod,
                line=node.start_point.row + 1,
                module=mod,
                names=[],
                level=0,
                is_from=False,
            ),
        )


# ---------------------------------------------------------------------------
# Lua
# ---------------------------------------------------------------------------


def _augment_lua(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь `require` и именованные табличные функции (`M.foo = function`, `M:bar`) в Lua."""
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    by_line: dict[int, Symbol] = {sym.line: sym for sym in symbols}

    for node in _walk_nodes(tree.root_node):
        if node.type == "function_call":
            first_id = next((c for c in node.named_children if c.type == "identifier"), None)
            args_node = next((c for c in node.named_children if c.type == "arguments"), None)
            if first_id is None or args_node is None:
                continue
            if _node_text(first_id, source_bytes).strip() != "require":
                continue
            str_node = next((c for c in args_node.named_children if c.type == "string"), None)
            if str_node is None:
                continue
            mod = _strip_quotes(_node_text(str_node, source_bytes))
            if not mod:
                continue
            level = relative_level(mod)
            module = strip_relative(mod) if level else mod
            _add_import_unique(
                imports,
                seen_imports,
                ImportRef(
                    raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or mod,
                    line=node.start_point.row + 1,
                    module=module,
                    names=[],
                    level=level,
                    is_from=False,
                ),
            )
        elif node.type == "function_declaration":
            target = next(
                (
                    c
                    for c in node.named_children
                    if c.type in ("dot_index_expression", "method_index_expression")
                ),
                None,
            )
            if target is None:
                continue
            full = _node_text(target, source_bytes).strip()
            sep = ":" if ":" in full else "."
            parent_name, _, func_name = full.rpartition(sep)
            if func_name:
                line = node.start_point.row + 1
                params = next((c for c in node.named_children if c.type == "parameters"), None)
                sig = f"function {full}{_node_text(params, source_bytes)}" if params else full
                _record_named_symbol(
                    name=func_name,
                    kind=SymbolKind.METHOD,
                    parent=parent_name or None,
                    line=line,
                    end_line=node.end_point.row + 1,
                    signature=collapse(sig, MAX_SIGNATURE) or full,
                    symbols=symbols,
                    by_line=by_line,
                )
        elif node.type == "assignment_statement":
            var_list = next(
                (c for c in node.named_children if c.type == "variable_list"),
                None,
            )
            expr_list = next(
                (c for c in node.named_children if c.type == "expression_list"),
                None,
            )
            if var_list is None or expr_list is None:
                continue
            if len(var_list.named_children) != 1 or len(expr_list.named_children) != 1:
                continue
            lhs = var_list.named_children[0]
            rhs = expr_list.named_children[0]
            if rhs.type != "function_definition":
                continue
            if lhs.type == "identifier":
                parent_name = ""
                func_name = _node_text(lhs, source_bytes).strip()
                kind = SymbolKind.FUNCTION
            elif lhs.type == "dot_index_expression":
                parent_name, _, func_name = _node_text(lhs, source_bytes).strip().rpartition(".")
                kind = SymbolKind.METHOD
            else:
                continue
            if not func_name:
                continue
            params = next((c for c in rhs.named_children if c.type == "parameters"), None)
            param_text = _node_text(params, source_bytes) if params is not None else "()"
            label = f"{parent_name}.{func_name}" if parent_name else func_name
            sig = collapse(f"{label} = function{param_text}", MAX_SIGNATURE) or label
            _record_named_symbol(
                name=func_name,
                kind=kind,
                parent=parent_name or None,
                line=rhs.start_point.row + 1,
                end_line=rhs.end_point.row + 1,
                signature=sig,
                symbols=symbols,
                by_line=by_line,
            )

    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]


# ---------------------------------------------------------------------------
# Dart
# ---------------------------------------------------------------------------


def _dart_first_string(node: Any, source_bytes: bytes) -> str | None:
    for sub in _walk_nodes(node):
        if sub.type == "string_literal":
            val = _strip_quotes(_node_text(sub, source_bytes))
            return val or None
    return None


def _dart_signature_name(sig_node: Any, source_bytes: bytes) -> str:
    if sig_node.type == "constructor_signature":
        parts = [
            _node_text(c, source_bytes).strip()
            for c in sig_node.named_children
            if c.type == "identifier"
        ]
        return ".".join(p for p in parts if p)
    if sig_node.type == "factory_constructor_signature":
        parts = [
            _node_text(c, source_bytes).strip()
            for c in sig_node.named_children
            if c.type == "identifier"
        ]
        return ".".join(p for p in parts if p)
    id_node = sig_node.child_by_field_name("name") or next(
        (c for c in sig_node.named_children if c.type == "identifier"),
        None,
    )
    return _node_text(id_node, source_bytes).strip() if id_node is not None else ""


def _augment_dart(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Извлечь import/export/part, классы, enum, mixin, функции и методы в Dart."""
    symbols[:] = [s for s in symbols if s.name != "<anonymous>"]
    parsed = _parse_tree(source, language, pack)
    if parsed is None:
        return
    tree, source_bytes = parsed

    seen_imports: set[tuple[int, str, str]] = {(r.line, r.dotted, r.raw) for r in imports}
    by_line: dict[int, Symbol] = {sym.line: sym for sym in symbols}

    def add_uri_import(node: Any) -> None:
        uri = _dart_first_string(node, source_bytes)
        if not uri:
            return
        if uri.startswith(("dart:", "package:")) or "://" in uri:
            level = 0
            module = uri
        else:
            lvl = relative_level(uri)
            level = lvl or 1
            module = strip_relative(uri) if lvl else uri
        _add_import_unique(
            imports,
            seen_imports,
            ImportRef(
                raw=collapse(_node_text(node, source_bytes), MAX_RAW_IMPORT) or uri,
                line=node.start_point.row + 1,
                module=module,
                names=[],
                level=level,
                is_from=False,
            ),
        )

    def walk(node: Any, parent: str | None) -> None:
        ntype = node.type
        if ntype in ("import_or_export", "part_directive"):
            add_uri_import(node)
            return

        if ntype in (
            "class_definition",
            "enum_declaration",
            "mixin_declaration",
            "extension_declaration",
        ):
            name_node = next(
                (c for c in node.named_children if c.type == "identifier"),
                None,
            )
            name = _node_text(name_node, source_bytes).strip() if name_node else ""
            if name:
                _record_named_symbol(
                    name=name,
                    kind=SymbolKind.CLASS,
                    parent=parent,
                    line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    signature=_header_before_brace(node, source_bytes) or name,
                    symbols=symbols,
                    by_line=by_line,
                )
            for child in node.named_children:
                walk(child, name or parent)
            return

        if ntype in ("function_signature", "method_signature", "declaration"):
            sig_node = (
                node
                if ntype == "function_signature"
                else next(
                    (c for c in node.named_children if c.type in _DART_METHOD_SIG_TYPES),
                    None,
                )
            )
            if sig_node is not None:
                mname = _dart_signature_name(sig_node, source_bytes)
                if mname:
                    _record_named_symbol(
                        name=mname,
                        kind=SymbolKind.METHOD if parent else SymbolKind.FUNCTION,
                        parent=parent,
                        line=sig_node.start_point.row + 1,
                        end_line=sig_node.end_point.row + 1,
                        signature=collapse(_node_text(sig_node, source_bytes), MAX_SIGNATURE)
                        or mname,
                        symbols=symbols,
                        by_line=by_line,
                    )
                return

        for child in node.named_children:
            walk(child, parent)

    walk(tree.root_node, None)


_Augmentor = Callable[[str, str, Any, list[Symbol], list[ImportRef]], None]

_AUGMENTORS: dict[str, _Augmentor] = {
    "javascript": _augment_js,
    "typescript": _augment_js,
    "tsx": _augment_js,
    "c": _augment_c_cpp,
    "cpp": _augment_c_cpp,
    "go": _augment_go,
    "rust": _augment_rust,
    "java": _augment_java,
    "kotlin": _augment_kotlin,
    "csharp": _augment_csharp,
    "ruby": _augment_ruby,
    "php": _augment_php,
    "scala": _augment_scala,
    "swift": _augment_swift,
    "lua": _augment_lua,
    "dart": _augment_dart,
}


def augment_ast(
    source: str,
    language: str,
    pack: Any,
    symbols: list[Symbol],
    imports: list[ImportRef],
) -> None:
    """Применить языково-специфичный AST-обход поверх результата `pack.process()`."""
    handler = _AUGMENTORS.get(language)
    if handler is not None:
        handler(source, language, pack, symbols, imports)
    symbols.sort(key=lambda sym: (sym.line, sym.name))
    imports.sort(key=lambda ref: ref.line)
