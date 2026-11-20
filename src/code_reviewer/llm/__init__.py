"""Слой взаимодействия с языковыми моделями."""

from __future__ import annotations

from .client import OpenAICompatClient, client_from_config
from .keys import (
    KeyPool,
    StoredKeyEntry,
    add_api_key,
    clear_api_keys,
    list_key_entries,
    load_api_keys,
    mask_key,
    read_api_keys,
    remove_api_key,
    resolve_api_key_file,
)
from .protocol import LLMClient

__all__ = [
    "KeyPool",
    "LLMClient",
    "OpenAICompatClient",
    "StoredKeyEntry",
    "add_api_key",
    "clear_api_keys",
    "client_from_config",
    "list_key_entries",
    "load_api_keys",
    "mask_key",
    "read_api_keys",
    "remove_api_key",
    "resolve_api_key_file",
]
