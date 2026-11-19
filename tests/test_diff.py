from __future__ import annotations

from pathlib import Path

from code_reviewer.vcs.diff import (
    DiffScope,
    collect_diff,
    parse_diff,
    synthetic_diff_from_maps,
)

SAMPLE = """diff --git a/a b.py b/a b.py
--- a/a b.py
+++ b/a b.py
@@ -1,3 +1,4 @@
 x = 1
-y = 2
+y = 3
+z = 4
"""

ADDED = """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+def f():
+    return 1
"""

DELETED = """diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""


def test_parse_diff_handles_spaces_added_deleted_and_truncation():
    files = parse_diff(SAMPLE)
    assert len(files) == 1
    assert files[0].path == "a b.py"
    assert files[0].additions == 2
    assert files[0].deletions == 1
    assert files[0].changed_lines == {2, 3}

    added = parse_diff(ADDED)
    assert added[0].status == "added"
    assert added[0].additions == 2

    deleted = parse_diff(DELETED)
    assert deleted[0].status == "deleted"

    truncated = parse_diff(SAMPLE, max_lines=3)
    assert truncated[0].truncated is True
    assert "обрезан" in truncated[0].patch


def test_synthetic_diff_from_maps():
    diffset = synthetic_diff_from_maps(
        {"a.py": "x = 1\n", "gone.py": "y = 1\n"},
        {"a.py": "x = 2\n", "new.py": "z = 3\n"},
        scope="тест",
    )
    assert diffset.changed_paths == ["a.py", "new.py"]
    assert diffset.additions == 2
    assert diffset.deletions == 1


def test_collect_diff_supports_all_scopes_including_range(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run_git_checked(cwd, *args: str, timeout: int = 30) -> str:
        calls.append(args)
        if args[-1] == "--cached":
            return ""
        return ADDED

    monkeypatch.setattr("code_reviewer.vcs.diff.git.run_git_checked", fake_run_git_checked)
    monkeypatch.setattr("code_reviewer.vcs.diff.git.head_commit", lambda root: "deadbeef1234")
    monkeypatch.setattr("code_reviewer.vcs.diff.git.untracked_files", lambda root: [])

    res_range = collect_diff(tmp_path, scope=DiffScope.RANGE, range_="main..HEAD")
    assert res_range.scope == "диапазон main..HEAD"
    assert res_range.ref_for_content == "HEAD"
    assert any(call[-1] == "main..HEAD" for call in calls)

    res_commit = collect_diff(tmp_path, scope=DiffScope.COMMIT, commit="abc123456789")
    assert res_commit.ref_for_content == "abc123456789"

    res_last = collect_diff(tmp_path, scope=DiffScope.LAST)
    assert res_last.ref_for_content == "HEAD"

    res_since = collect_diff(tmp_path, scope=DiffScope.SINCE, since_commit="11112222")
    assert res_since.ref_for_content == "HEAD"
    assert any(call[-1] == "11112222..HEAD" for call in calls)

    res_auto = collect_diff(tmp_path, scope=DiffScope.AUTO)
    assert res_auto.scope == "рабочее дерево"
    assert res_auto.ref_for_content is None
