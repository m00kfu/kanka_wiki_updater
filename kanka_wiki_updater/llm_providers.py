"""
LLM provider implementations for the Kanka wiki updater pipeline.

Supports LM Studio (local OpenAI-compatible server) and Google Gemini via API.
Provider-specific HTTP logic lives here; llm_client.py re-exports chat_json() so
existing callers don't need to change their imports.
"""

import json
import re

import requests

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None  # type: ignore[assignment]

from . import config

JSON_BLOCK_RE = re.compile(r'\{.*\}', re.DOTALL)


class LLMError(RuntimeError):
    """Raised when an LLM call fails or produces unparseable output."""

    pass


# Heuristic patterns that suggest the LLM output was cut off mid-string.
_TRUNCATION_END_PATTERNS = (
    ',',
    ' ',
    '\n',  # ends with trailing whitespace/punctuation (common when cut mid-sentence)
    ':',
    ';',
    '(',
    '[',
    '{',  # structural punctuation suggesting incomplete content
)


def _looks_truncated(result):
    """Heuristic: does the updated_entry look like it was cut off?"""
    if not isinstance(result, dict):
        return False
    entry = result.get('updated_entry', '') or ''
    if not entry.strip():
        return False
    # Check for incomplete sentences / mid-token cutoff at end of synopsis text
    stripped = entry.rstrip()
    if stripped and len(stripped) > 0:
        last_char = stripped[-1]
        if last_char in (',', ':', ';', '(', '['):
            return True
        # Trailing space or newline after incomplete quote/word
        if stripped.endswith((' "', " '", ', ', '\n')) and len(stripped) > 5:
            return True
    return False


def _extract_json(text, finish_reason=None):
    """Extract JSON from model output using regex + json_repair fallback.

    Models sometimes wrap JSON in prose or markdown fences despite instructions
    not to -- grab the largest {...} block first. Then try increasingly forgiving
    parses, since local models frequently produce JSON with a literal unescaped
    quote or raw newline inside a text field (very common in this task, since
    session notes and synopses often contain quoted dialogue).

    If `finish_reason` is 'length'/'MAX_TOKENS', the model hit its token limit
    mid-response. The parsed result may be valid JSON but with truncated string
    fields -- we set ``truncated`` to True and append a warning to change_summary
    so review.py can flag it.

    We also apply heuristics (incomplete trailing punctuation) when
    ``finish_reason`` is unknown, catching cases where the HTTP response didn't
    include the field but the output still looks cut off.

    Returns:
        dict with a ``truncated`` bool key set to True when truncation was detected.
    """
    match = JSON_BLOCK_RE.search(text)
    if not match:
        raise LLMError(f'No JSON object found in model output:\n{text[:500]}')
    raw = match.group(0)

    try:
        result = json.loads(raw, strict=False)
    except json.JSONDecodeError as e:
        if repair_json is None:
            raise LLMError(
                'Model produced malformed JSON, and the `json_repair` package '
                "isn't installed to auto-fix it (run `pip install -r requirements.txt`). "
                f'Parse error: {e}\nOutput was:\n{text[:500]}'
            ) from e
        try:
            result = json.loads(repair_json(raw))
        except (json.JSONDecodeError, ValueError) as e2:
            raise LLMError(
                f"Model produced JSON that couldn't be parsed even after attempting "
                f'repair ({e2}). Output was:\n{text[:500]}'
            ) from e2

    if isinstance(result, dict):
        truncated = False
        trunc_msg = '[TRUNCATED: model hit token limit. Output may be incomplete -- review carefully.]'

        if finish_reason == 'length' or _looks_truncated(result):
            truncated = True

        if truncated:
            result['truncated'] = True
            summary = result.get('change_summary', '') or ''
            if trunc_msg not in summary:
                result['change_summary'] = f'{summary} {trunc_msg}'.strip() if summary else trunc_msg
            reason = result.get('reason', '') or ''
            if trunc_msg not in reason:
                result['reason'] = f'{reason} {trunc_msg}'.strip() if reason else trunc_msg

    return result


