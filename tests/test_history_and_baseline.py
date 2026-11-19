from __future__ import annotations

from pathlib import Path

from code_reviewer.core.models import Finding
from code_reviewer.core.storage import (
    Baseline,
    last_review_path,
    load_baseline,
    load_last_review,
    save_baseline,
    save_last_review,
)
from code_reviewer.review.history import (
    HistoryEntry,
    append_entry,
    compute_metrics,
    history_path,
    load_history,
)
from code_reviewer.review.pipeline import compute_verify


def _finding(file: str = "app.py", line: int = 1, message: str = "m") -> Finding:
    return Finding(file=file, line=line, severity="warning", category="logic", message=message)


def test_baseline_and_last_review_roundtrip(tmp_path: Path):
    findings = [_finding(message="a"), _finding(file="b.py", message="b")]

    saved = save_baseline(tmp_path, findings, commit="abc123")
    loaded_baseline = load_baseline(tmp_path)
    assert loaded_baseline.ids == saved.ids == {f.id for f in findings}
    assert loaded_baseline.commit == "abc123"

    save_last_review(tmp_path, findings, commit="abc123")
    loaded_last = load_last_review(tmp_path)
    assert [(f.file, f.id) for f in loaded_last] == [(f.file, f.id) for f in findings]

    broken = tmp_path / ".code-reviewer" / "baseline.json"
    broken.write_text("{ не json", encoding="utf-8")
    assert load_baseline(tmp_path).ids == set()

    lpath = last_review_path(tmp_path)
    lpath.write_text(
        '{"findings": [{"file": "", "message": "нет"}, '
        '{"file": "a.py", "line": 1, "message": "ок", "severity": "warning"}]}',
        encoding="utf-8",
    )
    assert [f.file for f in load_last_review(tmp_path)] == ["a.py"]

    payload = Baseline(ids={"b", "a"}, created_at="now", commit="c").to_dict()
    assert payload["ids"] == ["a", "b"]


def test_compute_verify():
    f1 = _finding(message="fixed")
    f2 = _finding(message="kept")
    summary = compute_verify([f1, f2], [f2])
    assert [f.id for f in summary.closed] == [f1.id]
    assert [f.id for f in summary.still_open] == [f2.id]


def test_history_append_load_and_broken_lines(tmp_path: Path):
    f = _finding()
    append_entry(
        tmp_path,
        commit="abc123",
        branch="main",
        scope="рабочее дерево",
        changed_files=["app.py"],
        chunks=1,
        cached_chunks=0,
        duration=0.5,
        suppressed=1,
        redactions=0,
        findings=[f.to_dict()],
    )
    with history_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("{ broken json }\n")

    entries = load_history(tmp_path)
    assert len(entries) == 1
    assert entries[0].commit == "abc123"
    assert entries[0].changed_files == ["app.py"]
    assert entries[0].severity_counts == {"error": 0, "warning": 1, "info": 0}


def test_compute_metrics_acceptance_respects_changed_files():
    # Регрессионный тест: при смене файла прошлые замечания не считаются исправленными.
    prev_a = _finding(file="a.py", message="баг в a")
    curr_b = _finding(file="b.py", message="баг в b")
    switched = [
        HistoryEntry(
            created_at="1",
            scope="staged",
            changed_files=["a.py"],
            findings=[prev_a.to_dict()],
        ),
        HistoryEntry(
            created_at="2",
            scope="staged",
            changed_files=["b.py"],
            findings=[curr_b.to_dict()],
        ),
    ]
    metrics_switched = compute_metrics(switched)
    assert metrics_switched.last_fixed == 0
    assert metrics_switched.acceptance == 0.0

    # А при повторной проверке того же файла исправленное замечание учитывается корректно.
    keeps = _finding(file="a.py", message="осталось")
    fixed = _finding(file="a.py", message="исправлено")
    same_file = [
        HistoryEntry(
            created_at="1",
            scope="staged",
            changed_files=["a.py"],
            findings=[keeps.to_dict(), fixed.to_dict()],
        ),
        HistoryEntry(
            created_at="2",
            scope="staged",
            changed_files=["a.py"],
            findings=[keeps.to_dict()],
        ),
    ]
    metrics_same = compute_metrics(same_file)
    assert metrics_same.last_fixed == 1
    assert metrics_same.acceptance == 0.5
