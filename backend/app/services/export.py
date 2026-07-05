from app.models import GeneratedTask
from app.services.validation import extract_numbers


def build_bank_export(
    tasks: list[GeneratedTask], *, source_code: str, source_title: str, version: str
) -> dict:
    groups: dict[str, list[GeneratedTask]] = {}
    order: list[str] = []
    for task in tasks:
        topic = task.topic.strip() or "Без темы"
        if topic not in groups:
            groups[topic] = []
            order.append(topic)
        groups[topic].append(task)
    paragraphs = []
    for index, topic in enumerate(order, start=1):
        paragraphs.append(
            {
                "paragraph": str(index),
                "topic": topic,
                "theory_text": "",
                "tasks": [
                    {"number": f"{index}.{position}", "text": task.statement, "images": [], "answer": task.answer}
                    for position, task in enumerate(groups[topic], start=1)
                ],
            }
        )
    return {
        "source": {"code": source_code, "title": source_title, "version": version},
        "paragraphs": paragraphs,
    }


def build_variants_export(tasks: list[GeneratedTask], tolerance_by_template: dict[str, float]) -> dict:
    items = []
    for task in tasks:
        tolerance_pct = tolerance_by_template.get(task.template_id or "", 0.0)
        numbers = extract_numbers(task.answer)
        answer_tolerance = abs(numbers[-1]) * tolerance_pct / 100 if numbers and tolerance_pct else 0.0
        items.append(
            {
                "title": task.topic or "Задача",
                "content": task.statement,
                "reference_solution": task.reference_solution,
                "reference_answer": task.answer,
                "answer_tolerance": round(answer_tolerance, 10),
                "max_score": task.max_score,
                "rubric": task.rubric,
            }
        )
    return {"tasks": items}
