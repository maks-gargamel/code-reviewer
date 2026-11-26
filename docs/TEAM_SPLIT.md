# Разделение ответственности и перенос в новый репозиторий (`code-reviewer`)

Документ описывает точное распределение всех **120 отслеживаемых файлов проекта** между **Максимом** (локальный анализ, парсеры, граф зависимостей, Git/VCS, инфраструктура) и **Машей** (LLM-клиент, AI-ревью, eval-стенд и вывод результатов), технические стыки между частями и безопасный порядок заливки в новый GitHub-репозиторий без поломки импортов.

---

## 1. Сводная карта ролей

Проект спроектирован по двухпроходной схеме (см. [ARCHITECTURE.md](../ARCHITECTURE.md)), поэтому код естественно делится на две части с узким контрактом данных в `core/models.py`:

| Критерий | Максим (Проход 1 и инфраструктура) | Маша (Проход 2 и визуализация) | Общий стык / интеграция |
|---|---|---|---|
| **Ключевая задача** | Локальная индексация репозитория без LLM: парсинг кода, граф зависимостей, Git diff и хуки, упаковка | Взаимодействие с LLM, конвейер AI-ревью, фильтрация находок, eval-стенд и весь вывод | Сборка CLI (`app.py`), интеграционные тесты и общая документация |
| **Слои и папки в `src/`** | `core/`, `vcs/`, `indexing/` (кроме `summarize.py`), `cli/common.py`, `cli/init_cmd.py`, `cli/map_cmd.py` | `llm/`, `indexing/summarize.py`, `review/`, `render/`, `workflows/`, `cli/check_cmd.py`, `cli/keys_cmd.py`, `cli/eval_cmd.py`, `cli/ops_cmd.py` | `cli/__init__.py`, `cli/app.py` (частично `cli/ops_cmd.py` для `install-hook`) |
| **Команды CLI** | `init`, `status`, `map` (`stats`, `deps`, `cycles`, `files`, `outline`, `search`, `rebuild`) | `check`, `mark-reviewed`, `keys`, `eval`, `demo`, `config`, `history`, `cache`, `doctor` | `install-hook` (CLI-обёртка у Маши в `ops_cmd.py`, логика в `vcs/hooks.py` у Максима) |
| **Тесты и данные** | 10 файлов тестов (`tests/conftest.py` + 9 unit-тестов) | 12 файлов тестов + 22 кейса в `evals/` | 2 интеграционных теста (`test_cli.py`, `test_git_integration.py`) |
| **Сборка и окружение** | `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `LICENSE`, `scripts/*`, `install.*`, `.github/workflows/*` | — | Общая документация (`README.md`, `SPEC.md`, `ARCHITECTURE.md`, `STATUS.md`, `GEMINI.md`, `docs/CLI.md`) |

---

## 2. Полный перечень файлов Максима

### 2.1. Конфигурация проекта, скелет пакета, сборка и CI (13 файлов)

| Файл | Назначение |
|---|---|
| `pyproject.toml` | Описание пакета, зависимости (`typer`, `rich`, `httpx`, `pydantic`, optional `treesitter`/`build`), настройки `pytest`, `ruff`, `mypy` |
| `uv.lock` | Зафиксированный lock-файл зависимостей `uv` |
| `.python-version` | Целевая версия Python проекта |
| `.gitignore` | Исключение `.code-reviewer/`, `.venv/`, артефактов сборки и кэшей |
| `LICENSE` | Лицензия проекта |
| `src/code_reviewer/__init__.py` | Версия и метаданные пакета |
| `src/code_reviewer/__main__.py` | Точка входа `python -m code_reviewer` |
| `scripts/build_binary.py` | Скрипт сборки standalone-бинарника через PyInstaller |
| `scripts/pyinstaller_entry.py` | Входная точка для PyInstaller-сборки |
| `install.sh` | Скрипт установки под Linux / macOS |
| `install.ps1` | Скрипт установки под Windows (PowerShell) |
| `.github/workflows/ci.yml` | CI-пайплайн (тесты, `ruff`, `mypy`) |
| `.github/workflows/release.yml` | Release-пайплайн сборки и публикации бинарников |

### 2.2. Слой `core/` — общий фундамент и контракт данных (7 файлов)

Этот слой заливается **первым**, так как его модели (`RepoMap`, `Finding`, `MAP_VERSION = 2`), конфиги и атомарное хранилище используют модули обоих участников:

| Файл | Назначение |
|---|---|
| `src/code_reviewer/core/__init__.py` | Инициализация и экспорт базовых сущностей `core` |
| `src/code_reviewer/core/models.py` | `MAP_VERSION = 2`, датаклассы (`Symbol`, `ImportRef`, `GraphEdge`, `FileInfo`, `RepoMap`, `Finding`) и enum-типы (`EdgeStatus`, `SymbolKind`, `Severity`, `OutputFormat`, `FailOn`, `Strictness`, `ReviewLanguage`) |
| `src/code_reviewer/core/config.py` | Pydantic-схемы и чтение `.code-reviewer/config.toml`, дефолтные значения, разрешение путей |
| `src/code_reviewer/core/errors.py` | Иерархия исключений (`AppError` и др.) для согласованных кодов возврата CLI |
| `src/code_reviewer/core/fs.py` | Вспомогательные файловые операции и атомарная запись (`temp file` + `os.replace`) |
| `src/code_reviewer/core/patterns.py` | Glob-матчинг и проверка исключений путей |
| `src/code_reviewer/core/storage.py` | Работа с состоянием и кэшем: `State`, `Baseline`, `ResponseCache`, `cache_key`, `save_last_review` |

### 2.3. Слой `vcs/` — работа с Git (4 файла)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/vcs/__init__.py` | Экспорт VCS-интерфейсов |
| `src/code_reviewer/vcs/git.py` | Безопасная обёртка над `git` CLI (`HEAD`, ветки, статус, корни репозитория, коммиты) |
| `src/code_reviewer/vcs/diff.py` | Сбор и разбор diff для режимов `auto`, `staged`, `unstaged`, `--last`, `--since-last`, `--commit`, `--range` |
| `src/code_reviewer/vcs/hooks.py` | Установка и проверка `.git/hooks/pre-commit` без перезаписи чужих хуков без `--force` |

### 2.4. Слой `indexing/` — парсинг кода и граф зависимостей (11 файлов; без `summarize.py`)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/indexing/__init__.py` | Публичный интерфейс индексации (на Этапе 1 — без реэкспорта `summarize.py`, см. раздел 5) |
| `src/code_reviewer/indexing/discovery.py` | Обход файлов репозитория с учётом `.gitignore` и игнор-правил, определение языков |
| `src/code_reviewer/indexing/builder.py` | Инкрементальная сборка карты репозитория (`.code-reviewer/map.json`), контроль `MAP_VERSION` |
| `src/code_reviewer/indexing/resolver.py` | Разрешение импортов в связи графа зависимостей между файлами/модулями |
| `src/code_reviewer/indexing/graph.py` | Операции над графом зависимостей: входящие/исходящие рёбра, обход окрестности, поиск циклов |
| `src/code_reviewer/indexing/parsers/__init__.py` | Реестр и выбор адаптера парсинга по расширению/языку |
| `src/code_reviewer/indexing/parsers/base.py` | Базовый протокол/интерфейс парсера |
| `src/code_reviewer/indexing/parsers/python_ast.py` | Tier A парсер Python на stdlib `ast` (символы, сигнатуры, docstrings, импорты, исключение `if TYPE_CHECKING:`) |
| `src/code_reviewer/indexing/parsers/treesitter.py` | Интеграция `tree-sitter` и `tree-sitter-language-pack` с соблюдением офлайн-флага `map.fetch_grammars` |
| `src/code_reviewer/indexing/parsers/ts_ast.py` | Извлечение символов, сигнатур и импортов для 16 языков Tier B (JS/TS/TSX, Go, Rust, Java, Kotlin, C/C++, C#, Ruby, PHP, Swift, Lua, Dart, Scala) |
| `src/code_reviewer/indexing/parsers/plain.py` | Tier D `PlainAdapter` для остальных текстовых файлов и деградации при отсутствии грамматики |

### 2.5. Команды CLI Максима (3 файла)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/cli/common.py` | Общие хелперы CLI: `cli_command`, `RootOption`, `resolve_root`, `ensure_gitignore`, `require_map`, `refresh_map`, `resolve_file` |
| `src/code_reviewer/cli/init_cmd.py` | Команды `init` (индексация + опциональный `--ai-summary`) и `status` |
| `src/code_reviewer/cli/map_cmd.py` | Подкоманды `map`: просмотр статистики, зависимостей, циклов, файлов, структуры символов, поиска и пересборки карты |

### 2.6. Юнит-тесты Максима (10 файлов)

| Файл | Что тестирует |
|---|---|
| `tests/conftest.py` | Общие фикстуры репозиториев и тестовых конфигов (заливается на Этапе 1) |
| `tests/test_config.py` | Загрузка, валидация и значения по умолчанию `config.toml` |
| `tests/test_patterns.py` | Glob-паттерны и фильтрация путей |
| `tests/test_diff.py` | Парсинг и режимы сбора `git diff` |
| `tests/test_discovery.py` | Поиск файлов с учётом `.gitignore` и исключений |
| `tests/test_python_parser.py` | Tier A Python `ast`-парсер (символы, сигнатуры, `TYPE_CHECKING`) |
| `tests/test_treesitter.py` | Работа `tree-sitter` и политика `fetch_grammars` |
| `tests/test_languages.py` | Извлечение символов и импортов во всех поддерживаемых языках (`ts_ast.py` / `plain.py`) |
| `tests/test_map_and_resolver.py` | Построение `RepoMap`, разрешение импортов и инкрементальное обновление |
| `tests/test_graph.py` | Граф зависимостей, связи и поиск циклов |

---

## 3. Полный перечень файлов Маши

### 3.1. Слой `llm/` — клиент к OpenAI-совместимому API и пул ключей (4 файла)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/llm/__init__.py` | Публичный экспорт модуля `llm` |
| `src/code_reviewer/llm/protocol.py` | Протокол/интерфейс `LLMClient` |
| `src/code_reviewer/llm/keys.py` | Потокобезопасный `KeyPool` (под `threading.Lock`), загрузка/проверка `.code-reviewer/keys.txt` и env, ротация ключей |
| `src/code_reviewer/llm/client.py` | HTTP-клиент OpenAI-совместимого `/chat/completions` (`httpx`), фабрика `client_from_config`, кэширование ответов |

### 3.2. AI-сводки в `indexing/` (1 файл)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/indexing/summarize.py` | Режим `init --ai-summary`: генерация кратких описаний модулей по сигнатурам и docstring через `LLMClient` и кэширование в `.code-reviewer/summary-cache/` (`summarize_map`, `summarize_repo`, `summarized_count`) |

### 3.3. Слой `review/` — пайплайн AI-ревью (10 файлов)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/review/__init__.py` | Экспорт компонентов пайплайна ревью |
| `src/code_reviewer/review/secrets.py` | Обнаружение и маскирование секретов/токенов перед отправкой кода в LLM |
| `src/code_reviewer/review/chunking.py` | Разбиение diff на чанки по связным компонентам графа зависимостей с лимитами `max_files` / `max_chunks` и фиксацией `omitted` |
| `src/code_reviewer/review/context.py` | Сбор релевантного контекста вокруг изменений из `RepoMap` |
| `src/code_reviewer/review/prompt.py` | Формирование `system`/`user` промптов с учётом `strictness`, языка отчёта и `omitted` |
| `src/code_reviewer/review/findings.py` | Разбор JSON-ответа модели, расчёт стабильного ID `sha256(f"{file}:{line}:{message}")[:8]`, `filter_to_paths` |
| `src/code_reviewer/review/ignore.py` | Фильтрация находок по `.code-reviewerignore` и inline-директивам по полному тексту файла |
| `src/code_reviewer/review/history.py` | Запись запусков в `history.jsonl`, работа с `baseline` и `last-review.json` |
| `src/code_reviewer/review/scan.py` | Режим `--all`: сканирование всей карты репозитория с `-p/--path`, `--no-tests` и защитой квоты |
| `src/code_reviewer/review/pipeline.py` | Главный оркестратор Прохода 2: секреты → чанки → контекст → LLM/кэш → строгий порядок фильтрации (`filter_to_paths` → `ignore` → `baseline`) |

### 3.4. Слой `render/` — терминальный UI и форматы отчёта (5 файлов)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/render/__init__.py` | Экспорт функций рендеринга |
| `src/code_reviewer/render/console.py` | Базовые `console`, `err_console`, безопасное экранирование `_s()` / `escape()`, красивый вывод для `init`, `status`, `map` (`print_init_summary`, `print_status`, `print_cycles`, `print_deps`, `print_files`, `print_outline`, `print_search`) |
| `src/code_reviewer/render/views.py` | Rich-представления для списков находок, истории, конфигурации, кэша, ключей, `demo` и `eval` |
| `src/code_reviewer/render/report.py` | Форматтеры отчёта: `table` (Rich), `json`, `md` (Markdown) |
| `src/code_reviewer/render/sarif.py` | Экспорт отчёта в стандартный формат `sarif` (SARIF 2.1.0) |

### 3.5. Слой `workflows/` — сценарии запуска и диагностика (4 файла)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/workflows/__init__.py` | Инициализация `workflows` |
| `src/code_reviewer/workflows/demo.py` | Офлайн-демо (`code-reviewer demo`) полного пайплайна ревью без сети и API-ключей |
| `src/code_reviewer/workflows/eval.py` | Eval-стенд для расчёта precision / recall / F1 по набору `evals/*.json` |
| `src/code_reviewer/workflows/doctor.py` | Команда `doctor`: диагностика окружения, git, парсеров (включая часть Максима), конфига, ключей и LLM API |

### 3.6. Эталонные кейсы `evals/` (22 файла)

Все 22 JSON-сценария оценки качества AI-ревью:

| Категория | Файлы |
|---|---|
| **Python — логика и безопасность (14)** | `evals/bare_except.json`, `evals/dict_keyerror.json`, `evals/eval_usage.json`, `evals/float_equality.json`, `evals/hardcoded_password.json`, `evals/mutable_default.json`, `evals/mutate_during_iteration.json`, `evals/mutation_of_argument.json`, `evals/none_deref.json`, `evals/off_by_one.json`, `evals/resource_leak.json`, `evals/shell_injection.json`, `evals/sql_injection.json`, `evals/swallowed_exception.json` |
| **JavaScript (5)** | `evals/js_inner_html.json`, `evals/js_loose_equality.json`, `evals/js_missing_await.json`, `evals/js_sort_without_comparator.json`, `evals/missing_return_js.json` |
| **Go (3)** | `evals/go_defer_in_loop.json`, `evals/go_nil_map.json`, `evals/ignored_error_go.json` |

### 3.7. Команды CLI Маши (4 файла)

| Файл | Назначение |
|---|---|
| `src/code_reviewer/cli/check_cmd.py` | Команды `check` (в т.ч. `--all`, `-p/--path`, `--no-tests`, `-y/--yes`, `--dry-run`) и `mark-reviewed` |
| `src/code_reviewer/cli/keys_cmd.py` | Команды управления пулом ключей `keys` |
| `src/code_reviewer/cli/eval_cmd.py` | Команда запуска eval-стенда `eval` (в т.ч. `--fail-under`) |
| `src/code_reviewer/cli/ops_cmd.py` | Команды `config` (`show`, `check`, `init`), `history`, `cache`, `doctor`, а также `install-hook` (вызывает `vcs/hooks.py` Максима) |

### 3.8. Тесты Маши (12 файлов)

| Файл | Что тестирует |
|---|---|
| `tests/test_llm.py` | OpenAI-совместимый HTTP-клиент, обработка ошибок, кэш ответов |
| `tests/test_keys.py` | Потокобезопасный `KeyPool`, загрузка и ротация ключей |
| `tests/test_secrets.py` | Маскирование секретов перед отправкой в LLM |
| `tests/test_chunking.py` | Разбиение на чанки по графу зависимостей и фиксация `omitted` |
| `tests/test_findings.py` | Парсинг JSON-находок, стабильные ID и `filter_to_paths` |
| `tests/test_ignore.py` | Директивы `.code-reviewerignore` и inline-игнорирование |
| `tests/test_history_and_baseline.py` | История запусков `history.jsonl` и фильтрация по `baseline` |
| `tests/test_review_pipeline.py` | Сквозной пайплайн AI-ревью (от diff до отфильтрованных находок) |
| `tests/test_scan.py` | Режим `--all` (`scan.py`), фильтры `-p/--path`, `--no-tests` |
| `tests/test_summarize.py` | Генерация и кэширование AI-сводок `indexing/summarize.py` |
| `tests/test_eval.py` | Подсчёт метрик и запуск `eval` |
| `tests/test_demo_and_render.py` | Офлайн `demo` и форматы вывода Rich / JSON / Markdown / SARIF |

---

## 4. Общие интеграционные файлы и документация (Этап 3, 10 файлов)

Эти файлы связывают Проход 1 и Проход 2 воедино или документируют весь проект:

| Файл | Ответственные | Назначение |
|---|---|---|
| `src/code_reviewer/cli/__init__.py` | Максим / совместно | Инициализация пакета CLI |
| `src/code_reviewer/cli/app.py` | Максим / совместно | Корневое Typer-приложение: регистрирует `init`, `status`, `check`, `mark-reviewed`, `demo`, `eval`, `history`, `cache`, `install-hook`, `doctor`, а также подгруппы `map`, `config`, `keys` |
| `tests/test_cli.py` | Совместно | Проверка всех CLI-команд (`init`/`status`/`map` — Максим, `check`/`keys`/`eval`/`demo`/`config`/`history` — Маша) |
| `tests/test_git_integration.py` | Максим / совместно | Сквозные сценарии в реальном git-репозитории (`init`, `diff`, `hooks`, `check`) |
| `README.md` | Совместно | Обзорное описание проекта для GitHub |
| `SPEC.md` | Совместно | Полная функциональная и техническая спецификация |
| `ARCHITECTURE.md` | Совместно | Слои, диаграммы потоков и форматы `.code-reviewer/` |
| `STATUS.md` | Совместно | Статус готовности и результаты проверок |
| `GEMINI.md` | Совместно | Правила и инженерные инварианты проекта |
| `docs/CLI.md` | Маша / совместно | Подробный справочник команд и флагов CLI |

*(Итого: **48** файлов Максима + **62** файла Маши + **10** общих интеграционных/документационных файлов = **120** отслеживаемых файлов в `git ls-files`.)*

---

## 5. Ключевые технические стыки (чтобы код не падал при раздельном пуше)

1. **`indexing/__init__.py` ↔ `indexing/summarize.py`:**
   `indexing/__init__.py` реэкспортирует `summarize_map`, `summarize_repo`, `summarized_count` из `indexing/summarize.py` (файл Маши, зависит от `LLMClient`).
   **Решение:** на Этапе 1 Максим кладёт `indexing/__init__.py` **без** импорта `.summarize`; на Этапе 2 Маша добавляет `indexing/summarize.py` вместе с `llm/` и возвращает реэкспорт обратно.

2. **`cli/init_cmd.py`, `cli/map_cmd.py`, `cli/common.py` ↔ `render/console.py` и `llm/client.py`:**
   Даже «максимовские» CLI-команды импортируют функции отрисовки Маши из `render.console` (`_s`, `console`, `err_console`, `print_init_summary`, `print_status`, `print_cycles`, `print_deps`, `print_files`, `print_outline`, `print_search`), а `init_cmd.py` для `--ai-summary` импортирует `client_from_config` и `indexing.summarize`.
   **Решение:** весь каталог `src/code_reviewer/cli/` переносится только на **Этапе 3**, когда в репозитории уже лежат и `indexing/`, и `llm/`, и `render/`.

3. **`core/models.py`, `core/storage.py`, `core/errors.py` — общий контракт:**
   Нельзя менять имена полей `Symbol`, `ImportRef`, `GraphEdge`, `FileInfo`, `RepoMap`, `Finding` и значение `MAP_VERSION = 2` без согласования, иначе сломается передача данных между `indexing/` (Максим) и `review/` + `render/` (Маша).

4. **Инвариант порядка зависимостей между слоями:**
   `cli/` → `workflows/`, `review/`, `indexing/`, `render/` → `vcs/` → `core/`. Циклические импорты запрещены.

---

## 6. Что КАТЕГОРИЧЕСКИ НЕЛЬЗЯ коммитить в новый репозиторий

Перед заливкой убедитесь, что в коммиты **не попали** локальные и секретные каталоги (проверяйте через `git status` перед каждым `git push`):

- `.code-reviewer/` — **внутри лежит `keys.txt` с реальными API-ключами**, а также `map.json`, `state.json`, `baseline.json`, `last-review.json`, `history.jsonl`, `cache/`, `eval-cache/`, `summary-cache/`;
- `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`;
- `dist/`, `build/`, `*.egg-info/`.

---

## 7. Пошаговый план переноса в новый GitHub-репозиторий (3 этапа)

Чтобы не возникло конфликтов и ошибок импорта (`ModuleNotFoundError`), лучше заливать проект по очереди в 3 этапа (через ветки + PR или прямые коммиты).

### Этап 1. Максим заливает фундамент, VCS, парсеры и граф

1. Создаёт новый пустой репозиторий на GitHub и клонирует его.
2. Переносит свои файлы:
   - Корень и базовый пакет: `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `LICENSE`, `src/code_reviewer/__init__.py`, `src/code_reviewer/__main__.py`;
   - Слои ядра и Git: целиком `src/code_reviewer/core/` и `src/code_reviewer/vcs/`;
   - Слой индексации: `src/code_reviewer/indexing/` (**без** `summarize.py`);
   - Свои 10 файлов тестов: `tests/conftest.py`, `tests/test_config.py`, `tests/test_patterns.py`, `tests/test_diff.py`, `tests/test_discovery.py`, `tests/test_python_parser.py`, `tests/test_treesitter.py`, `tests/test_languages.py`, `tests/test_map_and_resolver.py`, `tests/test_graph.py`.
3. **Обязательная временная правка на Этапе 1:** в `src/code_reviewer/indexing/__init__.py` временно убрать импорт и реэкспорт `.summarize` (`summarize_map`, `summarize_repo`, `summarized_count`).
4. Проверяет свою часть и пушит в репозиторий:
   ```bash
   uv sync --all-extras
   uv run pytest tests/test_config.py tests/test_patterns.py tests/test_diff.py tests/test_discovery.py tests/test_python_parser.py tests/test_treesitter.py tests/test_languages.py tests/test_map_and_resolver.py tests/test_graph.py
   ```

### Этап 2. Маша заливает LLM, AI-ревью, eval и рендеринг

1. Подтягивает коммиты Максима (`git pull` или создаёт ветку от `main`).
2. Переносит свою часть:
   - `src/code_reviewer/llm/`;
   - `src/code_reviewer/indexing/summarize.py` и возвращает реэкспорт `summarize_map`, `summarize_repo`, `summarized_count` в `src/code_reviewer/indexing/__init__.py`;
   - `src/code_reviewer/review/`;
   - `src/code_reviewer/render/`;
   - `src/code_reviewer/workflows/`;
   - Все 22 файла в `evals/`;
   - Свои 12 тестовых файлов (`test_llm.py`, `test_keys.py`, `test_secrets.py`, `test_chunking.py`, `test_findings.py`, `test_ignore.py`, `test_history_and_baseline.py`, `test_review_pipeline.py`, `test_scan.py`, `test_summarize.py`, `test_eval.py`, `test_demo_and_render.py`).
3. Проверяет свою часть и пушит (или открывает PR в `main`):
   ```bash
   uv sync --all-extras
   uv run pytest
   ```

### Этап 3. Интеграция CLI, сборка, сквозные тесты и документация

1. После слияния Этапа 2 оба участника (либо Маша первой вливает `check_cmd.py`, `keys_cmd.py`, `eval_cmd.py`, `ops_cmd.py`, а Максим добавляет `common.py`, `init_cmd.py`, `map_cmd.py`, `__init__.py`, `app.py`) переносят:
   - Весь `src/code_reviewer/cli/`;
   - Скрипты сборки, установщики и CI (`scripts/*`, `install.sh`, `install.ps1`, `.github/workflows/*`);
   - Интеграционные тесты (`tests/test_cli.py`, `tests/test_git_integration.py`);
   - Документацию (`README.md`, `SPEC.md`, `ARCHITECTURE.md`, `STATUS.md`, `GEMINI.md`, `docs/CLI.md`).
2. Запускаете полную финальную проверку всего проекта:
   ```bash
   uv sync --all-extras
   uv run pytest
   uv run ruff check src tests scripts
   uv run mypy src
   uv run code-reviewer demo
   ```

---

## 8. Краткая шпаргалка для защиты проекта

### Что уверенно объясняет Максим
- **Двухпроходная архитектура:** зачем строить локальную карту (`init` без LLM) до вызова модели — это экономит токены, даёт модели точный граф зависимостей вместо «слепого» куска diff и позволяет проверять изменения офлайн.
- **Многоязычный парсинг:** Tier A для Python через стандартный `ast` (выделение функций/классов, сигнатур, docstring и импортов), Tier B для 16 языков через `tree-sitter` + `ts_ast.py`, Tier D (`PlainAdapter`) как безопасный fallback. Отдельно: строгий офлайн-режим `map.fetch_grammars = false` и инкрементальная пересборка по `MAP_VERSION = 2`.
- **Граф зависимостей и `TYPE_CHECKING`:** как `resolver.py` превращает текстовые импорты в рёбра графа, как `graph.py` ищет циклические зависимости, и почему импорты внутри `if TYPE_CHECKING:` в Python намеренно исключаются, чтобы не плодить ложные циклы.
- **Git и безопасность хуков:** сбор diff в разных режимах (`staged`, `unstaged`, `--since-last`, `--range`) и установка `pre-commit` хука, который никогда не затирает чужой скрипт без `--force`.

### Что уверенно объясняет Маша
- **Работа с LLM и пул ключей:** OpenAI-совместимый клиент (`httpx`), детерминированный кэш `sha256(model, system, user)` для повторяемых прогонов, потокобезопасная ротация множества ключей из `.code-reviewer/keys.txt` (`KeyPool` под `threading.Lock`) и нераскрытие ключей в логах/`doctor`.
- **Интеллектуальный конвейер ревью (`review/`):** маскирование секретов до отправки в модель, разбиение diff на чанки по связным компонентам графа, подбор контекста из карты Максима, явный учёт невлезших файлов в `omitted`, а также сканирование всей кодовой базы в `--all`.
- **Борьба с галлюцинациями и шумом:** строгий порядок фильтрации `filter_to_paths` (отсев несуществующих/левых путей и приведение путей) → `ignore` (`.code-reviewerignore` и inline-директивы по полному тексту файла) → `baseline`, плюс стабильный ID находки `sha256(f"{file}:{line}:{message}")[:8]`.
- **Качество и визуальный вывод:** воспроизводимый офлайн-`demo`, оценка качества `eval` (precision / recall / F1 по 22 JSON-кейсам), красивый терминальный вывод в `rich` с обязательным экранированием пользовательских строк (`_s()` / `escape()`) и экспорт отчётов в JSON, Markdown и SARIF 2.1.0.
