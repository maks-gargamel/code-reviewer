"""Работа с системой контроля версий (git, diff, хуки)."""

from __future__ import annotations

from .diff import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_LINES_PER_FILE,
    DiffScope,
    DiffSet,
    FileDiff,
    collect_diff,
    parse_diff,
    synthetic_diff_from_maps,
)
from .git import (
    current_branch,
    find_repo_root,
    head_commit,
    hooks_dir,
    is_git_available,
    read_file_at_ref,
    run_git,
    run_git_checked,
    untracked_files,
    visible_files,
)
from .hooks import (
    HOOK_NAME,
    MARKER,
    hook_path,
    install_hook,
    is_hook_installed,
    render_hook_script,
    uninstall_hook,
)

__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_LINES_PER_FILE",
    "DiffScope",
    "DiffSet",
    "FileDiff",
    "HOOK_NAME",
    "MARKER",
    "collect_diff",
    "current_branch",
    "find_repo_root",
    "head_commit",
    "hook_path",
    "hooks_dir",
    "install_hook",
    "is_git_available",
    "is_hook_installed",
    "parse_diff",
    "read_file_at_ref",
    "render_hook_script",
    "run_git",
    "run_git_checked",
    "synthetic_diff_from_maps",
    "uninstall_hook",
    "untracked_files",
    "visible_files",
]
