import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.llm import client as llm
from app.models import (
    Assistant,
    GeneratedTask,
    GenerationBatch,
    KnowledgeDocument,
    ModelEntry,
    PromptVersion,
    Provider,
    ReferenceSheet,
    TaskTemplate,
    utcnow,
)
from app.services.assistant_profile import build_assistant_profile, with_assistant_profile
from app.services.chemistry_facts import FACT_BLOCK_BY_CHECK, normalize_chemistry_facts
from app.services.contracts import (
    CHEMISTRY_FACTS_GUIDE, GENERATION_JSON_CONTRACT, JSON_LATEX_ESCAPING_NOTE,
    PHYSICAL_GENERATION_JSON_EXAMPLE,
    PHYSICAL_GENERATION_RESPONSE_SCHEMA,
    ESSENTIAL_TOOLS_INSTRUCTION,
)
from app.services.grounding import AUTHORITY_LABELS, KB_HEADER, build_grounding_block
from app.services.physical_chemistry import (
    uses_single_verifier,
    run_physical_validation,
    assistant_tools_enabled,
    task_verifier_model_id,
    physical_json_schema_enabled,
)
from app.services.task_approval import task_is_export_ready
from app.services.task_evidence import evidence_matches_task, normalize_validation_config, task_content_fingerprint
from app.services.validation import run_validation
from app.services.model_policy import require_decision_model

FALLBACK_GENERATOR_PROMPT = """Вы — опытный преподаватель и методист высшей школы по дисциплине «{discipline}».
Вы составляете типовые учебные задания: условие, подробное эталонное решение, краткий финальный ответ (answer)
и рубрику оценивания. Задания должны быть корректными, решаемыми, с реалистичными числами и согласованными
единицами измерения.
Если в сообщении приведены справочные материалы курса — табличные величины берите ТОЛЬКО из них и перечисляйте
использованные значения в поле data_used. В data_used указывайте только реально существующий заголовок справочного
листа и дословно взятые из него значения. Копируйте в sheet_title всю строку после `###` без сокращений:
название раздела вроде «ЛЕКЦИЯ 6» не является самостоятельным источником. Числа, которые вы сами задаёте в самодостаточном условии, не являются
справочными данными: не добавляйте их в data_used; если справочники не использованы, верните data_used: [].
Запрещено подставлять справочные значения из общих знаний: если нужных
данных нет, стройте задачу на тех данных, которые приведены, либо задавайте недостающие величины прямо в условии.
Формулы записывайте в LaTeX ($...$). Отвечайте только на русском языке.

Ответ — строго JSON по схеме:
{contract}
Никакого текста вне JSON."""

REPAIR_GENERATOR_APPENDIX = """

РЕЖИМ ИСПРАВЛЕНИЯ УЖЕ СГЕНЕРИРОВАННОЙ ЗАДАЧИ
Вход содержит исходную задачу и отчёт независимой проверки. Не создавайте другую задачу
вместо исправления. Сохраните тему и учебный замысел, исправьте только найденные противоречия:
условие, числа, единицы, формулы, эталонное решение и answer должны стать взаимно согласованными.
Если verifier указал на ошибку, перепроверьте её самостоятельно; не переносите ошибку verifier
в исправленный вариант. Для Михаэлиса–Ментен значения [S]/K_M порядка 0,1 и 10 — переходная
область, а не строгие пределы; меняйте данные только если это необходимо для однозначного условия.
Верните ровно один объект tasks с исправленной версией исходной задачи в том же JSON-контракте.
Не добавляйте внешние константы и не удаляйте подпункты. Перед ответом пересчитайте все числа.
""".strip()

# Задачи с объёмным LaTeX-решением не помещаются по несколько в один JSON — генерируем порциями.
GENERATION_CHUNK = 1
# Дополнительные запросы сверх минимально необходимого числа порций. Они восполняют
# недостающие/невалидные элементы, но не дают фоновой задаче зациклиться на плохом ответе модели.
MAX_REFILL_ATTEMPTS = 3


