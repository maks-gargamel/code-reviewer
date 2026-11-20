from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_reviewer.cli.app import app
from code_reviewer.core.config import Config
from code_reviewer.core.errors import UsageAppError
from code_reviewer.llm.keys import (
    add_api_key,
    clear_api_keys,
    list_key_entries,
    mask_key,
    read_api_keys,
    remove_api_key,
)

runner = CliRunner()


def test_mask_key_bounds():
    assert mask_key("") == "****"
    assert mask_key("short") == "****"
    assert mask_key("12345678") == "****"

    assert mask_key("123456789") == "12…89"
    assert mask_key("abcdefghijkl") == "ab…kl"

    secret = "AIzaSy1234567890ABCD"
    masked = mask_key(secret)
    assert masked == "AIza…ABCD"
    assert secret not in masked


def test_add_remove_clear_api_keys_file_and_permissions(tmp_path: Path):
    key_file = tmp_path / ".code-reviewer" / "keys.txt"

    added, masked, total = add_api_key(
        key_file,
        "AIzaSySecretKey111111",
        comment="  gemini   free  ",
    )
    assert added is True
    assert masked == "AIza…1111"
    assert total == 1
    assert key_file.is_file()

    if os.name != "nt":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    content = key_file.read_text(encoding="utf-8")
    assert content.startswith("# Ключи API")
    assert "AIzaSySecretKey111111 # gemini free\n" in content

    # Повторное добавление того же ключа (с пробелами по краям) не создаёт дубликат
    dup_added, dup_masked, dup_total = add_api_key(key_file, "  AIzaSySecretKey111111  ")
    assert dup_added is False
    assert dup_masked == "AIza…1111"
    assert dup_total == 1

    # Сохраняем пользовательские комментарии и пустые строки при добавлении/удалении
    add_api_key(key_file, "sk-second-key-222222", comment="work")
    text = key_file.read_text(encoding="utf-8")
    key_file.write_text(f"{text}\n# личная заметка\n", encoding="utf-8")
    add_api_key(key_file, "sk-third-key-333333")

    removed_masked, remaining = remove_api_key(key_file, 2)
    assert removed_masked == "sk-s…2222"
    assert remaining == 2
    updated_text = key_file.read_text(encoding="utf-8")
    assert "# личная заметка" in updated_text
    assert read_api_keys(key_file) == ["AIzaSySecretKey111111", "sk-third-key-333333"]

    for bad_key in ("", "   ", "has space", "line1\nline2", "#comment-looking"):
        with pytest.raises(UsageAppError):
            add_api_key(key_file, bad_key)

    for bad_index in (0, 3, -1):
        with pytest.raises(UsageAppError):
            remove_api_key(key_file, bad_index)

    with pytest.raises(UsageAppError):
        remove_api_key(tmp_path / "missing.txt", 1)

    only_comments = tmp_path / "comments-only.txt"
    only_comments.write_text("# header\n\n", encoding="utf-8")
    with pytest.raises(UsageAppError):
        remove_api_key(only_comments, 1)

    existed, cleared = clear_api_keys(key_file)
    assert existed is True
    assert cleared == 2
    assert not key_file.exists()

    existed_again, cleared_again = clear_api_keys(key_file)
    assert existed_again is False
    assert cleared_again == 0


def test_list_key_entries_merges_file_and_env_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    key_file = tmp_path / ".code-reviewer" / "keys.txt"
    key_file.parent.mkdir(parents=True)
    key_file.write_text(
        "secret-alpha-1111 # primary\nsecret-beta-2222\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODE_REVIEWER_API_KEY", "secret-beta-2222, secret-gamma-3333")

    entries, resolved_file, env_name, total_unique = list_key_entries(Config(), tmp_path)
    assert resolved_file == key_file
    assert env_name == "CODE_REVIEWER_API_KEY"
    assert total_unique == 3
    assert len(entries) == 4
    assert [e.index for e in entries] == [1, 2, None, None]
    assert [e.source for e in entries] == ["file", "file", "env", "env"]
    assert entries[0].comment == "primary"

    serialized = json.dumps([e.masked for e in entries])
    for raw in ("secret-alpha-1111", "secret-beta-2222", "secret-gamma-3333"):
        assert raw not in serialized


