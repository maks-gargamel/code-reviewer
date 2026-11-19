from __future__ import annotations

import json
import threading
from pathlib import Path

from code_reviewer.core.config import Config
from code_reviewer.indexing.builder import build_map
from code_reviewer.indexing.summarize import parse_summary, summarize_map


class StubClient:
    def __init__(self, summary: str = "Модуль для тестов.") -> None:
        self.summary = summary
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, system: str, user: str) -> str:
        with self._lock:
            self.calls += 1
        return json.dumps({"summary": self.summary}, ensure_ascii=False)


def test_summarize_map_uses_cache_and_limit(project: Path, tmp_path: Path):
    repo_map = build_map(project, Config()).repo_map
    client = StubClient()
    cache_dir = tmp_path / "summary-cache"

    first = summarize_map(
        project,
        Config(),
        repo_map,
        client,
        limit=1,
        cache_dir=cache_dir,
    )
    assert first.requests == 1
    assert first.updated == 1
    assert first.skipped >= 1

    described = next(info for info in repo_map.files.values() if info.summary)
    described.summary = None

    second = summarize_map(
        project,
        Config(),
        repo_map,
        client,
        limit=1,
        cache_dir=cache_dir,
    )
    assert second.cached == 1
    assert second.requests == 1
    assert second.updated == 1
    assert client.calls == 2


def test_summarize_map_supports_parallel_jobs(project: Path, tmp_path: Path):
    (project / "extra.py").write_text("def extra_fn():\n    return 10\n", encoding="utf-8")
    repo_map = build_map(project, Config()).repo_map
    client = StubClient()
    cache_dir = tmp_path / "summary-cache"

    res = summarize_map(
        project,
        Config(),
        repo_map,
        client,
        limit=10,
        jobs=2,
        cache_dir=cache_dir,
    )
    assert res.updated >= 3
    assert res.requests == res.updated
    assert res.errors == []


def test_parse_summary_variants():
    assert parse_summary('{"summary": "Короткое описание."}') == "Короткое описание."
    assert parse_summary('```json\n{"summary": "Из блока."}\n```') == "Из блока."
    assert parse_summary('Вот JSON: {"summary": "Из прозы."}') == "Из прозы."
    assert parse_summary("  Просто текст  ") == "Просто текст"
    assert parse_summary('{"summary": ""}') == ""
