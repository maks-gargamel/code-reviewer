"""CLI-команды `init` и `status`."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from ..core.config import Config
from ..core.models import MAP_VERSION
from ..core.storage import State, state_path
from ..indexing.builder import build_map, load_map, map_path, stale_files
from ..indexing.summarize import summarize_map, summarized_count
from ..llm.client import client_from_config
from ..render.console import _s, console, print_init_summary, print_status
from ..vcs import git
from .common import RootOption, cli_command, ensure_gitignore, resolve_root


@cli_command
def init_command(
    root_option: RootOption = None,
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="Перестроить карту с нуля, игнорируя кэш."),
    ] = False,
    ai_summary: Annotated[
        bool,
        typer.Option("--ai-summary", help="Сгенерировать сводки по модулям через LLM."),
    ] = False,
    summary_limit: Annotated[
        int,
        typer.Option("--summary-limit", help="Максимум запросов к модели для сводок."),
    ] = 20,
    jobs: Annotated[
        int,
        typer.Option("--jobs", min=1, max=32, help="Число потоков для генерации AI-сводок."),
    ] = 1,
) -> None:
    """Инициализировать `.code-reviewer/` и построить карту репозитория."""
    root = resolve_root(root_option)
    config = Config.load(root)
    config_file = config.write_default(root)

    previous = None if rebuild else load_map(root)
    if previous is not None:
        if previous.version != MAP_VERSION:
            console.print(
                f"[dim]Формат карты изменился ({previous.version} → {MAP_VERSION}): "
                "полная пересборка[/dim]"
            )
        else:
            console.print(
                f"[dim]В кэше {len(previous.files)} файлов — обновляем только изменённые[/dim]"
            )

    head = git.head_commit(root)
    note = ensure_gitignore(root)
    result = build_map(root, config, previous=previous, head=head, force=rebuild)

    mfile = map_path(root)
    result.repo_map.save(mfile)

    summary_result = None
    if ai_summary:
        client = client_from_config(config, root)

        def _summary_progress(index: int, total: int, path: str) -> None:
            console.print(f"[dim]сводки: {index}/{total} {_s(path)}[/dim]")

        summary_result = summarize_map(
            root,
            config,
            result.repo_map,
            client,
            limit=max(0, summary_limit),
            jobs=jobs,
            progress=_summary_progress,
        )
        result.repo_map.save(mfile)

    sf = state_path(root)
    state = State.load(sf)
    checkpoint_set = False
    if head and not state.last_reviewed_commit:
        state.last_reviewed_commit = head
        checkpoint_set = True
    state.last_map_at = result.repo_map.generated_at
    state.last_map_head = head
    state.branch = git.current_branch(root)
    state.save(sf)

    print_init_summary(
        result.repo_map,
        result.stats,
        root=root,
        map_file=mfile,
        config_file=config_file,
        gitignore_note=note,
    )
    if checkpoint_set and head is not None:
        console.print(
            f"[dim]Точка отсчёта для --since-last: {_s(head[:10])} "
            "(полный аудит: code-reviewer check --all --dry-run)[/dim]"
        )

    if summary_result is not None:
        if summary_result.errors:
            console.print(
                f"[yellow]Сводки: обновлено {summary_result.updated}, "
                f"из кэша {summary_result.cached}, запросов {summary_result.requests}, "
                f"пропущено {summary_result.skipped}. "
                f"Ошибка: {_s(summary_result.errors[0])}[/yellow]"
            )
        elif summary_result.updated + summary_result.cached + summary_result.requests:
            console.print(
                f"[green]Сводки:[/green] обновлено {summary_result.updated}, "
                f"из кэша {summary_result.cached}, запросов {summary_result.requests}, "
                f"пропущено {summary_result.skipped}"
            )
        elif summary_result.skipped:
            console.print(
                f"[dim]Сводки: пропущено {summary_result.skipped} "
                f"(лимит запросов {summary_limit}).[/dim]"
            )
        else:
            console.print("[dim]Сводки: все подходящие модули уже описаны.[/dim]")


@cli_command
def status_command(
    root_option: RootOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Вывод в JSON.")] = False,
) -> None:
    """Показать состояние репозитория и карты."""
    root = resolve_root(root_option)
    config = Config.load(root)
    repo_map = load_map(root)
    state = State.load(state_path(root))
    stale = stale_files(root, config, repo_map) if repo_map is not None else []
    branch = git.current_branch(root)
    head = git.head_commit(root)

    if as_json:
        payload: dict[str, object] = {
            "repository": str(root),
            "branch": branch,
            "head": head,
            "last_check_at": state.last_check_at,
            "last_reviewed_commit": state.last_reviewed_commit,
            "map": None,
        }
        if repo_map is not None:
            payload["map"] = {
                "path": str(map_path(root)),
                "generated_at": repo_map.generated_at,
                "files": len(repo_map.files),
                "symbols": repo_map.stats.get("symbols", 0),
                "edges_internal": repo_map.stats.get("edges_internal", 0),
                "summaries": summarized_count(repo_map),
                "by_lang": repo_map.by_lang(),
                "stale": stale,
            }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print_status(
        repo_map,
        state,
        root=root,
        branch=branch,
        head=head,
        stale=stale,
        map_file=map_path(root),
    )
