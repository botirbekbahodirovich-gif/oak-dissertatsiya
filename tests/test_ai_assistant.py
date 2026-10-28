import sys
import types
from types import SimpleNamespace as NS

import pytest

from services import ai_assistant, ai_client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'testing')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'x')
    monkeypatch.setenv('GEMINI_API_KEY', 'x')
    monkeypatch.delenv('AI_PROVIDERS', raising=False)


def test_expand_terms_latin_cyrillic_english():
    terms = ai_assistant.expand_terms('blokcheyn', ['blockchain', 'блокчейн'])
    assert 'blokcheyn' in terms and 'блокчейн' in terms and 'blockchain' in terms
    assert len(terms) == len(set(terms)) <= ai_assistant.MAX_PATTERNS


def test_expand_terms_drops_short_variants():
    terms = ai_assistant.expand_terms("sun'iy intellekt", ['AI', 'ИИ', 'artificial intelligence'])
    assert 'ai' not in terms and 'ии' not in terms and 'artificial intelligence' in terms


def test_expand_terms_cyrillic_input_gets_latin():
    assert 'tibbiyot' in ai_assistant.expand_terms('тиббиёт')


def test_build_where_is_parameterised_and_whitelisted():
    where, params, _ = ai_assistant.build_where(
        {'query': "x'; DROP TABLE users;--", 'field': 'mavzu; DROP', 'daraja': 'PhD', 'year_from': 2020})
    assert 'DROP' not in where and where.count('%s') == len(params)
    assert any('drop table' in p for p in params if isinstance(p, str))
    assert 'PHD' in params and 2020 in params
    assert 'olim ILIKE' in where  # noto'g'ri field → barcha ustunlar


def test_build_where_empty_means_whole_table():
    assert ai_assistant.build_where({})[0] == 'TRUE'


class _FakeClient:
    def __init__(self, responses, seen):
        self._responses, self.seen = list(responses), seen
        self.messages = self

    def create(self, **kw):
        self.seen.append(kw)
        return self._responses.pop(0)


def _resp(stop, *blocks):
    return NS(stop_reason=stop, content=list(blocks),
              usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                       cache_creation_input_tokens=0))


def _install_fake(monkeypatch, responses):
    seen = []
    fake = types.ModuleType('anthropic')
    fake.Anthropic = lambda **kw: _FakeClient(responses, seen)
    fake.APIStatusError = type('APIStatusError', (Exception,), {})
    fake.APIConnectionError = type('APIConnectionError', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'anthropic', fake)
    return seen


def test_greeting_answers_without_tools(monkeypatch):
    seen = _install_fake(monkeypatch, [_resp('end_turn', NS(type='text', text='Assalomu alaykum!'))])
    text, used = ai_assistant.answer('salom')
    assert text == 'Assalomu alaykum!' and used == []
    assert seen[0]['tool_choice'] == {'type': 'auto'} and seen[0]['tools'] is ai_assistant.TOOLS


def test_tool_round_trip_numbers_come_from_tool(monkeypatch):
    calls = []
    monkeypatch.setitem(ai_assistant._HANDLERS, 'dissertation_stats',
                        lambda a: calls.append(a) or {'total_count': 42, 'groups': []})
    seen = _install_fake(monkeypatch, [
        _resp('tool_use', NS(type='tool_use', id='t1', name='dissertation_stats',
                             input={'query': 'blokcheyn', 'group_by': 'yil'})),
        _resp('end_turn', NS(type='text', text='Bazada **42** ta ish bor.')),
    ])
    text, used = ai_assistant.answer('blokcheyn bo‘yicha nechta ish bor?')
    assert '42' in text and calls == [{'query': 'blokcheyn', 'group_by': 'yil'}]
    tool_result = seen[1]['messages'][-1]['content'][0]
    assert tool_result['tool_use_id'] == 't1' and '"total_count": 42' in tool_result['content']
    assert used[0]['name'] == 'dissertation_stats'


def test_tool_error_is_reported_to_model_not_hidden(monkeypatch):
    def boom(a):
        raise RuntimeError('db down')
    monkeypatch.setitem(ai_assistant._HANDLERS, 'search_dissertations', boom)
    seen = _install_fake(monkeypatch, [
        _resp('tool_use', NS(type='tool_use', id='t1', name='search_dissertations', input={'query': 'x'})),
        _resp('end_turn', NS(type='text', text="Hozir bazaga ulanib bo'lmadi.")),
    ])
    ai_assistant.answer('x haqida')
    assert seen[1]['messages'][-1]['content'][0]['is_error'] is True


def test_last_round_disables_tools(monkeypatch):
    loop = [_resp('tool_use', NS(type='tool_use', id=f't{i}', name='search_dissertations', input={}))
            for i in range(ai_assistant.MAX_TOOL_ROUNDS)]
    monkeypatch.setitem(ai_assistant._HANDLERS, 'search_dissertations', lambda a: {'total_count': 0})
    seen = _install_fake(monkeypatch, loop + [_resp('end_turn', NS(type='text', text='ok'))])
    assert ai_assistant.answer('q')[0] == 'ok'
    assert seen[-1]['tool_choice'] == {'type': 'none'}


def test_falls_back_to_gemini_with_db_context(monkeypatch):
    fake = types.ModuleType('anthropic')

    class StatusErr(Exception):
        status_code, message = 529, 'overloaded'
    fake.APIStatusError, fake.APIConnectionError = StatusErr, ConnectionError

    def create(**kw):
        raise StatusErr()
    fake.Anthropic = lambda **kw: NS(messages=NS(create=create))
    monkeypatch.setitem(sys.modules, 'anthropic', fake)
    monkeypatch.setitem(ai_assistant.__dict__, 'search_dissertations',
                        lambda a: {'total_count': 7, 'rows': []})
    got = {}

    def fake_complete(feature, system, user, **kw):
        got.update(kw, user=user)
        return 'Gemini javobi'
    monkeypatch.setattr(ai_client, 'complete', fake_complete)
    text, used = ai_assistant.answer('tibbiyot')
    assert text == 'Gemini javobi' and got['providers'] == ['gemini']
    assert '"total_count": 7' in got['user'] and used[0]['fallback'] is True
