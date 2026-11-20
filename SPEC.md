# Спецификация проекта `code-reviewer` (`cr`)

Документ фиксирует функциональные и нефункциональные требования, контракты CLI, модель безопасности, форматы данных и схему дистрибуции локального AI код-ревьюера **`code-reviewer`** (короткий алиас `cr`).

---

## 1. Назначение и основная идея

`code-reviewer` анализирует изменения в коде (`git diff` или весь репозиторий целиком) в контексте **всего проекта**, а не изолированного патча.

Вместо отправки всей кодовой базы в языковую модель система работает по **двухпроходной схеме**:
1. **Проход 1 (`init`, локально без LLM):** обход репозитория, извлечение символов/сигнатур/doc-комментариев/импортов, построение графа зависимостей (`.code-reviewer/map.json`).
2. **Проход 2 (`check`, с вызовом LLM):** сбор diff, маскирование секретов, разбиение на чанки по связным компонентам графа, добавление в промпт компактных сигнатур связанных файлов, вызов OpenAI-совместимого API, разбор JSON-находок, детерминированная фильтрация и генерация отчёта.

---

## 2. Функциональные требования

### 2.1. Интерфейс командной строки (`code-reviewer` / `cr`)

Поддерживаются две равнозначные точки входа: `code-reviewer` и `cr`.

| Команда | Назначение | Обращается к LLM? |
|---|---|---|
| `init [--root] [--rebuild] [--ai-summary] [--summary-limit N] [--jobs N]` | Построить/обновить карту `.code-reviewer/map.json`, создать `config.toml`, добавить `.code-reviewer/` в `.gitignore`, автоматически зафиксировать `HEAD` как точку отсчёта (если есть коммиты и `last_reviewed_commit` пуст) | Нет (только с `--ai-summary`) |
| `status [--root] [--json]` | Показать ветку, `HEAD`, статистику карты, устаревшие файлы и точку последнего ревью | Нет |
| `check` *(без флагов области)* | Проверить staged-изменения (если есть) или working tree + untracked | Да (кроме `--dry-run`) |
| `check --last` | Проверить последний коммит (`HEAD`) | Да (кроме `--dry-run`) |
| `check --since-last` | Проверить все коммиты от `last_reviewed_commit` до `HEAD` | Да (кроме `--dry-run`) |
| `check --commit REF` | Проверить указанный коммит без сдвига `last_reviewed_commit` | Да (кроме `--dry-run`) |
| `check --range A..B` | Проверить диапазон коммитов без сдвига `last_reviewed_commit` | Да (кроме `--dry-run`) |
| `check --all [-p/--path PATH]... [--no-tests] [-y/--yes]` | Проверить все кодовые файлы репозитория по карте (`--dry-run` показывает таблицу чанков, попаданий в кэш и запросов; при `> 10` некэшированных удалённых запросах требует подтверждения или `--yes`/`--max-chunks`) | Да (кроме `--dry-run`) |
| `mark-reviewed [--root]` | Зафиксировать текущий `HEAD` как `last_reviewed_commit` | Нет |
| `map deps\|files\|outline\|cycles\|search\|export` | Навигация по карте, поиск символов, поиск циклов (Косараджу), экспорт в Graphviz DOT | Нет |
| `keys add\|list\|remove\|clear` | Управление пулом API-ключей в `.code-reviewer/keys.txt` (скрытый ввод, `--stdin`, комментарии, маска в выводе) | Нет |
| `config show\|check\|init` | Просмотр итогового конфига, валидация ключей TOML, создание шаблона | Нет |
| `history [--limit N] [--json]` | История проверок и метрики (среднее число замечаний, повторяемость, `acceptance`, категории) | Нет |
| `cache [--clean] [--json]` | Статистика и очистка кэшей (`cache`, `eval-cache`, `summary-cache`) | Нет |
| `install-hook [--force] [--uninstall]` | Установка/удаление git `pre-commit` хука (`check --quiet`) | Нет |
| `doctor [--root]` | Диагностика Python, Git, конфига, карты, `tree-sitter`, грамматик, хука и ключей | Нет |
| `demo` | Демонстрация полного пайплайна без сети, ключа и `git` | Нет |
| `eval [--cases] [--jobs N] [--fail-under F] [--no-cache] [--json]` | Оценка качества модели (`precision`, `recall`, `F1`) на наборе JSON-кейсов | Да (при промахе кэша) |

### 2.2. Поддержка языков (`indexing`)

- **Tier A — Python** (`.py`, `.pyi`, `.pyw`): stdlib `ast` (`PythonAdapter`), без внешних зависимостей. Аннотации типов, декораторы (до 3), `async def`, вложенные функции, константы верхнего уровня, docstrings, импорты без `if TYPE_CHECKING:`, flat и `src/`-layout, явные `SyntaxError`.
- **Tier B — Tree-Sitter + AST-дополнение** (`TreeSitterAdapter` + `ts_ast.py`):
  16 грамматик: `javascript` (`.js`, `.jsx`, `.mjs`, `.cjs`), `typescript` (`.ts`, `.mts`, `.cts`), `tsx` (`.tsx`), `go` (`.go`), `rust` (`.rs`), `java` (`.java`), `kotlin` (`.kt`, `.kts`), `csharp` (`.cs`), `ruby` (`.rb`), `php` (`.php`), `c` (`.c`, `.h`), `cpp` (`.cpp`, `.cc`, `.hpp`, `.hh`, `.cxx`), `scala` (`.scala`), `swift` (`.swift`), `lua` (`.lua`), `dart` (`.dart`).
