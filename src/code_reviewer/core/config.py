"""Конфигурация code-reviewer на базе TOML и Pydantic v2.

Приоритет источников (нижний побеждает):
    дефолты -> ~/.config/code-reviewer/config.toml -> .code-reviewer/config.toml -> CLI-флаги
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigError
from .fs import atomic_write_text
from .models import FailOn, OutputFormat, ReviewLanguage, Strictness

SERVICE_DIR = ".code-reviewer"
CONFIG_NAME = "config.toml"
MAP_NAME = "map.json"
STATE_NAME = "state.json"
BASELINE_NAME = "baseline.json"
LAST_REVIEW_NAME = "last-review.json"
HISTORY_NAME = "history.jsonl"
CACHE_DIR = "cache"
EVAL_CACHE_DIR = "eval-cache"
SUMMARY_CACHE_DIR = "summary-cache"
DEFAULT_KEYS_FILE = ".code-reviewer/keys.txt"

DEFAULT_CONFIG_TOML = """\
# Конфигурация code-reviewer.
# Сами ключи API в этом файле не хранятся: указывается путь к файлу или имя переменной окружения.

[llm]
# Поддерживается любой OpenAI-совместимый API: Gemini, OpenAI, DeepSeek, Groq, OpenRouter, Ollama.
provider = "openai-compatible"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
model = "gemini-3.5-flash"
api_key_file = ".code-reviewer/keys.txt"
api_key_env = "CODE_REVIEWER_API_KEY"
temperature = 0.1
max_tokens = 2048
timeout = 90.0
retries = 3

[review]
strictness = "middle"        # junior | middle | senior
language = "ru"              # ru | en
max_chunk_tokens = 8000      # порог разбиения большого diff на чанки
max_chunks = 3               # максимум LLM-запросов на одно ревью
jobs = 1                     # число параллельных потоков при обработке чанков
max_findings = 12            # максимум замечаний на один чанк
context_depth = 1            # глубина обхода графа зависимостей для контекста

[output]
format = "table"             # table | json | md | sarif
fail_on = "warning"          # never | warning | error

[map]
ignore = []                  # дополнительные шаблоны исключений в стиле .gitignore
max_file_bytes = 1000000     # пропускать файлы крупнее указанного размера
fetch_grammars = true        # дозагружать недостающие грамматики tree-sitter

[privacy]
redact_secrets = true        # маскировать секреты и токены перед отправкой
warn_on_remote = true        # предупреждать при первой отправке кода во внешний API
"""

KNOWN_KEYS: dict[str, set[str]] = {
    "llm": {
        "provider",
        "base_url",
        "model",
        "api_key_file",
        "api_key_env",
        "temperature",
        "max_tokens",
        "timeout",
        "retries",
    },
    "review": {
        "strictness",
        "language",
        "max_chunk_tokens",
        "max_chunks",
        "jobs",
        "max_findings",
        "context_depth",
    },
    "output": {"format", "fail_on"},
    "map": {"ignore", "max_file_bytes", "fetch_grammars"},
    "privacy": {"redact_secrets", "warn_on_remote"},
}


class _BaseSection(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)


class LLMConfig(_BaseSection):
    provider: str = "openai-compatible"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    model: str = "gemini-3.5-flash"
    api_key_file: str = DEFAULT_KEYS_FILE
    api_key_env: str = "CODE_REVIEWER_API_KEY"
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)
    timeout: float = Field(default=90.0, gt=0.0)
    retries: int = Field(default=3, ge=1, le=10)


class ReviewConfig(_BaseSection):
    strictness: Strictness = Strictness.MIDDLE
    language: ReviewLanguage = ReviewLanguage.RU
    max_chunk_tokens: int = Field(default=8000, ge=500)
    max_chunks: int = Field(default=3, ge=1)
    jobs: int = Field(default=1, ge=1, le=32)
    max_findings: int = Field(default=12, ge=1)
    context_depth: int = Field(default=1, ge=0, le=5)


class OutputConfig(_BaseSection):
    format: OutputFormat = OutputFormat.TABLE
    fail_on: FailOn = FailOn.WARNING


class MapConfig(_BaseSection):
    ignore: list[str] = Field(default_factory=list)
    max_file_bytes: int = Field(default=1_000_000, ge=1)
    fetch_grammars: bool = True


class PrivacyConfig(_BaseSection):
    redact_secrets: bool = True
    warn_on_remote: bool = True


class Config(_BaseSection):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    map: MapConfig = Field(default_factory=MapConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            detail = _format_validation_error(exc)
            raise ConfigError(f"некорректная конфигурация: {detail}") from exc

    @classmethod
    def load(cls, root: Path | None = None) -> Config:
        merged: dict[str, Any] = {}
        for path in config_sources(root):
            if not path.is_file():
                continue
            try:
                with path.open("rb") as handle:
                    parsed = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"не удалось прочитать {path}: {exc}") from exc
            merged = _deep_merge(merged, parsed)
        return cls.from_dict(merged)

    def write_default(self, root: Path, *, overwrite: bool = False) -> Path:
        path = project_config_path(root)
        if overwrite or not path.exists():
            atomic_write_text(path, DEFAULT_CONFIG_TOML)
        return path

    def is_local_llm(self) -> bool:
        """Вернуть True для локального провайдера (Ollama/LM Studio/localhost)."""
        if self.llm.provider.strip().lower() in {"ollama", "local"}:
            return True
        host = (urlparse(self.llm.base_url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}


def service_dir(root: Path) -> Path:
    return root / SERVICE_DIR


def global_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "code-reviewer" / CONFIG_NAME


def project_config_path(root: Path) -> Path:
    return service_dir(root) / CONFIG_NAME


def config_sources(root: Path | None = None) -> list[Path]:
    paths = [global_config_path()]
    if root is not None:
        paths.append(project_config_path(root))
    return paths


def unknown_config_keys(root: Path | None = None) -> list[str]:
    """Найти неизвестные секции/ключи или ошибки чтения TOML-файлов."""
    problems: list[str] = []
    for path in config_sources(root):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        for section, values in data.items():
            if section not in KNOWN_KEYS:
                problems.append(f"{path.name}: неизвестная секция [{section}]")
                continue
            if not isinstance(values, dict):
                problems.append(f"{path.name}: [{section}] должна быть таблицей")
                continue
            for key in values:
                if key not in KNOWN_KEYS[section]:
                    problems.append(f"{path.name}: [{section}] {key}")
    return problems


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "некорректное значение")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)
