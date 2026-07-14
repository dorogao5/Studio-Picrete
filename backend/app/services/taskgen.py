import json
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.llm import client as llm
from app.models import (
    Assistant,
    GeneratedTask,
    GenerationBatch,
    ModelEntry,
    PromptVersion,
    Provider,
    ReferenceSheet,
    TaskTemplate,
    utcnow,
)
from app.services.assistant_profile import with_assistant_profile
from app.services.contracts import GENERATION_JSON_CONTRACT, JSON_LATEX_ESCAPING_NOTE
from app.services.grounding import KB_HEADER, build_grounding_block
from app.services.validation import run_validation

FALLBACK_GENERATOR_PROMPT = """Вы — опытный преподаватель и методист высшей школы по дисциплине «{discipline}».
Вы составляете типовые учебные задания: условие, подробное эталонное решение, краткий финальный ответ (answer)
и рубрику оценивания. Задания должны быть корректными, решаемыми, с реалистичными числами и согласованными
единицами измерения.
Если в сообщении приведены справочные материалы курса — табличные величины берите ТОЛЬКО из них и перечисляйте
использованные значения в поле data_used. В data_used указывайте только реально существующий заголовок справочного
листа и дословно взятые из него значения. Числа, которые вы сами задаёте в самодостаточном условии, не являются
справочными данными: не добавляйте их в data_used; если справочники не использованы, верните data_used: [].
Запрещено подставлять справочные значения из общих знаний: если нужных
данных нет, стройте задачу на тех данных, которые приведены, либо задавайте недостающие величины прямо в условии.
Формулы записывайте в LaTeX ($...$). Отвечайте только на русском языке.

Ответ — строго JSON по схеме:
{contract}
Никакого текста вне JSON."""

# Задачи с объёмным LaTeX-решением не помещаются по несколько в один JSON — генерируем порциями.
GENERATION_CHUNK = 2
# Дополнительные запросы сверх минимально необходимого числа порций. Они восполняют
# недостающие/невалидные элементы, но не дают фоновой задаче зациклиться на плохом ответе модели.
MAX_REFILL_ATTEMPTS = 3

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
        lines = [f"Пример {index}.", f"Условие: {example['statement']}"]
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
    sections.append(f"Уже существующие задачи (НЕ повторяйте их сюжеты и числа):\n{existing or '(нет)'}")
    sections.append(
        "Каждая задача: условие + подробное эталонное решение + краткий финальный ответ (answer) "
        "+ рубрика с баллами + список использованных справочных значений (data_used).\n"
        "В data_used перечисляйте только значения, действительно скопированные из приложенного справочного листа, "
        "с его точным заголовком. Самостоятельно заданные числа условия туда не входят; если справочник не "
        "использован, верните data_used: [].\n"
        "Ответ — строго JSON по схеме (эта схема главнее любых других форматов):\n"
        f"{GENERATION_JSON_CONTRACT}\n{JSON_LATEX_ESCAPING_NOTE}"
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
) -> list[dict]:
    prompt = system_prompt or FALLBACK_GENERATOR_PROMPT.format(
        discipline=assistant.discipline, contract=GENERATION_JSON_CONTRACT
    )
    prompt = with_assistant_profile(prompt, assistant)
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
    )
    result = await llm.chat(
        provider, model, prompt, user_message, temperature=temperature, json_mode=True, max_tokens=8000
    )
    parsed = llm.extract_json(result.text)
    tasks = _coerce_tasks(parsed)
    if tasks is None:
        raise llm.LlmError(f"Генератор не вернул массив tasks; начало ответа: {result.text[:180]}")
    return tasks


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
        "rubric": list(getattr(template, "rubric", None) or []),
    }


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
    db: AsyncSession, assistant_id: str, *, sheet_ids: list[str] | None = None, query: str = ""
) -> str:
    return await build_grounding_block(db, assistant_id, sheet_ids=sheet_ids, query=query)


