# code-reviewer

Локальный AI код-ревьюер, который анализирует `git diff` в контексте **всего репозитория**: карты символов, сигнатур и графа зависимостей.

Карта репозитория строится **локально через AST и tree-sitter без вызовов LLM**. Модель получает только diff и компактные сигнатуры связанных по графу файлов, что снижает расход токенов и позволяет работать даже на бесплатных лимитах API или локально через Ollama.

## Возможности

- **Локальная карта репозитория (`init`):**
  - Tier A: Python через stdlib `ast` (классы, методы, функции, константы, сигнатуры с аннотациями и декораторами, docstring, импорты без `TYPE_CHECKING`).
  - Tier B: JS/TS/TSX (ES-модули и CommonJS), Go, Rust, Java, Kotlin, C/C++, C#, Ruby, PHP, Swift, Lua, Dart, Scala через `tree-sitter-language-pack`.
  - Tier D: любые остальные файлы (язык по расширению и первый комментарий).
  - Разрешение импортов в граф зависимостей (`internal`, `external`, `unresolved`) для flat- и `src`-layout, плюс локальные `#include` в C/C++.
  - Инкрементальное обновление по размеру и `mtime`, опциональные AI-сводки по модулям (`init --ai-summary`).
- **Умное ревью (`check`):**
  - Режимы: staged / working tree + untracked (по умолчанию), `--last`, `--since-last`, `--commit`, `--range`, а также полный аудит всего кода `--all` (`-p/--path`, `--no-tests`, `--dry-run`, защита квоты).
  - Автоматическое маскирование секретов перед отправкой в облако.
  - Чанкинг больших diff по связным компонентам графа зависимостей и параллельная проверка (`--jobs`).
  - Отчёты в `table`, `json`, `md`, `sarif` (2.1.0 для CI), коды возврата `0`/`1`/`2`.
  - Подавление шума (`.code-reviewerignore`, `# code-reviewer: ignore`, `# code-reviewer: ignore-file`), `baseline` и `--verify`.
- **Инструменты анализа и качества:**
  - `map deps / files / outline / cycles / search / export` (включая Graphviz DOT).
  - `demo` — полный прогон пайплайна на записанном ответе без сети, ключа API и даже без `git`.
  - `eval` — измерение `precision / recall / F1` на 22 тестовых кейсах (Python, JS, Go).
  - `history`, `cache`, `install-hook` (`pre-commit`), `keys`, `config`, `doctor`.

## Установка

Доступны две равнозначные CLI-команды: `code-reviewer` и короткий алиас `cr`.

### Готовый автономный бинарник (без Python)

**Linux / macOS** (установка в `/usr/local/bin`):

```bash
curl -fsSL https://raw.githubusercontent.com/maks-gargamel/code-reviewer/main/install.sh | sh
```

Удаление: `curl -fsSL https://raw.githubusercontent.com/maks-gargamel/code-reviewer/main/install.sh | sh -s -- --uninstall`.

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/maks-gargamel/code-reviewer/main/install.ps1 | iex
```

Готовые файлы для Linux (`x86_64`), macOS (`arm64`) и Windows (`x86_64`) публикуются в [GitHub Releases](https://github.com/maks-gargamel/code-reviewer/releases).

### Локальная сборка бинарника

На Linux предварительно установите `binutils` (`sudo apt install -y binutils`).

```bash
uv sync --all-extras
uv run python scripts/build_binary.py --checksum
./install.sh --local dist/code-reviewer
```

### Установка для разработки (Python 3.12+ / целевой 3.14)

**Через `uv`:**

```bash
uv sync --all-extras          # полная установка (tree-sitter, pytest, ruff, mypy, pyinstaller)
# или только базовый вариант (Python + Tier D):
uv sync
```

**Через `pip`:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[treesitter]"
```

## Быстрый старт

```bash
# 1. Проверить работу без интернета, ключа и git
code-reviewer demo

# 2. В своём git-репозитории построить карту (без обращений к LLM)
cd /path/to/repo
code-reviewer init
code-reviewer status

# 3. Исследовать карту и граф зависимостей
code-reviewer map files --lang python
code-reviewer map outline src/app/service.py
code-reviewer map deps src/app/service.py --depth 2
code-reviewer map cycles
code-reviewer map search MyClass
code-reviewer map export --output graph.dot

# 4. Проверить промпт и оценку токенов без обращения к модели
code-reviewer check --dry-run
code-reviewer check --all --dry-run        # план и стоимость сканирования всего репозитория

# 5. Запустить ревью
code-reviewer check
code-reviewer check --all --no-tests -p src/
code-reviewer check --range HEAD~3..HEAD --format sarif --output review.sarif
```

Подробный справочник по всем командам и флагам — в [`docs/CLI.md`](docs/CLI.md). Архитектура и форматы данных описаны в [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Настройка провайдера и ключей API

При первом `code-reviewer init` (или `code-reviewer config init`) создаётся `.code-reviewer/config.toml`. Поддерживается любой OpenAI-совместимый API: Gemini, OpenAI, DeepSeek, Groq, OpenRouter, Ollama, LM Studio.

Ключи можно задать:
- через CLI-команду `keys` (сохраняет их в `.code-reviewer/keys.txt` с правами `0600` и автоматически добавляет `.code-reviewer/` в `.gitignore`):

```bash
code-reviewer keys add                     # интерактивный скрытый ввод (рекомендуется)
code-reviewer keys add -c "gemini"         # с комментарием к ключу
code-reviewer keys list                    # посмотреть добавленные ключи (в маске)
code-reviewer keys remove 1                # удалить ключ по номеру из list
code-reviewer keys clear                   # удалить файл ключей целиком
```

- вручную в файле `.code-reviewer/keys.txt` (по одному ключу на строку, `#` — комментарий);
- через переменную окружения `CODE_REVIEWER_API_KEY` (можно несколько через запятую или пробел).

При `401`, `403`, `429` или серии `5xx` потокобезопасный пул ключей автоматически переключается на следующий рабочий ключ. Для локальных провайдеров (`provider = "ollama"` или `base_url` на `localhost`/`127.0.0.1`) API-ключ не требуется.

## Eval-стенд и разработка

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest
uv run code-reviewer eval --fail-under 0.8
```
