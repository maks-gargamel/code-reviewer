from __future__ import annotations

from pathlib import Path

from code_reviewer.core.models import Finding
from code_reviewer.review.ignore import (
    IgnoreRules,
    collect_inline_ignores,
    directive_kind,
    filter_findings,
    scan_inline_directives,
)
from code_reviewer.vcs.diff import DiffSet, FileDiff


def _finding(file: str = "app.py", line: int = 1, message: str = "m") -> Finding:
    return Finding(file=file, line=line, severity="warning", category="logic", message=message)


def test_directive_kind_and_full_source_scan():
    assert directive_kind("# code-reviewer: ignore") == "ignore"
    assert directive_kind("x = 1 // code-reviewer: ignore-file") == "ignore-file"
    assert directive_kind("# обычный комментарий") is None

    ignore_file, lines = scan_inline_directives(
        "# code-reviewer: ignore-file\nx = 1\n# code-reviewer: ignore\ny = 2\n"
    )
    assert ignore_file is True
    assert {3, 4} <= lines


def test_ignore_rules_with_dotfiles(tmp_path: Path):
    (tmp_path / ".code-reviewerignore").write_text(
        "# коммент\n.env\n.github/\nvendor/\n*.min.js\n", encoding="utf-8"
    )
    rules = IgnoreRules.load(tmp_path)
    assert rules.matches(".env")
    assert rules.matches(".github/workflows/ci.yml")
    assert rules.matches("vendor/lib/a.py")
    assert rules.matches("static/a.min.js")
    assert not rules.matches("src/env.py")


def test_preexisting_inline_ignore_outside_diff_hunk_is_respected(tmp_path: Path):
    # Регрессионный тест: директива над изменённой строкой не входит в `+`-строки diff.
    (tmp_path / "app.py").write_text(
        "# header\n# code-reviewer: ignore\nrisk = eval(x)\nother = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "skip.py").write_text(
        "# code-reviewer: ignore-file\n\n\n\n\nx = eval(1)\n",
        encoding="utf-8",
    )
    diffset = DiffSet(
        files=[
            FileDiff(path="app.py", patch="@@ -3,1 +3,1 @@\n-risk = 0\n+risk = eval(x)\n"),
            FileDiff(path="skip.py", patch="@@ -6,1 +6,1 @@\n-x = 0\n+x = eval(1)\n"),
        ],
        ref_for_content=None,
    )
    inline = collect_inline_ignores(tmp_path, diffset)
    kept, dropped = filter_findings(
        [_finding("app.py", 3), _finding("app.py", 4), _finding("skip.py", 6)],
        IgnoreRules(),
        inline,
    )
    assert [(f.file, f.line) for f in kept] == [("app.py", 4)]
    assert dropped == 2


def test_collect_inline_ignores_reads_from_git_ref_when_present(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "code_reviewer.review.ignore.git.read_file_at_ref",
        lambda root, ref, path: "# code-reviewer: ignore-file\nx = 1\n" if ref == "HEAD" else None,
    )
    diffset = DiffSet(files=[FileDiff(path="committed.py")], ref_for_content="HEAD")
    inline = collect_inline_ignores(tmp_path, diffset)
    assert inline["committed.py"][0] is True
