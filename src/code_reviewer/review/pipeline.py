"""Оркестрация пайплайна ревью: diff -> секреты -> чанки -> промпт -> LLM -> фильтрация."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import CACHE_DIR, Config, service_dir
from ..core.models import Finding, RepoMap
from ..core.storage import ResponseCache, cache_key
from ..llm.client import client_from_config
from ..llm.protocol import LLMClient
from ..vcs.diff import DiffScope, DiffSet, collect_diff
from .chunking import plan_chunks
from .context import ReviewContext, build_context
from .findings import dedupe, filter_to_paths, parse_findings, sort_findings
from .ignore import IgnoreRules, collect_inline_ignores, filter_findings
from .prompt import PromptBundle, build_prompt
from .secrets import redact

ProgressCallback = Callable[[int, int, int], None]


@dataclass(slots=True)
class VerifySummary:
    """Сверка с результатами прошлого ревью."""

    closed: list[Finding] = field(default_factory=list)
    still_open: list[Finding] = field(default_factory=list)


@dataclass(slots=True)
class ReviewResult:
    scope: str
    diffset: DiffSet
    prompts: list[PromptBundle] = field(default_factory=list)
    contexts: list[ReviewContext] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    cached_chunks: int = 0
    redactions: int = 0
    suppressed: int = 0
    duration: float = 0.0
    raw_responses: list[str] = field(default_factory=list)
    dry_run: bool = False
    remote_warning_needed: bool = False

    @property
    def prompt(self) -> PromptBundle:
        """Первый промпт для обратной совместимости и одночанкового dry-run."""
        return self.prompts[0] if self.prompts else PromptBundle("", "")

    @property
    def chunks(self) -> int:
        return len(self.prompts)

    @property
    def changed_files(self) -> list[str]:
        return self.diffset.changed_paths


def compute_verify(previous: list[Finding], current: list[Finding]) -> VerifySummary:
    """Сопоставить замечания текущего прогона с прошлым по стабильным ID."""
    current_ids = {item.id for item in current}
    return VerifySummary(
        closed=[item for item in previous if item.id not in current_ids],
        still_open=[item for item in previous if item.id in current_ids],
    )


def run_review(
    root: Path,
    config: Config,
    repo_map: RepoMap,
    *,
    scope: DiffScope | str = DiffScope.AUTO,
    commit: str | None = None,
    range_: str | None = None,
    since_commit: str | None = None,
    diffset: DiffSet | None = None,
    client: LLMClient | None = None,
    use_cache: bool = True,
    use_ignore: bool = True,
    baseline_ids: set[str] | None = None,
    dry_run: bool = False,
    max_files: int | None = None,
    max_chunks: int | None = None,
    budget: int | None = None,
    cache_dir: Path | None = None,
    jobs: int | None = None,
    progress: ProgressCallback | None = None,
) -> ReviewResult:
    """Выполнить полный цикл ревью изменений."""
    started = time.perf_counter()

    if diffset is None:
        collect_kwargs: dict[str, object] = {}
        if max_files is not None:
            collect_kwargs["max_files"] = max_files
        diffset = collect_diff(
            root,
            scope=scope,
            commit=commit,
            range_=range_,
            since_commit=since_commit,
            **collect_kwargs,  # type: ignore[arg-type]
        )

    result = ReviewResult(scope=diffset.scope, diffset=diffset)
    if diffset.is_empty():
        result.duration = time.perf_counter() - started
        return result

    if config.privacy.redact_secrets:
        for item in diffset.files:
            item.patch, count = redact(item.patch)
            result.redactions += count

    plan = plan_chunks(
        diffset,
        repo_map,
        budget=budget or config.review.max_chunk_tokens,
        max_chunks=max_chunks or config.review.max_chunks,
    )
    result.omitted = list(dict.fromkeys([*diffset.omitted, *plan.omitted]))

    cache = ResponseCache(cache_dir or (service_dir(root) / CACHE_DIR))
    responses: dict[int, str] = {}
    chunk_paths_by_index: dict[int, list[str]] = {}
    pending: list[tuple[int, PromptBundle, str, int]] = []
    total = len(plan.chunks)

    for index, files in enumerate(plan.chunks, start=1):
        chunk_diff = DiffSet(
            files=files,
            scope=diffset.scope,
            head=diffset.head,
            ref_for_content=diffset.ref_for_content,
            omitted=result.omitted,
        )
        chunk_paths_by_index[index] = chunk_diff.changed_paths
        context = build_context(root, config, repo_map, chunk_diff.changed_paths)
        prompt = build_prompt(config, chunk_diff, context)
        result.prompts.append(prompt)
        result.contexts.append(context)

        if dry_run:
            if progress is not None:
                progress(index, total, len(files))
            continue

        key = cache_key(config.llm.model, prompt.system, prompt.user)
        raw = cache.get(key) if use_cache else None
        if raw is None:
            pending.append((index, prompt, key, len(files)))
        else:
            responses[index] = raw
            result.cached_chunks += 1
            if progress is not None:
                progress(index, total, len(files))

    if dry_run:
        result.dry_run = True
        result.duration = time.perf_counter() - started
        return result

    active_client = client
    if pending and active_client is None:
        active_client = client_from_config(config, root)

    if active_client is not None and pending:
        if not config.is_local_llm() and config.privacy.warn_on_remote:
            result.remote_warning_needed = True

        worker_count = max(1, min(max(1, jobs or config.review.jobs), len(pending)))
        if worker_count == 1:
            for index, prompt, key, files_count in pending:
                raw = active_client.complete(prompt.system, prompt.user)
                responses[index] = raw
                if use_cache:
                    cache.put(key, config.llm.model, raw)
                if progress is not None:
                    progress(index, total, files_count)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = {
                    pool.submit(active_client.complete, prompt.system, prompt.user): (
                        index,
                        key,
                        files_count,
                    )
                    for index, prompt, key, files_count in pending
                }
                for future in as_completed(futures):
                    index, key, files_count = futures[future]
                    raw = future.result()
                    responses[index] = raw
                    if use_cache:
                        cache.put(key, config.llm.model, raw)
                    if progress is not None:
                        progress(index, total, files_count)

    ordered_indices = sorted(responses)
    result.raw_responses = [responses[index] for index in ordered_indices]

    collected: list[Finding] = []
    for index in ordered_indices:
        parsed = parse_findings(responses[index])
        allowed = chunk_paths_by_index.get(index, diffset.changed_paths)
        collected.extend(filter_to_paths(parsed, allowed))

    findings = sort_findings(dedupe(collected))

    if use_ignore:
        rules = IgnoreRules.load(root)
        inline_ignores = collect_inline_ignores(root, diffset)
        findings, ignored = filter_findings(findings, rules, inline_ignores)
        result.suppressed += ignored

    if baseline_ids:
        before = len(findings)
        findings = [item for item in findings if item.id not in baseline_ids]
        result.suppressed += before - len(findings)

    result.findings = findings
    result.duration = time.perf_counter() - started
    return result
