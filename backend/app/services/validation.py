import re
from dataclasses import dataclass

import snowballstemmer

from app.llm import client as llm
from app.models import ModelEntry, Provider
from app.services.model_policy import current_model_use_policy

_ru_stemmer = snowballstemmer.stemmer("russian")

SOLVER_SYSTEM_PROMPT = """Вы — независимый решатель учебных задач по естественнонаучным дисциплинам.
Решите задачу самостоятельно, с нуля. Используйте ТОЛЬКО справочные данные, приведённые в сообщении;
если каких-то данных не хватает — явно отметьте это в решении, но не подставляйте значения из общих знаний.
В поле answer перечислите ВСЕ величины и выводы, которые требует условие, с названиями, знаками и единицами.
Ответ — строго JSON: {"solution": "решение по шагам", "answer": "полный финальный ответ"}. Никакого текста вне JSON."""

SOLVER_VERIFIER_SYSTEM_PROMPT = """Вы — второй независимый аудитор учебной задачи.
Решите задачу заново, не доверяя предполагаемому ответу и не пытаясь угадать решение первой модели.
Проверьте полноту данных, размерности, знаки, химический и зарядовый баланс. В поле answer перечислите ВСЕ
запрошенные величины и выводы с единицами. Используйте только данные условия и приложенного контекста.
Ответ — строго JSON: {"solution": "независимая проверка по шагам", "answer": "полный финальный ответ"}."""

VALIDATION_POLICY_VERSION = "evidence-gate-v4-blind-cross-check"

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
_UNIT_ALIASES = {
    "дм3": "л",
    "дм^3": "л",
    "mv": "мв",
    "см3": "мл",
    "см^3": "мл",
    "v": "в",
    "моль/дм3": "моль/л",
    "моль/дм^3": "моль/л",
}
_KNOWN_UNITS = {
    "%",
    "°c",
    "атм",
    "бар",
    "mv",
    "v",
    "в",
    "г",
    "г/л",
    "дм3",
    "дм^3",
    "дж",
    "дж/моль",
    "к",
    "кг",
    "кдж",
    "кдж/моль",
    "км",
    "кл",
    "кпа",
    "л",
    "м",
    "м3",
    "м^3",
    "мв",
    "мг",
    "мг/л",
    "мкг",
    "мкг/л",
    "мкл",
    "мкм",
    "мкмоль",
    "мл",
    "мм",
    "ммоль",
    "ммоль/л",
    "моль",
    "моль/дм3",
    "моль/дм^3",
    "моль/кг",
    "моль/л",
    "мпа",
    "нм",
    "па",
    "с",
    "см",
    "см3",
    "см^3",
    "ч",
    "эв",
}
_UNIT_SIGNATURES = {
    "в": ("voltage", 1.0),
    "мв": ("voltage", 1e-3),
}
_UNIT_RE = re.compile(
    r"(?<![a-zа-яё])("
    + "|".join(re.escape(unit) for unit in sorted(_KNOWN_UNITS, key=len, reverse=True))
    + r")(?![a-zа-яё])",
    re.IGNORECASE,
)
_ALTERNATIVE_CONNECTOR_RE = re.compile(r"(?<!\w)(?:или|либо|or)(?!\w)", re.IGNORECASE)
_EQUIVALENT_CONJUNCTION_RE = re.compile(r"(?<!\w)(?:и|and)(?!\w)", re.IGNORECASE)
_ALTERNATIVE_PUNCTUATION_RE = re.compile(r"[\s()\[\]{}<>,.;:|/\\=~≈±+\-–—'\"]+")


@dataclass(frozen=True)
class _NumberOccurrence:
    value: float
    start: int
    end: int
    unit: str | None
    unit_end: int | None
    label: str | None


