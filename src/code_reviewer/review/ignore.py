"""Подавление замечаний: `.code-reviewerignore` и inline-директивы по полному тексту файла."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.models import Finding
from ..core.patterns import matches_patterns
from ..vcs import git
from ..vcs.diff import DiffSet, FileDiff

IGNORE_FILE = ".code-reviewerignore"
DIRECTIVE = re.compile(r"code-reviewer:\s*(ignore-file|ignore)\b", re.IGNORECASE)


def directive_kind(line: str) -> str | None:
    """Распознать директиву `code-reviewer: ignore` или `ignore-file` в строке."""
    match = DIRECTIVE.search(line)
    return match.group(1).lower() if match else None


def scan_inline_directives(source: str) -> tuple[bool, set[int]]:
    """Просканировать весь исходный текст файла на директивы подавления."""
    ignore_file = False
    ignored_lines: set[int] = set()
    for lineno, line in enumerate(source.splitlines(), start=1):
        kind = directive_kind(line)
        if kind == "ignore-file":
            ignore_file = True
        elif kind == "ignore":
            ignored_lines.add(lineno)
            ignored_lines.add(lineno + 1)
    return ignore_file, ignored_lines


def _scan_patch_directives(file_diff: FileDiff) -> tuple[bool, set[int]]:
    """Fallback-разбор директив по строкам патча (если файл недоступен на диске и в git)."""
    ignore_file = False
    ignored_lines: set[int] = set()
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
    in_hunk = False
    new_line = 0

    for line in file_diff.patch.splitlines():
        hunk = hunk_re.match(line)
        if hunk:
            in_hunk = True
            new_line = int(hunk.group("start"))
            continue
        if not in_hunk or line.startswith("\\") or line.startswith("-"):
            continue
        kind = directive_kind(line)
        if kind == "ignore-file":
            ignore_file = True
        elif kind == "ignore":
            ignored_lines.add(new_line)
            ignored_lines.add(new_line + 1)
        new_line += 1

    return ignore_file, ignored_lines


def collect_inline_ignores(root: Path, diffset: DiffSet) -> dict[str, tuple[bool, set[int]]]:
    """Собрать inline-игноры по полному содержимому каждой проверяемой версии файла."""
    collected: dict[str, tuple[bool, set[int]]] = {}
    for file_diff in diffset.files:
        source: str | None = None
        if diffset.ref_for_content is not None:
            source = git.read_file_at_ref(root, diffset.ref_for_content, file_diff.path)
        else:
            full = root / file_diff.path
            if full.is_file():
                try:
                    source = full.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    source = None

        if source is not None:
            collected[file_diff.path] = scan_inline_directives(source)
        else:
            collected[file_diff.path] = _scan_patch_directives(file_diff)
    return collected


@dataclass(slots=True)
class IgnoreRules:
    patterns: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> IgnoreRules:
        path = root / IGNORE_FILE
        if not path.is_file():
            return cls()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return cls()
        patterns = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return cls(patterns)

    def matches(self, path: str) -> bool:
        return bool(self.patterns) and matches_patterns(path, self.patterns)


def filter_findings(
    findings: list[Finding],
    rules: IgnoreRules,
    inline_ignores: dict[str, tuple[bool, set[int]]],
) -> tuple[list[Finding], int]:
    """Убрать замечания, подавленные через `.code-reviewerignore` или inline-директивы."""
    kept: list[Finding] = []
    dropped = 0

    for item in findings:
        if rules.matches(item.file):
            dropped += 1
            continue

        file_ignore = inline_ignores.get(item.file)
        if file_ignore is not None:
            ignore_file, ignored_lines = file_ignore
            if ignore_file or item.line in ignored_lines:
                dropped += 1
                continue

        kept.append(item)

    return kept, dropped
