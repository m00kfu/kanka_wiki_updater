"""Tests for LLM client JSON extraction."""

import pytest

from kanka_wiki_updater.llm_client import LLMError, _extract_json


def test_extract_json_valid():
    text = '{"updated_entry": "Hello", "change_summary": "Test"}'
    result = _extract_json(text)
    assert result == {'updated_entry': 'Hello', 'change_summary': 'Test'}


def test_extract_json_with_markdown_fence():
    text = '```json\n{"updated_entry": "Hello"}\n```'
    result = _extract_json(text)
    assert result == {'updated_entry': 'Hello'}


def test_extract_json_no_json_raises():
    with pytest.raises(LLMError):
        _extract_json('Just plain text, no JSON here')


def test_extract_json_truncation_warning(tmp_path):
    import kanka_wiki_updater.config as config_mod

    original_max_tokens = config_mod.LLM_MAX_TOKENS
    try:
        config_mod.LLM_MAX_TOKENS = 1024
        text = '{"updated_entry": "Cut off", "change_summary": ""}'
        result = _extract_json(text, finish_reason='length')
        assert '[TRUNCATED:' in result.get('change_summary', '')
    finally:
        config_mod.LLM_MAX_TOKENS = original_max_tokens


def test_extract_json_with_escaped_quotes():
    text = '{"updated_entry": "She said \\"hello\\""}'
    result = _extract_json(text)
    assert result['updated_entry'] == 'She said "hello"'


def test_extract_json_nested_object():
    text = '{"relation_changes": [{"action": "create", "target_name": "Bob"}]}'
    result = _extract_json(text)
    assert len(result['relation_changes']) == 1
    assert result['relation_changes'][0]['action'] == 'create'


def test_extract_json_greedy_brace_match():
    # Greedy \{.*\} captures from first { to last }, so both objects become a list
    text = '{"a": 1} some prose {"b": 2}'
    result = _extract_json(text)
    assert len(result) == 2
