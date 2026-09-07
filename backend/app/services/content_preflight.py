"""Deterministic release checks for task-bank content.

The LLM validation pipeline proves subject correctness.  This module covers a
different boundary: whether the exact artifact about to leave Studio is
complete, portable and renderable as a standalone student/exam card.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable
from pathlib import PurePosixPath


REVIEW_TOKEN_VERSION = "studio-review-v1"
REVIEW_TOKEN_MAX_AGE_SECONDS = 60 * 60

_IMAGE_REFERENCE_RE = re.compile(
    r"\b(?:на|по|согласно)\s+(?:рисунк\w*|рис\.?|схем\w*|диаграмм\w*|график\w*)\b|"
    r"\b(?:рис\.?|рисунок|схема|диаграмма)\s*№?\s*\d+",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
_EXTERNAL_REFERENCE_RE = re.compile(
    r"\bсм\.?\s+(?:пояснен\w*|ответ\w*|решен\w*|задач\w*|пример\w*)\s*(?:к|в)?\s*№?\s*\d+(?:\.\d+)*|"
    r"\b(?:используя|по|согласно)\s+(?:данн\w*\s+)?(?:приложени\w*|таблиц\w*|справочник\w*)\s*№?\s*\d+|"
    r"\b(?:как|согласно)\s+(?:указано|показано|описано)\s+(?:выше|ниже|ранее)|"
    r"\bпредыдущ(?:ая|ей|ем|ую)\s+(?:задач\w*|пункт\w*|раздел\w*)",
    re.IGNORECASE,
)
_SUBPART_RE = re.compile(r"(?:^|[;,\n]\s*)(?:\d+[.)]|[а-яa-z][.)])\s+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w])([+\-−–—]?\s*\d+(?:[.,]\d+)?(?:[eE][+\-]?\d+)?)")
_DANGEROUS_IMAGE_SCHEMES = ("data:", "javascript:", "file:")
_IMAGE_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[a-z]:[\\/]", re.IGNORECASE)


def _issue(
    severity: str,
    code: str,
    title: str,
    message: str,
    *,
    task: object | None = None,
    field: str = "",
) -> dict:
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "message": message,
        "task_id": str(getattr(task, "id", "") or ""),
        "task_label": str(getattr(task, "topic", "") or "Без темы"),
        "field": field,
    }


def normalize_task_images(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _formula_issues(text: str, *, task: object, field: str) -> list[dict]:
    issues: list[dict] = []
    escaped = re.sub(r"\\\$", "", text)
    display_dollars = escaped.count("$$")
    inline_source = escaped.replace("$$", "")
    if display_dollars % 2 or inline_source.count("$") % 2:
        issues.append(
            _issue(
                "blocker",
                "formula_delimiter",
                "Формула обрывается",
                f"В поле «{field}» не закрыт разделитель $…$ или $$…$$.",
                task=task,
                field=field,
            )
        )
    for opening, closing in ((r"\(", r"\)"), (r"\[", r"\]")):
        if text.count(opening) != text.count(closing):
            issues.append(
                _issue(
                    "blocker",
                    "formula_delimiter",
                    "Формула обрывается",
                    f"В поле «{field}» не закрыт LaTeX-разделитель {opening}…{closing}.",
                    task=task,
                    field=field,
                )
            )
    # A common converter regression: a fraction command survives but loses one
    # of its braced operands.  Full TeX parsing belongs to the UI/KaTeX preview;
    # this catches the destructive form before release.
    if re.search(r"\\frac(?!\s*\{)", text) or re.search(r"\\frac\s*\{[^{}]*}(?!\s*\{)", text):
        issues.append(
            _issue(
                "blocker",
                "formula_fraction",
                "Повреждена дробь",
                f"В поле «{field}» команда \\frac не содержит два явных аргумента в фигурных скобках.",
                task=task,
                field=field,
            )
        )
    return issues


def _signed_numbers(text: str) -> dict[float, set[int]]:
    result: dict[float, set[int]] = {}
    for match in _NUMBER_RE.finditer(text.replace("−", "-").replace("–", "-").replace("—", "-")):
        token = re.sub(r"\s+", "", match.group(1)).replace(",", ".")
        try:
            value = float(token)
        except ValueError:
            continue
        if value == 0:
            continue
        result.setdefault(round(abs(value), 10), set()).add(-1 if value < 0 else 1)
    return result


def _image_path_issue(path: str, *, task: object) -> dict | None:
    lower = path.casefold()
    if lower.startswith(_DANGEROUS_IMAGE_SCHEMES):
        return _issue(
            "blocker",
            "image_unsafe",
            "Небезопасная ссылка на изображение",
            f"«{path}» использует запрещённую схему.",
            task=task,
            field="images",
        )
    if lower.startswith(("http://", "https://")):
        return _issue(
            "blocker",
            "image_not_portable",
            "Изображение не войдёт в банк",
            "Picrete импортирует файлы по относительным путям, а не внешние URL. Перенесите изображение в пакет банка.",
            task=task,
            field="images",
        )
    if _WINDOWS_ABSOLUTE_RE.match(path):
        return _issue(
            "blocker",
            "image_path",
            "Непереносимый путь к изображению",
            "Используйте относительный путь внутри пакета банка без «..».",
            task=task,
            field="images",
        )
    if _IMAGE_SCHEME_RE.match(path):
        return _issue(
            "blocker",
            "image_not_portable",
            "Изображение не войдёт в банк",
            f"«{path}» использует внешний URI. Перенесите изображение в пакет банка.",
            task=task,
            field="images",
        )
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        return _issue(
            "blocker",
            "image_path",
            "Непереносимый путь к изображению",
            "Используйте относительный путь внутри пакета банка без «..».",
            task=task,
            field="images",
        )
    if pure.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return _issue(
            "warning",
            "image_format",
            "Необычный формат изображения",
            f"Проверьте, что Picrete сможет открыть «{path}».",
            task=task,
            field="images",
        )
    return None


def _markdown_image_paths(statement: str) -> list[str]:
    """Return CommonMark image destinations without an optional title.

    Spaces in a destination are valid only in ``<angle brackets>``; an
    unbracketed remainder is the optional title and must not become part of the
    path checked against ``images``.
    """
    paths: list[str] = []
    for raw_destination in _MARKDOWN_IMAGE_RE.findall(statement):
        destination = raw_destination.strip()
        if destination.startswith("<") and ">" in destination:
            destination = destination[1 : destination.index(">")]
        else:
            destination = destination.split(maxsplit=1)[0]
        if destination:
            paths.append(destination)
    return paths


def inspect_task(task: object) -> list[dict]:
    statement = str(getattr(task, "statement", "") or "").strip()
    solution = str(getattr(task, "reference_solution", "") or "").strip()
    answer = str(getattr(task, "answer", "") or "").strip()
    rubric = getattr(task, "rubric", None)
    rubric = rubric if isinstance(rubric, list) else []
    images = normalize_task_images(getattr(task, "images", []))
    issues: list[dict] = []

    if not statement:
        issues.append(_issue("blocker", "statement_empty", "Нет условия", "Карточка не содержит вопроса.", task=task, field="statement"))
    if len(solution) < 20:
        issues.append(
            _issue(
                "blocker",
                "solution_incomplete",
                "Эталонное решение слишком короткое",
                "Без полного хода решения нельзя проверить знак, формулу и согласованность ответа.",
                task=task,
                field="reference_solution",
            )
        )
    if not answer:
        issues.append(_issue("blocker", "answer_empty", "Нет ответа", "Краткий финальный ответ пуст.", task=task, field="answer"))

    if _EXTERNAL_REFERENCE_RE.search("\n".join((statement, solution, answer))):
        issues.append(
            _issue(
                "blocker",
                "external_reference",
                "Карточка не самодостаточна",
                "Есть ссылка на другой номер или контекст «выше/ниже», которого студент не увидит.",
                task=task,
                field="content",
            )
        )

    markdown_images = _markdown_image_paths(statement)
    references_image = bool(_IMAGE_REFERENCE_RE.search(statement) or markdown_images)
    if references_image and not images:
        issues.append(
            _issue(
                "blocker",
                "image_missing",
                "Упомянут рисунок, но файл не приложен",
                "Добавьте относительный путь к каждому обязательному изображению и проверьте превью.",
                task=task,
                field="images",
            )
        )
    if markdown_images:
        issues.append(
            _issue(
                "warning",
                "markdown_image",
                "Изображение встроено в текст",
                "Picrete использует отдельный список images. Убедитесь, что те же файлы перечислены в поле изображений.",
                task=task,
                field="statement",
            )
        )
        normalized_images = {path.replace("\\", "/") for path in images}
        for raw_path in markdown_images:
            markdown_path = raw_path.strip()
            path_issue = _image_path_issue(markdown_path, task=task)
            if path_issue:
                issues.append(path_issue)
                continue
            if markdown_path.replace("\\", "/") not in normalized_images:
                issues.append(
                    _issue(
                        "blocker",
                        "markdown_image_unbound",
                        "Изображение из текста не войдёт в экспорт",
                        f"Добавьте «{markdown_path}» в отдельное поле images — только оно формирует пакет банка.",
                        task=task,
                        field="images",
                    )
                )
    for path in images:
        path_issue = _image_path_issue(path, task=task)
        if path_issue:
            issues.append(path_issue)

    for field, text in (("условие", statement), ("решение", solution), ("ответ", answer)):
        issues.extend(_formula_issues(text, task=task, field=field))

    if answer and solution:
        solution_numbers = _signed_numbers(solution)
        answer_numbers = _signed_numbers(answer)
        for absolute, answer_signs in answer_numbers.items():
            solution_signs = solution_numbers.get(absolute)
            if solution_signs and answer_signs.isdisjoint(solution_signs):
                issues.append(
                    _issue(
                        "blocker",
                        "answer_sign_mismatch",
                        "В ответе изменился знак",
                        f"Число {absolute:g} встречается в решении и ответе с противоположными знаками.",
                        task=task,
                        field="answer",
                    )
                )
                break
        if answer_numbers and not any(absolute in solution_numbers for absolute in answer_numbers):
            issues.append(
                _issue(
                    "warning",
                    "answer_not_in_solution",
                    "Финальное число не найдено в решении",
                    "Проверьте, что answer относится к этому условию и явно завершает эталонное решение.",
                    task=task,
                    field="answer",
                )
            )

    statement_parts = len(_SUBPART_RE.findall(statement))
    answer_parts = len(_SUBPART_RE.findall(answer))
    if statement_parts >= 2 and answer_parts < statement_parts:
        issues.append(
            _issue(
                "warning",
                "compound_split",
                "Составная задача могла быть разрезана",
                f"В условии найдено подпунктов: {statement_parts}, в кратком ответе: {answer_parts}. Сверьте границы и порядок.",
                task=task,
                field="answer",
            )
        )

    if not rubric:
        issues.append(_issue("blocker", "rubric_empty", "Нет рубрики", "Задачу нельзя воспроизводимо оценить.", task=task, field="rubric"))
    else:
        try:
            rubric_total = sum(float(item.get("max_score", 0)) for item in rubric if isinstance(item, dict))
            max_score = float(getattr(task, "max_score", 0))
        except (TypeError, ValueError):
            rubric_total = -1
            max_score = 0
        if max_score <= 0 or abs(rubric_total - max_score) > 1e-6:
            issues.append(
                _issue(
                    "blocker",
                    "rubric_total",
                    "Баллы рубрики не сходятся",
                    f"Сумма критериев {rubric_total:g}, максимум задачи {max_score:g}.",
                    task=task,
                    field="rubric",
                )
            )

    if not str(getattr(task, "topic", "") or "").strip():
        issues.append(
            _issue("warning", "topic_empty", "Не указана тема", "В банке задача попадёт в раздел «Без темы».", task=task, field="topic")
        )
    return issues


def task_release_digest(tasks: Iterable[object], mode: str) -> str:
    payload = {
        "mode": mode,
        "tasks": [
            {
                "id": str(getattr(task, "id", "") or ""),
                "statement": str(getattr(task, "statement", "") or ""),
                "reference_solution": str(getattr(task, "reference_solution", "") or ""),
                "answer": str(getattr(task, "answer", "") or ""),
                "rubric": getattr(task, "rubric", []),
                "max_score": getattr(task, "max_score", 0),
                "topic": str(getattr(task, "topic", "") or ""),
                "images": normalize_task_images(getattr(task, "images", [])),
            }
            for task in tasks
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_review_token(*, scope: str, digest: str, secret_key: str, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    message = f"{REVIEW_TOKEN_VERSION}:{scope}:{digest}:{issued_at}"
    signature = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{issued_at}.{signature}"


def verify_review_token(
    token: str,
    *,
    scope: str,
    digest: str,
    secret_key: str,
    now: int | None = None,
    max_age_seconds: int = REVIEW_TOKEN_MAX_AGE_SECONDS,
) -> bool:
    try:
        issued_raw, supplied = token.split(".", 1)
        issued_at = int(issued_raw)
    except (AttributeError, TypeError, ValueError):
        return False
    current = int(time.time() if now is None else now)
    if issued_at > current + 30 or current - issued_at > max_age_seconds:
        return False
    expected = create_review_token(scope=scope, digest=digest, secret_key=secret_key, now=issued_at).split(".", 1)[1]
    return hmac.compare_digest(supplied, expected)


def build_task_preflight(tasks: list[object], *, mode: str, secret_key: str) -> dict:
    issues = [issue for task in tasks for issue in inspect_task(task)]
    blockers = [issue for issue in issues if issue["severity"] == "blocker"]
    warnings = [issue for issue in issues if issue["severity"] == "warning"]
    digest = task_release_digest(tasks, mode)
    scope = f"task-export:{mode}"
    return {
        "ok": not blockers,
        "summary": {
            "tasks": len(tasks),
            "images": sum(len(normalize_task_images(getattr(task, "images", []))) for task in tasks),
            "blockers": len(blockers),
            "warnings": len(warnings),
        },
        "blockers": blockers,
        "warnings": warnings,
        "review_token": create_review_token(scope=scope, digest=digest, secret_key=secret_key) if not blockers else "",
        "digest": digest,
    }
