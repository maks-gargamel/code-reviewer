"""Генерация кратких AI-сводок по модулям карты с вечным кэшированием."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import SUMMARY_CACHE_DIR, Config, service_dir
from ..core.errors import LLMError
from ..core.models import FileInfo, RepoMap
from ..core.storage import ResponseCache, cache_key
from ..llm.protocol import LLMClient
from .parsers.base import collapse

SUMMARY_SYSTEM = (
    "Ты описываешь назначение модулей программного кода. "
    'Отвечай строго JSON-объектом вида {"summary": "..."}.'
)
MAX_SYMBOLS = 24
MAX_SUMMARY_CHARS = 400
MAX_PROMPT_CHARS = 4000
SUMMARY_LIMIT = 20

SOURCE_LANGUAGES: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "go",
        "rust",
        "java",
        "kotlin",
        "csharp",
        "ruby",
        "php",
        "c",
        "cpp",
        "scala",
        "swift",
        "lua",
        "dart",
    }
)

ProgressCallback = Callable[[int, int, str], None]
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


@dataclass(slots=True)
class SummaryResult:
    updated: int = 0
    cached: int = 0
    requests: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def summarized_count(repo_map: RepoMap) -> int:
    return sum(1 for info in repo_map.files.values() if info.summary)


def summarize_map(
    root: Path,
    config: Config,
    repo_map: RepoMap,
    client: LLMClient,
    *,
    limit: int = SUMMARY_LIMIT,
    jobs: int = 1,
    use_cache: bool = True,
    cache_dir: Path | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> SummaryResult:
    """Заполнить `FileInfo.summary` для исходников, используя кэш и лимит запросов."""
    result = SummaryResult()
    targets = [
        path
        for path in sorted(repo_map.files)
        if _eligible(repo_map.files[path])
        and (force or not repo_map.files[path].summary)
    ]
    if not targets:
        return result

    cache = ResponseCache(cache_dir or (service_dir(root) / SUMMARY_CACHE_DIR))
    total = len(targets)
    pending: list[tuple[int, str, FileInfo, str, str, str]] = []

    for index, path in enumerate(targets, start=1):
        info = repo_map.files[path]
        system, user = build_summary_prompt(path, info)
        key = cache_key(config.llm.model, system, user)
        raw = cache.get(key) if use_cache else None

        if raw is not None:
            summary = parse_summary(raw)
            if summary:
                info.summary = summary
                result.cached += 1
            else:
                result.skipped += 1
            if progress is not None:
                progress(index, total, path)
            continue

        if len(pending) >= max(0, limit):
            result.skipped += 1
            continue

        pending.append((index, path, info, system, user, key))

    if not pending:
        return result

    worker_count = max(1, min(max(1, jobs), len(pending)))
    if worker_count == 1:
        for index, path, info, system, user, key in pending:
            try:
                raw = client.complete(system, user)
            except LLMError as exc:
                result.errors.append(f"{path}: {exc}")
                remaining = sum(1 for item in pending if item[0] >= index)
                result.skipped += remaining
                break
            result.requests += 1
            _store_summary(info, raw, key, config.llm.model, cache, use_cache, result, path)
            if progress is not None:
                progress(index, total, path)
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(client.complete, system, user): (index, path, info, key)
                for index, path, info, system, user, key in pending
            }
            for future in as_completed(futures):
                index, path, info, key = futures[future]
                try:
                    raw = future.result()
                except LLMError as exc:
                    result.errors.append(f"{path}: {exc}")
                    result.skipped += 1
                    continue
                result.requests += 1
                _store_summary(info, raw, key, config.llm.model, cache, use_cache, result, path)
                if progress is not None:
                    progress(index, total, path)

    return result


def _store_summary(
    info: FileInfo,
    raw: str,
    key: str,
    model: str,
    cache: ResponseCache,
    use_cache: bool,
    result: SummaryResult,
    path: str,
) -> None:
    summary = parse_summary(raw)
    if summary:
        info.summary = summary
        result.updated += 1
        if use_cache:
            cache.put(key, model, raw)
    else:
        result.errors.append(f"{path}: пустая сводка")
        result.skipped += 1


def _eligible(info: FileInfo) -> bool:
    return bool(info.symbols) and info.lang in SOURCE_LANGUAGES


def build_summary_prompt(path: str, info: FileInfo) -> tuple[str, str]:
    lines = [
        "Опиши назначение модуля по его метаданным.",
        f"Файл: {path}",
        f"Язык: {info.lang}",
    ]
    if info.doc:
        lines.append(f"Doc: {collapse(info.doc, 240) or info.doc}")
    lines.append("Символы:")
    for symbol in info.symbols[:MAX_SYMBOLS]:
        declaration = collapse(symbol.signature or symbol.name, 200) or symbol.name
        prefix = f"{symbol.parent}." if symbol.parent else ""
        lines.append(f"- {prefix}{declaration}")
    if len(info.symbols) > MAX_SYMBOLS:
        lines.append(f"… ещё {len(info.symbols) - MAX_SYMBOLS} символов")
    lines.append("")
    lines.append(
        'Верни JSON {"summary": "1–2 предложения о роли модуля в проекте."} '
        "Без markdown и лишних ключей."
    )
    return SUMMARY_SYSTEM, "\n".join(lines)[:MAX_PROMPT_CHARS]


def parse_summary(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    data: object = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = None

    if isinstance(data, dict):
        value = data.get("summary")
        if value is None:
            value = data.get("description")
        if isinstance(value, str):
            return _clean(value)
    return _clean(text)


def _clean(value: str) -> str:
    text = (collapse(value, MAX_SUMMARY_CHARS) or "").strip().strip('"').strip()
    if text.lower().startswith("summary:"):
        text = text.split(":", 1)[1].strip()
    return text
