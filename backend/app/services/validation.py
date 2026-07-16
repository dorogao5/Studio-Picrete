import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import snowballstemmer

from app.llm import client as llm
from app.models import ModelEntry, Provider
from app.services.chemistry_facts import chemistry_admission_evidence, normalize_chemistry_facts
from app.services.chemistry_validation import CHEMISTRY_VALIDATION_VERSION
from app.services.chemistry_units import known_unit_spellings, unit_definition
from app.services.contracts import CHEMISTRY_FACTS_GUIDE
from app.services.model_policy import current_model_use_policy
from app.services.task_evidence import (
    build_task_content_fingerprint,
    normalize_validation_config,
    task_evidence_digest,
)

_ru_stemmer = snowballstemmer.stemmer("russian")

SOLVER_SYSTEM_PROMPT = """Вы — независимый решатель учебных задач по естественнонаучным дисциплинам.
Решите задачу самостоятельно, с нуля. Используйте ТОЛЬКО справочные данные, приведённые в сообщении;
если каких-то данных не хватает — явно отметьте это в решении, но не подставляйте значения из общих знаний.
В поле answer перечислите ВСЕ величины и выводы, которые требует условие, с названиями, знаками и единицами.
Если условие разбито на пункты, поле answer обязано повторить каждый пункт по порядку, включая полуреакции,
уравнения и качественные выводы; не заменяйте запрошенный пункт ссылкой на поле solution.
Ответ — строго JSON: {"solution": "решение по шагам", "answer": "полный финальный ответ"}. Никакого текста вне JSON."""

SOLVER_VERIFIER_SYSTEM_PROMPT = """Вы — второй независимый аудитор учебной задачи.
Решите задачу заново, не доверяя предполагаемому ответу и не пытаясь угадать решение первой модели.
Проверьте полноту данных, размерности, знаки, химический и зарядовый баланс. В поле answer перечислите ВСЕ
запрошенные величины и выводы с единицами. Для многочастной задачи повторите в answer каждый пункт по порядку,
включая полуреакции и уравнения. Используйте только данные условия и приложенного контекста.
Ответ — строго JSON: {"solution": "независимая проверка по шагам", "answer": "полный финальный ответ"}."""

SOLVER_CRITIC_SYSTEM_PROMPT = """Вы — строгий предметный редактор университетских задач по химии.
Вы не решаете задачу в третий раз и не голосуете за большинство. Проверьте доказательства ниже на внутреннюю
согласованность: самодостаточность условия, соответствие эталонного решения финальному ответу, независимость и
полноту двух контрольных решений, размерности, знаки, атомный/зарядовый баланс и явно указанные ограничения модели.
Отдельно установите семантическое следование: действительно ли полный вывод основного решателя следует из эталона
и действительно ли полный вывод независимого аудитора следует из эталона. Совпадение терминов или общий сюжет не
считаются доказательством. Для формул проверьте эквивалентность, область применимости, знаки и все множители.
Считайте solution и answer одного решателя единым пакетом доказательств: пропущенный в answer подпункт допустим
только если он явно и однозначно дан в solution; любое противоречие между answer и solution означает verdict="fail".
Отдельно проверьте, что structured chemistry_facts не содержат чисел, формул, коэффициентов или допущений,
которых нет в условии, эталонном решении либо финальном ответе, и что выбранный блок относится к этой задаче.
Не используйте внешние табличные значения. Любая конкретная необъяснённая проблема означает verdict="fail".
Ответ — строго JSON:
{"verdict":"pass|fail","checks":{"statement_self_contained":true,"reference_consistent":true,
"solver_matches_reference":true,"verifier_matches_reference":true,"solver_agreement":true,
"units_and_chemistry_consistent":true,"structured_facts_grounded":true},
"issues":["конкретная проблема"]}.
Никакого текста вне JSON."""

CHEMISTRY_FACT_EXTRACTOR_SYSTEM_PROMPT = """Вы — аккуратный структурировщик доказательств химического расчёта.
Вы не исправляете и не дополняете задачу, не решаете её заново и не используете внешние константы. Извлеките
только явно записанные в условии, эталонном решении и финальном ответе величины, уравнения, коэффициенты и
оговорки применимости. Числа и единицы копируйте точно; неизвестное поле пропускайте. Верните строго JSON
{"facts": {}} по приложенной схеме. Никакого текста вне JSON."""

VALIDATION_POLICY_VERSION = "evidence-gate-v13-cross-answer-lexical-audit"

CRITIC_REQUIRED_CHECKS = frozenset(
    {
        "statement_self_contained",
        "reference_consistent",
        "solver_matches_reference",
        "verifier_matches_reference",
        "solver_agreement",
        "structured_facts_grounded",
        "units_and_chemistry_consistent",
    }
)
SEMANTIC_ENTAILMENT_ANSWER_FORMATS = frozenset({"formula", "text"})
SEMANTIC_ENTAILMENT_BASIS = "subject_critic_semantic_entailment"
SOLUTION_BACKED_ENTAILMENT_BASIS = "solution_backed_subject_critic"
SOLVER_EVIDENCE_CHAR_LIMIT = 4000
DETERMINISTIC_NUMERIC_BASIS = "deterministic_numeric_tolerance"
NUMERIC_RELATION_CHECKS = frozenset(
    {
        "solver_matches_reference",
        "verifier_matches_reference",
        "solver_agreement",
    }
)

ANSWER_FORMAT_HINTS = {
    "numeric": "число с единицами измерения",
    "formula": "формула",
    "choice": "выбранный вариант ответа",
    "text": "краткий текст",
}

DUPLICATE_THRESHOLD = 0.85

