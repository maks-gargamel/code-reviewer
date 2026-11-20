"""Полное сканирование репозитория и оценка числа запросов к API перед запуском."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..core.config import CACHE_DIR, Config, service_dir
from ..core.errors import LLMError, UsageAppError
from ..core.models import RepoMap
from ..core.storage import ResponseCache, cache_key
from ..llm.keys import load_api_keys
from ..vcs import git
from ..vcs.diff import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_LINES_PER_FILE,
    SNIFF_BYTES,
    DiffSet,
    FileDiff,
    filter_and_limit_files,
    parse_diff,
    should_skip_path,
)
from .chunking import plan_chunks
from .context import build_context
from .ignore import IgnoreRules
from .prompt import build_prompt
from .secrets import redact

SCAN_CONFIRM_THRESHOLD = 10
SCAN_MAX_FILE_BYTES = 500_000

NON_CODE_LANGS: frozenset[str] = frozenset(
    {
        "markdown",
        "rst",
        "text",
        "json",
        "xml",
        "ini",
        "toml",
        "yaml",
        "csv",
    }
)

_TEST_DIR_NAMES: frozenset[str] = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "spec",
        "specs",
        "evals",
        "fixtures",
    }
)


@dataclass(slots=True)
class ScanChunkPreview:
    """Сведения об одном планируемом чанке при полном сканировании."""

    index: int
    files: list[str]
    tokens: int
    cached: bool


@dataclass(slots=True)
class ScanEstimate:
    """Предварительный расчёт стоимости и кэша для `check --all`."""

    scope: str
    files_total: int
    chunks: list[ScanChunkPreview] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    total_tokens: int = 0
    cached_chunks: int = 0
    uncached_requests: int = 0
    keys_available: int = 0
    is_local: bool = False


def is_test_file(path: str) -> bool:
    """Проверить, относится ли путь к тестам или фикстурам."""
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    parts = normalized.split("/")
    if any(part.lower() in _TEST_DIR_NAMES for part in parts[:-1]):
        return True

    filename = parts[-1]
    lower = filename.lower()
    if lower == "conftest.py" or lower.startswith("test_"):
        return True
    if (
        "_test." in lower
        or ".test." in lower
        or ".spec." in lower
        or "_spec." in lower
    ):
        return True

    stem = filename.rsplit(".", 1)[0]
    return stem.endswith(("Test", "Tests", "Spec"))


def _normalize_path_filters(paths: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in paths:
        norm = raw.replace("\\", "/").strip()
        while norm.startswith("./"):
            norm = norm[2:]
        norm = norm.strip("/")
        if norm in ("", "."):
            return []
        cleaned.append(norm)
    return list(dict.fromkeys(cleaned))


def _matches_filters(rel: str, filters: list[str]) -> tuple[bool, bool]:
    if not filters:
        return True, False
    exact = rel in filters
    matched = exact or any(rel.startswith(f"{flt}/") for flt in filters)
    return matched, exact


def _build_scan_scope(filters: list[str], *, include_tests: bool) -> str:
    label = "весь репозиторий"
    if filters:
        label += f" ({', '.join(filters)})"
    if not include_tests:
        label += ", без тестов"
    return label


def collect_repo_diffset(
    root: Path,
    repo_map: RepoMap,
    *,
    paths: Sequence[str] = (),
    include_tests: bool = True,
    use_ignore: bool = True,
    max_files: int | None = None,
    max_lines: int = DEFAULT_MAX_LINES_PER_FILE,
    max_file_bytes: int = SCAN_MAX_FILE_BYTES,
) -> DiffSet:
    """Собрать синтетический DiffSet по всем подходящим файлам карты репозитория."""
    filters = _normalize_path_filters(paths)
    rules = IgnoreRules.load(root) if use_ignore else IgnoreRules()
    items: list[FileDiff] = []
    matched_any_filter = False

    for rel, info in sorted(repo_map.files.items()):
        matched, exact = _matches_filters(rel, filters)
        if not matched:
            continue
        matched_any_filter = True

        if not exact and info.lang in NON_CODE_LANGS:
            continue
        if not include_tests and is_test_file(rel):
            continue
        if rules.matches(rel) or should_skip_path(rel):
            continue

        full = root / rel
        try:
            if not full.is_file():
                continue
            data = full.read_bytes()
        except OSError:
            continue

        if not data or len(data) > max_file_bytes or b"\x00" in data[:SNIFF_BYTES]:
            continue
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            continue

        patch = "\n".join(
            [
                f"diff --git a/{rel} b/{rel}",
                "new file mode 100644",
                "--- /dev/null",
                f"+++ b/{rel}",
                f"@@ -0,0 +1,{len(lines)} @@",
                *[f"+{line}" for line in lines],
            ]
        )
        parsed = parse_diff(patch, max_lines=max_lines)
        if parsed:
            items.append(parsed[0])

    if filters and not matched_any_filter:
        raise UsageAppError(
            f"По фильтру --path не найдено файлов в карте: {', '.join(filters)}"
        )

    scope = _build_scan_scope(filters, include_tests=include_tests)
    effective_max_files = max_files if max_files is not None else max(DEFAULT_MAX_FILES, len(items))
    return filter_and_limit_files(
        items,
        scope=scope,
        head=git.head_commit(root),
        ref_for_content=None,
        max_files=effective_max_files,
    )


def estimate_scan(
    root: Path,
    config: Config,
    repo_map: RepoMap,
    diffset: DiffSet,
    *,
    max_chunks: int | None = None,
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> ScanEstimate:
    """Оценить число чанков, токенов и запросов к API (с учётом кэша) без сетевых вызовов."""
    effective_max_chunks = max_chunks if max_chunks is not None else max(1, len(diffset.files))

    if config.privacy.redact_secrets:
        prepared_files = [
            replace(item, patch=redact(item.patch)[0]) for item in diffset.files
        ]
        prepared_diff = replace(diffset, files=prepared_files)
    else:
        prepared_diff = diffset

    plan = plan_chunks(
        prepared_diff,
        repo_map,
        budget=config.review.max_chunk_tokens,
        max_chunks=effective_max_chunks,
    )
    omitted = list(dict.fromkeys([*prepared_diff.omitted, *plan.omitted]))
    cache = ResponseCache(cache_dir or (service_dir(root) / CACHE_DIR))

    previews: list[ScanChunkPreview] = []
    total_tokens = 0
    cached_chunks = 0
    for index, files in enumerate(plan.chunks, start=1):
        chunk_diff = DiffSet(
            files=files,
            scope=prepared_diff.scope,
            head=prepared_diff.head,
            ref_for_content=prepared_diff.ref_for_content,
            omitted=omitted,
        )
        context = build_context(root, config, repo_map, chunk_diff.changed_paths)
        prompt = build_prompt(config, chunk_diff, context)
        tokens = prompt.estimated_tokens
        total_tokens += tokens

        hit = False
        if use_cache:
            key = cache_key(config.llm.model, prompt.system, prompt.user)
            hit = cache.get(key) is not None
        if hit:
            cached_chunks += 1

        previews.append(
            ScanChunkPreview(
                index=index,
                files=chunk_diff.changed_paths,
                tokens=tokens,
                cached=hit,
            )
        )

    try:
        keys_available = len(load_api_keys(config, root))
    except LLMError:
        keys_available = 0

    return ScanEstimate(
        scope=diffset.scope,
        files_total=len(diffset.files),
        chunks=previews,
        omitted=omitted,
        truncated=list(diffset.truncated),
        total_tokens=total_tokens,
        cached_chunks=cached_chunks,
        uncached_requests=len(previews) - cached_chunks,
        keys_available=keys_available,
        is_local=config.is_local_llm(),
    )
