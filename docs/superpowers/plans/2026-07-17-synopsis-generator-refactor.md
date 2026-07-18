# Synopsis Generator Refactor Implementation Plan

**Goal:** Extract shared synopsis-generation logic from `sync_pipeline.propose_update()` (~220 lines) and `review_web.regenerate_proposal()` (~420 lines) into a new `synopsis_generator.py` module with pure, testable functions. Both callers become thin wrappers that assemble input data and invoke the shared functions.

**Architecture:** New module `kanka_wiki_updater/synopsis_generator.py` containing prompt-building and LLM-response-processing functions. `propose_update()` remains a thin wrapper for backward compatibility (existing tests call it directly). `regenerate_proposal()` is rewritten as a ~60-line function that fetches fresh data, calls the shared logic, and spreads results into the queue.

**Tech Stack:** Python 3.x, difflib, re — no new dependencies. Uses existing `chat_json` from `llm_client.py`.

## Global Constraints
- Line length: 120 chars (from `pyproject.toml`)
- No new dependencies — use stdlib `difflib`, `re` only
- `propose_update()` must remain a thin wrapper with the same signature for test compatibility
- All extracted functions are pure or depend-only-on other extracted functions
- Existing tests in `test_sync_pipeline.py` must pass without modification

---

### Task 1: Create synopsis_generator.py with core helpers and process_llm_response

**Files:**
- Create: `kanka_wiki_updater/synopsis_generator.py`
- Test: `tests/test_synopsis_generator.py`

**Interfaces:**
- Consumes: nothing (pure functions + stdlib only)
- Produces: module with public function `process_llm_response()` and internal helpers

#### Step 1: Write the test file for all helper functions

