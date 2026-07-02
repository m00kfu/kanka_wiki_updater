# Architecture — Kanka Wiki Updater

> Code Intelligence Graph: **932 nodes | 1,565 edges | 41 clusters | 45 execution flows**

## Overview

This tool automates the maintenance of a [Kanka](https://kanka.io) RPG campaign wiki from session journal entries. It fetches new journals, uses an LLM to propose synopsis updates and entity changes for every character/location mentioned, then presents those proposals to a human reviewer who approves or rejects them one-by-one. Nothing reaches Kanka without human sign-off.

```
┌──────────────┐    ┌───────────────┐    ┌──────────────┐    ┌──────────────┐
│  sync_pipeline│───▶│   review.py   │───▶│  kanka_client│───▶│ KANKA API    │
│               │    │  (CLI)        │    │              │    │ v1           │
│ propose       │◀───│ approve/reject│◀───│ HTTP wrapper │    └──────────────┘
│ changes to    │    └───────┬───────┘    └──────────────┘               ▲
│ data/         │            │                                         │
│ pending_      │     ┌──────┴───────┐                                  │
│ changes.json  │◀────│ review_web   │                                  │
└───────┬───────┘    │ Flask UI :5555│                                apply
        │            └────────────────┘                                  ▼
        │                                                                  │
        └──────────────────────────────────────────────────────────────────┘
```

**Core principle: nothing auto-publishes.** The sync pipeline writes proposals to `data/pending_changes.json`. Changes reach Kanka only through `review.py` or `review_web.py` after human approval. A separate `revert.py` tool undoes the most recent unreverted batch.

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
- **`llm_providers.py`** provides provider implementations: `lmstudio_chat()` for LM Studio's OpenAI-compatible server, `gemini_chat()` for Google Gemini. The dispatcher `chat_json()` routes based on the `LLM_PROVIDER` env var. Parses JSON output with `json_repair` fallback.

### 6. Prompt Templates — `prompts.py`
System and user prompt templates for synopsis updates and new-entity detection. Strict JSON schema enforcement: no markdown fences, escaped quotes/backslashes. Preserves `[entity:N]` wiki link tokens character-for-character.

### 7. Sync Pipeline — `sync_pipeline.py` (main orchestrator)
The central data flow module. Orchestrates the full fetch→analyze→propose cycle:

1. **`build_entity_index()`** — gathers all known characters/locations + their current synopses and relations into an in-memory index
2. Fetches journals since last sync cursor via `KankaClient.get_journals()`
3. **`find_mentioned_entities()`** — parses `[entity:N]` links + fuzzy name matches per journal
4. For each mentioned entity, calls the LLM through **`propose_update()`** to generate a revised synopsis and relation changes
5. Applies relation changes locally so subsequent journals see updated drafts (**"memory index"** carry-forward)
6. Identifies new entities via **`propose_new_entities()`**
7. Writes all proposals to `data/pending_changes.json`

Idempotent: tracks processed journal IDs in `data/processed_journals.json`. Uses `ProgressTracker` for per-journal progress display.

### 8. CLI Review — `review.py`
Interactive terminal review tool (`python -m kanka_wiki_updater.review`):
- Shows new-entity suggestions first, then synopsis/relation diffs
- Options: **yes** (apply all), **no** (reject), approve-synopsis-only
- Calls back into `build_entity_index()` so approved new entities are immediately available as relation targets
- Flags dropped mention links and unlinked plain-text mentions as warnings
- Writes applied changes to an audit log with run_id and revert flag

### 9. Web Review — `review_web.py`
Flask web UI at `http://127.0.0.1:5555`:
- **Review tab** — browse, filter, edit proposals inline; manage relations via modals
- **Sync tab** — run sync pipeline from browser with SSE output streaming
- Reads/writes `data/pending_changes.json`

### 10. Revert — `revert.py`
One-step undo of the most recent unreverted review batch:
- Reverses relation changes (create→delete, update→restore previous, delete→recreate) in reverse order
- Then reverts synopsis edits to their pre-batch state
- New-entity deletions happen last
- Only works on batches with structured revert logs

### 11. State Management — `state.py`
Plain JSON files under `data/`:
- **Sync cursor** (`get_last_sync()`) — tracks the last successfully processed journal position
- **Pending queue** (`load_queue()` / `save_queue()` / `append_to_queue()`) — manages `pending_changes.json`
- **Applied log** (`log_applied_batch()`, `get_last_applied_batch()`) — records run_id, title, timestamp; supports revert flag
- **Processed journal IDs** (`mark_journal_processed()`, `get_processed_journal_ids()`) — idempotency guard
- **Batch tracking** (`mark_batch_reverted()`) — marks batches as reverted so they're excluded from future reverts

## Key Execution Flows

### Flow A: Sync Pipeline (main entry point)
```
sync_pipeline.py:main
  ├── build_entity_index()                    → kanka_client (characters, locations, relations)
  ├── KankaClient.get_journals()              → KANKA API (fetch new journals since cursor)
  └── for each journal:
        find_mentioned_entities(journal_text)
          ├── strip_html()                     → mentions.py
          ├── linked_entity_ids(text, index)   → mentions.py ([entity:N] parsing)
          └── fuzzy_name_matches(text, index)  → mentions.py (plain-text fallback)
        propose_update(entity_id, journal, entity_index)
          ├── chat_json(system_prompt + user_prompt)  → llm_providers.py:chat_json()
          │     ├── lmstudio_chat() or gemini_chat()   → LM Studio / Gemini API
          │     └── _extract_json(raw_response)        → json_repair fallback
          └── apply_relation_changes_locally(draft)    → memory index update
        propose_new_entities(...)               → new character/location suggestions
  ├── ProgressTracker.mark_done() / finish()   → progress.py (terminal display)
  └── append_to_queue(proposals)                → state.py (write pending_changes.json)
```

### Flow B: CLI Review
```
review.py:main
  ├── load_queue()                              → state.py (read pending_changes.json)
  ├── build_entity_index()                      ← sync_pipeline.py (for relation targets)
  └── for each proposal:
        review_new_entity_proposal(proposal)     → new entities first
          ├── strip_html(proposed_text)          → mentions.py
          ├── prompt_choice(yes/no/approve)      → terminal UI
          ├── unlinked_mention_warning(...)      → mentions.py warnings
          └── save_fn(result, ...)               → state.py (audit log)
        review_proposal(proposal)                → synopsis + relation diffs
          ├── has_meaningful_change(old, new)    → skip empty changes
          ├── print_diff(old_text, new_text)     → colored terminal diff
          └── save_fn(result, ...)               → state.py (audit log)
  └── apply approved changes to KANKA API        → kanka_client (post updates)
```

### Flow C: Web Review & Sync
```
review_web.py:main (Flask app :5555)
  ├── GET /              → review tab (browse/filter proposals)
  ├── POST /approve      → approve all pending changes
  ├── POST /reject       → reject all pending changes
  ├── PUT /proposal/:id  → inline edit proposal text
  ├── Relation modals    → create/update/delete relations via API
  ├── resolve_name_to_id(name, index) ← sync_pipeline:build_entity_index()
  └── GET /sync/stream   → SSE output streaming of sync_pipeline.py:main()
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

## Module Dependency Graph

```
config.py          ← .env (python-dotenv, os)
kanka_client.py    ← config.py (KANKA_TOKEN, KANKA_CAMPAIGN_ID)
progress.py        ← stdlib only
mentions.py        ← stdlib only (regex, html.parser)
prompts.py         ← stdlib only (string constants)

llm_providers.py   ← config.py, requests, stdlib
llm_client.py      ← llm_providers.py  (re-exports chat_json)

sync_pipeline.py   ← mentions.py, llm_client.py, kanka_client.py, progress.py, prompts.py
review.py          ← sync_pipeline.py, mentions.py, kanka_client.py
review_web.py      ← sync_pipeline.py, mentions.py, kanka_client.py, flask
revert.py          ← kanka_client.py

state.py           ← stdlib only (json, pathlib)
```

## Mermaid Architecture Diagram

```mermaid
graph TB
    subgraph External["External Services"]
        KANKA[Kanka API v1<br/>characters, locations,<br/>relations, journals]
        LMSTUDIO[LM Studio Server<br/>OpenAI-compatible /v1/chat/completions]
        GEMINI[Google Gemini API]
    end

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
        LLMPROVIDERS[llm_providers.py<br/>lmstudio_chat(), gemini_chat()<br/>chat_json() dispatcher]
        LLMCLIENT[llm_client.py<br/>re-exports chat_json()]
    end

    subgraph Pipeline["Sync Pipeline"]
        SYNCPipeline[sync_pipeline.py<br/>main orchestrator]
        BINDEX[build_entity_index()<br/>character/location index]
        FINDMEN[find_mentioned_entities()<br/>link parsing + fuzzy match]
        PROPOSE[propose_update()<br/>LLM synopsis generation]
        RELLOCAL[apply_relation_changes_locally()<br/>memory index carry-forward]
        NEWENT[propose_new_entities()<br/>new entity detection]
    end

    subgraph Review["Review Layer"]
        CLIReview[review.py<br/>CLI review tool<br/>yes/no/approve-synopsis-only]
        WEBReview[review_web.py<br/>Flask UI :5555<br/>tabbed: Review + Sync tabs<br/>SSE streaming, inline edit, relation modals]
    end

    subgraph Revert["Revert"]
        REVERTMODULE[revert.py<br/>one-step undo of batch<br/>relation reversal → synopsis restore → entity delete]
    end

    %% Config dependencies
    CLIENT --> CONFIG
    LLMPROVIDERS --> CONFIG
    LLMCLIENT -.->|re-exports| LLMPROVIDERS

    %% Pipeline dependencies
    SYNCPipeline --> BINDEX
    SYNCPipeline --> FINDMEN
    SYNCPipeline --> PROPOSE
    SYNCPipeline --> RELLOCAL
    SYNCPipeline --> NEWENT
    SYNCPipeline --> PROGRESS
    SYNCPipeline --> CLIENT
    LLMCLIENT -.->|calls| LLMPROVIDERS

    BINDEX --> CLIENT
    FINDMEN --> MENTIONS
    PROPOSE --> LLMCLIENT
    PROPOSE --> PROMPTS
    NEWENT --> CLIENT

    %% Review dependencies
    CLIReview --> SYNCPipeline
    CLIReview --> CLIENT
    CLIReview --> MENTIONS
    WEBReview --> SYNCPipeline
    WEBReview --> CLIENT
    WEBReview --> MENTIONS
    CLIReview -.->|approve→write| PENDING
    WEBReview -.->|approve→write| PENDING

    %% Revert dependencies
    REVERTMODULE --> CLIENT

    %% Data layer connections
    SYNCPipeline -.->|read/write| PROCESSED
    SYNCPipeline -.->|update cursor| SYNC_CURSOR
    CLIReview -.->|read queue| PENDING
    WEBReview -.->|read/write queue| PENDING
    CLIReview -.->|audit log| APPLIED
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

    class KANKA,LMSTUDIO,GEMINI external
    class CLIENT,MENTIONS,PROGRESS,PROMPTS core
    class CLIReview,WEBReview review
    class PENDING,PROCESSED,APPLIED,SYNC_CURSOR data
    class LLMPROVIDERS,LLMCLIENT llm
```

## Data Flow Summary

1. **Sync** (`sync_pipeline.py:main`) reads Kanka characters/locations → fetches new journals since last cursor → for each journal, finds mentioned entities (wiki links + fuzzy names) → calls LLM to propose synopsis updates and relation changes per entity → applies relation drafts locally so subsequent proposals see updated context → writes all proposals to `pending_changes.json`

2. **Review** (`review.py` or `review_web.py`) loads the pending queue → shows new-entity suggestions first, then diffs → human approves/rejects each → approved changes are applied directly to Kanka API + logged as an audit batch

3. **Revert** (`revert.py`) finds the most recent unreverted batch → reverses relation operations in reverse order (create→delete, update→restore, delete→recreate) → restores synopsis text to pre-batch state → deletes newly created entities → marks batch as reverted

## Key Constraints & Gotchas

- **Idempotency**: processed journal IDs prevent duplicate proposals on re-run
- **Memory index carry-forward**: when multiple journals mention the same entity in one sync run, each proposal's `proposed_entry` is carried forward so subsequent journals see the latest draft (not the stale Kanka copy)
- **In-fiction date sorting**: journals are sorted by their in-fiction date, not creation timestamp
- **Nothing auto-publishes**: pipeline only writes proposals; human review gates all Kanka API writes
- **Revert limitation**: only one-step undo of the most recent unreverted batch; reverted batches leave journals marked "processed"
- **Rate limits**: default 1 request / 2.1s (`KANKA_REQUEST_INTERVAL`); adjust via env var
