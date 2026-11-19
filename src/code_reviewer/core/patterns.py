"""Сопоставление путей с шаблонами в стиле .gitignore."""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache


def matches_patterns(relative: str, patterns: Sequence[str]) -> bool:
    """Проверить, попадает ли относительный путь под список правил в стиле .gitignore.

    Поддерживаются:
    - комментарии `# ...` и пустые строки;
    - отрицание `!pattern` (выигрывает последнее совпавшее правило);
    - директории с завершающим `/` и скрытые файлы вида `.env`, `.github/`;
    - префикс `./` убирается аккуратно через removeprefix без порчи ведущих точек;
    - маски `*`, `?` и `**`.
    """
    normalized = relative.replace("\\", "/").lstrip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return False

    matched = False
    for raw in patterns:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        negated = line.startswith("!")
        body = line[1:].strip() if negated else line
        if not body:
            continue

        compiled = _compile_pattern(body)
        if compiled is not None and compiled.search(normalized):
            matched = not negated

    return matched


@lru_cache(maxsize=512)
def _compile_pattern(raw_pattern: str) -> re.Pattern[str] | None:
    pattern = raw_pattern.replace("\\", "/")
    if pattern.startswith("./"):
        pattern = pattern[2:]

    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    if not pattern:
        return None

    directory_only = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    if not pattern:
        return None

    has_slash = "/" in pattern
    regex_body = _glob_to_regex(pattern)

    if anchored or has_slash:
        full = f"^{regex_body}/.+$" if directory_only else f"^{regex_body}(?:/.*)?$"
    else:
        full = (
            f"(?:^|/){regex_body}/.+$"
            if directory_only
            else f"(?:^|/){regex_body}(?:/.*)?$"
        )

    return re.compile(full)


def _glob_to_regex(pattern: str) -> str:
    """Преобразовать glob-шаблон (включая `**`) в фрагмент регулярного выражения."""
    out: list[str] = []
    i = 0
    length = len(pattern)

    while i < length:
        if pattern[i : i + 3] == "**/":
            out.append("(?:.+/)?")
            i += 3
            continue
        if pattern[i : i + 3] == "/**":
            out.append("(?:/.*)?")
            i += 3
            continue
        if pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
            continue

        ch = pattern[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            end = pattern.find("]", i + 1)
            if end != -1:
                cls_body = pattern[i + 1 : end]
                if cls_body.startswith("!"):
                    cls_body = "^" + cls_body[1:]
                out.append(f"[{cls_body}]")
                i = end + 1
                continue
            out.append(r"\[")
        else:
            out.append(re.escape(ch))
        i += 1

    return "".join(out)
