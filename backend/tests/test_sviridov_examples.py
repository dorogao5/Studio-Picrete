from app.schemas import ExampleTask
from app.services.taskgen import _render_example_tasks


def test_source_provenance_survives_editor_roundtrip_and_reaches_generator():
    example = ExampleTask.model_validate({
        'source_number': '8.146', 'source_task_id': 'tb_sviridov_8_146',
        'source_document': 'Свиридов таблица для преподавателя.docx',
        'statement': 'Условие', 'solution': 'Полный эталон', 'answer': '',
    }).model_dump()
    assert example['source_number'] == '8.146'
    assert example['source_task_id'] == 'tb_sviridov_8_146'
    rendered = _render_example_tasks([example])
    assert 'Свиридов № 8.146' in rendered
    assert 'Полный эталон' in rendered


def test_general_chemistry_runs_faraday_check_and_rejects_wrong_mass():
    from app.services.chemistry_facts import chemistry_admission_evidence
    facts = {'faraday': {'current': '5 A', 'time': '9000 s', 'charge': '45000 C',
                        'mass': '14.82 g', 'molar_mass': '63.55 g/mol',
                        'electrons': 2, 'faraday_constant': '96485 C/mol'}}
    def check():
        return chemistry_admission_evidence(
            discipline='Общая и неорганическая химия', statement='Электролиз раствора соли меди. F = 96485 C/mol; I = 5 A; t = 9000 s; M = 63.55 g/mol; z = 2.',
            reference_solution='', answer='', topic='Электролиз', facts=facts,
            facts_source='generator', chemistry_check='analytical.faraday',
        )
    assert check()['admission_effect'] == 'pass'
    facts['faraday']['mass'] = '29.64 g'
    assert check()['admission_effect'] == 'block'
