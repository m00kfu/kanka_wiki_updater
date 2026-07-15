# Journal Attribution Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepend a Kanka wiki link `[journal:N|Session Name]` to synopsis proposals that add new information, injected during the sync pipeline before review.

**Architecture:** Add `_is_new_info` boolean to the LLM JSON schema in prompts. In `sync_pipeline.py`, check this flag and prepend the journal link when true. Minimal changes: one prompt field, one injection point, no downstream impact on review or revert.

**Tech Stack:** Python 3, no new dependencies — only existing Kanka wiki markup format `[journal:N|Name]`.

## Global Constraints

- LLM output must remain valid JSON; `_is_new_info` is a top-level boolean field
- Journal link characters `\|` and `]` in journal names are stripped before embedding
- New entity proposals (not update) do NOT get journal links — synopsis built from scratch
- No changes to review.py, review_web.py, or revert.py — injection happens upstream in sync_pipeline

---

### Task 1: Add `_is_new_info` to LLM JSON schema

**Files:**
- Modify: `kanka_wiki_updater/prompts.py:42-47`

**Interfaces:**
- Consumes: None (schema change only)
- Produces: LLM output includes `_is_new_info: boolean` alongside existing `updated_entry`, `change_summary`, `uncertain` fields

- [ ] **Step 1: Add `_is_new_info` to SYSTEM_PROMPT JSON schema**

In `prompts.py`, add `_is_new_info` to the JSON schema in `SYSTEM_PROMPT`:

```python
# Change lines 42-47 from:
"""
JSON schema:
{
  "updated_entry": "<paragraph 1>\\n\\n<paragraph 2>\\n\\n<paragraph 3>",
  "change_summary": "<string, 1-2 sentences describing what changed>",
  "uncertain": ["<string>", "..."]
}
"""

# To:
"""
JSON schema:
{
  "updated_entry": "<paragraph 1>\\n\\n<paragraph 2>\\n\\n<paragraph 3>",
  "change_summary": "<string, 1-2 sentences describing what changed>",
  "_is_new_info": <boolean — true when new facts/events/relationships are added to the synopsis; false when only rephrasing or refining existing content>,
  "uncertain": ["<string>", "..."]
}
"""
```

Also add a rule to the rules section (before line 40, after rule 7):

```python
8. ATTRIBUTION: When new facts, events, relationships, or status changes are added based on the session notes, set _is_new_info to true in your JSON output. If you are only rephrasing, refining formatting, or restructuring existing content without adding new facts, set _is_new_info to false.
```

- [ ] **Step 2: Run linting to verify no syntax errors**

Run: `ruff check kanka_wiki_updater/prompts.py`
Expected: No issues (or only pre-existing issues)

---

### Task 2: Inject journal link in sync_pipeline

**Files:**
- Modify: `kanka_wiki_updater/sync_pipeline.py:245-287` (the `propose_update()` function)

**Interfaces:**
- Consumes: `result['_is_new_info']` from LLM output, `journal['id']`, `journal.get('name')`
- Produces: `proposed_entry` field in returned dict has `[journal:N|Name]` prepended when `_is_new_info` is true

- [ ] **Step 1: Add journal link injection logic to `propose_update()`**

After line 251 (newline normalization block) and before the `no_text_change` check at line 262, add:

```python
    # Inject journal attribution link when LLM flags new information.
    _is_new_info = result.get('_is_new_info') is True
    if _is_new_info:
        journal_id = journal['id']
        journal_name = (journal.get('name') or '').replace('|', '').replace(']', '')
        proposed_text = f'[journal:{journal_id}|{journal_name}] {proposed_text}'
```

This must be placed between the newline normalization block (ending at line 251) and the `previous_text` assignment (line 254). The link is prepended to `proposed_text` before the no-change detection so that a proposal with only a journal link addition still passes through if the underlying text also differs.

- [ ] **Step 2: Run linting**

Run: `ruff check kanka_wiki_updater/sync_pipeline.py`
Expected: No issues

---

### Task 3: Add tests for `_is_new_info` behavior

**Files:**
- Modify: `tests/test_sync_pipeline.py` (append after line 534)

**Interfaces:**
- Consumes: None
- Produces: Test coverage for the new journal link injection logic

- [ ] **Step 1: Write tests for `_is_new_info` in `propose_update()`**

Append these test functions to `tests/test_sync_pipeline.py`:

```python
# --- _is_new_info and journal link injection tests ---


def test_propose_update_injects_journal_link_when_new_info():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session 1: The Beginning',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Added new info',
            '_is_new_info': True,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    assert result['proposed_entry'].startswith('[journal:789|Session 1: The Beginning] ')


def test_propose_update_no_journal_link_when_not_new_info():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session 1',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Rephrased',
            '_is_new_info': False,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    assert '[journal:' not in result['proposed_entry']


def test_propose_update_no_journal_link_when_missing_field():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session 1',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM output without _is_new_info field (backward compat)
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Added info',
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    assert '[journal:' not in result['proposed_entry']


def test_propose_update_sanitize_journal_name_special_chars():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session | Special ] Name',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Added info',
            '_is_new_info': True,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    # Pipe and bracket should be stripped from the link text
    assert '[journal:789|Session  Special  Name] ' in result['proposed_entry']
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_sync_pipeline.py -v`
Expected: All tests PASS, including the new journal link injection tests

---

### Task 4: Run full test suite and linting

**Files:**
- No file changes — verification only

**Interfaces:**
- Consumes: None
- Produces: Green CI signal for this change

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: All existing tests PASS, no regressions

- [ ] **Step 2: Run linter on all modified files**

Run: `ruff check .`
Expected: No new issues

- [ ] **Step 3: Format code**

Run: `ruff format .`
Expected: Clean formatting (no changes needed if already formatted)

---

## Summary of Changes

| File | Lines Changed | Description |
|------|--------------|-------------|
| `prompts.py` | ~4 added | Add `_is_new_info` to JSON schema + rule 8 |
| `sync_pipeline.py` | ~6 added | Inject journal link in `propose_update()` when `_is_new_info: true` |
| `tests/test_sync_pipeline.py` | ~120 added | 4 new test functions for the feature |

## Edge Cases Handled

- `_is_new_info` missing from LLM output → no link (defensive, backward compatible)
- Journal name contains `\|` or `]` → stripped before embedding in wiki link
- New entity proposals → not affected, they don't go through `propose_update()` with a `previous_entry`
- Multiple journals for same entity → each proposal gets its own source journal's link
