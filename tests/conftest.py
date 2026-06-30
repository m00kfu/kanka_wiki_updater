import os

import pytest


@pytest.fixture(autouse=True, scope='session')
def mock_env():
    """Set minimal env vars for modules that require them."""
    os.environ.setdefault('KANKA_TOKEN', 'test-token')
    os.environ.setdefault('KANKA_CAMPAIGN_ID', '1')
    yield
