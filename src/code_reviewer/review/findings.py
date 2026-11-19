"""Разбор и нормализация замечаний из ответа LLM."""

from __future__ import annotations

import json
import re
from typing import Any

from ..core.models import SEVERITY_ORDER, Finding

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*([\]}])")


def normalize_path(path: str) -> str:
    cleaned = path.strip().strip('"').strip("'").replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def parse_findings(raw: str) -> list[Finding]:
    """Разобрать JSON-ответ модели (объект `{findings: [...]}` или массив `[...]`)."""
    payload = _extract_json(raw)
    items: list[Any] = []

    if isinstance(payload, dict):
        for key in ("findings", "issues", "comments", "results"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
        else:
            if (payload.get("file") or payload.get("path")) and (
                payload.get("message") or payload.get("text")
            ):
                items = [payload]
    elif isinstance(payload, list):
        items = payload

    findings: list[Finding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file") or item.get("path") or "").strip()
        message = str(item.get("message") or item.get("text") or "").strip()
        if not file or not message:
            continue
        findings.append(
            Finding(
                file=normalize_path(file),
                line=_to_line(item.get("line")),
                severity=str(item.get("severity") or "warning"),
                category=str(item.get("category") or "logic"),
                message=message,
                suggestion=str(item.get("suggestion") or item.get("fix") or ""),
            )
        )
    return sort_findings(dedupe(findings))


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[str] = set()
    result: list[Finding] = []
    for item in findings:
        if item.id in seen:
            continue
        seen.add(item.id)
        result.append(item)
    return result


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (item.file, item.line, -SEVERITY_ORDER.get(item.severity, 0)),
    )


def filter_to_paths(findings: list[Finding], paths: list[str]) -> list[Finding]:
    """Отфильтровать галлюцинации вне diff и привести совпадения к каноническим путям."""
    canonical_by_norm: dict[str, str] = {normalize_path(p): p for p in paths}
    result: list[Finding] = []

    for item in findings:
        name = normalize_path(item.file)
        if name in canonical_by_norm:
            canonical = canonical_by_norm[name]
            result.append(item if item.file == canonical else item.with_file(canonical))
            continue

        suffix_matches = [
            canonical
            for norm, canonical in canonical_by_norm.items()
            if norm.endswith("/" + name)
        ]
        if len(suffix_matches) == 1:
            result.append(item.with_file(suffix_matches[0]))

    return result


def worst_severity(findings: list[Finding]) -> str | None:
    if not findings:
        return None
    return max(findings, key=lambda item: SEVERITY_ORDER.get(item.severity, 0)).severity


def worst_rank(findings: list[Finding]) -> int:
    return max((SEVERITY_ORDER.get(item.severity, 0) for item in findings), default=0)


def _to_line(value: object) -> int:
    try:
        line = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return max(0, line)


def _extract_json(raw: str) -> object:
    text = _FENCE.sub("", raw).strip()
    if not text:
        return None
    for candidate in _candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _TRAILING_COMMA.sub(r"\1", candidate)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue
    return None


def _candidates(text: str) -> list[str]:
    result = [text]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            result.append(text[start : end + 1])
    return result