@dataclass(slots=True)
class _GenerationCallBudget:
    """A single paid-call budget shared by the initial request and every refill wave."""

    limit: int
    used: int = 0

    def claim(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def _generation_call_limit(candidate_budget: int) -> int:
    """Bound provider calls needed to fill the entire candidate budget, including retries."""

    minimum_calls = (candidate_budget + GENERATION_CHUNK - 1) // GENERATION_CHUNK
    return minimum_calls + MAX_REFILL_ATTEMPTS


TASK_KIND_LABELS = {
    "calculation": "расчётная задача",
    "conceptual": "теоретический вопрос",
    "test_tf": "тест «верно/неверно»",
    "test_mc": "тест с выбором ответа",
    "derivation": "вывод формулы",
}

ANSWER_FORMAT_LABELS = {
    "numeric": "число с единицами измерения",
    "formula": "формула",
    "text": "краткий текст",
    "choice": "выбранный вариант ответа",
}


class GenerationError(Exception):
    pass


def _render_example_tasks(example_tasks: list[dict]) -> str:
    blocks: list[str] = []
    for index, example in enumerate(example_tasks, start=1):
        if not isinstance(example, dict) or not example.get("statement"):
            continue
        source = f" (Свиридов № {example['source_number']})" if example.get("source_number") else ""
        lines = [f"Пример {index}{source}.", f"Условие: {example['statement']}"]
        if example.get("source_image_ids"):
            lines.append("У исходного примера есть рисунок, не переданный в текстовый контекст. Не восстанавливайте его данные по догадке; для аналога задайте все уровни/зависимости явно текстом или таблицей.")
        if example.get("solution"):
            lines.append(f"Решение: {example['solution']}")
        if example.get("answer"):
            lines.append(f"Ответ: {example['answer']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_generation_user_message(
    *,
    topic: str,
    difficulty: str,
    count: int,
    task_kind: str = "calculation",
    answer_format: str = "numeric",
    instructions: str = "",
    grounding: str = "",
    rubric: list[dict] | None = None,
    example_tasks: list[dict] | None = None,
    existing_statements: list[str] | None = None,
    chemistry_check: str = "auto",
    reuse_blueprint: bool = False,
) -> str:
    examples = _render_example_tasks(list(example_tasks or []))
    existing = "\n---\n".join((existing_statements or [])[:8])
    sections = [
        f"Сгенерируйте {count} задач(и).",
        f"""Тема: {topic or "(на усмотрение, в рамках дисциплины)"}
Сложность: {difficulty}
Вид задания: {TASK_KIND_LABELS.get(task_kind, task_kind)}
Формат ответа: {ANSWER_FORMAT_LABELS.get(answer_format, answer_format)}""",
    ]
    if grounding:
        sections.append(grounding)
    if rubric:
        sections.append(
            "Рубрика преподавателя (обязательный контракт):\n"
            f"{json.dumps(rubric, ensure_ascii=False, indent=2)}\n"
            "Для каждой задачи верните rubric с ТОЧНО теми же criterion_name, max_score и description, "
            "в том же порядке. Не добавляйте, не удаляйте, не переименовывайте и не перераспределяйте критерии. "
            "Поле max_score задачи должно быть равно 10."
        )
    sections.append(f"Инструкции преподавателя:\n{instructions or '(нет)'}")
    sections.append(f"Примеры задач в нужном стиле:\n{examples or '(нет)'}")
    existing_rule = (
        "Повторное использование сюжета, физической модели и структуры выбранного blueprint разрешено. "
        "Сохраняйте учебный замысел и меняйте числовые исходные данные в допустимых диапазонах blueprint. "
        "Не повторяйте целиком набор числовых исходных данных существующей задачи; "
        "совпадение отдельных констант допустимо. Пересчитайте решение и ответ."
        if reuse_blueprint else "НЕ повторяйте их сюжеты и числа"
    )
    sections.append(f"Уже существующие задачи ({existing_rule}):\n{existing or '(нет)'}")
    output_contract = (
        "Корневой объект обязан содержать непустой массив tasks с запрошенным количеством задач. "
        "Пустой объект {} и отдельная задача без tasks не являются ответом. "
        "У каждой задачи обязательны все поля примера; chemistry_facts всегда {}.\n"
        "Ниже полный валидный JSON-пример формата. Его сюжет, числа, сложность и рубрика показаны "
        "только для иллюстрации формата: используйте выбранный blueprint, запрошенную сложность "
        "и рубрику преподавателя.\n"
        f"{PHYSICAL_GENERATION_JSON_EXAMPLE}"
        if reuse_blueprint else GENERATION_JSON_CONTRACT
    )
    evidence_line = (
        "Верните chemistry_facts: {}: задача проверяется отдельным независимым verifier и не использует "
        "общий chemistry-facts классификатор."
        if chemistry_check == "off"
        else (
            "chemistry_facts для детерминированной перепроверки.\n"
            f"Предметная проверка: {chemistry_check}. Если указан конкретный тип вместо auto, "
            "соответствующий блок chemistry_facts обязателен и должен содержать полный набор величин.\n"
            f"{CHEMISTRY_FACTS_GUIDE}"
        )
    )
    # Keep the complete platform contract ahead of per-request data. Roles and
    # contract contents stay unchanged; no provider-specific cache flags needed.
    sections.insert(0,
        "Каждая задача: условие + подробное эталонное решение + краткий финальный ответ (answer) "
        "+ рубрика с баллами + список использованных справочных значений (data_used) + "
        f"{evidence_line}\n"
        "В data_used перечисляйте только значения, действительно скопированные из приложенного справочного листа, "
        "с его точным заголовком. Самостоятельно заданные числа условия туда не входят; если справочник не "
        "использован, верните data_used: [].\n\n"
        "Ответ — строго JSON по схеме (эта схема главнее любых других форматов):\n"
        f"{output_contract}\n{JSON_LATEX_ESCAPING_NOTE}"
    )
    return "\n\n".join(sections)


async def generate_tasks(
    provider: Provider,
    model: ModelEntry,
    assistant: Assistant,
    system_prompt: str | None,
    *,
    topic: str,
    difficulty: str,
    count: int,
    task_kind: str = "calculation",
    answer_format: str = "numeric",
    instructions: str = "",
    grounding: str = "",
    rubric: list[dict] | None = None,
    example_tasks: list[dict] | None = None,
    existing_statements: list[str] | None = None,
    temperature: float = 0.7,
    chemistry_check: str = "auto",
) -> list[dict]:
    physical = uses_single_verifier(assistant)
    if physical:
        chemistry_check = "off"
    prompt = system_prompt or FALLBACK_GENERATOR_PROMPT.format(
        discipline=assistant.discipline,
        contract=PHYSICAL_GENERATION_JSON_EXAMPLE if physical else GENERATION_JSON_CONTRACT,
    )
    prompt = with_assistant_profile(prompt, assistant)
    if getattr(assistant, "generation_policy", "legacy") == "single_verifier" and example_tasks:
        import secrets
        candidates = [e for e in example_tasks if isinstance(e, dict) and e.get("statement")]
        anchors = [e for e in candidates if e.get("generation_anchor") is True]
        example_tasks = [secrets.choice(anchors or candidates)] if candidates else []
    if getattr(assistant, "generation_policy", "legacy") == "single_verifier":
        # Recent tasks are not exemplars. Their full text made the model copy the
        # preceding blueprint instead of the selected one, and defeated prefix caching.
        existing_statements = []
    user_message = build_generation_user_message(
        topic=topic,
        difficulty=difficulty,
        count=count,
        task_kind=task_kind,
        answer_format=answer_format,
        instructions=instructions,
        grounding=grounding,
        rubric=rubric,
        example_tasks=example_tasks,
        existing_statements=existing_statements,
        chemistry_check=chemistry_check,
        reuse_blueprint=physical,
    )
    if getattr(assistant, "generation_policy", "legacy") == "single_verifier":
        prompt += (
            "\n\nКОНТРАКТ ТИПОВОГО ВАРИАНТА: выбранный опорный пример задаёт число вопросов, "
            "вещества и способ решения. Обязательные критерии оценивают эти вопросы, "
            "а не требуют дополнительных вопросов. Меняйте только независимые данные; "
            "константы и химическую модель сохраняйте. Перед выдачей сопоставьте вариант "
            "с опорным примером и устраните расширение задания в том же ответе."
        )
    use_tools = assistant_tools_enabled(assistant, "generator")
    if use_tools:
        prompt += ESSENTIAL_TOOLS_INSTRUCTION
    result = await llm.chat(
        provider,
        model,
        prompt,
        user_message,
        temperature=temperature,
        json_mode=True,
        **({"response_schema": PHYSICAL_GENERATION_RESPONSE_SCHEMA}
           if (use_tools and physical) or physical_json_schema_enabled(assistant, provider, model) else {}),
        **({"essential_tools": True, "initial_tool_choice": "required"}
           if use_tools else {}),
        **({"reasoning_effort": "high"}
           if getattr(assistant, "generation_policy", "legacy") == "single_verifier"
           and getattr(provider, "kind", "") == "deepseek" else {}),
    )
    parsed = llm.extract_json(result.text)
    tasks = _coerce_tasks(parsed)
    if tasks is None:
        raise llm.LlmError(f"Генератор не вернул массив tasks; начало ответа: {result.text[:180]}")
    for item in tasks:
        if isinstance(item, dict):
            if getattr(assistant, "generation_policy", "legacy") == "single_verifier":
                # Server-owned metadata and empty optional fields are not chemistry verdicts.
                item["topic"], item["difficulty"] = topic, difficulty
                item["_blueprint"] = {"instructions": instructions,
                    "source_number": (example_tasks or [{}])[0].get("source_number"),
                    "example_statement": (example_tasks or [{}])[0].get("statement", "")}
                for key, default in (("images", []), ("data_used", []), ("chemistry_facts", {}),
                                     ("rubric", rubric or []), ("max_score", 10)):
                    if item.get(key) is None:
                        item[key] = default
            # Never trust an audit claimed in generated JSON; only transport supplies it.
            item.pop("_calculation_audit", None)
            if use_tools:
                item["_calculation_audit"] = result.raw
    return tasks


async def repair_generated_task(
    provider: Provider,
    model: ModelEntry,
    assistant: Assistant,
    system_prompt: str | None,
    task: GeneratedTask,
    validation: dict,
) -> dict | None:
    """Ask the generator to repair a rejected candidate before making a replacement."""

    prompt = system_prompt or FALLBACK_GENERATOR_PROMPT.format(
        discipline=assistant.discipline, contract=GENERATION_JSON_CONTRACT
    )
    prompt = with_assistant_profile(f"{prompt.rstrip()}\n\n{REPAIR_GENERATOR_APPENDIX}", assistant)
    payload = {
        "task": {
            "statement": task.statement,
            "reference_solution": task.reference_solution,
            "answer": task.answer,
            "images": task.images or [],
            "rubric": task.rubric or [],
            "max_score": task.max_score,
            "difficulty": task.difficulty,
            "topic": task.topic,
            "data_used": (task.grounding or {}).get("data_used", []),
            "chemistry_facts": (task.grounding or {}).get("chemistry_facts", {}),
        },
        "validation": validation,
    }
    try:
        result = await llm.chat(
            provider,
            model,
            prompt,
            json.dumps(payload, ensure_ascii=False),
            temperature=0.2,
            json_mode=True,
        )
        parsed = llm.extract_json(result.text)
    except llm.LlmError:
        return None
    tasks = _coerce_tasks(parsed)
    return tasks[0] if tasks else None


def _coerce_tasks(parsed: dict) -> list | None:
    """Модели иногда меняют обёртку: одна задача без списка, иной ключ вместо tasks — принимаем и это."""
    tasks = parsed.get("tasks")
    if isinstance(tasks, list) and tasks:
        return tasks
    if parsed.get("statement"):
        return [parsed]
    candidates = [v for v in parsed.values() if isinstance(v, list) and v and all(isinstance(i, dict) for i in v)]
    if len(candidates) == 1 and any(item.get("statement") for item in candidates[0]):
        return candidates[0]
    return None


def merge_template_params(template: TaskTemplate | None, *, topic: str, difficulty: str, instructions: str) -> dict:
    if template is None:
        return {
            "topic": topic,
            "difficulty": difficulty or "medium",
            "instructions": instructions,
            "task_kind": "calculation",
            "answer_format": "numeric",
            "tolerance_pct": 2.0,
            "sheet_ids": None,
            "kb_query": "",
            "example_tasks": [],
            "validation_solver": True,
            "validation_data_check": True,
            "chemistry_check": "auto",
            "rubric": [],
        }
    example_tasks = list(template.example_tasks or [])
    if not example_tasks and template.example:
        example_tasks = [{"statement": template.example, "solution": "", "answer": ""}]
    return {
        "topic": topic or template.topic,
        "difficulty": difficulty or template.difficulty or "medium",
        "instructions": "\n".join(filter(None, [template.instructions, instructions])),
        "task_kind": template.task_kind,
        "answer_format": template.answer_format,
        "tolerance_pct": template.numeric_tolerance_pct,
        "sheet_ids": list(template.reference_sheet_ids or []) or None,
        "kb_query": template.kb_query,
        "example_tasks": example_tasks,
        "validation_solver": template.validation_solver,
        "validation_data_check": template.validation_data_check,
        "chemistry_check": getattr(template, "chemistry_check", "auto") or "auto",
        "rubric": list(getattr(template, "rubric", None) or []),
    }


def merge_batch_template_params(template: TaskTemplate | None, params: dict) -> dict:
    """Apply only explicit batch overrides, preserving the template contract.

    Empty request fields mean "use the template".  In particular, eagerly
    replacing an empty difficulty with ``medium`` here silently downgraded hard
    templates before generation.
    """

    return merge_template_params(
        template,
        topic=str(params.get("topic") or ""),
        difficulty=str(params.get("difficulty") or ""),
        instructions=str(params.get("instructions") or ""),
    )


async def resolve_generator_prompt_version(
    db: AsyncSession, assistant_id: str, prompt_version_id: str | None
) -> PromptVersion | None:
    if prompt_version_id:
        prompt = (
            await db.execute(
                select(PromptVersion).where(
                    PromptVersion.id == prompt_version_id, PromptVersion.assistant_id == assistant_id
                )
            )
        ).scalar_one_or_none()
        if prompt is None:
            raise GenerationError("Версия промпта не найдена")
        return prompt
    return (
        (
            await db.execute(
                select(PromptVersion)
                .where(
                    PromptVersion.assistant_id == assistant_id,
                    PromptVersion.role == "generator",
                    PromptVersion.status == "active",
                )
                .order_by(PromptVersion.version.desc())
            )
        )
        .scalars()
        .first()
    )


async def resolve_generator_prompt(db: AsyncSession, assistant_id: str, prompt_version_id: str | None) -> str | None:
    prompt = await resolve_generator_prompt_version(db, assistant_id, prompt_version_id)
    return prompt.system_prompt if prompt else None


async def load_reference_sheets(
    db: AsyncSession, assistant_id: str, sheet_ids: list[str] | None
) -> list[ReferenceSheet]:
    stmt = select(ReferenceSheet).where(ReferenceSheet.assistant_id == assistant_id)
    if sheet_ids:
        stmt = stmt.where(ReferenceSheet.id.in_(sheet_ids))
    else:
        stmt = stmt.where(ReferenceSheet.is_canonical.is_(True))
    return list((await db.execute(stmt.order_by(ReferenceSheet.ord, ReferenceSheet.created_at))).scalars())


def sheets_to_text(sheets: list[ReferenceSheet]) -> str:
    return "\n\n".join(f"{sheet.title}\n{sheet.content_markdown}" for sheet in sheets)


async def build_generation_grounding(
    db: AsyncSession, assistant_id: str, *, sheet_ids: list[str] | None = None, query: str = "",
    include_kb: bool = True,
) -> str:
    return await build_grounding_block(db, assistant_id, sheet_ids=sheet_ids, query=query, include_kb=include_kb)


async def build_grounding_meta(
    db: AsyncSession,
    sheets: list[ReferenceSheet],
    grounding_text: str,
    query: str,
    *,
    assistant_id: str | None = None,
) -> dict:
    # Automatic grounding is query-aware and capped. Freeze only the sheets
    # actually rendered into the model context; otherwise provenance could
    # claim evidence that the generator never saw.
    rendered_sheets = [sheet for sheet in sheets if f"### {sheet.title} (" in grounding_text]
    document_ids = {sheet.source_document_id for sheet in rendered_sheets if sheet.source_document_id}
    documents: dict[str, tuple[str, str]] = {}
    if document_ids:
        rows = (
            await db.execute(
                select(KnowledgeDocument.id, KnowledgeDocument.authority, KnowledgeDocument.effective_version).where(
                    KnowledgeDocument.id.in_(document_ids)
                )
            )
        ).all()
        documents = {document_id: (authority, effective_version) for document_id, authority, effective_version in rows}
    kb_chunks = 0
    kb_sources: list[dict[str, object]] = []
    if KB_HEADER in grounding_text:
        kb_text = grounding_text.split(KB_HEADER, 1)[1]
        headers = [match.group(1).strip() for match in re.finditer(r"^###\s+(.+?)\s*$", kb_text, re.MULTILINE)]
        kb_chunks = len(headers)
        resolved_assistant_id = assistant_id or (sheets[0].assistant_id if sheets else None)
        if headers and resolved_assistant_id:
            rows = (
                await db.execute(
                    select(
                        KnowledgeDocument.id,
                        KnowledgeDocument.title,
                        KnowledgeDocument.authority,
                        KnowledgeDocument.effective_version,
                    ).where(KnowledgeDocument.assistant_id == resolved_assistant_id)
                )
            ).all()
            seen_headers: set[str] = set()
            for document_id, title, authority, effective_version in rows:
                displayed_title = f"{title} [{AUTHORITY_LABELS.get(authority, authority)}]"
                for header in headers:
                    if header in seen_headers or not (
                        header == displayed_title or header.startswith(f"{displayed_title} — ")
                    ):
                        continue
                    seen_headers.add(header)
                    kb_sources.append(
                        {
                            "id": "",
                            "title": header,
                            "source_document_id": document_id,
                            "source_document_exists": True,
                            "source_authority": authority,
                            "source_version": effective_version,
                            "source_kind": "kb_chunk",
                        }
                    )
    return {
        "sheets": [
            {
                "id": sheet.id,
                "title": sheet.title,
                "source_document_id": sheet.source_document_id or "",
                "source_document_exists": bool(sheet.source_document_id and sheet.source_document_id in documents),
                "source_authority": documents.get(sheet.source_document_id or "", ("", ""))[0],
                "source_version": documents.get(sheet.source_document_id or "", ("", ""))[1],
            }
            for sheet in rendered_sheets
        ],
        "kb_chunks": kb_chunks,
        "kb_sources": kb_sources,
        "query": query,
    }


def build_validation_contract(merged: dict, grounding_meta: dict | None = None) -> dict:
    has_rendered_snapshot = grounding_meta is not None
    grounding_meta = grounding_meta or {}
    sheet_ids = (
        [
            str(sheet.get("id") or "")
            for sheet in grounding_meta.get("sheets") or []
            if isinstance(sheet, dict) and sheet.get("id")
        ]
        if has_rendered_snapshot
        else list(merged.get("sheet_ids") or [])
    )
    return normalize_validation_config(
        {
            "answer_format": merged.get("answer_format"),
            "tolerance_pct": merged.get("tolerance_pct"),
            "validation_solver": merged.get("validation_solver") is True,
            "validation_data_check": merged.get("validation_data_check") is True,
            # Once grounding has been rendered, freeze exactly what the model
            # saw — including an intentionally empty set. Never resurrect a
            # selected sheet that was omitted by limits or source policy.
            "sheet_ids": sheet_ids,
            "kb_query": grounding_meta.get("query") or merged.get("kb_query") or merged.get("topic") or "",
            "task_kind": merged.get("task_kind") or "",
            "chemistry_check": merged.get("chemistry_check") or "auto",
        }
    )


def validation_contract_for_task(task: GeneratedTask, merged: dict) -> dict:
    validation = task.validation if isinstance(task.validation, dict) else {}
    grounding = task.grounding if isinstance(task.grounding, dict) else {}
    for candidate in (validation.get("validation_config"), grounding.get("validation_contract")):
        if isinstance(candidate, dict) and candidate.get("answer_format"):
            return normalize_validation_config(candidate)
    return build_validation_contract(merged, grounding)


def _normalize_data_used(value: object) -> list[dict] | None:
    if not isinstance(value, list):
        return None
    normalized: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        title = str(item.get("sheet_title") or "").strip()
        values = item.get("values")
        if not title or not isinstance(values, list) or not values:
            return None
        normalized.append({"sheet_title": title, "values": [str(entry) for entry in values]})
    return normalized


def task_from_item(
    item: dict,
    *,
    assistant_id: str,
    template_id: str | None,
    batch_id: str | None,
    topic: str,
    difficulty: str,
    model_used: str,
    grounding_meta: dict,
    validation_contract: dict | None = None,
    template_rubric: list[dict] | None = None,
    authoritative_topic: bool = False,
) -> GeneratedTask | None:
    if not isinstance(item, dict) or not item.get("statement"):
        return None
    try:
        max_score = float(item.get("max_score") or 10)
    except (TypeError, ValueError):
        max_score = 10.0
    rubric = item.get("rubric")
    if template_rubric:
        rubric = [
            {
                "criterion_name": criterion["criterion_name"],
                "max_score": criterion["max_score"],
                "description": criterion.get("description", ""),
            }
            for criterion in template_rubric
        ]
        max_score = 10.0
    data_used = _normalize_data_used(item.get("data_used"))
    if data_used is None:
        return None
    contract = normalize_validation_config(validation_contract or {})
    chemistry_facts = normalize_chemistry_facts(item.get("chemistry_facts"))
    if chemistry_facts is None and contract.get("chemistry_check") == "off":
        chemistry_facts = {}
    if chemistry_facts is None and contract.get("chemistry_check", "auto") == "auto" and "chemistry_facts" not in item:
        chemistry_facts = {}
    if chemistry_facts is None:
        return None
    required_block = FACT_BLOCK_BY_CHECK.get(contract.get("chemistry_check", "auto"))
    if required_block and required_block not in chemistry_facts:
        return None
    return GeneratedTask(
        assistant_id=assistant_id,
        template_id=template_id,
        batch_id=batch_id,
        statement=str(item.get("statement", "")),
        reference_solution=str(item.get("reference_solution", "")),
        answer=str(item.get("answer") or ""),
        images=[str(path).strip() for path in item.get("images", []) if str(path).strip()]
        if isinstance(item.get("images"), list)
        else [],
        rubric=rubric if isinstance(rubric, list) else [],
        max_score=max_score,
        difficulty=str(item.get("difficulty") or difficulty),
        topic=str(topic if authoritative_topic else item.get("topic") or topic),
        model_used=model_used,
        status="draft",
        grounding={
            **grounding_meta,
            "data_used": data_used,
            "chemistry_facts": chemistry_facts,
            "chemistry_facts_source": "generator",
            "validation_contract": contract,
            **({"blueprint": item["_blueprint"]} if "_blueprint" in item else {}),
            **({"calculation_audit": item["_calculation_audit"]} if "_calculation_audit" in item else {}),
        },
    )


def generation_item_contract_error(item: object, chemistry_check: str) -> str | None:
    if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
        return "нет условия"
    if _normalize_data_used(item.get("data_used")) is None:
        return "нет явного data_used"
    chemistry_facts = normalize_chemistry_facts(item.get("chemistry_facts"))
    if chemistry_facts is None and chemistry_check == "off":
        chemistry_facts = {}
    if chemistry_facts is None and chemistry_check == "auto" and "chemistry_facts" not in item:
        chemistry_facts = {}
    if chemistry_facts is None:
        return "нет корректного chemistry_facts"
    required_block = FACT_BLOCK_BY_CHECK.get(chemistry_check)
    if required_block and required_block not in chemistry_facts:
        return f"нет обязательного блока chemistry_facts.{required_block}"
    return None


async def _resolve_batch_model(db: AsyncSession, model_entry_id: str) -> tuple[Provider, ModelEntry]:
    model = (await db.execute(select(ModelEntry).where(ModelEntry.id == model_entry_id))).scalar_one_or_none()
    if model is None:
        raise GenerationError(f"Модель {model_entry_id} не найдена")
    provider = (await db.execute(select(Provider).where(Provider.id == model.provider_id))).scalar_one_or_none()
    if provider is None or not provider.enabled:
        raise GenerationError(f"Провайдер модели {model.model_id} недоступен")
    return provider, model


async def _set_progress(db: AsyncSession, batch: GenerationBatch, stage: str, done: int, total: int) -> None:
    batch.progress = {"stage": stage, "done": done, "total": total}
    await db.commit()


async def _generate_batch_items(
    provider: Provider,
    model: ModelEntry,
    assistant: Assistant,
    system_prompt: str | None,
    *,
    merged: dict,
    params: dict,
    count: int,
    grounding_text: str,
    existing_statements: list[str],
    on_progress: Callable[[int], Awaitable[None]] | None = None,
    call_budget: _GenerationCallBudget | None = None,
    on_attempt: Callable[[int, int, str], Awaitable[None]] | None = None,
    on_items: Callable[[list[dict]], Awaitable[None]] | None = None,
    on_error_audit: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[list[dict], list[str]]:
    if uses_single_verifier(assistant):
        merged = {**merged, "chemistry_check": "off"}
    items: list[dict] = []
    seen_statements = list(existing_statements)
    errors: list[str] = []
    minimum_calls = (count + GENERATION_CHUNK - 1) // GENERATION_CHUNK
    max_calls = minimum_calls + MAX_REFILL_ATTEMPTS
    effective_call_budget = call_budget or _GenerationCallBudget(limit=max_calls)

    for _attempt in range(max_calls):
        missing = count - len(items)
        if missing <= 0:
            break
        if not effective_call_budget.claim():
            errors.append(
                "Исчерпан общий бюджет вызовов генератора: "
                f"{effective_call_budget.used} из {effective_call_budget.limit}"
            )
            break
        take = min(GENERATION_CHUNK, missing)
        if on_attempt is not None:
            await on_attempt(effective_call_budget.used, len(items), errors[-1] if errors else "")
        try:
            chunk = await generate_tasks(
                provider,
                model,
                assistant,
                system_prompt,
                topic=merged["topic"],
                difficulty=merged["difficulty"],
                count=take,
                task_kind=merged["task_kind"],
                answer_format=merged["answer_format"],
                instructions=merged["instructions"],
                grounding=grounding_text,
                rubric=merged.get("rubric", []),
                example_tasks=merged["example_tasks"],
                existing_statements=seen_statements,
                temperature=float(params.get("temperature") or 0.7),
                chemistry_check=merged.get("chemistry_check", "auto"),
            )
        except llm.LlmError as err:
            if on_error_audit is not None and err.raw:
                raw = err.raw
                failed = raw.get("failed_completion", raw)
                failed = failed if isinstance(failed, dict) else {}
                finish = failed.get("finish_reason")
                if (not isinstance(finish, (str, type(None))) or finish not in
                        {None, "stop", "length", "tool_calls", "content_filter", "function_call", "error"}):
                    finish = "unknown"
                safe = llm.completion_failure_audit(failed.get("usage"), finish)
                safe["attempt"] = effective_call_budget.used
                safe["total_usage"] = llm.completion_failure_audit(raw.get("usage"), None)["usage"]
                if type(raw.get("model_calls")) is int and raw["model_calls"] >= 0:
                    safe["model_calls"] = raw["model_calls"]
                await on_error_audit(safe)
            errors.append(str(err))
            continue

        rejected_contracts = [
            error
            for item in chunk
            if (error := generation_item_contract_error(item, merged.get("chemistry_check", "auto")))
        ]
        usable = [
            item
            for item in chunk
            if generation_item_contract_error(item, merged.get("chemistry_check", "auto")) is None
        ]
        usable = usable[:missing]
        if not usable:
            detail = ", ".join(sorted(set(rejected_contracts)))
            errors.append(f"Модель вернула порцию без полного evidence-контракта: {detail or 'нет задач'}")
            continue
        if on_items is not None:
            await on_items(usable)
        items.extend(usable)
        seen_statements.extend(str(item["statement"]) for item in usable)
        if on_progress is not None:
            await on_progress(len(items))

    return items, errors


def _mark_batch_finished(
    batch: GenerationBatch, *, requested_count: int, generated_count: int, generation_errors: list[str]
) -> None:
    batch.finished_at = utcnow()
    if generated_count >= requested_count:
        batch.status = "completed"
        batch.error = ""
        batch.progress = {"stage": "Готово", "done": requested_count, "total": requested_count}
        return

    batch.status = "failed"
    detail = generation_errors[-1][:400] if generation_errors else "модель вернула меньше валидных задач"
    candidate_count = getattr(batch, "generated_count", generated_count)
    batch.error = (
        f"Неполная партия: готово {generated_count} из {requested_count}; "
        f"проверено кандидатов: {candidate_count}. Последняя причина: {detail}"
    )
    batch.progress = {
        "stage": "Неполная партия",
        "done": generated_count,
        "total": requested_count,
    }


async def _validate_batch(
    db: AsyncSession,
    batch: GenerationBatch,
    created: list[GeneratedTask],
    merged: dict,
    solver_provider: Provider,
    solver_model: ModelEntry,
    grounding_text: str,
    sheets_text: str,
    discipline_context: str = "",
) -> None:
    assistant_result = await db.execute(select(Assistant).where(Assistant.id == batch.assistant_id))
    if hasattr(assistant_result, "scalar_one_or_none"):
        assistant = assistant_result.scalar_one_or_none()
    else:
        # Keep the helper usable with the small in-memory DB doubles used by
        # unit tests and migration tooling. A missing assistant is generic,
        # never physical-chemistry, by default.
        assistant = None
    physical = assistant is not None and uses_single_verifier(assistant)
    verifier_prompt = None
    if physical:
        verifier_prompt = (
            await db.execute(select(PromptVersion).where(
                PromptVersion.assistant_id == batch.assistant_id,
                PromptVersion.role == "verifier",
                PromptVersion.status == "active",
            ))
        ).scalar_one_or_none()
        if verifier_prompt and verifier_prompt.target_family and (
            verifier_prompt.target_family.casefold() != solver_model.family.casefold()
        ):
            raise GenerationError("Активный промпт проверки не соответствует семейству модели")
    # Include metadata absent from the shared evidence fingerprint: repairs can
    # change these fields too, so teacher edits to them must also win.
    repair_fields = ("statement", "reference_solution", "answer", "images", "rubric",
                     "max_score", "difficulty", "topic", "grounding")
    original_content = {
        task.id: deepcopy({key: getattr(task, key, None) for key in repair_fields})
        for task in created
    } if physical else {}
    prior = (
        (
            await db.execute(
                select(GeneratedTask.statement)
                .where(
                    GeneratedTask.assistant_id == batch.assistant_id,
                    GeneratedTask.id.not_in([task.id for task in created]),
                )
                .order_by(GeneratedTask.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    total = len(created)
    stage_name = "Проверка решателем" if merged["validation_solver"] else "Проверка задач"
    semaphore = asyncio.Semaphore(2)
    async def validate_one(task):
        async with semaphore:
            neighbours = [other.statement for other in created if other is not task]
            contract = validation_contract_for_task(task, merged)
            if physical:
                validation, correction = await run_physical_validation(
                    task=task,
                    provider=solver_provider,
                    model=solver_model,
                    grounding=grounding_text,
                    discipline_context=discipline_context + "\n\nБлюпринт задания:\n" + str(merged.get("instructions", "")),
                    answer_format=contract["answer_format"],
                    tolerance_pct=contract["tolerance_pct"],
                    system_prompt=verifier_prompt.system_prompt if verifier_prompt else None,
                    essential_tools=assistant_tools_enabled(assistant, "verifier"),
                    generation_policy=getattr(assistant, "generation_policy", "legacy"),
                )
                return task, validation, correction
            validation = await run_validation(
                statement=task.statement,
                reference_solution=task.reference_solution,
                reference_answer=task.answer,
                rubric=task.rubric,
                max_score=task.max_score,
                task_images=getattr(task, "images", []),
                answer_format=contract["answer_format"],
                tolerance_pct=contract["tolerance_pct"],
                grounding=grounding_text,
                sheets_text=sheets_text,
                existing_statements=list(prior) + neighbours,
                data_used=(task.grounding or {}).get("data_used"),
                solver_provider=solver_provider,
                solver_model=solver_model,
                run_solver=contract["validation_solver"],
                essential_tools=assistant_tools_enabled(assistant, "verifier"),
                run_data=contract["validation_data_check"],
                validation_config=contract,
                discipline_context=discipline_context,
                topic=getattr(task, "topic", ""),
                chemistry_facts=(task.grounding or {}).get("chemistry_facts"),
                chemistry_facts_source=str((task.grounding or {}).get("chemistry_facts_source") or ""),
                grounding_sheets=[
                    *((task.grounding or {}).get("sheets") or []),
                    *((task.grounding or {}).get("kb_sources") or []),
                ],
            )
            return task, validation, None

    await _set_progress(db, batch, f"{stage_name}: готово 0/{total}", 0, total)
    async with asyncio.TaskGroup() as group:
        pending = [group.create_task(validate_one(task)) for task in created]
        for index, completed in enumerate(asyncio.as_completed(pending), start=1):
            task, validation, correction = await completed
            if physical:
                # Lock only after the remote check, through the following commit.
                await db.refresh(task, with_for_update=True)
                if (original_content[task.id] != {key: getattr(task, key, None) for key in repair_fields}
                        or not evidence_matches_task(validation, task)):
                    await _set_progress(db, batch, f"{stage_name}: готово {index}/{total}", index, total)
                    continue
            else:
                await db.refresh(task)
            if physical and correction is not None:
                repaired = task_from_item(
                    correction,
                    assistant_id=task.assistant_id,
                    template_id=task.template_id,
                    batch_id=task.batch_id,
                    topic=task.topic,
                    difficulty=task.difficulty,
                    model_used=task.model_used,
                    grounding_meta=task.grounding or {},
                    validation_contract=validation.get("validation_config"),
                )
                if repaired is None:
                    validation = dict(validation)
                    validation["verdict"] = "needs_review"
                    validation["reasons"] = [
                        *(validation.get("reasons") or []),
                        "Верификатор вернул неполную исправленную задачу",
                    ]
                else:
                    task.statement = repaired.statement
                    task.reference_solution = repaired.reference_solution
                    task.answer = repaired.answer
                    task.rubric = repaired.rubric
                    task.max_score = repaired.max_score
                    task.difficulty = repaired.difficulty
                    task.topic = repaired.topic
                    task.grounding = repaired.grounding
                    validation = dict(validation)
                    validation["correction"] = {**validation.get("correction", {}), "applied": True}
                    validation["content_fingerprint"] = task_content_fingerprint(
                        task, validation.get("validation_config") or {}
                    )
            if not evidence_matches_task(validation, task):
                # Преподаватель успел изменить содержимое во время LLM-проверки.
                # Старое evidence не записываем и пользовательские изменения не затираем.
                continue
            task.validation = validation
            if validation["verdict"] == "validated":
                task.status = "validated"
                if task_is_export_ready(task):
                    batch.validated_count += 1
                else:
                    validation = dict(validation)
                    validation["candidate_disposition"] = "discarded"
                    validation["reasons"] = [
                        *(validation.get("reasons") or []),
                        "Проверка не сформировала полный экспортный evidence-контракт",
                    ]
                    task.validation = validation
                    task.status = "rejected"
            else:
                validation = dict(validation)
                validation["candidate_disposition"] = "needs_review"
                task.validation = validation
                task.status = "needs_review"
            task.approved = False
            await db.commit()

            await _set_progress(db, batch, f"{stage_name}: готово {index}/{total}", index, total)


async def _execute_batch(db: AsyncSession, batch: GenerationBatch) -> None:
    params = batch.params or {}
    count = int(params.get("count") or batch.requested_count or 5)
    assistant = (await db.execute(select(Assistant).where(Assistant.id == batch.assistant_id))).scalar_one_or_none()
    if assistant is None:
        raise GenerationError("Дисциплина не найдена")
    physical = uses_single_verifier(assistant)
    provider, model = await _resolve_batch_model(db, str(params.get("model_entry_id") or ""))
    physical_verifier = None
    if physical:
        verifier_id = params.get("solver_model_entry_id") or task_verifier_model_id(assistant)
        if not verifier_id:
            raise GenerationError("Выберите отдельную модель верификации задач")
        physical_verifier = await _resolve_batch_model(db, str(verifier_id))
        if not physical_verifier[1].enabled:
            raise GenerationError("Модель верификации отключена")
        require_decision_model(physical_verifier[1])

    template: TaskTemplate | None = None
    if batch.template_id:
        template = (
            await db.execute(
                select(TaskTemplate).where(
                    TaskTemplate.id == batch.template_id, TaskTemplate.assistant_id == batch.assistant_id
                )
            )
        ).scalar_one_or_none()
        if template is None:
            raise GenerationError("Шаблон не найден")

    merged = merge_batch_template_params(template, params)
    if physical:
        merged["chemistry_check"] = "off"
    system_prompt = await resolve_generator_prompt(db, batch.assistant_id, params.get("prompt_version_id"))

    await _set_progress(db, batch, "Сбор справочных материалов", 0, count)
    sheets = await load_reference_sheets(db, batch.assistant_id, merged["sheet_ids"])
    grounding_query = merged["kb_query"] or merged["topic"]
    grounding_text = await build_generation_grounding(
        db, batch.assistant_id, sheet_ids=merged["sheet_ids"], query=grounding_query,
        include_kb=not (getattr(assistant, "generation_policy", "legacy") == "single_verifier"
                        and bool(merged["example_tasks"])),
    )

    existing = (
        (
            await db.execute(
                select(GeneratedTask.statement)
                .where(GeneratedTask.assistant_id == batch.assistant_id)
                .order_by(GeneratedTask.created_at.desc())
                .limit(8)
            )
        )
        .scalars()
        .all()
    )

    await _set_progress(db, batch, "Генерация условий", 0, count)

    # Генерируем небольшими порциями: задачи с тяжёлым LaTeX-решением в решении не помещаются
    # в один JSON-ответ. Если модель недодала элементы или вернула элемент без условия,
    # ограниченное число дополнительных запросов восполняет недостающее количество.
    async def update_generation_progress(done: int) -> None:
        await _set_progress(db, batch, "Генерация условий", done, count)

    async def update_attempt(attempt: int, done: int, last_error: str) -> None:
        detail = f"; повтор после ошибки: {last_error[:180]}" if last_error else ""
        await _set_progress(db, batch,
            f"Генерация: запрос {attempt}, получено {done}/{count}{detail}", done, count)

    async def save_error_audit(audit: dict) -> None:
        batch.params = {**batch.params, "generation_error_audits": [
            *(batch.params.get("generation_error_audits") or []), audit]}
        # Persist before the caller propagates the generation failure.
        await db.commit()

    # Лимит рассчитывается один раз на всю партию. Иначе каждая новая волна добора
    # заново получает MAX_REFILL_ATTEMPTS и число оплачиваемых запросов растёт без
    # связи с общим бюджетом кандидатов.
    # Physical chemistry uses exactly one generator call per requested task.
    # A failed verifier repairs that same row; it never opens a refill wave.
    candidate_budget = count if physical else min(count * 2, count + 5)
    call_budget = _GenerationCallBudget(limit=count if physical else _generation_call_limit(candidate_budget))
    grounding_meta = await build_grounding_meta(
        db,
        sheets,
        grounding_text,
        grounding_query,
        assistant_id=batch.assistant_id,
    )
    validation_contract = build_validation_contract(merged, grounding_meta)

    async def persist_candidates(candidate_items: list[dict]) -> list[GeneratedTask]:
        persisted: list[GeneratedTask] = []
        for item in candidate_items:
            task = task_from_item(
                item,
                assistant_id=batch.assistant_id,
                template_id=batch.template_id,
                batch_id=batch.id,
                topic=merged["topic"],
                difficulty=merged["difficulty"],
                model_used=f"{provider.name}/{model.model_id}",
                grounding_meta=grounding_meta,
                validation_contract=validation_contract,
                template_rubric=merged.get("rubric", []),
                authoritative_topic=physical,
            )
            if task is not None:
                db.add(task)
                persisted.append(task)
        batch.generated_count += len(persisted)
        await db.commit()
        return persisted

    created: list[GeneratedTask] = []
    async def save_generated(items: list[dict]) -> None:
        created.extend(await persist_candidates(items))

    items, gen_errors = await _generate_batch_items(
        provider,
        model,
        assistant,
        system_prompt,
        merged=merged,
        params=params,
        count=count,
        grounding_text=grounding_text,
        existing_statements=list(existing),
        on_progress=update_generation_progress,
        on_attempt=update_attempt,
        on_items=save_generated,
        on_error_audit=save_error_audit,
        call_budget=call_budget,
    )
    if not items and gen_errors:
        raise llm.LlmError(" || ".join(gen_errors[:3]))

    if not created:
        raise GenerationError("Модель не вернула ни одной валидной задачи")

    validation_enabled = True if physical else bool(params.get("validate_tasks", True))
    solver_provider, solver_model = provider, model
    solver_entry_id = params.get("solver_model_entry_id") or (task_verifier_model_id(assistant) if physical else None)
    if physical_verifier is not None:
        solver_provider, solver_model = physical_verifier
    elif solver_entry_id:
        solver_provider, solver_model = await _resolve_batch_model(db, str(solver_entry_id))
    if validation_enabled:
        await _validate_batch(
            db,
            batch,
            created,
            merged,
            solver_provider,
            solver_model,
            grounding_text,
            sheets_to_text(sheets),
            build_assistant_profile(assistant),
        )

    if physical:
        needs_review = [task for task in created if task.status == "needs_review"]
        batch.params = {
            **(batch.params or {}),
            "quality_summary": {
                "pipeline": "single-generator-single-verifier-v1",
                "candidate_count": batch.generated_count,
                "ready_count": batch.validated_count,
                "discarded_count": 0,
                "needs_review_count": len(needs_review),
                "discarded_by_reason": {},
                "candidate_budget": candidate_budget,
                "generation_calls_used": call_budget.used,
                "generation_call_limit": call_budget.limit,
                "repair_attempts": 0,
                "replacement_generations": 0,
            },
        }
        # The verifier's unresolved candidates remain visible for review; they
        # are not silently discarded and do not trigger paid replacements.
        _mark_batch_finished(
            batch,
            requested_count=count,
            generated_count=batch.generated_count,
            generation_errors=gen_errors,
        )
        await db.commit()
        return

    repair_attempts = 0
    repaired_task_ids: set[str] = set()

    def repairable_task(task: GeneratedTask) -> bool:
        if task.id in repaired_task_ids or task.status == "validated":
            return False
        validation = task.validation or {}
        if any(
            (validation.get(role) or {}).get("status") == "error"
            for role in ("solver", "verifier", "critic")
        ):
            return False
        return bool(validation)

    # First repair the rejected candidate in place.  Only if the repaired
    # candidate still fails do we spend a call on a genuinely new task.
    repair_candidates = [task for task in created if repairable_task(task)]
    for task in repair_candidates:
        repaired_task_ids.add(task.id)
        repair_attempts += 1
        await _set_progress(
            db,
            batch,
            f"Исправление кандидата {repair_attempts}/{len(repair_candidates)}",
            batch.validated_count,
            count,
        )
        repaired_item = await repair_generated_task(
            provider,
            model,
            assistant,
            system_prompt,
            task,
            task.validation or {},
        )
        if repaired_item is None:
            continue
        repaired = task_from_item(
            repaired_item,
            assistant_id=batch.assistant_id,
            template_id=batch.template_id,
            batch_id=batch.id,
            topic=merged["topic"],
            difficulty=merged["difficulty"],
            model_used=f"{provider.name}/{model.model_id}",
            grounding_meta=grounding_meta,
            validation_contract=validation_contract,
            template_rubric=merged.get("rubric", []),
        )
        if repaired is None:
            continue
        task.statement = repaired.statement
        task.reference_solution = repaired.reference_solution
        task.answer = repaired.answer
        task.images = repaired.images
        task.rubric = repaired.rubric
        task.max_score = repaired.max_score
        task.difficulty = repaired.difficulty
        task.topic = repaired.topic
        task.grounding = repaired.grounding
        task.validation = {}
        task.status = "draft"
        task.approved = False
        await db.commit()
        await _validate_batch(
            db,
            batch,
            [task],
            merged,
            solver_provider,
            solver_model,
            grounding_text,
            sheets_to_text(sheets),
            build_assistant_profile(assistant),
        )

    # Пользователь заказывает готовые задачи, а не число сырых ответов модели.
    # Непрошедший кандидат сохраняется для разбора и автоматически
    # заменяется новым в пределах ограниченного бюджета.
    def retryable_content_failure(task: GeneratedTask) -> bool:
        v = task.validation or {}
        if not v or v.get("verdict") == "validated":
            return False
        if any(
            (v.get(role) or {}).get("status") == "error"
            for role in ("solver", "verifier", "critic")
        ):
            return False
        return True
    while (validation_enabled and batch.validated_count < count and batch.generated_count < candidate_budget
           and any(retryable_content_failure(task) for task in created)
           and not any((task.validation or {}).get("solver", {}).get("status") == "error" for task in created)):
        missing = count - batch.validated_count
        remaining_budget = candidate_budget - batch.generated_count
        refill_count = min(missing, remaining_budget)
        await _set_progress(
            db,
            batch,
            f"Восполнение: готово {batch.validated_count}/{count}",
            batch.validated_count,
            count,
        )
        refill_items, refill_errors = await _generate_batch_items(
            provider,
            model,
            assistant,
            system_prompt,
            merged=merged,
            params=params,
            count=refill_count,
            grounding_text=grounding_text,
            existing_statements=list(existing) + [task.statement for task in created],
            call_budget=call_budget,
            on_attempt=update_attempt,
            on_error_audit=save_error_audit,
        )
        gen_errors.extend(refill_errors)
        if not refill_items:
            break
        refill = await persist_candidates(refill_items)
        if not refill:
            break
        created.extend(refill)
        await _validate_batch(
            db,
            batch,
            refill,
            merged,
            solver_provider,
            solver_model,
            grounding_text,
            sheets_to_text(sheets),
            build_assistant_profile(assistant),
        )

    rejected = [task for task in created if task.status in {"rejected", "needs_review"}]
    failure_counts: dict[str, int] = {}
    for task in rejected:
        validation = task.validation or {}
        if (validation.get("dedup") or {}).get("duplicate"):
            code = "duplicate"
        elif (validation.get("data") or {}).get("status") != "ok":
            code = "source_data"
        elif (validation.get("sanity") or {}).get("issues"):
            code = "task_contract"
        elif (validation.get("reference_solution_check") or {}).get("verdict") != "match":
            code = "reference_solution"
        else:
            code = "solution_disagreement"
        failure_counts[code] = failure_counts.get(code, 0) + 1
    batch.params = {
        **(batch.params or {}),
        "quality_summary": {
            "candidate_count": batch.generated_count,
            "ready_count": batch.validated_count,
            "discarded_count": len(rejected),
            "discarded_by_reason": failure_counts,
            "candidate_budget": candidate_budget,
            "generation_calls_used": call_budget.used,
            "generation_call_limit": call_budget.limit,
            "repair_attempts": repair_attempts,
        },
    }
    if batch.validated_count < count:
        gen_errors.extend(dict.fromkeys(reason for task in rejected for reason in (task.validation or {}).get("reasons", [])))
    _mark_batch_finished(
        batch,
        requested_count=count,
        generated_count=batch.validated_count if validation_enabled else batch.generated_count,
        generation_errors=gen_errors,
    )
    await db.commit()


async def run_batch(batch_id: str) -> None:
    async with SessionLocal() as db:
        batch = (await db.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))).scalar_one_or_none()
        if batch is None:
            return
        try:
            async with asyncio.timeout(1200):
                await _execute_batch(db, batch)
        except Exception as err:  # партия не должна падать молча — фиксируем любую ошибку в статусе
            await db.rollback()
            batch.status = "failed"
            batch.error = ("Достигнут лимит 20 минут. Уже сохранённые задачи доступны; повторите проверку оставшихся задач." if isinstance(err, TimeoutError) else str(err))
            batch.finished_at = utcnow()
            await db.commit()
