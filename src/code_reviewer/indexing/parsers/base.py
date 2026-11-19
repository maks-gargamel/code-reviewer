"""Базовый интерфейс языкового адаптера и общие утилиты извлечения метаданных."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ...core.models import FileInfo


@dataclass(slots=True, frozen=True)
class ParseOptions:
    """Параметры разбора одного файла."""

    fetch_grammars: bool = True


class LanguageAdapter(ABC):
    """Извлекает символы, документацию и импорты из исходного файла."""

    name: ClassVar[str] = "text"
    extensions: ClassVar[tuple[str, ...]] = ()
    filenames: ClassVar[tuple[str, ...]] = ()

    def matches(self, path: str) -> bool:
        filename = path.rsplit("/", 1)[-1]
        if filename in self.filenames:
            return True
        dot = filename.rfind(".")
        return dot != -1 and filename[dot:].lower() in self.extensions

    @abstractmethod
    def extract(
        self,
        path: str,
        source: str,
        *,
        size: int,
        mtime: float,
        digest: str,
        options: ParseOptions | None = None,
    ) -> FileInfo:
        """Разобрать файл и вернуть метаданные."""


def collapse(text: str | None, limit: int) -> str | None:
    """Сжать переводы строк/пробелы и обрезать строку до `limit` символов."""
    if not text:
        return None
    flat = " ".join(text.split())
    if not flat:
        return None
    if len(flat) > limit:
        flat = flat[: max(0, limit - 1)].rstrip() + "…"
    return flat


def leading_comment(
    source: str,
    prefixes: tuple[str, ...] = ("#", "//"),
    *,
    max_lines: int = 12,
) -> str | None:
    """Извлечь верхний комментарий файла (аналог docstring для не-Python языков)."""
    collected: list[str] = []
    for raw in source.splitlines()[:60]:
        line = raw.strip()
        if not line:
            if collected:
                break
            continue
        if line.startswith("#!"):
            continue
        if line.startswith("#") and ("coding" in line or "-*-" in line):
            continue
        matched = next((prefix for prefix in prefixes if line.startswith(prefix)), None)
        if matched is None:
            break
        body = line[len(matched) :].strip()
        if matched == "/*" and body.endswith("*/"):
            body = body[:-2].strip()
        if body.startswith(("/", "*")) and matched in ("/*", "*"):
            body = body.lstrip("/*").strip()
        if body.startswith("!"):
            body = body[1:].strip()
        if body:
            collected.append(body)
        if len(collected) >= max_lines:
            break
    return collapse(" ".join(collected), 240)
