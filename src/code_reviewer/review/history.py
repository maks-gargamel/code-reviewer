"""История ревью (`.code-reviewer/history.jsonl`) и расчёт метрик."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import HISTORY_NAME, service_dir
from ..core.models import utc_now

SEVERITIES = ("error", "warning", "info")


@dataclass(slots=True)
class HistoryEntry:
    created_at: str
    commit: str | None = None
    branch: str | None = None
    scope: str = ""
    files: int = 0
    changed_files: list[str] = field(default_factory=list)
    chunks: int = 0
    requests: int = 0
    cached: int = 0
    duration: float = 0.0
    suppressed: int = 0
    redactions: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ids(self) -> set[str]:
        return {
            str(item["id"])
            for item in self.findings
            if isinstance(item, dict) and item.get("id")
        }

    @property
    def severity_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(SEVERITIES, 0)
        for item in self.findings:
            severity = str(item.get("severity", "warning"))
            counts[severity] = counts.get(severity, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "commit": self.commit,
            "branch": self.branch,
            "scope": self.scope,
            "files": self.files,
            "changed_files": list(self.changed_files),
            "chunks": self.chunks,
            "requests": self.requests,
            "cached": self.cached,
            "duration": round(self.duration, 3),
            "suppressed": self.suppressed,
            "redactions": self.redactions,
            "findings": self.findings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryEntry:
        findings = data.get("findings", [])
        raw_changed = data.get("changed_files", [])
        return cls(
            created_at=str(data.get("created_at") or utc_now()),
            commit=data.get("commit"),
            branch=data.get("branch"),
            scope=str(data.get("scope", "")),
            files=int(data.get("files", 0)),
            changed_files=[str(p) for p in raw_changed] if isinstance(raw_changed, list) else [],
            chunks=int(data.get("chunks", 0)),
            requests=int(data.get("requests", 0)),
            cached=int(data.get("cached", 0)),
            duration=float(data.get("duration", 0.0)),
            suppressed=int(data.get("suppressed", 0)),
            redactions=int(data.get("redactions", 0)),
            findings=[item for item in findings if isinstance(item, dict)]
            if isinstance(findings, list)
            else [],
        )


@dataclass(slots=True)
class Metrics:
    reviews: int = 0
    findings_total: int = 0
    unique_findings: int = 0
    new: int = 0
    repeated: int = 0
    last_fixed: int = 0
    acceptance: float = 0.0
    average_per_review: float = 0.0
    by_severity: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviews": self.reviews,
            "findings_total": self.findings_total,
            "unique_findings": self.unique_findings,
            "new": self.new,
            "repeated": self.repeated,
            "last_fixed": self.last_fixed,
            "acceptance": round(self.acceptance, 4),
            "average_per_review": round(self.average_per_review, 2),
            "by_severity": dict(self.by_severity),
            "by_category": dict(self.by_category),
        }


def history_path(root: Path) -> Path:
    return service_dir(root) / HISTORY_NAME


def append_entry(
    root: Path,
    *,
    commit: str | None,
    branch: str | None,
    scope: str,
    changed_files: list[str],
    chunks: int,
    cached_chunks: int,
    duration: float,
    suppressed: int,
    redactions: int,
    findings: list[dict[str, Any]],
) -> HistoryEntry:
    entry = HistoryEntry(
        created_at=utc_now(),
        commit=commit,
        branch=branch,
        scope=scope,
        files=len(changed_files),
        changed_files=list(changed_files),
        chunks=chunks,
        requests=max(0, chunks - cached_chunks),
        cached=cached_chunks,
        duration=duration,
        suppressed=suppressed,
        redactions=redactions,
        findings=findings,
    )
    path = history_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    return entry


def load_history(root: Path, *, limit: int | None = None) -> list[HistoryEntry]:
    path = history_path(root)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    entries: list[HistoryEntry] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(HistoryEntry.from_dict(payload))

    return entries[-limit:] if limit and limit > 0 else entries


def compute_metrics(entries: list[HistoryEntry]) -> Metrics:
    metrics = Metrics(reviews=len(entries))
    if not entries:
        return metrics

    seen: set[str] = set()
    by_severity = dict.fromkeys(SEVERITIES, 0)
    by_category: dict[str, int] = {}

    for entry in entries:
        metrics.findings_total += len(entry.findings)
        for item in entry.findings:
            finding = str(item.get("id") or "")
            if finding:
                if finding in seen:
                    metrics.repeated += 1
                else:
                    metrics.new += 1
                    seen.add(finding)
            severity = str(item.get("severity", "warning"))
            by_severity[severity] = by_severity.get(severity, 0) + 1
            category = str(item.get("category", "logic"))
            by_category[category] = by_category.get(category, 0) + 1

    metrics.unique_findings = len(seen)
    metrics.by_severity = dict(sorted(by_severity.items(), key=lambda kv: -kv[1]))
    metrics.by_category = dict(sorted(by_category.items(), key=lambda kv: -kv[1])[:8])
    metrics.average_per_review = metrics.findings_total / len(entries)

    if len(entries) > 1:
        previous_entry = entries[-2]
        current_entry = entries[-1]
        if current_entry.changed_files:
            relevant_files = set(current_entry.changed_files)
            previous_ids = {
                str(item["id"])
                for item in previous_entry.findings
                if isinstance(item, dict)
                and item.get("id")
                and str(item.get("file", "")) in relevant_files
            }
        elif current_entry.scope == previous_entry.scope:
            previous_ids = previous_entry.ids
        else:
            previous_ids = set()

        if previous_ids:
            metrics.last_fixed = len(previous_ids - current_entry.ids)
            metrics.acceptance = metrics.last_fixed / len(previous_ids)

    return metrics
