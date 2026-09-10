import asyncio

import pytest

from app.llm import client
from app.models import Provider
from app.services import validation, task_approval
from test_model_use_policy import _policy, _model, _validation_kwargs


@pytest.mark.parametrize('critic_pass', [True, False])
def test_uncovered_core_runs_two_blind_solutions_and_requires_editor(monkeypatch, critic_pass):
    calls = []
    async def solve(*args, **kwargs):
        calls.append((args, kwargs))
        return {'status': 'ok', 'solution': 'Из условия вычисляем массу: m = 5 г.',
                'answer': 'm = 5 г', 'error': '', 'duration_ms': 1, 'tokens_total': 1}
    async def critic(*args, **kwargs):
        return {'status': 'pass' if critic_pass else 'fail',
                'checks': {k: critic_pass for k in validation.CRITIC_REQUIRED_CHECKS},
                'issues': [] if critic_pass else ['Неверно выбран закон расчёта']}
    monkeypatch.setattr(validation, 'current_model_use_policy', _policy)
    monkeypatch.setattr(task_approval, 'current_model_use_policy', _policy)
    monkeypatch.setattr(validation, 'solver_check', solve)
    monkeypatch.setattr(validation, 'critic_check', critic)
    result = asyncio.run(validation.run_validation(
        **_validation_kwargs(), validation_config={'task_kind': 'calculation', 'chemistry_check': 'auto'},
        chemistry_facts={}, discipline_context='Общая и неорганическая химия',
        solver_provider=Provider(id='p', name='DeepSeek', base_url='https://example.test'),
        solver_model=_model('deepseek-v4-pro'),
    ))
    assert len(calls) == 2
    # Neither blind solver receives the reference solution or answer.
    assert all(call[0][2] == _validation_kwargs()['statement'] and call[0][3] == '' for call in calls)
    assert result['verdict'] == ('validated' if critic_pass else 'needs_review')
    assert result['chemistry']['admission_effect'] == ('reviewed' if critic_pass else 'limited')
    if critic_pass:
        assert result['critic']['basis'] == 'independent_subject_review'
        assert task_approval.validation_is_current_decision(result)
        result['verifier']['solution'] = ''
        assert not task_approval.validation_is_current_decision(result)
    else:
        assert any('Неверно выбран' in reason for reason in result['reasons'])


def test_total_stream_deadline_cancels_even_a_live_stream(monkeypatch):
    cancelled = []
    async def endless(*args, **kwargs):
        try:
            while True:
                await asyncio.sleep(0.001)
        finally:
            cancelled.append(True)
    monkeypatch.setattr(client, '_chat', endless)
    with pytest.raises(client.LlmError, match='Запрос остановлен'):
        asyncio.run(client.chat(timeout=0.01))
    assert cancelled == [True]


@pytest.mark.parametrize('critic_pass', [True, False])
def test_verified_typed_calculation_still_gets_semantic_review(monkeypatch, critic_pass):
    async def solve(*_args, **_kwargs):
        return {'status': 'ok', 'solution': 'Вычисляем массу: m = 5 г.',
                'answer': 'm = 5 г', 'error': '', 'duration_ms': 1, 'tokens_total': 1}
    seen = []
    async def critic(*_args, **_kwargs):
        seen.append(True)
        return {'status': 'pass' if critic_pass else 'fail',
                'checks': {k: critic_pass for k in validation.CRITIC_REQUIRED_CHECKS},
                'issues': [] if critic_pass else ['Пропущен результат']}
    monkeypatch.setattr(validation, 'current_model_use_policy', _policy)
    monkeypatch.setattr(task_approval, 'current_model_use_policy', _policy)
    monkeypatch.setattr(validation, 'solver_check', solve)
    monkeypatch.setattr(validation, 'critic_check', critic)
    monkeypatch.setattr(validation, 'chemistry_admission_evidence', lambda **_: {
        'validation_version': validation.CHEMISTRY_VALIDATION_VERSION,
        'admission_effect': 'pass', 'blocking_codes': [], 'indeterminate_codes': [],
        'warning_codes': [], 'required_check_ids': ['analytical.faraday'],
        'results': [{'check_id': 'analytical.faraday', 'state': 'pass'}],
    })
    kwargs = _validation_kwargs()
    kwargs['reference_answer'] = 'm = 5 г. Расчёт выполняется в 2 этапа.'
    result = asyncio.run(validation.run_validation(
        **kwargs, validation_config={'task_kind': 'calculation', 'chemistry_check': 'analytical.faraday'},
        chemistry_facts={}, discipline_context='Общая и неорганическая химия',
        solver_provider=Provider(id='p', name='DeepSeek', base_url='https://example.test'),
        solver_model=_model('deepseek-v4-pro'),
    ))
    assert seen
    assert result['verdict'] == ('validated' if critic_pass else 'needs_review')
    assert result['chemistry']['admission_effect'] == 'pass'
    if critic_pass:
        assert task_approval.validation_is_current_decision(result)
        result['solver']['solution'] = ''
        assert not task_approval.validation_is_current_decision(result)
