# Kanka Wiki Updater — Agent Instructions

## Running the project

```bash
pip install -r requirements.txt        # once: requests, python-dotenv, json_repair, colorama
python -m kanka_wiki_updater.sync_pipeline [--limit N]   # fetch new session journals → LLM proposals (no writes to Kanka)
python -m kanka_wiki_updater.review                           # human review of pending proposals; approved changes go live immediately
python -m kanka_wiki_updater.revert                           # undo the most recent unreverted review batch
```

- Run **from the parent directory** containing `kanka_wiki_updater/` (the module entry point expects relative imports).
- `.env` is required: copy `.env.example`, fill in `KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`, `LMSTUDIO_MODEL`.
- No linter, type checker, or test suite exists — changes are manual verification only.

## Architecture at a glance

| File | Purpose |
|---|---|
| `config.py` | Loads `.env` settings; required vars: `KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`. Defaults for LM Studio, rate limits, batch size. |
| `kanka_client.py` | Thin HTTP wrapper around Kanka API v1 (characters, locations, relations, journal entries). Throttles requests to ~1/2s. |
| `mentions.py` | Entity resolution: parses `[entity:N]` wiki links + fuzzy name-match fallback for plain-text mentions in session notes. |
| `llm_client.py` | Sends prompts to LM Studio's OpenAI-compatible `/v1/chat/completions`. Parses JSON output with `json_repair` fallback. |
| `prompts.py` | System/user prompt templates for synopsis updates and new-entity detection (strict JSON schema). |
| `sync_pipeline.py` | **Main orchestrator:** builds entity index → fetches journals since last sync → identifies mentioned entities → calls LLM per entity → queues proposals to `data/pending_changes.json`. Idempotent: tracks processed journal IDs so interrupted runs don't duplicate work. |
| `review.py` | Interactive CLI: new-entity suggestions first, then synopsis/relation diffs. Options: yes (apply all), no (reject), approve-synopsis-only (skip relations). Auto-links known entity names in proposed text before showing diff. Flags dropped mention links and unlinked plain-text mentions as warnings. |
| `revert.py` | Undoes the most recent unreverted review batch in reverse order: relation changes + synopsis edits first, then new-entity deletions last. Only one-step undo; older batches without structured logs can't be reverted automatically. |
| `state.py` | Plain JSON state files under `data/`: sync cursor, pending queue, applied-log (with run_id and revert flag), processed journal IDs. |

## Gotchas & constraints

- **Nothing auto-publishes.** The pipeline only writes proposals to `pending_changes.json`. Changes reach Kanka only through `review.py` after human approval.
- **Journal date sorting:** journals are sorted by in-fiction date (`date` field or calendar fields), not creation timestamp, so synopses build chronologically even if sessions were logged out of order.
- **In-memory carry-forward:** when multiple journals mention the same entity in one sync run, each proposal's `proposed_entry` is carried forward into the memory index so subsequent journals see the latest draft (not the stale Kanka copy). The real Kanka is untouched until review.
- **Entity creation during review:** approving a new-entity suggestion makes that entity immediately available as a relation target for update proposals reviewed later in the same session. If `review.py` can't read back an `entity_id` from Kanka's response, it won't resolve as a relation target this run — raw response is printed.
- **Relation ID quirks:** Kanka's API doesn't always return an `id` per relation in list responses. If create/update/delete relations misbehave, print a raw relation object and adjust the code accordingly (see README notes).
- **Rate limits:** default 1 request every 2.1 s (`KANKA_REQUEST_INTERVAL`). Subscribers can lower this; upgraders get ~90/min.
- **Revert limitations:** reverted batches leave journals marked as "processed" — re-running `sync_pipeline` won't regenerate those proposals unless you remove the journal IDs from `data/processed_journals.json`. Only the most recent unreverted batch is undoable; pre-revert-tool runs lack sufficient detail.

## Prompt engineering notes

- The LLM system prompt enforces strict JSON output (no markdown fences, escaped quotes/backslashes). If parsing fails frequently: switch to a model with better structured-output capability or lower `temperature` in `llm_client.py`.
- For reasoning/"thinking" models: increase `LLM_MAX_TOKENS` and `LLM_TIMEOUT_SECONDS` — hidden chain-of-thought consumes tokens and time.
