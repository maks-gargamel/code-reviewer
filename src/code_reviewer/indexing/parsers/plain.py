"""Tier D адаптер: язык по расширению/имени файла и первый комментарий без символов."""

from __future__ import annotations

from ...core.models import FileInfo
from .base import LanguageAdapter, ParseOptions, leading_comment

LANG_BY_EXTENSION: dict[str, str] = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".vue": "vue",
    ".svelte": "svelte",
    ".json": "json",
    ".jsonc": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".xml": "xml",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".lua": "lua",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".hs": "haskell",
    ".clj": "clojure",
    ".r": "r",
    ".zig": "zig",
    ".nim": "nim",
    ".proto": "protobuf",
    ".gradle": "groovy",
    ".tf": "terraform",
    ".dockerfile": "dockerfile",
}

LANG_BY_FILENAME: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "make",
    "justfile": "just",
    "cmakelists.txt": "cmake",
    "rakefile": "ruby",
    "gemfile": "ruby",
}

COMMENT_PREFIXES: tuple[str, ...] = ("//", "#", "--", ";", "/*", "*", "<!--", "%")


class PlainAdapter(LanguageAdapter):
    """Универсальный catch-all адаптер для всех прочих файлов."""

    name = "plain"

    def matches(self, path: str) -> bool:
        return True

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
        del options
        filename = path.rsplit("/", 1)[-1]
        lowered = filename.lower()
        if lowered in LANG_BY_FILENAME:
            lang = LANG_BY_FILENAME[lowered]
        else:
            dot = filename.rfind(".")
            extension = filename[dot:].lower() if dot != -1 else ""
            lang = LANG_BY_EXTENSION.get(extension, "text")

        return FileInfo(
            path=path,
            lang=lang,
            size=size,
            mtime=mtime,
            loc=source.count("\n") + (1 if source else 0),
            digest=digest,
            doc=leading_comment(source, COMMENT_PREFIXES),
        )
