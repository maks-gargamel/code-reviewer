from __future__ import annotations

import json
import threading
from pathlib import Path

from code_reviewer.core.config import Config
from code_reviewer.core.models import ReviewLanguage
from code_reviewer.indexing.builder import build_map
from code_reviewer.review.pipeline import run_review
from code_reviewer.vcs.diff import synthetic_diff_from_maps


class StubClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0
        self.last_user = ""
        self.last_system = ""

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.last_system = system
        self.last_user = user
        return self.response


RESPONSE = json.dumps(
    {
        "findings": [
            {
                "file": "pkg/utils.py",
                "line": 2,
                "severity": "warning",
                "category": "logic",
                "message": "Возвращается захардкоженное значение",
                "suggestion": "Вычислить результат",
            },
            {
                "file": "ghost.py",
                "line": 1,
                "severity": "error",
                "category": "bug",
                "message": "Файла нет в diff",
            },
        ]
    },
    ensure_ascii=False,
)


def _diff_for_utils(before: str, after: str):
    return synthetic_diff_from_maps({"pkg/utils.py": before}, {"pkg/utils.py": after}, scope="diff")


def test_review_parses_caches_and_filters_hallucinations(project: Path):
    repo_map = build_map(project, Config()).repo_map
    diffset = _diff_for_utils(
        "def helper() -> int:\n    return 1\n",
        "def helper() -> int:\n    return 2\n",
    )
    client = StubClient(RESPONSE)

    first = run_review(project, Config(), repo_map, diffset=diffset, client=client)
    second = run_review(project, Config(), repo_map, diffset=diffset, client=client)

    assert client.calls == 1
    assert second.cached_chunks == 1
    assert [f.file for f in first.findings] == ["pkg/utils.py"]
    assert "### pkg/models.py" in first.prompt.user
    assert first.remote_warning_needed is True


def test_review_language_switches_prompt_language(project: Path):
    repo_map = build_map(project, Config()).repo_map
    diffset = _diff_for_utils("x = 1\n", "x = 2\n")

    cfg_en = Config()
    cfg_en.review.language = ReviewLanguage.EN
    res_en = run_review(project, cfg_en, repo_map, diffset=diffset, dry_run=True)
    assert "You are an experienced software engineer" in res_en.prompt.system

    cfg_ru = Config()
    cfg_ru.review.language = ReviewLanguage.RU
    res_ru = run_review(project, cfg_ru, repo_map, diffset=diffset, dry_run=True)
    assert "Ты — опытный разработчик" in res_ru.prompt.system


def test_review_secrets_and_baseline_and_local_warning(project: Path):
    repo_map = build_map(project, Config()).repo_map
    diffset = _diff_for_utils(
        'API_KEY = "empty"\n',
        'API_KEY = "sk-abcdefghijklmnopqrstuvwx"\n',
    )
    cfg = Config()
    cfg.llm.provider = "ollama"
    client = StubClient('{"findings": []}')

    res = run_review(project, cfg, repo_map, diffset=diffset, client=client, use_cache=False)
    assert res.redactions >= 1
    assert "sk-abcdefghijklmnop" not in res.prompt.user
    assert res.remote_warning_needed is False


def test_parallel_chunks_when_chunks_exceed_jobs(project: Path):
    # Регрессионный тест: 5 чанков при jobs=2 не должны ломаться.
    repo_map = build_map(project, Config()).repo_map
    names = [f"f{i}.py" for i in range(5)]
    before = {name: "x = 1\n" for name in names}
    after = {name: "x = 2\n" for name in names}
    diffset = synthetic_diff_from_maps(before, after, scope="diff")

    lock = threading.Lock()
    active = 0
    max_active = 0

    class ParallelClient:
        def complete(self, system: str, user: str) -> str:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                for name in names:
                    if f"- {name} (" in user:
                        return json.dumps(
                            {
                                "findings": [
                                    {
                                        "file": name,
                                        "line": 1,
                                        "severity": "warning",
                                        "category": "logic",
                                        "message": f"Проблема в {name}",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                return '{"findings": []}'
            finally:
                with lock:
                    active -= 1

    res = run_review(
        project,
        Config(),
        repo_map,
        diffset=diffset,
        client=ParallelClient(),
        use_cache=False,
        budget=100,
        max_chunks=5,
        jobs=2,
    )
    assert res.chunks == 5
    assert {f.file for f in res.findings} == set(names)
