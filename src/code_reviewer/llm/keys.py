"""Загрузка API-ключей и потокобезопасный пул с ротацией."""

from __future__ import annotations

import contextlib
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Config
from ..core.errors import LLMError, UsageAppError
from ..core.fs import atomic_write_text

KEYS_FILE_HEADER = "# Ключи API (по одному на строку; строки с # игнорируются)"


@dataclass(slots=True)
class StoredKeyEntry:
    """Маскированная запись API-ключа для отображения пользователю."""

    index: int | None
    masked: str
    comment: str = ""
    source: str = "file"


def mask_key(key: str) -> str:
    """Вернуть безопасное маскированное представление API-ключа."""
    cleaned = key.strip()
    if len(cleaned) <= 8:
        return "****"
    if len(cleaned) <= 12:
        return f"{cleaned[:2]}…{cleaned[-2:]}"
    return f"{cleaned[:4]}…{cleaned[-4:]}"


def resolve_api_key_file(value: str, root: Path | None = None) -> Path | None:
    """Преобразовать путь к файлу с ключами относительно корня проекта."""
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        path = (root or Path.cwd()) / path
    return path


def _parse_key_line(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    comment = ""
    if " #" in line:
        key_part, comment_part = line.split(" #", 1)
        line = key_part.strip()
        comment = comment_part.strip()
    if not line:
        return None
    return line, comment


def _read_key_file_lines(path: Path) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise LLMError(f"не удалось прочитать файл ключей {path}: {exc}") from exc
    return text.splitlines()


def _write_key_file_lines(path: Path, lines: list[str]) -> None:
    text = ("\n".join(lines) + "\n") if lines else ""
    atomic_write_text(path, text)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def read_api_keys(path: Path) -> list[str]:
    """Прочитать ключи из файла: по одному на строку, `#` — комментарий."""
    lines = _read_key_file_lines(path)
    if lines is None:
        return []
    keys: list[str] = []
    for raw in lines:
        parsed = _parse_key_line(raw)
        if parsed is not None:
            keys.append(parsed[0])
    return keys


def add_api_key(path: Path, key: str, *, comment: str = "") -> tuple[bool, str, int]:
    """Добавить API-ключ в файл без дублей и с правами 0600."""
    cleaned = key.strip()
    if not cleaned:
        raise UsageAppError("API-ключ не может быть пустым.")
    if any(ch.isspace() for ch in cleaned):
        raise UsageAppError("API-ключ не должен содержать пробелы или переносы строк.")
    if cleaned.startswith("#"):
        raise UsageAppError("API-ключ не может начинаться с символа '#'.")

    clean_comment = " ".join(comment.split())
    existing_lines = _read_key_file_lines(path)
    existing_keys = [
        parsed[0]
        for raw in (existing_lines or [])
        if (parsed := _parse_key_line(raw)) is not None
    ]
    if cleaned in existing_keys:
        return False, mask_key(cleaned), len(existing_keys)

    new_line = f"{cleaned} # {clean_comment}" if clean_comment else cleaned
    if existing_lines is None:
        new_lines = [KEYS_FILE_HEADER, new_line]
    else:
        new_lines = [*existing_lines, new_line]
    _write_key_file_lines(path, new_lines)
    return True, mask_key(cleaned), len(existing_keys) + 1


def remove_api_key(path: Path, index: int) -> tuple[str, int]:
    """Удалить ключ из файла по 1-based индексу, сохраняя комментарии."""
    existing_lines = _read_key_file_lines(path)
    if existing_lines is None:
        raise UsageAppError(f"Файл ключей не найден: {path}")

    kept_lines: list[str] = []
    total_keys = 0
    removed_key: str | None = None
    for raw in existing_lines:
        parsed = _parse_key_line(raw)
        if parsed is None:
            kept_lines.append(raw)
            continue
        total_keys += 1
        if total_keys == index:
            removed_key = parsed[0]
        else:
            kept_lines.append(raw)

    if total_keys == 0:
        raise UsageAppError(f"В файле ключей нет ни одного ключа: {path}")
    if index < 1 or index > total_keys or removed_key is None:
        raise UsageAppError(f"Некорректный номер ключа: {index} (доступно: 1..{total_keys}).")

    _write_key_file_lines(path, kept_lines)
    return mask_key(removed_key), total_keys - 1


def clear_api_keys(path: Path) -> tuple[bool, int]:
    """Удалить файл с ключами и вернуть количество удалённых ключей."""
    if not path.exists():
        return False, 0
    count = len(read_api_keys(path))
    path.unlink(missing_ok=True)
    return True, count


def list_key_entries(
    config: Config,
    root: Path | None = None,
) -> tuple[list[StoredKeyEntry], Path | None, str, int]:
    """Собрать маскированный список ключей из файла и переменной окружения."""
    entries: list[StoredKeyEntry] = []
    raw_keys: list[str] = []

    key_file = resolve_api_key_file(config.llm.api_key_file, root)
    if key_file is not None:
        lines = _read_key_file_lines(key_file)
        if lines is not None:
            file_index = 0
            for raw in lines:
                parsed = _parse_key_line(raw)
                if parsed is None:
                    continue
                file_index += 1
                raw_key, comment = parsed
                raw_keys.append(raw_key)
                entries.append(
                    StoredKeyEntry(
                        index=file_index,
                        masked=mask_key(raw_key),
                        comment=comment,
                        source="file",
                    )
                )

    env_name = config.llm.api_key_env.strip()
    if env_name:
        env_value = os.environ.get(env_name, "")
        if env_value:
            for part in env_value.replace(",", " ").split():
                cleaned = part.strip()
                if not cleaned:
                    continue
                raw_keys.append(cleaned)
                entries.append(
                    StoredKeyEntry(
                        index=None,
                        masked=mask_key(cleaned),
                        comment="",
                        source="env",
                    )
                )

    total_unique = len(dict.fromkeys(k.strip() for k in raw_keys if k.strip()))
    return entries, key_file, env_name, total_unique


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