_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_NUMBER_RE = re.compile(r"(?<![a-zа-яё_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", re.IGNORECASE)
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
_KNOWN_UNITS = set(known_unit_spellings()) | {
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
_UNIT_RE = re.compile(
    r"(?<![a-zа-яё])("
    + "|".join(re.escape(unit) for unit in sorted(_KNOWN_UNITS, key=len, reverse=True))
    + r")(?![a-zа-яё0-9])",
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


_LABEL_TOKEN_PATTERN = r"[a-zа-яёζδφω][\wа-яёζδφω]*(?:\s*\([^()]+\))?"
_EXPLICIT_LABEL_RE = re.compile(rf"({_LABEL_TOKEN_PATTERN})\s*=\s*$", re.IGNORECASE)
_CHAINED_EQUAL_LABELS_RE = re.compile(
    rf"({_LABEL_TOKEN_PATTERN})\s*=\s*({_LABEL_TOKEN_PATTERN})\s*=\s*$",
    re.IGNORECASE,
)
_UNSAFE_CHAIN_PREFIX_RE = re.compile(r"[=+\-*/^<>~≈→←↔⇌()\[\]{}]|\d")
_CHEMICAL_FORMULA_PATTERN = r"(?:[A-Z][a-z]?\d*|\((?:[A-Z][a-z]?\d*)+\)\d*)+"
_NAMED_MASS_FRACTION_RE = re.compile(
    rf"(?<!\w)(?i:массов(?:ая|ую)\s+дол(?:я|ю))\s+(?P<formula>{_CHEMICAL_FORMULA_PATTERN})\s*=\s*$",
)
_NAMED_MASS_RE = re.compile(
    rf"(?<!\w)(?i:масс(?:а|у))\s+(?P<formula>{_CHEMICAL_FORMULA_PATTERN})\s*=\s*$",
)
_NAMED_ANALYTE_MASS_FRACTION_RE = re.compile(
    r"(?<!\w)(?i:массов(?:ая|ую)\s+дол(?:я|ю))\s+(?P<name>[а-яё-]+)"
    r"(?:\s+(?:в|из)\s+[а-яё-]+(?:\s+[а-яё-]+){0,2})?\s*=\s*$",
    re.IGNORECASE,
)
_NAMED_ANALYTE_MASS_RE = re.compile(
    r"(?<!\w)(?i:масс(?:а|у))\s+(?P<name>[а-яё-]+)"
    r"(?:\s+(?:в|из)\s+[а-яё-]+(?:\s+[а-яё-]+){0,2})?\s*=\s*$",
    re.IGNORECASE,
)
# Controlled aliases for analyte names that DeepSeek commonly writes in prose
# instead of conventional m(X)/w(X) notation.  Unknown nouns intentionally do
# not become quantity aliases: ``масса образца`` must not silently match
# ``m_sample``.  Both nominative and the genitive required after ``масса`` are
# listed so the rule is deterministic and independent of a morphological model.
_RUSSIAN_ELEMENT_ALIASES = {
    "алюминий": "Al",
    "алюминия": "Al",
    "барий": "Ba",
    "бария": "Ba",
    "бор": "B",
    "бора": "B",
    "бром": "Br",
    "брома": "Br",
    "ванадий": "V",
    "ванадия": "V",
    "водород": "H",
    "водорода": "H",
    "вольфрам": "W",
    "вольфрама": "W",
    "железо": "Fe",
    "железа": "Fe",
    "золото": "Au",
    "золота": "Au",
    "йод": "I",
    "йода": "I",
    "кадмий": "Cd",
    "кадмия": "Cd",
    "калий": "K",
    "калия": "K",
    "кальций": "Ca",
    "кальция": "Ca",
    "кобальт": "Co",
    "кобальта": "Co",
    "кремний": "Si",
    "кремния": "Si",
    "литий": "Li",
    "лития": "Li",
    "магний": "Mg",
    "магния": "Mg",
    "марганец": "Mn",
    "марганца": "Mn",
    "медь": "Cu",
    "меди": "Cu",
    "молибден": "Mo",
    "молибдена": "Mo",
    "мышьяк": "As",
    "мышьяка": "As",
    "натрий": "Na",
    "натрия": "Na",
    "никель": "Ni",
    "никеля": "Ni",
    "олово": "Sn",
    "олова": "Sn",
    "палладий": "Pd",
    "палладия": "Pd",
    "платина": "Pt",
    "платины": "Pt",
    "ртуть": "Hg",
    "ртути": "Hg",
    "свинец": "Pb",
    "свинца": "Pb",
    "селен": "Se",
    "селена": "Se",
    "сера": "S",
    "серы": "S",
    "серебро": "Ag",
    "серебра": "Ag",
    "стронций": "Sr",
    "стронция": "Sr",
    "сурьма": "Sb",
    "сурьмы": "Sb",
    "титан": "Ti",
    "титана": "Ti",
    "углерод": "C",
    "углерода": "C",
    "фосфор": "P",
    "фосфора": "P",
    "фтор": "F",
    "фтора": "F",
    "хлор": "Cl",
    "хлора": "Cl",
    "хром": "Cr",
    "хрома": "Cr",
    "цинк": "Zn",
    "цинка": "Zn",
}
_ELEMENT_SYMBOLS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu "
    "Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg "
    "Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split()
)
_CLAIM_SPLIT_RE = re.compile(r"[;\n.!?]+")
_OUTPUT_CUE_RE = re.compile(
    r"(?<!\w)(?:ответ|итог|результат|получаем|получен[оаы]?|требуется|нужн[оа]|составляет|равен|равна|равно)(?!\w)",
    re.IGNORECASE,
)
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
    # Ordered-list markers describe answer structure, not measured values.
    # Strip ``1.`` / ``2)`` only when followed by whitespace, preserving
    # decimals, coefficients and oxidation states.
    text = re.sub(r"(^|\s)\d{1,2}[.)](?=\s+\S)", r"\1", text, flags=re.MULTILINE)
    # LaTeX: -28{,}7\\,\\text{кДж}, 2\\cdot10^{-5}, H_2O — чистим макросы и индексы до извлечения чисел.
    text = text.replace("\\cdot", "·").replace("\\times", "×")
    text = text.replace("{,}", ",")
    # Math delimiters are presentation only. In common model output they split a
    # value from its unit (``99.7$ м$^2$/г``), which must still be parsed as one
    # measurement. Escaped percent signs are units rather than LaTeX commands.
    text = text.replace("$", "")
    text = re.sub(r"\\[()\[\]]", "", text)
    text = text.replace(r"\%", "%")
    text = re.sub(r"\^\s*\{\s*([-+]?\d+)\s*\}", r"^\1", text)
    # Preserve textual subscripts as labels before unwrapping LaTeX text macros:
    # S_{\text{уд}}, w_{\mathrm{Al}} -> S_уд, w_Al.
    text = re.sub(
        r"_\s*\{\s*\\(?:text|mathrm)\s*\{([^{}]*)\}\s*\}",
        r"_\1",
        text,
    )
    text = re.sub(r"_\s*\{\s*([a-zA-Zа-яА-ЯёЁ]+)\s*\}", r"_\1", text)
    text = re.sub(r"\\(?:text|mathrm|operatorname)\s*\{([^{}]*)\}", r" \1 ", text)
    # ``~`` is commonly used as a non-breaking space before a LaTeX unit.
    # Normalize only the number-to-unit form so approximate-value separators
    # remain available to the equivalent-representation parser.
    text = re.sub(r"(?<=\d)~(?=\s*(?:[%a-zA-Zа-яА-ЯёЁ]))", " ", text)
    # Keep numeric indices inside quantity identifiers. ``_`` prevents them
    # from becoming answer numbers, while preserving enough information to
    # bind LaTeX ``c_2`` to Unicode ``c₂`` later.
    text = re.sub(r"_\{?(\d+)\}?", r"_\1", text)
    text = re.sub(r"\\[,;!:]", "", text)
    text = re.sub(r"\\[a-zA-Z]+|\\ ", " ", text)
    text = re.sub(r"\s*\^\s*", "^", text)
    text = re.sub(r"\s*/\s*", "/", text)
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


def _canonical_quantity_label(value: str) -> str:
    translated = value.translate(_SUBSCRIPTS).strip()
    mass_fraction_omega = (
        translated == "ω"
        or re.fullmatch(r"ω\s*\(\s*[A-Z][a-z]?\s*\)", translated) is not None
        or re.fullmatch(r"ω_[A-Z][a-z]?", translated) is not None
    )
    candidate = translated.casefold()
    candidate = re.sub(r"[_\s(){}]", "", candidate)
    # A superscript charge marker is presentation, not part of the quantity
    # identity: n(e^-) and n(e-) both denote the electron amount.  Limit this
    # normalization to signs so mathematical powers such as x^2 stay distinct.
    candidate = re.sub(r"\^(?=[+-])", "", candidate)
    # Latin w and Greek omega are the two conventional notations for mass
    # fraction. Restrict the alias to plain omega or omega(element), so indexed
    # angular frequencies such as omega_0 do not collapse into a w-label.
    if mass_fraction_omega:
        candidate = "w" + candidate[1:]
    return candidate


def _named_quantity_label(segment: str) -> str | None:
    """Bind a small set of unambiguous Russian quantity names to symbols.

    Models often render ``m_MgO`` as ``масса MgO`` and ``w_MgO`` as
    ``массовая доля MgO``.  The chemical formula is mandatory and must be the
    final token before ``=``; free prose such as ``масса образца`` therefore
    cannot silently acquire a reference label.
    """

    for pattern, symbol in ((_NAMED_MASS_FRACTION_RE, "w"), (_NAMED_MASS_RE, "m")):
        match = pattern.search(segment)
        if match is None:
            continue
        formula = match.group("formula")
        if all(element in _ELEMENT_SYMBOLS for element in re.findall(r"[A-Z][a-z]?", formula)):
            return _canonical_quantity_label(f"{symbol}({formula})")
    for pattern, symbol in (
        (_NAMED_ANALYTE_MASS_FRACTION_RE, "w"),
        (_NAMED_ANALYTE_MASS_RE, "m"),
    ):
        match = pattern.search(segment)
        if match is None:
            continue
        formula = _RUSSIAN_ELEMENT_ALIASES.get(match.group("name").casefold())
        if formula is not None:
            return _canonical_quantity_label(f"{symbol}({formula})")
    return None


def _explicit_labels_before(normalized: str, number_start: int) -> tuple[str, ...]:
    segment = re.split(r"[;\n,.!?]", normalized[:number_start])[-1]
    named_quantity = _named_quantity_label(segment)
    if named_quantity is not None:
        return (named_quantity,)
    chained = _CHAINED_EQUAL_LABELS_RE.search(segment)
    if chained is not None and _UNSAFE_CHAIN_PREFIX_RE.search(segment[: chained.start()]) is None:
        raw_labels = chained.groups()
    else:
        explicit = _EXPLICIT_LABEL_RE.search(segment)
        if explicit is None:
            return ()
        raw_labels = (explicit.group(1),)
    labels: list[str] = []
    for raw_label in raw_labels:
        candidate = _canonical_quantity_label(raw_label)
        # In ``... / 100 mL = 0.010 mol/L`` the token before ``=`` is a
        # denominator unit, not the label of the result. A chained equality is
        # expanded only when every term is a real quantity label.
        if unit_definition(candidate) is not None:
            return ()
        if candidate not in labels:
            labels.append(candidate)
    return tuple(labels)


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
        labels: tuple[str | None, ...] = _explicit_labels_before(normalized, match.start()) or (None,)
        occurrences.extend(
            _NumberOccurrence(
                value=value,
                start=match.start(),
                end=match.end(),
                unit=unit,
                unit_end=unit_end,
                label=label,
            )
            for label in labels
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
    definition = unit_definition(occurrence.unit or "")
    if definition is None:
        return None
    return definition.dimension.value, occurrence.value * definition.factor_to_si + definition.offset_to_si


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


def _significant_digits(token: str) -> int | None:
    """Return explicit significant digits, rejecting ambiguous trailing-zero integers."""

    unsigned = token.strip().lower().lstrip("+-")
    mantissa = unsigned.split("e", 1)[0]
    digits = mantissa.replace(".", "").lstrip("0")
    if not digits:
        return None
    if "." not in mantissa and "e" not in unsigned and mantissa.endswith("0"):
        return None
    return len(digits)


def _is_safe_rounded_value(rounded: _NumberOccurrence, exact: _NumberOccurrence, normalized: str) -> bool:
    rounded_token = normalized[rounded.start : rounded.end]
    exact_token = normalized[exact.start : exact.end]
    rounded_digits = _significant_digits(rounded_token)
    exact_digits = _significant_digits(exact_token)
    if rounded_digits is None or exact_digits is None or rounded_digits >= exact_digits or exact.value == 0:
        return False
    try:
        exact_decimal = Decimal(exact_token)
        rounded_decimal = Decimal(rounded_token)
        quantum = Decimal(1).scaleb(exact_decimal.copy_abs().adjusted() - rounded_digits + 1)
        return exact_decimal.quantize(quantum, rounding=ROUND_HALF_UP) == rounded_decimal
    except (InvalidOperation, ValueError):
        return False


def _is_parenthetical_same_quantity(
    left: _NumberOccurrence,
    right: _NumberOccurrence,
    normalized: str,
) -> bool:
    """Recognize a compact ``value unit (value unit)`` restatement only."""

    first, second = sorted((left, right), key=lambda occurrence: occurrence.start)
    if first.unit is None or second.unit is None or first.unit != second.unit:
        return False
    if first.label is not None and second.label is not None and first.label != second.label:
        return False
    if first.unit_end is None or second.unit_end is None:
        return False
    opening = normalized[first.unit_end : second.start]
    if re.fullmatch(r"\s*[\[(]\s*", opening) is None:
        return False
    return re.match(r"\s*[)\]]", normalized[second.unit_end :]) is not None


def _is_harmless_rounded_duplicate(
    candidate: _NumberOccurrence,
    matched: _NumberOccurrence,
    reference: _NumberOccurrence,
    normalized: str,
) -> bool:
    """Allow only an explicitly coarser parenthetical rendering of one result."""

    if not _is_parenthetical_same_quantity(candidate, matched, normalized):
        return False
    inherited_label = candidate.label or matched.label
    if inherited_label is not None and reference.label is not None and inherited_label != reference.label:
        return False
    return _is_safe_rounded_value(candidate, matched, normalized)


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
    _, occurrences = _number_occurrences(text)
    return {occurrence.unit for occurrence in occurrences if occurrence.unit is not None}


def _close(a: float, b: float, rel: float) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-15)


def _is_explicit_output_occurrence(occurrence: _NumberOccurrence, normalized: str) -> bool:
    if occurrence.label is not None:
        return True
    clause_start = max(normalized.rfind(separator, 0, occurrence.start) for separator in (";", "\n", ".", "!", "?"))
    prefix = normalized[clause_start + 1 : occurrence.start]
    return _OUTPUT_CUE_RE.search(prefix) is not None


def _drop_reference_context_occurrences(
    occurrences: list[_NumberOccurrence], context_values: list[float], normalized: str
) -> list[_NumberOccurrence]:
    return [
        occurrence
        for occurrence in occurrences
        if _is_explicit_output_occurrence(occurrence, normalized)
        or not any(_close(occurrence.value, context, 1e-6) for context in context_values)
    ]


def _drop_solver_context_occurrences(
    occurrences: list[_NumberOccurrence],
    context_values: list[float],
    references: list[_NumberOccurrence],
    rel: float,
) -> list[_NumberOccurrence]:
    return [
        occurrence
        for occurrence in occurrences
        if not any(_close(occurrence.value, context, 1e-6) for context in context_values)
        or any(_occurrences_match(reference, occurrence, rel) for reference in references)
    ]


def _drop_context_groups(
    groups: list[list[_NumberOccurrence]], context_values: list[float], normalized: str
) -> list[list[_NumberOccurrence]]:
    filtered: list[list[_NumberOccurrence]] = []
    for group in groups:
        remaining = _drop_reference_context_occurrences(group, context_values, normalized)
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
        # Formula subscripts are typography, not lexical content: ``SO₃`` and
        # ``SO3`` must contribute the same claim token.  Keep this conversion
        # local to claim matching so chemical subscripts never become numeric
        # answer occurrences.
        words = [word.casefold().translate(_SUBSCRIPTS) for word in _WORD_RE.findall(clause)]
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


def compare_answers(
    reference: str,
    solver: str,
    tolerance_pct: float,
    context: str = "",
    *,
    allow_extra_numbers: bool = False,
) -> dict:
    ref_text = (reference or "").strip()
    solver_text = (solver or "").strip()
    result: dict = {"verdict": "uncertain", "reference": ref_text, "solver": solver_text}
    if not ref_text or not solver_text:
        return result
    ref_normalized, ref_occurrence_groups = _number_occurrence_groups(ref_text)
    solver_normalized, solver_occurrences = _number_occurrences(solver_text)
    ref_numbers = [occurrence.value for group in ref_occurrence_groups for occurrence in group]
    solver_numbers = [occurrence.value for occurrence in solver_occurrences]
    if ref_numbers and solver_numbers:
        rel = tolerance_pct / 100
        # Числа из условия (T=298, табличные значения) — контекст, а не ответ: убираем их с обеих сторон.
        context_values = extract_numbers(context)
        ref_grouped = _drop_context_groups(ref_occurrence_groups, context_values, ref_normalized)
        required_occurrences = [occurrence for group in ref_grouped for occurrence in group]
        solver_filtered = _drop_solver_context_occurrences(
            solver_occurrences,
            context_values,
            required_occurrences,
            rel,
        )
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
        rounded_solver_duplicate_indexes = {
            index
            for index in unmatched_solver
            if any(
                _is_harmless_rounded_duplicate(
                    solver_filtered[index],
                    matched_solver_occurrence,
                    matched_reference_occurrence,
                    solver_normalized,
                )
                for matched_reference_occurrence, matched_solver_occurrence in matched
            )
        }
        rounded_solver_duplicates = [solver_filtered[index].value for index in sorted(rounded_solver_duplicate_indexes)]
        unexpected_solver_numbers = [
            solver_filtered[index].value
            for index in unmatched_solver
            if not any(
                _occurrences_match(reference_occurrence, solver_filtered[index], rel)
                for reference_occurrence in reference_occurrences
            )
            and index not in rounded_solver_duplicate_indexes
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
            rounded_solver_duplicates=rounded_solver_duplicates,
            extra_numbers_allowed=allow_extra_numbers,
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
        solver_stems = set(
            _ru_stemmer.stemWords(
                [word.casefold().translate(_SUBSCRIPTS) for word in _WORD_RE.findall(solver_text)]
            )
        )
        missing_text_claims = [
            claim for claim, stems in _required_text_claims(ref_text) if not stems.issubset(solver_stems)
        ]
        result.update(
            reference_units=sorted(reference_units),
            solver_units=sorted(solver_units),
            missing_reference_units=missing_units,
            missing_text_claims=missing_text_claims,
        )
        if missing_units or missing_text_claims or (unexpected_solver_numbers and not allow_extra_numbers):
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
        if not isinstance(data_used, list):
            return {
                "status": "invalid",
                "unknown_numbers": [],
                "unknown_sources": [],
                "invalid_entries": ["data_used должен быть массивом"],
            }
        if not data_used:
            return {"status": "ok", "unknown_numbers": [], "unknown_sources": []}
        sheet_tokens, sheet_values = _sheet_number_index(sheets_text)
        unknown_sources: list[str] = []
        claimed_values: list[str] = []
        sheets_casefold = (sheets_text or "").casefold()
        invalid_entries: list[str] = []
        for index, item in enumerate(data_used):
            if not isinstance(item, dict):
                invalid_entries.append(f"data_used[{index}] должен быть объектом")
                continue
            title = str(item.get("sheet_title") or "").strip()
            values = item.get("values")
            if not title:
                invalid_entries.append(f"data_used[{index}].sheet_title не задан")
            if not isinstance(values, list) or not values or any(not str(value).strip() for value in values):
                invalid_entries.append(f"data_used[{index}].values должен быть непустым массивом")
                values = []
            title_heading = re.compile(
                rf"(?m)^(?:##?\#?\s+)?{re.escape(title)}(?:\s|\(|\[|—|$)",
                re.IGNORECASE,
            )
            if title and title_heading.search(sheets_casefold) is None and title not in unknown_sources:
                unknown_sources.append(title)
            claimed_values.extend(str(value) for value in values)
        unknown = _unknown_number_tokens("\n".join(claimed_values), sheet_tokens, sheet_values)
        return {
            "status": "invalid" if invalid_entries else ("warn" if unknown or unknown_sources else "ok"),
            "unknown_numbers": unknown[:20],
            "unknown_sources": unknown_sources[:20],
            **({"invalid_entries": invalid_entries[:20]} if invalid_entries else {}),
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


_GROUNDING_SHEET_KIND_LABELS = frozenset({"Таблица данных", "Глоссарий", "Обозначения", "Формулы", "Справка"})
_GROUNDING_AUTHORITY_LABELS = {
    "course_policy": "правила курса",
    "course_lecture": "материал курса",
    "reference": "справочный источник",
}


def _source_title_key(value: object) -> str:
    """Normalize only casing and whitespace; punctuation remains significant."""
    return " ".join(str(value or "").strip().casefold().split())


def _grounding_heading_keys(provenance_text: str) -> set[str]:
    return {_source_title_key(match.group(1)) for match in re.finditer(r"(?m)^###\s+(.+?)\s*$", provenance_text or "")}


def _resolve_lineage_sheet(
    title: str,
    sheets: list[dict],
    by_title: dict[str, dict],
    grounding_headings: set[str],
) -> dict | None:
    title_key = _source_title_key(title)
    sheet = by_title.get(title_key)
    if sheet is not None:
        return sheet

    # A model can copy the Markdown marker together with a KB heading. Strip
    # exactly the platform's level-three marker only when the remaining title
    # is both a real provenance heading and an exact trusted metadata title.
    markdown_heading = re.fullmatch(r"###\s+(.+)", title.strip())
    if markdown_heading is not None:
        unmarked_key = _source_title_key(markdown_heading.group(1))
        if unmarked_key in grounding_headings:
            sheet = by_title.get(unmarked_key)
            if sheet is not None:
                return sheet

    # ``build_grounding_block`` renders a canonical sheet as
    # ``<title> (<kind label>, <authority label>)``. The generator sees and
    # correctly copies that complete heading, while the frozen lineage record
    # deliberately keeps the undecorated database title. Accept only this
    # finite, platform-produced alias and only when that exact heading was in
    # the context. This is not substring/fuzzy title matching.
    if title_key not in grounding_headings:
        return None
    for candidate in sheets:
        authority_label = _GROUNDING_AUTHORITY_LABELS.get(str(candidate.get("source_authority") or ""))
        if authority_label is None:
            continue
        base_title = str(candidate.get("title") or "").strip()
        for kind_label in _GROUNDING_SHEET_KIND_LABELS:
            alias = f"{base_title} ({kind_label}, {authority_label})"
            if title_key == _source_title_key(alias):
                return candidate
    return None


def source_lineage_check(
    data_used: list | None,
    grounding_sheets: list | None,
    provenance_text: str = "",
) -> dict:
    if data_used is None:
        return {"status": "skipped", "unbound_sources": [], "kb_sources": [], "invalid_entries": []}
    if not isinstance(data_used, list):
        return {
            "status": "invalid",
            "unbound_sources": ["Некорректный data_used"],
            "kb_sources": [],
            "invalid_entries": ["data_used должен быть массивом"],
        }
    if not data_used:
        return {"status": "ok", "unbound_sources": [], "kb_sources": [], "invalid_entries": []}
    sheets = [sheet for sheet in (grounding_sheets or []) if isinstance(sheet, dict)]
    by_title = {_source_title_key(sheet.get("title")): sheet for sheet in sheets}
    grounding_headings = _grounding_heading_keys(provenance_text)
    unbound: list[str] = []
    kb_sources: list[str] = []
    invalid_entries: list[str] = []
    for index, item in enumerate(data_used):
        if not isinstance(item, dict):
            invalid_entries.append(f"data_used[{index}] должен быть объектом")
            continue
        title = str(item.get("sheet_title") or "").strip()
        values = item.get("values")
        if not title:
            invalid_entries.append(f"data_used[{index}].sheet_title не задан")
        if not isinstance(values, list) or not values or any(not str(value).strip() for value in values):
            invalid_entries.append(f"data_used[{index}].values должен быть непустым массивом")
        sheet = _resolve_lineage_sheet(title, sheets, by_title, grounding_headings)
        source_ok = bool(
            sheet is not None
            and str(sheet.get("source_document_id") or "").strip()
            and sheet.get("source_document_exists") is True
            and str(sheet.get("source_authority") or "") in {"course_policy", "course_lecture", "reference"}
        )
        if not source_ok:
            label = title or f"data_used[{index}]"
            if label not in unbound:
                unbound.append(label)
    return {
        "status": "invalid" if invalid_entries else ("warn" if unbound else "ok"),
        "unbound_sources": unbound,
        "kb_sources": kb_sources,
        "invalid_entries": invalid_entries,
    }


def sanity_check(task: dict) -> dict:
    issues: list[str] = []
    statement = str(task.get("statement") or "").strip()
    if len(statement) < 30:
        issues.append("Условие подозрительно короткое (меньше 30 символов)")
    if len(str(task.get("reference_solution") or "").strip()) < 20:
        issues.append("Эталонное решение отсутствует или слишком короткое")
    rubric = task.get("rubric")
    try:
        max_score = float(task.get("max_score"))
    except (TypeError, ValueError):
        max_score = math.nan
    max_score_valid = math.isfinite(max_score) and max_score > 0
    if not max_score_valid:
        issues.append("Максимальный балл должен быть конечным положительным числом")
    if not isinstance(rubric, list) or not rubric:
        issues.append("Рубрика оценивания пуста")
    else:
        total = 0.0
        rubric_scores_valid = True
        criterion_names: set[str] = set()
        for index, criterion in enumerate(rubric):
            if not isinstance(criterion, dict):
                issues.append(f"Критерий рубрики {index + 1} должен быть объектом")
                rubric_scores_valid = False
                continue
            criterion_name = str(criterion.get("criterion_name") or "").strip()
            if not criterion_name:
                issues.append(f"У критерия рубрики {index + 1} не задано название")
            elif criterion_name.casefold() in criterion_names:
                issues.append(f"Название критерия рубрики повторяется: {criterion_name}")
            else:
                criterion_names.add(criterion_name.casefold())
            try:
                criterion_score = float(criterion.get("max_score"))
            except (TypeError, ValueError):
                criterion_score = math.nan
            if not math.isfinite(criterion_score) or criterion_score <= 0:
                issues.append(f"Балл критерия рубрики {index + 1} должен быть конечным положительным числом")
                rubric_scores_valid = False
                continue
            total += criterion_score
        if max_score_valid and rubric_scores_valid and abs(total - max_score) > 0.01:
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
    discipline_context: str = "",
) -> dict:
    hint = ANSWER_FORMAT_HINTS.get(answer_format, ANSWER_FORMAT_HINTS["text"])
    parts = [f"Задача:\n{statement}"]
    if grounding:
        parts.append(grounding)
    if discipline_context:
        parts.append(
            "Профиль дисциплины и требования преподавателя (это правила проверки, не источник численных данных):\n"
            + discipline_context
        )
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


def _solver_report(
    solved: dict,
    reference_answer: str,
    model_name: str,
    comparison: dict | None = None,
    solution_comparison: dict | None = None,
    answer_solution_comparison: dict | None = None,
) -> dict:
    return {
        "status": solved["status"] if comparison is None else comparison["verdict"],
        "answer": solved["answer"],
        "solution": solved["solution"][:SOLVER_EVIDENCE_CHAR_LIMIT],
        "reference_answer": reference_answer,
        "model": model_name,
        "error": solved["error"],
        "duration_ms": solved.get("duration_ms", 0),
        "tokens_total": solved.get("tokens_total"),
        "comparison": comparison or {},
        "solution_comparison": solution_comparison or {},
        "answer_solution_comparison": answer_solution_comparison or {},
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


def _solver_outcome_complete(report: dict) -> bool:
    return bool(
        report.get("status") != "error"
        and not str(report.get("error") or "").strip()
        and str(report.get("answer") or "").strip()
        and str(report.get("solution") or "").strip()
    )


def _semantic_entailment_candidate(
    answer_format: str,
    solver: dict,
    verifier: dict,
    cross_comparison: dict,
    *,
    chemistry_verified: bool = False,
) -> bool:
    """Allow the critic to resolve representation uncertainty, never missing evidence.

    Formula-heavy chemistry answers often render the same coefficient as a
    charge, subscript or pseudo-unit in different notation.  The numeric parser
    then reports ``incomplete`` even though the missing values reappear exactly
    among the unmatched values.  Such a case may reach the semantic critic only
    after deterministic chemistry has passed; a genuinely absent result cannot.
    """

    if answer_format not in SEMANTIC_ENTAILMENT_ANSWER_FORMATS:
        return False
    if not (_solver_outcome_complete(solver) and _solver_outcome_complete(verifier)):
        return False
    comparisons = [solver.get("comparison") or {}, verifier.get("comparison") or {}, cross_comparison]
    verdicts = {str(comparison.get("verdict") or "") for comparison in comparisons}
    if verdicts.issubset({"match", "uncertain"}) and "uncertain" in verdicts:
        return True
    if not chemistry_verified or "incomplete" not in verdicts or not verdicts.issubset(
        {"match", "uncertain", "incomplete"}
    ):
        return False
    if (
        solver.get("status") == "match"
        and verifier.get("status") == "match"
        and cross_comparison.get("verdict") in {"incomplete", "uncertain"}
    ):
        # Each independent answer already entails the reference.  Differences
        # between their wording still require the full subject critic; lexical
        # claim matching alone must not discard an otherwise proven task.
        return True
    reference_comparisons_safe = all(
        _representation_only_incomplete(comparison)
        for comparison in (solver.get("comparison") or {}, verifier.get("comparison") or {})
    )
    cross_comparison_safe = _representation_only_incomplete(
        cross_comparison
    ) or _lexical_only_cross_incomplete(cross_comparison)
    return reference_comparisons_safe and cross_comparison_safe


def _solution_backed_entailment_candidate(
    answer_format: str,
    solver: dict,
    verifier: dict,
    cross_comparison: dict,
    *,
    chemistry_verified: bool = False,
) -> bool:
    """Admit exactly one compact-answer omission to the fail-closed critic.

    The other independent answer must already match.  The incomplete answer
    must be fully contained in its saved solution, and that exact solution
    slice must contain the complete reference.  This never promotes evidence
    by itself; the subject critic must still pass every required check.
    """

    if answer_format not in SEMANTIC_ENTAILMENT_ANSWER_FORMATS or not chemistry_verified:
        return False
    if not (_solver_outcome_complete(solver) and _solver_outcome_complete(verifier)):
        return False
    if cross_comparison.get("verdict") not in {"match", "incomplete"}:
        return False
    reports = (solver, verifier)
    incomplete = [report for report in reports if report.get("status") == "incomplete"]
    matched = [report for report in reports if report.get("status") == "match"]
    if len(incomplete) != 1 or len(matched) != 1:
        return False
    compact = incomplete[0]
    return bool(
        (compact.get("solution_comparison") or {}).get("verdict") == "match"
        and (compact.get("answer_solution_comparison") or {}).get("verdict") == "match"
    )


def _representation_only_incomplete(comparison: dict) -> bool:
    verdict = comparison.get("verdict")
    if verdict in {"match", "uncertain"}:
        return True
    if verdict != "incomplete" or comparison.get("missing_text_claims"):
        return False
    missing_groups = comparison.get("missing_reference_groups") or []
    if not missing_groups:
        # All required outputs matched; only additional symbolic coefficients
        # remain for the critic to interpret.
        return comparison.get("matched_count") == comparison.get("required_count")
    unexpected = list(comparison.get("unexpected_solver_numbers") or [])
    for group in missing_groups:
        alternatives = group if isinstance(group, list) else [group]
        match_index = next(
            (
                index
                for index, candidate in enumerate(unexpected)
                if any(_close(float(reference), float(candidate), 1e-9) for reference in alternatives)
            ),
            None,
        )
        if match_index is None:
            return False
        unexpected.pop(match_index)
    return True


def _lexical_only_cross_incomplete(comparison: dict) -> bool:
    """Recognize wording-only disagreement after both reference gates passed."""

    return bool(
        comparison.get("verdict") == "incomplete"
        and comparison.get("missing_text_claims")
        and comparison.get("matched_count") == comparison.get("required_count")
        and not (comparison.get("missing_reference_groups") or [])
        and not (comparison.get("unexpected_solver_numbers") or [])
        and not (comparison.get("missing_reference_units") or [])
    )


def _critic_confirms_semantic_entailment(critic: dict) -> bool:
    checks = critic.get("checks") if isinstance(critic.get("checks"), dict) else {}
    return bool(
        critic.get("status") == "pass"
        and critic.get("issues") == []
        and all(checks.get(key) is True for key in CRITIC_REQUIRED_CHECKS)
    )


def _apply_deterministic_numeric_critic_evidence(
    critic: dict,
    *,
    answer_format: str,
    solver: dict,
    verifier: dict,
    cross_comparison: dict,
) -> dict:
    """Prevent a semantic critic from overruling proven numeric equivalence.

    The critic remains authoritative for self-containment, reference logic,
    structured facts, units and chemistry. Only the three answer-relation flags
    are replaced, and only when every corresponding deterministic comparison
    has already matched within the frozen task tolerance.
    """

    if answer_format != "numeric":
        return critic
    deterministic_relations = {
        "solver_matches_reference": solver.get("status") == "match",
        "verifier_matches_reference": verifier.get("status") == "match",
        "solver_agreement": cross_comparison.get("verdict") == "match",
    }
    if not all(deterministic_relations.values()):
        return critic

    checks = critic.get("checks") if isinstance(critic.get("checks"), dict) else {}
    false_checks = {key for key in CRITIC_REQUIRED_CHECKS if checks.get(key) is not True}
    overridden_checks = false_checks & NUMERIC_RELATION_CHECKS
    if not overridden_checks or false_checks - NUMERIC_RELATION_CHECKS:
        return critic

    reconciled_checks = {key: checks.get(key) is True for key in CRITIC_REQUIRED_CHECKS}
    reconciled_checks.update(deterministic_relations)
    return {
        **critic,
        "status": "pass",
        "checks": {key: reconciled_checks[key] for key in sorted(CRITIC_REQUIRED_CHECKS)},
        "issues": [],
        "overridden_issues": list(critic.get("issues") or []),
        "deterministic_overrides": sorted(overridden_checks),
        "basis": DETERMINISTIC_NUMERIC_BASIS,
    }


def _promote_comparison(comparison: dict, *, basis: str = SEMANTIC_ENTAILMENT_BASIS) -> dict:
    return {
        **comparison,
        "previous_verdict": comparison.get("verdict"),
        "verdict": "match",
        "basis": basis,
    }


def _promote_solver_report(report: dict, *, basis: str = SEMANTIC_ENTAILMENT_BASIS) -> dict:
    return {
        **report,
        "status": "match",
        "comparison": _promote_comparison(report.get("comparison") or {}, basis=basis),
    }


async def critic_check(
    provider: Provider,
    model: ModelEntry,
    *,
    statement: str,
    reference_solution: str,
    reference_answer: str,
    solver: dict,
    verifier: dict,
    discipline_context: str,
    chemistry_facts: dict,
) -> dict:
    payload = {
        "statement": statement,
        "reference_solution": reference_solution,
        "reference_answer": reference_answer,
        "solver": {"solution": solver.get("solution", ""), "answer": solver.get("answer", "")},
        "verifier": {"solution": verifier.get("solution", ""), "answer": verifier.get("answer", "")},
        "chemistry_facts": chemistry_facts,
        "discipline_context": discipline_context,
    }
    try:
        result = await llm.chat(
            provider,
            model,
            SOLVER_CRITIC_SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            temperature=0.0,
            json_mode=True,
        )
        parsed = llm.extract_json(result.text)
    except llm.LlmError as err:
        return {"status": "error", "issues": [str(err)], "checks": {}, "duration_ms": 0, "tokens_total": None}
    checks = parsed.get("checks") if isinstance(parsed.get("checks"), dict) else {}
    issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []
    issues = [str(issue).strip() for issue in issues if str(issue).strip()]
    passed = (
        parsed.get("verdict") == "pass"
        and CRITIC_REQUIRED_CHECKS.issubset(checks)
        and all(checks.get(key) is True for key in CRITIC_REQUIRED_CHECKS)
        and not issues
    )
    return {
        "status": "pass" if passed else "fail",
        "checks": {key: checks.get(key) is True for key in sorted(CRITIC_REQUIRED_CHECKS)},
        "issues": issues or ([] if passed else ["Критик не подтвердил все обязательные проверки"]),
        "duration_ms": result.duration_ms,
        "tokens_total": result.tokens_total,
        "model": f"{provider.name}/{model.model_id}",
    }


async def extract_chemistry_facts(
    provider: Provider,
    model: ModelEntry,
    *,
    statement: str,
    reference_solution: str,
    reference_answer: str,
    topic: str,
    discipline_context: str,
    chemistry_check: str,
) -> dict:
    payload = {
        "discipline_context": discipline_context,
        "topic": topic,
        "required_check": chemistry_check,
        "statement": statement,
        "reference_solution": reference_solution,
        "reference_answer": reference_answer,
        "facts_schema": CHEMISTRY_FACTS_GUIDE,
    }
    try:
        result = await llm.chat(
            provider,
            model,
            CHEMISTRY_FACT_EXTRACTOR_SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            temperature=0.0,
            json_mode=True,
            max_tokens=3000,
        )
        parsed = llm.extract_json(result.text)
    except llm.LlmError as err:
        return {
            "status": "error",
            "facts": {},
            "error": str(err),
            "duration_ms": 0,
            "tokens_total": None,
        }
    facts = normalize_chemistry_facts(parsed.get("facts"))
    if facts is None:
        return {
            "status": "error",
            "facts": {},
            "error": "Модель вернула неподдерживаемую структуру chemistry_facts",
            "duration_ms": result.duration_ms,
            "tokens_total": result.tokens_total,
        }
    return {
        "status": "ok",
        "facts": facts,
        "duration_ms": result.duration_ms,
        "tokens_total": result.tokens_total,
        "model": f"{provider.name}/{model.model_id}",
    }


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
    validation_config: dict | None = None,
    discipline_context: str = "",
    topic: str = "",
    chemistry_facts: dict | None = None,
    chemistry_facts_source: str = "",
    extract_chemistry_facts_if_missing: bool = False,
    grounding_sheets: list | None = None,
) -> dict:
    reasons: list[str] = []

    config = normalize_validation_config(
        {
            **(validation_config or {}),
            "answer_format": answer_format,
            "tolerance_pct": tolerance_pct,
            "validation_solver": run_solver,
            "validation_data_check": run_data,
            "source_digest": hashlib.sha256(
                json.dumps(
                    {
                        "grounding": grounding,
                        "sheets": sheets_text,
                        "data_used": data_used,
                        "grounding_sheets": grounding_sheets,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            "profile_digest": hashlib.sha256(discipline_context.encode("utf-8")).hexdigest(),
            "task_evidence_digest": task_evidence_digest(
                data_used=data_used,
                chemistry_facts=chemistry_facts,
            ),
        }
    )

    data: dict = {"status": "skipped", "unknown_numbers": [], "unknown_sources": []}
    provenance_text = grounding if (grounding or "").strip() else sheets_text
    if run_data:
        # The generator can cite either a selected ReferenceSheet or a retrieved KB chunk.
        # Validate against the exact grounding it actually saw; ``sheets_text`` contains
        # only ReferenceSheets and would falsely reject a real KB heading. Falling back
        # preserves revalidation for older/manual calls that have no grounding block.
        data = data_check(statement, provenance_text, data_used=data_used)
        if data["unknown_numbers"]:
            reasons.append("Числа не из справочника: " + ", ".join(data["unknown_numbers"][:10]))
        if data["unknown_sources"]:
            reasons.append("Неизвестные источники данных: " + ", ".join(data["unknown_sources"][:10]))
        if data.get("invalid_entries"):
            reasons.append("Некорректный provenance данных: " + "; ".join(data["invalid_entries"][:5]))
    else:
        reasons.append("Проверка происхождения данных отключена — автоматический допуск невозможен")
    source_lineage = source_lineage_check(data_used, grounding_sheets, provenance_text)
    if source_lineage["unbound_sources"]:
        reasons.append(
            "Справочные данные не связаны с исходным документом: " + ", ".join(source_lineage["unbound_sources"][:10])
        )
    if source_lineage.get("invalid_entries"):
        reasons.append("Некорректная привязка к источникам: " + "; ".join(source_lineage["invalid_entries"][:5]))

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

    model_use = current_model_use_policy().classify(solver_model)
    preliminary_hard_fail = bool(
        data.get("status") != "ok"
        or data["unknown_numbers"]
        or data["unknown_sources"]
        or source_lineage.get("status") != "ok"
        or source_lineage["unbound_sources"]
        or sanity["issues"]
        or dedup["duplicate"]
    )
    chemistry_check = config.get("chemistry_check", "auto")
    normalized_facts = normalize_chemistry_facts(chemistry_facts) if chemistry_facts is not None else None
    facts_extraction: dict = {"status": "not_needed" if normalized_facts is not None else "skipped"}
    facts_source = chemistry_facts_source or ("provided" if normalized_facts is not None else "not_available")

    if chemistry_facts is not None and normalized_facts is None:
        facts_extraction = {"status": "error", "error": "Некорректный формат сохранённых chemistry_facts"}
        normalized_facts = {}
        facts_source = "invalid"
    elif (
        normalized_facts is None
        and extract_chemistry_facts_if_missing
        and not preliminary_hard_fail
        and solver_provider is not None
        and solver_model is not None
        and model_use.decision_capable
    ):
        facts_extraction = await extract_chemistry_facts(
            solver_provider,
            solver_model,
            statement=statement,
            reference_solution=reference_solution,
            reference_answer=reference_answer,
            topic=topic,
            discipline_context=discipline_context,
            chemistry_check=chemistry_check,
        )
        normalized_facts = facts_extraction.get("facts") if facts_extraction.get("status") == "ok" else {}
        facts_source = "deepseek_extractor" if facts_extraction.get("status") == "ok" else "extraction_failed"

    config = normalize_validation_config(
        {
            **config,
            "chemistry_facts_digest": hashlib.sha256(
                json.dumps(
                    normalized_facts,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
        }
    )
    content_fingerprint = build_task_content_fingerprint(
        statement=statement,
        reference_solution=reference_solution,
        answer=reference_answer,
        rubric=rubric,
        max_score=max_score,
        validation_config=config,
    )

    chemistry_enabled = bool(discipline_context.strip() or normalized_facts is not None or chemistry_check != "auto")
    if chemistry_enabled:
        chemistry = chemistry_admission_evidence(
            discipline=discipline_context,
            statement=statement,
            reference_solution=reference_solution,
            answer=reference_answer,
            topic=topic,
            facts=normalized_facts or {},
            facts_source=facts_source,
            chemistry_check=chemistry_check,
        )
    else:
        chemistry = {
            "validation_version": CHEMISTRY_VALIDATION_VERSION,
            "discipline": "unknown",
            "deterministic_pass": False,
            "applicable_count": 0,
            "blocking_codes": [],
            "indeterminate_codes": [],
            "warning_codes": [],
            "results": [],
            "required_check_ids": [],
            "required_not_passed": [],
            "facts_source": facts_source,
            "admission_effect": "limited",
        }
    chemistry["facts_extraction"] = facts_extraction
    if chemistry_facts is not None and facts_source == "invalid":
        chemistry["admission_effect"] = "block"
        chemistry.setdefault("blocking_codes", []).append("chemistry.facts_schema")
    requires_deterministic_core = config.get("task_kind") == "calculation" or answer_format == "numeric"
    if requires_deterministic_core and chemistry.get("admission_effect") == "limited":
        chemistry["coverage_before_admission"] = "limited"
        chemistry["admission_effect"] = "block"
        chemistry["admission_reason"] = "Для расчётной задачи не подтверждён основной предметный инвариант"
        chemistry.setdefault("blocking_codes", []).append("chemistry.core_calculation_uncovered")
    chemistry_blocked = chemistry.get("admission_effect") == "block"
    if chemistry_blocked:
        unsafe_results = [
            result
            for result in chemistry.get("results") or []
            if result.get("state") in {"fail", "warning", "indeterminate", "error"}
        ]
        reasons.extend(
            f"Предметный контроль ({result.get('check_id')}): {result.get('message')}" for result in unsafe_results
        )
        reported = {result.get("check_id") for result in unsafe_results}
        reasons.extend(
            f"Предметный контроль не подтвердил обязательную проверку: {check_id}"
            for check_id in chemistry.get("required_not_passed") or []
            if check_id not in reported
        )
        if facts_extraction.get("status") == "error" and chemistry_check != "auto":
            reasons.append("Не удалось подготовить данные для обязательной химической проверки")
        if chemistry.get("admission_reason"):
            reasons.append(str(chemistry["admission_reason"]))

    hard_fail = preliminary_hard_fail or chemistry_blocked
    solver: dict = {"status": "skipped"}
    verifier: dict = {"status": "skipped"}
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
            solved = await solver_check(
                solver_provider,
                solver_model,
                statement,
                "",
                answer_format,
                discipline_context=discipline_context,
            )
            compared = (
                compare_answers(reference_answer, solved["answer"], tolerance_pct, context=statement)
                if solved["status"] != "error"
                else None
            )
            solver_solution_evidence = solved["solution"][:SOLVER_EVIDENCE_CHAR_LIMIT]
            solution_compared = (
                compare_answers(
                    reference_answer,
                    solver_solution_evidence,
                    tolerance_pct,
                    context=statement,
                    allow_extra_numbers=True,
                )
                if solved["status"] != "error"
                else None
            )
            answer_solution_compared = (
                compare_answers(
                    solved["answer"],
                    solver_solution_evidence,
                    tolerance_pct,
                    context=statement,
                    allow_extra_numbers=True,
                )
                if solved["status"] != "error"
                else None
            )
            solver = _solver_report(
                solved,
                reference_answer,
                model_name,
                compared,
                solution_compared,
                answer_solution_compared,
            )

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
                    discipline_context=discipline_context,
                )
                verified_comparison = (
                    compare_answers(reference_answer, verified["answer"], tolerance_pct, context=statement)
                    if verified["status"] != "error"
                    else None
                )
                verifier_solution_evidence = verified["solution"][:SOLVER_EVIDENCE_CHAR_LIMIT]
                verified_solution_comparison = (
                    compare_answers(
                        reference_answer,
                        verifier_solution_evidence,
                        tolerance_pct,
                        context=statement,
                        allow_extra_numbers=True,
                    )
                    if verified["status"] != "error"
                    else None
                )
                verified_answer_solution_comparison = (
                    compare_answers(
                        verified["answer"],
                        verifier_solution_evidence,
                        tolerance_pct,
                        context=statement,
                        allow_extra_numbers=True,
                    )
                    if verified["status"] != "error"
                    else None
                )
                verifier = _solver_report(
                    verified,
                    reference_answer,
                    model_name,
                    verified_comparison,
                    verified_solution_comparison,
                    verified_answer_solution_comparison,
                )

    cross_comparison = (
        compare_answers(solver.get("answer", ""), verifier.get("answer", ""), tolerance_pct, context=statement)
        if _solver_outcome_complete(solver) and _solver_outcome_complete(verifier)
        else {"verdict": "skipped"}
    )
    strict_comparison_candidate = bool(
        solver.get("status") == "match" and verifier.get("status") == "match" and cross_comparison["verdict"] == "match"
    )
    semantic_entailment_candidate = _semantic_entailment_candidate(
        answer_format,
        solver,
        verifier,
        cross_comparison,
        chemistry_verified=chemistry.get("admission_effect") == "pass",
    )
    solution_backed_entailment_candidate = _solution_backed_entailment_candidate(
        answer_format,
        solver,
        verifier,
        cross_comparison,
        chemistry_verified=chemistry.get("admission_effect") == "pass",
    )

    critic: dict = {"status": "skipped", "checks": {}, "issues": []}
    if (
        solver_provider is not None
        and solver_model is not None
        and model_use.decision_capable
        and (
            strict_comparison_candidate
            or semantic_entailment_candidate
            or solution_backed_entailment_candidate
        )
    ):
        critic = await critic_check(
            solver_provider,
            solver_model,
            statement=statement,
            reference_solution=reference_solution,
            reference_answer=reference_answer,
            solver=solver,
            verifier=verifier,
            discipline_context=discipline_context,
            chemistry_facts=normalized_facts or {},
        )
        critic = _apply_deterministic_numeric_critic_evidence(
            critic,
            answer_format=answer_format,
            solver=solver,
            verifier=verifier,
            cross_comparison=cross_comparison,
        )

    critic_confirmed = _critic_confirms_semantic_entailment(critic)
    solution_backed_entailment_applied = bool(solution_backed_entailment_candidate and critic_confirmed)
    semantic_entailment_applied = bool(semantic_entailment_candidate and critic_confirmed)
    entailment_basis = (
        SOLUTION_BACKED_ENTAILMENT_BASIS
        if solution_backed_entailment_applied
        else SEMANTIC_ENTAILMENT_BASIS
    )
    if solution_backed_entailment_applied or semantic_entailment_applied:
        solver = _promote_solver_report(solver, basis=entailment_basis)
        verifier = _promote_solver_report(verifier, basis=entailment_basis)
        cross_comparison = _promote_comparison(cross_comparison, basis=entailment_basis)
        critic = {**critic, "basis": entailment_basis}

    _append_solver_reason(reasons, "Основной решатель", solver)
    _append_solver_reason(reasons, "Независимый аудитор", verifier)
    if cross_comparison["verdict"] != "match":
        reasons.append("Контрольные решения не совпали друг с другом полностью")

    if critic["status"] != "pass":
        if critic["status"] == "error":
            reasons.append("Предметный критик не завершил проверку")
        elif critic["status"] == "fail":
            reasons.extend(f"Предметный критик: {issue}" for issue in critic.get("issues") or [])

    reference_solution_check = compare_answers(
        reference_answer,
        reference_solution,
        tolerance_pct,
        context=statement,
        allow_extra_numbers=True,
    )
    if (
        solution_backed_entailment_applied or semantic_entailment_applied
    ) and reference_solution_check["verdict"] == "uncertain":
        reference_solution_check = _promote_comparison(reference_solution_check, basis=entailment_basis)
    if reference_solution_check["verdict"] != "match":
        reasons.append("Эталонное решение не содержит полный финальный ответ")

    semantic_validation_complete = (
        run_solver
        and model_use.decision_capable
        and solver["status"] == "match"
        and verifier["status"] == "match"
        and cross_comparison["verdict"] == "match"
        and _critic_confirms_semantic_entailment(critic)
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
        or source_lineage.get("status") != "ok"
        or bool(source_lineage["unbound_sources"])
        or bool(sanity["issues"])
        or dedup["duplicate"]
        or chemistry_blocked
    )
    return {
        "policy_version": VALIDATION_POLICY_VERSION,
        "validation_config": config,
        "content_fingerprint": content_fingerprint,
        "model_policy": model_use.as_dict(),
        "solver": solver,
        "verifier": verifier,
        "cross_comparison": cross_comparison,
        "critic": critic,
        "chemistry": chemistry,
        "reference_solution_check": reference_solution_check,
        "data": data,
        "source_lineage": source_lineage,
        "sanity": sanity,
        "dedup": dedup,
        "answer_format": answer_format,
        "verdict": "needs_review" if needs_review else "validated",
        "reasons": reasons,
    }