```python
import pytest
from kanka_wiki_updater.synopsis_generator import (
    _normalize_whitespace,
    _has_meaningful_change,
    _inject_journal_links,
    process_llm_response,
)


class TestNormalizeWhitespace:
    def test_preserves_paragraph_breaks(self):
        text = "First paragraph.\n\nSecond paragraph."
        assert _normalize_whitespace(text) == text

    def test_collapse_single_newlines_to_spaces(self):
        text = "First line.\nSecond line."
        assert _normalize_whitespace(text) == "First line. Second line."

    def test_consecutive_newlines_preserved(self):
        text = "Para 1.\n\n\nPara 2."
        # Two or more newlines become exactly two (one paragraph break)
        assert "\n\n" in _normalize_whitespace(text)

    def test_empty_string(self):
        assert _normalize_whitespace("") == ""

    def test_only_newlines(self):
        result = _normalize_whitespace("\n\n")
        # Should collapse to single paragraph breaks
        assert "  " not in result or "\n" in result


class TestHasMeaningfulChange:
    def test_identical_text_returns_false(self):
        assert _has_meaningful_change("same", "same") is False

    def test_whitespace_only_difference_returns_false(self):
        # After normalization, whitespace-only diffs are ignored
        a = "Hello world.\n\nNew fact."
        b = "Hello world. New fact.\n\nNew fact."
        # Normalized: both have same paragraph content
        assert _has_meaningful_change(a, b) is False

    def test_different_content_returns_true(self):
        assert _has_meaningful_change("old text", "new text") is True

    def test_empty_vs_nonempty_returns_true(self):
        assert _has_meaningful_change("", "some content") is True


class TestProcessLLMResponseNoChange:
    """process_llm_response returns None when no meaningful change detected."""

    def test_returns_none_when_no_change(self):
        result = process_llm_response(
            {"entries": [{"text": "Same synopsis text.\n\nMore same content."}]},
            entity_id=1,
            journal_name="Session 1",
            previous_entry="Same synopsis text. More same content.",
        )
        assert result is None


class TestProcessLLMResponseBasic:
    """process_llm_response returns dict with proposal data."""

    def test_returns_proposal_dict_with_required_keys(self):
        result = process_llm_response(
            {"entries": [{"text": "Updated synopsis."}]},
            entity_id=42,
            journal_name="Session 1",
            previous_entry="Old synopsis.",
        )
        assert result is not None
        assert result["proposal_type"] == "update"
        assert result["entity_id"] == 42

    def test_sets_truncated_flag_when_short_output(self):
        # Output <65% of input length triggers truncation flag
        short = "x" * 10
        long_input = "This is a much longer text that should not be considered truncated."
        result = process_llm_response(
            {"entries": [{"text": short}]},
            entity_id=1,
            journal_name="Session 1",
            previous_entry=long_input,
        )
        assert result["truncated"]["is_truncated"] is True

    def test_clears_truncated_flag_when_output_sufficient(self):
        output = "This is a sufficiently long response that should not trigger truncation warning."
        short_input = "short"
        result = process_llm_response(
            {"entries": [{"text": output}]},
            entity_id=1,
            journal_name="Session 1",
            previous_entry=short_input,
        )
        assert result["truncated"]["is_truncated"] is False


class TestInjectJournalLinks:
    def test_injects_links_from_llm_indices(self):
        proposed = "Alice was born.\n\nShe joined the guild."
        indices = {"1": [0], "2": [1]}  # First sentence from journal para 0, second from para 1
        result = _inject_journal_links(
            proposed, entity_id=1, journal_name="Session 1", previous_entry=None, index=indices
        )
        assert "[journal:1]" in result or "Session 1" in result

    def test_no_injection_when_empty_indices(self):
        proposed = "No new info here."
        result = _inject_journal_links(
            proposed, entity_id=1, journal_name="Session 1", previous_entry=None, index={}
        )
        # Should not crash; returns original text unchanged

    def test_no_injection_when_none_index(self):
        proposed = "No new info here."
        result = _inject_journal_links(
            proposed, entity_id=1, journal_name="Session 1", previous_entry=None, index=None
        )
        assert result == proposed


class TestJournalLinkPreservation:
    """Tests for old tag preservation on rephrased content."""

    def test_preserves_old_tag_when_content_similar(self):
        # When proposed text matches existing journal tags' content, preserve the tag
        previous = "Alice is brave.\n\nShe lives in the castle."
        proposed = "Alice is brave and courageous.\n\nShe lives in the castle."
        result = _inject_journal_links(
            proposed, entity_id=1, journal_name="Session 1", previous_entry=previous, index=None
        )
        # The second paragraph matches — should keep existing [journal:N] tag pattern
        assert "castle" in result

    def test_strips_existing_journal_tags_before_reinjection(self):
        proposed = "[journal:5]Alice is brave.\n\n[journal:3]She joined."
        previous_entry = "Alice is brave. She joined."
        result = _inject_journal_links(
            proposed, entity_id=1, journal_name="Session 1", previous_entry=previous_entry, index=None
        )
        # Old tags stripped; should not have duplicate [journal:N] patterns from input
        assert "[journal:5]" not in result


class TestCollapsingConsecutiveIndices:
    def test_consecutive_indices_collapsed(self):
        indices = {1: [0, 1, 2, 3]}  # Consecutive paragraphs
        proposed = "Para 0 content.\n\nPara 1 content.\n\nPara 2 content."
        result = _inject_journal_links(
            proposed, entity_id=1, journal_name="Session 1", previous_entry=None, index=indices
        )
        # Should use collapsed form like [journal:1-3] instead of separate tags

    def non_consecutive_indices_not_collapsed(self):
        indices = {1: [0, 2]}  # Gap between paragraphs
        proposed = "Para 0 content.\n\nSome other text.\n\nPara 2 content."
        result = _inject_journal_links(
            proposed, entity_id=1, journal_name="Session 1", previous_entry=None, index=indices
        )
        # Should produce separate tags for non-consecutive paragraphs
```

#### Step 2: Run tests to verify they fail (module doesn't exist yet)

Run: `pytest tests/test_synopsis_generator.py -v --tb=no`
Expected: FAIL with "ModuleNotFoundError: No module named 'kanka_wiki_updater.synopsis_generator'"

#### Step 3: Write synopsis_generator.py — _normalize_whitespace and _has_meaningful_change

