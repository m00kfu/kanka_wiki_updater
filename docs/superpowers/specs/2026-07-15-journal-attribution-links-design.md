# Journal Attribution Links — Design Spec

## Problem

When synopsis updates are applied during review, there is no traceable link back to the source journal entry that inspired each addition. This makes it hard to verify facts or understand why a change was made.

## Solution

Prepend a Kanka wiki link `[journal:N|Session Name]` to every proposed synopsis that adds new information (as opposed to rephrasing existing content). The link is injected during the sync pipeline, before review.

## Scope

- **In scope:** Adding `_is_new_info` field to LLM schema, injecting journal links in `sync_pipeline.py`, handling edge cases
- **Out of scope:** Changing revert behavior (journal links are part of entry text and revert naturally), new-entity proposals (get full synopsis from scratch)

## Design Decisions

### 1. LLM-driven detection

The existing JSON output schema gets one field: `_is_new_info` (boolean). The system prompt instructs the model to set `true` when new facts/entities/dates are added, `false` when only rephrasing or refining.

**Why:** Semantically correct — the LLM already understands what's new vs rewritten. No extra runtime cost. Zero false negatives from text diffing heuristics.

### 2. Journal link format

Kanka native wiki links: `[journal:{id}|{display_name}]` where `id` is `_journal_id` and `display_name` is `source_journal`. Characters like `|` and `]` in journal names are stripped to prevent malformed links.

**Why:** Kanka's native format renders as clickable links inside entry fields. Avoids markdown-style which may not render in Kanka's HTML rendering.

### 3. Injection point: sync_pipeline.py

The link is prepended in `propose_update()` after the LLM returns but before the proposal dict is returned to the queue. This keeps review output clean — users see the journal link in diffs, confirming provenance.

**Why:** One injection point, all downstream consumers (CLI review, web review) get it automatically. Revert works naturally since the link is part of the entry text.

## Component Changes

### `prompts.py`
- Add `_is_new_info: boolean` to JSON schema description in system prompts for synopsis updates
- Update example output if present

### `sync_pipeline.py` in `propose_update()`
After LLM response parsing, check `result.get('_is_new_info')`:
- If truthy, prepend `[journal:{_journal_id}|{sanitized_name}]` to `proposed_text`
- Sanitize: strip characters that break Kanka wiki links (`|`, `]`)

## Edge Cases

| Case | Behavior |
|------|----------|
| `_is_new_info` missing from LLM output | Skip journal link (defensive) |
| `_is_new_info: false` | No link added |
| New entity proposal | No link — synopsis built from scratch, not an update |
| Journal name contains `\|` or `]` | Strip those characters before embedding in link |
| Multiple journals for same entity | Each proposal is independent; each gets its own source journal link |

## Testing Implications

- Unit tests: verify `_is_new_info` handling in `propose_update()`
  - `_is_new_info: true` → link prepended correctly
  - `_is_new_info: false` → no link added
  - Missing field → no link added (defensive)
  - Journal name with special chars → sanitized properly
- Integration test: end-to-end flow from journal fetch → LLM → proposal queue

## Revert Compatibility

No changes needed. `revert.py` restores the previous entry text verbatim, which doesn't include the injected journal link. The link is part of the new content that gets replaced during revert.
