"""Диагностика окружения, конфигурации, карты, грамматик и ключей API."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Config, service_dir, unknown_config_keys
from ..core.errors import ConfigError, LLMError
from ..indexing.builder import load_map, stale_files
from ..indexing.parsers import treesitter
from ..llm.keys import load_api_keys, read_api_keys, resolve_api_key_file
from ..review.history import load_history
from ..vcs import git
from ..vcs.hooks import MARKER, hook_path

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str = ""


def run_checks(root: Path) -> list[Check]:
    git_available = git.is_git_available()
    checks: list[Check] = [
        Check(
            "Python",
            OK if sys.version_info >= (3, 12) else FAIL,
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        Check(
            "Git",
            OK if git_available else WARN,
            "доступен" if git_available else "не найден (доступны demo и обход без .gitignore)",
        ),
    ]

    repo_root = git.find_repo_root(root) if git_available else None
    checks.append(
        Check(
            "Репозиторий",
            OK if repo_root is not None else WARN,
            str(repo_root) if repo_root else "git-репозиторий не найден",
        )
    )

    try:
        config = Config.load(root)
    except ConfigError as exc:
        checks.append(Check("Конфиг", FAIL, str(exc)))
        config = Config()
    else:
        unknown = unknown_config_keys(root)
        if unknown:
            preview = ", ".join(unknown[:5])
            suffix = f" и ещё {len(unknown) - 5}" if len(unknown) > 5 else ""
            checks.append(Check("Конфиг", WARN, f"неизвестные ключи: {preview}{suffix}"))
        else:
            checks.append(Check("Конфиг", OK, "валиден"))

    service = service_dir(root)
    checks.append(
        Check(
            ".code-reviewer",
            OK if service.is_dir() else WARN,
            str(service) if service.is_dir() else "не создана: запустите init",
        )
    )

    repo_map = load_map(root)
    if repo_map is None:
        checks.append(Check("Карта", WARN, "не построена: запустите init"))
    else:
        stale = stale_files(root, config, repo_map)
        checks.append(
            Check(
                "Карта",
                OK if not stale else WARN,
                f"{len(repo_map.files)} файлов"
                + (f", устарела: {len(stale)}" if stale else ""),
            )
        )

    if treesitter.installed():
        checks.append(Check("tree-sitter", OK, "пакет установлен"))
        grammars = treesitter.downloaded_grammars()
        checks.append(
            Check(
                "Грамматики",
                OK if grammars else WARN,
                ", ".join(grammars[:8]) if grammars else "кэш пуст (загрузятся при init)",
            )
        )
    else:
        checks.append(
            Check(
                "tree-sitter",
                WARN,
                "пакет не установлен: доступен только Python и Tier D",
            )
        )

    hook = hook_path(root) if repo_root is not None else None
    if hook is None:
        checks.append(Check("Хук", WARN, "git-репозиторий не найден"))
    elif hook.is_file() and MARKER in hook.read_text(encoding="utf-8", errors="replace"):
        checks.append(Check("Хук", OK, str(hook)))
    else:
        checks.append(Check("Хук", WARN, "pre-commit не установлен"))

    entries = load_history(root)
    checks.append(
        Check(
            "История",
            OK if entries else WARN,
            f"{len(entries)} записей" if entries else "пока пуста",
        )
    )

    if config.is_local_llm():
        checks.append(
            Check(
                "Ключ API",
                OK,
                f"локальный провайдер ({config.llm.base_url}): ключ не нужен",
            )
        )
    else:
        key_file = resolve_api_key_file(config.llm.api_key_file, root)
        try:
            keys = load_api_keys(config, root)
            file_keys = read_api_keys(key_file) if key_file is not None else []
        except LLMError as exc:
            checks.append(Check("Ключ API", FAIL, str(exc)))
        else:
            if keys:
                sources: list[str] = []
                if file_keys:
                    sources.append(str(key_file))
                env_name = config.llm.api_key_env.strip()
                if env_name and os.environ.get(env_name, "").strip():
                    sources.append(env_name)
                checks.append(
                    Check("Ключ API", OK, f"{len(keys)} шт.: " + " + ".join(sources))
                )
            else:
                detail = f"нет ключей: {config.llm.api_key_env}"
                if key_file is not None:
                    detail += f" или {key_file}"
                checks.append(Check("Ключ API", WARN, detail))

    return checks


def has_failures(checks: list[Check]) -> bool:
    return any(check.status == FAIL for check in checks)


def summary(checks: list[Check]) -> str:
    failures = sum(1 for check in checks if check.status == FAIL)
    warnings = sum(1 for check in checks if check.status == WARN)
    return f"проблем: {failures} · предупреждений: {warnings}"
