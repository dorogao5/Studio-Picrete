import re

from app.llm import client as llm
from app.models import ModelEntry, Provider

SOLVER_SYSTEM_PROMPT = """Вы — независимый решатель учебных задач по естественнонаучным дисциплинам.
Решите задачу самостоятельно, с нуля. Используйте ТОЛЬКО справочные данные, приведённые в сообщении;
если каких-то данных не хватает — явно отметьте это в решении, но не подставляйте значения из общих знаний.
Ответ — строго JSON: {"solution": "решение по шагам", "answer": "краткий финальный ответ"}. Никакого текста вне JSON."""

ANSWER_FORMAT_HINTS = {
    "numeric": "число с единицами измерения",
    "formula": "формула",
    "choice": "выбранный вариант ответа",
    "text": "краткий текст",
}

DUPLICATE_THRESHOLD = 0.85

_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", re.IGNORECASE)
_INTEGER_RE = re.compile(r"[-+]?\d+")
_WORD_RE = re.compile(r"\w+")


def normalize_numeric_text(text: str) -> str:
    # Надстрочные степени превращаем в ^-форму ДО общей транслитерации: 10⁻¹⁴ → 10^-14, а не 10-14.
    text = re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+",
        lambda m: "^" + m.group(0).translate(_SUPERSCRIPTS),
        text or "",
    )
    text = text.replace("−", "-")
    # LaTeX: -28{,}7\\,\\text{кДж}, 2\\cdot10^{-5}, H_2O — чистим макросы и индексы до извлечения чисел.
    text = text.replace("\\cdot", "·").replace("\\times", "×")
    text = text.replace("{,}", ",")
    text = re.sub(r"\^\s*\{\s*([-+]?\d+)\s*\}", r"^\1", text)
    text = re.sub(r"\\text\s*\{([^{}]*)\}", r" \1 ", text)
    text = re.sub(r"_\{?\d+\}?", " ", text)
    text = re.sub(r"\\[,;!:]", "", text)
    text = re.sub(r"\\[a-zA-Z]+|\\ ", " ", text)
    text = re.sub(r"(?<=\d)[\u00a0\u2007\u2009\u202f](?=\d)", "", text)
    text = re.sub(r"\s*[·×∙⋅*]\s*10\s*\^?\s*(?=[-+]?\d)", "e", text)
    text = re.sub(r"(?<![\d.,eE])10\s*\^\s*(?=[-+]?\d)", "1e", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return text


def _number_tokens(text: str) -> list[str]:
    return _NUMBER_RE.findall(normalize_numeric_text(text))


def extract_numbers(text: str) -> list[float]:
    values: list[float] = []
    for token in _number_tokens(text):
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values


def _close(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= max(rel * max(abs(a), abs(b)), 1e-9)


def _drop_context_numbers(values: list[float], context_values: list[float]) -> list[float]:
    return [v for v in values if not any(_close(v, c, 1e-6) for c in context_values)]


def compare_answers(reference: str, solver: str, tolerance_pct: float, context: str = "") -> dict:
    ref_text = (reference or "").strip()
    solver_text = (solver or "").strip()
    result: dict = {"verdict": "uncertain", "reference": ref_text, "solver": solver_text}
    if not ref_text or not solver_text:
        return result
    ref_numbers = extract_numbers(ref_text)
    solver_numbers = extract_numbers(solver_text)
    if ref_numbers and solver_numbers:
        rel = tolerance_pct / 100
        # Числа из условия (T=298, табличные значения) — контекст, а не ответ: убираем их с обеих сторон.
        context_values = extract_numbers(context)
        ref_filtered = _drop_context_numbers(ref_numbers, context_values)
        solver_filtered = _drop_context_numbers(solver_numbers, context_values)
        if not ref_filtered or not solver_filtered:
            ref_filtered, solver_filtered = ref_numbers, solver_numbers
        result["reference_number"] = ref_filtered[-1]
        result["solver_number"] = solver_filtered[-1]
        result["verdict"] = (
            "match" if any(_close(a, b, rel) for a in ref_filtered for b in solver_filtered) else "mismatch"
        )
        return result
    ref_norm = " ".join(ref_text.casefold().split())
    solver_norm = " ".join(solver_text.casefold().split())
    if not ref_numbers and not solver_numbers:
        result["verdict"] = "match" if ref_norm == solver_norm else "mismatch"
    else:
        result["verdict"] = "match" if ref_norm == solver_norm else "uncertain"
    return result


def data_check(statement: str, sheets_text: str) -> dict:
    if not (sheets_text or "").strip():
        return {"status": "skipped", "unknown_numbers": []}
    sheet_tokens: set[str] = set()
    sheet_values: list[float] = []
    for token in _number_tokens(sheets_text):
        try:
            sheet_values.append(float(token))
        except ValueError:
            continue
        sheet_tokens.add(token.lower().lstrip("+"))
    unknown: list[str] = []
    for token in _number_tokens(statement):
        try:
            value = float(token)
        except ValueError:
            continue
        # Целые до 1000 и «круглые» десятые — это заданные условия (масса, объём, T), а не табличные данные.
        if _INTEGER_RE.fullmatch(token) and abs(value) < 1000:
            continue
        if re.fullmatch(r"[-+]?\d+\.\d", token) and abs(value) < 100:
            continue
        key = token.lower().lstrip("+")
        if key in sheet_tokens or key in unknown:
            continue
        if any(_close(value, sheet_value, 1e-6) for sheet_value in sheet_values):
            continue
        unknown.append(key)
    return {"status": "warn" if unknown else "ok", "unknown_numbers": unknown[:20]}


def sanity_check(task: dict) -> dict:
    issues: list[str] = []
    statement = str(task.get("statement") or "").strip()
    if len(statement) < 30:
        issues.append("Условие подозрительно короткое (меньше 30 символов)")
    rubric = task.get("rubric") or []
    try:
        max_score = float(task.get("max_score") or 0)
    except (TypeError, ValueError):
        max_score = 0.0
    if not rubric:
        issues.append("Рубрика оценивания пуста")
    else:
        total = 0.0
        for criterion in rubric:
            if not isinstance(criterion, dict):
                continue
            try:
                total += float(criterion.get("max_score") or 0)
            except (TypeError, ValueError):
                continue
        if abs(total - max_score) > 0.01:
            issues.append(f"Сумма баллов рубрики ({total:g}) не совпадает с max_score ({max_score:g})")
    if task.get("answer_format") == "numeric" and not str(task.get("answer") or "").strip():
        issues.append("Для числовой задачи не указан ответ")
    return {"issues": issues}


def dedup_check(statement: str, existing_statements: list[str]) -> dict:
    tokens = set(_WORD_RE.findall((statement or "").lower()))
    numbers = {token.lstrip("+") for token in _number_tokens(statement or "")}
    best = 0.0
    duplicate = False
    if tokens:
        for other in existing_statements:
            other_tokens = set(_WORD_RE.findall((other or "").lower()))
            if not other_tokens:
                continue
            similarity = len(tokens & other_tokens) / len(tokens | other_tokens)
            best = max(best, similarity)
            if similarity <= DUPLICATE_THRESHOLD:
                continue
            # Задачи одного блюпринта похожи текстом по построению — дубликат только при совпадении чисел.
            other_numbers = {token.lstrip("+") for token in _number_tokens(other or "")}
            if not numbers and not other_numbers:
                duplicate = True
            elif numbers | other_numbers:
                overlap = len(numbers & other_numbers) / len(numbers | other_numbers)
                duplicate = duplicate or overlap >= 0.8
    return {"duplicate": duplicate, "similarity": round(best, 2)}


async def solver_check(
    provider: Provider, model: ModelEntry, statement: str, grounding: str, answer_format: str
) -> dict:
    hint = ANSWER_FORMAT_HINTS.get(answer_format, ANSWER_FORMAT_HINTS["text"])
    parts = [f"Задача:\n{statement}"]
    if grounding:
        parts.append(grounding)
    parts.append(f'Поле "answer" — {hint}. Ответ строго JSON {{"solution": "...", "answer": "..."}}.')
    try:
        result = await llm.chat(
            provider, model, SOLVER_SYSTEM_PROMPT, "\n\n".join(parts), temperature=0.0, json_mode=True
        )
        parsed = llm.extract_json(result.text)
    except llm.LlmError as err:
        return {"status": "error", "solution": "", "answer": "", "error": str(err)}
    return {
        "status": "ok",
        "solution": str(parsed.get("solution") or ""),
        "answer": str(parsed.get("answer") or ""),
        "error": "",
    }


async def run_validation(
    *,
    statement: str,
    reference_answer: str,
    rubric: list,
    max_score: float,
    answer_format: str,
    tolerance_pct: float,
    grounding: str,
    sheets_text: str,
    existing_statements: list[str],
    solver_provider: Provider | None = None,
    solver_model: ModelEntry | None = None,
    run_solver: bool = True,
    run_data: bool = True,
) -> dict:
    reasons: list[str] = []

    solver: dict = {"status": "skipped"}
    if run_solver and solver_provider is not None and solver_model is not None:
        solved = await solver_check(solver_provider, solver_model, statement, grounding, answer_format)
        solver = {
            "status": solved["status"],
            "answer": solved["answer"],
            "solution": solved["solution"][:4000],
            "reference_answer": reference_answer,
            "model": f"{solver_provider.name}/{solver_model.model_id}",
            "error": solved["error"],
        }
        if solved["status"] == "error":
            reasons.append(f"Решатель не смог решить задачу: {solved['error']}")
        else:
            compared = compare_answers(reference_answer, solved["answer"], tolerance_pct, context=statement)
            solver["status"] = compared["verdict"]
            if compared["verdict"] == "mismatch":
                reasons.append(
                    f"Решатель получил другой ответ: {solved['answer'] or '(пусто)'} vs {reference_answer or '(пусто)'}"
                )
            elif compared["verdict"] == "uncertain":
                reasons.append(
                    f"Не удалось однозначно сравнить ответы: {solved['answer'] or '(пусто)'} "
                    f"vs {reference_answer or '(пусто)'}"
                )

    data: dict = {"status": "skipped", "unknown_numbers": []}
    if run_data:
        data = data_check(statement, sheets_text)
        if data["unknown_numbers"]:
            reasons.append("Числа не из справочника: " + ", ".join(data["unknown_numbers"][:10]))

    sanity = sanity_check(
        {
            "statement": statement,
            "rubric": rubric,
            "max_score": max_score,
            "answer": reference_answer,
            "answer_format": answer_format,
        }
    )
    reasons.extend(sanity["issues"])

    dedup = dedup_check(statement, existing_statements)
    if dedup["duplicate"]:
        reasons.append(f"Похожа на уже существующую задачу (сходство {round(dedup['similarity'] * 100)}%)")

    needs_review = (
        solver["status"] in ("mismatch", "uncertain", "error")
        or bool(data["unknown_numbers"])
        or bool(sanity["issues"])
        or dedup["duplicate"]
    )
    return {
        "solver": solver,
        "data": data,
        "sanity": sanity,
        "dedup": dedup,
        "verdict": "needs_review" if needs_review else "validated",
        "reasons": reasons,
    }