_EXPLICIT_LABEL_RE = re.compile(r"([a-zа-яёζδφ][\wа-яёζδφ]*)\s*=\s*$", re.IGNORECASE)
_CLAIM_SPLIT_RE = re.compile(r"[;\n.!?]+")
_CLAIM_STOPWORDS = {
    "and",
    "or",
    "а",
    "в",
    "и",
    "или",
    "итог",
    "ответ",
    "равен",
    "равна",
    "равно",
    "составляет",
    "это",
}


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
    normalized = normalize_numeric_text(text)
    return [
        match.group(0)
        for match in _NUMBER_RE.finditer(normalized)
        if match.start() == 0 or normalized[match.start() - 1] != "^"
    ]


def _canonical_unit(value: str) -> str:
    normalized = value.casefold()
    return _UNIT_ALIASES.get(normalized, normalized)


def _unit_after(normalized: str, number_end: int) -> tuple[str | None, int | None]:
    whitespace = re.match(r"\s*", normalized[number_end:])
    unit_start = number_end + (whitespace.end() if whitespace else 0)
    match = _UNIT_RE.match(normalized, unit_start)
    if match is None:
        return None, None
    # Do not treat the prefix of an unknown compound (for example mV/cm) as a
    # standalone voltage. Known compounds are matched whole because _UNIT_RE is
    # ordered longest-first; an immediate operator means the full unit is unknown.
    if re.match(r"\s*[/·×*^]", normalized[match.end() :]):
        return None, None
    return _canonical_unit(match.group(1)), match.end()


def _explicit_label_before(normalized: str, number_start: int) -> str | None:
    segment = re.split(r"[;\n,.!?]", normalized[:number_start])[-1]
    match = _EXPLICIT_LABEL_RE.search(segment)
    return match.group(1).casefold() if match else None


def _number_occurrences(text: str) -> tuple[str, list[_NumberOccurrence]]:
    normalized = normalize_numeric_text(text)
    occurrences: list[_NumberOccurrence] = []
    for match in _NUMBER_RE.finditer(normalized):
        if match.start() > 0 and normalized[match.start() - 1] == "^":
            continue
        try:
            value = float(match.group(0))
        except ValueError:
            continue
        unit, unit_end = _unit_after(normalized, match.end())
        occurrences.append(
            _NumberOccurrence(
                value=value,
                start=match.start(),
                end=match.end(),
                unit=unit,
                unit_end=unit_end,
                label=_explicit_label_before(normalized, match.start()),
            )
        )
    return normalized, occurrences


def _is_direct_numeric_alternative(separator: str) -> bool:
    """True for separators such as ``см (или `` or `` / or `` between two numbers."""

    connectors = list(_ALTERNATIVE_CONNECTOR_RE.finditer(separator))
    if len(connectors) != 1:
        return False
    remainder = _ALTERNATIVE_CONNECTOR_RE.sub(" ", separator)
    remainder = _UNIT_RE.sub(" ", remainder)
    remainder = _ALTERNATIVE_PUNCTUATION_RE.sub("", remainder)
    return not remainder


def _physical_value(occurrence: _NumberOccurrence) -> tuple[str, float] | None:
    signature = _UNIT_SIGNATURES.get(occurrence.unit or "")
    if signature is None:
        return None
    dimension, factor = signature
    return dimension, occurrence.value * factor


def _same_physical_value(left: _NumberOccurrence, right: _NumberOccurrence, rel: float = 1e-9) -> bool:
    left_physical = _physical_value(left)
    right_physical = _physical_value(right)
    if left_physical is None or right_physical is None or left_physical[0] != right_physical[0]:
        return False
    return _close(left_physical[1], right_physical[1], rel)


def _is_equivalent_unit_representation(
    previous: _NumberOccurrence,
    current: _NumberOccurrence,
    separator: str,
    normalized: str,
) -> bool:
    """Recognize compact forms such as ``0.020 V (20 mV)`` or ``0.020 V = 20 mV``."""

    if not _same_physical_value(previous, current):
        return False
    has_parenthesis = "(" in separator or "[" in separator
    has_equality = any(marker in separator for marker in ("=", "≈", "~"))
    has_conjunction = _EQUIVALENT_CONJUNCTION_RE.search(separator) is not None
    if not has_parenthesis and not has_equality and not has_conjunction:
        return False
    remainder = _UNIT_RE.sub(" ", separator, count=1)
    remainder = _EQUIVALENT_CONJUNCTION_RE.sub(" ", remainder)
    remainder = _ALTERNATIVE_PUNCTUATION_RE.sub("", remainder)
    if remainder:
        return False
    if has_parenthesis:
        if current.unit_end is None or re.match(r"\s*[)\]]", normalized[current.unit_end :]) is None:
            return False
    return True


