"""
Configuration for the Kanka session-note sync pipeline.

Reads settings from environment variables (or a local .env file via
python-dotenv). Copy .env.example to .env and fill in your values before
running anything.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required setting '{name}'. Copy .env.example to .env "
            f'and fill it in, or set it as an environment variable.'
        )
    return value


KANKA_TOKEN = _require('KANKA_TOKEN')
KANKA_CAMPAIGN_ID = _require('KANKA_CAMPAIGN_ID')
KANKA_BASE_URL = os.environ.get('KANKA_BASE_URL', 'https://api.kanka.io/1.0')

# LM Studio's local server is OpenAI-compatible. Default port is 1234.
LMSTUDIO_BASE_URL = os.environ.get('LMSTUDIO_BASE_URL', 'http://localhost:1234/v1')
LMSTUDIO_MODEL = os.environ.get('LMSTUDIO_MODEL', 'local-model')

# Journal "type" field to treat as session notes. Set to "" to process all journals.
SESSION_JOURNAL_TYPE = os.environ.get('SESSION_JOURNAL_TYPE', 'Session')

# Kanka allows ~30 requests/min (90/min for subscribers). Subscribers may lower this value.

REQUEST_INTERVAL = float(os.environ.get('KANKA_REQUEST_INTERVAL', '2.1'))

# Raise this if you're using a reasoning/"thinking" model -- those spend
# tokens on hidden chain-of-thought before the actual JSON answer, and if
# max_tokens runs out during that phase you'll get an empty response back.
LLM_MAX_TOKENS = int(os.environ.get('LLM_MAX_TOKENS', '4096'))

# How long to wait for one LM Studio response before giving up on it, in
# seconds. Generating a few thousand tokens can legitimately take a while on
# consumer hardware -- especially with a reasoning model, or a high
# LLM_MAX_TOKENS -- so this defaults high. Raise it further if you're seeing
# timeout errors consistently rather than occasionally.
LLM_TIMEOUT_SECONDS = int(os.environ.get('LLM_TIMEOUT_SECONDS', '600'))

# Which LLM provider to use: "lmstudio" (local server) or "gemini" (Google API).
LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'lmstudio')

# Gemini-specific settings. Only required when LLM_PROVIDER == "gemini".
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash-latest')
GEMINI_TEMPERATURE = float(os.environ.get('GEMINI_TEMPERATURE', '0.2'))
GEMINI_MAX_TOKENS = int(os.environ.get('GEMINI_MAX_TOKENS', '4096'))

# Validation: Gemini requires an API key
if LLM_PROVIDER == 'gemini' and not GEMINI_API_KEY:
    raise ValueError(
        'LLM_PROVIDER is set to "gemini" but GEMINI_API_KEY is not set. '
        'Add your Google AI Studio API key to .env or set LLM_PROVIDER=lmstudio.'
    )

# Optional cap on how many new journals to process in a single sync run
# (still oldest-first, by in-fiction date). Leave blank to process the
# whole backlog in one go. Can also be overridden per-run with `--limit N`
# on the command line, which takes priority over this default.
_raw_batch_limit = os.environ.get('JOURNAL_BATCH_LIMIT', '').strip()
JOURNAL_BATCH_LIMIT = int(_raw_batch_limit) if _raw_batch_limit else None

DATA_DIR = os.environ.get('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))
