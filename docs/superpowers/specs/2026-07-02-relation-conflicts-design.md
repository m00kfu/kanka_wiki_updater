# Relation Conflict Resolution System

**Date**: 2026-07-02  
**Status**: Approved  

## Problem

Kanka allows only one relation between any two entities (owner → target). The LLM may propose a "create" action for an entity pair that already has an existing relation with a different label. Without conflict detection, these would be silently applied as duplicates or cause API errors.

Additionally, multiple journals in the same sync run could produce competing proposals for the same entity pair (e.g., journal 1 proposes creating "Ally → Bob", journal 2 proposes creating "Rival → Bob" for the same owner).

## Goals

- Automatically convert "create" actions to "update" when a relation already exists between the pair.
- Detect and flag cross-proposal conflicts where two different proposals suggest competing new relations for the same entity pair.
- Surface all label mismatches during sync (console warnings) and review (inline annotations).
- Keep conflict logic isolated in its own module, testable without external dependencies.

## Non-goals

- Semantic matching of relation types ("Ally" ≈ "Friend") — that comes later if needed.
- Auto-resolving cross-proposal conflicts — those always require human input.
- Changes to LLM prompts or the sync loop's journal processing logic.

## Design

### New module: `relation_conflicts.py`

#### Data model

```python
@dataclass
class RelationConflict:
    proposal_idx: int                    # position in pending queue
    entity_name: str                     # relation owner
    target_name: str                     # relation target
    existing_type: str | None            # prior label, or None if no prior relation
    proposed_type: str                   # what the LLM wants
    conflict_kind: Literal["label_mismatch", "cross_proposal"]
```

#### Functions

**`resolve_creates_to_updates(proposals: list[dict], entity_index: dict) -> (list[dict], list[RelationConflict])`**

For each proposal, walks its `relation_changes`. If an action is `"create"` and the owner→target pair already exists in `entity_index`, converts the action to `"update"`. When the existing label differs from the proposed one, attaches a `RelationConflict(label_mismatch)` to that relation_change dict under a new `.conflict` key.

**`detect_cross_proposal_conflicts(proposals: list[dict]) -> list[RelationConflict]`**

After resolve has run, scans remaining `"create"` actions across all proposals for duplicate entity pairs (same owner + target). Flags these as `cross_proposal` conflicts — the second proposal wins by default but both are flagged.

**`apply_resolutions(proposals: list[dict], entity_index: dict) -> (list[dict], list[RelationConflict])`**

Convenience wrapper: calls resolve then detect, returns resolved proposals and all conflict objects.

#### Conflict annotation on relation_changes

Each modified `relation_change` dict gains a `.conflict` key (a `RelationConflict` instance or `None`). This is preserved through the queue and surfaced in review without changing existing fields.

### Integration points

**sync_pipeline.py — before state.append_to_queue()**

```python
from .relation_conflicts import apply_resolutions

resolved_proposals, conflicts = apply_resolutions(all_proposals, index)
for c in conflicts:
    if c.conflict_kind == "label_mismatch":
        print(f"  ! Auto-updated existing relation: {c.entity_name} ↔ {c.target_name}: "
              f"'{c.existing_type}' → '{c.proposed_type}'")
state.append_to_queue(resolved_proposals)
```

**review.py — in review_proposal()**

When displaying proposed relations, check for `.conflict` on each `relation_change`:

```python
if rc.get('conflict'):
    c = rc['conflict']
    if c.conflict_kind == 'label_mismatch':
        print(colors.red(
            f"  !! Conflict: {c.entity_name} already has '{c.existing_type}' -> "
            f"{c.target_name}, proposing '{c.proposed_type}'. Verify which is correct."
        ))
```

### Testing strategy

| Test | What it covers |
|---|---|
| `test_no_conflict_when_relation_does_not_exist` | Normal create, no prior relation — passes through unchanged |
| `test_create_converted_to_update_when_relation_exists` | Create action + existing pair → converted to update |
| `test_no_flag_when_labels_match` | Create with same label as existing → no conflict object |
| `test_label_mismatch_flagged_and_updated` | Create with different label → conflict attached, action = "update" |
| `test_cross_proposal_conflict_detected` | Two proposals proposing creates for the same pair |
| `test_apply_resolutions_returns_both_results` | Wrapper returns resolved queue + all conflicts combined |

Location: `tests/test_relation_conflicts.py` — pure function tests, no mocking needed.

## Files changed

| File | Change |
|---|---|
| `kanka_wiki_updater/relation_conflicts.py` | New module (~100 lines) |
| `kanka_wiki_updater/sync_pipeline.py` | Import + call `apply_resolutions()` before queueing |
| `kanka_wiki_updater/review.py` | Display conflict warnings on relation_changes with `.conflict` set |
| `tests/test_relation_conflicts.py` | New test file (~10 tests) |

## Future extensions (out of scope)

- Semantic synonym map for relation types ("Ally" ≈ "Friend") to reduce false conflicts.
- Attitude-only changes (same label, different attitude) — currently always treated as update with no conflict flag.
- Cross-session conflict detection (relations conflicting across separate sync runs).
