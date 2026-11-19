"""Реестр языковых адаптеров."""

from __future__ import annotations

from .base import LanguageAdapter, ParseOptions, collapse, leading_comment
from .plain import PlainAdapter
from .python_ast import PythonAdapter
from .treesitter import TreeSitterAdapter, language_for, prefetch_grammars

_ADAPTERS: list[LanguageAdapter] = [
    PythonAdapter(),
    TreeSitterAdapter(),
    PlainAdapter(),
]


def adapter_for(path: str) -> LanguageAdapter:
    for adapter in _ADAPTERS:
        if adapter.matches(path):
            return adapter
    return _ADAPTERS[-1]


def supported_extensions() -> tuple[str, ...]:
    extensions: list[str] = []
    for adapter in _ADAPTERS[:-1]:
        extensions.extend(adapter.extensions)
    return tuple(extensions)


__all__ = [
    "LanguageAdapter",
    "ParseOptions",
    "PlainAdapter",
    "PythonAdapter",
    "TreeSitterAdapter",
    "adapter_for",
    "collapse",
    "language_for",
    "leading_comment",
    "prefetch_grammars",
    "supported_extensions",
]
