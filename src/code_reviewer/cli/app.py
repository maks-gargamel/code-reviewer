"""Корневое приложение CLI (точки входа `code-reviewer` и `cr`)."""

from __future__ import annotations

from typing import Annotated

import typer

from .. import __version__
from ..core.errors import AppError
from ..render.console import _s, err_console
from .check_cmd import check_command, mark_reviewed_command
from .eval_cmd import demo_command, eval_command
from .init_cmd import init_command, status_command
from .map_cmd import map_app
from .ops_cmd import (
    cache_command,
    config_app,
    doctor_command,
    history_command,
    install_hook_command,
)

app = typer.Typer(
    name="code-reviewer",
    help="Локальный AI код-ревьюер с картой репозитория и графом зависимостей.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"code-reviewer, version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "-V",
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Показать версию программы и выйти.",
        ),
    ] = False,
) -> None:
    """Локальный AI код-ревьюер с картой репозитория."""
    del version


app.command("init")(init_command)
app.command("status")(status_command)
app.command("check")(check_command)
app.command("mark-reviewed")(mark_reviewed_command)
app.command("demo")(demo_command)
app.command("eval")(eval_command)
app.command("history")(history_command)
app.command("cache")(cache_command)
app.command("install-hook")(install_hook_command)
app.command("doctor")(doctor_command)

app.add_typer(map_app, name="map")
app.add_typer(config_app, name="config")


def run() -> None:
    """Точка входа для консольных скриптов `code-reviewer` и `cr`."""
    try:
        app()
    except AppError as exc:
        err_console.print(f"[red]Ошибка:[/red] {_s(exc)}")
        raise SystemExit(2) from exc
