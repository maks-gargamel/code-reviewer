from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from code_reviewer.cli.app import app
from code_reviewer.core.models import Finding
from code_reviewer.core.storage import State, state_path
from code_reviewer.review.pipeline import ReviewResult
from code_reviewer.vcs.diff import DiffScope, DiffSet, FileDiff

runner = CliRunner()


def test_version_and_demo():
    ver = runner.invoke(app, ["--version"])
    assert ver.exit_code == 0
    assert "code-reviewer, version" in ver.output

    demo = runner.invoke(app, ["demo"])
    assert demo.exit_code == 0, demo.output
    assert "Демонстрация code-reviewer" in demo.output
    assert "Запросов к API: 0" in demo.output
    assert "SQL-инъекция" in demo.output


def test_init_status_and_map_commands(project: Path, monkeypatch):
    monkeypatch.chdir(project)

    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0, first.output
    assert (project / ".code-reviewer" / "map.json").is_file()
    assert (project / ".code-reviewer" / "config.toml").is_file()
    assert "Карта репозитория построена" in first.output

    second = runner.invoke(app, ["init"])
    assert second.exit_code == 0
    assert "из кэша" in second.output

    status_json = runner.invoke(app, ["status", "--json"])
    assert status_json.exit_code == 0
    payload = json.loads(status_json.output)
    assert payload["map"]["files"] >= 5
    assert payload["map"]["stale"] == []

    deps = runner.invoke(app, ["map", "deps", "pkg/utils.py"])
    assert deps.exit_code == 0
    assert "main.py" in deps.output

    outline = runner.invoke(app, ["map", "outline", "pkg/models.py"])
    assert outline.exit_code == 0
    assert "class Model" in outline.output

    files = runner.invoke(app, ["map", "files", "--lang", "python"])
    assert files.exit_code == 0
    assert "main.py" in files.output

    cycles = runner.invoke(app, ["map", "cycles"])
    assert cycles.exit_code == 0
    assert "Циклических зависимостей нет" in cycles.output

    search = runner.invoke(app, ["map", "search", "helper"])
    assert search.exit_code == 0
    assert "pkg/utils.py" in search.output

    search_brackets = runner.invoke(app, ["map", "search", "str]"])
    assert search_brackets.exit_code == 0

    exported = runner.invoke(app, ["map", "export", "--output", "graph.dot"])
    assert exported.exit_code == 0
    assert (project / "graph.dot").read_text(encoding="utf-8").startswith("digraph repo {")


def test_cache_config_history_and_doctor(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    monkeypatch.delenv("CODE_REVIEWER_API_KEY", raising=False)
    runner.invoke(app, ["init"])

    cache_dir = project / ".code-reviewer" / "cache"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "item.json").write_text("{}", encoding="utf-8")
    c_json = runner.invoke(app, ["cache", "--json"])
    assert json.loads(c_json.output)["responses"]["files"] == 1
    assert runner.invoke(app, ["cache", "--clean"]).exit_code == 0
    assert not cache_dir.exists()

    cfg_show = runner.invoke(app, ["config", "show", "--json"])
    assert cfg_show.exit_code == 0
    assert "llm" in json.loads(cfg_show.output)
    assert runner.invoke(app, ["config", "check"]).exit_code == 0

    key_file = project / ".code-reviewer" / "keys.txt"
    key_file.write_text("secret-one\nsecret-two\n", encoding="utf-8")
    doc = runner.invoke(app, ["doctor"])
    assert doc.exit_code == 0, doc.output
    assert "2 шт." in doc.output
    assert "secret-one" not in doc.output

    hist_empty = runner.invoke(app, ["history"])
    assert hist_empty.exit_code == 2
    assert "История пуста" in hist_empty.output


def test_check_range_flag_reaches_run_review_and_quiet_is_silent_when_clean(
    project: Path,
    monkeypatch,
):
    monkeypatch.chdir(project)
    runner.invoke(app, ["init"])
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.head_commit", lambda r: "newhead123456")
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.current_branch", lambda r: "main")

    # Изначально зафиксируем last_reviewed_commit = "prev1234"
    sf = state_path(project)
    st = State.load(sf)
    st.last_reviewed_commit = "prev1234"
    st.save(sf)

    seen_calls: list[dict[str, object]] = []

    def fake_run_review(root, config, repo_map, **kwargs):
        seen_calls.append(kwargs)
        return ReviewResult(
            scope=f"диапазон {kwargs.get('range_')}",
            diffset=DiffSet(files=[FileDiff(path="pkg/utils.py", additions=1)]),
            findings=[],
        )

    monkeypatch.setattr("code_reviewer.cli.check_cmd.run_review", fake_run_review)

    # Регрессионный тест 1: --range передаёт DiffScope.RANGE и range_="HEAD~2..HEAD".
    # Регрессионный тест 2: --quiet при 0 замечаниях ничего не печатает.
    res = runner.invoke(app, ["check", "--range", "HEAD~2..HEAD", "--quiet"])
    assert res.exit_code == 0, res.output
    assert res.output == ""
    assert seen_calls[0]["scope"] == DiffScope.RANGE
    assert seen_calls[0]["range_"] == "HEAD~2..HEAD"

    # Регрессионный тест 3: --range не должен перезаписывать last_reviewed_commit.
    assert State.load(sf).last_reviewed_commit == "prev1234"


