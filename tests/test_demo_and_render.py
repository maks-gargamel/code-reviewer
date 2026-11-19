from __future__ import annotations

import importlib
import json
from io import StringIO
from pathlib import Path

import httpx
from rich.console import Console

from code_reviewer.core.models import FileInfo, Finding, RepoMap, Symbol
from code_reviewer.core.storage import State
from code_reviewer.indexing.graph import SymbolHit
from code_reviewer.render.console import (
    print_outline,
    print_search,
    print_status,
)
from code_reviewer.render.report import findings_json, findings_markdown, print_findings
from code_reviewer.render.sarif import findings_sarif
from code_reviewer.review.pipeline import ReviewResult
from code_reviewer.vcs.diff import DiffSet, FileDiff
from code_reviewer.workflows.demo import DEMO_FILE, run_demo


def _finding(**overrides) -> Finding:
    data = {
        "file": "src/app.py",
        "line": 42,
        "severity": "warning",
        "category": "logic",
        "message": "Проблема с [bold]разметкой[/bold] и | трубой",
        "suggestion": "Вернуть list[str]",
    }
    data.update(overrides)
    return Finding(**data)


def test_run_demo_works_offline_and_without_git(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("demo не должно обращаться к сети")

    monkeypatch.setattr(httpx.Client, "post", no_network)

    result = run_demo()
    assert result.requests == 0
    assert result.review.findings
    assert result.review.findings[0].file == DEMO_FILE
    assert result.review.findings[0].severity == "error"
    assert "SQL-инъекция" in result.review.findings[0].message


def test_markdown_json_and_sarif_formats():
    f = _finding()
    md = findings_markdown([f])
    assert "Всего замечаний: **1**" in md
    assert "\\| трубой" in md
    assert "Замечаний нет" in findings_markdown([])

    parsed_json = json.loads(findings_json([f]))
    assert parsed_json[0]["file"] == "src/app.py"
    assert parsed_json[0]["id"] == f.id

    sarif = json.loads(findings_sarif([f, _finding(severity="info", category="style")]))
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "code-reviewer"
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} == {"logic", "style"}
    assert [res["level"] for res in run["results"]] == ["warning", "note"]
    assert run["results"][0]["partialFingerprints"]["codeReviewerId"] == f.id


def test_rich_rendering_escapes_brackets_safely(monkeypatch, tmp_path: Path):
    buf = StringIO()
    test_con = Console(file=buf, force_terminal=False, highlight=False, width=120)
    render_console_mod = importlib.import_module("code_reviewer.render.console")
    render_report_mod = importlib.import_module("code_reviewer.render.report")
    monkeypatch.setattr(render_console_mod, "console", test_con)
    monkeypatch.setattr(render_report_mod, "console", test_con)

    hit = SymbolHit(
        file="a/[id].py",
        line=10,
        name="parse",
        kind="function",
        signature="def parse(x: dict[str, int]) -> list[str]",
        doc="Doc with [red]tags[/red]",
    )
    print_search([hit], "dict[str")

    info = FileInfo(
        path="a/[id].py",
        lang="python",
        doc="Модуль [bold]описание[/bold]",
        symbols=[
            Symbol(
                name="parse",
                kind="function",
                line=1,
                end_line=2,
                signature="def parse() -> list[int]",
                doc="[dim]doc[/dim]",
            )
        ],
    )
    print_outline("a/[id].py", info)

    print_status(
        RepoMap(files={"a/[id].py": info}, stats={"symbols": 1, "edges_internal": 0}),
        State(),
        root=tmp_path,
        branch="feat/[test]",
        head="abcdef1234",
        stale=["a/[id].py"],
        map_file=tmp_path / "map.json",
    )

    res = ReviewResult(
        scope="ветка [1]",
        diffset=DiffSet(files=[FileDiff(path="a/[id].py")]),
        findings=[_finding(file="a/[id].py")],
    )
    print_findings(res)

    output = buf.getvalue()
    assert "dict[str, int]" in output
    assert "[red]tags[/red]" in output
    assert "[bold]разметкой[/bold]" in output
