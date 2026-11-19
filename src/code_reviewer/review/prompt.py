"""Сборка промпта для код-ревью с учётом языка (ru/en) и строгости чеклиста."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import Config
from ..core.models import ReviewLanguage, Strictness
from ..vcs.diff import DiffSet
from .context import ReviewContext

CATEGORIES = (
    "bug, logic, security, performance, api-contract, "
    "error-handling, tests, maintainability"
)

CHECKLISTS: dict[str, dict[str, str]] = {
    ReviewLanguage.RU: {
        Strictness.JUNIOR: (
            "Ищи в первую очередь явные дефекты: необработанные исключения, "
            "неверные условия, None/пустые значения, забытые return, "
            "ресурсы без закрытия, несоответствие типов."
        ),
        Strictness.MIDDLE: (
            "Ищи дефекты и риски: логические ошибки, неверную обработку ошибок, "
            "нарушения контрактов между модулями, побочные эффекты для вызывающего "
            "кода, проблемы с ресурсами и безопасностью, отсутствие проверок на "
            "граничные случаи."
        ),
        Strictness.SENIOR: (
            "Ищи дефекты, риски и архитектурные последствия: логические ошибки, "
            "нарушения инвариантов и контрактов между модулями, побочные эффекты "
            "для вызывающего кода, проблемы конкурентности, обратную совместимость "
            "публичного API, утечки ресурсов, безопасность, отсутствие тестов на "
            "новое поведение."
        ),
    },
    ReviewLanguage.EN: {
        Strictness.JUNIOR: (
            "Focus primarily on clear defects: unhandled exceptions, wrong conditions, "
            "null/empty dereferences, missing return statements, unclosed resources, "
            "and type mismatches."
        ),
        Strictness.MIDDLE: (
            "Look for defects and risks: logic bugs, broken error handling, cross-module "
            "contract violations, unexpected side effects, resource leaks, security "
            "issues, and missing boundary checks."
        ),
        Strictness.SENIOR: (
            "Look for defects, invariants, and architectural risks: logic errors, "
            "contract violations, caller side effects, concurrency hazards, backwards "
            "compatibility breaks, resource leaks, security vulnerabilities, and "
            "missing test coverage for new behavior."
        ),
    },
}

SYSTEM_TEMPLATES: dict[str, str] = {
    ReviewLanguage.RU: """\
Ты — опытный разработчик, который ревьюит изменения в репозитории.
Тебе переданы git diff и сигнатуры связанных файлов из карты репозитория.

Правила:
1. Комментируй только реальные проблемы в изменённых строках.
2. Не комментируй стиль, форматирование и то, что ловят линтеры.
3. Если проблема видна только в связке с другим файлом — укажи это в message.
4. Ссылайся только на файлы и строки, присутствующие в diff. Не выдумывай.
5. severity: error — сломает поведение или безопасность; warning — вероятный дефект;
   info — важное замечание. Категория — одна из: {categories}.
6. Не более {max_findings} замечаний. Если проблем нет — верни {{"findings": []}}.
7. Ответ — только валидный JSON-объект без markdown и пояснений:
   {{"findings": [{{"file": "путь/из/diff.py", "line": 42, "severity": "warning",
   "category": "logic", "message": "что не так и почему", "suggestion": "как исправить"}}]}}
8. Пиши по-русски, кратко и по делу.

Чеклист проекта: {checklist}
""",
    ReviewLanguage.EN: """\
You are an experienced software engineer reviewing changes in a repository.
You are given a git diff and signatures of related files from the repository map.

Rules:
1. Comment only on real issues in the modified lines.
2. Do not comment on formatting, naming style, or things caught by linters.
3. If an issue arises from interaction with another module, explain that in message.
4. Reference only files and line numbers present in the diff. Do not fabricate paths.
5. severity: error (breaks behavior/security), warning (likely defect), info (note).
   category must be one of: {categories}.
6. At most {max_findings} findings. If there are no issues, return {{"findings": []}}.
7. Reply strictly with a valid JSON object without markdown fences or prose:
   {{"findings": [{{"file": "path/from/diff.py", "line": 42, "severity": "warning",
   "category": "logic", "message": "what is wrong and why", "suggestion": "how to fix"}}]}}
8. Write concisely in English.

Review checklist: {checklist}
""",
}

USER_TEMPLATES: dict[str, str] = {
    ReviewLanguage.RU: """\
## Проект
{overview}

## Изменённые файлы ({count})
{files}

## Diff
{diff}

## Контекст: связанные файлы (только сигнатуры)
{context}

## Задача
Найди проблемы в изменениях с учётом контекста. Верни JSON-объект вида {{"findings": [...]}}.
""",
    ReviewLanguage.EN: """\
## Project
{overview}

## Changed files ({count})
{files}

## Diff
{diff}

## Context: related files (signatures only)
{context}

## Task
Find real issues in the changes considering the context. Return JSON object {{"findings": [...]}}.
""",
}

MAX_DIFF_CHARS = 40_000


@dataclass(slots=True)
class PromptBundle:
    system: str
    user: str

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.system) + estimate_tokens(self.user)

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


def estimate_tokens(text: str) -> int:
    """Грубая оценка числа токенов (~4 символа на токен)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def build_prompt(config: Config, diffset: DiffSet, context: ReviewContext) -> PromptBundle:
    lang = str(config.review.language)
    if lang not in SYSTEM_TEMPLATES:
        lang = ReviewLanguage.RU

    strictness = str(config.review.strictness)
    lang_checklists = CHECKLISTS[lang]
    checklist = lang_checklists.get(strictness, lang_checklists[Strictness.MIDDLE])

    system = SYSTEM_TEMPLATES[lang].format(
        categories=CATEGORIES,
        max_findings=config.review.max_findings,
        checklist=checklist,
    )

    file_lines = [
        f"- {item.path} ({item.status}, +{item.additions}/-{item.deletions})"
        for item in diffset.files
    ]
    if diffset.omitted:
        omitted_note = (
            f"- … ещё {len(diffset.omitted)} файлов не вошли в лимит и не проверялись"
            if lang == ReviewLanguage.RU
            else f"- ... {len(diffset.omitted)} more files exceeded limits and were not reviewed"
        )
        file_lines.append(omitted_note)

    diff_text = "\n\n".join(item.patch for item in diffset.files)
    if len(diff_text) > MAX_DIFF_CHARS:
        suffix = (
            "\n… (diff обрезан по размеру)"
            if lang == ReviewLanguage.RU
            else "\n... (diff truncated by size)"
        )
        diff_text = diff_text[:MAX_DIFF_CHARS] + suffix

    user = USER_TEMPLATES[lang].format(
        overview=context.overview or "—",
        count=len(diffset.files),
        files="\n".join(file_lines) or "—",
        diff=diff_text,
        context=context.related or "—",
    )
    return PromptBundle(system=system, user=user)
