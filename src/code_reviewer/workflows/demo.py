"""Офлайн-демонстрация пайплайна ревью без сети, API-ключа и git."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Config
from ..core.models import RepoMap
from ..indexing.parsers.python_ast import PythonAdapter
from ..review.pipeline import ReviewResult, run_review
from ..vcs.diff import synthetic_diff_from_maps

DEMO_FILE = "app.py"
DEMO_BEFORE = '''\
def find_user(cursor, name):
    cursor.execute("SELECT * FROM users WHERE name = %s", [name])
    return cursor.fetchall()
'''
DEMO_AFTER = '''\
def find_user(cursor, name):
    query = f"SELECT * FROM users WHERE name = '{name}'"
    cursor.execute(query)
    return cursor.fetchall()
'''
DEMO_RESPONSE = json.dumps(
    {
        "findings": [
            {
                "file": DEMO_FILE,
                "line": 2,
                "severity": "error",
                "category": "security",
                "message": (
                    "SQL-инъекция: имя пользователя подставляется "
                    "в SQL-запрос через f-строку."
                ),
                "suggestion": "Верните параметризованный запрос с плейсхолдером %s.",
            }
        ]
    },
    ensure_ascii=False,
)


@dataclass(slots=True)
class DemoResult:
    review: ReviewResult
    before: str
    after: str

    @property
    def requests(self) -> int:
        return 0


class RecordedClient:
    """Клиент-заглушка, возвращающий заранее записанный ответ модели."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        del system, user
        self.calls += 1
        return self.response


def run_demo(config: Config | None = None) -> DemoResult:
    """Прогнать полный цикл ревью на учебном примере без сети, ключа и git."""
    cfg = config or Config()
    client = RecordedClient(DEMO_RESPONSE)

    with tempfile.TemporaryDirectory(prefix="code-reviewer-demo-") as tmp:
        work = Path(tmp)
        target = work / DEMO_FILE
        target.write_text(DEMO_AFTER, encoding="utf-8")
        raw_bytes = DEMO_AFTER.encode("utf-8")
        digest = hashlib.sha256(raw_bytes).hexdigest()

        info = PythonAdapter().extract(
            DEMO_FILE,
            DEMO_AFTER,
            size=len(raw_bytes),
            mtime=1.0,
            digest=digest,
        )
        repo_map = RepoMap(
            files={DEMO_FILE: info},
            root=str(work),
            stats={"files": 1, "symbols": len(info.symbols), "edges_internal": 0},
        )
        diffset = synthetic_diff_from_maps(
            {DEMO_FILE: DEMO_BEFORE},
            {DEMO_FILE: DEMO_AFTER},
            scope="демо-пример",
        )
        review = run_review(
            work,
            cfg,
            repo_map,
            diffset=diffset,
            client=client,
            use_cache=False,
            use_ignore=False,
        )

    return DemoResult(review=review, before=DEMO_BEFORE, after=DEMO_AFTER)