def lmstudio_chat(system_prompt, user_prompt, temperature=0.2, max_tokens=None):
    """Send a request to LM Studio's local OpenAI-compatible server."""
    try:
        resp = requests.post(
            f'{config.LMSTUDIO_BASE_URL}/chat/completions',
            json={
                'model': config.LMSTUDIO_MODEL,
                'temperature': temperature,
                'max_tokens': max_tokens or config.LLM_MAX_TOKENS,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
            },
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout as e:
        raise LLMError(
            f"LM Studio didn't respond within {config.LLM_TIMEOUT_SECONDS}s. This usually "
            f'just means generation is legitimately slower than that on your hardware/model '
            f'combo (especially with a large max_tokens or a reasoning model) -- raise '
            f"LLM_TIMEOUT_SECONDS in your .env. If it's timing out on every single request "
            f"rather than just occasionally, check LM Studio's own logs for how long a "
            f'typical response is actually taking.'
        ) from e
    resp.raise_for_status()
    data = resp.json()
    choice = data['choices'][0]
    message = choice.get('message', {})
    content = (message.get('content') or '').strip()
    finish_reason = choice.get('finish_reason')

    if not content:
        reasoning = message.get('reasoning_content') or message.get('reasoning') or ''
        if finish_reason == 'length':
            raise LLMError(
                'Model returned no content -- it hit the token limit '
                f'(max_tokens={max_tokens or config.LLM_MAX_TOKENS}) before producing an '
                "answer. If this is a reasoning/'thinking' model, either switch to a "
                "non-thinking/Instruct build, disable thinking in LM Studio's model "
                'settings, or raise LLM_MAX_TOKENS in your .env.'
            )
        if reasoning:
            raise LLMError(
                'Model produced reasoning text but no final answer '
                f'(finish_reason={finish_reason}). It likely needs thinking '
                'disabled, or a stop sequence/template fix in LM Studio.'
            )
        raise LLMError(f'Model returned empty content (finish_reason={finish_reason}).')

    return _extract_json(content, finish_reason=finish_reason)


def gemini_chat(system_prompt, user_prompt, temperature=0.2, max_tokens=None):
    """Send a request to Google Gemini's generative language API."""
    api_key = config.GEMINI_API_KEY
    model = config.GEMINI_MODEL

    if not api_key:
        raise LLMError('GEMINI_API_KEY is not set. Get an API key from Google AI Studio and add it to your .env file.')

    # Map system prompt to Gemini's system_instruction format
    payload = {
        'contents': [
            {'role': 'user', 'parts': [{'text': user_prompt}]},
        ],
        'generationConfig': {
            'temperature': temperature,
            'maxOutputTokens': max_tokens or config.GEMINI_MAX_TOKENS,
        },
    }

    if system_prompt:
        payload['system_instruction'] = {
            'role': 'model',
            'parts': [{'text': system_prompt}],
        }

    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'

    try:
        resp = requests.post(url, json=payload, timeout=config.LLM_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout as e:
        raise LLMError(
            f"Gemini API didn't respond within {config.LLM_TIMEOUT_SECONDS}s. "
            'Check your network connection or raise LLM_TIMEOUT_SECONDS in .env.'
        ) from e

    if resp.status_code == 401:
        raise LLMError('Gemini API returned 401 -- invalid or expired API key.')
    if resp.status_code == 403:
        raise LLMError(
            'Gemini API returned 403 -- quota exceeded or API not enabled. '
            'Check Google AI Studio for your project status.'
        )
    if resp.status_code == 429:
        raise LLMError('Gemini API returned 429 -- rate limit hit. Wait a moment and retry.')

    resp.raise_for_status()
    data = resp.json()

    # Extract text from Gemini's response format
    try:
        candidate = data['candidates'][0]
        content = candidate.get('content', {})
        parts = content.get('parts', [])
        if not parts:
            raise LLMError('Gemini returned no parts in response.')
        finish_reason_raw = candidate.get('finishReason', '')
        # Map Gemini's finish reason to our convention
        finish_reason = 'length' if finish_reason_raw == 'MAX_TOKENS' else None
        text = ''
        for part in parts:
            text += part.get('text', '')
        text = text.strip()
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f'Gemini returned unexpected response format:\n{resp.text[:500]}') from e

    if not text:
        raise LLMError('Gemini returned empty content.')

    return _extract_json(text, finish_reason=finish_reason)


def chat_json(system_prompt, user_prompt, temperature=None, max_tokens=None):
    """Route a chat request to the configured provider and extract JSON.

    This is the only function callers should import -- it dispatches to either
    LM Studio or Gemini based on LLM_PROVIDER in config.
    """
    if config.LLM_PROVIDER == 'gemini':
        temp = temperature if temperature is not None else config.GEMINI_TEMPERATURE
        tokens = max_tokens if max_tokens is not None else config.GEMINI_MAX_TOKENS
        return gemini_chat(system_prompt, user_prompt, temp, tokens)
    else:
        temp = temperature if temperature is not None else 0.2
        tokens = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS
        return lmstudio_chat(system_prompt, user_prompt, temp, tokens)
