"""Управление git pre-commit хуком для автоматического ревью staged-изменений."""

from __future__ import annotations

import shlex
import stat
import sys
from pathlib import Path

from ..core.errors import HookError
from . import git

HOOK_NAME = "pre-commit"
MARKER = "# code-reviewer-hook"


def render_hook_script(python_executable: str | None = None) -> str:
    py = shlex.quote(python_executable or sys.executable)
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "# Установлен командой `code-reviewer install-hook`.\n"
        "# Удалить: code-reviewer install-hook --uninstall\n"
        "if command -v code-reviewer >/dev/null 2>&1; then\n"
        '  exec code-reviewer check --quiet "$@"\n'
        "fi\n"
        f'exec {py} -m code_reviewer check --quiet "$@"\n'
    )


def hook_path(root: Path) -> Path | None:
    directory = git.hooks_dir(root)
    return directory / HOOK_NAME if directory is not None else None


def is_hook_installed(root: Path) -> bool:
    path = hook_path(root)
    if path is None or not path.is_file():
        return False
    return MARKER in path.read_text(encoding="utf-8", errors="replace")


def install_hook(root: Path, *, force: bool = False) -> Path:
    path = hook_path(root)
    if path is None:
        raise HookError("не удалось найти каталог hooks: убедитесь, что это git-репозиторий")

    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARKER not in text and not force:
            raise HookError(
                f"хук {path} уже существует и создан не code-reviewer. "
                "Используйте --force, чтобы перезаписать."
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_hook_script(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def uninstall_hook(root: Path) -> bool:
    path = hook_path(root)
    if path is None or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if MARKER not in text:
        raise HookError(f"хук {path} создан не code-reviewer — оставлен без изменений")
    path.unlink()
    return True
