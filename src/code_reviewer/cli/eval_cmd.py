"""CLI-команды `eval` и `demo`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..core.config import Config
from ..core.errors import AppError
from ..llm.client import client_from_config
from ..render.console import _s, console
from ..render.views import eval_json, print_demo, print_eval
from ..workflows.demo import DEMO_FILE, run_demo
from ..workflows.eval import (
    DEFAULT_TOLERANCE,
    eval_cache_dir,
    load_cases,
    run_eval,
)
from .common import RootOption, cli_command, resolve_root


@cli_command
def demo_command() -> None:
    """Показать полный цикл ревью на встроенном примере без сети, ключа и git."""
    result = run_demo()
    print_demo(result, filename=DEMO_FILE)


@cli_command
def eval_command(
    root_option: RootOption = None,
    cases_dir: Annotated[
        Path | None,
        typer.Option("--cases", file_okay=False, help="Каталог с кейсами (по умолчанию ./evals)."),
    ] = None,
    tolerance: Annotated[
        int,
        typer.Option("--tolerance", min=0, help="Допуск по номеру строки."),
    ] = DEFAULT_TOLERANCE,
    max_cases: Annotated[
        int | None,
        typer.Option("--max-cases", min=1, help="Максимум кейсов для прогона."),
    ] = None,
    jobs: Annotated[
        int | None,
        typer.Option("--jobs", min=1, max=32, help="Число потоков для чанков внутри кейса."),
    ] = None,
    fail_under: Annotated[
        float | None,
        typer.Option("--fail-under", min=0.0, max=1.0, help="Код 1, если recall ниже порога."),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Отключить кэш eval-ответов."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Вывод в JSON.")] = False,
) -> None:
    """Прогнать ревью на наборе кейсов и рассчитать precision / recall / F1."""
    root = resolve_root(root_option)
    config = Config.load(root)

    directory = cases_dir or (root / "evals")
    if not directory.is_dir():
        raise AppError(f"Каталог кейсов не найден: {directory}")

    try:
        cases = load_cases(directory)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    if not cases:
        raise AppError(f"В {directory} нет кейсов (*.json)")

    client = client_from_config(config, root)

    def _progress(index: int, total: int, name: str) -> None:
        if not as_json:
            console.print(f"[dim][{index}/{total}] {_s(name)}[/dim]")

    report = run_eval(
        cases,
        config,
        client,
        tolerance=tolerance,
        use_cache=not no_cache,
        cache_dir=eval_cache_dir(root),
        max_cases=max_cases,
        jobs=jobs or config.review.jobs,
        progress=_progress,
    )

    if as_json:
        typer.echo(eval_json(report))
    else:
        print_eval(report)

    if fail_under is not None and report.recall < fail_under:
        raise typer.Exit(code=1)
