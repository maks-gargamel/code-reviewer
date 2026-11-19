"""Высокоуровневые сценарии: demo, eval и диагностика."""

from __future__ import annotations

from .demo import (
    DEMO_AFTER,
    DEMO_BEFORE,
    DEMO_FILE,
    DEMO_RESPONSE,
    DemoResult,
    RecordedClient,
    run_demo,
)
from .doctor import FAIL, OK, WARN, Check, has_failures, run_checks, summary
from .eval import (
    DEFAULT_TOLERANCE,
    Case,
    CaseResult,
    EvalReport,
    Expected,
    eval_cache_dir,
    load_cases,
    match_findings,
    run_case,
    run_eval,
)

__all__ = [
    "DEFAULT_TOLERANCE",
    "DEMO_AFTER",
    "DEMO_BEFORE",
    "DEMO_FILE",
    "DEMO_RESPONSE",
    "Case",
    "CaseResult",
    "Check",
    "DemoResult",
    "EvalReport",
    "Expected",
    "FAIL",
    "OK",
    "RecordedClient",
    "WARN",
    "eval_cache_dir",
    "has_failures",
    "load_cases",
    "match_findings",
    "run_case",
    "run_checks",
    "run_demo",
    "run_eval",
    "summary",
]
