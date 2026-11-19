"""Пайплайн ревью, промпты, маскирование секретов, ignore и история."""

from __future__ import annotations

from .chunking import ChunkPlan, file_tokens, plan_chunks
from .context import ReviewContext, build_context
from .findings import (
    dedupe,
    filter_to_paths,
    normalize_path,
    parse_findings,
    sort_findings,
    worst_rank,
    worst_severity,
)
from .history import (
    HistoryEntry,
    Metrics,
    append_entry,
    compute_metrics,
    history_path,
    load_history,
)
from .ignore import (
    IGNORE_FILE,
    IgnoreRules,
    collect_inline_ignores,
    directive_kind,
    filter_findings,
    scan_inline_directives,
)
from .pipeline import ReviewResult, VerifySummary, compute_verify, run_review
from .prompt import MAX_DIFF_CHARS, PromptBundle, build_prompt, estimate_tokens
from .secrets import PLACEHOLDER, redact

__all__ = [
    "ChunkPlan",
    "HistoryEntry",
    "IGNORE_FILE",
    "IgnoreRules",
    "MAX_DIFF_CHARS",
    "Metrics",
    "PLACEHOLDER",
    "PromptBundle",
    "ReviewContext",
    "ReviewResult",
    "VerifySummary",
    "append_entry",
    "build_context",
    "build_prompt",
    "collect_inline_ignores",
    "compute_metrics",
    "compute_verify",
    "dedupe",
    "directive_kind",
    "estimate_tokens",
    "file_tokens",
    "filter_findings",
    "filter_to_paths",
    "history_path",
    "load_history",
    "normalize_path",
    "parse_findings",
    "plan_chunks",
    "redact",
    "run_review",
    "scan_inline_directives",
    "sort_findings",
    "worst_rank",
    "worst_severity",
]
