from app.models import Assistant


PROFILE_HEADING = "ПРОФИЛЬ КУРСА И ПРЕПОДАВАТЕЛЯ"


def _single_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _list_line(values: list | None) -> str:
    return "; ".join(item for value in (values or []) if (item := _single_line(value)))


def _criterion_line(value: object) -> str:
    if not isinstance(value, dict):
        return _single_line(value)
    name = _single_line(value.get("name")) or "Критерий"
    score = value.get("max_score")
    description = _single_line(value.get("description"))
    parts = [name]
    if score is not None:
        parts.append(f"максимум {score} балла")
    if description:
        parts.append(description)
    return " — ".join(parts)


def build_assistant_profile(assistant: Assistant) -> str:
    """Render current teacher settings as a stable runtime instruction block."""
    lines = [
        PROFILE_HEADING,
        "Это актуальные настройки преподавателя; соблюдайте их во всех режимах ассистента.",
        f"Ассистент: {_single_line(assistant.name)}",
        f"Дисциплина: {_single_line(assistant.discipline)}",
        f"Аудитория: {_single_line(assistant.audience)}",
        f"Язык: {_single_line(assistant.language)}",
    ]
    if description := _single_line(assistant.description):
        lines.append(f"Назначение: {description}")
    if topics := _list_line(assistant.topics):
        lines.append(f"Темы курса: {topics}")
    criteria = [line for value in (assistant.criteria or []) if (line := _criterion_line(value))]
    if criteria:
        lines.append("Критерии оценивания:")
        lines.extend(f"- {line}" for line in criteria)
    if nuances := _list_line(assistant.nuances):
        lines.append(f"Требования преподавателя: {nuances}")
    return "\n".join(lines)


def with_assistant_profile(system_prompt: str, assistant: Assistant | None) -> str:
    if assistant is None:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{build_assistant_profile(assistant)}"
