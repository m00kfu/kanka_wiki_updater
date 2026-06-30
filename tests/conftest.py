import os
import unittest.mock as mock

import pytest


@pytest.fixture(autouse=True, scope='session')
def mock_env():
    """Set minimal env vars for modules that require them."""
    os.environ.setdefault('KANKA_TOKEN', 'test-token')
    os.environ.setdefault('KANKA_CAMPAIGN_ID', '1')
    yield


@pytest.fixture
def mock_requests(monkeypatch):
    """Provide a mocked requests module."""
    monkeypatch.setattr('kanka_wiki_updater.llm_providers.requests', mock)
