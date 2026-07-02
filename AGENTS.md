# Kanka Wiki Updater — Agent Instructions

## Running the project

```bash
pip install -r requirements.txt        # once: requests, python-dotenv, json_repair, colorama, ruff, pytest, flask
python -m kanka_wiki_updater.sync_pipeline [--limit N]   # fetch new session journals → LLM proposals (no writes to Kanka)
python -m kanka_wiki_updater.review                           # human review of pending proposals; approved changes go live immediately
python -m kanka_wiki_updater.review_web                       # web-based review UI at http://127.0.0.1:5555
python -m kanka_wiki_updater.revert                           # undo the most recent unreverted review batch
```

- Run **from the parent directory** containing `kanka_wiki_updater/` (the module entry point expects relative imports).
- `.env` is required: copy `.env.example`, fill in `KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`, `LMSTUDIO_MODEL`.
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
pytest tests/test_mentions.py   # single file
pytest --cov=kanka_wiki_updater # coverage report
```

Tests go in `tests/` alongside the source. Pure functions (mentions, state) are tested first — no mocking needed. I/O-heavy modules (kanka_client, sync_pipeline) use pytest-mock to isolate external dependencies. Test files: `test_mentions.py`, `test_state.py`, `test_sync_pipeline.py`, `test_sync_pipeline_main.py`, `test_review.py`, `test_review_main.py`, `test_llm_providers.py`, `test_progress.py`, `test_kanka_client.py`, `test_config.py`, `test_colors.py`, `test_revert.py`, `test_review_web.py`.

## Architecture at a glance

| File | Purpose |
|---|---|
| `config.py` | Loads `.env` settings; required vars: `KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`. Defaults for LM Studio, rate limits, batch size. |
| `kanka_client.py` | Thin HTTP wrapper around Kanka API v1 (characters, locations, relations, journal entries). Throttles requests to ~1/2s. |
| `mentions.py` | Entity resolution: parses `[entity:N]` wiki links + fuzzy name-match fallback for plain-text mentions in session notes. |
| `progress.py` | In-memory progress tracker with Unicode progress bar; uses `\r` carriage return for in-place terminal updates (disabled on Windows). |
| `llm_client.py` | Sends prompts to LM Studio's OpenAI-compatible `/v1/chat/completions`. Parses JSON output with `json_repair` fallback. |
| `llm_providers.py` | Provider implementations for LM Studio and Google Gemini. HTTP logic lives here; `llm_client.py` re-exports `chat_json()`. |
| `prompts.py` | System/user prompt templates for synopsis updates and new-entity detection (strict JSON schema). |
| `sync_pipeline.py` | **Main orchestrator:** builds entity index → fetches journals since last sync → identifies mentioned entities → calls LLM per entity → queues proposals to `data/pending_changes.json`. Idempotent: tracks processed journal IDs so interrupted runs don't duplicate work. |
| `review.py` | Interactive CLI: new-entity suggestions first, then synopsis/relation diffs. Options: yes (apply all), no (reject), approve-synopsis-only (skip relations). Auto-links known entity names in proposed text before showing diff. Flags dropped mention links and unlinked plain-text mentions as warnings. |
| `review_web.py` | Flask web UI at http://127.0.0.1:5555 with tabbed interface — Review tab (browse, filter, edit proposals inline, manage relations via modals) and Sync tab (run sync pipeline from browser with SSE output streaming). Reads/writes `data/pending_changes.json`. |
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
- **Gemini provider:** set `LLM_PROVIDER=gemini` plus `GEMINI_API_KEY` and `GEMINI_MODEL` in `.env` to use Google Gemini instead of LM Studio. Defaults to `lmstudio`.

## Prompt engineering notes

- The LLM system prompt enforces strict JSON output (no markdown fences, escaped quotes/backslashes). If parsing fails frequently: switch to a model with better structured-output capability or lower `temperature` in `llm_client.py`.
- For reasoning/"thinking" models: increase `LLM_MAX_TOKENS` and `LLM_TIMEOUT_SECONDS` — hidden chain-of-thought consumes tokens and time. This applies to both LM Studio and Gemini providers.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **kanka_wiki_updater** (832 symbols, 1444 relationships, 44 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
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
