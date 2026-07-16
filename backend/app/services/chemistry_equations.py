"""Deterministic parsing and conservation checks for chemical equations.

The parser covers the notation used by the three launch courses: nested groups,
hydrates, phase suffixes, ionic charge and fractional/decimal coefficients.  An
unsupported notation raises ``ChemistryParseError``; callers must treat that as
indeterminate evidence rather than silently declaring the equation valid.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction


class ChemistryParseError(ValueError):
    pass


@dataclass(frozen=True)
class SpeciesComposition:
    formula: str
    atoms: dict[str, int]
    charge: int


@dataclass(frozen=True)
class ReactionTerm:
    coefficient: Fraction
    species: SpeciesComposition


@dataclass(frozen=True)
class ReactionBalance:
    equation: str
    reactants: tuple[ReactionTerm, ...]
    products: tuple[ReactionTerm, ...]
    atom_delta: dict[str, Fraction]
    charge_delta: Fraction

    @property
    def balanced(self) -> bool:
        return not self.atom_delta and self.charge_delta == 0

    def as_dict(self) -> dict:
        return {
            "equation": self.equation,
            "balanced": self.balanced,
            "atom_delta": {element: str(value) for element, value in self.atom_delta.items()},
            "charge_delta": str(self.charge_delta),
        }


_ARROW_RE = re.compile(r"(?:<=>|<->|=>|->|⇌|↔|→|⟶)")
_EQUALS_RE = re.compile(r"(?<![<>=])=(?!=)")
_PHASE_RE = re.compile(r"\((?:aq|s|l|g|газ|ж|тв|р-?р)\)\s*$", re.IGNORECASE)
_ELEMENT_RE = re.compile(r"[A-Z][a-z]?")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
_SUPERSCRIPT_CHARGE_RE = re.compile(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]*)([⁺⁻])$")
_MONATOMIC_IONS = {
    "Ag",
    "Al",
    "Ba",
    "Ca",
    "Cd",
    "Cl",
    "Co",
    "Cr",
    "Cu",
    "F",
    "Fe",
    "H",
    "Hg",
    "I",
    "K",
    "Li",
    "Mg",
    "Mn",
    "Na",
    "Ni",
    "O",
    "Pb",
    "S",
    "Sn",
    "Zn",
}


def _clean_formula(raw: str) -> str:
    value = (raw or "").strip().strip("$` ")
    # Preserve the semantic boundary between an atomic subscript and an ionic
    # superscript before translating Unicode digits: SO₄²⁻ -> SO4^2-, not SO42-.
    value = _SUPERSCRIPT_CHARGE_RE.sub(
        lambda match: "^" + match.group(1).translate(_SUPERSCRIPTS) + match.group(2).translate(_SUPERSCRIPTS),
        value,
    )
    value = value.translate(_SUBSCRIPTS).translate(_SUPERSCRIPTS)
    value = value.replace("−", "-").replace("∙", "·").replace("⋅", "·")
    value = re.sub(r"\\(?:mathrm|text)\s*\{([^{}]+)\}", r"\1", value)
    value = re.sub(r"_\{?(\d+)\}?", r"\1", value)
    value = re.sub(r"\s+", "", value)
    while _PHASE_RE.search(value):
        value = _PHASE_RE.sub("", value)
    return value


def _extract_charge(formula: str) -> tuple[str, int]:
    # Unambiguous caret notation: SO4^2-, [Fe(CN)6]^{3-}.
    caret = re.search(r"\^\{?(\d*)\s*([+-])\}?$", formula)
    if caret:
        magnitude = int(caret.group(1) or "1")
        charge = magnitude if caret.group(2) == "+" else -magnitude
        return formula[: caret.start()], charge

    # Coordination complexes are conventionally written [Fe(CN)6]3-.
    bracket = re.search(r"(?<=[\])])(\d+)([+-])$", formula)
    if bracket:
        magnitude = int(bracket.group(1))
        charge = magnitude if bracket.group(2) == "+" else -magnitude
        return formula[: bracket.start()], charge

    # Fe3+ is standard shorthand for a monatomic ion.  For NH4+ the 4 is a
    # subscript and the final sign alone denotes charge +1.
    monatomic = re.fullmatch(r"([A-Z][a-z]?)(\d+)([+-])", formula)
    if monatomic and monatomic.group(1) in _MONATOMIC_IONS:
        magnitude = int(monatomic.group(2))
        charge = magnitude if monatomic.group(3) == "+" else -magnitude
        return monatomic.group(1), charge

    if formula.endswith(("+", "-")):
        charge = 1 if formula.endswith("+") else -1
        return formula[:-1], charge
    return formula, 0


def _read_multiplier(formula: str, index: int) -> tuple[int, int]:
    end = index
    while end < len(formula) and formula[end].isdigit():
        end += 1
    return (int(formula[index:end]) if end > index else 1), end


def _parse_atom_group(formula: str) -> Counter[str]:
    if not formula:
        raise ChemistryParseError("empty chemical formula")
    stack: list[Counter[str]] = [Counter()]
    brackets: list[str] = []
    index = 0
    pairs = {")": "(", "]": "["}
    while index < len(formula):
        char = formula[index]
        if char in "([":
            brackets.append(char)
            stack.append(Counter())
            index += 1
            continue
        if char in ")]":
            if not brackets or brackets[-1] != pairs[char]:
                raise ChemistryParseError(f"mismatched bracket in {formula!r}")
            brackets.pop()
            group = stack.pop()
            multiplier, index = _read_multiplier(formula, index + 1)
            for element, count in group.items():
                stack[-1][element] += count * multiplier
            continue
        element_match = _ELEMENT_RE.match(formula, index)
        if element_match is None:
            raise ChemistryParseError(f"unsupported token {formula[index:]!r} in {formula!r}")
        element = element_match.group(0)
        multiplier, index = _read_multiplier(formula, element_match.end())
        stack[-1][element] += multiplier
    if brackets:
        raise ChemistryParseError(f"unclosed bracket in {formula!r}")
    return stack[0]


def parse_species(raw: str) -> SpeciesComposition:
    formula = _clean_formula(raw)
    if formula in {"e-", "e^-"}:
        return SpeciesComposition(formula="e-", atoms={}, charge=-1)
    if formula in {"e+", "e^+"}:
        return SpeciesComposition(formula="e+", atoms={}, charge=1)
    formula, charge = _extract_charge(formula)
    if not formula:
        raise ChemistryParseError(f"missing formula in species {raw!r}")

    total: Counter[str] = Counter()
    for hydrate_part in re.split(r"[·.]", formula):
        if not hydrate_part:
            raise ChemistryParseError(f"empty hydrate component in {raw!r}")
        leading = re.match(r"(\d+)(?=[A-Z\[])", hydrate_part)
        if leading:
            multiplier = int(leading.group(1))
            hydrate_part = hydrate_part[leading.end() :]
        else:
            multiplier = 1
        for element, count in _parse_atom_group(hydrate_part).items():
            total[element] += multiplier * count
    return SpeciesComposition(formula=formula, atoms=dict(total), charge=charge)


def _coefficient_and_formula(raw: str) -> tuple[Fraction, str]:
    value = raw.strip()
    coefficient = re.match(r"^(?P<value>\d+(?:\.\d+)?|\d+/\d+)\s*(?=[A-Z\[e])", value)
    if coefficient is None:
        return Fraction(1), value
    try:
        parsed = Fraction(coefficient.group("value"))
    except (ValueError, ZeroDivisionError) as exc:
        raise ChemistryParseError(f"invalid stoichiometric coefficient in {raw!r}") from exc
    if parsed <= 0:
        raise ChemistryParseError(f"non-positive stoichiometric coefficient in {raw!r}")
    return parsed, value[coefficient.end() :]


def _split_reaction_side(side: str) -> tuple[ReactionTerm, ...]:
    # Whitespace-delimited plus signs safely preserve H+ and Fe3+.  The second
    # branch also accepts conventional neutral equations written without spaces.
    pieces = re.split(r"\s+\+\s+|(?<=[A-Za-z0-9)\]])\+(?=(?:\d+(?:\.\d+)?\s*)?[A-Z\[])", side.strip())
    terms: list[ReactionTerm] = []
    for piece in pieces:
        if not piece.strip():
            raise ChemistryParseError(f"empty species in reaction side {side!r}")
        coefficient, formula = _coefficient_and_formula(piece)
        terms.append(ReactionTerm(coefficient=coefficient, species=parse_species(formula)))
    if not terms:
        raise ChemistryParseError(f"empty reaction side {side!r}")
    return tuple(terms)


def parse_reaction(equation: str) -> tuple[tuple[ReactionTerm, ...], tuple[ReactionTerm, ...]]:
    cleaned = (equation or "").strip().strip("$` ").rstrip(".;")
    arrows = list(_ARROW_RE.finditer(cleaned))
    # Russian chemistry handbooks commonly use one plain equals sign as the
    # reaction separator.  Accept it only when no arrow is present and there is
    # exactly one standalone ``=``; the normal species grammar still validates
    # both sides, so comparisons and arbitrary prose remain unsupported.
    if not arrows:
        arrows = list(_EQUALS_RE.finditer(cleaned))
    if len(arrows) != 1:
        raise ChemistryParseError(f"expected exactly one reaction arrow in {equation!r}")
    arrow = arrows[0]
    left, right = cleaned[: arrow.start()], cleaned[arrow.end() :]
    if not left.strip() or not right.strip():
        raise ChemistryParseError(f"reaction has an empty side: {equation!r}")
    return _split_reaction_side(left), _split_reaction_side(right)


def check_reaction_balance(equation: str) -> ReactionBalance:
    reactants, products = parse_reaction(equation)

    def totals(terms: tuple[ReactionTerm, ...]) -> tuple[Counter[str], Fraction]:
        atoms: Counter[str] = Counter()
        charge = Fraction()
        for term in terms:
            for element, count in term.species.atoms.items():
                atoms[element] += term.coefficient * count
            charge += term.coefficient * term.species.charge
        return atoms, charge

    left_atoms, left_charge = totals(reactants)
    right_atoms, right_charge = totals(products)
    atom_delta = {
        element: Fraction(right_atoms[element] - left_atoms[element])
        for element in sorted(set(left_atoms) | set(right_atoms))
        if right_atoms[element] != left_atoms[element]
    }
    return ReactionBalance(
        equation=equation,
        reactants=reactants,
        products=products,
        atom_delta=atom_delta,
        charge_delta=right_charge - left_charge,
    )


_REACTION_SPAN_BOUNDARY_LIMIT = 64
_REACTION_EDGE_PUNCTUATION = " \t\r\n$`:,.…"


def _is_oxidation_state_transition(fragment: str, arrow: re.Match[str]) -> bool:
    """Return true for prose such as ``Cr: +6 → +3``, not an equation."""

    left = fragment[: arrow.start()]
    right = fragment[arrow.end() :]
    left_state = re.search(r"(?:^|[\s:(])[-+−]?\d+\s*$", left)
    right_state = re.match(r"\s*[-+−]?\d+(?=\s*(?:[).,;:]|$))", right)
    return left_state is not None and right_state is not None


def _parseable_reaction_spans(
    fragment: str,
    separator_re: re.Pattern[str] = _ARROW_RE,
) -> list[str]:
    """Extract complete parseable equations around every arrow in a prose fragment.

    A solution step often embeds an equation between an introductory phrase and
    a follow-up calculation.  Trying every nearby whitespace boundary lets the
    grammar, rather than punctuation heuristics, decide where the equation ends.
    The cap keeps malformed or adversarially long prose bounded; callers retain
    the original arrow-containing fragment when no supported span is found.
    """

    whitespace = list(re.finditer(r"\s+", fragment))
    spans: list[str] = []
    for arrow in separator_re.finditer(fragment):
        if _is_oxidation_state_transition(fragment, arrow):
            continue
        starts = sorted({0, *(match.end() for match in whitespace if match.end() <= arrow.start())})
        ends = sorted({len(fragment), *(match.start() for match in whitespace if match.start() >= arrow.end())})
        starts = starts[-_REACTION_SPAN_BOUNDARY_LIMIT:]
        ends = ends[:_REACTION_SPAN_BOUNDARY_LIMIT]

        best_left: tuple[tuple[int, int, int], str] | None = None
        for start in starts:
            side = fragment[start : arrow.start()].strip(_REACTION_EDGE_PUNCTUATION)
            if not side:
                continue
            try:
                terms = _split_reaction_side(side)
            except ChemistryParseError:
                continue
            score = (len(terms), sum(term.coefficient != 1 for term in terms), -len(side))
            if best_left is None or score > best_left[0]:
                best_left = (score, side)

        best_right: tuple[tuple[int, int, int], str] | None = None
        for end in ends:
            side = fragment[arrow.end() : end].strip(_REACTION_EDGE_PUNCTUATION)
            if not side:
                continue
            try:
                terms = _split_reaction_side(side)
            except ChemistryParseError:
                continue
            score = (len(terms), sum(term.coefficient != 1 for term in terms), -len(side))
            if best_right is None or score > best_right[0]:
                best_right = (score, side)

        if best_left is not None and best_right is not None:
            candidate = f"{best_left[1]} {arrow.group()} {best_right[1]}"
            if candidate not in spans:
                spans.append(candidate)
    return spans


def reaction_candidates(text: str) -> list[str]:
    """Return conservative equation-looking fragments from solution text."""

    candidates: list[str] = []
    for fragment in re.split(r"[\n;]+", text or ""):
        arrows = [
            arrow for arrow in _ARROW_RE.finditer(fragment) if not _is_oxidation_state_transition(fragment, arrow)
        ]
        if not arrows:
            equals = list(_EQUALS_RE.finditer(fragment))
            if len(equals) == 1:
                candidates.extend(
                    candidate
                    for candidate in _parseable_reaction_spans(fragment, _EQUALS_RE)
                    if candidate not in candidates
                )
            continue
        spans = _parseable_reaction_spans(fragment)
        if spans:
            candidates.extend(candidate for candidate in spans if candidate not in candidates)
            continue
        # Fail closed for unsupported notation: preserve the arrow-containing
        # fragment so the balance check reports INDETERMINATE instead of
        # silently pretending there was no equation.
        candidate = fragment.strip().strip("$` ")
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates
