"""Подкоманды `map`: deps, files, outline, cycles, search, export."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from ..core.errors import AppError
from ..indexing.graph import find_cycles, search_symbols, to_dot
from ..render.console import (
    _s,
    console,
    print_cycles,
    print_deps,
    print_files,
    print_outline,
    print_search,
)
from .common import RootOption, cli_command, require_map, resolve_file, resolve_root

map_app = typer.Typer(
    help="Навигация по карте репозитория и графу зависимостей.",
    no_args_is_help=True,
)


class ExportFormat(StrEnum):
    DOT = "dot"


@map_app.command("deps")
@cli_command
def map_deps(
    path: Annotated[str, typer.Argument(help="Путь (или суффикс пути) к файлу в карте.")],
    depth: Annotated[int, typer.Option("--depth", min=0, help="Глубина обхода графа.")] = 1,
    root_option: RootOption = None,
) -> None:
    """Показать зависимости и зависимые файлы."""
    root = resolve_root(root_option)
    repo_map = require_map(root)
    resolved = resolve_file(repo_map, path)
    print_deps(resolved, repo_map, depth=depth)


@map_app.command("files")
@cli_command
def map_files(
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Сколько файлов показать."),
    ] = None,
    lang: Annotated[str | None, typer.Option("--lang", help="Фильтр по языку.")] = None,
    root_option: RootOption = None,
) -> None:
    """Показать список файлов из карты."""
    root = resolve_root(root_option)
    repo_map = require_map(root)
    print_files(repo_map, limit=limit, lang=lang)


@map_app.command("outline")
@cli_command
def map_outline(
    path: Annotated[str, typer.Argument(help="Путь (или суффикс пути) к файлу в карте.")],
    root_option: RootOption = None,
) -> None:
    """Показать структуру файла: классы, функции и сигнатуры."""
    root = resolve_root(root_option)
    repo_map = require_map(root)
    resolved = resolve_file(repo_map, path)
    print_outline(resolved, repo_map.files[resolved])


@map_app.command("cycles")
@cli_command
def map_cycles(root_option: RootOption = None) -> None:
    """Найти циклические зависимости в графе."""
    root = resolve_root(root_option)
    repo_map = require_map(root)
    print_cycles(find_cycles(repo_map), files=len(repo_map.files))


@map_app.command("search")
@cli_command
def map_search(
    query: Annotated[str, typer.Argument(help="Подстрока имени или сигнатуры символа.")],
    limit: Annotated[int, typer.Option("--limit", min=1, help="Максимум совпадений.")] = 50,
    root_option: RootOption = None,
) -> None:
    """Поиск символов в карте репозитория."""
    root = resolve_root(root_option)
    repo_map = require_map(root)
    print_search(search_symbols(repo_map, query, limit=limit), query)


@map_app.command("export")
@cli_command
def map_export(
    export_format: Annotated[
        ExportFormat,
        typer.Option("--format", help="Формат экспорта графа."),
    ] = ExportFormat.DOT,
    output_file: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Файл для сохранения (по умолчанию stdout)."),
    ] = None,
    focus: Annotated[
        str | None,
        typer.Option("--focus", help="Ограничить граф окружением указанного файла."),
    ] = None,
    depth: Annotated[
        int,
        typer.Option("--depth", min=1, help="Глубина окружения вокруг --focus."),
    ] = 1,
    root_option: RootOption = None,
) -> None:
    """Экспорт графа зависимостей в Graphviz DOT."""
    del export_format
    root = resolve_root(root_option)
    repo_map = require_map(root)

    resolved = resolve_file(repo_map, focus) if focus else None
    payload = to_dot(repo_map, focus=resolved, depth=depth)

    if output_file is None:
        typer.echo(payload)
        return

    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(payload + "\n", encoding="utf-8")
    except OSError as exc:
        raise AppError(f"не удалось записать файл: {exc}") from exc

    if resolved:
        related_count = len(repo_map.related(resolved, depth=depth)) + 1
        console.print(
            f"[green]Граф записан:[/green] {_s(output_file)} "
            f"({related_count} файлов вокруг {_s(resolved)})"
        )
    else:
        console.print(
            f"[green]Граф записан:[/green] {_s(output_file)} ({len(repo_map.files)} файлов)"
        )
    console.print("[dim]Отрисовка: dot -Tsvg graph.dot -o graph.svg[/dim]")
