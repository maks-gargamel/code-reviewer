"""OpenAI-совместимый HTTP-клиент к LLM на базе httpx."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .. import __version__
from ..core.config import Config
from ..core.errors import LLMError
from .keys import KeyPool, load_api_keys, resolve_api_key_file

DEFAULT_RETRIES = 3
BACKOFF_BASE = 1.5

_KEY_PROBLEM_400 = (
    "api key",
    "api_key",
    "permission denied",
    "billing",
    "quota",
)


def _is_key_problem_400(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _KEY_PROBLEM_400)


def _backoff(attempt: int) -> float:
    return float(BACKOFF_BASE**attempt)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return _backoff(attempt)


def _extract_content(parsed: Any) -> str:
    if not isinstance(parsed, dict):
        raise LLMError("ответ модели не является JSON-объектом")
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("в ответе модели отсутствует поле choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        ]
        return "".join(parts)
    raise LLMError("модель вернула пустой ответ")


@dataclass(slots=True)
class OpenAICompatClient:
    """Потокобезопасный клиент для `/chat/completions`."""

    base_url: str
    model: str
    api_key: str = ""
    api_keys: tuple[str, ...] = ()
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout: float = 90.0
    retries: int = DEFAULT_RETRIES
    transport: httpx.BaseTransport | None = None

    _pool: KeyPool = field(init=False, repr=False)
    _http: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        raw_keys: list[str] = []
        if self.api_key.strip():
            raw_keys.append(self.api_key.strip())
        raw_keys.extend(k.strip() for k in self.api_keys if k and k.strip())
        self._pool = KeyPool(tuple(raw_keys))
        self._http = httpx.Client(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=True,
        )

    @property
    def keys(self) -> tuple[str, ...]:
        return self._pool.keys

    @property
    def current_key(self) -> str:
        return self._pool.current_key

    def close(self) -> None:
        self._http.close()

    def complete(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        return self._request(payload)

    def _headers(self, key: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"code-reviewer/{__version__}",
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _request(self, payload: dict[str, Any]) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        index, key = self._pool.acquire()
        with_format = True
        attempt = 1

        while True:
            body = dict(payload)
            if with_format:
                body["response_format"] = {"type": "json_object"}

            try:
                response = self._http.post(
                    url,
                    json=body,
                    headers=self._headers(key),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self.retries:
                    raise LLMError(f"запрос к модели не удался (сеть/таймаут): {exc}") from exc
                time.sleep(_backoff(attempt))
                attempt += 1
                continue

            status = response.status_code
            if 200 <= status < 300:
                if index >= 0:
                    self._pool.mark_success(index)
                try:
                    parsed = response.json()
                except ValueError as exc:
                    raise LLMError(f"некорректный JSON от модели: {exc}") from exc
                return _extract_content(parsed)

            detail = response.text[:400]

            if status == 400 and "location is not supported" in detail.lower():
                raise LLMError(
                    "провайдер не поддерживает текущий регион "
                    "(HTTP 400: User location is not supported). "
                    "Смените сеть/VPN или используйте локальную модель (Ollama)."
                )

            if status == 400 and self._pool.keys and _is_key_problem_400(detail):
                rotated = self._pool.mark_rejected(index)
                if rotated is None:
                    raise LLMError(self._pool.all_failed_message())
                index, key = rotated
                attempt = 1
                continue

            if status == 400 and with_format:
                with_format = False
                continue

            if status in (401, 403):
                if not self._pool.keys:
                    raise LLMError(
                        f"модель отклонила запрос (HTTP {status}). "
                        "Проверьте настройки провайдера."
                    )
                rotated = self._pool.mark_rejected(index)
                if rotated is None:
                    raise LLMError(self._pool.all_failed_message())
                index, key = rotated
                attempt = 1
                continue

            if status == 404:
                raise LLMError(
                    f"эндпоинт не найден (HTTP 404): {url}. Проверьте base_url и model."
                )

            if status == 429:
                if not self._pool.keys:
                    if attempt >= self.retries:
                        raise LLMError("превышен лимит запросов (HTTP 429)")
                    time.sleep(_retry_delay(response, attempt))
                    attempt += 1
                    continue

                if len(self._pool.keys) == 1:
                    if attempt >= self.retries:
                        self._pool.mark_exhausted(index)
                        raise LLMError(self._pool.all_failed_message())
                    time.sleep(_retry_delay(response, attempt))
                    attempt += 1
                    continue

                rotated = self._pool.mark_exhausted(index)
                if rotated is None:
                    raise LLMError(self._pool.all_failed_message())
                index, key = rotated
                attempt = 1
                continue

            if 500 <= status < 600:
                if attempt < self.retries:
                    time.sleep(_backoff(attempt))
                    attempt += 1
                    continue
                if self._pool.keys:
                    rotated = self._pool.mark_unavailable(index)
                    if rotated is not None:
                        index, key = rotated
                        attempt = 1
                        continue
                    raise LLMError(self._pool.all_failed_message())
                raise LLMError(f"сервер модели вернул ошибку HTTP {status}: {detail}")

            raise LLMError(f"запрос к модели не удался (HTTP {status}): {detail}")


def client_from_config(
    config: Config,
    root: Path | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> OpenAICompatClient:
    """Создать OpenAI-совместимый клиент из конфигурации проекта."""
    api_keys = load_api_keys(config, root)
    if not api_keys and not config.is_local_llm():
        path = resolve_api_key_file(config.llm.api_key_file, root)
        sources = [f"переменная окружения {config.llm.api_key_env} пуста"]
        if path is not None:
            if path.is_file():
                sources.append(f"файл {path} пуст")
            else:
                sources.append(f"файл {path} не найден")
        raise LLMError(
            "не задан ключ API: "
            + "; ".join(sources)
            + ".\nДобавьте ключ командой `code-reviewer keys add` или задайте "
            "переменную окружения. Посмотреть промпт без ключа: check --dry-run."
        )

    if not config.llm.model.strip():
        raise LLMError("в config.toml не указана модель ([llm] model)")

    return OpenAICompatClient(
        base_url=config.llm.base_url,
        model=config.llm.model,
        api_keys=tuple(api_keys),
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        timeout=config.llm.timeout,
        retries=config.llm.retries,
        transport=transport,
    )
