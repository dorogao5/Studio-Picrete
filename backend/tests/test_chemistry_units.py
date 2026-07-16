import pytest

from app.services.chemistry_units import (
    Dimension,
    extract_assigned_measurements,
    extract_measurements,
    parse_measurement,
)


@pytest.mark.parametrize(
    ("raw", "dimension", "si_value"),
    [
        ("25.0 mL", Dimension.VOLUME, 25e-6),
        ("0.100 mol/L", Dimension.AMOUNT_CONCENTRATION, 100.0),
        ("3.2e-8 m2/(V*s)", Dimension.MOBILITY, 3.2e-8),
        ("1.0 mPa*s", Dimension.VISCOSITY, 1e-3),
        ("107.868 g/mol", Dimension.MOLAR_MASS, 0.107868),
        ("25 мВ", Dimension.VOLTAGE, 0.025),
        ("2 Кл", Dimension.CHARGE, 2.0),
        ("1.5 kΩ", Dimension.RESISTANCE, 1500.0),
        ("2.0 mS", Dimension.CONDUCTANCE, 2e-3),
        ("850 μS/cm", Dimension.CONDUCTIVITY, 0.085),
        ("1.25 cm^-1", Dimension.INVERSE_LENGTH, 125.0),
        ("1.000 mmol/g", Dimension.AMOUNT_PER_MASS, 1.0),
        ("1.000e-3 mol/g", Dimension.AMOUNT_PER_MASS, 1.0),
        ("0.162 nm²", Dimension.AREA, 0.162e-18),
        ("6.02214076e23 mol^-1", Dimension.RECIPROCAL_AMOUNT, 6.02214076e23),
        ("97.5587 m²/g", Dimension.SPECIFIC_SURFACE, 97558.7),
    ],
)
def test_measurements_are_converted_to_si(raw: str, dimension: Dimension, si_value: float) -> None:
    parsed = parse_measurement(raw)

    assert parsed is not None
    assert parsed.dimension == dimension
    assert parsed.si_value == pytest.approx(si_value)


def test_assignment_extraction_preserves_labels_and_units() -> None:
    values = extract_assigned_measurements("c1 = 0.100 mol/L; V1 = 25.0 mL; mu = 3.2e-8 m2/(V*s); eta = 1.0 mPa*s")

    assert [value.label for value in values] == ["c1", "V1", "mu", "eta"]
    assert [value.measurement.dimension for value in values] == [
        Dimension.AMOUNT_CONCENTRATION,
        Dimension.VOLUME,
        Dimension.MOBILITY,
        Dimension.VISCOSITY,
    ]


def test_latex_measurement_extraction_preserves_compound_units_and_scientific_notation() -> None:
    values = extract_measurements(
        r"M(Fe) = 55.85\ \text{г/моль}; "
        r"N_A = 6.02214076 \times 10^{23}\ \text{моль}^{-1}; "
        r"S = 229\ \text{м}^2/\text{г}"
    )

    assert [value.dimension for value in values] == [
        Dimension.MOLAR_MASS,
        Dimension.RECIPROCAL_AMOUNT,
        Dimension.SPECIFIC_SURFACE,
    ]
    assert values[0].si_value == pytest.approx(0.05585)
    assert values[1].si_value == pytest.approx(6.02214076e23)
    assert values[2].si_value == pytest.approx(229000.0)


def test_unknown_unit_is_not_silently_interpreted() -> None:
    assert parse_measurement("5 arbitrary_units") is None


def test_case_distinguishes_millicoulomb_from_microlitre_in_russian_notation() -> None:
    charge = parse_measurement("2 мКл")
    volume = parse_measurement("2 мкл")

    assert charge is not None and charge.dimension == Dimension.CHARGE
    assert charge.si_value == pytest.approx(2e-3)
    assert volume is not None and volume.dimension == Dimension.VOLUME
    assert volume.si_value == pytest.approx(2e-9)


def test_case_distinguishes_siemens_from_seconds_and_russian_centimetres() -> None:
    conductance = parse_measurement("2 S")
    duration = parse_measurement("2 s")
    russian_conductance = parse_measurement("2 См")
    russian_length = parse_measurement("2 см")

    assert conductance is not None and conductance.dimension == Dimension.CONDUCTANCE
    assert duration is not None and duration.dimension == Dimension.TIME
    assert russian_conductance is not None and russian_conductance.dimension == Dimension.CONDUCTANCE
    assert russian_length is not None and russian_length.dimension == Dimension.LENGTH


def test_resistance_prefix_case_is_not_silently_conflated() -> None:
    milliohm = parse_measurement("1 mΩ")
    megaohm = parse_measurement("1 MΩ")

    assert milliohm is not None and milliohm.si_value == pytest.approx(1e-3)
    assert megaohm is not None and megaohm.si_value == pytest.approx(1e6)
