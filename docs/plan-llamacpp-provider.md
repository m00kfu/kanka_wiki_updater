# Plan: Add llama.cpp as an LLM provider

## Overview

llama.cpp ships `llama-server` (Windows: `server.exe`) which exposes an OpenAI-compatible API at `/v1/chat/completions` -- identical wire format to LM Studio. The implementation is nearly a copy-paste of `lmstudio_chat`, with different config keys.

---

## Changes

### 1. `config.py` — Add llama.cpp config variables

| Variable | Default | Required |
|---|---|---|
| `LLAMACPP_BASE_URL` | `http://localhost:8080/v1` | no |
| `LLAMACPP_MODEL` | `local-model` | no |

- No validation needed (no API key required)
- `llama.cpp` joins `LLM_PROVIDER` choices: `"lmstudio"`, `"gemini"`, `"llamacpp"`

### 2. `llm_providers.py` — Add `llamacpp_chat()` function

Mirror `lmstudio_chat` exactly:
- Same `requests.post` call to `{LLAMACPP_BASE_URL}/chat/completions`
- Same payload structure: `model`, `temperature`, `max_tokens`, `messages`
- Same error handling: `requests.exceptions.Timeout` → `LLMError`, empty content handling, finish_reason pass-through
- Pass through to `_extract_json(content, finish_reason=finish_reason)`

### 3. `llm_providers.py` — Update `chat_json()` dispatcher

Add branch:
```python
elif config.LLM_PROVIDER == 'llamacpp':
    ...  # call llamacpp_chat
```

### 4. `.env.example` — Document new config keys

Add section after LM Studio:
```
# llama.cpp settings (only needed when LLM_PROVIDER=llamacpp)
# LLAMACPP_BASE_URL=http://localhost:8080/v1
# LLAMACPP_MODEL=local-model
```

### 5. `requirements.txt` — No changes needed

llama.cpp uses only `requests` (already a dependency). The user just needs to run `llama-server -m <model>` locally.

### 6. `tests/test_llm_providers.py` — Add `TestLlamaCppChat` class

Same test coverage as `TestLmStudioChat`:
- `test_llamacpp_chat_success` — happy path, returns parsed JSON
- `test_llamacpp_chat_timeout` — `requests.exceptions.Timeout` → `LLMError`
- `test_llamacpp_chat_empty_content` — no content → `LLMError`
- `test_llamacpp_chat_reasoning_only` — reasoning text but no answer → `LLMError`
- `test_llamacpp_chat_token_limit` — finish_reason=stop but no content → `LLMError`

### 7. `tests/test_llm_providers.py` — Update `TestChatJsonDispatcher`

Add `test_dispatcher_routes_to_llamacpp` — sets `LLM_PROVIDER=llamacpp`, mocks `requests.post`, verifies `llamacpp_chat` path.

### 8. `AGENTS.md` — Update architecture table and gotchas

Add `"llamacpp"` to the `LLM_PROVIDER` choices, note that llama.cpp server must be running.

### 9. `AGENTS.md` — Update prompt engineering notes

Mention that llama.cpp uses the same OpenAI-compatible endpoint as LM Studio, so configuration is the same format.

---

## Effort estimate

~150 lines of new code (mostly copy-paste + tests). No new dependencies. No breaking changes.
