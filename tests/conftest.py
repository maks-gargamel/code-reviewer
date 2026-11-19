from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest


def write_file(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def json_chat_response(
    content: str = '{"findings": []}',
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"choices": [{"message": {"content": content}}]},
        headers=headers or {},
    )


def mock_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Небольшой Python-проект (без обязательной зависимости от установленного git)."""
    write_file(tmp_path, "README.md", "# Demo Project\n")
    write_file(
        tmp_path,
        "main.py",
        "from pkg.models import Model\nimport pkg.utils\nimport requests\n\n"
        "if __name__ == '__main__':\n    print(Model)\n",
    )
    write_file(tmp_path, "pkg/__init__.py", '"""Пакет pkg."""\n')
    write_file(
        tmp_path,
        "pkg/models.py",
        '"""Модели."""\n\nfrom .utils import helper\n\n\nclass Model:\n    """Модель."""\n\n'
        "    def __init__(self, name: str) -> None:\n        self.name = name\n",
    )
    write_file(tmp_path, "pkg/utils.py", "def helper() -> int:\n    return 1\n")
    write_file(tmp_path, "pkg/sub/__init__.py", "")
    write_file(tmp_path, "pkg/sub/mod.py", "from .. import utils\n")
    return tmp_path


@pytest.fixture
def src_project(tmp_path: Path) -> Path:
    """Проект со структурой src-layout."""
    write_file(tmp_path, "pyproject.toml", "[project]\nname = 'demo'\n")
    write_file(tmp_path, "src/app/__init__.py", "")
    write_file(
        tmp_path,
        "src/app/core.py",
        "from .helpers import util\nfrom app import settings\n",
    )
    write_file(tmp_path, "src/app/helpers.py", "def util():\n    return 42\n")
    write_file(tmp_path, "src/app/settings.py", "DEBUG = True\n")
    return tmp_path
