# Synopsis Generator Refactor Design

## Problem

`sync_pipeline.propose_update()` (~220 lines) and `review_web.regenerate_proposal()` (~420 lines) share the same core logic: build an LLM prompt, call the model, normalize whitespace, detect no-change, inject journal attribution links, flag truncation/info-loss. The duplicate code has diverged — review_web's regeneration is a hand-written copy that doesn't match sync_pipeline's current behavior in subtle ways (e.g., different old-tag preservation logic).

## Goal

Extract shared synopsis-generation logic into a new `synopsis_generator.py` module with pure, testable functions. Both callers become thin wrappers that assemble input data and invoke the shared functions.

## Design

### New Module: `kanka_wiki_updater/synopsis_generator.py`

#### `build_prompt(name, entity_kind, current_entry, session_text) → str`
Formats `USER_PROMPT_TEMPLATE`. Single source of truth for prompt assembly. No dependencies beyond the template constant.

#### `process_llm_response(result, entity_id, journal_name, previous_entry, index=None) → dict | None`
Takes raw LLM JSON result + context metadata. Returns a proposal-ready dict or `None` (no meaningful change).

Handles in order:
1. **Whitespace normalization** — preserve `\n\n` paragraph breaks, collapse single newlines to spaces within paragraphs
2. **No-change detection** — normalized text comparison against `previous_entry`, returns `None` if identical
3. **Journal link injection** — LLM-provided indices → diff-based fallback; preserves old tags on rephrased content; collapses consecutive indices
4. **Truncation/info-loss flagging** — flags when output is <65% of input length

Returns dict with: `proposal_type`, `entity_id/kind/name`, `previous_entry`, `proposed_entry`, `change_summary`, `relation_changes`, `uncertain`, `truncated`, `_info_loss_warning`

#### `build_new_entity_prompt(known_names, journal_name, journal_date, session_text) → str`
Formats `NEW_ENTITY_USER_PROMPT_TEMPLATE`. Extracts prompt assembly from `sync_pipeline.propose_new_entities()`.

### Caller Changes

**`sync_pipeline.py` — `propose_update()` shrinks to ~15 lines:**
```python
def propose_update(entity_id, entity, journal, index):
    session_text = _annotate_journals(clean_raw_text, str(journal.get('id') or ''))
    prompt = build_prompt(name, kind, current_entry, session_text)
    result = chat_json(SYSTEM_PROMPT, prompt)
    return process_llm_response(result, entity_id, journal_name, previous_entry)
```

**`review_web.py` — `regenerate_proposal()` shrinks from ~420 lines to ~60 lines:**
```python
def regenerate_proposal(index):
    # ... fetch fresh data as it does now ...
    prompt = build_prompt(name, kind, current_entry, session_text)
    result = chat_json(SYSTEM_PROMPT, prompt, max_tokens=regen_max)
    updated = process_llm_response(result, entity_id, journal_name, previous_entry)
    if not updated: return error('Regenerated output is identical')
    queue[index].update(updated)
```

### Test Impact
- Existing tests in `test_sync_pipeline.py` call `propose_update()` — they keep working since it remains a thin wrapper
- Add direct unit tests for `build_prompt`, `process_llm_response` (no-change, truncation detection, journal link injection edge cases)