def _number_occurrence_groups(text: str) -> tuple[str, list[list[_NumberOccurrence]]]:
    normalized, occurrences = _number_occurrences(text)
    groups: list[list[_NumberOccurrence]] = []
    previous: _NumberOccurrence | None = None
    for occurrence in occurrences:
        separator = normalized[previous.end : occurrence.start] if previous is not None else ""
        same_group = previous is not None and (
            _is_direct_numeric_alternative(separator)
            or _is_equivalent_unit_representation(previous, occurrence, separator, normalized)
        )
        if same_group:
            groups[-1].append(occurrence)
        else:
            groups.append([occurrence])
        previous = occurrence
    return normalized, groups


def extract_number_groups(text: str) -> list[list[float]]:
    """Extract required outputs, grouping explicit alternatives and equivalent unit forms."""

    _, groups = _number_occurrence_groups(text)
    return [[occurrence.value for occurrence in group] for group in groups]


def extract_numbers(text: str) -> list[float]:
    values: list[float] = []
    for token in _number_tokens(text):
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values


def extract_units(text: str) -> set[str]:
    normalized = normalize_numeric_text(text).casefold()
    normalized = re.sub(r"\^\s*\{?\s*([+-]?\d+)\s*\}?", r"^\1", normalized)
    normalized = re.sub(r"\s*/\s*", "/", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    units = {_canonical_unit(match.group(1)) for match in _UNIT_RE.finditer(normalized)}
    return units


def _close(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= max(rel * max(abs(a), abs(b)), 1e-9)


def _drop_context_numbers(values: list[float], context_values: list[float]) -> list[float]:
    return [v for v in values if not any(_close(v, c, 1e-6) for c in context_values)]


def _drop_context_occurrences(
    occurrences: list[_NumberOccurrence], context_values: list[float]
) -> list[_NumberOccurrence]:
    return [
        occurrence
        for occurrence in occurrences
        if not any(_close(occurrence.value, context, 1e-6) for context in context_values)
    ]


def _drop_context_groups(
    groups: list[list[_NumberOccurrence]], context_values: list[float]
) -> list[list[_NumberOccurrence]]:
    filtered: list[list[_NumberOccurrence]] = []
    for group in groups:
        remaining = _drop_context_occurrences(group, context_values)
        if remaining:
            filtered.append(remaining)
    return filtered


def _occurrences_match(reference: _NumberOccurrence, solver: _NumberOccurrence, rel: float) -> bool:
    numeric_match = _close(reference.value, solver.value, rel) or _same_physical_value(reference, solver, rel)
    if not numeric_match:
        return False
    if reference.label and solver.label and reference.label != solver.label:
        return False
    if reference.unit is None:
        return True
    return reference.unit == solver.unit or _same_physical_value(reference, solver, rel)


def _required_text_claims(text: str) -> list[tuple[str, frozenset[str]]]:
    claims: list[tuple[str, frozenset[str]]] = []
    normalized = normalize_numeric_text(text)
    for raw_clause in _CLAIM_SPLIT_RE.split(normalized):
        clause = raw_clause.strip()
        if not clause or _number_tokens(clause):
            continue
        words = [word.casefold() for word in _WORD_RE.findall(clause)]
        words = [word for word in words if len(word) > 1 and word not in _CLAIM_STOPWORDS]
        stems = frozenset(_ru_stemmer.stemWords(words))
        if stems:
            claims.append((clause, stems))
    return claims


def _unit_has_equivalent_value(
    unit: str,
    references: list[_NumberOccurrence],
    solvers: list[_NumberOccurrence],
    rel: float,
) -> bool:
    return any(
        reference.unit == unit and _same_physical_value(reference, solver, rel)
        for reference in references
        for solver in solvers
    )


def compare_answers(reference: str, solver: str, tolerance_pct: float, context: str = "") -> dict:
    ref_text = (reference or "").strip()
    solver_text = (solver or "").strip()
    result: dict = {"verdict": "uncertain", "reference": ref_text, "solver": solver_text}
    if not ref_text or not solver_text:
        return result
    _, ref_occurrence_groups = _number_occurrence_groups(ref_text)
    _, solver_occurrences = _number_occurrences(solver_text)
    ref_numbers = [occurrence.value for group in ref_occurrence_groups for occurrence in group]
    solver_numbers = [occurrence.value for occurrence in solver_occurrences]
    if ref_numbers and solver_numbers:
        rel = tolerance_pct / 100
        # Числа из условия (T=298, табличные значения) — контекст, а не ответ: убираем их с обеих сторон.
        context_values = extract_numbers(context)
        ref_grouped = _drop_context_groups(ref_occurrence_groups, context_values)
        solver_filtered = _drop_context_occurrences(solver_occurrences, context_values)
        if not ref_grouped or not solver_filtered:
            ref_grouped, solver_filtered = ref_occurrence_groups, solver_occurrences
        ref_filtered = [occurrence.value for group in ref_grouped for occurrence in group]
        solver_filtered_values = [occurrence.value for occurrence in solver_filtered]
        unmatched_solver = set(range(len(solver_filtered)))
        matched: list[tuple[_NumberOccurrence, _NumberOccurrence]] = []
        missing_occurrence_groups: list[list[_NumberOccurrence]] = []
        for reference_group in ref_grouped:
            match: tuple[int, _NumberOccurrence] | None = next(
                (
                    (index, reference_occurrence)
                    for reference_occurrence in reference_group
                    for index in unmatched_solver
                    if _occurrences_match(reference_occurrence, solver_filtered[index], rel)
                ),
                None,
            )
            if match is None:
                missing_occurrence_groups.append(reference_group)
                continue
            match_index, matched_reference = match
            unmatched_solver.remove(match_index)
            matched.append((matched_reference, solver_filtered[match_index]))
        reference_groups = [[occurrence.value for occurrence in group] for group in ref_grouped]
        missing_groups = [[occurrence.value for occurrence in group] for group in missing_occurrence_groups]
        missing: list[float | list[float]] = [group[0] if len(group) == 1 else group for group in missing_groups]
        reference_occurrences = [occurrence for group in ref_grouped for occurrence in group]
        unexpected_solver_numbers = [
            solver_filtered[index].value
            for index in unmatched_solver
            if not any(
                _occurrences_match(reference_occurrence, solver_filtered[index], rel)
                for reference_occurrence in reference_occurrences
            )
        ]
        result.update(
            reference_number=ref_filtered[-1],
            solver_number=solver_filtered_values[-1],
            reference_numbers=ref_filtered,
            reference_number_groups=reference_groups,
            solver_numbers=solver_filtered_values,
            matched_count=len(matched),
            required_count=len(reference_groups),
            missing_reference_numbers=missing,
            missing_reference_groups=missing_groups,
            unexpected_solver_numbers=unexpected_solver_numbers,
        )
        reference_units = extract_units(ref_text)
        solver_units = extract_units(solver_text)
        missing_units = sorted(
            {
                occurrence.unit
                for group in missing_occurrence_groups
                for occurrence in group
                if occurrence.unit is not None
            }
        )
        solver_stems = set(_ru_stemmer.stemWords([word.casefold() for word in _WORD_RE.findall(solver_text)]))
        missing_text_claims = [
            claim for claim, stems in _required_text_claims(ref_text) if not stems.issubset(solver_stems)
        ]
        result.update(
            reference_units=sorted(reference_units),
            solver_units=sorted(solver_units),
            missing_reference_units=missing_units,
            missing_text_claims=missing_text_claims,
        )
        if missing_units or missing_text_claims or unexpected_solver_numbers:
            result["verdict"] = "incomplete" if matched else "mismatch"
        else:
            result["verdict"] = "match" if not missing else ("incomplete" if matched else "mismatch")
        return result
    ref_norm = " ".join(ref_text.casefold().split())
    solver_norm = " ".join(solver_text.casefold().split())
    if ref_norm == solver_norm:
        result["verdict"] = "match"
        return result
    if not ref_numbers and not solver_numbers:
        # Лексическое пересечение полезно как диагностический сигнал, но не доказывает
        # смысловую эквивалентность. Теоретический ответ утверждает преподаватель:
        # одинаковые термины встречаются и в химически противоположных утверждениях.
        ref_stems = set(_ru_stemmer.stemWords(re.findall(r"\w+", ref_text.lower())))
        solver_stems = set(_ru_stemmer.stemWords(re.findall(r"\w+", solver_text.lower())))
        similarity = (
            len(ref_stems & solver_stems) / len(ref_stems | solver_stems) if ref_stems and solver_stems else 0.0
        )
        result["similarity"] = round(similarity, 2)
        result["verdict"] = "mismatch" if similarity < 0.12 else "uncertain"
    else:
        result["verdict"] = "uncertain"
    return result


def _sheet_number_index(sheets_text: str) -> tuple[set[str], list[float]]:
    sheet_tokens: set[str] = set()
    sheet_values: list[float] = []
    for token in _number_tokens(sheets_text):
        try:
            sheet_values.append(float(token))
        except ValueError:
            continue
        sheet_tokens.add(token.lower().lstrip("+"))
    return sheet_tokens, sheet_values


def _unknown_number_tokens(text: str, sheet_tokens: set[str], sheet_values: list[float]) -> list[str]:
    unknown: list[str] = []
    for token in _number_tokens(text):
        try:
            value = float(token)
        except ValueError:
            continue
        key = token.lower().lstrip("+")
        if key in sheet_tokens or key in unknown:
            continue
        if any(_close(value, sheet_value, 1e-6) for sheet_value in sheet_values):
            continue
        unknown.append(key)
    return unknown


def data_check(statement: str, sheets_text: str, data_used: list | None = None) -> dict:
    """Validate source claims without confusing self-contained task inputs with reference data.

    New tasks carry ``data_used`` provenance from the generator. Only values explicitly
    claimed as copied from course sheets must be present in those sheets. The numbers a
    teacher or generator puts directly into a self-contained problem are legitimate task
    inputs and cannot be distinguished from tabular constants by their formatting alone.
    ``None`` preserves the conservative legacy heuristic for older stored tasks.
    """
    if data_used is not None:
        if not data_used:
            return {"status": "ok", "unknown_numbers": [], "unknown_sources": []}
        sheet_tokens, sheet_values = _sheet_number_index(sheets_text)
        unknown_sources: list[str] = []
        claimed_values: list[str] = []
        sheets_casefold = (sheets_text or "").casefold()
        for item in data_used:
            if not isinstance(item, dict):
                continue
            title = str(item.get("sheet_title") or "").strip()
            if title and title.casefold() not in sheets_casefold and title not in unknown_sources:
                unknown_sources.append(title)
            values = item.get("values") or []
            if isinstance(values, list):
                claimed_values.extend(str(value) for value in values)
        unknown = _unknown_number_tokens("\n".join(claimed_values), sheet_tokens, sheet_values)
        return {
            "status": "warn" if unknown or unknown_sources else "ok",
            "unknown_numbers": unknown[:20],
            "unknown_sources": unknown_sources[:20],
        }

    if not (sheets_text or "").strip():
        return {"status": "skipped", "unknown_numbers": [], "unknown_sources": []}
    sheet_tokens, sheet_values = _sheet_number_index(sheets_text)
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
    return {"status": "warn" if unknown else "ok", "unknown_numbers": unknown[:20], "unknown_sources": []}


def sanity_check(task: dict) -> dict:
    issues: list[str] = []
    statement = str(task.get("statement") or "").strip()
    if len(statement) < 30:
        issues.append("Условие подозрительно короткое (меньше 30 символов)")
    if len(str(task.get("reference_solution") or "").strip()) < 20:
        issues.append("Эталонное решение отсутствует или слишком короткое")
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
    if not str(task.get("answer") or "").strip():
        issues.append("Не указан финальный ответ")
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
    provider: Provider,
    model: ModelEntry,
    statement: str,
    grounding: str,
    answer_format: str,
    system_prompt: str = SOLVER_SYSTEM_PROMPT,
) -> dict:
    hint = ANSWER_FORMAT_HINTS.get(answer_format, ANSWER_FORMAT_HINTS["text"])
    parts = [f"Задача:\n{statement}"]
    if grounding:
        parts.append(grounding)
    parts.append(f'Поле "answer" — {hint}. Ответ строго JSON {{"solution": "...", "answer": "..."}}.')
    try:
        result = await llm.chat(provider, model, system_prompt, "\n\n".join(parts), temperature=0.0, json_mode=True)
        parsed = llm.extract_json(result.text)
    except llm.LlmError as err:
        return {
            "status": "error",
            "solution": "",
            "answer": "",
            "error": str(err),
            "duration_ms": 0,
            "tokens_total": None,
        }
    return {
        "status": "ok",
        "solution": str(parsed.get("solution") or ""),
        "answer": str(parsed.get("answer") or ""),
        "error": "",
        "duration_ms": result.duration_ms,
        "tokens_total": result.tokens_total,
    }


def _solver_report(solved: dict, reference_answer: str, model_name: str, comparison: dict | None = None) -> dict:
    return {
        "status": solved["status"] if comparison is None else comparison["verdict"],
        "answer": solved["answer"],
        "solution": solved["solution"][:4000],
        "reference_answer": reference_answer,
        "model": model_name,
        "error": solved["error"],
        "duration_ms": solved.get("duration_ms", 0),
        "tokens_total": solved.get("tokens_total"),
        "comparison": comparison or {},
    }


def _append_solver_reason(reasons: list[str], label: str, report: dict) -> None:
    status = report["status"]
    if status == "error":
        reasons.append(f"{label} не смог решить задачу: {report['error']}")
    elif status == "mismatch":
        reasons.append(
            f"{label} получил другой ответ: {report['answer'] or '(пусто)'} vs "
            f"{report['reference_answer'] or '(пусто)'}"
        )
    elif status == "incomplete":
        comparison = report.get("comparison") or {}
        reasons.append(
            f"{label} вернул не все величины: совпало {comparison.get('matched_count', 0)} "
            f"из {comparison.get('required_count', 0)}"
        )
    elif status == "uncertain":
        reasons.append(
            f"Не удалось однозначно сравнить ответ ({label.lower()}): "
            f"{report['answer'] or '(пусто)'} vs {report['reference_answer'] or '(пусто)'}"
        )


async def run_validation(
    *,
    statement: str,
    reference_solution: str = "",
    reference_answer: str,
    rubric: list,
    max_score: float,
    answer_format: str,
    tolerance_pct: float,
    grounding: str,
    sheets_text: str,
    existing_statements: list[str],
    data_used: list | None = None,
    solver_provider: Provider | None = None,
    solver_model: ModelEntry | None = None,
    run_solver: bool = True,
    run_data: bool = True,
) -> dict:
    reasons: list[str] = []

    data: dict = {"status": "skipped", "unknown_numbers": [], "unknown_sources": []}
    if run_data:
        # The generator can cite either a selected ReferenceSheet or a retrieved KB chunk.
        # Validate against the exact grounding it actually saw; ``sheets_text`` contains
        # only ReferenceSheets and would falsely reject a real KB heading. Falling back
        # preserves revalidation for older/manual calls that have no grounding block.
        provenance_text = grounding if (grounding or "").strip() else sheets_text
        data = data_check(statement, provenance_text, data_used=data_used)
        if data["unknown_numbers"]:
            reasons.append("Числа не из справочника: " + ", ".join(data["unknown_numbers"][:10]))
        if data["unknown_sources"]:
            reasons.append("Неизвестные источники данных: " + ", ".join(data["unknown_sources"][:10]))
    else:
        reasons.append("Проверка происхождения данных отключена — автоматический допуск невозможен")

    sanity = sanity_check(
        {
            "statement": statement,
            "reference_solution": reference_solution,
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

    hard_fail = bool(data["unknown_numbers"] or data["unknown_sources"] or sanity["issues"] or dedup["duplicate"])
    solver: dict = {"status": "skipped"}
    verifier: dict = {"status": "skipped"}
    model_use = current_model_use_policy().classify(solver_model)
    advisory_only = not model_use.decision_capable
    if not run_solver:
        reasons.append("Семантическая проверка решателем отключена — требуется решение преподавателя")
    if run_solver and not hard_fail:
        if solver_provider is None or solver_model is None:
            solver = {"status": "error", "error": "Модель-решатель не настроена"}
            reasons.append("Модель-решатель не настроена")
        else:
            model_name = f"{solver_provider.name}/{solver_model.model_id}"
            # Проверяем ровно то условие, которое увидит студент. Скрытый grounding
            # используется для аудита источников, но не должен делать неполную задачу решаемой.
            solved = await solver_check(solver_provider, solver_model, statement, "", answer_format)
            compared = (
                compare_answers(reference_answer, solved["answer"], tolerance_pct, context=statement)
                if solved["status"] != "error"
                else None
            )
            solver = _solver_report(solved, reference_answer, model_name, compared)
            _append_solver_reason(reasons, "Основной решатель", solver)

            if advisory_only:
                reasons.append(f"{model_use.reason}: {solver_model.model_id}. Задача не подтверждена автоматически")
            else:
                verified = await solver_check(
                    solver_provider,
                    solver_model,
                    statement,
                    "",
                    answer_format,
                    system_prompt=SOLVER_VERIFIER_SYSTEM_PROMPT,
                )
                verified_comparison = (
                    compare_answers(reference_answer, verified["answer"], tolerance_pct, context=statement)
                    if verified["status"] != "error"
                    else None
                )
                verifier = _solver_report(verified, reference_answer, model_name, verified_comparison)
                _append_solver_reason(reasons, "Независимый аудитор", verifier)

    cross_comparison = (
        compare_answers(solver.get("answer", ""), verifier.get("answer", ""), tolerance_pct, context=statement)
        if solver.get("status") == "match" and verifier.get("status") == "match"
        else {"verdict": "skipped"}
    )
    if cross_comparison["verdict"] != "match":
        reasons.append("Контрольные решения не совпали друг с другом полностью")

    reference_solution_check = compare_answers(
        reference_answer,
        reference_solution,
        tolerance_pct,
        context=statement,
    )
    if reference_solution_check["verdict"] != "match":
        reasons.append("Эталонное решение не содержит полный финальный ответ")

    semantic_validation_complete = (
        run_solver
        and model_use.decision_capable
        and solver["status"] == "match"
        and verifier["status"] == "match"
        and cross_comparison["verdict"] == "match"
        and reference_solution_check["verdict"] == "match"
    )
    needs_review = (
        not semantic_validation_complete
        or not run_data
        or data["status"] != "ok"
        or solver["status"] in ("mismatch", "incomplete", "uncertain", "error")
        or verifier["status"] in ("mismatch", "incomplete", "uncertain", "error")
        or bool(data["unknown_numbers"])
        or bool(data["unknown_sources"])
        or bool(sanity["issues"])
        or dedup["duplicate"]
    )
    return {
        "policy_version": VALIDATION_POLICY_VERSION,
        "model_policy": model_use.as_dict(),
        "solver": solver,
        "verifier": verifier,
        "cross_comparison": cross_comparison,
        "reference_solution_check": reference_solution_check,
        "data": data,
        "sanity": sanity,
        "dedup": dedup,
        "answer_format": answer_format,
        "verdict": "needs_review" if needs_review else "validated",
        "reasons": reasons,
    }
