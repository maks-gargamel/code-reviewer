from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from code_reviewer.core.config import Config
from code_reviewer.core.errors import LLMError
from code_reviewer.llm.client import OpenAICompatClient, client_from_config
from code_reviewer.llm.keys import load_api_keys
from conftest import json_chat_response, mock_transport


def test_load_api_keys_from_file_and_env(tmp_path: Path, monkeypatch):
    key_file = tmp_path / ".code-reviewer" / "keys.txt"
    key_file.parent.mkdir()
    key_file.write_text(
        "\ufeff# комментарий\nkey-one\n\nkey-two # inline comment\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODE_REVIEWER_API_KEY", "key-two, key-three\nkey-four")
    cfg = Config()
    cfg.llm.api_key_file = ".code-reviewer/keys.txt"

    assert load_api_keys(cfg, tmp_path) == [
        "key-one",
        "key-two",
        "key-three",
        "key-four",
    ]


def test_client_from_config_allows_localhost_ollama_without_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CODE_REVIEWER_API_KEY", raising=False)
    cfg = Config()
    cfg.llm.api_key_file = ""
    cfg.llm.base_url = "http://localhost:11434/v1"

    client = client_from_config(cfg, tmp_path)
    assert client.keys == ()


def test_client_from_config_requires_key_for_remote(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CODE_REVIEWER_API_KEY", raising=False)
    cfg = Config()
    cfg.llm.api_key_file = ""

    with pytest.raises(LLMError, match="CODE_REVIEWER_API_KEY"):
        client_from_config(cfg, tmp_path)


def test_client_rotates_on_401_and_429():
    auth_calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        auth_calls.append(auth)
        if auth == "Bearer dead":
            return json_chat_response(status_code=401)
        if auth == "Bearer limited":
            return json_chat_response(status_code=429)
        return json_chat_response('{"findings": []}')

    client = OpenAICompatClient(
        base_url="https://example.test/v1",
        model="m",
        api_keys=("dead", "limited", "live"),
        transport=mock_transport(handler),
    )

    assert client.complete("s", "u") == '{"findings": []}'
    assert client.complete("s", "u") == '{"findings": []}'
    assert auth_calls == ["Bearer dead", "Bearer limited", "Bearer live", "Bearer live"]


def test_single_key_429_reports_rate_limit_error(monkeypatch):
    monkeypatch.setattr("code_reviewer.llm.client.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_chat_response(status_code=429, headers={"Retry-After": "0"})

    client = OpenAICompatClient(
        base_url="https://example.test/v1",
        model="m",
        api_keys=("only-key",),
        retries=2,
        transport=mock_transport(handler),
    )

    with pytest.raises(LLMError, match="лимит"):
        client.complete("s", "u")


def test_location_not_supported_fails_in_one_request_without_rotating():
    # Регрессионный тест: при ошибке региона не должно быть ни сброса response_format, ни ротации.
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization"))
        return httpx.Response(
            400,
            json={"error": {"message": "User location is not supported for the API use."}},
        )

    client = OpenAICompatClient(
        base_url="https://example.test/v1",
        model="m",
        api_keys=("one", "two"),
        transport=mock_transport(handler),
    )

    with pytest.raises(LLMError, match="регион"):
        client.complete("s", "u")

    assert calls == ["Bearer one"]


def test_400_falls_back_without_response_format():
    seen_formats: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen_formats.append(payload.get("response_format"))
        if "response_format" in payload:
            return httpx.Response(400, json={"error": {"message": "unsupported response_format"}})
        return json_chat_response('{"findings": []}')

    client = OpenAICompatClient(
        base_url="https://example.test/v1",
        model="m",
        api_keys=("one",),
        transport=mock_transport(handler),
    )

    assert client.complete("s", "u") == '{"findings": []}'
    assert seen_formats == [{"type": "json_object"}, None]


def test_5xx_retries_and_rotates_and_timeout_retries(monkeypatch):
    monkeypatch.setattr("code_reviewer.llm.client.time.sleep", lambda _: None)
    calls: list[str | None] = []

    def handler_5xx(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        calls.append(auth)
        if auth == "Bearer busy":
            return json_chat_response(status_code=503)
        return json_chat_response('{"findings": []}')

    client = OpenAICompatClient(
        base_url="https://example.test/v1",
        model="m",
        api_keys=("busy", "live"),
        retries=2,
        transport=mock_transport(handler_5xx),
    )
    assert client.complete("s", "u") == '{"findings": []}'
    assert calls == ["Bearer busy", "Bearer busy", "Bearer live"]

    attempts = 0

    def handler_timeout(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timed out")
        return json_chat_response('{"findings": []}')

    timeout_client = OpenAICompatClient(
        base_url="https://example.test/v1",
        model="m",
        api_keys=("one",),
        retries=2,
        transport=mock_transport(handler_timeout),
    )
    assert timeout_client.complete("s", "u") == '{"findings": []}'
    assert attempts == 2


def test_concurrent_workers_share_key_pool_safely():
    lock = threading.Lock()
    used: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        with lock:
            used.append(auth)
        if auth == "Bearer k1":
            return json_chat_response(status_code=429)
        return json_chat_response('{"findings": []}')

    client = OpenAICompatClient(
        base_url="https://example.test/v1",
        model="m",
        api_keys=("k1", "k2"),
        transport=mock_transport(handler),
    )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda i: client.complete("s", f"u{i}"), range(6)))

    assert results == ['{"findings": []}'] * 6
    assert client.current_key == "k2"
