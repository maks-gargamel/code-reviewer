# Справочник CLI `code-reviewer` (`cr`)

Обе точки входа (`code-reviewer` и `cr`) идентичны.

## Коды возврата

| Код | Значение |
|---|---|
| `0` | Успешное выполнение (или находки ниже порога `output.fail_on`) |
| `1` | Найдены замечания `>= output.fail_on`, `eval` ниже `--fail-under` или `doctor` выявил критическую ошибку (`FAIL`) |
| `2` | Ошибка аргументов, конфигурации, git, отсутствующей карты или LLM API |

## Основные команды

### `init` и `status`

```bash
code-reviewer init [--root DIR] [--rebuild] [--ai-summary] [--summary-limit N] [--jobs N]
code-reviewer status [--root DIR] [--json]
```

- `init` строит или инкрементально обновляет `.code-reviewer/map.json`, добавляет `.code-reviewer/` в `.gitignore` и при наличии коммитов автоматически фиксирует первый `HEAD` как точку отсчёта для `--since-last`. Без `--ai-summary` модель не вызывается.
- `status` показывает текущую ветку, `HEAD`, статистику карты, список устаревших файлов и время последнего ревью.

### `check` и `mark-reviewed`

```bash
code-reviewer check [--root DIR]
                    [--all [-p|--path PATH]... [--no-tests] [-y|--yes]
                     | --last | --since-last | --commit REF | --range A..B]
                    [--dry-run] [--format table|json|md|sarif] [--output FILE] [--json]
                    [--no-cache] [--no-ignore] [--baseline | --save-baseline] [--verify]
                    [--max-files N] [--max-chunks N] [--jobs N] [--quiet]
code-reviewer mark-reviewed [--root DIR]
```

- Без указания области проверки берутся staged-изменения (если они есть), иначе working tree + новые (untracked) файлы.
- `--all` сканирует весь репозиторий по карте (пропуская документацию/конфиги без явного `-p`), с `--dry-run` заранее показывает таблицу чанков, попадания в кэш и число запросов к API, а при `> 10` некэшированных запросах к удалённому API требует подтверждения (или `-y` / `--max-chunks`).
- `--dry-run` показывает собранные system/user промпты (или план `--all`) и оценку токенов без сетевых вызовов.
- `--quiet` подавляет служебный вывод и выводит результат только при наличии замечаний (удобно для pre-commit хука).
- `mark-reviewed` фиксирует текущий `HEAD` как `last_reviewed_commit` без обращения к модели.

### `map` — навигация по карте и графу

```bash
code-reviewer map deps PATH [--depth N] [--root DIR]
code-reviewer map files [--lang LANG] [--limit N] [--root DIR]
code-reviewer map outline PATH [--root DIR]
code-reviewer map cycles [--root DIR]
code-reviewer map search QUERY [--limit N] [--root DIR]
code-reviewer map export [--format dot] [--output FILE] [--focus PATH] [--depth N] [--root DIR]
```

### `demo` и `eval`

```bash
code-reviewer demo
code-reviewer eval [--root DIR] [--cases DIR] [--tolerance N] [--max-cases N] [--jobs N] [--fail-under F] [--no-cache] [--json]
```

- `demo` запускает пайплайн на встроенном примере с SQL-инъекцией и записанным ответом (без ключа, сети и `git`).
- `eval` прогоняет кейсы из `./evals` (или `--cases`) и считает micro-average `precision`, `recall`, `F1`.

### `keys` — управление API-ключами

```bash
code-reviewer keys add [KEY] [-c|--comment TEXT] [--stdin] [--root DIR]
code-reviewer keys list [--root DIR] [--json]
code-reviewer keys remove INDEX [--root DIR]
code-reviewer keys clear [--root DIR]
```

- `keys add` без аргумента запрашивает ключ скрытым вводом (не остаётся в истории shell); флаг `--stdin` читает ключ из стандартного ввода.
- Файл `.code-reviewer/keys.txt` записывается атомарно с правами `0600`, а `.code-reviewer/` при необходимости добавляется в `.gitignore`.
- `keys list` и `keys list --json` отображают ключи только в маскированном виде (исходные значения никогда не печатаются).

### Обслуживание, конфиг и CI

```bash
code-reviewer config show [--root DIR] [--json]
code-reviewer config check [--root DIR]
code-reviewer config init [--root DIR] [--force]
code-reviewer history [--root DIR] [--limit N] [--json]
code-reviewer cache [--root DIR] [--clean] [--json]
code-reviewer install-hook [--root DIR] [--force] [--uninstall]
code-reviewer doctor [--root DIR]
```
