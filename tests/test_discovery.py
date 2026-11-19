from __future__ import annotations

from pathlib import Path

from code_reviewer.indexing.discovery import discover_files


def test_walk_fallback_skips_default_ignored_dirs(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("var x;\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "site.py").write_text("y = 2\n", encoding="utf-8")

    result = discover_files(tmp_path, use_git=False)
    assert result.used_git is False
    assert {item.path for item in result.files} == {"a.py"}


def test_binary_and_large_files_are_skipped(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    (tmp_path / "raw.dat").write_bytes(b"abc\x00def")
    (tmp_path / "huge.py").write_text("x = 1\n" * 200, encoding="utf-8")

    result = discover_files(tmp_path, max_bytes=200, use_git=False)
    paths = {item.path for item in result.files}
    assert paths == {"app.py"}
    assert result.skipped_binary == 2
    assert result.skipped_large == 1


def test_extra_ignore_patterns_including_dotfiles(tmp_path: Path):
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "env").write_text("not dotfile\n", encoding="utf-8")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001.py").write_text("z = 3\n", encoding="utf-8")

    result = discover_files(
        tmp_path,
        ignore=[".env", "migrations/"],
        use_git=False,
    )
    paths = {item.path for item in result.files}
    assert paths == {"env", "keep.py"}
    assert result.skipped_ignored == 2


def test_git_visible_files_used_when_available(tmp_path: Path, monkeypatch):
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "unlisted.py").write_text("y = 2\n", encoding="utf-8")

    monkeypatch.setattr(
        "code_reviewer.indexing.discovery.git.visible_files",
        lambda root: ["tracked.py"],
    )

    result = discover_files(tmp_path, use_git=True)
    assert result.used_git is True
    assert [item.path for item in result.files] == ["tracked.py"]
