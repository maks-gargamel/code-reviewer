from __future__ import annotations

from pathlib import Path

import pytest

from code_reviewer.core.config import Config, unknown_config_keys
from code_reviewer.core.errors import ConfigError


def test_default_toml_matches_config_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = Config()
    cfg.write_default(tmp_path)

    loaded = Config.load(tmp_path)
    assert loaded == cfg
    assert unknown_config_keys(tmp_path) == []


def test_global_and_project_config_deep_merge(tmp_path: Path, monkeypatch):
    xdg = tmp_path / "xdg"
    global_cfg = xdg / "code-reviewer" / "config.toml"
    global_cfg.parent.mkdir(parents=True)
    global_cfg.write_text(
        '[llm]\nmodel = "global-model"\ntemperature = 0.3\n[review]\njobs = 4\n',
        encoding="utf-8",
    )

    project_cfg = tmp_path / ".code-reviewer" / "config.toml"
    project_cfg.parent.mkdir(parents=True)
    project_cfg.write_text(
        '[llm]\nmodel = "project-model"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    loaded = Config.load(tmp_path)

    assert loaded.llm.model == "project-model"
    assert loaded.llm.temperature == 0.3
    assert loaded.review.jobs == 4


def test_invalid_toml_raises_config_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg_path = tmp_path / ".code-reviewer" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("[llm\nbroken = ", encoding="utf-8")

    with pytest.raises(ConfigError, match="не удалось прочитать"):
        Config.load(tmp_path)


def test_invalid_values_raise_config_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg_path = tmp_path / ".code-reviewer" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("[llm]\ntemperature = 9.5\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="некорректная конфигурация"):
        Config.load(tmp_path)


def test_unknown_config_keys_reports_typos(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg_path = tmp_path / ".code-reviewer" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        '[llm]\nmodell = "typo"\n[extra_section]\na = 1\n',
        encoding="utf-8",
    )

    problems = unknown_config_keys(tmp_path)
    assert any("modell" in p for p in problems)
    assert any("extra_section" in p for p in problems)


def test_is_local_llm_detection():
    cfg = Config()
    assert cfg.is_local_llm() is False

    cfg.llm.provider = "ollama"
    assert cfg.is_local_llm() is True

    cfg.llm.provider = "openai-compatible"
    cfg.llm.base_url = "http://localhost:11434/v1"
    assert cfg.is_local_llm() is True

    cfg.llm.base_url = "http://127.0.0.1:8080/v1"
    assert cfg.is_local_llm() is True

    cfg.llm.base_url = "https://api.deepseek.com/v1"
    assert cfg.is_local_llm() is False