- **Граф зависимостей (`resolver.py`):** разрешение относительных импортов (`./`, `../`), `#include` (с поиском в `include/`/`inc/`/`src/`), пакетов Go по `go.mod`, путей Rust (`crate::`, `super::`, `self::`, `mod`), пакетов/пространств имён Java, Kotlin, Scala, PHP, Ruby, Lua, Dart (`pubspec.yaml`) и C#.
- **Tier D — Catch-all** (`PlainAdapter`): прочие языки, конфиги и разметка (`.sh`, `.sql`, `.yaml`, `.toml`, `.md`, `.vue`, `.svelte`, `.zig`, `.ex`, `.proto`, `Dockerfile`, `Makefile` и т. д.) — определяется язык, LOC и первый комментарий; в LLM-ревью `git diff` участвуют наравне со всеми языками.

### 2.3. Фильтрация находок и форматы вывода

1. Строгий порядок постобработки ответа модели:
   `filter_to_paths` (отсев галлюцинаций вне diff, нормализация пути, пересчёт ID) → `ignore` (`.code-reviewerignore` и `code-reviewer: ignore` / `ignore-file` по полному тексту файла) → `baseline`.
2. Стабильный ID находки: `sha256(f"{file}:{line}:{message}")[:8]`.
3. Форматы отчёта: `table` (Rich-таблица), `json`, `md`, `sarif` (SARIF 2.1.0 с `partialFingerprints`).
4. Коды возврата CLI:
   - `0` — успех / замечания ниже порога `output.fail_on`;
   - `1` — найдены замечания `>= output.fail_on`, провален `eval --fail-under` или `doctor` нашёл `FAIL`;
   - `2` — ошибка CLI-флагов, конфига, git, отсутствующей карты или LLM API.

---

## 3. Нефункциональные требования и безопасность

- **Секреты и ключи API:**
  - ключи читаются из `.code-reviewer/keys.txt` (`0600` на POSIX, `.code-reviewer/` в `.gitignore`) и переменной окружения `CODE_REVIEWER_API_KEY`;
  - исходные значения ключей **никогда** не выводятся в stdout, stderr, JSON, логи и `doctor`;
  - пул ключей (`KeyPool`) потокобезопасен под `threading.Lock` и автоматически ротирует ключ при `401`/`403`/`429`/`5xx` (и 400 с ошибкой ключа), не ротируя при геоблоке региона;
  - `redact()` автоматически маскирует известные форматы токенов, PEM-ключи, JWT, строковые литералы секретных переменных и высокоэнтропийные строки перед отправкой в облачный API (не ломая `obj.password`).
- **Детерминизм и прозрачность ограничений:**
  - ключ кэша ответов `sha256(model, system, user)` (нормализованный без путей и времени);
  - файлы, не попавшие в `--max-files` или `--max-chunks`, никогда не отбрасываются молча — попадают в `omitted` и отражаются в промпте и отчёте;
  - при `map.fetch_grammars = false` код никогда не обращается к сети для скачивания грамматик `tree-sitter`.
- **Надёжность хранения:**
  - `MAP_VERSION = 2` в `core/models.py` автоматически вызывает полную пересборку устаревшей карты;
  - файлы состояния (`map.json`, `state.json`, `baseline.json`, `last-review.json`, `keys.txt`, кэши) записываются атомарно через временный файл + `os.fsync` + `os.replace`.
- **Инженерная гигиена:**
  - Python `>= 3.12` (целевой рантайм разработки — `3.14`), строгая типизация (`mypy src`), линтинг `ruff`, `from __future__ import annotations` во всех модулях;
  - весь пользовательский текст в Rich экранируется через `_s()` / `escape()`;
  - пользовательский интерфейс и документация на русском языке, символы кода и коммиты (Conventional Commits) — на английском.

---

## 4. Сборка, установка и дистрибуция

- **Разработка:** через `uv` (`uv sync --all-extras`), запуск проверок: `uv run ruff check src tests scripts`, `uv run mypy src`, `uv run pytest`, `uv run cr demo`.
- **Автономный бинарник (без Python у конечного пользователя):**
  - сборка через PyInstaller ([build_binary.py](file:///home/gargamel/vyatsu/code-rewiewer/scripts/build_binary.py)) с автоматическим `.sha256` и дымовыми тестами `demo` / `init` / `check --dry-run`;
  - кроссплатформенный CI/Release ([release.yml](file:///home/gargamel/vyatsu/code-rewiewer/.github/workflows/release.yml)) собирает релизы для Linux (`x86_64`), macOS (`arm64`) и Windows (`x86_64`) при пуше тега `v*`;
  - скрипты установки: [install.sh](file:///home/gargamel/vyatsu/code-rewiewer/install.sh) (установка в `/usr/local/bin/code-reviewer` + симлинк `cr`, атомарная замена и сверка SHA-256) и [install.ps1](file:///home/gargamel/vyatsu/code-rewiewer/install.ps1) для Windows.
