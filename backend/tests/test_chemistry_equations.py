import pytest

from app.services.chemistry_equations import (
    ChemistryParseError,
    check_reaction_balance,
    parse_species,
    reaction_candidates,
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


def test_plain_equals_is_supported_as_a_single_reaction_separator() -> None:
    result = check_reaction_balance("NO + KMnO4 = KNO3 + MnO2")

    assert result.balanced is True


@pytest.mark.parametrize("equation", ["H2 = O2 = H2O", "x = 1", "= H2O"])
def test_plain_equals_does_not_enable_ambiguous_or_nonchemical_expressions(equation: str) -> None:
    with pytest.raises(ChemistryParseError):
        check_reaction_balance(equation)


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


def test_reaction_candidate_extracts_equation_before_followup_explanation() -> None:
    candidates = reaction_candidates(
        "1. По уравнению реакции 3 MnO₂ → Mn₃O₄ + O₂ определяем количество вещества: "
        "для навески используем следующий расчёт."
    )

    assert candidates == ["3 MnO₂ → Mn₃O₄ + O₂"]
    assert check_reaction_balance(candidates[0]).balanced is True


def test_reaction_candidate_uses_parser_for_prefix_and_preserves_full_stoichiometry() -> None:
    candidates = reaction_candidates("По уравнению: 2 H₂ + O₂ → 2 H₂O, затем вычисляем объём.")

    assert candidates == ["2 H₂ + O₂ → 2 H₂O"]
    assert check_reaction_balance(candidates[0]).balanced is True


def test_reaction_candidate_accepts_one_parseable_handbook_equals() -> None:
    candidates = reaction_candidates("Молекулярное уравнение: NO + KMnO4 = KNO3 + MnO2.")

    assert candidates == ["NO + KMnO4 = KNO3 + MnO2"]
    assert check_reaction_balance(candidates[0]).balanced is True


def test_reaction_candidate_does_not_treat_calculation_or_equals_chain_as_reaction() -> None:
    assert reaction_candidates("n = cV = 0.030 mol") == []
    assert reaction_candidates("H2 = O2 = H2O") == []


def test_unsupported_reaction_notation_remains_a_blocking_candidate() -> None:
    text = "По схеме: перманганат + Fe^2+ -> продукты, затем продолжаем расчёт"

    assert reaction_candidates(text) == [text]


def test_oxidation_state_transition_in_prose_is_not_a_reaction_candidate() -> None:
    text = "Окислитель: Cr2O7^2- (Cr: +6 → +3). Восстановитель: I^- (I: -1 → 0)."

    assert reaction_candidates(text) == []


def test_oxidation_state_note_does_not_hide_a_real_equation() -> None:
    text = "Cr: +6 → +3; Cr2O7^2- + 14H+ + 6e- → 2Cr^3+ + 7H2O"

    assert reaction_candidates(text) == ["Cr2O7^2- + 14H+ + 6e- → 2Cr^3+ + 7H2O"]


def test_latex_bond_energy_solution_checks_reaction_not_calculation_arrows():
    text = r'''$$\mathrm{C_2H_2(г)} + 2\,\mathrm{H_2(г)} \rightarrow \mathrm{C_2H_6(г)}$$
- $\mathrm{C\equiv C}$ в $\mathrm{C_2H_2}$: 1 связь → $1 \cdot 806{,}7 = 806{,}7$ кДж
- $\mathrm{C{-}H}$ в $\mathrm{C_2H_6}$: 6 связей → $6 \cdot 413{,}8 = 2482{,}8$ кДж.'''
    candidates = reaction_candidates(text)
    assert len(candidates) == 1
    assert check_reaction_balance(candidates[0]).balanced
    broken = reaction_candidates(text.replace(r'2\,\mathrm{H_2', r'3\,\mathrm{H_2'))
    assert not check_reaction_balance(broken[0]).balanced


def test_numeric_mole_count_transition_is_not_a_chemical_reaction():
    assert reaction_candidates(r"Число моль газа уменьшается ($4 \to 2$).") == []
    candidates = reaction_candidates(r"Число моль ($4 \to 2$); N2 + 3H2 \to 2NH3")
    assert len(candidates) == 1
    assert check_reaction_balance(candidates[0]).balanced


def test_latex_display_delimiters_do_not_drop_reaction_reagents():
    text = r"\[\mathrm{CH_3COOH} + \mathrm{NaOH} \rightarrow \mathrm{CH_3COONa} + \mathrm{H_2O}\]"
    candidates = reaction_candidates(text)
    assert len(candidates) == 1
    balance = check_reaction_balance(candidates[0])
    assert len(balance.reactants) == len(balance.products) == 2
    assert balance.balanced
    bad = reaction_candidates(text.replace('CH_3COONa', 'CH_3COOK'))
    assert not check_reaction_balance(bad[0]).balanced


def test_latex_electrolysis_counts_ionic_coefficients_and_electron_loss():
    text = r'''Катод $(-)$: $2\mathrm{H_2O} + 2e^- \rightarrow \mathrm{H_2}\uparrow + 2\mathrm{OH^-}$.
Анод $(+)$: $2\mathrm{H_2O} - 4e^- \rightarrow \mathrm{O_2}\uparrow + 4\mathrm{H^+}$.
Суммарно: $2\mathrm{H_2O} \rightarrow 2\mathrm{H_2}\uparrow + \mathrm{O_2}\uparrow$.'''
    candidates = reaction_candidates(text)
    assert len(candidates) == 3
    assert all(check_reaction_balance(c).balanced for c in candidates)
    # Wrong electron count must still fail charge conservation.
    bad = reaction_candidates(text.replace('+ 2e^-', '+ 3e^-'))
    assert not check_reaction_balance(bad[0]).balanced


def test_enthalpy_function_argument_keeps_the_entire_reaction():
    text = r'$\Delta_r H^{\circ}_{298}(\mathrm{N_2H_4(г)} + \mathrm{O_2(г)} \rightarrow \mathrm{N_2(г)} + 2\,\mathrm{H_2O(г)}) = -577$ кДж'
    candidates = reaction_candidates(text)
    assert len(candidates) == 1
    balance = check_reaction_balance(candidates[0])
    assert len(balance.reactants) == len(balance.products) == 2
    assert balance.balanced
    assert not check_reaction_balance(reaction_candidates(text.replace('2\\,', '3\\,'))[0]).balanced
