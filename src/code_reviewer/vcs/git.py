"""Обёртка над git CLI через subprocess."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..core.errors import GitError

DEFAULT_TIMEOUT = 30


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


def run_git(
    cwd: str | Path,
    *args: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Запустить git CLI и вернуть CompletedProcess (при отсутствии git поднимает GitError)."""
    try:
        return subprocess.run(
            ["git", "--no-pager", "-c", "core.quotepath=false", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise GitError("git не установлен или не найден в PATH") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError(f"не удалось выполнить git: {exc}") from exc


def run_git_checked(
    cwd: str | Path,
    *args: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Запустить git CLI и вернуть stdout или поднять GitError при ошибке."""
    result = run_git(cwd, *args, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"код выхода {result.returncode}"
        raise GitError(detail)
    return result.stdout


def is_git_available() -> bool:
    """Проверить, установлен ли git в системе."""
    try:
        return run_git(Path.cwd(), "--version").returncode == 0
    except GitError:
        return False


def find_repo_root(start: str | Path) -> Path | None:
    """Найти корень git-репозитория для каталога `start`."""
    try:
        result = run_git(start, "rev-parse", "--show-toplevel")
    except GitError:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return Path(out) if out else None


def head_commit(root: str | Path) -> str | None:
    """Хэш HEAD-коммита или None, если коммитов ещё нет / не git-репозиторий."""
    try:
        result = run_git(root, "rev-parse", "HEAD")
    except GitError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def current_branch(root: str | Path) -> str | None:
    """Текущая ветка git или короткое имя для detached HEAD."""
    try:
        result = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    except GitError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def visible_files(root: str | Path) -> list[str] | None:
    """Все отслеживаемые и неигнорируемые новые файлы с учётом .gitignore."""
    try:
        result = run_git(
            root,
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        )
    except GitError:
        return None
    if result.returncode != 0:
        return None
    return [item for item in result.stdout.split("\0") if item]


def untracked_files(root: str | Path) -> list[str]:
    """Новые (неотслеживаемые) неигнорируемые файлы рабочего дерева."""
    try:
        result = run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    except GitError:
        return []
    if result.returncode != 0:
        return []
    return [item for item in result.stdout.split("\0") if item]


def read_file_at_ref(root: str | Path, ref: str, relative_path: str) -> str | None:
    """Прочитать содержимое файла на указанном git ref (например `HEAD` или `:0` для индекса)."""
    spec = f":0:{relative_path}" if ref == ":0" else f"{ref}:{relative_path}"
    try:
        result = run_git(root, "show", spec)
    except GitError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def hooks_dir(root: str | Path) -> Path | None:
    """Каталог git hooks с учётом worktree и кастомного core.hooksPath."""
    try:
        result = run_git(root, "rev-parse", "--git-path", "hooks")
    except GitError:
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (Path(root) / path).resolve()
    return path
