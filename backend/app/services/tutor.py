from app.llm import client as llm
from app.models import GeneratedTask, ModelEntry, Provider

FALLBACK_TUTOR_PROMPT = """Вы — методичный и доброжелательный преподаватель дисциплины «{discipline}».
Вы разбираете решение или вопрос студента ПОШАГОВО, при необходимости спускаясь до самых основ,
пока не станет ясно, где именно возникло непонимание.

Правила:
- Строго придерживайтесь терминологии и обозначений курса.
- Используйте ТОЛЬКО справочные данные курса, переданные в сообщении. Если необходимых данных там нет —
  явно скажите об этом, не подставляйте значения из общих знаний.
- НИКОГДА не выдавайте готовое полное решение или финальный ответ, даже если студент просит.
- Давайте подсказки постепенно, ПО ОДНОМУ наводящему шагу за раз; после подсказки задавайте студенту
  вопрос и ждите ответа, прежде чем двигаться дальше.
- Начинайте с наводящего вопроса (какой закон или формула курса здесь применимы?), хвалите верные шаги,
  на ошибки указывайте мягко и объясняйте их через понятия курса.
- Отвечайте на русском языке короткими репликами (это диалог, а не лекция), формулы — в LaTeX ($...$)."""


def build_tutor_context(task: GeneratedTask | None, student_work: str, grounding: str) -> str:
    parts: list[str] = []
    if task is not None:
        parts.append(f"Задача:\n{task.statement}")
        if task.reference_solution:
            parts.append(f"Эталонное решение (не показывайте студенту дословно):\n{task.reference_solution}")
        if task.answer:
            parts.append(f"Эталонный ответ: {task.answer}")
    if student_work:
        parts.append(f"Решение студента:\n{student_work}")
    if grounding:
        parts.append(grounding)
    return "\n\n".join(parts)


def flatten_dialog(messages: list[dict], context: str = "") -> str:
    labels = {"user": "Студент", "assistant": "Ассистент"}
    dialog = "\n\n".join(f"{labels.get(m['role'], m['role'])}: {m['content']}" for m in messages)
    parts: list[str] = []
    if context:
        parts.append(context)
    parts.append(f"Диалог:\n\n{dialog}")
    parts.append("Ответьте на последнее сообщение студента.")
    return "\n\n".join(parts)


async def run_tutor_reply(
    provider: Provider,
    model: ModelEntry,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.4,
) -> llm.LlmResult:
    return await llm.chat(provider, model, system_prompt, user_message, temperature=temperature, json_mode=False)
