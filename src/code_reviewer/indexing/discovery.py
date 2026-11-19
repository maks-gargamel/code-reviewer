"""Обход файлов репозитория с учётом .gitignore, бинарных фильтров и лимитов."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.patterns import matches_patterns
from ..vcs import git

DEFAULT_MAX_FILE_BYTES = 1_000_000
SNIFF_BYTES = 8192

DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".code-reviewer",
        ".idea",
        ".vscode",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "vendor",
        "site-packages",
        ".next",
        ".nuxt",
        "coverage",
        "htmlcov",
    }
)

DEFAULT_IGNORED_FILES: frozenset[str] = frozenset(
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

BINARY_EXTENSIONS: frozenset[str] = frozenset(
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
        ".nupkg",
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


@dataclass(slots=True)
class DiscoveredFile:
    path: str
    size: int
    mtime: float


@dataclass(slots=True)
class DiscoveryResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    skipped_binary: int = 0
    skipped_large: int = 0
    skipped_ignored: int = 0
    used_git: bool = False


def _walk_paths(root: Path) -> list[str]:
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_IGNORED_DIRS)
        for filename in sorted(filenames):
            if filename in DEFAULT_IGNORED_FILES:
                continue
            full = Path(dirpath) / filename
            try:
                relative = full.relative_to(root)
            except ValueError:
                continue
            paths.append(relative.as_posix())
    return paths


def _looks_binary(full: Path) -> bool:
    try:
        with full.open("rb") as handle:
            chunk = handle.read(SNIFF_BYTES)
    except OSError:
        return True
    return b"\x00" in chunk


def discover_files(
    root: Path,
    *,
    ignore: Sequence[str] = (),
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    use_git: bool = True,
) -> DiscoveryResult:
    """Собрать список текстовых файлов проекта, подходящих для построения карты."""
    result = DiscoveryResult()
    candidates: list[str] | None = None
    if use_git:
        candidates = git.visible_files(root)
        result.used_git = candidates is not None
    if candidates is None:
        candidates = _walk_paths(root)

    for relative in sorted(set(candidates)):
        parts = relative.split("/")
        if any(part in DEFAULT_IGNORED_DIRS for part in parts[:-1]):
            result.skipped_ignored += 1
            continue
        if parts[-1] in DEFAULT_IGNORED_FILES:
            result.skipped_ignored += 1
            continue
        if ignore and matches_patterns(relative, ignore):
            result.skipped_ignored += 1
            continue

        dot = parts[-1].rfind(".")
        extension = parts[-1][dot:].lower() if dot != -1 else ""
        if extension in BINARY_EXTENSIONS:
            result.skipped_binary += 1
            continue

        full = root / relative
        try:
            stat = full.stat()
        except OSError:
            continue
        if not full.is_file():
            continue
        if stat.st_size > max_bytes:
            result.skipped_large += 1
            continue
        if _looks_binary(full):
            result.skipped_binary += 1
            continue

        result.files.append(
            DiscoveredFile(path=relative, size=stat.st_size, mtime=stat.st_mtime)
        )

    return result
