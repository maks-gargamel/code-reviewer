"""Tier A парсер Python-файлов на базе стандартного модуля `ast`."""

from __future__ import annotations

import ast

from ...core.models import FileInfo, ImportRef, Symbol, SymbolKind
from .base import LanguageAdapter, ParseOptions, collapse, leading_comment

MAX_DOC = 240
MAX_SIGNATURE = 300
MAX_CONSTANT_VALUE = 60


class PythonAdapter(LanguageAdapter):
    name = "python"
    extensions = (".py", ".pyi", ".pyw")

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
        del options
        info = FileInfo(
            path=path,
            lang=self.name,
            size=size,
            mtime=mtime,
            loc=source.count("\n") + (1 if source else 0),
            digest=digest,
        )
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            location = f" (строка {exc.lineno})" if exc.lineno else ""
            info.parse_errors.append(f"{exc.msg}{location}")
            info.doc = leading_comment(source)
            return info

        info.doc = collapse(ast.get_docstring(tree), MAX_DOC) or leading_comment(source)
        info.imports = _collect_imports(tree)
        info.symbols = _collect_symbols(tree)
        return info


def _collect_imports(tree: ast.AST) -> list[ImportRef]:
    """Собрать импорты, выполняемые в рантайме (исключая `if TYPE_CHECKING:`)."""
    imports: list[ImportRef] = []

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for else_stmt in node.orelse:
                walk(else_stmt)
            return

        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    ImportRef(
                        raw=alias.name,
                        line=node.lineno,
                        module=alias.name,
                        names=[],
                        level=0,
                        is_from=False,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                ImportRef(
                    raw=ast.unparse(node),
                    line=node.lineno,
                    module=node.module or "",
                    names=[alias.name for alias in node.names],
                    level=node.level,
                    is_from=True,
                )
            )

        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree)
    imports.sort(key=lambda item: item.line)
    return imports


def _is_type_checking(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _collect_symbols(tree: ast.Module) -> list[Symbol]:
    symbols: list[Symbol] = []
    _walk_body(tree.body, parent=None, parent_kind=None, out=symbols)
    return symbols


def _walk_body(
    body: list[ast.stmt],
    *,
    parent: str | None,
    parent_kind: str | None,
    out: list[Symbol],
) -> None:
    for node in body:
        if isinstance(node, ast.ClassDef):
            out.append(
                Symbol(
                    name=node.name,
                    kind=SymbolKind.CLASS,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=collapse(_class_signature(node), MAX_SIGNATURE) or "",
                    doc=collapse(ast.get_docstring(node), MAX_DOC),
                    parent=parent,
                )
            )
            _walk_body(
                node.body,
                parent=node.name,
                parent_kind=SymbolKind.CLASS,
                out=out,
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if parent is None:
                kind = SymbolKind.FUNCTION
            elif parent_kind == SymbolKind.CLASS:
                kind = SymbolKind.METHOD
            else:
                kind = SymbolKind.NESTED_FUNCTION
            out.append(
                Symbol(
                    name=node.name,
                    kind=kind,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=collapse(_function_signature(node), MAX_SIGNATURE) or "",
                    doc=collapse(ast.get_docstring(node), MAX_DOC),
                    parent=parent,
                )
            )
            _walk_body(
                node.body,
                parent=node.name,
                parent_kind=kind,
                out=out,
            )
        elif isinstance(node, ast.If) and parent is None:
            _walk_body(node.body, parent=None, parent_kind=None, out=out)
            _walk_body(node.orelse, parent=None, parent_kind=None, out=out)
        elif parent is None:
            out.extend(_module_constants(node))


def _module_constants(node: ast.stmt) -> list[Symbol]:
    targets: list[ast.expr] = []
    value: ast.expr | None = None
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
        value = node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = [node.target]
        value = node.value

    if value is None:
        return []

    rendered = collapse(ast.unparse(value), MAX_CONSTANT_VALUE) or ""
    found: list[Symbol] = []
    for target in targets:
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        found.append(
            Symbol(
                name=target.id,
                kind=SymbolKind.CONSTANT,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                signature=f"{target.id} = {rendered}".strip(),
            )
        )
    return found


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []

    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults.extend(args.defaults)

    for index, arg in enumerate(positional):
        rendered = arg.arg
        if arg.annotation is not None:
            rendered += f": {ast.unparse(arg.annotation)}"
        default_expr = defaults[index]
        if default_expr is not None:
            rendered += f" = {ast.unparse(default_expr)}"
        parts.append(rendered)
        if args.posonlyargs and index + 1 == len(args.posonlyargs):
            parts.append("/")

    if args.vararg is not None:
        rendered = "*" + args.vararg.arg
        if args.vararg.annotation is not None:
            rendered += f": {ast.unparse(args.vararg.annotation)}"
        parts.append(rendered)
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        rendered = arg.arg
        if arg.annotation is not None:
            rendered += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            rendered += f" = {ast.unparse(default)}"
        parts.append(rendered)

    if args.kwarg is not None:
        rendered = "**" + args.kwarg.arg
        if args.kwarg.annotation is not None:
            rendered += f": {ast.unparse(args.kwarg.annotation)}"
        parts.append(rendered)

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    signature = f"{prefix} {node.name}({', '.join(parts)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"

    decorators = [f"@{ast.unparse(d)}" for d in node.decorator_list[:3]]
    if decorators:
        signature = " ".join(decorators) + " " + signature
    return signature


def _class_signature(node: ast.ClassDef) -> str:
    parts = [ast.unparse(base) for base in node.bases]
    for keyword in node.keywords:
        if keyword.arg is None:
            parts.append(f"**{ast.unparse(keyword.value)}")
        else:
            parts.append(f"{keyword.arg}={ast.unparse(keyword.value)}")
    signature = f"class {node.name}"
    if parts:
        signature += f"({', '.join(parts)})"
    decorators = [f"@{ast.unparse(d)}" for d in node.decorator_list[:3]]
    if decorators:
        signature = " ".join(decorators) + " " + signature
    return signature
