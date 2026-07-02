"""Tests for Gemini provider integration."""

from unittest.mock import MagicMock, patch

import pytest

from kanka_wiki_updater.llm_providers import (
    LLMError,
    _extract_json,
    chat_json,
    gemini_chat,
    lmstudio_chat,
)


class TestExtractJson:
    """Shared JSON extraction tests -- work for any provider."""

    def test_extract_json_valid(self):
        text = '{"updated_entry": "Hello", "change_summary": "Test"}'
        result = _extract_json(text)
        assert result == {'updated_entry': 'Hello', 'change_summary': 'Test'}

    def test_extract_json_with_markdown_fence(self):
        text = '```json\n{"updated_entry": "Hello"}\n```'
        result = _extract_json(text)
        assert result == {'updated_entry': 'Hello'}

    def test_extract_json_no_json_raises(self):
        with pytest.raises(LLMError):
            _extract_json('Just plain text, no JSON here')

    def test_extract_json_truncation_warning(self):
        import kanka_wiki_updater.config as config_mod

        original_max_tokens = config_mod.LLM_MAX_TOKENS
        try:
            config_mod.LLM_MAX_TOKENS = 1024
            text = '{"updated_entry": "Cut off", "change_summary": ""}'
            result = _extract_json(text, finish_reason='length')
            assert '[TRUNCATED:' in result.get('change_summary', '')
        finally:
            config_mod.LLM_MAX_TOKENS = original_max_tokens

    def test_extract_json_with_escaped_quotes(self):
        text = '{"updated_entry": "She said \\"hello\\""}'
        result = _extract_json(text)
        assert result['updated_entry'] == 'She said "hello"'


class TestGeminiChat:
    """Tests for the Gemini provider implementation."""

    @pytest.fixture(autouse=True)
    def setup_gemini_env(self):
        import kanka_wiki_updater.config as config_mod
        import kanka_wiki_updater.llm_providers as llm_mod

        self._orig_provider = config_mod.LLM_PROVIDER
        self._orig_key = config_mod.GEMINI_API_KEY
        self._orig_llm_provider = getattr(llm_mod, 'LLM_PROVIDER', None)
        self._orig_gemini_key = getattr(llm_mod, 'GEMINI_API_KEY', None)

        try:
            with patch('kanka_wiki_updater.llm_providers.config') as mock_cfg:
                mock_cfg.LLM_PROVIDER = 'gemini'
                mock_cfg.GEMINI_API_KEY = 'test-key'
                # Also set on the module-level cached variables where they're consumed
                import kanka_wiki_updater.llm_providers as llm_mod2

                llm_mod2.GEMINI_API_KEY = 'test-key'
                llm_mod2.LLM_PROVIDER = 'gemini'

                yield
        finally:
            config_mod.LLM_PROVIDER = self._orig_provider
            config_mod.GEMINI_API_KEY = self._orig_key
            import kanka_wiki_updater.llm_providers as llm_mod3

            if self._orig_llm_provider is not None:
                llm_mod3.LLM_PROVIDER = self._orig_llm_provider
            else:
                delattr(llm_mod3, 'LLM_PROVIDER')
            if self._orig_gemini_key is not None:
                llm_mod3.GEMINI_API_KEY = self._orig_gemini_key
            else:
                delattr(llm_mod3, 'GEMINI_API_KEY')

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_success(self, mock_post):
        """Successful Gemini response returns parsed JSON."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'candidates': [
                {
                    'content': {'parts': [{'text': '{"updated_entry": "Test", "change_summary": "OK"}'}]},
                    'finishReason': 'STOP',
                }
            ],
        }
        mock_post.return_value = mock_response

        result = gemini_chat('system prompt', 'user prompt')
        assert result == {'updated_entry': 'Test', 'change_summary': 'OK'}

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_with_system_instruction(self, mock_post):
        """System prompt is sent as system_instruction."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'candidates': [
                {
                    'content': {'parts': [{'text': '{"updated_entry": "X"}'}]},
                    'finishReason': 'STOP',
                }
            ],
        }
        mock_post.return_value = mock_response

        gemini_chat('my system prompt', 'user message')

        call_args = mock_post.call_args
        payload = call_args.kwargs['json']
        assert payload['system_instruction'] == {
            'role': 'model',
            'parts': [{'text': 'my system prompt'}],
        }

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_no_system_prompt(self, mock_post):
        """No system_instruction key when system_prompt is empty."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'candidates': [
                {
                    'content': {'parts': [{'text': '{"updated_entry": "X"}'}]},
                    'finishReason': 'STOP',
                }
            ],
        }
        mock_post.return_value = mock_response

        gemini_chat('', 'user message')

        call_args = mock_post.call_args
        payload = call_args.kwargs['json']
        assert 'system_instruction' not in payload

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_truncation(self, mock_post):
        """MAX_TOKENS finish reason maps to truncation warning."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'candidates': [
                {
                    'content': {'parts': [{'text': '{"updated_entry": "Cut", "change_summary": ""}'}]},
                    'finishReason': 'MAX_TOKENS',
                }
            ],
        }
        mock_post.return_value = mock_response

        result = gemini_chat('system', 'user')
        assert '[TRUNCATED:' in result['change_summary']

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_no_api_key(self, mock_post):
        """Raises LLMError when API key is empty."""
        import kanka_wiki_updater.llm_providers as llm_mod

        original_key = getattr(llm_mod.config, 'GEMINI_API_KEY', None)
        try:
            # Patch at the point of consumption (llm_providers.config), not on config.py directly
            llm_mod.config.GEMINI_API_KEY = ''
            with pytest.raises(LLMError) as exc_info:
                gemini_chat('system', 'user')
            assert 'GEMINI_API_KEY' in str(exc_info.value)
        finally:
            if original_key is not None:
                llm_mod.config.GEMINI_API_KEY = original_key

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_401_error(self, mock_post):
        """401 response raises LLMError with clear message."""
        resp = MagicMock()
        resp.status_code = 401
        mock_post.return_value = resp

        with pytest.raises(LLMError) as exc_info:
            gemini_chat('system', 'user')
        assert 'invalid or expired API key' in str(exc_info.value)

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_403_error(self, mock_post):
        """403 response raises LLMError with quota message."""
        resp = MagicMock()
        resp.status_code = 403
        mock_post.return_value = resp

        with pytest.raises(LLMError) as exc_info:
            gemini_chat('system', 'user')
        assert 'quota exceeded' in str(exc_info.value).lower() or 'not enabled' in str(exc_info.value).lower()

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_429_error(self, mock_post):
        """429 response raises LLMError with rate limit message."""
        resp = MagicMock()
        resp.status_code = 429
        mock_post.return_value = resp

        with pytest.raises(LLMError) as exc_info:
            gemini_chat('system', 'user')
        assert 'rate limit' in str(exc_info.value).lower()

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_gemini_chat_empty_response(self, mock_post):
        """Empty content raises LLMError."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'candidates': [
                {
                    'content': {'parts': []},
                    'finishReason': 'STOP',
                }
            ],
        }
        mock_post.return_value = mock_response

        with pytest.raises(LLMError) as exc_info:
            gemini_chat('system', 'user')
        assert 'no parts' in str(exc_info.value).lower() or 'empty content' in str(exc_info.value).lower()


class TestLmStudioChat:
    """Tests for the LM Studio provider (unchanged behavior)."""

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_lmstudio_chat_success(self, mock_post):
        """Successful LM Studio response returns parsed JSON."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'choices': [
                {
                    'message': {'content': '{"updated_entry": "Test"}'},
                    'finish_reason': 'stop',
                }
            ],
        }
        mock_post.return_value = mock_response

        result = lmstudio_chat('system prompt', 'user prompt')
        assert result == {'updated_entry': 'Test'}


