"""Small, deterministic unit layer for chemistry validation.

The module deliberately does not try to be a general computer algebra system.  It
recognises only units that occur in the launch chemistry courses and refuses an
unknown unit instead of guessing.  All conversion factors produce SI values.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum


class Dimension(StrEnum):
    AMOUNT = "amount"
    AMOUNT_PER_MASS = "amount_per_mass"
    AMOUNT_CONCENTRATION = "amount_concentration"
    AREA = "area"
    CHARGE = "charge"
    CHARGE_PER_AMOUNT = "charge_per_amount"
    CONDUCTANCE = "electrical_conductance"
    CONDUCTIVITY = "electrical_conductivity"
    CURRENT = "current"
    DIMENSIONLESS = "dimensionless"
    INVERSE_LENGTH = "inverse_length"
    LENGTH = "length"
    MASS = "mass"
    MASS_CONCENTRATION = "mass_concentration"
    MOBILITY = "electrophoretic_mobility"
    MOLAR_MASS = "molar_mass"
    PERMITTIVITY = "permittivity"
    PRESSURE = "pressure"
    RECIPROCAL_AMOUNT = "reciprocal_amount"
    RESISTANCE = "electrical_resistance"
    SPECIFIC_SURFACE = "specific_surface"
    TEMPERATURE = "temperature"
    TIME = "time"
    VISCOSITY = "dynamic_viscosity"
    VOLTAGE = "voltage"
    VOLUME = "volume"


@dataclass(frozen=True)
class UnitDefinition:
    symbol: str
    dimension: Dimension
    factor_to_si: float
    offset_to_si: float = 0.0


@dataclass(frozen=True)
class Measurement:
    value: float
    unit: str
    dimension: Dimension
    si_value: float
    source: str = ""


@dataclass(frozen=True)
class AssignedMeasurement:
    label: str
    normalized_label: str
    measurement: Measurement
    start: int
    end: int


def _unit(symbol: str, dimension: Dimension, factor: float, offset: float = 0.0) -> UnitDefinition:
    return UnitDefinition(symbol=symbol, dimension=dimension, factor_to_si=factor, offset_to_si=offset)


# Keys are normalised by ``_normalise_unit_key``.  Capital M is handled before
# case-folding because it means mol/L, whereas lower-case m means metre.
_UNIT_ALIASES: dict[str, UnitDefinition] = {
    "%": _unit("%", Dimension.DIMENSIONLESS, 0.01),
    "a": _unit("A", Dimension.CURRENT, 1.0),
    "ma": _unit("mA", Dimension.CURRENT, 1e-3),
    "мка": _unit("µA", Dimension.CURRENT, 1e-6),
    "μa": _unit("µA", Dimension.CURRENT, 1e-6),
    "c": _unit("C", Dimension.CHARGE, 1.0),
    "кл": _unit("C", Dimension.CHARGE, 1.0),
    "mc": _unit("mC", Dimension.CHARGE, 1e-3),
    "мкл-заряд": _unit("mC", Dimension.CHARGE, 1e-3),
    "μc": _unit("µC", Dimension.CHARGE, 1e-6),
    "c/mol": _unit("C/mol", Dimension.CHARGE_PER_AMOUNT, 1.0),
    "кл/моль": _unit("C/mol", Dimension.CHARGE_PER_AMOUNT, 1.0),
    "ω": _unit("Ω", Dimension.RESISTANCE, 1.0),
    "ohm": _unit("Ω", Dimension.RESISTANCE, 1.0),
    "ом": _unit("Ω", Dimension.RESISTANCE, 1.0),
    "kω": _unit("kΩ", Dimension.RESISTANCE, 1e3),
    "kohm": _unit("kΩ", Dimension.RESISTANCE, 1e3),
    "ком": _unit("kΩ", Dimension.RESISTANCE, 1e3),
    "siemens": _unit("S", Dimension.CONDUCTANCE, 1.0),
    "сименс": _unit("S", Dimension.CONDUCTANCE, 1.0),
    "v": _unit("V", Dimension.VOLTAGE, 1.0),
    "в": _unit("V", Dimension.VOLTAGE, 1.0),
    "mv": _unit("mV", Dimension.VOLTAGE, 1e-3),
    "мв": _unit("mV", Dimension.VOLTAGE, 1e-3),
    "s": _unit("s", Dimension.TIME, 1.0),
    "с": _unit("s", Dimension.TIME, 1.0),
    "сек": _unit("s", Dimension.TIME, 1.0),
    "min": _unit("min", Dimension.TIME, 60.0),
    "мин": _unit("min", Dimension.TIME, 60.0),
    "h": _unit("h", Dimension.TIME, 3600.0),
    "ч": _unit("h", Dimension.TIME, 3600.0),
    "mol": _unit("mol", Dimension.AMOUNT, 1.0),
    "моль": _unit("mol", Dimension.AMOUNT, 1.0),
    "mmol": _unit("mmol", Dimension.AMOUNT, 1e-3),
    "ммоль": _unit("mmol", Dimension.AMOUNT, 1e-3),
    "μmol": _unit("µmol", Dimension.AMOUNT, 1e-6),
    "мкмоль": _unit("µmol", Dimension.AMOUNT, 1e-6),
    "mol/kg": _unit("mol/kg", Dimension.AMOUNT_PER_MASS, 1.0),
    "моль/кг": _unit("mol/kg", Dimension.AMOUNT_PER_MASS, 1.0),
    "mmol/kg": _unit("mmol/kg", Dimension.AMOUNT_PER_MASS, 1e-3),
    "ммоль/кг": _unit("mmol/kg", Dimension.AMOUNT_PER_MASS, 1e-3),
    "mol/g": _unit("mol/g", Dimension.AMOUNT_PER_MASS, 1e3),
    "моль/г": _unit("mol/g", Dimension.AMOUNT_PER_MASS, 1e3),
    "mmol/g": _unit("mmol/g", Dimension.AMOUNT_PER_MASS, 1.0),
    "ммоль/г": _unit("mmol/g", Dimension.AMOUNT_PER_MASS, 1.0),
    "μmol/g": _unit("µmol/g", Dimension.AMOUNT_PER_MASS, 1e-3),
    "мкмоль/г": _unit("µmol/g", Dimension.AMOUNT_PER_MASS, 1e-3),
    "1/mol": _unit("mol⁻¹", Dimension.RECIPROCAL_AMOUNT, 1.0),
    "1/моль": _unit("mol⁻¹", Dimension.RECIPROCAL_AMOUNT, 1.0),
    "mol-1": _unit("mol⁻¹", Dimension.RECIPROCAL_AMOUNT, 1.0),
    "моль-1": _unit("mol⁻¹", Dimension.RECIPROCAL_AMOUNT, 1.0),
    "kg": _unit("kg", Dimension.MASS, 1.0),
    "кг": _unit("kg", Dimension.MASS, 1.0),
    "g": _unit("g", Dimension.MASS, 1e-3),
    "г": _unit("g", Dimension.MASS, 1e-3),
    "mg": _unit("mg", Dimension.MASS, 1e-6),
    "мг": _unit("mg", Dimension.MASS, 1e-6),
    "μg": _unit("µg", Dimension.MASS, 1e-9),
    "мкг": _unit("µg", Dimension.MASS, 1e-9),
    "m3": _unit("m³", Dimension.VOLUME, 1.0),
    "м3": _unit("m³", Dimension.VOLUME, 1.0),
    "dm3": _unit("L", Dimension.VOLUME, 1e-3),
    "дм3": _unit("L", Dimension.VOLUME, 1e-3),
    "l": _unit("L", Dimension.VOLUME, 1e-3),
    "л": _unit("L", Dimension.VOLUME, 1e-3),
    "ml": _unit("mL", Dimension.VOLUME, 1e-6),
    "мл": _unit("mL", Dimension.VOLUME, 1e-6),
    "cm3": _unit("mL", Dimension.VOLUME, 1e-6),
    "см3": _unit("mL", Dimension.VOLUME, 1e-6),
    "μl": _unit("µL", Dimension.VOLUME, 1e-9),
    "мкл": _unit("µL", Dimension.VOLUME, 1e-9),
    "mol/l": _unit("mol/L", Dimension.AMOUNT_CONCENTRATION, 1e3),
    "моль/л": _unit("mol/L", Dimension.AMOUNT_CONCENTRATION, 1e3),
    "mol/dm3": _unit("mol/L", Dimension.AMOUNT_CONCENTRATION, 1e3),
    "моль/дм3": _unit("mol/L", Dimension.AMOUNT_CONCENTRATION, 1e3),
    "mmol/l": _unit("mmol/L", Dimension.AMOUNT_CONCENTRATION, 1.0),
    "ммоль/л": _unit("mmol/L", Dimension.AMOUNT_CONCENTRATION, 1.0),
    "mol/m3": _unit("mol/m³", Dimension.AMOUNT_CONCENTRATION, 1.0),
    "моль/м3": _unit("mol/m³", Dimension.AMOUNT_CONCENTRATION, 1.0),
    "g/l": _unit("g/L", Dimension.MASS_CONCENTRATION, 1.0),
    "г/л": _unit("g/L", Dimension.MASS_CONCENTRATION, 1.0),
    "mg/l": _unit("mg/L", Dimension.MASS_CONCENTRATION, 1e-3),
    "мг/л": _unit("mg/L", Dimension.MASS_CONCENTRATION, 1e-3),
    "μg/l": _unit("µg/L", Dimension.MASS_CONCENTRATION, 1e-6),
    "мкг/л": _unit("µg/L", Dimension.MASS_CONCENTRATION, 1e-6),
    "kg/mol": _unit("kg/mol", Dimension.MOLAR_MASS, 1.0),
    "кг/моль": _unit("kg/mol", Dimension.MOLAR_MASS, 1.0),
    "g/mol": _unit("g/mol", Dimension.MOLAR_MASS, 1e-3),
    "г/моль": _unit("g/mol", Dimension.MOLAR_MASS, 1e-3),
    "m": _unit("m", Dimension.LENGTH, 1.0),
    "м": _unit("m", Dimension.LENGTH, 1.0),
    "cm": _unit("cm", Dimension.LENGTH, 1e-2),
    "см": _unit("cm", Dimension.LENGTH, 1e-2),
    "mm": _unit("mm", Dimension.LENGTH, 1e-3),
    "мм": _unit("mm", Dimension.LENGTH, 1e-3),
    "μm": _unit("µm", Dimension.LENGTH, 1e-6),
    "мкм": _unit("µm", Dimension.LENGTH, 1e-6),
    "nm": _unit("nm", Dimension.LENGTH, 1e-9),
    "нм": _unit("nm", Dimension.LENGTH, 1e-9),
    "m2": _unit("m²", Dimension.AREA, 1.0),
    "м2": _unit("м²", Dimension.AREA, 1.0),
    "cm2": _unit("cm²", Dimension.AREA, 1e-4),
    "см2": _unit("см²", Dimension.AREA, 1e-4),
    "mm2": _unit("mm²", Dimension.AREA, 1e-6),
    "мм2": _unit("мм²", Dimension.AREA, 1e-6),
    "μm2": _unit("µm²", Dimension.AREA, 1e-12),
    "мкм2": _unit("мкм²", Dimension.AREA, 1e-12),
    "nm2": _unit("nm²", Dimension.AREA, 1e-18),
    "нм2": _unit("нм²", Dimension.AREA, 1e-18),
    "m-1": _unit("m⁻¹", Dimension.INVERSE_LENGTH, 1.0),
    "1/m": _unit("m⁻¹", Dimension.INVERSE_LENGTH, 1.0),
    "м-1": _unit("м⁻¹", Dimension.INVERSE_LENGTH, 1.0),
    "1/м": _unit("м⁻¹", Dimension.INVERSE_LENGTH, 1.0),
    "cm-1": _unit("cm⁻¹", Dimension.INVERSE_LENGTH, 100.0),
    "1/cm": _unit("cm⁻¹", Dimension.INVERSE_LENGTH, 100.0),
    "см-1": _unit("см⁻¹", Dimension.INVERSE_LENGTH, 100.0),
    "1/см": _unit("см⁻¹", Dimension.INVERSE_LENGTH, 100.0),
    "pa": _unit("Pa", Dimension.PRESSURE, 1.0),
    "па": _unit("Pa", Dimension.PRESSURE, 1.0),
    "kpa": _unit("kPa", Dimension.PRESSURE, 1e3),
    "кпа": _unit("kPa", Dimension.PRESSURE, 1e3),
    "bar": _unit("bar", Dimension.PRESSURE, 1e5),
    "бар": _unit("bar", Dimension.PRESSURE, 1e5),
    "pa*s": _unit("Pa·s", Dimension.VISCOSITY, 1.0),
    "па*с": _unit("Pa·s", Dimension.VISCOSITY, 1.0),
    "mpa*s": _unit("mPa·s", Dimension.VISCOSITY, 1e-3),
    "мпа*с": _unit("mPa·s", Dimension.VISCOSITY, 1e-3),
    "f/m": _unit("F/m", Dimension.PERMITTIVITY, 1.0),
    "ф/м": _unit("F/m", Dimension.PERMITTIVITY, 1.0),
    "m2/(v*s)": _unit("m²/(V·s)", Dimension.MOBILITY, 1.0),
    "м2/(в*с)": _unit("m²/(V·s)", Dimension.MOBILITY, 1.0),
    "cm2/(v*s)": _unit("cm²/(V·s)", Dimension.MOBILITY, 1e-4),
    "см2/(в*с)": _unit("cm²/(V·s)", Dimension.MOBILITY, 1e-4),
    "m2/kg": _unit("m²/kg", Dimension.SPECIFIC_SURFACE, 1.0),
    "м2/кг": _unit("m²/kg", Dimension.SPECIFIC_SURFACE, 1.0),
    "m2/g": _unit("m²/g", Dimension.SPECIFIC_SURFACE, 1e3),
    "м2/г": _unit("m²/g", Dimension.SPECIFIC_SURFACE, 1e3),
    "k": _unit("K", Dimension.TEMPERATURE, 1.0),
    "к": _unit("K", Dimension.TEMPERATURE, 1.0),
    "°c": _unit("°C", Dimension.TEMPERATURE, 1.0, 273.15),
    "°с": _unit("°C", Dimension.TEMPERATURE, 1.0, 273.15),
}

# Conductance symbols are case-sensitive: ``S`` is siemens while ``s`` is a
# second; Russian ``См`` is siemens while ``см`` is centimetre.  Keep these
# spellings outside the case-folded table to prevent a physically dangerous
# reinterpretation.  Conductivity factors are expressed relative to S/m.
_CASE_SENSITIVE_UNIT_ALIASES: dict[str, UnitDefinition] = {
    "mΩ": _unit("mΩ", Dimension.RESISTANCE, 1e-3),
    "MΩ": _unit("MΩ", Dimension.RESISTANCE, 1e6),
    "mOhm": _unit("mΩ", Dimension.RESISTANCE, 1e-3),
    "MOhm": _unit("MΩ", Dimension.RESISTANCE, 1e6),
    "мОм": _unit("mΩ", Dimension.RESISTANCE, 1e-3),
    "МОм": _unit("MΩ", Dimension.RESISTANCE, 1e6),
    "S": _unit("S", Dimension.CONDUCTANCE, 1.0),
    "mS": _unit("mS", Dimension.CONDUCTANCE, 1e-3),
    "μS": _unit("µS", Dimension.CONDUCTANCE, 1e-6),
    "См": _unit("S", Dimension.CONDUCTANCE, 1.0),
    "мСм": _unit("mS", Dimension.CONDUCTANCE, 1e-3),
    "мкСм": _unit("µS", Dimension.CONDUCTANCE, 1e-6),
    "S/m": _unit("S/m", Dimension.CONDUCTIVITY, 1.0),
    "mS/m": _unit("mS/m", Dimension.CONDUCTIVITY, 1e-3),
    "μS/m": _unit("µS/m", Dimension.CONDUCTIVITY, 1e-6),
    "S/cm": _unit("S/cm", Dimension.CONDUCTIVITY, 100.0),
    "mS/cm": _unit("mS/cm", Dimension.CONDUCTIVITY, 0.1),
    "μS/cm": _unit("µS/cm", Dimension.CONDUCTIVITY, 1e-4),
    "См/м": _unit("S/m", Dimension.CONDUCTIVITY, 1.0),
    "мСм/м": _unit("mS/m", Dimension.CONDUCTIVITY, 1e-3),
    "мкСм/м": _unit("µS/m", Dimension.CONDUCTIVITY, 1e-6),
    "См/см": _unit("S/cm", Dimension.CONDUCTIVITY, 100.0),
    "мСм/см": _unit("mS/cm", Dimension.CONDUCTIVITY, 0.1),
    "мкСм/см": _unit("µS/cm", Dimension.CONDUCTIVITY, 1e-4),
}

_SUPERSCRIPT_TRANSLATION = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_NUMBER_PATTERN = r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?"


def _normalise_unit_key(raw: str) -> str:
    value = (raw or "").strip().strip(".,;:")
    value = value.translate(_SUPERSCRIPT_TRANSLATION)
    value = value.replace("−", "-").replace("µ", "μ")
    value = value.replace("\\cdot", "*").replace("·", "*").replace("×", "*")
    value = value.replace("^", "")
    value = re.sub(r"\s+", "", value)
    if value == "M":
        return "__molar__"
    if value == "мКл":
        return "__millicoulomb__"
    return value.casefold()


def unit_definition(raw: str) -> UnitDefinition | None:
    exact = (raw or "").strip().strip(".,;:").replace("µ", "μ")
    case_sensitive = _CASE_SENSITIVE_UNIT_ALIASES.get(exact)
    if case_sensitive is not None:
        return case_sensitive
    key = _normalise_unit_key(raw)
    if key == "__molar__":
        return _unit("mol/L", Dimension.AMOUNT_CONCENTRATION, 1e3)
    if key == "__millicoulomb__":
        return _unit("mC", Dimension.CHARGE, 1e-3)
    return _UNIT_ALIASES.get(key)


def measurement(value: float, unit: str, *, source: str = "") -> Measurement | None:
    definition = unit_definition(unit)
    if definition is None or not math.isfinite(value):
        return None
    si_value = value * definition.factor_to_si + definition.offset_to_si
    return Measurement(
        value=value,
        unit=definition.symbol,
        dimension=definition.dimension,
        si_value=si_value,
        source=source,
    )


def parse_measurement(value: object, *, expected: Dimension | None = None) -> Measurement | None:
    """Parse ``{"value": 25, "unit": "mL"}`` or ``"25 mL"`` without implicit units."""

    raw_value: object
    raw_unit: object
    source = ""
    if isinstance(value, dict):
        raw_value = value.get("value")
        raw_unit = value.get("unit")
        source = str(value)
    elif isinstance(value, str):
        match = re.fullmatch(rf"\s*({_NUMBER_PATTERN})\s*(.+?)\s*", value)
        if match is None:
            return None
        raw_value, raw_unit = match.groups()
        source = value
    else:
        return None
    if not isinstance(raw_unit, str):
        return None
    try:
        number = float(str(raw_value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    parsed = measurement(number, raw_unit, source=source)
    if parsed is None or (expected is not None and parsed.dimension != expected):
        return None
    return parsed


def normalize_label(label: str) -> str:
    value = label.translate(_SUBSCRIPT_TRANSLATION)
    value = value.replace("\\", "").replace("{", "").replace("}", "")
    return re.sub(r"[^a-zа-яё0-9]+", "", value.casefold())


def _raw_unit_spellings() -> list[str]:
    # Include display spellings and common exponent variants.  Longest first is
    # required so that ``mol/L`` wins over ``mol``.
    spellings = set(_UNIT_ALIASES)
    spellings.update(_CASE_SENSITIVE_UNIT_ALIASES)
    spellings.update(definition.symbol for definition in _UNIT_ALIASES.values())
    spellings.update(definition.symbol for definition in _CASE_SENSITIVE_UNIT_ALIASES.values())
    spellings.update(
        {
            "M",
            "mC",
            "мКл",
            "μC",
            "µC",
            "μA",
            "Pa*s",
            "mPa*s",
            "m2/(V*s)",
            "cm2/(V*s)",
            "м2/(В*с)",
            "см2/(В*с)",
            "m²/(V·s)",
            "cm²/(V·s)",
            "м²/(В·с)",
            "см²/(В·с)",
            "m^2/(V·s)",
            "cm^2/(V·s)",
            "м^2/(В·с)",
            "см^2/(В·с)",
            "m^2/(V*s)",
            "cm^2/(V*s)",
            "м^2/(В*с)",
            "см^2/(В*с)",
            "m²/g",
            "м²/г",
            "m^2/g",
            "м^2/г",
            "m^2/kg",
            "м^2/кг",
            "mol^-1",
            "моль^-1",
            "mol⁻¹",
            "моль⁻¹",
            "m^-1",
            "cm^-1",
            "м^-1",
            "см^-1",
            "m⁻¹",
            "cm⁻¹",
            "м⁻¹",
            "см⁻¹",
            "m^2",
            "cm^2",
            "mm^2",
            "μm^2",
            "nm^2",
            "м^2",
            "см^2",
            "мм^2",
            "мкм^2",
            "нм^2",
        }
    )
    return sorted(spellings, key=len, reverse=True)


def known_unit_spellings() -> tuple[str, ...]:
    """Public, deterministic vocabulary for parsers that bind numbers to units."""

    return tuple(_raw_unit_spellings())


_UNIT_PATTERN = "|".join(re.escape(unit) for unit in _raw_unit_spellings())
_ASSIGNMENT_RE = re.compile(
    rf"(?P<label>[A-Za-zА-Яа-яЁёΔδΖζΗηΜμΚκΛλ][\wА-Яа-яЁёΔδΖζημκλ()_{{}}\\-]{{0,32}})"
    rf"\s*=\s*(?P<number>{_NUMBER_PATTERN})\s*(?P<unit>{_UNIT_PATTERN})(?![\w/^])",
    re.IGNORECASE,
)
_MEASUREMENT_RE = re.compile(
    rf"(?<![\w.,])(?P<number>{_NUMBER_PATTERN})\s*(?P<unit>{_UNIT_PATTERN})(?![\w/^])",
    re.IGNORECASE,
)


def normalize_measurement_text(text: str) -> str:
    """Expose measurements written in ordinary chemistry LaTeX to the unit parser."""

    normalized = text or ""
    normalized = re.sub(
        rf"(?P<base>{_NUMBER_PATTERN})\s*\\(?:times|cdot)\s*10\s*\^\s*\{{\s*(?P<exp>[-+]?\d+)\s*\}}",
        lambda match: f"{match.group('base')}e{match.group('exp')}",
        normalized,
    )
    normalized = re.sub(r"\\(?:text|mathrm)\s*\{([^{}]*)\}", r" \1 ", normalized)
    normalized = re.sub(r"\^\s*\{\s*([-+]?\d+)\s*\}", r"^\1", normalized)
    normalized = re.sub(r"\\[,;!:]|\\\s", " ", normalized)
    normalized = normalized.replace("{", "").replace("}", "").replace("$", "")
    normalized = re.sub(r"\s*\^\s*", "^", normalized)
    normalized = re.sub(r"\s*/\s*", "/", normalized)
    return re.sub(r"\s+", " ", normalized)


def extract_assigned_measurements(text: str) -> list[AssignedMeasurement]:
    found: list[AssignedMeasurement] = []
    for match in _ASSIGNMENT_RE.finditer(normalize_measurement_text(text)):
        try:
            value = float(match.group("number").replace(",", "."))
        except ValueError:
            continue
        parsed = measurement(value, match.group("unit"), source=match.group(0))
        if parsed is None:
            continue
        label = match.group("label")
        found.append(
            AssignedMeasurement(
                label=label,
                normalized_label=normalize_label(label),
                measurement=parsed,
                start=match.start(),
                end=match.end(),
            )
        )
    return found


def extract_measurements(text: str) -> list[Measurement]:
    found: list[Measurement] = []
    for match in _MEASUREMENT_RE.finditer(normalize_measurement_text(text)):
        try:
            value = float(match.group("number").replace(",", "."))
        except ValueError:
            continue
        parsed = measurement(value, match.group("unit"), source=match.group(0))
        if parsed is not None:
            found.append(parsed)
    return found


def close_si(left: float, right: float, *, relative_tolerance: float = 0.01) -> bool:
    scale = max(abs(left), abs(right), 1e-30)
    return abs(left - right) <= relative_tolerance * scale


def value_in_unit(si_value: float, unit: str) -> float | None:
    definition = unit_definition(unit)
    if definition is None:
        return None
    return (si_value - definition.offset_to_si) / definition.factor_to_si
