"""Rich-вывод для карты репозитория, статуса и навигации по графу."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from ..core.models import FileInfo, RepoMap
from ..core.storage import State
from ..indexing.graph import Cycle, SymbolHit

console = Console(highlight=False, soft_wrap=False)
err_console = Console(stderr=True, highlight=False, soft_wrap=False)


def _s(value: object) -> str:
    """Экранировать пользовательский текст: `[str, int]` в сигнатурах иначе ломает Rich."""
    return escape(str(value))


def human_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} мс"
    return f"{seconds:.2f} с"


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{int(value)} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} Б"


def print_init_summary(
    repo_map: RepoMap,
    stats: Any,
    *,
    root: Path,
    map_file: Path,
    config_file: Path,
    gitignore_note: str | None = None,
) -> None:
    languages = ", ".join(
        f"{_s(lang)} {count}" for lang, count in repo_map.by_lang().items()
    )
    lines = [
        f"[bold]Файлов:[/bold] {stats.files_total} "
        f"[dim](разобрано {stats.files_parsed}, из кэша {stats.files_reused})[/dim]",
        f"[bold]Символов:[/bold] {stats.symbols}",
        f"[bold]Связей:[/bold] {stats.edges_internal} внутренних, "
        f"{stats.edges_external} внешних"
        + (f", {stats.edges_unresolved} неразрешённых" if stats.edges_unresolved else ""),
    ]
    if languages:
        lines.append(f"[bold]Языки:[/bold] {languages}")
    grammars = getattr(stats, "grammars", None)
    if grammars:
        lines.append(f"[bold]Грамматики:[/bold] загружены {_s(', '.join(grammars))}")
    if getattr(stats, "parse_errors", 0):
        lines.append(f"[bold]Ошибки разбора:[/bold] {stats.parse_errors}")
    if stats.skipped_binary or stats.skipped_large or stats.skipped_ignored:
        lines.append(
            "[bold]Пропущено:[/bold] "
            f"{stats.skipped_binary} бинарных, {stats.skipped_large} больших, "
            f"{stats.skipped_ignored} по ignore"
        )
    lines.append(f"[bold]Время:[/bold] {human_duration(stats.duration)}")
    if not stats.used_git:
        lines.append(
            "[yellow]git недоступен или не репозиторий — .gitignore не учитывался[/yellow]"
        )

    console.print()
    console.print("[bold green]✓ Карта репозитория построена[/bold green]")
    console.print(
        Panel("\n".join(lines), title=_s(root), border_style="green", expand=False)
    )
    console.print(f"[dim]карта:[/dim]   {_s(map_file)}")
    console.print(f"[dim]конфиг:[/dim] {_s(config_file)}")
    if gitignore_note:
        console.print(f"[yellow]! {_s(gitignore_note)}[/yellow]")
    console.print()


def print_status(
    repo_map: RepoMap | None,
    state: State,
    *,
    root: Path,
    branch: str | None,
    head: str | None,
    stale: list[str],
    map_file: Path,
) -> None:
    console.print()
    console.print(f"[bold]Репозиторий:[/bold] {_s(root)}")
    branch_text = _s(branch) if branch else "—"
    head_text = _s(head[:10]) if head else "нет коммитов"
    console.print(f"[bold]Git:[/bold] {branch_text} @ {head_text}")

    if repo_map is None:
        console.print("[yellow]Карта не построена. Запустите: code-reviewer init[/yellow]")
        console.print()
        return

    console.print(f"[bold]Карта:[/bold] {_s(map_file)} [dim]({_s(repo_map.generated_at)})[/dim]")
    languages = ", ".join(f"{_s(k)} {v}" for k, v in repo_map.by_lang().items())
    stats = repo_map.stats
    summary = (
        f"{len(repo_map.files)} файлов, "
        f"{stats.get('symbols', 0)} символов, "
        f"{stats.get('edges_internal', 0)} внутренних связей"
    )
    console.print(f"[bold]Состав:[/bold] {summary}")
    summaries = sum(1 for info in repo_map.files.values() if info.summary)
    if summaries:
        console.print(f"[bold]Сводки:[/bold] {summaries}")
    if languages:
        console.print(f"[bold]Языки:[/bold] {languages}")

    if stale:
        preview = ", ".join(stale[:5])
        suffix = f" и ещё {len(stale) - 5}" if len(stale) > 5 else ""
        console.print(
            f"[yellow]Устарела:[/yellow] изменено файлов — {len(stale)} "
            f"({_s(preview)}{suffix})"
        )
        console.print("[dim]Обновить: code-reviewer init[/dim]")
    else:
        console.print("[green]Карта актуальна[/green]")

    if state.last_check_at:
        commit = _s((state.last_reviewed_commit or "")[:10]) or "—"
        console.print(f"[bold]Последнее ревью:[/bold] {_s(state.last_check_at)} ({commit})")
    else:
        console.print("[dim]Ревью ещё не запускалось[/dim]")
    console.print()


def _file_label(path: str, repo_map: RepoMap) -> str:
    info = repo_map.files.get(path)
    if info is None:
        return _s(path)
    return f"{_s(path)} [dim]({_s(info.lang)}, {len(info.symbols)} симв.)[/dim]"


def _dependency_tree(path: str, repo_map: RepoMap, depth: int, seen: set[str]) -> Tree:
    tree = Tree(_file_label(path, repo_map))
    if depth <= 0 or path in seen:
        return tree
    seen.add(path)
    for edge in repo_map.dependencies(path):
        if edge.dst is None or edge.dst in seen:
            continue
        tree.add(_dependency_tree(edge.dst, repo_map, depth - 1, seen))
    return tree


def _dependent_tree(path: str, repo_map: RepoMap, depth: int, seen: set[str]) -> Tree:
    tree = Tree(_file_label(path, repo_map))
    if depth <= 0 or path in seen:
        return tree
    seen.add(path)
    for edge in repo_map.dependents(path):
        if edge.src in seen:
            continue
        tree.add(_dependent_tree(edge.src, repo_map, depth - 1, seen))
    return tree


def print_deps(path: str, repo_map: RepoMap, *, depth: int) -> None:
    info = repo_map.files[path]
    console.print()
    if info.doc:
        console.print(f"[dim]{_s(info.doc)}[/dim]")
    console.print(
        f"[bold]{_s(path)}[/bold] — {_s(info.lang)}, {info.loc} строк, "
        f"{len(info.symbols)} символов"
    )

    dependencies = repo_map.dependencies(path)
    console.print()
    if dependencies:
        console.print("[bold]Импортирует:[/bold]")
        console.print(_dependency_tree(path, repo_map, depth, set()))
    else:
        console.print("[dim]Внутренних зависимостей нет[/dim]")

    dependents = repo_map.dependents(path)
    console.print()
    if dependents:
        console.print("[bold]Используется в:[/bold]")
        console.print(_dependent_tree(path, repo_map, depth, set()))
    else:
        console.print("[dim]Никто не импортирует этот файл[/dim]")

    externals = repo_map.externals(path)
    if externals:
        names = sorted({edge.name for edge in externals})
        console.print()
        console.print(f"[dim]Внешние зависимости: {_s(', '.join(names))}[/dim]")
    console.print()


def print_files(
    repo_map: RepoMap,
    *,
    limit: int | None = None,
    lang: str | None = None,
) -> None:
    table = Table(title="Файлы репозитория", header_style="bold", show_lines=False)
    table.add_column("Файл", overflow="fold")
    table.add_column("Язык", justify="right")
    table.add_column("Строк", justify="right")
    table.add_column("Симв.", justify="right")
    table.add_column("Импортов", justify="right")

    items = sorted(repo_map.files.items())
    if lang:
        items = [(p, i) for p, i in items if i.lang == lang]
    shown = items[:limit] if limit else items
    for path, info in shown:
        table.add_row(
            _s(path),
            _s(info.lang),
            str(info.loc),
            str(len(info.symbols)),
            str(len(info.imports)),
        )
    console.print()
    console.print(table)
    if limit and len(items) > limit:
        console.print(f"[dim]показано {limit} из {len(items)}[/dim]")
    console.print()


def print_outline(path: str, info: FileInfo) -> None:
    table = Table(title=_s(path), header_style="bold")
    table.add_column("Строка", justify="right", style="dim", no_wrap=True)
    table.add_column("Символ", ratio=3, overflow="fold")
    table.add_column("Описание", ratio=2, style="dim", overflow="fold")

    for symbol in info.symbols:
        indent = "  " if symbol.parent else ""
        declaration = symbol.signature or symbol.name
        table.add_row(
            str(symbol.line),
            _s(f"{indent}{declaration}"),
            _s(symbol.doc or ""),
        )
    console.print()
    if info.doc:
        console.print(f"[dim]{_s(info.doc)}[/dim]")
    console.print(table)
    for error in info.parse_errors:
        console.print(f"[red]ошибка разбора:[/red] {_s(error)}")
    console.print()


def print_cycles(cycles: list[Cycle], *, files: int) -> None:
    console.print()
    if not cycles:
        console.print(f"[green]Циклических зависимостей нет[/green] ({files} файлов)")
        console.print()
        return

    console.print(f"[bold]Циклы зависимостей:[/bold] {len(cycles)}")
    for index, cycle in enumerate(cycles, start=1):
        console.print(f"\n[bold]{index}.[/bold] файлов: {len(cycle.files)}")
        for src, dst in cycle.edges:
            console.print(f"   {_s(src)} → {_s(dst)}")
    console.print()


def print_search(hits: list[SymbolHit], query: str) -> None:
    console.print()
    if not hits:
        console.print(f"[dim]Ничего не найдено по запросу «{_s(query)}».[/dim]")
        console.print()
        return

    table = Table(title=f"Символы: {_s(query)}", header_style="bold")
    table.add_column("Файл:строка")
    table.add_column("Вид", no_wrap=True)
    table.add_column("Символ", ratio=2)
    table.add_column("Описание", ratio=2, style="dim")

    for hit in hits:
        table.add_row(
            _s(f"{hit.file}:{hit.line}"),
            _s(hit.kind),
            _s(hit.signature),
            _s(hit.doc or ""),
        )

    console.print(table)
    console.print(f"[dim]найдено: {len(hits)}[/dim]")
    console.print()
