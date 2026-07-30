# Kanka Wiki Updater — Agent Instructions

## Running the project

```bash
pip install -r requirements.txt        # once: requests, python-dotenv, json_repair, colorama, ruff, pytest, flask
python -m kanka_wiki_updater.review.web                   # web-based review UI at http://127.0.0.1:5555 (includes sync trigger)
python -m kanka_wiki_updater.cli revert                   # undo the most recent unreverted review batch
python -m kanka_wiki_updater.cli reset [--dry-run]        # nuclear undo to first recorded state
```

Or install as a package and use the entry point:
```bash
pip install -e .
kanka-wiki-updater revert
kanka-wiki-updater reset
```

- Run **from the project root** (the directory containing `pyproject.toml`).
- `.env` is required: copy `.env.example`, fill in `KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`, and at least one LLM provider config.
- Changes are verified by `ruff check` + manual testing.

## Linting & formatting

```bash
ruff check .          # lint all modules
ruff format .         # auto-format (run after editing)
ruff check . --fix    # auto-fix fixable issues
```

Configuration is in `pyproject.toml`. Line length: 120 chars.

## Testing

```bash
pytest                          # run all tests
pytest -v                       # verbose output
pytest tests/test_core/test_mentions.py   # single test file
pytest --cov=kanka_wiki_updater           # coverage report
```

Tests live in `tests/` mirroring the package structure:
- `test_core/` — config, kanka_client, mentions, progress, state, colors (pure functions tested first)
- `test_llm/` — provider implementations
- `test_sync/` — ingest callbacks, sync orchestrator, sync pipeline, relation conflicts, sync events, attitude helpers, relation types & parsing, reciprocals
- `test_review/` — queue manager, review web routes
- `test_cli/` — revert command

I/O-heavy modules (kanka_client, sync_engine, ingest_journal) use pytest-mock to isolate external dependencies.

## Architecture at a glance

### Package layout
```
kanka_wiki_updater/
├── cli/                    # CLI entry points (revert, reset)
│   ├── __main__.py         # argparse dispatcher → subcommand handlers
│   ├── revert.py           # undo last applied batch
│   └── reset_to_first.py   # nuclear undo to first journal state
├── core/                   # Core business logic
│   ├── config.py           # .env loading; required: KANKA_TOKEN, KANKA_CAMPAIGN_ID
│   ├── kanka_client.py     # HTTP wrapper around Kanka API v1 (throttled ~1 req/2.1s)
│   ├── mentions.py         # Entity resolution: [entity:N] links + fuzzy name matching
│   ├── progress.py         # In-memory progress tracker with Unicode bar
│   ├── prompts.py          # System/user prompt templates for LLM calls
│   ├── state.py            # JSON state files under data/: sync cursor, pending queue, applied-log
│   └── colors.py           # ANSI color helpers for CLI output
├── llm/                    # LLM provider abstraction
│   ├── client.py           # Sends prompts to OpenAI-compatible /v1/chat/completions
│   └── providers.py        # LM Studio, Google Gemini, and OpenCode Zen implementations
├── sync/                   # Sync pipeline
│   ├── ingest_journal.py   # [backend only — never run directly] fetch journals → identify entities → LLM per entity → queue proposals
│   ├── sync_engine.py      # Apply proposals to Kanka: create/update/delete entities & relations
│   ├── sync_orchestrator.py# Job lifecycle: start/cancel/status with per-entity progress
│   ├── synopsis_generator.py # LLM-driven synopsis generation (used by ingest + review_web)
│   ├── sync_events.py      # Event type constants for pipeline contract
│   ├── relation_conflicts.py    # Detect/resolve conflicts in proposed relations
│   ├── default_attitudes.py  # Starting attitude values per relation type (guideline for LLM prompts)
│   └── relation_types.py       # Track known relation types, fuzzy matching, symmetric/inverse mappings
├── review/                 # Review & queue management
│   ├── queue_manager.py    # Load/save pending_changes.json, edit proposal text/status/relation changes
│   └── web/                # Flask web UI
│       ├── __main__.py     # Entry point: python -m kanka_wiki_updater.review.web
│       └── __init__.py     # App factory, SSE streaming, API routes
└── tests/                  # pytest suite (see below)
```

### Key modules