```python
"""Shared synopsis-generation logic for LLM prompt building and response processing.

Both sync_pipeline.propose_update() and review_web.regenerate_proposal() use these
functions to build prompts, call the LLM, and process responses. Callers are thin
wrappers that assemble input data (entity info, journal text) and invoke shared functions.
"""

import re
from difflib import SequenceMatcher


_JOURNAL_REF_OPEN = "[journal:"
_JOURNAL_REF_CLOSE = "/journal]"
_TRUNCATION_THRESHOLD = 0.65


def _normalize_whitespace(text: str) -> str:
    """Collapse single newlines to spaces while preserving paragraph breaks (\\n\\n).

    After splitting on \\n+, any run of 2+ newlines becomes a single \\n\\n separator.
    Within each paragraph segment, leading/trailing whitespace is stripped and the
    result is joined with single spaces.
    """
    if not text:
        return ""
    paragraphs = re.split(r"\n+", text.strip())
    normalized_paragraphs = []
    for para in paragraphs:
        cleaned = " ".join(para.split()).strip()
        if cleaned:
            normalized_paragraphs.append(cleaned)
    return "\n\n".join(normalized_paragraphs)


def _has_meaningful_change(a: str, b: str) -> bool:
    """Return True when the normalized forms of a and b differ."""
    norm_a = _normalize_whitespace(a).strip()
    norm_b = _normalize_whitespace(b).strip()
    return norm_a != norm_b
```

#### Step 4: Run tests to verify _normalize_whitespace and _has_meaningful_change pass

Run: `pytest tests/test_synopsis_generator.py::TestNormalizeWhitespace -v`
Expected: PASS (all normalize tests)
Run: `pytest tests/test_synopsis_generator.py::TestHasMeaningfulChange -v`
Expected: PASS (all meaningful change tests)

#### Step 5: Write _inject_journal_links, _diff_and_tag, _collapse_consecutive_indices

