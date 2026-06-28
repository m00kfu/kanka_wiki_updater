"""
Thin client for LM Studio's local OpenAI-compatible server.

Start LM Studio, load a model, and start the local server (Developer tab ->
Start Server; defaults to http://localhost:1234) before running the pipeline.
"""
import json
import re
import requests

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None

from . import config


class LLMError(RuntimeError):
    pass


JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text, finish_reason=None):
    """Models sometimes wrap JSON in prose or markdown fences despite
    instructions not to -- grab the largest {...} block first. Then try
    increasingly forgiving parses, since local models frequently produce
    JSON with a literal unescaped quote or raw newline inside a text field
    (very common in this task, since session notes and synopses often
    contain quoted dialogue).

    If `finish_reason` is 'length', the model hit its token limit mid-response.
    The parsed result may be valid JSON but with truncated string fields --
    we append a warning to change_summary so review.py can flag it."""
    match = JSON_BLOCK_RE.search(text)
    if not match:
        raise LLMError(f"No JSON object found in model output:\n{text[:500]}")
    raw = match.group(0)

    try:
        result = json.loads(raw, strict=False)
    except json.JSONDecodeError as e:
        if repair_json is None:
            raise LLMError(
                "Model produced malformed JSON, and the `json_repair` package "
                "isn't installed to auto-fix it (run `pip install -r requirements.txt`). "
                f"Parse error: {e}\nOutput was:\n{text[:500]}"
            )
        try:
            result = json.loads(repair_json(raw))
        except (json.JSONDecodeError, ValueError) as e2:
            raise LLMError(
                f"Model produced JSON that couldn't be parsed even after attempting "
                f"repair ({e2}). Output was:\n{text[:500]}"
            )

    # Truncation can leave valid JSON with cut-off string values -- detect it
    # and annotate the result so callers can surface a warning during review.
    if finish_reason == "length" and isinstance(result, dict):
        trunc_msg = (
            f"[TRUNCATED: model hit token limit (max_tokens={config.LLM_MAX_TOKENS}). "
            "Output may be incomplete -- review carefully.]"
        )
        summary = result.get("change_summary", "") or ""
        if trunc_msg not in summary:
            result["change_summary"] = f"{summary} {trunc_msg}".strip() if summary else trunc_msg
        reason = result.get("reason", "") or ""
        if trunc_msg not in reason:
            result["reason"] = f"{reason} {trunc_msg}".strip() if reason else trunc_msg
    return result


def chat_json(system_prompt, user_prompt, temperature=0.2, max_tokens=None):
    try:
        resp = requests.post(
            f"{config.LMSTUDIO_BASE_URL}/chat/completions",
            json={
                "model": config.LMSTUDIO_MODEL,
                "temperature": temperature,
                "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        raise LLMError(
            f"LM Studio didn't respond within {config.LLM_TIMEOUT_SECONDS}s. This usually "
            f"just means generation is legitimately slower than that on your hardware/model "
            f"combo (especially with a large max_tokens or a reasoning model) -- raise "
            f"LLM_TIMEOUT_SECONDS in your .env. If it's timing out on every single request "
            f"rather than just occasionally, check LM Studio's own logs for how long a "
            f"typical response is actually taking."
        )
    resp.raise_for_status()
    data = resp.json()
    choice = data["choices"][0]
    message = choice.get("message", {})
    content = (message.get("content") or "").strip()
    finish_reason = choice.get("finish_reason")

    if not content:
        # Some "thinking"/reasoning models expose their chain-of-thought in a
        # separate field (reasoning / reasoning_content) and only put the
        # real answer in `content` once thinking is done. If max_tokens ran
        # out during that phase, content ends up empty -- this is the most
        # common cause of "no JSON object found in model output" with
        # nothing after the colon.
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        if finish_reason == "length":
            raise LLMError(
                "Model returned no content -- it hit the token limit "
                f"(max_tokens={max_tokens or config.LLM_MAX_TOKENS}) before producing an "
                "answer. If this is a reasoning/'thinking' model, either switch to a "
                "non-thinking/Instruct build, disable thinking in LM Studio's model "
                "settings, or raise LLM_MAX_TOKENS in your .env."
            )
        if reasoning:
            raise LLMError(
                "Model produced reasoning text but no final answer "
                f"(finish_reason={finish_reason}). It likely needs thinking "
                "disabled, or a stop sequence/template fix in LM Studio."
            )
        raise LLMError(f"Model returned empty content (finish_reason={finish_reason}).")

    return _extract_json(content, finish_reason=finish_reason)
