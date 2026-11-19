"""Сбор и разбор git diff (и синтетического unified diff)."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..core.errors import GitError, UsageAppError
from . import git

DEFAULT_MAX_FILES = 60
DEFAULT_MAX_LINES_PER_FILE = 600
UNTRACKED_MAX_BYTES = 200_000
SNIFF_BYTES = 8192

IGNORED_DIFF_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".code-reviewer",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
    }
)

IGNORED_DIFF_FILES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "poetry.lock",
        "pdm.lock",
        "Pipfile.lock",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "uv.lock",
        ".DS_Store",
    }
)

BINARY_DIFF_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".tiff",
        ".psd",
        ".svg",
        ".pdf",
        ".zip",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".tar",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".wav",
        ".flac",
        ".ogg",
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".o",
        ".a",
        ".jar",
        ".war",
        ".class",
        ".whl",
        ".egg",
        ".sqlite",
        ".db",
        ".parquet",
        ".npy",
        ".npz",
        ".pt",
        ".onnx",
    }
)

DIFF_HEADER = re.compile(r"^diff --git a/(?P<old>.+) b/(?P<new>.+)$")
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")


class DiffScope(StrEnum):
    AUTO = "auto"
    STAGED = "staged"
    UNSTAGED = "unstaged"
    LAST = "last"
    COMMIT = "commit"
    RANGE = "range"
    SINCE = "since"


@dataclass(slots=True)
class FileDiff:
    """Изменения одного файла в рамках diff."""

    path: str
    status: str = "modified"  # modified | added | deleted | renamed
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    old_path: str | None = None
    is_binary: bool = False
    is_untracked: bool = False
    truncated: bool = False
    changed_lines: set[int] = field(default_factory=set)


@dataclass(slots=True)
class DiffSet:
    """Набор изменений для ревью."""

    files: list[FileDiff] = field(default_factory=list)
    scope: str = ""
    head: str | None = None
    ref_for_content: str | None = None
    omitted: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)

    @property
    def changed_paths(self) -> list[str]:
        return [item.path for item in self.files]

    @property
    def additions(self) -> int:
        return sum(item.additions for item in self.files)

    @property
    def deletions(self) -> int:
        return sum(item.deletions for item in self.files)

    def is_empty(self) -> bool:
        return not self.files


def _diff_args(*extra: str) -> tuple[str, ...]:
    return ("diff", "-U3", "--no-color", "--no-ext-diff", *extra)


def _has_changes(text: str) -> bool:
    return any(line.startswith("diff --git ") for line in text.splitlines())


def _range_end_ref(range_: str) -> str | None:
    if "..." in range_:
        right = range_.split("...", 1)[1].strip()
        return right or "HEAD"
    if ".." in range_:
        right = range_.split("..", 1)[1].strip()
        return right or "HEAD"
    return None


def _strip_diff_prefix(raw: str, prefix: str) -> str:
    cleaned = raw.rstrip("\t\r\n")
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1]
    return cleaned.removeprefix(prefix)


def should_skip_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if any(part in IGNORED_DIFF_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in IGNORED_DIFF_FILES:
        return True
    dot = name.rfind(".")
    extension = name[dot:].lower() if dot != -1 else ""
    return extension in BINARY_DIFF_EXTENSIONS


def parse_diff(
    text: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES_PER_FILE,
) -> list[FileDiff]:
    """Разобрать unified diff в список FileDiff."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    patch: list[str] = []
    in_hunk = False
    new_line = 0

    def flush() -> None:
        nonlocal current, patch
        if current is None:
            return
        body = patch
        if len(body) > max_lines:
            body = body[:max_lines] + ["... (diff обрезан по лимиту строк)"]
            current.truncated = True
        current.patch = "\n".join(body)
        files.append(current)
        current = None
        patch = []

    for line in text.splitlines():
        header = DIFF_HEADER.match(line)
        if header:
            flush()
            current = FileDiff(
                path=header.group("new").strip(),
                old_path=header.group("old").strip(),
            )
            patch = [line]
            in_hunk = False
            new_line = 0
            continue
        if current is None:
            continue

        patch.append(line)

        hunk = HUNK_HEADER.match(line)
        if hunk:
            in_hunk = True
            new_line = int(hunk.group("start"))
            continue

        if not in_hunk:
            if line.startswith("new file mode"):
                current.status = "added"
            elif line.startswith("deleted file mode"):
                current.status = "deleted"
            elif line.startswith("rename from "):
                current.status = "renamed"
                current.old_path = line.removeprefix("rename from ").strip()
            elif line.startswith("rename to "):
                current.status = "renamed"
                current.path = line.removeprefix("rename to ").strip()
            elif line.startswith("--- a/"):
                current.old_path = _strip_diff_prefix(line.removeprefix("--- "), "a/")
            elif line.startswith("+++ b/"):
                current.path = _strip_diff_prefix(line.removeprefix("+++ "), "b/")
            elif line.startswith("Binary files"):
                current.is_binary = True
            continue

        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            current.additions += 1
            current.changed_lines.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            current.deletions += 1
        else:
            new_line += 1

    flush()
    return files


def filter_and_limit_files(
    files: list[FileDiff],
    *,
    scope: str,
    head: str | None = None,
    ref_for_content: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
) -> DiffSet:
    """Отфильтровать удалённые, бинарные и служебные файлы и применить лимит max_files."""
    prepared: list[FileDiff] = []
    for item in sorted(files, key=lambda f: f.path):
        if item.status == "deleted":
            continue
        if should_skip_path(item.path) or item.is_binary:
            continue
        prepared.append(item)

    filtered: list[FileDiff] = []
    omitted: list[str] = []
    for item in prepared:
        if len(filtered) >= max_files:
            omitted.append(item.path)
        else:
            filtered.append(item)

    return DiffSet(
        files=filtered,
        scope=scope,
        head=head,
        ref_for_content=ref_for_content,
        omitted=omitted,
        truncated=[item.path for item in filtered if item.truncated],
    )


