# Architecture — Kanka Wiki Updater

> Code Intelligence Graph: **932 nodes | 1,565 edges | 41 clusters | 45 execution flows**

## Overview

This tool automates the maintenance of a [Kanka](https://kanka.io) RPG campaign wiki from session journal entries. It fetches new journals, uses an LLM to propose synopsis updates and entity changes for every character/location mentioned, then presents those proposals to a human reviewer who approves or rejects them one-by-one. Nothing reaches Kanka without human sign-off.

```
┌──────────────┐    ┌───────────────┐    ┌──────────────┐    ┌──────────────┐
│  ingest/     │───▶│   review_web  │───▶│  kanka_client│───▶│ KANKA API    │
│  sync engine │    │ Flask UI :5555│    │              │    │ v1           │
│ propose      │◀───│ approve/reject│◀───│ HTTP wrapper │    └──────────────┘
│ changes to   │    └───────┬───────┘    └──────────────┘               ▲
│ pending_     │         apply                                          │
│ changes.json │                                              revert  ▼
└───────┬──────┘                                                    ┌──────────┐
        │                                                           │ revert.py│
        └──────────────────────────────────────────────────────────▶│          │
                                                                    └──────────┘
```

**Core principle: nothing auto-publishes.** The sync pipeline writes proposals to `data/pending_changes.json`. Changes reach Kanka only through `review_web.py` (web UI) or `sync_engine.apply_proposal()` after human approval. A separate `revert.py` tool undoes the most recent unreverted batch, and `reset_to_first.py` provides a nuclear undo to first recorded state.

## Functional Areas

### 1. Configuration — `config.py`
Loads `.env` settings: required `KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`; optional `LMSTUDIO_MODEL`, rate limits, batch size, LLM timeout/token budget. Uses `python-dotenv`.

### 2. Kanka API Client — `kanka_client.py`
Thin HTTP wrapper around Kanka API v1. Exposes a `KankaClient` class with methods for fetching characters, locations, relations, and journal entries. Throttles requests (~1/2s default).

### 3. Entity Mentions — `mentions.py`
Entity resolution utilities:
- **`strip_html()`** — strips HTML tags from synopsis text
- **`linked_entity_ids()`** — parses `[entity:N]` wiki links in text
- **`fuzzy_name_matches()`** — plain-text names that match known entities (first-word matching)
- **`find_unlinked_mentions()`** — detects unlinked plain-text mentions as warnings
- **`auto_link_entry()`** / **`add_missing_entity_tags()`** — auto-link entity names in proposed text

### 4. Progress Tracking — `progress.py`
In-memory tracker with Unicode progress bar using `\r` carriage return for in-place terminal updates (disabled on Windows).

### 5. LLM Integration — `llm_client.py`, `llm_providers.py`
- **`llm_client.py`** re-exports `chat_json(prompt)` as a convenience entry point
- **`llm_providers.py`** provides provider implementations: `lmstudio_chat()` for LM Studio's OpenAI-compatible server, `gemini_chat()` for Google Gemini, and `opencode_chat()` for OpenCode Zen. The dispatcher `chat_json()` routes based on the `LLM_PROVIDER` env var (choices: `"lmstudio"`, `"gemini"`, `"opencode"`). Parses JSON output with `json_repair` fallback.

### 6. Synopsis Generation — `synopsis_generator.py`
Shared LLM-driven synopsis generation used by both ingest_journal and review_web:
- **`build_synopsis_proposal()`** — core function: builds prompts, calls the LLM, parses response, normalises paragraphs, deduplicates journal tags, injects `<i>` italics into citation tags
- **`propose_update()`** — thin wrapper for ingest_journal compatibility
- **`build_entity_index()`** — one-pass index of all characters/locations/organizations/creatures keyed by entity_id (shared with review_web)
- **`relation_summary()`** — human-readable relation summary for LLM prompts
- **`_annotate_journals()`** / **`_normalize_proposed()`** / **`_deduplicate_journal_tags()`** / **`_inject_journal_italics()`** — internal helpers
- **`_is_known_entity()`** — filters false-positive new-entity suggestions (accent-insensitive, substring/fuzzy matching)

### 7. Prompt Templates — `prompts.py`
System and user prompt templates for synopsis updates and new-entity detection. Strict JSON schema enforcement: no markdown fences, escaped quotes/backslashes. Preserves `[entity:N]` wiki link tokens character-for-character.

### 8. Ingest Engine — `ingest_journal.py` (shared core)
The shared ingestion logic used by both CLI and web backends. Orchestrates the full fetch→analyze→propose cycle:

1. **`build_entity_index()`** — gathers all known characters/locations + their current synopses and relations into an in-memory index
2. Fetches journals since last sync cursor via `KankaClient.get_journals()`
3. **`find_mentioned_entities()`** — parses `[entity:N]` links + fuzzy name matches per journal
4. For each mentioned entity, calls the LLM through **`propose_update()`** to generate a revised synopsis and relation changes
5. Applies relation changes locally so subsequent journals see updated drafts (**"memory index"** carry-forward)
6. Identifies new entities via **`propose_new_entities()`**
7. Writes all proposals to `data/pending_changes.json`

Idempotent: tracks processed journal IDs in `data/processed_journals.json`. Uses pluggable callbacks for progress reporting.

### 9. Sync Orchestrator — `sync_orchestrator.py` (job lifecycle)
Manages sync job lifecycle for background execution (used by web UI). Provides:
- **`start_sync(callbacks, cancelled_event, limit)`** — runs ingest in a daemon thread, returns a job ID
- **`cancel_sync(job_id)`** — marks a running job as cancelled
- **`get_job_status(job_id)`** / **`list_jobs()`** — query job status and progress
- Thread-safe job state protected by `_lock`; per-job entity progress tracking via `_set_entity_status()`

### 10. Sync Engine — `sync_engine.py` (apply to Kanka)
Business logic for pushing proposals back to the Kanka API:
- **`resolve_entity(client, name, entity_index_cache)`** — resolves an entity name to its ID via wiki-link parsing, exact match, substring match, or fuzzy matching
- **`build_entity_index(client)`** — one-pass index of characters + locations keyed by `entity_id`
- **`apply_proposal(client, proposal, entity_index_cache)`** — applies a single proposal: creates new entities (character/location/organization/creature), updates synopsis text, and handles relation create/update/delete with conflict detection for reverse-direction duplicates

### 11. Sync Events — `sync_events.py` (event type constants)
Defines the contract between any frontend (web, TUI) and `ingest_journal.run_ingest()`:
- Event types: `EVENT_ENTITY_PROGRESS`, `EVENT_PROPOSAL_PUSHED`, `EVENT_STATUS_CHANGE`, `EVENT_SYNC_START`, `EVENT_SYNC_COMPLETE`
- Entity statuses: `'pending'`, `'processing'`, `'done'`, `'skipped'`, `'error'`

### 12. Web Review — `review_web.py` (Flask UI)
Web-based review UI at `http://127.0.0.1:5555`:
- **Review tab** — browse, filter, edit proposals inline; manage relations via modals; regenerate truncated proposals
- **Sync tab** — run sync pipeline from browser with SSE output streaming and cancel support
- Uses `sync_orchestrator.start_sync()` for background execution with per-entity progress tracking
- Reads/writes `data/pending_changes.json` via `queue_manager`
- Entity name editing and type selector in the web UI

> **Note:** The standalone CLI review tool (`review.py`) has been removed. All review is done through the web UI.

### 13. Queue Manager — `queue_manager.py`
Queue I/O and in-memory manipulation for `pending_changes.json`:
- **File I/O**: `load_queue()`, `save_queue()` — read/write the pending changes file
- **In-memory helpers**: `edit_proposal_text()`, `update_status()` — update proposal text or status
- **Relation CRUD**: `add_relation_change()`, `delete_relation_change()`, `update_relation_change()` — manage relation change entries

### 14. Revert — `revert.py`
One-step undo of the most recent unreverted review batch:
- Reverses relation changes (create→delete, update→restore previous, delete→recreate) in reverse order
- Then reverts synopsis edits to their pre-batch state
- New-entity deletions happen last
- Only works on batches with structured revert logs

### 15. Reset to First — `reset_to_first.py`
Nuclear undo: reads `pending_changes.json`, deduplicates by entity name (first occurrence wins), and PATCHes each unique entity's synopsis back to its earliest recorded `previous_entry`. Supports `--dry-run` mode.

### 16. Relation Conflicts — `relation_conflicts.py`
Detects and resolves conflicts in proposed relation changes:
- **`resolve_creates_to_updates()`** — converts 'create' actions to 'update' when a prior relation already exists (with conflict dict for label mismatches)
- **`detect_cross_proposal_conflicts()`** — flags competing proposals for the same owner→target pair
- **`apply_resolutions()`** — convenience wrapper running both checks

### 17. State Management — `state.py`
Plain JSON files under `data/`:
- **Sync cursor** (`get_last_sync()`) — tracks the last successfully processed journal position
- **Pending queue** (`load_queue()` / `save_queue()` / `append_to_queue()`) — manages `pending_changes.json`
- **Applied log** (`log_applied_batch()`, `get_last_applied_batch()`) — records run_id, title, timestamp; supports revert flag
- **Processed journal IDs** (`mark_journal_processed()`, `get_processed_journal_ids()`) — idempotency guard
- **Batch tracking** (`mark_batch_reverted()`) — marks batches as reverted so they're excluded from future reverts

## Key Execution Flows

### Flow A: Ingest Engine (shared core)
```
ingest_journal.run_ingest(client, callbacks, limit, cancelled_event)
  ├── build_entity_index()                    → kanka_client (characters, locations, relations)
  ├── KankaClient.get_journals(since=cursor)  → KANKA API (fetch new journals since cursor)
  └── for each journal:
        find_mentioned_entities(journal_text)   → mentions.py
          ├── strip_html()                     → mentions.py
          ├── linked_entity_ids(text, index)   → mentions.py ([entity:N] parsing)
          └── fuzzy_name_matches(text, index)  → mentions.py (plain-text fallback)
        propose_new_entities(journal, names)    → LLM new-entity scan
        for each mentioned entity:
          callbacks['entity_started'](name, jnl)
          propose_update(entity_id, entity, journal, index)
            └── chat_json(system_prompt + user_prompt)  → llm_providers.py:chat_json()
                 ├── lmstudio_chat() or gemini_chat() or opencode_chat()   → LM Studio / Gemini / OpenCode
                 └── _extract_json(raw_response)        → json_repair fallback
          callbacks['llm_result'](name, jnl, ok, data)
          apply_relation_changes_locally(draft)    → memory index carry-forward
  ├── state.append_to_queue(proposals)           → state.py (write pending_changes.json)
  └── callbacks['sync_completed'](total_proposals, total_new_entities)
```

### Flow B: Web Review & Apply
```
review_web.py:main (Flask app :5555)
  ├── GET /              → review tab (browse/filter proposals)
  ├── GET /api/proposals          → load queue via queue_manager.load_queue()
  ├── POST /api/proposals/:id/status  → update status via queue_manager.update_status()
  ├── PUT /api/proposals/:id/edit   → edit text via queue_manager.edit_proposal_text()
  ├── POST /api/proposals/:id/relation → CRUD relations via queue_manager
  ├── POST /api/proposals/:id/sync      → apply via sync_engine.apply_proposal()
  ├── POST /api/proposals/:id/regenerate→ re-run truncated proposal via LLM (2× tokens)
  └── resolve_name_to_id(name, index) ← sync_engine.resolve_entity()
```

### Flow C: Sync Pipeline (background job)
```
sync_orchestrator:start_sync(callbacks, cancelled_event, limit)
  ├── creates daemon thread → _sync_thread()
  │     └── ingest_journal.run_ingest(client, callbacks, limit, cancelled_event)
  │           ├── build_entity_index()                    → kanka_client
  │           ├── KankaClient.get_journals(since=cursor)  → KANKA API
  │           └── for each journal:
  │                 find_mentioned_entities(journal_text)   → mentions.py
  │                 propose_new_entities(journal, names)    → LLM scan
  │                 for each mentioned entity:
  │                   callbacks['entity_started'](name, jnl)
  │                   propose_update(entity_id, entity, journal, index)
  │                     └── chat_json(system_prompt + user_prompt) → llm_providers.py
  │                   callbacks['llm_result'](name, jnl, ok, data)
  │                 state.append_to_queue(new_candidates)
  │               callbacks['journal_completed'](jnl, n_ent, n_sug)
  │           └── callbacks['sync_completed'](total_proposals, total_new_entities)
  ├── get_job_status(job_id) → {'status': 'running'|'completed'|'error'|'cancelled', ...}
  └── cancel_sync(job_id)    → sets status to 'cancelled'
```

### Flow D: Revert
```
revert.py:main
  ├── get_last_applied_batch()     → state.py (find latest unreverted batch)
  └── for each change in reverse:
        revert_relation_result(change)
          ├── _rel_target(entity, rel_type)   → find relation object
          ├── _rel_id(relations, target)      → find by name match
          └── KankaClient.delete_relation() / create_relation()  → undo
        revert_update_entry(change)           → restore previous synopsis text
        revert_new_entity_entry(change)       → delete newly created entity
  └── mark_batch_reverted(run_id)             → state.py (mark as reverted)
```

### Flow E: Reset to First
```
reset_to_first.py:main([--dry-run])
  ├── load pending_changes.json
  ├── deduplicate by entity_name (first occurrence = oldest version)
  └── for each unique entity:
        KankaClient.update_entity_entry(kind, local_id, previous_entry)
```

## Module Dependency Graph

```
config.py          ← .env (python-dotenv, os)
kanka_client.py    ← config.py (KANKA_TOKEN, KANKA_CAMPAIGN_ID)
progress.py        ← stdlib only
mentions.py        ← stdlib only (regex, html.parser)
prompts.py         ← stdlib only (string constants)

llm_providers.py   ← config.py, requests, stdlib (lmstudio_chat, gemini_chat, opencode_chat)
llm_client.py      ← llm_providers.py  (re-exports chat_json, LLMError)
synopsis_generator.py ← mentions.py, prompts.py, llm_client.py, config

ingest_journal.py   ← synopsis_generator.py, kanka_client.py, config, state, mentions
sync_orchestrator.py ← ingest_journal.py, kanka_client.py, config (threading, job lifecycle)
sync_engine.py      ← kanka_client.py, config (apply proposals to Kanka)
sync_events.py      ← stdlib only (event type constants)
queue_manager.py    ← state.py, config (pending_changes.json I/O + in-memory helpers)
relation_conflicts.py ← stdlib only (conflict detection/resolution)
review_web.py       ← synopsis_generator.py, mentions.py, kanka_client.py,
                      queue_manager.py, sync_engine.py, sync_orchestrator.py, flask
revert.py           ← kanka_client.py
reset_to_first.py   ← kanka_client.py, config

state.py            ← stdlib only (json, pathlib)
```

## Mermaid Architecture Diagram

```mermaid
graph TB
    subgraph External["External Services"]
        KANKA[Kanka API v1<br/>characters, locations,<br/>relations, journals]
        LMSTUDIO[LM Studio Server<br/>OpenAI-compatible /v1/chat/completions]
        GEMINI[Google Gemini API]
        OPCODE[OpenCode Zen API<br/>opencode.ai/zen/v1]    end

    subgraph Config["Configuration Layer"]
        CONFIG[config.py<br/>.env loader<br/>KANKA_TOKEN, KANKA_CAMPAIGN_ID,<br/>LMSTUDIO_MODEL, rate limits]
    end

    subgraph DataLayer["Data Persistence"]
        PENDING[data/pending_changes.json<br/>proposal queue]
        PROCESSED[data/processed_journals.json<br/>idempotency journal IDs]
        APPLIED[data/applied_batches.json<br/>audit log with run_id/revert flag]
        SYNC_CURSOR[data/sync_state.json<br/>last sync position]
    end

    subgraph Core["Core Modules"]
        CLIENT[kanka_client.py<br/>KankaClient class<br/>HTTP wrapper, rate limiting]
        MENTIONS[mentions.py<br/>strip_html, linked_entity_ids,<br/>fuzzy_name_matches,<br/>find_unlinked_mentions,<br/>auto_link_entry]
        PROGRESS[progress.py<br/>ProgressTracker<br/>Unicode progress bar]
        PROMPTS[prompts.py<br/>SYSTEM_PROMPT / USER_PROMPT<br/>strict JSON schema templates]
    end

    subgraph LLM["LLM Integration"]
        LLMPROVIDERS[llm_providers.py<br/>lmstudio_chat(), gemini_chat(),<br/>opencode_chat()<br/>chat_json() dispatcher]
        LLMCLIENT[llm_client.py<br/>re-exports chat_json, LLMError]
    end

    subgraph SynopsisGen["Synopsis Generation"]
        SYNOPSIS[synopsis_generator.py<br/>build_synopsis_proposal(),<br/>propose_update(), build_entity_index()<br/>relation_summary(), _annotate_journals()]
    end

    subgraph Pipeline["Sync Engine"]
        INGEST[ingest_journal.py<br/>shared ingest core<br/>run_ingest(), find_mentioned_entities(),<br/>propose_new_entities(), apply_relation_changes_locally()]
        ORCHESTRATE[sync_orchestrator.py<br/>job lifecycle<br/>start_sync(), cancel_sync(), get_job_status()<br/>threading, progress tracking]
        APPLY[sync_engine.py<br/>apply to Kanka<br/>resolve_entity(), build_entity_index(),<br/>apply_proposal()]  
        EVENTS[sync_events.py<br/>event type constants<br/>ENTITY_STATUSES]    end

    subgraph Review["Review Layer"]
        WEBReview[review_web.py<br/>Flask UI :5555<br/>tabbed: Review + Sync tabs<br/>SSE streaming, inline edit,<br/>relation modals, queue_manager]  
      end

    subgraph Queue["Queue Management"]
        QM[queue_manager.py<br/>load_queue(), save_queue()<br/>edit_proposal_text(), update_status()<br/>add/delete/update_relation_change()]
      end

    subgraph Conflicts["Relation Conflicts"]
        RC[relation_conflicts.py<br/>resolve_creates_to_updates()<br/>detect_cross_proposal_conflicts()<br/>apply_resolutions()]
      end

    subgraph Reset["Reset / Undo"]
        REVERTMODULE[revert.py<br/>one-step undo of batch<br/>relation reversal → synopsis restore → entity delete]
        RESETFIRST[reset_to_first.py<br/>nuclear undo to first state<br/>--dry-run support]
      end

    %% Config dependencies
    CLIENT --> CONFIG
    LLMPROVIDERS --> CONFIG
    LLMCLIENT -.->|re-exports| LLMPROVIDERS

    %% Pipeline dependencies
    INGEST --> BINDEX
    INGEST --> FINDMEN
    INGEST --> PROPOSE
    INGEST --> RELLOCAL
    INGEST --> NEWENT
    INGEST --> CLIENT
    INGEST --> SYNOPSIS
    ORCHESTRATE --> INGEST
    ORCHESTRATE --> CLIENT
    LLMCLIENT -.->|calls| LLMPROVIDERS

    BINDEX --> CLIENT
    FINDMEN --> MENTIONS
    PROPOSE --> LLMCLIENT
    PROPOSE --> PROMPTS
    NEWENT --> CLIENT
    SYNOPSIS --> LLMCLIENT
    SYNOPSIS --> PROMPTS
    SYNOPSIS --> MENTIONS

    %% Review dependencies
    WEBReview --> SYNOPSIS
    WEBReview --> CLIENT
    WEBReview --> MENTIONS
    WEBReview --> QM
    WEBReview --> APPLY
    WEBReview --> ORCHESTRATE
    WEBReview -.->|approve→write| PENDING

    %% Queue, conflicts, reset dependencies
    QM --> STATE
    RC --> SYNOPSIS
    APPLY --> CLIENT
    APPLY --> QM
    REVERTMODULE --> CLIENT
    RESETFIRST --> CLIENT

    %% Data layer connections
    INGEST -.->|read/write| PROCESSED
    INGEST -.->|update cursor| SYNC_CURSOR
    WEBReview -.->|read queue| PENDING
    WEBReview -.->|write queue| PENDING
    REVERTMODULE -.->|read audit + mark reverted| APPLIED

    %% External connections
    CLIENT --> KANKA
    LLMPROVIDERS --> LMSTUDIO
    LLMPROVIDERS --> GEMINI

    %% Styling
    classDef external fill:#f0f0f0,stroke:#999,stroke-dasharray:5 5
    classDef core fill:#d4edda,stroke:#28a745
    classDef review fill:#cce5ff,stroke:#007bff
    classDef data fill:#fff3cd,stroke:#ffc107
    classDef llm fill:#e2d5f1,stroke:#6f42c1
    classDef pipeline fill:#d0f0f0,stroke:#0891b2
    classDef queue fill:#fef3c7,stroke:#d97706
    classDef conflicts fill:#fce7f3,stroke:#db2777
    classDef reset fill:#fee2e2,stroke:#dc2626

    class KANKA,LMSTUDIO,GEMINI external
    class CLIENT,MENTIONS,PROGRESS,PROMPTS core
    class WEBReview review
    class PENDING,PROCESSED,APPLIED,SYNC_CURSOR data
    class LLMPROVIDERS,LLMCLIENT llm
    class SYNOPSIS synopsis
    class INGEST,ORCHESTRATE,APPLY,EVENTS pipeline
    class QM queue
    class RC conflicts
    class REVERTMODULE,RESETFIRST reset
```

## Data Flow Summary

1. **Sync** (`ingest_journal.run_ingest()`) reads Kanka characters/locations → fetches new journals since last cursor → for each journal, finds mentioned entities (wiki links + fuzzy names) → calls LLM to propose synopsis updates and relation changes per entity → applies relation drafts locally so subsequent proposals see updated context → writes all proposals to `pending_changes.json`. Web UI triggers this via `sync_orchestrator.start_sync()` in a background thread with SSE progress streaming.

2. **Review** (`review_web.py`) loads the pending queue → shows new-entity suggestions first, then diffs → human approves/rejects each via web UI → approved changes are applied via `sync_engine.apply_proposal()` directly to Kanka API + logged as an audit batch.

3. **Revert** (`revert.py`) finds the most recent unreverted batch → reverses relation operations in reverse order (create→delete, update→restore, delete→recreate) → restores synopsis text to pre-batch state → deletes newly created entities → marks batch as reverted.

4. **Reset to First** (`reset_to_first.py`) reads `pending_changes.json`, deduplicates by entity name (first occurrence = oldest version), and PATCHes each unique entity's synopsis back to its earliest recorded `previous_entry`. Nuclear undo option.

## Key Constraints & Gotchas

- **Idempotency**: processed journal IDs prevent duplicate proposals on re-run
- **Memory index carry-forward**: when multiple journals mention the same entity in one sync run, each proposal's `proposed_entry` is carried forward so subsequent journals see the latest draft (not the stale Kanka copy)
- **In-fiction date sorting**: journals are sorted by their in-fiction date, not creation timestamp
- **Nothing auto-publishes**: pipeline only writes proposals; human review gates all Kanka API writes
- **Revert limitation**: `revert.py` only does one-step undo of the most recent unreverted batch; reverted batches leave journals marked "processed"
- **Rate limits**: default 1 request / 2.1s (`KANKA_REQUEST_INTERVAL`); adjust via env var
- **Background sync jobs**: web UI runs sync in daemon threads with per-entity progress tracking and cancellation support via `sync_orchestrator`
- **Relation conflict detection**: `relation_conflicts.py` detects label mismatches (existing relation vs proposed) and cross-proposal conflicts (same owner→target pair in multiple proposals)
