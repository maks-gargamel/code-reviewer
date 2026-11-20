# Статус проекта `code-reviewer`

Документ служит точкой быстрого входа для нового чата или разработчика: текущая готовность, результаты проверок и живых тестов, особенности окружения и открытый бэклог.

---

## 1. Текущее состояние и git-история

- **Версия пакета:** `0.1.0` ([pyproject.toml](pyproject.toml), [__init__.py](src/code_reviewer/__init__.py)) — **готовность функционала ~90–95%**.
- **Последний релизный тег на GitHub:** `v0.1.0` (`81b2f6a`) — сборка до добавления `keys`, фиксов 16 языков Tier B и `check --all`.
- **Ключевые коммиты на `main`:**
  - `81b2f6a` — упаковка в автономный бинарник PyInstaller, `install.sh`, `install.ps1`, GitHub Release workflow.
  - `4181a0d` — группа CLI-команд `keys` (`add`, `list`, `remove`, `clear`).
  - `69ec85b` — AST-дополнение [ts_ast.py](src/code_reviewer/indexing/parsers/ts_ast.py) для 16 языков Tier B, мульти-языковой [resolver.py](src/code_reviewer/indexing/resolver.py), исправление C# (`"csharp"`), добавление `.cxx`, `MAP_VERSION = 2`.
  - `230af96` — автоматическая точка отсчёта `last_reviewed_commit` в `init` и полный аудит репозитория `check --all` ([scan.py](src/code_reviewer/review/scan.py)).

---

## 2. Результаты проверок качества кода

Все проверки выполняются командой `export PATH="$HOME/.local/bin:$PATH"` и далее через `uv run`:

| Проверка | Результат |
|---|---|
| `uv run ruff check src tests scripts` | **OK** (`0` замечаний) |
| `uv run mypy src` | **OK** (`0` ошибок по `57` исходным файлам) |
| `uv run pytest -v` | **OK** (**`109 passed`** в `24` тестовых файлах) |
| Объём кодовой базы (`git ls-files`) | `57` файлов / `11 264` строк в `src/`; `24` файла / `3 147` строк в `tests/`; всего `15 778` строк без `uv.lock` |

### Масштабный бенчмарк языков (36 репозиториев)

Проверен на **18 синтетических** многофайловых репозиториях и **18 реальных open-source проектах** с GitHub (`click`, `express`, `zod`, `zustand`, `chi`, `anyhow`, `cJSON`, `spdlog`, `gson`, `clikt`, `Dapper`, `sinatra`, `monolog`, `Alamofire`, `Penlight`, `path`, `scopt` и сам `code-reviewer`):
- Загружены и работают все **16** грамматик Tier B `tree-sitter-language-pack` + `Python` Tier A + `PlainAdapter` Tier D.
- После фиксов `69ec85b`: **`0%` `<anonymous>` символов** и **`0` ошибок разбора** на всех 18 реальных проектах; построены внутренние рёбра графа и межфайловый контекст для Go, Rust, C#, Java, Kotlin, PHP, Ruby, C, C++, Lua, Dart, Scala (дополнительно к Python, JS, TS, TSX).

---

## 3. Результаты живых проверок (на текущем репозитории)

Конфигурация в локальном `.code-reviewer/config.toml`:
`base_url = "https://generativelanguage.googleapis.com/v1beta/openai"`, `model = "gemini-3.5-flash"`, в `.code-reviewer/keys.txt` добавлено **6** API-ключей.

1. **`uv run cr init`** (без LLM):
   - построена карта: **`117` файлов** за `213 мс`, **`771` символ**, **`314` внутренних связей**, `318` внешних;
   - автоматически зафиксирована точка отсчёта для `--since-last`: `230af96679`.
2. **`uv run cr doctor`**:
   - **`проблем: 0`**, предупреждений `2` (не установлен `pre-commit` хук и пуста история `check`), ключи обнаружены (`6 шт.`).
3. **`uv run cr check --all --no-tests --dry-run`** (без LLM):
   - **`61` кодовый файл**, **`16` чанков**, `~124 674` токенов, оценка: **`16` запросов к API** (ключа: `6`).
4. **Живой прогон бенчмарка качества (`evals/`, 22 кейса):**
   - Одиночный тест `uv run cr eval --max-cases 1` (`bare_except`): `TP=1, FN=0, FP=0`, `precision 100%`, `recall 100%`, `39.61 с`.
   - Полный прогон `uv run cr eval --jobs 3` (22 кейса): **`precision 100%` · `recall 91%` · `F1 95%`**, запросов `19` (из кэша `1`).
   - Все **20/20** ответивших кейсов дали `100% precision / 100% recall / 0 FP`; оставшиеся 2 кейса (`dict_keyerror`, `float_equality`) завершились по сетевому таймауту чтения (`The read operation timed out`).

---

## 4. Локальное окружение разработки

- **Менеджер пакетов и Python:** `uv` установлен в `~/.local/bin/uv` (не всегда в дефолтном `$PATH` новых shell-сессий — использовать `export PATH="$HOME/.local/bin:$PATH"`). Целевой рантайм в `.venv` — Python `3.14.10`.
- **Системные пакеты:** установлены `git`, `curl`, `graphviz`, `binutils` (`objdump` нужен для локальной сборки PyInstaller).
- **Служебные каталоги (в `.gitignore`, не коммитить):**
  - `.code-reviewer/` — локальные `keys.txt` (`chmod 600`), `config.toml`, `map.json`, `state.json`, `eval-cache/`;
  - `dist/` — локально собранный автономный бинарник (остался от `v0.1.0`; после изменений `keys`/`ts_ast`/`scan` для локального использования требует пересборки).
- **Вне проекта (только для истории, не трогать):** в `/home/gargamel/Music` лежит старая архивная версия; актуальная чистая реализация находится только в `/home/gargamel/vyatsu/code-rewiewer`.

---

## 5. Открытый бэклог и следующие шаги

1. **Выпуск релиза `v0.2.0`:** обновить версию до `0.2.0` в [pyproject.toml](pyproject.toml) и [__init__.py](src/code_reviewer/__init__.py), пересобрать локальный бинарник (`uv run python scripts/build_binary.py --checksum`), создать и запушить тег `v0.2.0` — GitHub Actions соберёт и опубликует бинарники под Linux, macOS и Windows с `keys`, фиксами 16 языков и `check --all`.
2. **Допрогон 2 таймаутных кейсов `eval` (по желанию):** `20/22` уже лежат в `.code-reviewer/eval-cache/`, поэтому повторный `uv run cr eval` выполнит только `dict_keyerror` и `float_equality` (~2 запроса).
3. **Собственный аудит и хук (по желанию):**
   - `uv run cr check --all --no-tests` — прогнать аудит всего кода `code-reviewer` (16 запросов при холодном кэше);
   - `uv run cr install-hook` — включить автоматическую проверку staged-изменений перед каждым `git commit`.
4. **Дальнейшие улучшения качества (идеи):**
   - добавить JSON/YAML/TOML-кейсы или кейсы на доп. языки (Rust/C++/Kotlin/C#) в `evals/`;
   - при работе с медленными thinking/flash моделями увеличить `timeout` в дефолтном `config.toml` с `90.0` (или `120–180` с), если таймауты на отдельных файлах повторятся.
