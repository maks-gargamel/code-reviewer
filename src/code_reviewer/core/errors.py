"""Исключения приложения с понятными сообщениями для CLI."""

from __future__ import annotations


class AppError(RuntimeError):
    """Базовая ожидаемая ошибка приложения (завершается с кодом 2 без трейсбека)."""


class ConfigError(AppError):
    """Ошибка чтения или валидации конфигурации."""


class GitError(AppError):
    """Ошибка вызова или состояния git."""


class MapNotFoundError(AppError):
    """Карта репозитория ещё не создана."""


class LLMError(AppError):
    """Ошибка взаимодействия с LLM API или отсутствующего ключа."""


class HookError(AppError):
    """Ошибка установки или удаления git pre-commit хука."""


class UsageAppError(AppError):
    """Некорректное сочетание флагов или аргументов."""
