"""CLI-команды управления пулом API-ключей."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from ..core.config import Config, service_dir
from ..core.errors import ConfigError, UsageAppError
from ..llm.keys import (
    add_api_key,
    clear_api_keys,
    list_key_entries,
    remove_api_key,
    resolve_api_key_file,
)
from ..render.console import _s, console
from ..render.views import keys_json, print_keys
from .common import RootOption, cli_command, ensure_gitignore, resolve_root

keys_app = typer.Typer(
    help="Управление пулом API-ключей (файл keys.txt и переменные окружения).",
    no_args_is_help=True,
)


def _require_key_file(config: Config, root: Path) -> Path:
    key_file = resolve_api_key_file(config.llm.api_key_file, root)
    if key_file is None:
        raise ConfigError(
            "Хранение ключей в файле отключено (llm.api_key_file пуст в конфиге). "
            "Задайте путь к файлу или используйте переменную окружения."
        )
    return key_file


@keys_app.command("add")
@cli_command
def keys_add(
    key: Annotated[
        str | None,
        typer.Argument(help="API-ключ. Если не указан, будет запрошен скрытым вводом."),
    ] = None,
    comment: Annotated[
        str,
        typer.Option("-c", "--comment", help="Необязательный комментарий к ключу."),
    ] = "",
    from_stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Прочитать API-ключ из стандартного ввода (stdin)."),
    ] = False,
    root_option: RootOption = None,
) -> None:
    """Добавить API-ключ в файл пула ключей."""
    if key is not None and from_stdin:
        raise UsageAppError("Нельзя одновременно передать ключ аргументом и --stdin.")

    root = resolve_root(root_option)
    config = Config.load(root)
    key_file = _require_key_file(config, root)

    if from_stdin:
        raw_key = sys.stdin.read().strip()
    elif key is not None:
        raw_key = key
    else:
        raw_key = str(typer.prompt("API-ключ", hide_input=True))

    added, masked, total = add_api_key(key_file, raw_key, comment=comment)

    try:
        inside_service_dir = key_file.resolve().is_relative_to(service_dir(root).resolve())
    except OSError:
        inside_service_dir = False
    if inside_service_dir:
        note = ensure_gitignore(root)
        if note:
            console.print(f"[dim]Git: {_s(note)}[/dim]")

    if added:
        console.print(f"[green]Ключ добавлен:[/green] {_s(masked)} (всего в файле: {total})")
        console.print(f"[dim]Файл: {_s(key_file)}[/dim]")
    else:
        console.print(
            f"[yellow]Ключ уже есть в файле:[/yellow] {_s(masked)} (всего в файле: {total})"
        )


@keys_app.command("list")
@cli_command
def keys_list(
    root_option: RootOption = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Вывод в JSON (только маскированные ключи)."),
    ] = False,
) -> None:
    """Показать настроенные API-ключи (в маскированном виде)."""
    root = resolve_root(root_option)
    config = Config.load(root)
    entries, key_file, env_name, total_unique = list_key_entries(config, root)
    if as_json:
        typer.echo(
            keys_json(
                entries,
                key_file=key_file,
                env_name=env_name,
                total_unique=total_unique,
            )
        )
        return
    print_keys(
        entries,
        key_file=key_file,
        env_name=env_name,
        total_unique=total_unique,
        is_local=config.is_local_llm(),
    )


@keys_app.command("remove")
@cli_command
def keys_remove(
    index: Annotated[
        int,
        typer.Argument(min=1, help="Номер ключа из `keys list` (начиная с 1)."),
    ],
    root_option: RootOption = None,
) -> None:
    """Удалить API-ключ из файла по номеру."""
    root = resolve_root(root_option)
    config = Config.load(root)
    key_file = _require_key_file(config, root)
    masked, remaining = remove_api_key(key_file, index)
    console.print(f"[green]Ключ удалён:[/green] {_s(masked)} (осталось в файле: {remaining})")


@keys_app.command("clear")
@cli_command
def keys_clear(root_option: RootOption = None) -> None:
    """Удалить файл с API-ключами."""
    root = resolve_root(root_option)
    config = Config.load(root)
    key_file = _require_key_file(config, root)
    existed, removed = clear_api_keys(key_file)
    if not existed:
        console.print("[dim]Файл ключей не найден — удалять нечего.[/dim]")
        return
    if removed == 0:
        console.print(f"[green]Файл ключей удалён:[/green] {_s(key_file)} (ключей не было)")
        return
    console.print(f"[green]Удалено ключей:[/green] {removed} ({_s(key_file)})")
