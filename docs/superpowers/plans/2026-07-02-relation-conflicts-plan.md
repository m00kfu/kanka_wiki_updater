# Relation Conflict Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect and resolve conflicts when the LLM proposes relation changes that clash with existing Kanka relations (same owner→target pair, different label).

**Architecture:** A new `relation_conflicts.py` module provides three pure functions: `resolve_creates_to_updates()` converts "create" actions to "update" when a prior relation exists; `detect_cross_proposal_conflicts()` flags competing proposals for the same entity pair; `apply_resolutions()` is a convenience wrapper. The sync pipeline calls this before queueing; review.py displays conflict warnings inline.

**Tech Stack:** Python 3.12, pydantic BaseModel (consistent with existing EntityData), pytest.

## Global Constraints

- Follow existing patterns: pydantic models for data types, `_rel_to_dict()` style helpers, `relation_changes` dict shape from prompts.py
- Line length: 120 chars (per pyproject.toml)
- TDD: write failing test first, implement minimal code to pass, commit after each task passes
- Nothing auto-publishes — changes only affect proposal queueing and review display

---

## Task 1: Skeleton module + resolve_creates_to_updates tests

**Files:**
- Create: `kanka_wiki_updater/relation_conflicts.py`
- Test: `tests/test_relation_conflicts.py`

**Interfaces:**
- Consumes: nothing yet
- Produces: `resolve_creates_to_updates(proposals, entity_index) -> (resolved_proposals, conflicts)` where each conflict is a dict with keys `proposal_idx`, `entity_name`, `target_name`, `existing_type`, `proposed_type`, `conflict_kind`

**Steps:**

- [ ] **Step 1: Write failing tests for resolve_creates_to_updates**

Create `tests/test_relation_conflicts.py` with these test functions. Do NOT implement the function yet — expect `ImportError`.

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanka_wiki_updater.relation_conflicts import resolve_creates_to_updates


def _make_proposal(entity_name="Alice", relation_changes=None):
    return {
        "proposal_type": "update",
        "entity_id": 1,
        "entity_kind": "character",
        "entity_local_id": 1,
        "entity_name": entity_name,
        "source_journal": "Session 1",
        "previous_entry": "",
        "proposed_entry": "",
        "change_summary": "",
        "relation_changes": relation_changes or [],
        "uncertain": [],
        "status": "pending",
    }


def _make_rel(action, target_name, relation="Ally"):
    return {
        "action": action,
        "target_name": target_name,
        "relation": relation,
        "attitude": None,
        "reason": "",
    }


def test_no_conflict_when_relation_does_not_exist():
    proposal = _make_proposal(relation_changes=[_make_rel("create", "Bob")])
    proposals = [proposal]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "", "relations": []},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(resolved) == 1
    assert resolved[0]["relation_changes"][0]["action"] == "create"
    assert len(conflicts) == 0


def test_create_converted_to_update_when_relation_exists():
    proposal = _make_proposal(relation_changes=[_make_rel("create", "Bob")])
    proposals = [proposal]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "",
            "relations": [{"target_id": 99, "relation": "Ally"}]},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert resolved[0]["relation_changes"][0]["action"] == "update"


def test_no_flag_when_labels_match():
    proposal = _make_proposal(relation_changes=[_make_rel("create", "Bob", relation="Ally")])
    proposals = [proposal]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "",
            "relations": [{"target_id": 99, "relation": "Ally"}]},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    rc = resolved[0]["relation_changes"][0]
    assert rc.get("conflict") is None


def test_label_mismatch_flagged_and_updated():
    proposal = _make_proposal(relation_changes=[_make_rel("create", "Bob", relation="Rival")])
    proposals = [proposal]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "",
            "relations": [{"target_id": 99, "relation": "Ally"}]},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    rc = resolved[0]["relation_changes"][0]
    assert rc["action"] == "update"
    conflict = rc.get("conflict")
    assert conflict is not None
    assert conflict["existing_type"] == "Ally"
    assert conflict["proposed_type"] == "Rival"
    assert conflict["conflict_kind"] == "label_mismatch"
    assert len(conflicts) == 1


