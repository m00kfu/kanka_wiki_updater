"""Tests for config module — env var loading, validation, defaults."""

import importlib
import os
import unittest

import pytest


@pytest.fixture(autouse=True)
def restore_env():
    """Save and restore all config-related env vars around each test."""
    _saved = {
        k: os.environ.get(k)
        for k in ('KANKA_TOKEN', 'KANKA_CAMPAIGN_ID', 'LLM_PROVIDER', 'GEMINI_API_KEY', 'JOURNAL_BATCH_LIMIT')
    }
    yield
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestRequire:
    def test_require_returns_value_when_set(self):
        import kanka_wiki_updater.config as config_mod

        with unittest.mock.patch.dict(os.environ, {'TEST_VAR': 'hello'}):
            result = config_mod._require('TEST_VAR')
        assert result == 'hello'

    def test_require_raises_when_missing(self):
        import kanka_wiki_updater.config as config_mod

        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('NONEXISTENT_CONFIG_VAR_XYZ', None)
            with pytest.raises(RuntimeError) as exc_info:
                config_mod._require('NONEXISTENT_CONFIG_VAR_XYZ')
            assert "Missing required setting 'NONEXISTENT_CONFIG_VAR_XYZ'" in str(exc_info.value)


class TestDefaults:
    def test_kanka_base_url_defaults(self):
        import kanka_wiki_updater.config as config_mod

        assert config_mod.KANKA_BASE_URL == 'https://api.kanka.io/1.0'

    def test_lmstudio_base_url_defaults(self):
        import kanka_wiki_updater.config as config_mod

        assert config_mod.LMSTUDIO_BASE_URL == 'http://localhost:1234/v1'

    def test_session_journal_type_defaults(self):
        import kanka_wiki_updater.config as config_mod

        assert config_mod.SESSION_JOURNAL_TYPE == 'Session'

    def test_llm_timeout_defaults(self):
        import kanka_wiki_updater.config as config_mod

        assert config_mod.LLM_TIMEOUT_SECONDS == 600


class TestGeminiValidation:
    def test_gemini_without_key_raises(self):
        with unittest.mock.patch.dict(os.environ, {'LLM_PROVIDER': 'gemini', 'GEMINI_API_KEY': ''}):
            import kanka_wiki_updater.config as config_mod

            with pytest.raises(ValueError) as exc_info:
                importlib.reload(config_mod)
            assert 'GEMINI_API_KEY' in str(exc_info.value)

    def test_gemini_with_key_ok(self):
        with unittest.mock.patch.dict(os.environ, {'LLM_PROVIDER': 'gemini', 'GEMINI_API_KEY': 'sk-test'}):
            import kanka_wiki_updater.config as config_mod

            importlib.reload(config_mod)
            assert config_mod.LLM_PROVIDER == 'gemini'


class TestBatchLimit:
    def test_batch_limit_parses_int(self):
        with unittest.mock.patch.dict(os.environ, {'JOURNAL_BATCH_LIMIT': '50'}):
            import kanka_wiki_updater.config as config_mod

            importlib.reload(config_mod)
            assert config_mod.JOURNAL_BATCH_LIMIT == 50

    def test_batch_limit_none_when_empty(self):
        with unittest.mock.patch.dict(os.environ, {'JOURNAL_BATCH_LIMIT': ''}):
            import kanka_wiki_updater.config as config_mod

            importlib.reload(config_mod)
            assert config_mod.JOURNAL_BATCH_LIMIT is None


class TestCustomEnv:
    def test_custom_kanka_base_url(self):
        with unittest.mock.patch.dict(os.environ, {'KANKA_BASE_URL': 'http://localhost:8080'}):
            import kanka_wiki_updater.config as config_mod

            importlib.reload(config_mod)
            assert config_mod.KANKA_BASE_URL == 'http://localhost:8080'

    def test_custom_llm_settings(self):
        with unittest.mock.patch.dict(os.environ, {'LLM_MAX_TOKENS': '8192', 'LLM_TIMEOUT_SECONDS': '300'}):
            import kanka_wiki_updater.config as config_mod

            importlib.reload(config_mod)
            assert config_mod.LLM_MAX_TOKENS == 8192
            assert config_mod.LLM_TIMEOUT_SECONDS == 300
