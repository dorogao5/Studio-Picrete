from app.llm import client as llm
from app.models import Assistant, ModelEntry, Provider
from app.services.contracts import GENERATION_JSON_CONTRACT

FALLBACK_GENERATOR_PROMPT = """Вы — опытный преподаватель и методист высшей школы по дисциплине «{discipline}».
Вы составляете типовые учебные задания: условие, подробное эталонное решение и рубрику оценивания.
Задания должны быть корректными, решаемыми, с реалистичными числами и согласованными единицами измерения.
Формулы записывайте в LaTeX ($...$). Отвечайте только на русском языке.

Ответ — строго JSON по схеме:
{contract}
Никакого текста вне JSON."""


def build_generation_user_message(
    topic: str,
    difficulty: str,
    count: int,
    instructions: str,
    example: str,
    existing_statements: list[str],
) -> str:
    existing = "\n---\n".join(existing_statements[:8]) or "(нет)"
    return f"""Сгенерируйте {count} задач(и).

Тема: {topic or "(на усмотрение, в рамках дисциплины)"}
Сложность: {difficulty}

Инструкции шаблона от преподавателя:
{instructions or "(нет)"}

Пример задачи в нужном стиле:
{example or "(нет)"}

Уже существующие задачи (НЕ повторяйте их сюжеты и числа):
{existing}

Каждая задача: условие + подробное эталонное решение + рубрика с баллами. Ответ строго JSON."""


async def generate_tasks(
    provider: Provider,
    model: ModelEntry,
    assistant: Assistant,
    system_prompt: str | None,
    topic: str,
    difficulty: str,
    count: int,
    instructions: str,
    example: str,
    existing_statements: list[str],
    temperature: float = 0.7,
) -> list[dict]:
    prompt = system_prompt or FALLBACK_GENERATOR_PROMPT.format(
        discipline=assistant.discipline, contract=GENERATION_JSON_CONTRACT
    )
    user_message = build_generation_user_message(topic, difficulty, count, instructions, example, existing_statements)
    result = await llm.chat(provider, model, prompt, user_message, temperature=temperature, json_mode=True)
    parsed = llm.extract_json(result.text)
    tasks = parsed.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise llm.LlmError("Генератор не вернул массив tasks")
    return tasks