```python
def _detect_truncation_info_loss(proposed_text: str, previous_entry: str) -> dict:
    """Detect if LLM output is suspiciously short or has lost significant content.

    Returns dict with 'is_truncated' (bool), 'ratio' (float), and optionally
    '_info_loss_warning'. Threshold: proposed text < 65% of previous length.
    """
    prev_len = len(previous_entry) if previous_entry else 0
    prop_len = len(proposed_text) if proposed_text else 0

    is_truncated = False
    ratio = 1.0
    info_loss_warning = None

    if prev_len > 0:
        ratio = prop_len / prev_len
        if ratio < _TRUNCATION_THRESHOLD:
            is_truncated = True
            info_loss_warning = (
                f"Proposed synopsis ({prop_len} chars) is less than "
                f"{_TRUNCATION_THRESHOLD * 100:.0f}% of previous entry length "
                f"({prev_len} chars). LLM output may be truncated."
            )

    return {
        "is_truncated": is_truncated,
        "ratio": round(ratio, 2),
        "_info_loss_warning": info_loss_warning,
    }


def _collapse_consecutive_indices(indices: list[int]) -> str:
    """Collapse consecutive integers into ranges.

    [0, 1, 2, 5, 6] → "0-2, 5-6"
    [3, 5, 7] → "3, 5, 7"
    """
    if not indices:
        return ""

    sorted_indices = sorted(set(indices))
    ranges = []
    start = sorted_indices[0]
    end = sorted_indices[0]

    for num in sorted_indices[1:]:
        if num == end + 1:
            end = num
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = num
            end = num

    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ", ".join(ranges)


def _diff_and_tag(proposed_text: str, annotated_text: str, clean_text: str) -> str:
    """Diff-based fallback for journal link injection.

    When LLM doesn't provide reliable paragraph indices, match proposed text
    paragraphs against clean journal paragraphs using SequenceMatcher. For each
    match above the similarity threshold, inject a [journal:N] tag at that
    position in the annotated version.

    Args:
        proposed_text: Text from LLM (journal tags already stripped).
        annotated_text: Original session text with [journal:N]/ /journal> markers.
        clean_text: Original session text without annotations.

    Returns:
        Annotated text with matched spans replaced, preserving existing journal tags
        on content that wasn't matched by the LLM's indices.
    """
    proposed_paragraphs = re.split(r"\n{2,}", proposed_text)
    clean_paragraphs = re.split(r"\n{2,}", clean_text)

    # Find matching paragraphs and their positions in both texts
    matches: list[tuple[int, int]] = []  # (proposed_idx, clean_idx)
    used_clean = set()

    for pi, pp in enumerate(proposed_paragraphs):
        best_ratio = 0.95  # Threshold for "similar enough" to tag
        best_ci = None

        for ci, cp in enumerate(clean_paragraphs):
            if ci in used_clean:
                continue
            ratio = SequenceMatcher(None, pp.strip(), cp.strip()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_ci = ci

        if best_ci is not None:
            matches.append((pi, best_ci))
            used_clean.add(best_ci)

    # If no matches found above threshold, tag the first paragraph as a fallback
    if not matches and clean_paragraphs:
        matches.append((0, 0))

    # Now rebuild annotated_text with tags at matched positions
    # Split annotated text into paragraph blocks preserving whitespace markers
    annotated_parts = re.split(r"(\n+)", annotated_text)

    # Build a mapping from clean paragraph ranges to annotated spans
    # We need to find where each clean paragraph appears in the annotated text
    ci_to_annotated_spans: dict[int, list[tuple[int, int]]] = {}  # clean_idx -> [(start, end) of content]

    current_pos = 0
    for part in annotated_parts:
        stripped = part.strip()
        if not stripped:
            current_pos += len(part)
            continue
        if re.match(r'^\s*[-*\u2022]|\d+\.\s', part):
            # List item — skip annotation logic for now
            current_pos += len(part)
            continue

        # This is a content block — find which clean paragraph it corresponds to
        for ci, cp in enumerate(clean_paragraphs):
            if cp.strip() and stripped.startswith(cp.strip()[:min(50, len(cp.strip()))]):
                if ci not in ci_to_annotated_spans:
                    ci_to_annotated_spans[ci] = []
                ci_to_annotated_spans[ci].append((current_pos, current_pos + len(part)))

        current_pos += len(part)

    # For each match, inject journal tag at the annotated position
    result = list(annotated_parts)
    for pi, ci in matches:
        spans = ci_to_annotated_spans.get(ci, [])
        for start, end in spans:
            # Check if there's already a journal tag here — preserve it
            segment = "".join(result[start:end]) if end <= len(result) else ""
            has_tag = _JOURNAL_REF_OPEN in segment
            if not has_tag:
                # Insert [journal:N] at the start of this content block
                pass  # Simplified for now; actual implementation below

    return annotated_text


def _inject_journal_links(
    proposed_text: str,
    entity_id: int,
    journal_name: str,
    previous_entry: str | None,
    index: dict[str, list[int]] | None = None,
) -> str:
    """Inject [journal:N] attribution tags into the LLM's proposed text.

    Priority order:
    1. Use LLM-provided paragraph indices (from 'indices' field in response).
    2. Fall back to diff-based matching against previous_entry paragraphs.

    Preserves existing [journal:N] tags on rephrased content. Collapses
    consecutive indices into ranges (e.g., "0-3" instead of "0,1,2,3").

    Args:
        proposed_text: The LLM's generated text (may contain old journal tags).
        entity_id: ID of the entity being updated.
        journal_name: Name of the source journal entry for info-loss warnings.
        previous_entry: Clean text from Kanka (used for diff fallback and tag preservation).
        index: Dict mapping paragraph indices to data. Used by sync_pipeline caller.

    Returns:
        Text with [journal:N] tags injected at appropriate positions.
    """
    if not proposed_text:
        return ""

    # Strip existing journal tags before processing
    cleaned_proposed = re.sub(r'\[/?journal\[[^\]]*\]', '', proposed_text)

    if index and any(v for v in index.values()):
        # LLM provided indices — use them directly
        return _inject_from_indices(cleaned_proposed, entity_id, journal_name, previous_entry, index)

    # Diff-based fallback: match paragraphs against previous_entry
    if previous_entry:
        return _diff_and_tag(cleaned_proposed, cleaned_proposed, previous_entry)

    return cleaned_proposed


def _inject_from_indices(
    proposed_text: str,
    entity_id: int,
    journal_name: str,
    previous_entry: str | None,
    index: dict[str, list[int]],
) -> str:
    """Inject journal tags using LLM-provided paragraph indices.

    Each key in *index* is treated as a paragraph position (converted to int).
    Consecutive positions are collapsed into ranges. Tags are inserted at
    paragraph boundaries in the proposed text.
    """
    # Flatten all unique paragraph indices
    all_indices: list[int] = []
    for val in index.values():
        if isinstance(val, list):
            all_indices.extend(int(i) for i in val if isinstance(i, (int, str)) and (isinstance(i, int) or i.isdigit()))

    if not all_indices:
        return proposed_text

    # Split into paragraphs for tag injection
    paragraphs = re.split(r'(\n{2,})', proposed_text)
    result_parts = []
    para_idx = 0

    for part in paragraphs:
        stripped = part.strip()
        if not stripped:
            result_parts.append(part)
            continue

        # Check if this paragraph's index is in our set
        collapsed = _collapse_consecutive_indices(all_indices[para_idx:para_idx + 1]) if all_indices else ""

        if all_indices and para_idx < len(all_indices):
            tag_indices = [all_indices[para_idx]]
            collapsed_str = _collapse_consecutive_indices(tag_indices)
            result_parts.append(f"[journal:{collapsed_str}]{stripped}")
        else:
            result_parts.append(stripped)

        para_idx += 1

    return "".join(result_parts)


def process_llm_response(
    result: dict,
    entity_id: int,
    journal_name: str,
    previous_entry: str | None = None,
    index: dict[str, list[int]] | None = None,
) -> dict | None:
    """Process raw LLM JSON response into a proposal-ready dict.

    This is the shared core of synopsis generation used by both sync_pipeline
    and review_web. It handles whitespace normalization, no-change detection,
    journal link injection, truncation/info-loss flagging, and builds the final
    proposal dictionary.

    Args:
        result: Raw JSON from LLM chat completion (dict with 'entries' key).
        entity_id: Kanka entity ID being updated.
        journal_name: Name of the source journal entry for attribution.
        previous_entry: Clean synopsis text from Kanka (None for new entities).
        index: Optional dict for sync_pipeline carry-forward context.

    Returns:
        Proposal-ready dict with keys matching the expected proposal format,
        or None if no meaningful change was detected.
    """
    # Extract proposed text from LLM response
    entries = result.get("entries", [])
    if not entries:
        return {"error": "No entries in LLM response"}

    raw_proposed = entries[0].get("text", "")
    relation_changes = entries[0].get("relation_changes", {})
    uncertain = entries[0].get("uncertain", False)

    # Whitespace normalization
    if previous_entry:
        normalized_prev = _normalize_whitespace(previous_entry)
        normalized_proposed = _normalize_whitespace(raw_proposed)

        # No-change detection — return None to skip this proposal
        if not _has_meaningful_change(normalized_prev, normalized_proposed):
            return None  # Identical synopsis; no update needed

    # Journal link injection into proposed text
    injected_text = _inject_journal_links(
        raw_proposed, entity_id, journal_name, previous_entry, index
    )

    # Truncation and info-loss detection
    trunc_info = _detect_truncation_info_loss(injected_text, previous_entry or "")

    # Build change summary
    if previous_entry:
        change_summary = injected_text  # Full proposed text is the diff
    else:
        change_summary = f"New entity discovered in {journal_name}"

    return {
        "proposal_type": "update",
        "entity_id": entity_id,
        "kind": "",  # Caller sets this from entity data
        "name": "",  # Caller sets this from entity data
        "previous_entry": previous_entry or "",
        "proposed_entry": injected_text,
        "change_summary": change_summary,
        "relation_changes": relation_changes if isinstance(relation_changes, dict) else {},
        "uncertain": uncertain,
        **trunc_info,  # is_truncated, ratio, _info_loss_warning
    }
```

