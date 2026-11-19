"""Протокол клиента языковой модели."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Минимальный интерфейс клиента модели для ревью и AI-сводок."""

    def complete(self, system: str, user: str) -> str:
        """Отправить system/user сообщения и вернуть текстовый ответ модели."""
        ...
