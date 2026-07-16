GRADING_JSON_CONTRACT = """
{
  "unreadable": false,
  "unreadable_reason": null,
  "total_score": <число>,
  "max_score": <число>,
  "criteria_scores": [
    {"criterion_name": "название критерия", "score": <число>, "max_score": <число>, "comment": "комментарий"}
  ],
  "detailed_analysis": {
    "method_correctness": "анализ метода",
    "calculations": "анализ вычислений",
    "units_and_dimensions": "анализ размерностей",
    "chemical_rules": "проверка правил дисциплины",
    "errors_found": ["список ошибок"]
  },
  "feedback": "общий фидбек для студента с рекомендациями",
  "recommendations": ["рекомендация 1", "рекомендация 2"],
  "confidence": <число 0..1 — диагностическая самооценка модели, не сигнал допуска>,
  "needs_teacher_review": <true|false>
}
""".strip()

GENERATION_JSON_CONTRACT = """
{
  "tasks": [
    {
      "statement": "полное условие задачи (Markdown, формулы в $...$)",
      "reference_solution": "подробное эталонное решение по шагам",
      "answer": "краткий финальный ответ (число с единицами измерения / формула / краткий текст)",
      "rubric": [
        {"criterion_name": "название критерия", "max_score": <число>, "description": "за что начисляется"}
      ],
      "max_score": <число>,
      "difficulty": "easy|medium|hard",
      "topic": "тема задачи",
      "data_used": [
        {"sheet_title": "название справочной таблицы", "values": ["какие именно значения взяты"]}
      ],
      "chemistry_facts": {
        "<тип проверяемого расчёта>": {"<величина>": "<число с явной единицей или точный параметр>"}
      }
    }
  ]
}
""".strip()

CHEMISTRY_FACTS_GUIDE = """
Поле chemistry_facts — машиночитаемое доказательство расчёта, а не пересказ решения.
Копируйте в него только величины и итоговые параметры, которые явно присутствуют в условии,
эталонном решении или answer. Не придумывайте недостающие значения. Для неприменимых блоков ничего не пишите.
Если поддерживаемого численного расчёта нет, верните {}.

Поддерживаемые блоки:
- stoichiometry: reaction с единственной стрелкой -> (не знаком =), reactant_amounts
  (формула -> "число unit") для всех количественно заданных реагентов, target_species,
  target_amount (обязательно количество вещества), limiting_reagent при наличии.
  Если компонент среды явно дан в избытке без количества, перечислите его формулу в
  excess_reactants (JSON-массив); не объявляйте избыток без прямого указания в условии;
- dilution: c1, v1, c2, v2 — каждое значение строкой с единицей;
- titration: analyte и titrant, внутри concentration, volume и equivalent_factor
  либо stoichiometric_coefficient;
- gravimetry: analyte_stoichiometric_coefficient, weighing_form_stoichiometric_coefficient,
  analyte_molar_mass, weighing_form_molar_mass, gravimetric_factor, weighing_form_mass,
  analyte_mass. Массы и молярные массы задаются с единицами; исходные молярные массы и масса
  весовой формы должны быть явно даны в условии. Проверяются F=(ν_a M_a)/(ν_f M_f) и m_a=F m_f.
  F и m_a вычисляйте из неокруглённого промежуточного результата: каждая показанная цифра обязана
  совпадать с арифметически правильным округлением, а точность результата — не ниже точности исходных данных;
- conductometry: resistance, conductance, cell_constant, conductivity — все четыре величины
  с явными единицами. В условии должны быть даны R или G и постоянная ячейки; проверяются
  G=1/R и κ=K_cell G без неявных коэффициентов перевода;
- faraday: доступные current, time, charge, electron_amount, mass, molar_mass,
  electrons, current_efficiency; если используется n_e=Q/F или расчёт массы, обязательна
  faraday_constant с единицей C/mol, причём её численное значение должно быть дано в условии;
- calibration: slope, intercept, signal, concentration, при наличии calibration_range [min, max];
- bet: для линейной формы slope, intercept, monolayer_capacity, bet_constant и при
  наличии relative_pressures; для удельной поверхности обязательны variant="surface_area",
  monolayer_amount_per_mass, molecular_cross_section, avogadro_constant, specific_surface с явными
  единицами; NA задаётся численно и в условии, и в avogadro_constant (mol^-1);
- smoluchowski: mobility, viscosity, relative_permittivity, vacuum_permittivity, zeta,
  при наличии kappa_a и claims_applicable; численное ε0 должно быть дано в условии студенту;
- dlvo: ionic_strength, debye_length и debye_model="water_1_1_25c" только для воды,
  1:1 электролита при 25 °C; либо particle_radius, separation, claims_derjaguin.
  claims_derjaguin — строго JSON boolean, не строка; если задача просит проверку геометрии,
  answer обязан содержать и длину Дебая, и численное h/a, и краткий вывод о применимости;
  claims_dlvo_sufficient и non_dlvo_forces_present — только если это прямо сформулировано.

Физические величины всегда задавайте как "число unit" или {"value": число, "unit": "unit"}.
""".strip()

# Напоминание моделям: LaTeX внутри JSON-строк требует двойного бэкслеша.
JSON_LATEX_ESCAPING_NOTE = (
    "ВАЖНО: внутри строк JSON обратная косая черта экранируется — LaTeX-команды пишите "
    "с двойным бэкслешем: \\\\frac, \\\\alpha, \\\\text, \\\\cdot."
)