def test_check_failure_and_commit_scope_do_not_advance_last_reviewed_commit(
    project: Path,
    monkeypatch,
):
    monkeypatch.chdir(project)
    runner.invoke(app, ["init"])
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.head_commit", lambda r: "head9999")
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.current_branch", lambda r: "main")

    sf = state_path(project)
    st = State.load(sf)
    st.last_reviewed_commit = "base0000"
    st.save(sf)

    def failing_review(root, config, repo_map, **kwargs):
        return ReviewResult(
            scope="рабочее дерево",
            diffset=DiffSet(files=[FileDiff(path="pkg/utils.py", additions=1)]),
            findings=[
                Finding(
                    file="pkg/utils.py",
                    line=1,
                    severity="error",
                    category="bug",
                    message="Ошибка",
                )
            ],
        )

    monkeypatch.setattr("code_reviewer.cli.check_cmd.run_review", failing_review)
    res_fail = runner.invoke(app, ["check"])
    assert res_fail.exit_code == 1
    assert State.load(sf).last_reviewed_commit == "base0000"

    def clean_review(root, config, repo_map, **kwargs):
        return ReviewResult(
            scope="коммит old111",
            diffset=DiffSet(files=[FileDiff(path="pkg/utils.py", additions=1)]),
            findings=[],
        )

    monkeypatch.setattr("code_reviewer.cli.check_cmd.run_review", clean_review)
    res_commit = runner.invoke(app, ["check", "--commit", "old111"])
    assert res_commit.exit_code == 0
    assert State.load(sf).last_reviewed_commit == "base0000"

    res_last = runner.invoke(app, ["check", "--last"])
    assert res_last.exit_code == 0
    assert State.load(sf).last_reviewed_commit == "head9999"


def test_check_baseline_verify_and_mark_reviewed(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    runner.invoke(app, ["init"])
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.head_commit", lambda r: "abc123456789")
    monkeypatch.setattr("code_reviewer.cli.check_cmd.git.current_branch", lambda r: "main")

    finding = Finding(
        file="pkg/utils.py",
        line=1,
        severity="warning",
        category="logic",
        message="Старое замечание",
    )

    def fake_review(root, config, repo_map, **kwargs):
        baseline_ids = kwargs.get("baseline_ids")
        filtered = [] if baseline_ids and finding.id in baseline_ids else [finding]
        return ReviewResult(
            scope="рабочее дерево",
            diffset=DiffSet(files=[FileDiff(path="pkg/utils.py", additions=1)]),
            findings=filtered,
        )

    monkeypatch.setattr("code_reviewer.cli.check_cmd.run_review", fake_review)

    saved = runner.invoke(app, ["check", "--save-baseline"])
    assert saved.exit_code == 0
    assert "Baseline сохранён" in saved.output

    verified = runner.invoke(app, ["check", "--verify", "--baseline"])
    assert verified.exit_code == 0
    assert "Замечаний нет" in verified.output
    assert "закрыто: 1" in verified.output

    hist = runner.invoke(app, ["history", "--json"])
    assert hist.exit_code == 0
    assert len(json.loads(hist.output)["entries"]) == 2

    mr = runner.invoke(app, ["mark-reviewed"])
    assert mr.exit_code == 0
    assert "Отмечено как проверенное" in mr.output


def test_install_hook_with_mocked_git_hooks_dir(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    hooks = project / ".git" / "hooks"
    monkeypatch.setattr("code_reviewer.vcs.hooks.git.hooks_dir", lambda r: hooks)

    res = runner.invoke(app, ["install-hook"])
    assert res.exit_code == 0, res.output
    hook = hooks / "pre-commit"
    assert hook.is_file()
    assert "code-reviewer check --quiet" in hook.read_text(encoding="utf-8")

    # Чужой хук без --force не перезаписывается
    hook.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    refused = runner.invoke(app, ["install-hook"])
    assert refused.exit_code == 2
    assert "--force" in refused.output

    forced = runner.invoke(app, ["install-hook", "--force"])
    assert forced.exit_code == 0
    removed = runner.invoke(app, ["install-hook", "--uninstall"])
    assert removed.exit_code == 0
    assert not hook.exists()


def test_render_hook_script_handles_python_and_frozen_binary():
    from code_reviewer.vcs.hooks import render_hook_script

    py_script = render_hook_script("/opt/venv/bin/python", frozen=False)
    assert 'exec /opt/venv/bin/python -m code_reviewer check --quiet "$@"' in py_script

    bin_script = render_hook_script("/usr/local/bin/code-reviewer", frozen=True)
    assert 'exec /usr/local/bin/code-reviewer check --quiet "$@"' in bin_script
    assert "-m code_reviewer" not in bin_script
