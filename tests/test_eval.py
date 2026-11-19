from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_reviewer.core.config import Config
from code_reviewer.core.models import Finding
from code_reviewer.workflows.eval import (
    Case,
    CaseResult,
    EvalReport,
    Expected,
    load_cases,
    match_findings,
    run_case,
    run_eval,
)

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


class StubClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.response


def _response(file: str, line: int, message: str = "Проблема") -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "file": file,
                    "line": line,
                    "severity": "warning",
                    "category": "logic",
                    "message": message,
                }
            ]
        },
        ensure_ascii=False,
    )


def _case() -> Case:
    return Case(
        name="demo",
        files={"app.py": "x = 1\n"},
        changes={"app.py": "x = 1\ny = 2\n"},
        expected=[Expected(file="app.py", line=2, category="logic")],
    )


def test_load_cases_and_broken_json(tmp_path: Path):
    payload = {
        "name": "demo",
        "files": {"a.py": "x = 1\n"},
        "changes": {"a.py": "x = 2\n"},
        "expected": [{"file": "a.py", "line": 1, "category": "bug"}],
    }
    (tmp_path / "demo.json").write_text(json.dumps(payload), encoding="utf-8")
    cases = load_cases(tmp_path)
    assert [c.name for c in cases] == ["demo"]

    (tmp_path / "broken.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.json"):
        load_cases(tmp_path)


def test_match_findings_picks_minimum_distance_pair():
    # Регрессионный тест: жадный first-match раньше давал 1/2 вместо оптимальных 2/2.
    expected = [Expected(file="a.py", line=10), Expected(file="a.py", line=13)]
    findings = [
        Finding(file="a.py", line=12, severity="warning", category="logic", message="m1"),
        Finding(file="a.py", line=9, severity="warning", category="logic", message="m2"),
    ]
    matched, missed, extra = match_findings(expected, findings, tolerance=2)
    assert matched == 2
    assert missed == []
    assert extra == []


def test_eval_report_precision_is_zero_when_no_predictions_on_bugs():
    # Регрессионный тест: модель вернула [] на всех дефектных кейсах -> precision = 0.0, а не 1.0.
    report = EvalReport(
        cases=[
            CaseResult(name="a", matched=0, expected=2, extra=[]),
            CaseResult(name="b", matched=0, expected=1, extra=[]),
        ]
    )
    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0


def test_run_case_and_run_eval_without_requiring_git():
    client = StubClient(_response("app.py", 2))
    res = run_case(_case(), Config(), client, use_cache=False, has_git=False)
    assert res.error == ""
    assert res.matched == 1
    assert res.precision == 1.0
    assert res.recall == 1.0

    report = run_eval([_case()], Config(), client, use_cache=False)
    assert report.f1 == 1.0


def test_all_shipped_eval_cases_are_well_formed_and_pass_with_oracle():
    cases = load_cases(EVALS_DIR)
    assert len(cases) == 22

    for case in cases:
        assert case.files and case.changes and case.expected, case.name
        for item in case.expected:
            assert item.file in case.changes, f"{case.name}: {item.file}"
            total = len(case.changes[item.file].splitlines())
            assert 1 <= item.line <= total, f"{case.name}: строка {item.line}"

        oracle_response = json.dumps(
            {
                "findings": [
                    {
                        "file": item.file,
                        "line": item.line,
                        "severity": item.severity or "warning",
                        "category": item.category or "logic",
                        "message": f"Оракл: {case.name}",
                    }
                    for item in case.expected
                ]
            },
            ensure_ascii=False,
        )
        result = run_case(
            case,
            Config(),
            StubClient(oracle_response),
            use_cache=False,
            has_git=False,
        )
        assert result.error == "", f"{case.name}: {result.error}"
        assert result.recall == 1.0, f"{case.name}: missed {result.missed}"
        assert result.precision == 1.0, f"{case.name}: extra {result.extra}"