| Module | Purpose |
|---|---|
| `core/config.py` | Loads `.env`; required: `KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`. Defaults for LLM, rate limits, batch size. |
| `core/kanka_client.py` | Thin HTTP wrapper around Kanka API v1 (characters, locations, relations, journals). Throttled requests. |
| `core/mentions.py` | Entity resolution: parses `[entity:N]` wiki links + fuzzy name-match fallback for plain-text mentions. |
| `llm/client.py` | Sends prompts to OpenAI-compatible `/v1/chat/completions`. Parses JSON with `json_repair` fallback. |
| `llm/providers.py` | Provider implementations: LM Studio, Google Gemini, OpenCode Zen. Re-exports `chat_json()`. |
| `sync/ingest_journal.py` | **Ingest core:** entity index → fetch journals → identify entities → LLM per entity → queue proposals. Idempotent via processed-journal tracking. |
| `sync/sync_engine.py` | Apply proposals to Kanka: wiki-link parsing, exact/fuzzy matching, new entity creation, synopsis updates, relation CRUD with reverse-direction conflict detection. |
| `sync/sync_orchestrator.py` | Job lifecycle management: start/cancel/status APIs with per-entity progress tracking. Used by review_web Sync tab. |
| `sync/synopsis_generator.py`      | LLM-driven synopsis generation: prompt construction, response parsing, paragraph normalisation, journal-tag dedup/italics. Exports `build_synopsis_proposal()`, `propose_update()`, `build_entity_index()`.
| `sync/relation_conflicts.py`    | Detect/resolve conflicts in proposed relations |
| `sync/default_attitudes.py`     | Starting attitude values per relation type (guideline for LLM prompts) |
| `sync/relation_types.py`        | Track known relation types, fuzzy matching, symmetric/inverse mappings |
| `review/queue_manager.py` | Queue I/O and in-memory manipulation: load/save `pending_changes.json`, edit proposal text/status/relation changes. |
| `review/web/__init__.py` | Flask app factory at http://127.0.0.1:5555 — Review tab (browse, filter, edit, approve/reject, regenerate) and Sync tab (SSE streaming, per-entity progress, cancel). |
| `cli/revert.py` | Undoes the most recent unreverted batch in reverse order: relations + synopses first, new-entity deletions last. One-step undo only. |
| `cli/reset_to_first.py` | Nuclear undo: resets all entities to their earliest recorded `previous_entry`. |
| `core/state.py` | Plain JSON state files under `data/`: sync cursor, pending queue, applied-log (with run_id/revert flag), processed journal IDs. |

## Gotchas & constraints

- **Nothing auto-publishes.** The pipeline only writes proposals to `pending_changes.json`. Changes reach Kanka only through the web UI (`review/web/__init__.py`) or `sync_engine.apply_proposal()` after human approval.
- **Journal date sorting:** journals are sorted by in-fiction date (`date` field or calendar fields), not creation timestamp, so synopses build chronologically even if sessions were logged out of order.
- **In-memory carry-forward:** when multiple journals mention the same entity in one sync run, each proposal's `proposed_entry` is carried forward into the memory index so subsequent journals see the latest draft (not the stale Kanka copy). The real Kanka is untouched until review.
- **Entity creation during review:** approving a new-entity suggestion makes that entity immediately available as a relation target for update proposals reviewed later in the same session. If `sync_engine` can't read back an `entity_id` from Kanka's response, it won't resolve as a relation target this run — raw response is printed.
- **Relation ID quirks:** Kanka's API doesn't always return an `id` per relation in list responses. If create/update/delete relations misbehave, print a raw relation object and adjust the code accordingly (see README notes).
- **Rate limits:** default 1 request every 2.1 s (`KANKA_REQUEST_INTERVAL`). Subscribers can lower this; upgraders get ~90/min.
- **Revert limitations:** reverted batches leave journals marked as "processed" — re-running `ingest_journal` won't regenerate those proposals unless you remove the journal IDs from `data/processed_journals.json`. Only the most recent unreverted batch is undoable; pre-revert-tool runs lack sufficient detail.
- **LLM providers:** defaults to LM Studio (local OpenAI-compatible server). Set `LMSTUDIO_MODEL` for LM Studio, set `LLM_PROVIDER=gemini` plus `GEMINI_API_KEY` + `GEMINI_MODEL` in `.env` to use Google Gemini, or set `LLM_PROVIDER=opencode` plus `OPENCODE_API_KEY` (+ optional `OPENCODE_MODEL`) for OpenCode Zen. Provider logic lives in `llm/providers.py`. If you see JSON parsing failures, try a model that's better at structured output, or lower `temperature`.
- **Batch limit:** `JOURNAL_BATCH_LIMIT` controls how many journals are processed per sync run. Defaults to 1 when multiple journal types are configured; unset for unlimited.

## External references

- **Kanka API v1 docs:** https://app.kanka.io/api-docs/1.0/overview — the canonical reference for all Kanka endpoint parameters, response shapes, and rate limits. Refer to this when updating `core/kanka_client.py` or debugging unexpected responses.

## Prompt engineering notes

- The LLM system prompt enforces strict JSON output (no markdown fences, escaped quotes/backslashes). If parsing fails frequently: switch to a model with better structured-output capability or lower `temperature`. All providers use the same format; just set the appropriate `LLM_PROVIDER` and API key.
- For reasoning/"thinking" models: increase `LLM_MAX_TOKENS` and `LLM_TIMEOUT_SECONDS` — hidden chain-of-thought consumes tokens and time. This applies to all providers.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **kanka_wiki_updater** (1721 symbols, 2806 relationships, 79 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "master"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/kanka_wiki_updater/context` | Codebase overview, check index freshness |
| `gitnexus://repo/kanka_wiki_updater/clusters` | All functional areas |
| `gitnexus://repo/kanka_wiki_updater/processes` | All execution flows |
| `gitnexus://repo/kanka_wiki_updater/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
