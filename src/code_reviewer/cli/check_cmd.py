"""CLI-команды `check` и `mark-reviewed`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..core.config import Config
from ..core.errors import AppError, GitError, UsageAppError
from ..core.models import OutputFormat, utc_now
from ..core.storage import (
    State,
    last_review_path,
    load_baseline,
    load_last_review,
    save_baseline,
    save_last_review,
    state_path,
)
from ..render.console import _s, console, err_console
from ..render.report import (
    findings_json,
    findings_markdown,
    print_dry_run,
    print_findings,
    print_no_changes,
    print_verify,
)
from ..render.sarif import findings_sarif
from ..review.findings import worst_rank
from ..review.history import append_entry
from ..review.pipeline import compute_verify, run_review
from ..vcs import git
from ..vcs.diff import DiffScope
from .common import RootOption, cli_command, refresh_map, require_map, resolve_root


@cli_command
def check_command(
    root_option: RootOption = None,
    use_last: Annotated[bool, typer.Option("--last", help="Проверить последний коммит.")] = False,
    use_since_last: Annotated[
        bool,
        typer.Option("--since-last", help="Проверить изменения с последнего ревью."),
    ] = False,
    commit_hash: Annotated[
        str | None,
        typer.Option("--commit", help="Проверить конкретный коммит."),
    ] = None,
    range_: Annotated[
        str | None,
        typer.Option("--range", help="Проверить диапазон коммитов A..B."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Показать промпт и токены без вызова модели."),
    ] = False,
    output_format: Annotated[
        OutputFormat | None,
        typer.Option("--format", help="Формат вывода."),
    ] = None,
    output_file: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Записать отчёт в файл."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Сокращение для --format json."),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Игнорировать кэш ответов модели."),
    ] = False,
    no_ignore: Annotated[
        bool,
        typer.Option("--no-ignore", help="Отключить .code-reviewerignore и inline-директивы."),
    ] = False,
    use_baseline: Annotated[
        bool,
        typer.Option("--baseline", help="Показывать только новые замечания (без baseline)."),
    ] = False,
    save_baseline_flag: Annotated[
        bool,
        typer.Option("--save-baseline", help="Зафиксировать текущие замечания как baseline."),
    ] = False,
    verify: Annotated[
        bool,
        typer.Option("--verify", help="Сверить с замечаниями прошлого ревью."),
    ] = False,
    max_files: Annotated[
        int | None,
        typer.Option("--max-files", min=1, help="Максимум проверяемых файлов."),
    ] = None,
    max_chunks: Annotated[
        int | None,
        typer.Option("--max-chunks", min=1, help="Максимум запросов (чанков) на одно ревью."),
    ] = None,
    jobs: Annotated[
        int | None,
        typer.Option("--jobs", min=1, max=32, help="Число параллельных потоков для чанков."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Подавлять служебный и пустой вывод (для pre-commit хука)."),
    ] = False,
) -> None:
    """Проверить изменения через LLM с учётом карты репозитория."""
    if sum([use_last, use_since_last, commit_hash is not None, range_ is not None]) > 1:
        raise UsageAppError(
            "Выберите только один режим: --last, --since-last, --commit или --range"
        )
    if use_baseline and save_baseline_flag:
        raise UsageAppError("--baseline и --save-baseline несовместимы")

    root = resolve_root(root_option)
    config = Config.load(root)
    repo_map = require_map(root)

    if verify and not last_review_path(root).is_file():
        raise AppError("Нет прошлого ревью для --verify. Сначала выполните check.")

    repo_map = refresh_map(root, config, repo_map, quiet=quiet)

    sf = state_path(root)
    state = State.load(sf)
    baseline = load_baseline(root) if use_baseline else None

    scope = DiffScope.AUTO
    commit: str | None = None
    since_commit: str | None = None
    if use_last:
        scope = DiffScope.LAST
    elif use_since_last:
        if not state.last_reviewed_commit:
            raise AppError(
                "Нет сохранённого коммита последнего ревью. "
                "Запустите check --last или выполните mark-reviewed."
            )
        scope = DiffScope.SINCE
        since_commit = state.last_reviewed_commit
    elif commit_hash is not None:
        scope = DiffScope.COMMIT
        commit = commit_hash
    elif range_ is not None:
        scope = DiffScope.RANGE

    selected_format = (
        OutputFormat.JSON
        if as_json
        else (output_format or OutputFormat(str(config.output.format)))
    )

    def _progress(index: int, total: int, files_count: int) -> None:
        if (
            total > 1
            and selected_format == OutputFormat.TABLE
            and output_file is None
            and not quiet
        ):
            console.print(f"[dim]чанк {index}/{total}: файлов {files_count}[/dim]")

    result = run_review(
        root,
        config,
        repo_map,
        scope=scope,
        commit=commit,
        range_=range_,
        since_commit=since_commit,
        use_cache=not no_cache,
        use_ignore=not no_ignore,
        baseline_ids=baseline.ids if baseline is not None else None,
        dry_run=dry_run,
        max_files=max_files,
        max_chunks=max_chunks,
        jobs=jobs or config.review.jobs,
        progress=_progress,
    )

    if result.diffset.is_empty():
        if not quiet:
            print_no_changes(result.scope)
        return

    if dry_run:
        print_dry_run(result.prompts, scope=result.scope, files=len(result.diffset.files))
        return

    if result.remote_warning_needed and not state.remote_warned and not quiet:
        err_console.print(
            "[yellow]! Отправка diff во внешний LLM API "
            f"({_s(config.llm.base_url)}). Секреты маскируются автоматически.[/yellow]"
        )
        state.remote_warned = True

    verify_summary = (
        compute_verify(load_last_review(root), result.findings) if verify else None
    )

    payload = ""
    if selected_format == OutputFormat.JSON:
        payload = findings_json(result.findings)
    elif selected_format == OutputFormat.MD:
        payload = findings_markdown(result.findings)
    elif selected_format == OutputFormat.SARIF:
        payload = findings_sarif(result.findings)

    if output_file is not None:
        text = payload or findings_markdown(result.findings)
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            raise AppError(f"не удалось записать отчёт: {exc}") from exc
        if not quiet:
            console.print(
                f"\n[dim]Отчёт записан: {_s(output_file)} "
                f"(замечаний: {len(result.findings)})[/dim]"
            )
    elif selected_format == OutputFormat.TABLE:
        if not quiet or result.findings:
            print_findings(result)
    else:
        typer.echo(payload)

    if verify_summary is not None:
        print_verify(
            verify_summary,
            stderr=selected_format != OutputFormat.TABLE or output_file is not None,
        )

    head = git.head_commit(root)
    branch = git.current_branch(root)
    append_entry(
        root,
        commit=head,
        branch=branch,
        scope=result.scope,
        changed_files=result.changed_files,
        chunks=result.chunks,
        cached_chunks=result.cached_chunks,
        duration=result.duration,
        suppressed=result.suppressed,
        redactions=result.redactions,
        findings=[
            {
                "id": item.id,
                "file": item.file,
                "line": item.line,
                "severity": item.severity,
                "category": item.category,
                "message": item.message,
            }
            for item in result.findings
        ],
    )

    save_last_review(root, result.findings, commit=head)

    if save_baseline_flag:
        saved = save_baseline(root, result.findings, commit=head)
        if not quiet:
            console.print(
                f"[green]Baseline сохранён:[/green] {len(saved.ids)} замечаний. "
                "Теперь check --baseline покажет только новые."
            )

    threshold = {"never": 99, "warning": 2, "error": 3}.get(str(config.output.fail_on), 2)
    failed = (not save_baseline_flag) and worst_rank(result.findings) >= threshold

    state.last_check_at = utc_now()
    state.branch = branch
    if (
        not failed
        and head
        and scope in (DiffScope.AUTO, DiffScope.STAGED, DiffScope.LAST, DiffScope.SINCE)
    ):
        state.last_reviewed_commit = head
    state.save(sf)

    if failed:
        raise typer.Exit(code=1)


@cli_command
def mark_reviewed_command(root_option: RootOption = None) -> None:
    """Пометить текущий HEAD как проверенный без вызова LLM."""
    root = resolve_root(root_option)
    head = git.head_commit(root)
    if head is None:
        raise GitError("В репозитории нет коммитов")

    sf = state_path(root)
    state = State.load(sf)
    state.last_reviewed_commit = head
    state.last_check_at = utc_now()
    state.branch = git.current_branch(root)
    state.save(sf)

    console.print(
        f"[green]Отмечено как проверенное:[/green] {_s(head[:10])} "
        f"({_s(state.branch or '—')})"
    )
