"""Служебные CLI-команды: history, cache, install-hook, doctor, config."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from ..core.config import (
    CACHE_DIR,
    SUMMARY_CACHE_DIR,
    Config,
    config_sources,
    service_dir,
    unknown_config_keys,
)
from ..core.errors import AppError
from ..core.storage import cache_stats, clean_cache
from ..render.console import _s, console
from ..render.views import history_json, print_cache, print_config, print_doctor, print_history
from ..review.history import compute_metrics, load_history
from ..vcs.hooks import install_hook as install_hook_file
from ..vcs.hooks import uninstall_hook as uninstall_hook_file
from ..workflows.doctor import has_failures, run_checks, summary
from ..workflows.eval import eval_cache_dir
from .common import RootOption, cli_command, resolve_root

config_app = typer.Typer(
    help="Просмотр, проверка и инициализация конфигурации.",
    no_args_is_help=True,
)


@cli_command
def history_command(
    root_option: RootOption = None,
    limit: Annotated[int, typer.Option("--limit", min=0, help="Сколько запусков показать.")] = 10,
    as_json: Annotated[bool, typer.Option("--json", help="Вывод в JSON.")] = False,
) -> None:
    """Показать историю ревью и метрики."""
    root = resolve_root(root_option)
    entries = load_history(root)
    if not entries:
        raise AppError("История пуста. Запустите check — записи появятся автоматически.")

    metrics = compute_metrics(entries)
    recent = entries[-limit:] if limit > 0 else entries
    if as_json:
        typer.echo(history_json(recent, metrics))
    else:
        print_history(recent, metrics)


@cli_command
def cache_command(
    root_option: RootOption = None,
    clean: Annotated[
        bool,
        typer.Option("--clean", help="Удалить кэш ответов, eval и сводок."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Вывод в JSON.")] = False,
) -> None:
    """Показать размер или очистить кэши."""
    root = resolve_root(root_option)
    targets = {
        "responses": service_dir(root) / CACHE_DIR,
        "eval": eval_cache_dir(root),
        "summaries": service_dir(root) / SUMMARY_CACHE_DIR,
    }
    stats = {name: cache_stats(path) for name, path in targets.items()}
    removed = {name: clean_cache(path) for name, path in targets.items()} if clean else None

    if as_json:
        payload: dict[str, object] = {
            name: {"path": str(targets[name]), "files": files, "bytes": size}
            for name, (files, size) in stats.items()
        }
        if removed is not None:
            payload["removed"] = removed
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print_cache(targets, stats, clean=clean, removed=removed)


@cli_command
def install_hook_command(
    root_option: RootOption = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Перезаписать чужой pre-commit хук."),
    ] = False,
    uninstall: Annotated[bool, typer.Option("--uninstall", help="Удалить хук.")] = False,
) -> None:
    """Установить или удалить git pre-commit хук."""
    root = resolve_root(root_option)
    try:
        if uninstall:
            removed = uninstall_hook_file(root)
            if removed:
                console.print("[green]Хук удалён.[/green]")
            else:
                console.print("[dim]Хук не установлен — удалять нечего.[/dim]")
            return
        path = install_hook_file(root, force=force)
    except OSError as exc:
        raise AppError(f"не удалось обновить хук: {exc}") from exc

    console.print(f"[green]Хук установлен:[/green] {_s(path)}")
    console.print("[dim]При git commit будет запускаться check --quiet на staged-изменениях.[/dim]")


@cli_command
def doctor_command(root_option: RootOption = None) -> None:
    """Проверить окружение и состояние проекта."""
    root = resolve_root(root_option)
    checks = run_checks(root)
    print_doctor(checks, summary_text=summary(checks))
    if has_failures(checks):
        raise typer.Exit(code=1)


@config_app.command("show")
@cli_command
def config_show(
    root_option: RootOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Вывод в JSON.")] = False,
) -> None:
    """Показать итоговую объединённую конфигурацию."""
    root = resolve_root(root_option)
    config = Config.load(root)
    if as_json:
        typer.echo(json.dumps(config.model_dump(), ensure_ascii=False, indent=2))
        return
    print_config(config, sources=config_sources(root))


@config_app.command("check")
@cli_command
def config_check(root_option: RootOption = None) -> None:
    """Проверить корректность TOML-конфигурации и неизвестные ключи."""
    root = resolve_root(root_option)
    Config.load(root)
    unknown = unknown_config_keys(root)
    if unknown:
        for problem in unknown:
            console.print(f"[yellow]Предупреждение:[/yellow] {_s(problem)}")
        raise typer.Exit(code=1)
    console.print("[green]Конфигурация корректна.[/green]")


@config_app.command("init")
@cli_command
def config_init(
    root_option: RootOption = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Перезаписать существующий config.toml."),
    ] = False,
) -> None:
    """Создать шаблон `.code-reviewer/config.toml`."""
    root = resolve_root(root_option)
    path = Config().write_default(root, overwrite=force)
    console.print(f"[green]Конфиг сохранён:[/green] {_s(path)}")