class TestChatJsonDispatcher:
    """Tests for the dispatcher that routes to providers."""

    @pytest.fixture(autouse=True)
    def restore_provider(self):
        import kanka_wiki_updater.config as config_mod

        self._original = config_mod.LLM_PROVIDER
        yield
        config_mod.LLM_PROVIDER = self._original

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_dispatcher_routes_to_lmstudio_by_default(self, mock_post):
        """Default provider is LM Studio."""
        import kanka_wiki_updater.config as config_mod

        config_mod.LLM_PROVIDER = 'lmstudio'

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'choices': [
                {
                    'message': {'content': '{"updated_entry": "X"}'},
                    'finish_reason': 'stop',
                }
            ],
        }
        mock_post.return_value = mock_response

        result = chat_json('system', 'user')
        assert result == {'updated_entry': 'X'}

    @patch('kanka_wiki_updater.llm_providers.requests.post')
    def test_dispatcher_routes_to_gemini(self, mock_post):
        """Gemini provider is used when configured."""
        import kanka_wiki_updater.config as config_mod
        import kanka_wiki_updater.llm_providers as llm_mod

        config_mod.LLM_PROVIDER = 'gemini'
        config_mod.GEMINI_API_KEY = 'test-key'
        # Also set on the module-level cached variables where they're consumed
        llm_mod.GEMINI_API_KEY = 'test-key'
        llm_mod.LLM_PROVIDER = 'gemini'

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'candidates': [
                {
                    'content': {'parts': [{'text': '{"updated_entry": "Y"}'}]},
                    'finishReason': 'STOP',
                }
            ],
        }
        mock_post.return_value = mock_response

        result = chat_json('system', 'user')
        assert result == {'updated_entry': 'Y'}


class TestConfigValidation:
    """Tests for config.py validation logic."""

    def test_gemini_without_api_key_raises(self):
        """Setting LLM_PROVIDER=gemini without API key raises ValueError."""
        import importlib
        import unittest.mock as mock

        import kanka_wiki_updater.config as config_mod

        # Save original values
        _orig_provider = config_mod.LLM_PROVIDER
        _orig_key = config_mod.GEMINI_API_KEY

        try:
            with mock.patch('kanka_wiki_updater.config.load_dotenv', return_value=None):
                config_mod.os.environ['LLM_PROVIDER'] = 'gemini'
                config_mod.os.environ['GEMINI_API_KEY'] = ''

                with pytest.raises(ValueError) as exc_info:
                    importlib.reload(config_mod)

            assert 'GEMINI_API_KEY' in str(exc_info.value)
        finally:
            config_mod.LLM_PROVIDER = _orig_provider
            config_mod.GEMINI_API_KEY = _orig_key

    def test_lmstudio_without_api_key_ok(self):
        """LM Studio provider works without GEMINI_API_KEY."""
        import importlib
        import unittest.mock as mock

        import kanka_wiki_updater.config as config_mod

        # Save original values
        _orig_provider = config_mod.LLM_PROVIDER
        _orig_key = config_mod.GEMINI_API_KEY

        try:
            with mock.patch('kanka_wiki_updater.config.load_dotenv', return_value=None):
                config_mod.os.environ['LLM_PROVIDER'] = 'lmstudio'
                config_mod = importlib.reload(config_mod)
                assert config_mod.LLM_PROVIDER == 'lmstudio'
        finally:
            config_mod.LLM_PROVIDER = _orig_provider
            config_mod.GEMINI_API_KEY = _orig_key
