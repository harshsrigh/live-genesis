"""The adapters normalise why a provider stopped, and send the output limit and system text they are given.

Offline. The SDK client classes are always replaced; no model is called. What the archive does with a
reply cut off at the limit is covered in tests/test_archive.py.
"""
import sys
from types import SimpleNamespace

import openai
import pytest

from live_genesis import engine

CUT = 'This reply stops in the middle of a sent'


# ---- 1. Adapters normalise the stop reason -------------------------------------------------

def fake_openai(monkeypatch, *, chat=None, response=None):
    seen = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.responses = SimpleNamespace(create=self.create_response)

        def create(self, **kwargs):
            seen.append(kwargs)
            return chat

        def create_response(self, **kwargs):
            seen.append(kwargs)
            return response
    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    return seen


def fake_anthropic(monkeypatch, response):
    seen = []

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            seen.append(kwargs)
            return response
    monkeypatch.setitem(sys.modules, 'anthropic', SimpleNamespace(Anthropic=FakeAnthropic))
    return seen


def chat_reply(**choice):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=CUT), **choice)],
                           usage=SimpleNamespace(prompt_tokens=200, completion_tokens=50))


def responses_reply(**fields):
    return SimpleNamespace(output_text=CUT, usage=SimpleNamespace(input_tokens=200, output_tokens=50), **fields)


def anthropic_reply(**fields):
    return SimpleNamespace(content=[SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=CUT)],
                           usage=SimpleNamespace(input_tokens=200, output_tokens=50), **fields)


@pytest.mark.parametrize('choice,expected', [
    ({'finish_reason': 'length'}, 'length'), ({'finish_reason': 'stop'}, 'end'),
    ({'finish_reason': 'content_filter'}, 'other'), ({'finish_reason': None}, 'unknown'), ({}, 'unknown')])
def test_chat_completions_stop_is_normalised(monkeypatch, choice, expected):
    monkeypatch.setenv('LIVE_COMPAT_BASE_URL', 'http://localhost:11434/v1')
    seen = fake_openai(monkeypatch, chat=chat_reply(**choice))
    done = engine.infer('openai-compatible', 'm', 'p', system='the system line', max_output_tokens=120)
    assert done.stop == expected and (done.input_tokens, done.output_tokens) == (200, 50)
    assert seen[0]['max_tokens'] == 120 and seen[0]['messages'][0] == {'role': 'system', 'content': 'the system line'}


@pytest.mark.parametrize('fields,expected', [
    ({'status': 'incomplete', 'incomplete_details': SimpleNamespace(reason='max_output_tokens')}, 'length'),
    ({'status': 'incomplete', 'incomplete_details': SimpleNamespace(reason='content_filter')}, 'other'),
    ({'status': 'incomplete'}, 'other'), ({'status': 'completed'}, 'end'), ({}, 'unknown')])
def test_openai_responses_stop_is_normalised(monkeypatch, fields, expected):
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    seen = fake_openai(monkeypatch, response=responses_reply(**fields))
    assert engine.infer('openai', 'm', 'p', system='the system line', max_output_tokens=900).stop == expected
    assert seen[0]['max_output_tokens'] == 900 and seen[0]['instructions'] == 'the system line'


@pytest.mark.parametrize('fields,expected', [
    ({'stop_reason': 'max_tokens'}, 'length'), ({'stop_reason': 'end_turn'}, 'end'), ({'stop_reason': 'stop_sequence'}, 'end'),
    ({'stop_reason': 'refusal'}, 'other'), ({'stop_reason': None}, 'unknown'), ({}, 'unknown')])
def test_anthropic_stop_is_normalised(monkeypatch, fields, expected):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake-key-never-used')
    seen = fake_anthropic(monkeypatch, anthropic_reply(**fields))
    done = engine.infer('anthropic', 'm', 'p', system='the system line', max_output_tokens=2000)
    assert done.stop == expected and done.text == CUT
    assert seen[0]['max_tokens'] == 2000 and seen[0]['system'] == 'the system line'


def test_completion_keeps_its_positional_form_and_the_limit_and_system_text_are_required(monkeypatch):
    assert engine.Completion('t', 1, 2).stop == 'unknown'
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-never-used')
    seen = fake_openai(monkeypatch, response=responses_reply())
    engine.infer('openai', 'm', 'p', system='one line', max_output_tokens=650)
    assert seen[0]['max_output_tokens'] == 650 and seen[0]['instructions'] == 'one line'
    # No default system text and no default limit exist any more: a caller that names neither is an error.
    for missing in ({}, {'system': 'one line'}, {'max_output_tokens': 650}):
        with pytest.raises(TypeError):
            engine.infer('openai', 'm', 'p', **missing)
    assert len(seen) == 1
