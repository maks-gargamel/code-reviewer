"""Слой визуализации и форматирования результатов."""

from __future__ import annotations

from .console import (
    _s,
    console,
    err_console,
    human_duration,
    human_size,
    print_cycles,
    print_deps,
    print_files,
    print_init_summary,
    print_outline,
    print_search,
    print_status,
)
from .report import (
    findings_json,
    findings_markdown,
    print_dry_run,
    print_findings,
    print_no_changes,
    print_scan_plan,
    print_verify,
)
from .sarif import findings_sarif
from .views import (
    eval_json,
    history_json,
    keys_json,
    print_cache,
    print_config,
    print_demo,
    print_doctor,
    print_eval,
    print_history,
    print_keys,
)

__all__ = [
    "_s",
    "console",
    "err_console",
    "eval_json",
    "findings_json",
    "findings_markdown",
    "findings_sarif",
    "history_json",
    "human_duration",
    "human_size",
    "keys_json",
    "print_cache",
    "print_config",
    "print_cycles",
    "print_demo",
    "print_deps",
    "print_doctor",
    "print_dry_run",
    "print_eval",
    "print_files",
    "print_findings",
    "print_history",
    "print_init_summary",
    "print_keys",
    "print_no_changes",
    "print_outline",
    "print_scan_plan",
    "print_search",
    "print_status",
    "print_verify",
]
