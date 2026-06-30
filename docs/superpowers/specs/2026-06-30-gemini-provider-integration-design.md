# Gemini Provider Integration for sync_pipeline

## Overview

Add Gemini as an optional LLM provider alongside the existing LM Studio local server. Users switch providers by setting `LLM_PROVIDER` in `.env`. No changes to pipeline logic, prompts, or review flow.

## Architecture

### New file: `llm_providers.py`
- Contains all LLM HTTP call logic (replaces inline calls in `llm_client.py`)
- Provider-specific implementations with shared JSON extraction utilities
- Dispatcher function that routes based on config

### Modified files
- **`config.py`** — add provider selection and Gemini parameters
- **`.env.example`** — document new variables
- **`llm_client.py`** — thin re-export module (`from .llm_providers import chat_json, LLMError`) for backward compatibility

### Unchanged files
- `sync_pipeline.py`, `prompts.py`, `review.py` — zero changes, they keep calling `chat_json()`

## Configuration (`config.py`)

```python
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "lmstudio")  # "lmstudio" | "gemini"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-latest")
GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))
GEMINI_MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "4096"))
```

Validation at module load: if `LLM_PROVIDER == "gemini"` and no `GEMINI_API_KEY`, raise `ValueError`.

## Provider implementations (`llm_providers.py`)

### LM Studio provider — `lmstudio_chat()`
Extracted from existing `llm_client.py:chat_json()`. POSTs to `{LMSTUDIO_BASE_URL}/chat/completions` with standard OpenAI-compatible payload. No changes to behavior.

### Gemini provider — `gemini_chat()`
POSTs to `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}`

**Payload mapping:**
| LM Studio | Gemini |
|---|---|
| `messages: [{role, content}]` | `contents: [{role:"user", parts:[{text}]}]` |
| (none) | `system_instruction: {role:"model", parts:[{text}]}` |
| `max_tokens` | `maxOutputTokens` |

**Response parsing:** Extracts text from `candidates[0].content.parts[0].text`. Checks `finishReason == "MAX_TOKENS"` for truncation (maps to same `[TRUNCATED]` warning as LM Studio).

### Shared utilities
- `_extract_json(text)` — regex extraction + json_repair fallback, works on any provider's text output
- `LLMError(RuntimeError)` — custom exception with provider name in message

## Dispatcher (`chat_json()`)

```python
def chat_json(system_prompt, user_prompt, temperature=0.2, max_tokens=None):
    if config.LLM_PROVIDER == "gemini":
        temp = temperature or config.GEMINI_TEMPERATURE
        tokens = max_tokens or config.GEMINI_MAX_TOKENS
        return gemini_chat(system_prompt, user_prompt, temp, tokens)
    else:
        temp = temperature or 0.2
        tokens = max_tokens or config.LLM_MAX_TOKENS
        return lmstudio_chat(system_prompt, user_prompt, temp, tokens)
```

## Error handling

- Gemini-specific errors (403 quota, 401 invalid key, 429 rate limit) caught and raised as `LLMError` with provider context
- Same fallback chain: strict JSON → json_repair → raise LLMError
- Truncation detection adapted per provider's response format

## Testing strategy

- Unit tests for each provider in isolation (mock HTTP responses)
- Integration test: run pipeline with `LLM_PROVIDER=gemini` against real API (optional, gated by env var)
- No changes to existing LM Studio tests — they continue passing unchanged
