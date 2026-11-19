from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_reviewer.cli.app import app
from code_reviewer.vcs.diff import DiffScope, collect_diff

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git binary not installed",
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_real_git_init_diff_scopes_and_hook(project: Path, monkeypatch):
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "initial")

    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert ".code-reviewer/" in (project / ".gitignore").read_text(encoding="utf-8")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-q", "-m", "track gitignore")

    (project / "pkg/utils.py").write_text("def helper() -> int:\n    return 2\n", encoding="utf-8")
    unstaged = collect_diff(project, scope=DiffScope.AUTO)
    assert unstaged.changed_paths == ["pkg/utils.py"]

    _git(project, "add", "pkg/utils.py")
    staged = collect_diff(project, scope=DiffScope.AUTO)
    assert staged.scope == "staged-изменения"
    _git(project, "commit", "-q", "-m", "second")

    last_diff = collect_diff(project, scope=DiffScope.LAST)
    assert last_diff.changed_paths == ["pkg/utils.py"]

    range_diff = collect_diff(project, scope=DiffScope.RANGE, range_="HEAD~1..HEAD")
    assert range_diff.changed_paths == ["pkg/utils.py"]

    assert runner.invoke(app, ["install-hook"]).exit_code == 0
    hook = project / ".git" / "hooks" / "pre-commit"
    fake_bin = project / "fake-bin"
    fake_bin.mkdir()
    log = project / "hook.log"
    fake = fake_bin / "code-reviewer"
    fake.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$CODE_REVIEWER_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{fake_bin}:{os.environ.get('PATH', '')}",
        CODE_REVIEWER_LOG=str(log),
    )
    proc = subprocess.run([str(hook)], cwd=project, env=env, capture_output=True, text=True)
    assert proc.returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == ["check", "--quiet"]
