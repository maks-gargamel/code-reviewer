"""Экспорт замечаний в формат SARIF 2.1.0 для CI и систем анализа кода."""

from __future__ import annotations

import json
from typing import Any

from .. import __version__
from ..core.models import Finding

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
LEVELS = {"error": "error", "warning": "warning", "info": "note"}


def findings_sarif(findings: list[Finding]) -> str:
    """Сформировать валидный SARIF 2.1.0 JSON для списка замечаний."""
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for item in findings:
        rules.setdefault(
            item.category,
            {"id": item.category, "shortDescription": {"text": item.category}},
        )
        result: dict[str, Any] = {
            "ruleId": item.category,
            "level": LEVELS.get(item.severity, "warning"),
            "message": {"text": item.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": item.file},
                        "region": {"startLine": max(1, item.line)},
                    }
                }
            ],
            "partialFingerprints": {"codeReviewerId": item.id},
        }
        if item.suggestion:
            result["properties"] = {"suggestion": item.suggestion}
        results.append(result)

    payload: dict[str, Any] = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "code-reviewer",
                        "version": __version__,
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