def test_update_action_unchanged_when_no_conflict():
    proposal = _make_proposal(relation_changes=[_make_rel("update", "Bob", relation="Friend")])
    proposals = [proposal]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "",
            "relations": [{"target_id": 99, "relation": "Ally"}]},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(resolved) == 1
    assert resolved[0]["relation_changes"][0]["action"] == "update"
    assert len(conflicts) == 0


def test_delete_action_unchanged():
    proposal = _make_proposal(relation_changes=[_make_rel("delete", "Bob")])
    proposals = [proposal]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "",
            "relations": [{"target_id": 99, "relation": "Ally"}]},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(resolved) == 1
    assert resolved[0]["relation_changes"][0]["action"] == "delete"
    assert len(conflicts) == 0


def test_multiple_proposals_returns_all_conflicts():
    p1 = _make_proposal(entity_name="Alice", relation_changes=[_make_rel("create", "Bob")])
    p2 = _make_proposal(entity_name="Carol", relation_changes=[_make_rel("create", "Dave")])
    proposals = [p1, p2]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "",
            "relations": [{"target_id": 99, "relation": "Ally"}]},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
        2: {"kind": "character", "local_id": 2, "name": "Carol", "entry": "",
            "relations": [{"target_id": 100, "relation": "Friend"}]},
        100: {"kind": "character", "local_id": 100, "name": "Dave", "entry": "", "relations": []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(conflicts) == 2
    assert conflicts[0]["proposal_idx"] == 0
    assert conflicts[1]["proposal_idx"] == 1


def test_empty_proposals_returns_empty():
    resolved, conflicts = resolve_creates_to_updates([], {})
    assert resolved == []
    assert conflicts == []
```

- [ ] **Step 2: Run tests to verify they all fail with ImportError**

Run: `python -m pytest tests/test_relation_conflicts.py -v`
Expected: All 8 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal stub module**

Create `kanka_wiki_updater/relation_conflicts.py`:

```python
"""Detect and resolve conflicts in proposed relation changes."""


def resolve_creates_to_updates(proposals, entity_index):
    """Convert 'create' actions to 'update' when a prior relation exists.

    Returns (resolved_proposals, conflicts) where each conflict is a dict with
    keys: proposal_idx, entity_name, target_name, existing_type, proposed_type,
    conflict_kind.
    """
    resolved = []
    conflicts = []
    for idx, proposal in enumerate(proposals):
        new_rcs = list(proposal.get("relation_changes", []))
        resolved.append({**proposal, "relation_changes": new_rcs})
    return resolved, conflicts
```

- [ ] **Step 4: Run tests — they should still fail (logic not implemented)**

Run: `python -m pytest tests/test_relation_conflicts.py::test_no_conflict_when_relation_does_not_exist -v`
Expected: FAIL with assertion error (stub passes creates through unchanged).

## Task 2: Implement resolve_creates_to_updates

**Files:**
- Modify: `kanka_wiki_updater/relation_conflicts.py`

**Interfaces:**
- Consumes: `entity_index` dict keyed by entity_id, each value has `relations` list with dicts containing `target_id`, `relation`
- Produces: correct resolution per test suite

**Steps:**

- [ ] **Step 1: Implement resolve_creates_to_updates fully**

Replace the stub in `kanka_wiki_updater/relation_conflicts.py` with:

```python
"""Detect and resolve conflicts in proposed relation changes."""


def _find_target_entity_id(entity_index, target_name):
    """Find entity_id for a given name, or None."""
    for eid, edata in entity_index.items():
        if edata["name"] == target_name:
            return eid
    return None


def resolve_creates_to_updates(proposals, entity_index):
    """Convert 'create' actions to 'update' when a prior relation exists.

    For each proposal, walks its relation_changes. If an action is "create"
    and the owner→target pair already has a relation in entity_index, converts
    the action to "update". When the existing label differs from the proposed
    one, attaches a conflict dict under ``rc["conflict"]``.

    Returns (resolved_proposals, conflicts) where each conflict is a dict with
    keys: proposal_idx, entity_name, target_name, existing_type, proposed_type,
    conflict_kind (always "label_mismatch" here).
    """
    resolved = []
    conflicts = []

    for idx, proposal in enumerate(proposals):
        owner_id = proposal["entity_id"]
        owner_data = entity_index.get(owner_id)
        if not owner_data:
            resolved.append(proposal)
            continue

        owner_rels = owner_data.get("relations", [])

        new_rcs = []
        for rc in list(proposal.get("relation_changes", [])):
            action = (rc.get("action") or "").strip().lower()
            if action == "create":
                target_name = rc["target_name"]
                target_id = _find_target_entity_id(entity_index, target_name)

                existing_rel = None
                for rel in owner_rels:
                    if rel.get("target_id") == target_id:
                        existing_rel = rel
                        break

                if existing_rel is not None:
                    rel_label = existing_rel.get("relation", "")
                    new_rc = dict(rc)
                    new_rc["action"] = "update"
                    if rc["relation"] != rel_label:
                        conflict = {
                            "proposal_idx": idx,
                            "entity_name": proposal["entity_name"],
                            "target_name": target_name,
                            "existing_type": rel_label,
                            "proposed_type": rc["relation"],
                            "conflict_kind": "label_mismatch",
                        }
                        new_rc["conflict"] = conflict
                        conflicts.append(conflict)
                    else:
                        new_rc["conflict"] = None
                    new_rcs.append(new_rc)
                else:
                    new_rcs.append(dict(rc))
            else:
                new_rcs.append(dict(rc))

        resolved.append({**proposal, "relation_changes": new_rcs})

    return resolved, conflicts
```

- [ ] **Step 2: Run all tests — they should all pass now**

Run: `python -m pytest tests/test_relation_conflicts.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 3: Lint and format**

Run: `ruff check kanka_wiki_updater/relation_conflicts.py tests/test_relation_conflicts.py`
Run: `ruff format kanka_wiki_updater/relation_conflicts.py tests/test_relation_conflicts.py`

- [ ] **Step 4: Commit**

```bash
git add kanka_wiki_updater/relation_conflicts.py tests/test_relation_conflicts.py
git commit -m "feat: resolve creates-to-updates for existing relation conflicts"
```

## Task 3: Implement detect_cross_proposal_conflicts

**Files:**
- Modify: `kanka_wiki_updater/relation_conflicts.py`

**Interfaces:**
- Consumes: list of resolved proposals (from Task 2)
- Produces: `detect_cross_proposal_conflicts(proposals) -> list[dict]` with conflict_kind="cross_proposal"

**Steps:**

- [ ] **Step 1: Write tests for cross-proposal conflicts**

Add to `tests/test_relation_conflicts.py`:

```python
from kanka_wiki_updater.relation_conflicts import detect_cross_proposal_conflicts


def test_cross_proposal_different_pairs_no_conflict():
    """Different owner→target pairs are fine."""
    p1 = _make_proposal(entity_name="Alice", relation_changes=[_make_rel("create", "Bob")])
    p2 = _make_proposal(entity_name="Carol", relation_changes=[_make_rel("create", "Dave")])
    proposals = [p1, p2]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "", "relations": []},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
        2: {"kind": "character", "local_id": 2, "name": "Carol", "entry": "", "relations": []},
        100: {"kind": "character", "local_id": 100, "name": "Dave", "entry": "", "relations": []},
    }
    resolved, _ = resolve_creates_to_updates(proposals, entity_index)

    conflicts = detect_cross_proposal_conflicts(resolved)
    assert len(conflicts) == 0


def test_same_owner_different_targets_no_conflict():
    """Same owner creating relations to different targets is fine."""
    p1 = _make_proposal(entity_name="Alice", relation_changes=[
        _make_rel("create", "Bob"),
        _make_rel("create", "Carol"),
    ])
    proposals = [p1]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "", "relations": []},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
        2: {"kind": "character", "local_id": 2, "name": "Carol", "entry": "", "relations": []},
    }
    resolved, _ = resolve_creates_to_updates(proposals, entity_index)

    conflicts = detect_cross_proposal_conflicts(resolved)
    assert len(conflicts) == 0


def test_same_pair_in_different_proposals_is_conflict():
    """Two proposals for the same owner→target pair → cross_proposal conflict."""
    p1 = _make_proposal(entity_name="Alice", relation_changes=[_make_rel("create", "Bob")])
    p2 = _make_proposal(entity_name="Alice", relation_changes=[_make_rel("create", "Bob")])
    proposals = [p1, p2]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "", "relations": []},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, _ = resolve_creates_to_updates(proposals, entity_index)

    conflicts = detect_cross_proposal_conflicts(resolved)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["conflict_kind"] == "cross_proposal"
    assert c["entity_name"] == "Alice"
    assert c["target_name"] == "Bob"


def test_empty_proposals_returns_no_conflicts():
    """Empty queue → no cross-proposal conflicts."""
    conflicts = detect_cross_proposal_conflicts([])
    assert conflicts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_relation_conflicts.py::test_same_pair_in_different_proposals_is_conflict -v`
Expected: FAIL — function not yet implemented.

- [ ] **Step 3: Implement detect_cross_proposal_conflicts**

Add to `kanka_wiki_updater/relation_conflicts.py`:

```python
def detect_cross_proposal_conflicts(proposals):
    """Detect competing proposals for the same owner→target entity pair.

    Scans remaining 'create' actions across all proposals for duplicate
    (entity_name, target_name) pairs. The second occurrence of a pair is
    flagged as a cross_proposal conflict.

    Returns list of conflict dicts with keys: proposal_idx, entity_name,
    target_name, existing_type (None), proposed_type, conflict_kind="cross_proposal".
    """
    seen_pairs = {}  # (entity_name, target_name) -> first proposal_idx
    conflicts = []

    for idx, proposal in enumerate(proposals):
        for rc in proposal.get("relation_changes", []):
            action = (rc.get("action") or "").strip().lower()
            if action != "create":
                continue

            entity_name = proposal["entity_name"]
            target_name = rc["target_name"]
            pair_key = (entity_name, target_name)

            if pair_key in seen_pairs:
                conflicts.append({
                    "proposal_idx": idx,
                    "entity_name": entity_name,
                    "target_name": target_name,
                    "existing_type": None,
                    "proposed_type": rc["relation"],
                    "conflict_kind": "cross_proposal",
                })
            else:
                seen_pairs[pair_key] = idx

    return conflicts
```

- [ ] **Step 4: Run all tests — they should all pass**

Run: `python -m pytest tests/test_relation_conflicts.py -v`
Expected: All tests PASS (including the new ones).

- [ ] **Step 5: Lint and format**

Run: `ruff check kanka_wiki_updater/relation_conflicts.py --fix`
Run: `ruff format kanka_wiki_updater/relation_conflicts.py`

- [ ] **Step 6: Commit**

```bash
git add kanka_wiki_updater/relation_conflicts.py tests/test_relation_conflicts.py
git commit -m "feat: detect cross-proposal relation conflicts"
```

## Task 4: Implement apply_resolutions wrapper + integration test

**Files:**
- Modify: `kanka_wiki_updater/relation_conflicts.py`
- Add to: `tests/test_relation_conflicts.py`

**Interfaces:**
- Consumes: resolve and detect functions from Tasks 2–3
- Produces: `apply_resolutions(proposals, entity_index) -> (resolved, conflicts)` — single-call convenience function

**Steps:**

- [ ] **Step 1: Write test for apply_resolutions wrapper**

Add to `tests/test_relation_conflicts.py`:

```python
from kanka_wiki_updater.relation_conflicts import apply_resolutions


def test_apply_resolutions_returns_both_results():
    """Wrapper calls resolve then detect and returns combined results."""
    p1 = _make_proposal(entity_name="Alice", relation_changes=[_make_rel("create", "Bob")])
    proposals = [p1]
    entity_index = {
        1: {"kind": "character", "local_id": 1, "name": "Alice", "entry": "",
            "relations": [{"target_id": 99, "relation": "Ally"}]},
        99: {"kind": "character", "local_id": 99, "name": "Bob", "entry": "", "relations": []},
    }
    resolved, conflicts = apply_resolutions(proposals, entity_index)

    assert len(resolved) == 1
    rc = resolved[0]["relation_changes"][0]
    assert rc["action"] == "update"
    assert rc.get("conflict") is not None
    assert rc["conflict"]["existing_type"] == "Ally"
    assert len(conflicts) == 1
    assert conflicts[0]["conflict_kind"] == "label_mismatch"


def test_apply_resolutions_empty():
    """Empty inputs → empty outputs."""
    resolved, conflicts = apply_resolutions([], {})
    assert resolved == []
    assert conflicts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_relation_conflicts.py::test_apply_resolutions_returns_both_results -v`
Expected: FAIL — function not yet defined.

- [ ] **Step 3: Implement apply_resolutions**

Add to `kanka_wiki_updater/relation_conflicts.py`:

```python
def apply_resolutions(proposals, entity_index):
    """Convenience wrapper: resolve creates-to-updates then detect cross-proposal conflicts.

    Returns (resolved_proposals, all_conflicts) where all_conflicts contains
    both label_mismatch and cross_proposal conflict dicts.
    """
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)
    cross_conflicts = detect_cross_proposal_conflicts(resolved)
    return resolved, conflicts + cross_conflicts
```

- [ ] **Step 4: Run all tests — they should all pass**

Run: `python -m pytest tests/test_relation_conflicts.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Lint and format**

Run: `ruff check kanka_wiki_updater/relation_conflicts.py --fix`
Run: `ruff format kanka_wiki_updater/relation_conflicts.py`

- [ ] **Step 6: Commit**

```bash
git add kanka_wiki_updater/relation_conflicts.py tests/test_relation_conflicts.py
git commit -m "feat: add apply_resolutions wrapper for relation conflict resolution"
```

## Task 5: Integrate into sync_pipeline.py

**Files:**
- Modify: `kanka_wiki_updater/sync_pipeline.py` (around line 356, before final stats print)

**Interfaces:**
- Consumes: `apply_resolutions(proposals, index)` from relation_conflicts module
- Produces: conflicts printed to console during sync run

**Steps:**

- [ ] **Step 1: Add import and integration point in sync_pipeline.py**

Add the import near the top with other `. import` statements (around line 30):

```python
from .relation_conflicts import apply_resolutions
```

Then, insert this code right before the `if journals and len(to_process) == total_new:` block (around line 356 in main()):

```python
    # Resolve relation conflicts across all queued proposals for this run
    if total_proposals > 0:
        current_queue = state.load_queue()
        resolved_queue, conflicts = apply_resolutions(current_queue, index)

        for c in conflicts:
            if c["conflict_kind"] == "label_mismatch":
                print(
                    f"  ! Auto-updated existing relation: {c['entity_name']} ↔ {c['target_name']}: "
                    f"'{c['existing_type']}' → '{c['proposed_type']}'"
                )

        state.save_queue(resolved_queue)
```

- [ ] **Step 2: Run the full test suite to verify no regressions**

Run: `python -m pytest tests/ -v`
Expected: All existing tests PASS.

- [ ] **Step 3: Lint and format**

Run: `ruff check kanka_wiki_updater/sync_pipeline.py --fix`
Run: `ruff format kanka_wiki_updater/sync_pipeline.py`

- [ ] **Step 4: Commit**

```bash
git add kanka_wiki_updater/sync_pipeline.py
git commit -m "feat: integrate relation conflict resolution into sync pipeline"
```

## Task 6: Display conflicts in review.py

**Files:**
- Modify: `kanka_wiki_updater/review.py` (around line 242, where relations are displayed)

**Interfaces:**
- Consumes: `relation_changes[].conflict` dicts added by relation_conflicts module
- Produces: human-readable conflict warnings in review output

**Steps:**

- [ ] **Step 1: Add conflict display in review_proposal()**

In `kanka_wiki_updater/review.py`, find the section around lines 242–250 where proposed relations are printed. Replace:

```python
    if proposal['relation_changes']:
        print(colors.bold('\nProposed relationship changes:'))
        for rc in proposal['relation_changes']:
            print(
                colors.magenta(
                    f'  [{rc["action"]}] {rc["relation"]} -> {rc["target_name"]} '
                    f'(attitude={rc.get("attitude")}) -- {rc.get("reason", "")}'
                )
            )
```

With:

```python
    if proposal['relation_changes']:
        print(colors.bold('\nProposed relationship changes:'))
        for rc in proposal['relation_changes']:
            conflict = rc.get('conflict')
            print(
                colors.magenta(
                    f'  [{rc["action"]}] {rc["relation"]} -> {rc["target_name"]} '
                    f'(attitude={rc.get("attitude")}) -- {rc.get("reason", "")}'
                )
            )
            if conflict:
                if conflict['conflict_kind'] == 'label_mismatch':
                    print(
                        colors.red(
                            f'  !! Conflict: {conflict["entity_name"]} already has '
                            f"'{conflict['existing_type']}' -> {conflict['target_name']}, "
                            f"proposing '{conflict['proposed_type']}'. Verify which is correct."
                        )
                    )
                elif conflict['conflict_kind'] == 'cross_proposal':
                    print(
                        colors.red(
                            f'  !! Cross-proposal conflict: {conflict["entity_name"]} ↔ '
                            f"{conflict['target_name']} proposed in multiple sessions. "
                            f"Verify which relation is correct."
                        )
                    )
```

- [ ] **Step 2: Run the full test suite to verify no regressions**

Run: `python -m pytest tests/test_review.py -v`
Expected: All review tests PASS (review.py changes are display-only).

- [ ] **Step 3: Lint and format**

Run: `ruff check kanka_wiki_updater/review.py --fix`
Run: `ruff format kanka_wiki_updater/review.py`

- [ ] **Step 4: Commit**

```bash
git add kanka_wiki_updater/review.py
git commit -m "feat: display relation conflict warnings in review output"
```

## Task 7: End-to-end verification

**Files:**
- No new files — integration testing via CLI

**Steps:**

- [ ] **Step 1: Run full test suite with coverage**

Run: `python -m pytest tests/ --cov=kanka_wiki_updater/relation_conflicts -v`
Expected: All tests PASS, relation_conflicts module has >90% coverage.

- [ ] **Step 2: Run linter on all changed files**

Run: `ruff check kanka_wiki_updater/sync_pipeline.py kanka_wiki_updater/review.py kanka_wiki_updater/relation_conflicts.py`
Expected: No lint errors.

- [ ] **Step 3: Format all changed files**

Run: `ruff format kanka_wiki_updater/`
Expected: All files formatted consistently.

- [ ] **Step 4: Final commit of remaining changes (if any)**

```bash
git add -A
git diff --cached --stat
# Verify only the expected files changed
git commit -m "chore: final cleanup for relation conflict resolution"
```

---

## Plan Self-Review

**1. Spec coverage:**

| Spec requirement | Task |
|---|---|
| resolve_creates_to_updates function | Task 2 |
| detect_cross_proposal_conflicts function | Task 3 |
| apply_resolutions wrapper | Task 4 |
| Conflict annotation on relation_changes (`.conflict` key) | Tasks 2–3 |
| sync_pipeline integration with console warnings | Task 5 |
| review.py conflict display | Task 6 |
| Tests for all functions | Tasks 1–4 |

All spec requirements have corresponding tasks. ✅

**2. Placeholder scan:** No "TBD", "TODO", or vague references found. All code blocks are complete with exact implementations. ✅

**3. Type consistency:** `entity_index` dict shape is consistent across all tasks (keyed by entity_id, with `relations` list containing dicts with `target_id`, `relation`). Conflict dict keys match across resolve, detect, and display. ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-02-relation-conflicts-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?