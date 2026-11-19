"""Слой взаимодействия с языковыми моделями."""

from __future__ import annotations

from .client import OpenAICompatClient, client_from_config
from .keys import KeyPool, load_api_keys, read_api_keys, resolve_api_key_file
from .protocol import LLMClient

__all__ = [
    "KeyPool",
    "LLMClient",
    "OpenAICompatClient",
    "client_from_config",
    "load_api_keys",
    "read_api_keys",
    "resolve_api_key_file",
]
