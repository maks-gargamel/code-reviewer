"""Индексация репозитория: обход файлов, парсеры, граф зависимостей и AI-сводки."""

from __future__ import annotations

from .builder import (
    BuildResult,
    BuildStats,
    build_map,
    detect_source_roots,
    load_map,
    map_path,
    stale_files,
)
from .discovery import (
    BINARY_EXTENSIONS,
    DEFAULT_IGNORED_DIRS,
    DEFAULT_IGNORED_FILES,
    DEFAULT_MAX_FILE_BYTES,
    DiscoveredFile,
    DiscoveryResult,
    discover_files,
)
from .graph import Cycle, SymbolHit, find_cycles, internal_edges, search_symbols, to_dot
from .resolver import module_by_path, module_index, resolve_edges
from .summarize import (
    SummaryResult,
    build_summary_prompt,
    parse_summary,
    summarize_map,
    summarized_count,
)

__all__ = [
    "BINARY_EXTENSIONS",
    "BuildResult",
    "BuildStats",
    "Cycle",
    "DEFAULT_IGNORED_DIRS",
    "DEFAULT_IGNORED_FILES",
    "DEFAULT_MAX_FILE_BYTES",
    "DiscoveredFile",
    "DiscoveryResult",
    "SummaryResult",
    "SymbolHit",
    "build_map",
    "build_summary_prompt",
    "detect_source_roots",
    "discover_files",
    "find_cycles",
    "internal_edges",
    "load_map",
    "map_path",
    "module_by_path",
    "module_index",
    "parse_summary",
    "resolve_edges",
    "search_symbols",
    "stale_files",
    "summarize_map",
    "summarized_count",
    "to_dot",
]
