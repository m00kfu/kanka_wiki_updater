# Kanka Wiki Updater

Reads new session-note journals from your Kanka campaign, asks a local LLM
(via LM Studio, Google Gemini, or OpenCode Zen) to propose updated character/location synopses and
relationship changes, and queues those proposals for you to review before
anything is published back to the wiki.

## How it works

1. `kanka_wiki_updater/sync/ingest_journal.py` fetches journals updated since the last run, figures
   out which characters/locations each one mentions (via Kanka's
   `[entity:N]` link syntax, with a fuzzy name-match fallback for plain
   prose mentions), and asks the LLM to propose an updated synopsis +
   relationship changes for each one mentioned. Proposals are written to
   `pending_changes.json` in the data directory (default:
   `~/.local/share/kanka_wiki_updater/`, configurable via `DATA_DIR`) —
   **nothing is sent to Kanka yet**.
2. `kanka_wiki_updater/review/web/` serves the review UI (template + static assets under `review/web/templates/` and `review/web/static/`) where you can
   approve, reject, or edit them inline. New-entity suggestions
   (proper nouns mentioned in a session note that don't match any existing
   character/location) are reviewed first, so approving one makes it
   available as a relation target for proposals reviewed right after it.
   Approved changes are PATCHed/POSTed to Kanka immediately.
3. `kanka_wiki_updater/cli/revert.py` undoes everything applied to Kanka in its most recent
   review run, in reverse order — restoring synopses, undoing relation
   create/update/delete actions, and deleting any newly-created entity
   (after first undoing anything that pointed at it).
4. `kanka_wiki_updater/cli/reset_to_first.py` performs a nuclear undo: resets all entities back to their
   earliest recorded `previous_entry` from the pending queue.

## Setup

1. **Get a Kanka API token**: Kanka profile → API → Create New Token.
   Tokens last ~364 days.
2. **Get your campaign ID**: visible in the campaign's URL,
   `kanka.io/w/<campaign_id>/...`.
3. **In LM Studio**: load a model and start the local server
   (Developer tab → Start Server). Note the port (default `1234`) and the
   model identifier shown in LM Studio's server log — that's your
   `LMSTUDIO_MODEL`.
4. Copy `.env.example` to `.env` and fill in `KANKA_TOKEN`,
   `KANKA_CAMPAIGN_ID`, and `LMSTUDIO_MODEL`.
5. `pip install -r requirements.txt`

## Running

From the project root:

```bash
python -m kanka_wiki_updater.review.web       # web-based review UI at http://127.0.0.1:5555 (includes sync trigger)
python -m kanka_wiki_updater.cli revert       # undo the most recent review run, if needed
python -m kanka_wiki_updater.cli reset        # nuclear undo to first recorded state (--dry-run)
```

Or install the package and use the CLI entry point:

```bash
pip install -e .
kanka-wiki-updater revert
kanka-wiki-updater reset
```

Run `review.web` after each session — click **Sync** in the Sync tab to fetch new journals,
generate proposals, and queue them for review. It only looks at journals updated since the last run
(tracked in `sync_state.json` in the data directory), so it's safe to run repeatedly without
reprocessing old notes.

The web review UI (`review_web`) offers a tabbed interface with:
- **Review tab** — browse, filter (by status/type), edit proposals inline, and approve/reject
- **Sync tab** — run the sync pipeline from the browser with live SSE output streaming and cancel support

If a review run published something you didn't mean to (or you just want
to compare before/after), `revert` will show you exactly what that run
changed and ask for confirmation before undoing all of it. It only goes back
one run, and won't redo something already reverted — see the docstring at
the top of `revert.py` for the exact rules and limitations.

The web review UI includes a **regenerate** button on truncated or uncertain
proposals that re-runs them through the LLM with double the token budget,
giving the model a second chance to produce a complete response.

## Notes & things worth tuning

- **Session notes location**: this assumes session notes are Kanka
  **Journal** entities, which is the normal place for them (Kanka even has
  a built-in "Session" journal type). If yours live somewhere else (Posts
  on a "Sessions" entity, Notes, etc.), `get_journals` in `core/kanka_client.py`
  is the place to adapt.
- **Entity resolution**: works best if you use Kanka's `@mention` linking
  when writing session notes — that gives exact, unambiguous matches. The
  fuzzy fallback in `core/mentions.py` catches plain-text name mentions but is
  intentionally conservative; tune `threshold` there if it's over- or
  under-matching.
- **LLM provider**: defaults to LM Studio (local OpenAI-compatible server).
  Set `LMSTUDIO_MODEL` for LM Studio, set `LLM_PROVIDER=gemini` plus
  `GEMINI_API_KEY` + `GEMINI_MODEL` in `.env` to use Google Gemini, or set
  `LLM_PROVIDER=opencode` plus `OPENCODE_API_KEY` (+ optional `OPENCODE_MODEL`) for OpenCode Zen.
  Provider logic lives in `llm/providers.py`. If you see JSON parsing failures, try a model that's
  better at structured output, or lower `temperature`. The web UI also supports regenerating truncated proposals (click the regenerate button to re-run through the LLM with 2× token budget).
- **Sync cancellation**: during a sync run from the web UI, click "Cancel" to stop processing
  further journals. In-flight LLM calls will complete but subsequent journals won't start.
- **Relation types**: the project ships with a built-in list of common
  relation labels (`sync/relation_types.py`). On first run it seeds these
  from defaults; approved new types are persisted in
  `data/known_relation_types.json` and used for fuzzy matching when the LLM
  proposes an unfamiliar label. The review UI highlights unknown types with
  suggestions.
- **Starting attitudes**: `sync/default_attitudes.py` provides suggested
  starting attitude values (−80 to +85) per relation type, which guide the
  LLM when creating brand-new relations. You can edit this file to adjust
  baselines for your campaign.
- **Rate limits**: Kanka allows 30 requests/minute (90/minute for
  subscribers). The client throttles to ~1 request every 2.1 seconds by
  default — lower `KANKA_REQUEST_INTERVAL` in `.env` if you're a subscriber
  and want more speed.
- **Nothing auto-publishes.** If you eventually trust it enough to skip the
  review step, you could wire `propose_update`'s output straight into the
  apply logic in `sync_engine.apply_proposal()` — but I'd watch it across a few real sessions
  first, since LLMs will occasionally invent a connection that "sounds
  right" but isn't actually in the notes.
