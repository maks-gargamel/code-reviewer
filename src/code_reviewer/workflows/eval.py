"""Eval-стенд: оценка precision / recall / F1 на кейсах с известными дефектами."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import EVAL_CACHE_DIR, Config, service_dir
from ..core.models import Finding
from ..indexing.builder import build_map
from ..llm.protocol import LLMClient
from ..review.findings import normalize_path
from ..review.pipeline import run_review
from ..vcs import git
from ..vcs.diff import synthetic_diff_from_maps

DEFAULT_TOLERANCE = 2
ProgressCallback = Callable[[int, int, str], None]


@dataclass(slots=True)
class Expected:
    file: str
    line: int
    category: str = ""
    severity: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "category": self.category,
            "severity": self.severity,
        }


@dataclass(slots=True)
class Case:
    name: str
    files: dict[str, str] = field(default_factory=dict)
    changes: dict[str, str] = field(default_factory=dict)
    expected: list[Expected] = field(default_factory=list)
    description: str = ""

    @property
    def expected_files(self) -> list[str]:
        return list(self.changes) or list(self.files)


@dataclass(slots=True)
class CaseResult:
    name: str
    description: str = ""
    matched: int = 0
    missed: list[Expected] = field(default_factory=list)
    extra: list[Finding] = field(default_factory=list)
    findings: int = 0
    expected: int = 0
    requests: int = 0
    cached: int = 0
    duration: float = 0.0
    error: str = ""

    @property
    def false_negatives(self) -> int:
        return max(0, self.expected - self.matched)

    @property
    def false_positives(self) -> int:
        return len(self.extra)

    @property
    def precision(self) -> float:
        denominator = self.matched + len(self.extra)
        if denominator == 0:
            return 1.0 if not self.expected else 0.0
        return self.matched / denominator

    @property
    def recall(self) -> float:
        if not self.expected:
            return 1.0
        return self.matched / self.expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "matched": self.matched,
            "false_negatives": self.false_negatives,
            "false_positives": self.false_positives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "requests": self.requests,
            "cached": self.cached,
            "duration": round(self.duration, 3),
            "error": self.error,
            "missed": [item.to_dict() for item in self.missed],
            "extra": [item.to_dict() for item in self.extra],
        }


@dataclass(slots=True)
class EvalReport:
    cases: list[CaseResult] = field(default_factory=list)
    tolerance: int = DEFAULT_TOLERANCE

    @property
    def totals(self) -> tuple[int, int, int]:
        matched = sum(case.matched for case in self.cases)
        false_negatives = sum(case.false_negatives for case in self.cases)
        false_positives = sum(case.false_positives for case in self.cases)
        return matched, false_negatives, false_positives

    @property
    def precision(self) -> float:
        matched, false_negatives, false_positives = self.totals
        denominator = matched + false_positives
        if denominator == 0:
            return 1.0 if false_negatives == 0 else 0.0
        return matched / denominator

    @property
    def recall(self) -> float:
        matched, false_negatives, _ = self.totals
        denominator = matched + false_negatives
        return matched / denominator if denominator else 1.0

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        return 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    @property
    def requests(self) -> int:
        return sum(case.requests for case in self.cases)

    @property
    def cached(self) -> int:
        return sum(case.cached for case in self.cases)

    def to_dict(self) -> dict[str, Any]:
        matched, false_negatives, false_positives = self.totals
        return {
            "tolerance": self.tolerance,
            "matched": matched,
            "false_negatives": false_negatives,
            "false_positives": false_positives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "requests": self.requests,
            "cached": self.cached,
            "cases": [case.to_dict() for case in self.cases],
        }


def load_cases(directory: Path) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        if not isinstance(data, dict):
            continue
        files = data.get("files", {})
        changes = data.get("changes", {})
        expected_raw = data.get("expected", [])
        if not isinstance(files, dict) or not isinstance(changes, dict):
            raise ValueError(f"{path.name}: поля files и changes должны быть объектами")
        if not isinstance(expected_raw, list):
            raise ValueError(f"{path.name}: поле expected должно быть массивом")

        expected = [
            Expected(
                file=str(item.get("file", "")),
                line=int(item.get("line", 0)),
                category=str(item.get("category", "")),
                severity=str(item.get("severity", "")),
            )
            for item in expected_raw
            if isinstance(item, dict)
        ]
        cases.append(
            Case(
                name=str(data.get("name") or path.stem),
                files={str(k): str(v) for k, v in files.items()},
                changes={str(k): str(v) for k, v in changes.items()},
                expected=expected,
                description=str(data.get("description", "")),
            )
        )
    return cases


def match_findings(
    expected: list[Expected],
    findings: list[Finding],
    *,
    tolerance: int = DEFAULT_TOLERANCE,
) -> tuple[int, list[Expected], list[Finding]]:
    """Оптимальное жадное сопоставление ожидаемых дефектов и находок по минимальной дистанции."""
    candidates: list[tuple[int, int, int]] = []
    for e_idx, target in enumerate(expected):
        target_file = normalize_path(target.file)
        for f_idx, item in enumerate(findings):
            if normalize_path(item.file) != target_file:
                continue
            distance = abs(item.line - target.line)
            if distance <= tolerance:
                candidates.append((distance, e_idx, f_idx))

    candidates.sort()
    matched_expected: set[int] = set()
    matched_findings: set[int] = set()

    for _, e_idx, f_idx in candidates:
        if e_idx in matched_expected or f_idx in matched_findings:
            continue
        matched_expected.add(e_idx)
        matched_findings.add(f_idx)

    missed = [item for idx, item in enumerate(expected) if idx not in matched_expected]
    extra = [item for idx, item in enumerate(findings) if idx not in matched_findings]
    return len(matched_expected), missed, extra


def run_case(
    case: Case,
    config: Config,
    client: LLMClient,
    *,
    tolerance: int = DEFAULT_TOLERANCE,
    use_cache: bool = True,
    cache_dir: Path | None = None,
    jobs: int = 1,
    has_git: bool | None = None,
) -> CaseResult:
    """Прогнать один eval-кейс (через git или синтетический diff, если git не установлен)."""
    started = time.perf_counter()
    result = CaseResult(
        name=case.name,
        description=case.description,
        expected=len(case.expected),
    )
    use_git = git.is_git_available() if has_git is None else has_git

    with tempfile.TemporaryDirectory(prefix="code-reviewer-eval-") as tmp:
        work = Path(tmp)
        for relative, content in case.files.items():
            _write(work, relative, content)
        if use_git:
            _commit_baseline(work)
        for relative, content in case.changes.items():
            _write(work, relative, content)

        try:
            repo_map = build_map(work, config).repo_map
            diffset = (
                None
                if use_git
                else synthetic_diff_from_maps(case.files, case.changes, scope="eval")
            )
            review = run_review(
                work,
                config,
                repo_map,
                scope="unstaged",
                diffset=diffset,
                client=client,
                use_cache=use_cache,
                use_ignore=False,
                cache_dir=cache_dir,
                jobs=jobs,
            )
        except Exception as exc:  # noqa: BLE001 - ошибка одного кейса не роняет весь прогон
            result.error = f"{type(exc).__name__}: {exc}"
            result.duration = time.perf_counter() - started
            return result

    matched, missed, extra = match_findings(
        case.expected, review.findings, tolerance=tolerance
    )
    result.matched = matched
    result.missed = missed
    result.extra = extra
    result.findings = len(review.findings)
    result.requests = max(0, review.chunks - review.cached_chunks)
    result.cached = review.cached_chunks
    result.duration = time.perf_counter() - started
    return result


def run_eval(
    cases: list[Case],
    config: Config,
    client: LLMClient,
    *,
    tolerance: int = DEFAULT_TOLERANCE,
    use_cache: bool = True,
    cache_dir: Path | None = None,
    max_cases: int | None = None,
    jobs: int = 1,
    progress: ProgressCallback | None = None,
) -> EvalReport:
    selected = cases[:max_cases] if max_cases else cases
    report = EvalReport(tolerance=tolerance)
    has_git = git.is_git_available()
    for index, case in enumerate(selected, start=1):
        if progress is not None:
            progress(index, len(selected), case.name)
        report.cases.append(
            run_case(
                case,
                config,
                client,
                tolerance=tolerance,
                use_cache=use_cache,
                cache_dir=cache_dir,
                jobs=jobs,
                has_git=has_git,
            )
        )
    return report


def eval_cache_dir(root: Path) -> Path:
    return service_dir(root) / EVAL_CACHE_DIR


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit_baseline(root: Path) -> None:
    git.run_git_checked(root, "init", "-q")
    identity = (
        "-c",
        "user.email=eval@code-reviewer.local",
        "-c",
        "user.name=code-reviewer-eval",
    )
    git.run_git_checked(root, *identity, "add", "-A")
    git.run_git_checked(root, *identity, "commit", "-q", "-m", "baseline")
