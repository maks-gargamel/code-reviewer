from __future__ import annotations

import json

from code_reviewer.core.models import Finding, finding_id, normalize_severity
from code_reviewer.review.findings import filter_to_paths, parse_findings


def test_parse_object_array_and_single_finding():
    obj_raw = json.dumps(
        {
            "findings": [
                {
                    "file": "src/app.py",
                    "line": 42,
                    "severity": "warning",
                    "category": "logic",
                    "message": "Широкое исключение",
                    "suggestion": "Сузить до ValueError",
                }
            ]
        },
        ensure_ascii=False,
    )
    assert len(parse_findings(obj_raw)) == 1

    arr_raw = '[{"file": "a.py", "line": 1, "message": "x"},]'
    assert len(parse_findings(arr_raw)) == 1

    single_raw = """```json
    {"file": "./app.py", "line": "7", "severity": "critical", "message": "SQLi"}
    ```"""
    single = parse_findings(single_raw)
    assert len(single) == 1
    assert single[0].file == "app.py"
    assert single[0].line == 7
    assert single[0].severity == "error"


def test_parse_garbage_and_dedupe():
    assert parse_findings("не json") == []
    dup = json.dumps(
        [
            {"file": "a.py", "line": 1, "message": "повтор"},
            {"file": "a.py", "line": 1, "message": "повтор"},
        ]
    )
    assert len(parse_findings(dup)) == 1


def test_filter_to_paths_rewrites_canonical_path_and_id_and_rejects_hallucinations():
    findings = [
        Finding(file="utils.py", line=5, severity="warning", category="logic", message="m"),
        Finding(file="ghost/app.py", line=1, severity="error", category="bug", message="fake"),
        Finding(file="other.py", line=1, severity="error", category="bug", message="missing"),
    ]
    filtered = filter_to_paths(findings, ["pkg/utils.py", "app.py"])
    assert len(filtered) == 1
    assert filtered[0].file == "pkg/utils.py"
    assert filtered[0].id == finding_id("pkg/utils.py", 5, "m")


def test_filter_to_paths_rejects_ambiguous_suffix():
    findings = [
        Finding(file="utils.py", line=1, severity="warning", category="logic", message="m")
    ]
    assert filter_to_paths(findings, ["a/utils.py", "b/utils.py"]) == []


def test_severity_aliases():
    assert normalize_severity("critical") == "error"
    assert normalize_severity("minor") == "info"
    assert normalize_severity("unknown") == "warning"