def collect_diff(
    root: Path,
    *,
    scope: DiffScope | str = DiffScope.AUTO,
    commit: str | None = None,
    range_: str | None = None,
    since_commit: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_lines: int = DEFAULT_MAX_LINES_PER_FILE,
) -> DiffSet:
    """Собрать diff из git для выбранного режима."""
    mode = DiffScope(scope)
    head = git.head_commit(root)
    untracked = False
    ref_for_content: str | None = None

    if mode == DiffScope.AUTO:
        staged = git.run_git_checked(root, *_diff_args("--cached"))
        if _has_changes(staged):
            text, label, ref_for_content = staged, "staged-изменения", ":0"
        else:
            text, label = git.run_git_checked(root, *_diff_args()), "рабочее дерево"
            untracked = True
    elif mode == DiffScope.STAGED:
        text = git.run_git_checked(root, *_diff_args("--cached"))
        label, ref_for_content = "staged-изменения", ":0"
    elif mode == DiffScope.UNSTAGED:
        text = git.run_git_checked(root, *_diff_args())
        label, untracked = "рабочее дерево", True
    elif mode == DiffScope.LAST:
        text = git.run_git_checked(
            root, "show", "--format=", "--patch", "-U3", "--no-color", "HEAD"
        )
        label = f"коммит {(head or 'HEAD')[:10]}"
        ref_for_content = "HEAD"
    elif mode == DiffScope.COMMIT:
        if not commit:
            raise UsageAppError("для режима commit необходимо указать хэш или ref")
        text = git.run_git_checked(
            root, "show", "--format=", "--patch", "-U3", "--no-color", commit
        )
        label = f"коммит {commit[:10]}"
        ref_for_content = commit
    elif mode == DiffScope.RANGE:
        if not range_:
            raise UsageAppError("для режима range необходимо указать диапазон A..B")
        text = git.run_git_checked(root, *_diff_args(range_))
        label = f"диапазон {range_}"
        ref_for_content = _range_end_ref(range_)
    elif mode == DiffScope.SINCE:
        if not since_commit:
            raise GitError("нет сохранённого коммита последнего ревью")
        text = git.run_git_checked(root, *_diff_args(f"{since_commit}..HEAD"))
        label = f"изменения с {since_commit[:10]}"
        ref_for_content = "HEAD"
    else:  # pragma: no cover - StrEnum гарантирует перечень
        raise UsageAppError(f"неизвестный режим diff: {mode}")

    parsed = parse_diff(text, max_lines=max_lines)
    if untracked:
        parsed.extend(_untracked_diffs(root, max_lines=max_lines))

    return filter_and_limit_files(
        parsed,
        scope=label,
        head=head,
        ref_for_content=ref_for_content,
        max_files=max_files,
    )


def synthetic_diff_from_maps(
    before: dict[str, str],
    after: dict[str, str],
    *,
    scope: str = "локальное сравнение",
    max_files: int = DEFAULT_MAX_FILES,
    max_lines: int = DEFAULT_MAX_LINES_PER_FILE,
) -> DiffSet:
    """Собрать DiffSet напрямую по двум словарям {путь: текст} без вызова git."""
    chunks: list[str] = []
    for path in sorted(set(before) | set(after)):
        old_text = before.get(path)
        new_text = after.get(path)
        if old_text == new_text:
            continue
        if new_text is None:
            old_lines = old_text.splitlines(keepends=True) if old_text else []
            chunks.append(f"diff --git a/{path} b/{path}")
            chunks.append("deleted file mode 100644")
            chunks.extend(
                line.rstrip("\n")
                for line in difflib.unified_diff(
                    old_lines, [], fromfile=f"a/{path}", tofile="/dev/null", n=3
                )
            )
            continue

        old_lines = old_text.splitlines(keepends=True) if old_text is not None else []
        new_lines = new_text.splitlines(keepends=True)
        chunks.append(f"diff --git a/{path} b/{path}")
        if old_text is None:
            chunks.append("new file mode 100644")
            from_file = "/dev/null"
        else:
            from_file = f"a/{path}"
        chunks.extend(
            line.rstrip("\n")
            for line in difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=from_file,
                tofile=f"b/{path}",
                n=3,
            )
        )

    parsed = parse_diff("\n".join(chunks), max_lines=max_lines)
    return filter_and_limit_files(
        parsed,
        scope=scope,
        head=None,
        ref_for_content=None,
        max_files=max_files,
    )


def _untracked_diffs(root: Path, *, max_lines: int) -> list[FileDiff]:
    items: list[FileDiff] = []
    for relative in sorted(set(git.untracked_files(root))):
        if should_skip_path(relative):
            continue
        full = root / relative
        try:
            if not full.is_file() or full.stat().st_size > UNTRACKED_MAX_BYTES:
                continue
            data = full.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:SNIFF_BYTES]:
            continue
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        patch = "\n".join(
            [
                f"diff --git a/{relative} b/{relative}",
                "new file mode 100644",
                "--- /dev/null",
                f"+++ b/{relative}",
                f"@@ -0,0 +1,{len(lines)} @@",
                *[f"+{line}" for line in lines],
            ]
        )
        parsed = parse_diff(patch, max_lines=max_lines)
        if parsed:
            parsed[0].is_untracked = True
            items.append(parsed[0])
    return items
