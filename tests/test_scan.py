from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_reviewer.cli.app import app
from code_reviewer.core.config import CACHE_DIR, Config, service_dir
from code_reviewer.core.errors import UsageAppError
from code_reviewer.core.storage import ResponseCache, State, cache_key, state_path
from code_reviewer.indexing.builder import build_map
from code_reviewer.llm.keys import add_api_key
from code_reviewer.review.context import build_context
from code_reviewer.review.pipeline import ReviewResult
from code_reviewer.review.prompt import build_prompt
from code_reviewer.review.scan import (
    SCAN_CONFIRM_THRESHOLD,
    collect_repo_diffset,
    estimate_scan,
    is_test_file,
)
from code_reviewer.review.secrets import redact
from code_reviewer.vcs import git
from code_reviewer.vcs.diff import DiffSet, FileDiff

runner = CliRunner()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary not installed")
def test_init_sets_checkpoint_only_when_commits_exist_and_does_not_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    empty_repo = tmp_path / "empty"
    empty_repo.mkdir()
    _git(empty_repo, "init", "-q")
    (empty_repo / "app.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.chdir(empty_repo)
    res_empty = runner.invoke(app, ["init"])
    assert res_empty.exit_code == 0, res_empty.output
    assert State.load(state_path(empty_repo)).last_reviewed_commit is None
    assert "Точка отсчёта" not in res_empty.output

    _git(empty_repo, "add", "app.py")
    _git(empty_repo, "commit", "-q", "-m", "initial")
    commit1 = git.head_commit(empty_repo)
    assert commit1 is not None

    res_first = runner.invoke(app, ["init"])
    assert res_first.exit_code == 0, res_first.output
    assert State.load(state_path(empty_repo)).last_reviewed_commit == commit1
    assert "Точка отсчёта для --since-last" in res_first.output

    (empty_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(empty_repo, "add", "app.py")
    _git(empty_repo, "commit", "-q", "-m", "second")
    commit2 = git.head_commit(empty_repo)
    assert commit2 != commit1

    res_second = runner.invoke(app, ["init"])
    assert res_second.exit_code == 0, res_second.output
    assert State.load(state_path(empty_repo)).last_reviewed_commit == commit1


def test_is_test_file_patterns():
    assert is_test_file("tests/test_app.py") is True
    assert is_test_file("pkg/handler_test.go") is True
    assert is_test_file("src/utils.spec.ts") is True
    assert is_test_file("src/test/java/UserTest.java") is True
    assert is_test_file("OrderTests.cs") is True
    assert is_test_file("ParserSpec.scala") is True
    assert is_test_file("conftest.py") is True
    assert is_test_file("evals/sql_injection.json") is True

    assert is_test_file("src/contest.py") is False
    assert is_test_file("src/request_handler.py") is False
    assert is_test_file("pkg/utils.py") is False


def test_collect_repo_diffset_filters_non_code_tests_paths_ignore_and_limits(project: Path):
    (project / "docs").mkdir()
    (project / "docs" / "notes.md").write_text("# Notes\nLine 2\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_utils.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (project / "pkg" / "ignored_mod.py").write_text("SECRET = 1\n", encoding="utf-8")
    (project / ".code-reviewerignore").write_text("pkg/ignored_mod.py\n", encoding="utf-8")

    repo_map = build_map(project, Config()).repo_map

    full = collect_repo_diffset(project, repo_map)
    full_paths = set(full.changed_paths)
    assert "main.py" in full_paths
    assert "pkg/utils.py" in full_paths
    assert "tests/test_utils.py" in full_paths
    assert "README.md" not in full_paths
    assert "docs/notes.md" not in full_paths
    assert "pkg/ignored_mod.py" not in full_paths

    no_tests = collect_repo_diffset(project, repo_map, include_tests=False)
    assert "tests/test_utils.py" not in no_tests.changed_paths
    assert "без тестов" in no_tests.scope

    only_pkg = collect_repo_diffset(project, repo_map, paths=["pkg"])
    assert all(p.startswith("pkg/") for p in only_pkg.changed_paths)

    explicit_doc = collect_repo_diffset(project, repo_map, paths=["docs/notes.md"])
    assert explicit_doc.changed_paths == ["docs/notes.md"]

    limited = collect_repo_diffset(project, repo_map, max_files=1)
    assert len(limited.files) == 1
    assert len(limited.omitted) >= 1

    with pytest.raises(UsageAppError, match="не найдено"):
        collect_repo_diffset(project, repo_map, paths=["missing_dir"])


def test_estimate_scan_reports_cache_hits_and_keys_consistently(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("CODE_REVIEWER_API_KEY", raising=False)
    fake_secret = "sk-" + "1234567890abcdefghijklmnop"
    (project / "main.py").write_text(
        f'API_KEY = "{fake_secret}"\ndef ok() -> int:\n    return 1\n',
        encoding="utf-8",
    )
    add_api_key(project / ".code-reviewer" / "keys.txt", "AIzaSyScanKeyOne1111")
    add_api_key(project / ".code-reviewer" / "keys.txt", "AIzaSyScanKeyTwo2222")

    cfg = Config()
    cfg.review.max_chunk_tokens = 500
    repo_map = build_map(project, cfg).repo_map
    diffset = collect_repo_diffset(project, repo_map, include_tests=False)

    est1 = estimate_scan(project, cfg, repo_map, diffset)
    assert est1.keys_available == 2
    assert est1.cached_chunks == 0
    assert est1.uncached_requests == len(est1.chunks)
    assert len(est1.chunks) >= 2

    # Записываем ответ первого чанка в кэш (как это делает run_review с redact) и проверяем хит
    first_paths = set(est1.chunks[0].files)
    redacted_files = [
        FileDiff(
            path=item.path,
            status=item.status,
            additions=item.additions,
            deletions=item.deletions,
            patch=redact(item.patch)[0],
        )
        for item in diffset.files
        if item.path in first_paths
    ]
    chunk_diff = DiffSet(files=redacted_files, scope=diffset.scope, omitted=est1.omitted)
    ctx = build_context(project, cfg, repo_map, chunk_diff.changed_paths)
    prompt = build_prompt(cfg, chunk_diff, ctx)
    ResponseCache(service_dir(project) / CACHE_DIR).put(
        cache_key(cfg.llm.model, prompt.system, prompt.user),
        cfg.llm.model,
        '{"findings": []}',
    )

    est2 = estimate_scan(project, cfg, repo_map, diffset)
    assert est2.cached_chunks == 1
    assert est2.uncached_requests == len(est2.chunks) - 1


def test_cli_check_all_dry_run_flags_and_quota_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODE_REVIEWER_API_KEY", raising=False)

    body = "\n".join(f"def fn_{j}(x: int) -> int:\n    return x + {j}\n" for j in range(15))
    for i in range(SCAN_CONFIRM_THRESHOLD + 2):
        (tmp_path / f"mod_{i:02d}.py").write_text(f'"""M{i}."""\n{body}', encoding="utf-8")

    svc = tmp_path / ".code-reviewer"
    svc.mkdir()
    (svc / "config.toml").write_text(
        '[review]\nmax_chunk_tokens = 500\n',
        encoding="utf-8",
    )

    assert runner.invoke(app, ["init"]).exit_code == 0

    assert runner.invoke(app, ["check", "--all", "--last"]).exit_code == 2
    assert runner.invoke(app, ["check", "--path", "mod_00.py"]).exit_code == 2
    assert runner.invoke(app, ["check", "--no-tests"]).exit_code == 2

    dry = runner.invoke(app, ["check", "--all", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "План полного сканирования" in dry.output
    assert "Новых запросов к API: 12" in dry.output
    assert "--max-chunks" in dry.output

    # Без --yes и без TTY защита не даёт молча потратить квоту
    monkeypatch.setattr("code_reviewer.cli.check_cmd._is_interactive", lambda: False)
    blocked = runner.invoke(app, ["check", "--all"])
    assert blocked.exit_code == 2
    assert "порог подтверждения" in blocked.output

    calls: list[dict[str, object]] = []

    def fake_run_review(root, config, repo_map, **kwargs):
        calls.append(kwargs)
        omitted = ["mod_02.py"] if kwargs.get("max_chunks") == 2 else []
        ds = kwargs.get("diffset")
        files = list(ds.files[:2]) if isinstance(ds, DiffSet) else []
        return ReviewResult(
            scope=ds.scope if isinstance(ds, DiffSet) else "весь репозиторий",
            diffset=DiffSet(files=files),
            findings=[],
            omitted=omitted,
        )

    monkeypatch.setattr("code_reviewer.cli.check_cmd.run_review", fake_run_review)
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.head_commit", lambda r: "newhead999")
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.current_branch", lambda r: "main")

    # При интерактивном отказе ("n") запрос не уходит
    monkeypatch.setattr("code_reviewer.cli.check_cmd._is_interactive", lambda: True)
    declined = runner.invoke(app, ["check", "--all"], input="n\n")
    assert declined.exit_code == 0, declined.output
    assert "Отменено" in declined.output
    assert calls == []

    # Явный лимит --max-chunks 2 проходит без --yes, но не сдвигает checkpoint (есть omitted)
    sf = state_path(tmp_path)
    st = State.load(sf)
    st.last_reviewed_commit = "base000"
    st.save(sf)

    limited = runner.invoke(app, ["check", "--all", "--max-chunks", "2"])
    assert limited.exit_code == 0, limited.output
    assert len(calls) == 1
    assert calls[0]["max_chunks"] == 2
    assert State.load(sf).last_reviewed_commit == "base000"

    # Частичный скан по --path тоже не перезаписывает полный checkpoint
    partial = runner.invoke(app, ["check", "--all", "-p", "mod_00.py"])
    assert partial.exit_code == 0, partial.output
    assert State.load(sf).last_reviewed_commit == "base000"

    # Полный скан с --yes сдвигает last_reviewed_commit на HEAD
    confirmed = runner.invoke(app, ["check", "--all", "--yes"])
    assert confirmed.exit_code == 0, confirmed.output
    assert State.load(sf).last_reviewed_commit == "newhead999"

    # Локальный провайдер (Ollama) не требует подтверждения даже при >10 чанках
    (svc / "config.toml").write_text(
        '[llm]\nbase_url = "http://localhost:11434/v1"\n[review]\nmax_chunk_tokens = 500\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("code_reviewer.cli.check_cmd._is_interactive", lambda: False)
    local_res = runner.invoke(app, ["check", "--all"])
    assert local_res.exit_code == 0, local_res.output
