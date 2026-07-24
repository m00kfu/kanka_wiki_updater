"""
Thin re-export of LLM provider functionality.

All HTTP call logic lives in llm_providers.py; this module exists for backward
compatibility so existing callers can keep importing from .llm_client.
"""

from .providers import (
    JSON_BLOCK_RE,
    LLMError,
    _extract_json,
    chat_json,
)

__all__ = ['JSON_BLOCK_RE', 'LLMError', '_extract_json', 'chat_json']
