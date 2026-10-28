import json
import logging

import pytest

from services import ai_client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'testing')  # DB log o'chiq, faqat journal qatori
    for k in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GROQ_API_KEY', 'AI_PROVIDERS',
              'AI_FAST_MODEL', 'AI_DEEP_MODEL'):
        monkeypatch.delenv(k, raising=False)


def _ok(text='javob'):
    return lambda *a, **k: {'text': text, 'input_tokens': 1000, 'output_tokens': 200,
                            'cache_read': 0, 'cache_write': 0}


def _fail(msg):
    def f(*a, **k):
        raise ai_client._ProviderError(msg)
    return f


def _records(caplog):
    return [json.loads(r.getMessage().split(' ', 1)[1])
            for r in caplog.records if r.getMessage().startswith('AI_REQUEST {')]


def test_no_keys_raises_not_configured():
    with pytest.raises(ai_client.AIError) as ei:
        ai_client.complete('t', 's', 'u')
    assert ei.value.user_message == ai_client.USER_MESSAGE_NOT_CONFIGURED


def test_default_order_is_anthropic_then_gemini_groq_opt_in(monkeypatch):
    for k in ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GROQ_API_KEY'):
        monkeypatch.setenv(k, 'x')
    assert ai_client.configured_providers() == ['anthropic', 'gemini']
    monkeypatch.setenv('AI_PROVIDERS', 'anthropic,gemini,groq')
    assert ai_client.configured_providers() == ['anthropic', 'gemini', 'groq']


def test_tier_models_come_from_env(monkeypatch):
    assert ai_client._model_for('anthropic', 'fast') == 'claude-haiku-5-5'
    assert ai_client._model_for('anthropic', 'deep') == 'claude-sonnet-5-5'
    monkeypatch.setenv('AI_DEEP_MODEL', 'claude-opus-5-5')
    assert ai_client._model_for('anthropic', 'deep') == 'claude-opus-5-5'


def test_falls_back_to_gemini_and_logs_both_attempts(monkeypatch, caplog):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'x')
    monkeypatch.setenv('GEMINI_API_KEY', 'x')
    monkeypatch.setitem(ai_client._CALLERS, 'anthropic', _fail('HTTP 529: overloaded'))
    monkeypatch.setitem(ai_client._CALLERS, 'gemini', _ok('zaxira javob'))
    caplog.set_level(logging.INFO, logger='services.ai_client')

    assert ai_client.complete('ai_yordamchi', 's', 'u') == 'zaxira javob'

    recs = _records(caplog)
    assert [(r['provider'], r['ok']) for r in recs] == [('anthropic', False), ('gemini', True)]
    assert recs[0]['error'] == 'HTTP 529: overloaded'
    assert recs[0]['feature'] == 'ai_yordamchi'
    assert recs[1]['input_tokens'] == 1000 and recs[1]['latency_ms'] >= 0


def test_all_fail_raises_with_every_cause(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'x')
    monkeypatch.setenv('GEMINI_API_KEY', 'x')
    monkeypatch.setitem(ai_client._CALLERS, 'anthropic', _fail('HTTP 401: invalid x-api-key'))
    monkeypatch.setitem(ai_client._CALLERS, 'gemini', _fail('HTTP 404: model not found'))
    with pytest.raises(ai_client.AIError) as ei:
        ai_client.complete('t', 's', 'u', tier='deep')
    assert '401' in str(ei.value) and '404' in str(ei.value)
    assert ei.value.user_message == ai_client.USER_MESSAGE_UNAVAILABLE


def test_unexpected_exception_is_not_swallowed_silently(monkeypatch, caplog):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'x')

    def boom(*a, **k):
        raise KeyError('content')
    monkeypatch.setitem(ai_client._CALLERS, 'anthropic', boom)
    caplog.set_level(logging.INFO, logger='services.ai_client')
    with pytest.raises(ai_client.AIError):
        ai_client.complete('t', 's', 'u')
    assert 'KeyError' in _records(caplog)[0]['error']


def test_cost_uses_price_table():
    r = {'input_tokens': 1_000_000, 'output_tokens': 1_000_000, 'cache_read': 0, 'cache_write': 0}
    assert ai_client._cost('claude-sonnet-5-5', r) == 12.0
    assert ai_client._cost('gemini-flash-lite-latest', r) is None


def test_extract_json_handles_fences():
    assert ai_client.extract_json('```json\n{"a": 1}\n```') == {'a': 1}
    with pytest.raises(ValueError):
        ai_client.extract_json('salom')