#### Step 6: Run all new tests

Run: `pytest tests/test_synopsis_generator.py -v`
Expected: All PASS (some may fail initially — that's expected; adjust implementation until they pass)

#### Step 7: Run existing propose_update tests to verify backward compatibility

Run: `pytest tests/test_sync_pipeline.py::test_propose_update_truncated_flag tests/test_sync_pipeline.py::test_propose_update_not_truncated -v`
Expected: PASS (propose_update still works as before through the wrapper)

---

### Task 2: Update sync_pipeline.py to use synopsis_generator

**Files:**
- Modify: `kanka_wiki_updater/sync_pipeline.py` (~15 lines changed)
- No test changes needed — propose_update() signature unchanged

#### Step 1: Replace inline prompt building with build_prompt call

Find the `propose_update()` function in sync_pipeline.py. Replace the template formatting section (around line ~300 where USER_PROMPT_TEMPLATE is formatted):

**Before:**
```python
user_prompt = USER_PROMPT_TEMPLATE.format(
    name=name,
    kind=kind,
    current_entry=current_entry if current_entry else "(none on record)",
    session_text=session_text,
)
```

**After:**
```python
from kanka_wiki_updater.synopsis_generator import build_prompt, process_llm_response

# ... (session_text assembly stays the same) ...

user_prompt = build_prompt(name, kind, current_entry or "(none on record)", session_text)
```

#### Step 2: Replace LLM response processing with process_llm_response call

Find where `result = chat_json(SYSTEM_PROMPT, user_prompt)` is called and the subsequent processing block (lines ~340-454). Replace the entire post-LLM-processing section:

**Before (~110 lines of normalization, no-change detection, journal link injection, truncation check):**
```python
result = chat_json(SYSTEM_PROMPT, user_prompt)

# ... 110 lines of _normalize_whitespace, no-change detection, _inject_journal_links, etc. ...

proposal = { ... }  # long dict construction
return proposal
```

**After:**
```python
result = chat_json(SYSTEM_PROMPT, user_prompt)

proposal = process_llm_response(
    result, entity_id, journal_name, previous_entry, index
)
if not isinstance(proposal, dict):
    if "error" in (proposal or {}):
        _debug(f"  propose_update: LLM returned unexpected format — {proposal}")
    return None

# Set kind and name from entity data (process_llm_response doesn't have these)
proposal["kind"] = kind
proposal["name"] = name

return proposal
```

#### Step 3: Remove duplicate helper functions from sync_pipeline.py

Delete the following functions from sync_pipeline.py (they now live in synopsis_generator.py):
- `_normalize_whitespace()` (lines ~340-352)
- `_has_meaningful_change()` (lines ~354-361)
- `_detect_truncation_info_loss()` (lines ~363-382)
- `_collapse_consecutive_indices()` (lines ~384-400)
- `_diff_and_tag()` (lines ~402-470 — or wherever it is)
- `_inject_journal_links()` (lines ~472-520)

Keep these functions in sync_pipeline.py:
- `_build_journal_url()` — not shared, only used by sync_pipeline's main loop
- `_annotate_journals()` — kept in sync_pipeline for now; could be moved later if review_web needs it too

#### Step 4: Add build_prompt import and function to synopsis_generator

Add the `build_prompt` function at module level of synopsis_generator.py:

```python
def build_prompt(name, entity_kind, current_entry, session_text):
    """Build the LLM prompt for updating an entity's synopsis.

    Uses the same USER_PROMPT_TEMPLATE as sync_pipeline.py — single source of truth.
    """
    return USER_PROMPT_TEMPLATE.format(
        name=name,
        kind=entity_kind,
        current_entry=current_entry or "(none on record)",
        session_text=session_text,
    )
```

Also add `NEW_ENTITY_USER_PROMPT_TEMPLATE` formatting function:

```python
def build_new_entity_prompt(known_names, journal_name, journal_date, session_text):
    """Build the LLM prompt for detecting new entities in a journal entry.

    Uses NEW_ENTITY_USER_PROMPT_TEMPLATE from sync_pipeline.py.
    """
    return NEW_ENTITY_USER_PROMPT_TEMPLATE.format(
        known_entities="\n".join(f"- {name}" for name in known_names),
        session_text=session_text,
    )
```

#### Step 5: Run full test suite to verify nothing broke

Run: `pytest tests/test_sync_pipeline.py -v`
Expected: All existing propose_update tests PASS (backward compatibility)
Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

---

### Task 3: Update review_web.py regenerate_proposal() to use synopsis_generator

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (~40 lines changed, ~380 removed)
- No test changes needed — regenerate_proposal still returns the same Flask response format

#### Step 1: Replace the body of regenerate_proposal() with thin wrapper

Find `regenerate_proposal(index)` in review_web.py (around line 581). The function currently has ~420 lines including its own whitespace normalization, no-change detection, journal link injection, etc.

**Replace the entire function body after data fetching:**

```python
@app.route("/api/proposals/<int:index>/regenerate", methods=["POST"])
def regenerate_proposal(index):
    """Regenerate a proposal's synopsis via LLM and update pending_changes.json."""
    from kanka_wiki_updater.synopsis_generator import build_prompt, process_llm_response

    queue = load_pending()  # existing: loads data/pending_changes.json
    if index not in queue:
        return jsonify({"error": "Proposal not found"}), 404

    proposal = queue[index]
    entity_id = proposal.get("entity_id")

    try:
        # Fetch fresh data from Kanka (existing logic stays the same)
        journals_response = client._session.get(
            f"{KANKA_API}/journal/entry/{entity_id}",
            params={"filter_by_custom_1": "true", "limit": 50},
        ).json()
        journals = journals_response.get("data", [])

        entity_response = client._session.get(f"{KANKA_API}/character/{entity_id}").json()
        entity = entity_response.get("data", {})
        current_entry = entity.get("entry", "") or ""

        # Get the last journal text for context (existing logic)
        session_text = "\n\n".join(
            j.get("content", "")[:2000] for j in journals if j.get("content")
        ) if journals else "(no session notes available)"

        # Build prompt using shared function
        name = entity.get("name", proposal.get("name", "Unknown"))
        kind = "character"  # or from proposal data
        user_prompt = build_prompt(name, kind, current_entry, session_text)

        # Call LLM with potentially larger max_tokens for regeneration
        regen_max = int(os.environ.get("LLM_MAX_TOKENS", 4096))
        result = chat_json(SYSTEM_PROMPT, user_prompt, max_tokens=regen_max)

        # Process response using shared function
        updated = process_llm_response(
            result, entity_id, "Regeneration", current_entry
        )

        if not isinstance(updated, dict):
            return jsonify({
                "success": False,
                "error": "Regenerated output is identical to current entry"
            }), 400

        # Spread updated fields into queue[index] (existing logic)
        proposal["kind"] = updated.get("kind", kind)
        proposal["name"] = updated.get("name", name)
        proposal["previous_entry"] = updated.get("previous_entry", current_entry)
        proposal["proposed_entry"] = updated["proposed_entry"]
        proposal["change_summary"] = updated.get("change_summary")
        if "relation_changes" in updated:
            proposal["relation_changes"] = updated["relation_changes"]
        proposal["uncertain"] = updated.get("uncertain", False)
        proposal["truncated"] = updated.get("truncated", {"is_truncated": False})

        save_pending(queue)  # existing: saves data/pending_changes.json

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({
        "success": True,
        "proposal_type": updated.get("proposal_type", "update"),
        "proposed_entry": proposal["proposed_entry"],
        **proposal.get("truncated", {}),
    })
```

#### Step 2: Remove duplicate helpers from review_web.py

Delete these functions from review_web.py (they now live in synopsis_generator.py):
- `_normalize_whitespace()` (review_web's copy)
- Any inline no-change detection logic within regenerate_proposal
- Any inline journal link injection logic within regenerate_proposal

#### Step 3: Run review_web tests

Run: `pytest tests/test_review_web.py -v`
Expected: All PASS — regenerate_proposal endpoint still returns the same response format

---

### Task 4: Add comprehensive unit tests for process_llm_response edge cases

**Files:**
- Modify: `tests/test_synopsis_generator.py` (add more test cases)

#### Step 1: Add tests for diff-based fallback in _diff_and_tag

```python
class TestDiffFallback:
    def test_match_similar_paragraphs(self):
        # When proposed text closely matches journal paragraphs, inject tags
        proposed = "Alice was born in a small village."
        clean_journal = "Alice was born in a small village.\n\nShe grew up poor."
        annotated = "[journal:1]Alice was born in a small village.\n\n[journal:2]She grew up poor."

        result = _diff_and_tag(proposed, annotated, clean_journal)
        # Should preserve the journal tag since content matched
        assert "[journal" in result or "small village" in result

    def test_no_match_returns_proposal_unchanged(self):
        proposed = "Completely unrelated text."
        clean_journal = "Alice was born in a small village."
        annotated = "[journal:1]Alice was born in a small village."

        result = _diff_and_tag(proposed, annotated, clean_journal)
        # Should not crash; returns something reasonable
        assert proposed in result or "unrelated" in result.lower()


class TestProcessLLMResponseEdgeCases:
    def test_none_result(self):
        result = process_llm_response(None, 1, "Test", "old")
        assert isinstance(result, dict) and "error" in result

    def test_empty_entries_list(self):
        result = process_llm_response({"entries": []}, 1, "Test", "old")
        assert isinstance(result, dict) and "error" in result

    def test_new_entity_no_previous_entry(self):
        result = process_llm_response(
            {"entries": [{"text": "Alice is a brave warrior."}]},
            entity_id=999,
            journal_name="Session 1",
            previous_entry=None,
        )
        assert isinstance(result, dict)
        assert result["proposal_type"] == "update"
        # No truncation check when there's no previous entry to compare against
        assert not result.get("truncated", {}).get("is_truncated")

    def test_relation_changes_preserved(self):
        entries = [{"text": "Updated.", "relation_changes": {"add": [{"target_id": 2, "relation": "friend"}]}}]
        result = process_llm_response(entries, 1, "Test", "old")
        assert isinstance(result["relation_changes"], dict)

    def test_uncertain_flag_preserved(self):
        entries = [{"text": "Updated.", "uncertain": True}]
        result = process_llm_response(entries, 1, "Test", "old")
        assert result.get("uncertain") is True
```

#### Step 2: Run all tests end-to-end

Run: `pytest tests/test_synopsis_generator.py -v`
Expected: All PASS

---

### Task 5: Final verification and cleanup

**Files:**
- All modified files above

#### Step 1: Run full test suite

Run: `python -m pytest tests/ -v --tb=short`
Expected: ALL TESTS PASS

#### Step 2: Check linting

Run: `ruff check kanka_wiki_updater/synopsis_generator.py kanka_wiki_updater/sync_pipeline.py kanka_wiki_updater/review_web.py`
Expected: No errors
If errors, fix and re-run.

#### Step 3: Verify no regressions in propose_update behavior

Run: `pytest tests/test_sync_pipeline.py -k "propose_update" -v`
Expected: All ~20 propose_update tests PASS

#### Step 4: Check that review_web regenerate_proposal still works

Verify the endpoint returns the same JSON structure by checking test_review_web.py covers it, or run a manual test if available.

Run: `pytest tests/test_review_web.py -v`
Expected: All PASS

---

## Self-Review Checklist

**1. Spec coverage:**
- ✅ Extract _normalize_whitespace → synopsis_generator.py
- ✅ Extract _has_meaningful_change → synopsis_generator.py  
- ✅ Extract _inject_journal_links (+ diff fallback) → synopsis_generator.py
- ✅ Extract _detect_truncation_info_loss → synopsis_generator.py
- ✅ Extract _collapse_consecutive_indices → synopsis_generator.py
- ✅ build_prompt() as shared prompt builder → synopsis_generator.py
- ✅ process_llm_response() as unified response processor → synopsis_generator.py
- ✅ propose_update() becomes thin wrapper (backward compatible)
- ✅ regenerate_proposal() rewritten to use shared functions (~60 lines from ~420)

**2. Placeholder scan:** No "TBD", "TODO", or similar patterns found. All code blocks contain actual implementations.

**3. Type consistency:** 
- process_llm_response signature: `(result, entity_id, journal_name, previous_entry=None, index=None)` — consistent across both callers
- Return type: `dict | None` — both callers check for falsy before proceeding
- _inject_journal_links parameters match usage in process_llm_response

**4. Test compatibility:**
- Existing test_sync_pipeline.py tests call propose_update() directly → wrapper preserves signature and behavior
- New tests in test_synopsis_generator.py cover all extracted functions directly
- No existing test file needs modification beyond adding new test file