def build_grounding_meta(sheets: list[ReferenceSheet], grounding_text: str, query: str) -> dict:
    kb_chunks = 0
    if KB_HEADER in grounding_text:
        kb_chunks = grounding_text.split(KB_HEADER, 1)[1].count("\n### ")
    return {
        "sheets": [{"id": sheet.id, "title": sheet.title} for sheet in sheets],
        "kb_chunks": kb_chunks,
        "query": query,
    }


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
    template_rubric: list[dict] | None = None,
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
    data_used = item.get("data_used")
    return GeneratedTask(
        assistant_id=assistant_id,
        template_id=template_id,
        batch_id=batch_id,
        statement=str(item.get("statement", "")),
        reference_solution=str(item.get("reference_solution", "")),
        answer=str(item.get("answer") or ""),
        rubric=rubric if isinstance(rubric, list) else [],
        max_score=max_score,
        difficulty=str(item.get("difficulty") or difficulty),
        topic=str(item.get("topic") or topic),
        model_used=model_used,
        status="draft",
        grounding={**grounding_meta, "data_used": data_used if isinstance(data_used, list) else []},
    )


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
) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    seen_statements = list(existing_statements)
    errors: list[str] = []
    minimum_calls = (count + GENERATION_CHUNK - 1) // GENERATION_CHUNK
    max_calls = minimum_calls + MAX_REFILL_ATTEMPTS

    for _attempt in range(max_calls):
        missing = count - len(items)
        if missing <= 0:
            break
        take = min(GENERATION_CHUNK, missing)
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
            )
        except llm.LlmError as err:
            errors.append(str(err))
            continue

        usable = [item for item in chunk if isinstance(item, dict) and str(item.get("statement") or "").strip()]
        usable = usable[:missing]
        if not usable:
            errors.append("Модель вернула порцию без валидных условий задач")
            continue
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
) -> None:
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
    for index, task in enumerate(created, start=1):
        await _set_progress(db, batch, f"{stage_name} {index}/{total}", index - 1, total)
        neighbours = [other.statement for other in created if other is not task]
        validation = await run_validation(
            statement=task.statement,
            reference_solution=task.reference_solution,
            reference_answer=task.answer,
            rubric=task.rubric,
            max_score=task.max_score,
            answer_format=merged["answer_format"],
            tolerance_pct=merged["tolerance_pct"],
            grounding=grounding_text,
            sheets_text=sheets_text,
            existing_statements=list(prior) + neighbours,
            data_used=(task.grounding or {}).get("data_used", []),
            solver_provider=solver_provider,
            solver_model=solver_model,
            run_solver=merged["validation_solver"],
            run_data=merged["validation_data_check"],
        )
        task.validation = validation
        if validation["verdict"] == "validated":
            task.status = "validated"
            batch.validated_count += 1
        else:
            validation = dict(validation)
            validation["candidate_disposition"] = "discarded"
            task.validation = validation
            task.status = "rejected"
        task.approved = False
        await db.commit()


async def _execute_batch(db: AsyncSession, batch: GenerationBatch) -> None:
    params = batch.params or {}
    count = int(params.get("count") or batch.requested_count or 5)
    assistant = (await db.execute(select(Assistant).where(Assistant.id == batch.assistant_id))).scalar_one_or_none()
    if assistant is None:
        raise GenerationError("Дисциплина не найдена")
    provider, model = await _resolve_batch_model(db, str(params.get("model_entry_id") or ""))

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

    merged = merge_template_params(
        template,
        topic=str(params.get("topic") or ""),
        difficulty=str(params.get("difficulty") or "medium"),
        instructions=str(params.get("instructions") or ""),
    )
    system_prompt = await resolve_generator_prompt(db, batch.assistant_id, params.get("prompt_version_id"))

    await _set_progress(db, batch, "Сбор справочных материалов", 0, count)
    sheets = await load_reference_sheets(db, batch.assistant_id, merged["sheet_ids"])
    grounding_query = merged["kb_query"] or merged["topic"]
    grounding_text = await build_generation_grounding(
        db, batch.assistant_id, sheet_ids=merged["sheet_ids"], query=grounding_query
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
    )
    if not items and gen_errors:
        raise llm.LlmError(" || ".join(gen_errors[:3]))

    grounding_meta = build_grounding_meta(sheets, grounding_text, grounding_query)

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
                template_rubric=merged.get("rubric", []),
            )
            if task is not None:
                db.add(task)
                persisted.append(task)
        batch.generated_count += len(persisted)
        await db.commit()
        return persisted

    created = await persist_candidates(items)
    if not created:
        raise GenerationError("Модель не вернула ни одной валидной задачи")

    validation_enabled = bool(params.get("validate_tasks", True))
    solver_provider, solver_model = provider, model
    if params.get("solver_model_entry_id"):
        solver_provider, solver_model = await _resolve_batch_model(db, str(params["solver_model_entry_id"]))
    if validation_enabled:
        await _validate_batch(
            db, batch, created, merged, solver_provider, solver_model, grounding_text, sheets_to_text(sheets)
        )

    # Пользователь заказывает готовые задачи, а не число сырых ответов модели.
    # Непрошедший кандидат сохраняется для аудита как rejected и автоматически
    # заменяется новым в пределах ограниченного бюджета.
    candidate_budget = min(count * 3, count + 20)
    while validation_enabled and batch.validated_count < count and batch.generated_count < candidate_budget:
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
        )
        gen_errors.extend(refill_errors)
        if not refill_items:
            break
        refill = await persist_candidates(refill_items)
        if not refill:
            break
        created.extend(refill)
        await _validate_batch(
            db, batch, refill, merged, solver_provider, solver_model, grounding_text, sheets_to_text(sheets)
        )

    rejected = [task for task in created if task.status == "rejected"]
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
        },
    }
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
            await _execute_batch(db, batch)
        except Exception as err:  # партия не должна падать молча — фиксируем любую ошибку в статусе
            await db.rollback()
            batch.status = "failed"
            batch.error = str(err)
            batch.finished_at = utcnow()
            await db.commit()
