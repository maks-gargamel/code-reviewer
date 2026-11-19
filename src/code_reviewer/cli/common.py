"""Общие утилиты и типы для CLI-команд."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from ..core.config import SERVICE_DIR, Config
from ..core.errors import AppError, MapNotFoundError
from ..core.models import RepoMap
from ..core.storage import State, state_path
from ..indexing.builder import build_map, load_map, map_path, stale_files
from ..render.console import _s, console, err_console
from ..vcs import git

GITIGNORE_ENTRY = f"{SERVICE_DIR}/"

RootOption = Annotated[
    Path | None,
    typer.Option(
        "--root",
        exists=True,
        file_okay=False,
        resolve_path=True,
        help="Корень репозитория (по умолчанию — текущая директория или корень git).",
    ),
]


def cli_command[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Перехватить AppError в CLI-команде и завершить процесс с кодом 2 без трейсбека."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except AppError as exc:
            err_console.print(f"[red]Ошибка:[/red] {_s(exc)}")
            raise typer.Exit(code=2) from exc

    return wrapper


def resolve_root(value: Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser().resolve()
    cwd = Path.cwd().resolve()
    return git.find_repo_root(cwd) or cwd


def ensure_gitignore(root: Path) -> str | None:
    repo_root = git.find_repo_root(root)
    if repo_root is None:
        return None
    path = repo_root / ".gitignore"
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        existing = {line.strip().rstrip("/") for line in text.splitlines()}
        if SERVICE_DIR in existing:
            return None
        if text and not text.endswith("\n"):
            text += "\n"
        path.write_text(f"{text}{GITIGNORE_ENTRY}\n", encoding="utf-8")
        return f"в .gitignore добавлена строка {GITIGNORE_ENTRY}"
    path.write_text(f"{GITIGNORE_ENTRY}\n", encoding="utf-8")
    return f"создан .gitignore со строкой {GITIGNORE_ENTRY}"


def require_map(root: Path) -> RepoMap:
    repo_map = load_map(root)
    if repo_map is None:
        raise MapNotFoundError("Карта репозитория не найдена. Запустите: code-reviewer init")
    return repo_map


def refresh_map(
    root: Path,
    config: Config,
    repo_map: RepoMap,
    *,
    quiet: bool = False,
) -> RepoMap:
    """Инкрементально обновить карту перед проверкой, если файлы изменились."""
    if not stale_files(root, config, repo_map):
        return repo_map

    head = git.head_commit(root)
    refreshed = build_map(root, config, previous=repo_map, head=head)
    refreshed.repo_map.save(map_path(root))
    if not quiet:
        console.print(
            f"[dim]Карта обновлена: разобрано файлов — {refreshed.stats.files_parsed}[/dim]"
        )

    sf = state_path(root)
    state = State.load(sf)
    state.last_map_at = refreshed.repo_map.generated_at
    state.last_map_head = head
    state.save(sf)
    return refreshed.repo_map


def resolve_file(repo_map: RepoMap, needle: str) -> str:
    matches = repo_map.find(needle)
    if not matches:
        raise MapNotFoundError(f"Файл не найден в карте: {needle}")
    if len(matches) > 1:
        listing = "\n  ".join(matches[:10])
        raise MapNotFoundError(f"Уточните путь, подходит несколько файлов:\n  {listing}")
    return matches[0]
