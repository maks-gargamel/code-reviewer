from __future__ import annotations

from code_reviewer.core.models import SymbolKind
from code_reviewer.indexing.parsers import adapter_for
from code_reviewer.indexing.parsers.plain import PlainAdapter
from code_reviewer.indexing.parsers.python_ast import PythonAdapter

PY_SOURCE = '''"""Модуль для тестов."""

import os
from dataclasses import dataclass
from .models import User, Repo
from .. import util

MAX_RETRIES = 3
A = B = "multi"


@dataclass
class Store:
    """Хранилище сущностей."""

    async def fetch(self, key: str, limit: int = 10, *args, **kwargs) -> dict[str, int]:
        """Загрузить запись."""

        def inner(value):
            return value

        return {}


def helper(a, b=1, /, c=None, *, d: int = 2) -> None:
    """Помощник."""
    pass
'''


def test_python_parser_extracts_symbols_signatures_and_imports():
    info = PythonAdapter().extract(
        "app/service.py", PY_SOURCE, size=len(PY_SOURCE), mtime=1.0, digest="abc"
    )
    assert info.lang == "python"
    assert info.doc == "Модуль для тестов."
    assert info.parse_errors == []

    imports = [(i.level, i.module, i.names, i.is_from) for i in info.imports]
    assert (0, "os", [], False) in imports
    assert (0, "dataclasses", ["dataclass"], True) in imports
    assert (1, "models", ["User", "Repo"], True) in imports
    assert (2, "", ["util"], True) in imports

    by_name = {s.name: s for s in info.symbols}
    assert by_name["Store"].kind == SymbolKind.CLASS
    assert by_name["Store"].signature.startswith("@dataclass class Store")
    assert by_name["fetch"].kind == SymbolKind.METHOD
    assert by_name["fetch"].parent == "Store"
    assert "-> dict[str, int]" in by_name["fetch"].signature
    assert by_name["inner"].kind == SymbolKind.NESTED_FUNCTION
    assert by_name["inner"].parent == "fetch"
    assert by_name["helper"].kind == SymbolKind.FUNCTION
    assert "/" in by_name["helper"].signature
    assert "d: int = 2" in by_name["helper"].signature
    assert by_name["MAX_RETRIES"].kind == SymbolKind.CONSTANT
    assert "A" in by_name and "B" in by_name


def test_type_checking_imports_are_skipped_while_else_branch_is_kept():
    src = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .models import User\n"
        "else:\n"
        "    from .fallback import Stub\n"
    )
    info = PythonAdapter().extract("typed.py", src, size=len(src), mtime=1.0, digest="d")
    modules = [item.module for item in info.imports]
    assert "typing" in modules
    assert "models" not in modules
    assert "fallback" in modules


def test_syntax_error_and_leading_comment_fallback():
    broken = PythonAdapter().extract(
        "broken.py",
        "# Комментарий\ndef bad(:\n    pass\n",
        size=10,
        mtime=1.0,
        digest="d",
    )
    assert broken.parse_errors
    assert "строка" in broken.parse_errors[0]
    assert broken.doc == "Комментарий"
    assert broken.symbols == []


def test_plain_adapter_and_registry():
    info = PlainAdapter().extract(
        "web/app.ts",
        "// Точка входа\nexport const x = 1;\n",
        size=10,
        mtime=1.0,
        digest="d",
    )
    assert info.lang == "typescript"
    assert info.doc == "Точка входа"
    assert isinstance(adapter_for("a/b.py"), PythonAdapter)
    assert adapter_for("Makefile").name == "plain"
