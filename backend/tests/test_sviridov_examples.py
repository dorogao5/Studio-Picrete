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
