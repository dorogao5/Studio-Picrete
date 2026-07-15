import pytest

from app.services.chemistry_equations import (
    ChemistryParseError,
    check_reaction_balance,
    parse_species,
)


def test_formula_parser_supports_nested_complexes_and_hydrates() -> None:
    complex_ion = parse_species("[Fe(CN)6]^3-")
    hydrate = parse_species("CuSO4·5H2O")

    assert complex_ion.atoms == {"Fe": 1, "C": 6, "N": 6}
    assert complex_ion.charge == -3
    assert hydrate.atoms == {"Cu": 1, "S": 1, "O": 9, "H": 10}


def test_redox_equation_checks_atoms_and_charge() -> None:
    result = check_reaction_balance("MnO4^- + 8H+ + 5Fe^2+ -> Mn^2+ + 5Fe^3+ + 4H2O")

    assert result.balanced is True
    assert result.atom_delta == {}
    assert result.charge_delta == 0


def test_unicode_subscripts_and_superscript_charge_remain_distinct() -> None:
    sulfate = parse_species("SO₄²⁻")
    ammonium = parse_species("NH₄⁺")

    assert sulfate.atoms == {"S": 1, "O": 4}
    assert sulfate.charge == -2
    assert ammonium.atoms == {"N": 1, "H": 4}
    assert ammonium.charge == 1


def test_atom_balance_alone_does_not_hide_charge_error() -> None:
    result = check_reaction_balance("Fe^2+ -> Fe^3+")

    assert result.atom_delta == {}
    assert result.charge_delta == 1
    assert result.balanced is False


def test_unbalanced_neutral_equation_exposes_element_delta() -> None:
    result = check_reaction_balance("H2 + O2 -> H2O")

    assert result.balanced is False
    assert result.atom_delta == {"O": -1}


def test_parser_refuses_unsupported_free_text_instead_of_guessing() -> None:
    with pytest.raises(ChemistryParseError):
        check_reaction_balance("перманганат + Fe^2+ -> продукты")