def test_cli_keys_workflow(project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(project)
    monkeypatch.delenv("CODE_REVIEWER_API_KEY", raising=False)

    empty_res = runner.invoke(app, ["keys", "list"])
    assert empty_res.exit_code == 0, empty_res.output
    assert "Ключи API не настроены" in empty_res.output

    interactive_secret = "AIzaSyInteractive123456"
    arg_secret = "sk-cli-argument-987654"
    stdin_secret = "sk-stdin-key-555555"
    env_secret = "sk-env-secret-777777"

    res_prompt = runner.invoke(app, ["keys", "add"], input=f"{interactive_secret}\n")
    assert res_prompt.exit_code == 0, res_prompt.output
    assert "Ключ добавлен" in res_prompt.output
    assert interactive_secret not in res_prompt.output

    res_arg = runner.invoke(app, ["keys", "add", arg_secret, "-c", "backup"])
    assert res_arg.exit_code == 0, res_arg.output
    assert "Ключ добавлен" in res_arg.output
    assert arg_secret not in res_arg.output

    res_dup = runner.invoke(app, ["keys", "add", arg_secret])
    assert res_dup.exit_code == 0, res_dup.output
    assert "уже есть" in res_dup.output
    assert arg_secret not in res_dup.output

    res_stdin = runner.invoke(app, ["keys", "add", "--stdin"], input=f"{stdin_secret}\n")
    assert res_stdin.exit_code == 0, res_stdin.output
    assert "Ключ добавлен" in res_stdin.output
    assert stdin_secret not in res_stdin.output

    res_conflict = runner.invoke(app, ["keys", "add", "somekey", "--stdin"])
    assert res_conflict.exit_code == 2

    monkeypatch.setenv("CODE_REVIEWER_API_KEY", env_secret)
    res_list = runner.invoke(app, ["keys", "list"])
    assert res_list.exit_code == 0, res_list.output
    assert "Активных уникальных ключей: 4" in res_list.output
    assert "backup" in res_list.output

    res_json = runner.invoke(app, ["keys", "list", "--json"])
    assert res_json.exit_code == 0, res_json.output
    payload = json.loads(res_json.output)
    assert payload["total_unique"] == 4
    assert payload["env"] == "CODE_REVIEWER_API_KEY"
    assert len(payload["keys"]) == 4

    for secret in (interactive_secret, arg_secret, stdin_secret, env_secret):
        assert secret not in res_list.output
        assert secret not in res_json.output

    res_rm = runner.invoke(app, ["keys", "remove", "2"])
    assert res_rm.exit_code == 0, res_rm.output
    assert "Ключ удалён" in res_rm.output
    assert arg_secret not in res_rm.output

    key_file = project / ".code-reviewer" / "keys.txt"
    assert read_api_keys(key_file) == [interactive_secret, stdin_secret]

    res_rm_bad = runner.invoke(app, ["keys", "remove", "99"])
    assert res_rm_bad.exit_code == 2

    res_clear = runner.invoke(app, ["keys", "clear"])
    assert res_clear.exit_code == 0, res_clear.output
    assert "Удалено ключей: 2" in res_clear.output
    assert not key_file.exists()

    res_clear_empty = runner.invoke(app, ["keys", "clear"])
    assert res_clear_empty.exit_code == 0, res_clear_empty.output
    assert "не найден" in res_clear_empty.output


def test_cli_keys_disabled_file_returns_exit_2(project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(project)
    monkeypatch.delenv("CODE_REVIEWER_API_KEY", raising=False)
    cfg_path = project / ".code-reviewer" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('[llm]\napi_key_file = ""\n', encoding="utf-8")

    assert runner.invoke(app, ["keys", "add", "some-secret-key-1234"]).exit_code == 2
    assert runner.invoke(app, ["keys", "remove", "1"]).exit_code == 2
    assert runner.invoke(app, ["keys", "clear"]).exit_code == 2
    assert runner.invoke(app, ["keys", "list"]).exit_code == 0


def test_cli_keys_add_updates_gitignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("code_reviewer.cli.common.git.find_repo_root", lambda p: tmp_path)

    res = runner.invoke(app, ["keys", "add", "AIzaSyGitIgnoreKey999"])
    assert res.exit_code == 0, res.output
    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    assert ".code-reviewer/" in gitignore.read_text(encoding="utf-8")
