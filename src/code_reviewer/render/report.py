"""Вывод отчётов ревью (таблица, JSON, Markdown, dry-run, сверка)."""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..core.models import Finding
from ..review.pipeline import ReviewResult, VerifySummary
from ..review.prompt import PromptBundle, estimate_tokens
from .console import _s, console, human_duration

SEVERITY_STYLE = {"error": "bold red", "warning": "yellow", "info": "cyan"}
SEVERITY_LABEL = {"error": "error", "warning": "warn", "info": "info"}


def findings_json(findings: list[Finding]) -> str:
    return json.dumps([item.to_dict() for item in findings], ensure_ascii=False, indent=2)


def findings_markdown(findings: list[Finding]) -> str:
    """Сформировать отчёт в формате Markdown."""
    lines = ["# Результаты ревью", ""]
    if not findings:
        lines.append("Замечаний нет.")
        return "\n".join(lines)

    counts = {
        severity: sum(1 for item in findings if item.severity == severity)
        for severity in ("error", "warning", "info")
    }
    summary = ", ".join(f"{name}: {count}" for name, count in counts.items() if count)
    lines.append(
        f"Всего замечаний: **{len(findings)}**" + (f" ({summary})" if summary else "")
    )
    lines.append("")
    lines.append("| Файл:строка | Уровень | Категория | Замечание | Как исправить |")
    lines.append("|---|---|---|---|---|")
    for item in findings:
        location = f"{item.file}:{item.line}" if item.line else item.file
        lines.append(
            "| `{}` | {} | {} | {} | {} |".format(
                _cell(location),
                _cell(item.severity),
                _cell(item.category),
                _cell(item.message),
                _cell(item.suggestion or "—"),
            )
        )
    return "\n".join(lines)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _plural(number: int, one: str, few: str, many: str) -> str:
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def print_dry_run(prompts: list[PromptBundle], *, scope: str, files: int) -> None:
    total = sum(prompt.estimated_tokens for prompt in prompts)
    console.print()
    console.print(
        f"[bold]Режим --dry-run[/bold] · {_s(scope)} · файлов: {files} · "
        f"чанков: {len(prompts)}"
    )
    console.print(f"[dim]~{total} токенов всего. Запрос к модели не выполнялся.[/dim]")

    for index, prompt in enumerate(prompts, start=1):
        suffix = f" · чанк {index}/{len(prompts)}" if len(prompts) > 1 else ""
        console.print()
        console.print(
            Panel(
                _s(prompt.system),
                title=f"system prompt{suffix}",
                border_style="dim",
                subtitle=f"~{estimate_tokens(prompt.system)} токенов",
            )
        )
        console.print()
        console.print(
            Panel(
                _s(prompt.user),
                title=f"user prompt{suffix}",
                border_style="dim",
                subtitle=f"~{estimate_tokens(prompt.user)} токенов",
            )
        )
    console.print()


def print_no_changes(scope: str) -> None:
    console.print()
    console.print(f"[dim]Нет изменений для ревью ({_s(scope)}).[/dim]")
    console.print()


def print_findings(result: ReviewResult) -> None:
    findings = result.findings
    console.print()

    if not findings:
        console.print("[green]Замечаний нет.[/green]")
    else:
        table = Table(header_style="bold", show_lines=True)
        table.add_column("Файл:строка", overflow="fold", no_wrap=False)
        table.add_column("Ур.", no_wrap=True)
        table.add_column("Категория", no_wrap=True)
        table.add_column("Замечание", ratio=3, overflow="fold")
        table.add_column("Как исправить", ratio=2, overflow="fold", style="dim")

        for item in findings:
            style = SEVERITY_STYLE.get(item.severity, "white")
            label = SEVERITY_LABEL.get(item.severity, item.severity)
            location = f"{item.file}:{item.line}" if item.line else item.file
            table.add_row(
                _s(location),
                f"[{style}]{_s(label)}[/{style}]",
                _s(item.category),
                _s(item.message),
                _s(item.suggestion or "—"),
            )
        console.print(table)

    counts = {
        severity: sum(1 for item in findings if item.severity == severity)
        for severity in ("error", "warning", "info")
    }
    summary = ", ".join(f"{name}: {count}" for name, count in counts.items() if count)
    noun = _plural(len(findings), "замечание", "замечания", "замечаний")
    console.print(
        f"[bold]Итог:[/bold] {len(findings)} {noun}"
        + (f" [dim]({summary})[/dim]" if summary else "")
    )

    meta = [
        f"scope: {_s(result.scope)}",
        f"файлов: {len(result.diffset.files)}",
        f"+{result.diffset.additions}/-{result.diffset.deletions}",
    ]
    if result.chunks > 1:
        meta.append(f"чанков: {result.chunks}")
    if result.cached_chunks:
        meta.append(f"из кэша: {result.cached_chunks}")
    if result.redactions:
        meta.append(f"скрыто секретов: {result.redactions}")
    if result.suppressed:
        meta.append(f"подавлено: {result.suppressed}")
    meta.append(human_duration(result.duration))
    console.print(f"[dim]{' · '.join(meta)}[/dim]")

    if result.omitted:
        console.print(
            f"[yellow]Не проверено файлов: {len(result.omitted)} "
            "(лимит --max-files / --max-chunks)[/yellow]"
        )
    if result.diffset.truncated:
        console.print(
            f"[yellow]Diff обрезан по размеру: {len(result.diffset.truncated)} файлов[/yellow]"
        )
    console.print()


def print_verify(verify: VerifySummary, *, stderr: bool = False) -> None:
    """Показать результат сверки с замечаниями прошлого запуска."""
    out = Console(stderr=True, highlight=False) if stderr else console
    out.print()
    out.print("[bold]Сверка с прошлым ревью[/bold]")
    if not verify.closed and not verify.still_open:
        out.print("[dim]Прошлых замечаний не было.[/dim]")
        return

    out.print(
        f"  закрыто: [green]{len(verify.closed)}[/green] · "
        f"осталось: [yellow]{len(verify.still_open)}[/yellow]"
    )
    for item in verify.still_open[:10]:
        location = f"{item.file}:{item.line}" if item.line else item.file
        out.print(f"  [yellow]•[/yellow] {_s(location)} — {_s(item.message)}")
    if len(verify.still_open) > 10:
        out.print(f"  [dim]… ещё {len(verify.still_open) - 10}[/dim]")
