"""Загрузка API-ключей и потокобезопасный пул с ротацией."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from ..core.config import Config
from ..core.errors import LLMError


def resolve_api_key_file(value: str, root: Path | None = None) -> Path | None:
    """Преобразовать путь к файлу с ключами относительно корня проекта."""
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        path = (root or Path.cwd()) / path
    return path


def read_api_keys(path: Path) -> list[str]:
    """Прочитать ключи из файла: по одному на строку, `#` — комментарий."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise LLMError(f"не удалось прочитать файл ключей {path}: {exc}") from exc

    keys: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if line:
            keys.append(line)
    return keys


def load_api_keys(config: Config, root: Path | None = None) -> list[str]:
    """Собрать уникальные ключи из файла и переменной окружения."""
    keys: list[str] = []
    path = resolve_api_key_file(config.llm.api_key_file, root)
    if path is not None:
        keys.extend(read_api_keys(path))

    env_name = config.llm.api_key_env.strip()
    if env_name:
        env_value = os.environ.get(env_name, "")
        if env_value:
            keys.extend(part for part in env_value.replace(",", " ").split() if part)

    return list(dict.fromkeys(key.strip() for key in keys if key.strip()))


class KeyPool:
    """Потокобезопасный пул API-ключей с учётом отклонённых, исчерпанных и недоступных."""

    def __init__(self, keys: tuple[str, ...] = ()) -> None:
        cleaned = tuple(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
        self._keys: tuple[str, ...] = cleaned
        self._index: int = 0
        self._rejected: set[int] = set()
        self._exhausted: set[int] = set()
        self._unavailable: set[int] = set()
        self._lock = threading.Lock()

    @property
    def keys(self) -> tuple[str, ...]:
        return self._keys

    @property
    def current_key(self) -> str:
        with self._lock:
            if not self._keys:
                return ""
            return self._keys[self._index]

    def acquire(self) -> tuple[int, str]:
        """Получить текущий рабочий ключ или поднять LLMError, если все исчерпаны."""
        with self._lock:
            if not self._keys:
                return -1, ""
            if self._is_blocked_unlocked(self._index):
                next_idx = self._next_available_unlocked(self._index)
                if next_idx is None:
                    raise LLMError(self._all_failed_unlocked())
                self._index = next_idx
            return self._index, self._keys[self._index]

    def mark_success(self, index: int) -> None:
        with self._lock:
            if 0 <= index < len(self._keys) and not self._is_blocked_unlocked(index):
                self._index = index

    def mark_rejected(self, index: int) -> tuple[int, str] | None:
        return self._mark_and_rotate(index, self._rejected)

    def mark_exhausted(self, index: int) -> tuple[int, str] | None:
        return self._mark_and_rotate(index, self._exhausted)

    def mark_unavailable(self, index: int) -> tuple[int, str] | None:
        return self._mark_and_rotate(index, self._unavailable)

    def all_failed_message(self) -> str:
        with self._lock:
            return self._all_failed_unlocked()

    def _mark_and_rotate(self, index: int, bucket: set[int]) -> tuple[int, str] | None:
        with self._lock:
            if not self._keys or index < 0 or index >= len(self._keys):
                return None
            bucket.add(index)
            next_idx = self._next_available_unlocked(index)
            if next_idx is None:
                return None
            self._index = next_idx
            return next_idx, self._keys[next_idx]

    def _is_blocked_unlocked(self, index: int) -> bool:
        return (
            index in self._rejected
            or index in self._exhausted
            or index in self._unavailable
        )

    def _next_available_unlocked(self, current: int) -> int | None:
        total = len(self._keys)
        for offset in range(1, total + 1):
            candidate = (current + offset) % total
            if not self._is_blocked_unlocked(candidate):
                return candidate
        return None

    def _all_failed_unlocked(self) -> str:
        total = len(self._keys)
        if total <= 1:
            if self._rejected:
                return (
                    "модель отклонила ключ (HTTP 401/403). "
                    "Проверьте ключ в файле или переменной окружения."
                )
            if self._exhausted:
                return (
                    "превышен лимит запросов (HTTP 429). Попробуйте позже "
                    "или добавьте дополнительный ключ в файл."
                )
            if self._unavailable:
                return "модель временно недоступна (HTTP 5xx). Попробуйте позже."
            return "запрос к модели не удался."

        reasons: list[str] = []
        if self._rejected:
            reasons.append(f"отклонено: {len(self._rejected)}")
        if self._exhausted:
            reasons.append(f"исчерпан лимит: {len(self._exhausted)}")
        if self._unavailable:
            reasons.append(f"недоступны: {len(self._unavailable)}")
        detail = "; ".join(reasons) or "неизвестная причина"
        return (
            f"все ключи API не сработали ({detail} из {total}). "
            "Добавьте рабочие ключи или дождитесь сброса квоты."
        )
