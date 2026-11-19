"""Представления вывода для demo, eval, history, doctor, cache и config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from ..core.config import Config
from ..review.history import HistoryEntry, Metrics
from .console import _s, console, human_duration, human_size
from .report import print_findings


def print_demo(result: Any, *, filename: str) -> None:
    console.print()
    console.print("[bold]Демонстрация code-reviewer[/bold]")
    console.print(
        "[dim]Сеть, ключ API и git не требуются: модель заменена записанным ответом.[/dim]"
    )
    console.print(
        Panel(
            Syntax(result.after, "python", line_numbers=True, word_wrap=True),
            title=f"{_s(filename)} — изменённая версия",
            border_style="dim",
        )
    )
    print_findings(result.review)
    console.print(
        f"[dim]Запросов к API: 0 · файлов в diff: {len(result.review.diffset.files)} · "
        f"замечаний: {len(result.review.findings)}[/dim]"
    )


def print_eval(report: Any) -> None:
    table = Table(
        title="Eval: ревью на кейсах с известными дефектами",
        header_style="bold",
    )
    table.add_column("Кейс")
    table.add_column("TP", justify="right")
    table.add_column("FN", justify="right")
    table.add_column("FP", justify="right")
    table.add_column("P", justify="right")
    table.add_column("R", justify="right")
    table.add_column("Запросы", justify="right")
    table.add_column("Время", justify="right")

    for case in report.cases:
        if case.error:
            precision = recall = "—"
        else:
            precision = f"{case.precision:.0%}"
            recall = f"{case.recall:.0%}"
        table.add_row(
            _s(case.name),
            str(case.matched),
            str(case.false_negatives),
            str(case.false_positives),
            precision,
            recall,
            f"{case.requests}/{case.cached}",
            human_duration(case.duration),
        )

    console.print()
    console.print(table)

    for case in report.cases:
        if case.error:
            console.print(f"[red]{_s(case.name)}:[/red] {_s(case.error)}")
        for item in case.missed:
            console.print(
                f"[yellow]пропущено[/yellow] {_s(item.file)}:{item.line} "
                f"({_s(item.category or '—')})"
            )

    console.print(
        f"[bold]Итог:[/bold] precision {report.precision:.0%} · "
        f"recall {report.recall:.0%} · F1 {report.f1:.0%} · "
        f"запросов {report.requests} (из кэша {report.cached})"
    )
    console.print()


def eval_json(report: Any) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def print_history(entries: list[HistoryEntry], metrics: Metrics) -> None:
    table = Table(title="История ревью", header_style="bold")
    table.add_column("Когда", no_wrap=True)
    table.add_column("Коммит", no_wrap=True)
    table.add_column("Scope", overflow="fold")
    table.add_column("Файлы", justify="right")
    table.add_column("E/W/I", justify="right")
    table.add_column("Запросы", justify="right")
    table.add_column("Время", justify="right")

    for entry in entries:
        counts = entry.severity_counts
        table.add_row(
            _s(entry.created_at.replace("T", " ")[:19]),
            _s((entry.commit or "")[:10]) or "—",
            _s(entry.scope),
            str(entry.files),
            f"{counts.get('error', 0)}/{counts.get('warning', 0)}/{counts.get('info', 0)}",
            f"{entry.requests}/{entry.cached}",
            human_duration(entry.duration),
        )

    console.print()
    console.print(table)
    console.print(
        f"[bold]Всего:[/bold] {metrics.reviews} ревью · "
        f"{metrics.findings_total} находок (уникальных {metrics.unique_findings}) · "
        f"в среднем {metrics.average_per_review:.1f} за ревью"
    )
    console.print(
        f"[bold]Повторяемость:[/bold] новых {metrics.new} · повторных {metrics.repeated} · "
        f"исправлено с прошлого ревью {metrics.last_fixed}"
        + (f" ({metrics.acceptance:.0%})" if metrics.acceptance else "")
    )
    if metrics.by_category:
        categories = ", ".join(
            f"{_s(name)} {count}" for name, count in metrics.by_category.items()
        )
        console.print(f"[bold]Категории:[/bold] {categories}")
    console.print()


def history_json(entries: list[HistoryEntry], metrics: Metrics) -> str:
    return json.dumps(
        {
            "metrics": metrics.to_dict(),
            "entries": [entry.to_dict() for entry in entries],
        },
        ensure_ascii=False,
        indent=2,
    )


def print_doctor(checks: list[Any], *, summary_text: str) -> None:
    marks = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]×[/red]"}
    table = Table(title="Диагностика code-reviewer", header_style="bold")
    table.add_column("", justify="center", no_wrap=True)
    table.add_column("Проверка", no_wrap=True)
    table.add_column("Детали", overflow="fold")

    for check in checks:
        table.add_row(marks.get(check.status, "?"), _s(check.name), _s(check.detail))

    console.print()
    console.print(table)
    console.print(f"[bold]Итог:[/bold] {_s(summary_text)}")
    console.print()


def print_cache(
    targets: dict[str, Path],
    stats: dict[str, tuple[int, int]],
    *,
    clean: bool = False,
    removed: dict[str, int] | None = None,
) -> None:
    if clean and removed is not None:
        total_files = sum(removed.values())
        total_bytes = sum(size for _, size in stats.values())
        console.print(
            f"[green]Кэш очищен:[/green] файлов {total_files}, {human_size(total_bytes)}"
        )
        return

    labels = {
        "responses": "Кэш ответов",
        "eval": "Eval-кэш",
        "summaries": "Кэш сводок",
    }
    total_files = 0
    total_bytes = 0
    for name, (files, size) in stats.items():
        label = labels.get(name, name)
        total_files += files
        total_bytes += size
        console.print(f"{_s(label)}: {files} файлов, {human_size(size)}")
        console.print(f"  [dim]{_s(targets[name])}[/dim]")
    console.print(f"[bold]Итого:[/bold] файлов {total_files}, {human_size(total_bytes)}")


def print_config(config: Config, *, sources: list[Path]) -> None:
    console.print()
    existing = [str(p) for p in sources if p.is_file()]
    sources_label = ", ".join(existing) if existing else "встроенные значения по умолчанию"
    console.print(f"[bold]Источники:[/bold] {_s(sources_label)}")
    console.print(Syntax(json.dumps(config.model_dump(), ensure_ascii=False, indent=2), "json"))
    console.print()
