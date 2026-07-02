# Kanka Session-Note Sync

Reads new session-note journals from your Kanka campaign, asks a local LLM
(via LM Studio or Google Gemini) to propose updated character/location synopses and
relationship changes, and queues those proposals for you to review before
anything is published back to the wiki.

## How it works

1. `sync_pipeline.py` fetches journals updated since the last run, figures
   out which characters/locations each one mentions (via Kanka's
   `[entity:N]` link syntax, with a fuzzy name-match fallback for plain
   prose mentions), and asks the LLM to propose an updated synopsis +
   relationship changes for each one mentioned. Proposals are written to
   `data/pending_changes.json` — **nothing is sent to Kanka yet**.
2. `review.py` walks you through each pending proposal (a diff of the
   synopsis, plus any relationship changes) so you can approve, reject, or
   approve-the-synopsis-but-skip-the-relationships. New-entity suggestions
   (proper nouns mentioned in a session note that don't match any existing
   character/location) are reviewed first, so approving one makes it
   available as a relation target for proposals reviewed right after it.
   Approved changes are PATCHed/POSTed to Kanka immediately.
3. `revert.py` undoes everything `review.py` applied in its most recent
   run, in reverse order — restoring synopses, undoing relation
   create/update/delete actions, and deleting any newly-created entity
   (after first undoing anything that pointed at it).

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

From the directory *containing* this `kanka_wiki_updater` folder:

```bash
python -m kanka_wiki_updater.sync_pipeline   # pull notes, generate proposals (with progress bar)
python -m kanka_wiki_updater.review          # interactive CLI review and publish
python -m kanka_wiki_updater.review_web      # web-based review UI at http://127.0.0.1:5555
python -m kanka_wiki_updater.revert          # undo the most recent review run, if needed
```

Run `sync_pipeline` after each session. It only looks at journals updated
since the last run (tracked in `data/sync_state.json`), so it's safe to run
repeatedly without reprocessing old notes. The pipeline shows a Unicode
progress bar with journal names as it processes each one.

The web review UI (`review_web`) offers a tabbed interface with:
- **Review tab** — browse, filter (by status/type), edit proposals inline, manage relations via modal dialogs, and approve/reject
- **Sync tab** — run the sync pipeline from the browser with live SSE output streaming and cancel support

If a `review` run published something you didn't mean to (or you just want
to compare before/after), `revert` will show you exactly what that run
changed and ask for confirmation before undoing all of it. It only goes back
one run, and won't redo something already reverted — see the docstring at
the top of `revert.py` for the exact rules and limitations.

## Notes & things worth tuning

- **Session notes location**: this assumes session notes are Kanka
  **Journal** entities, which is the normal place for them (Kanka even has
  a built-in "Session" journal type). If yours live somewhere else (Posts
  on a "Sessions" entity, Notes, etc.), `get_journals` in `kanka_client.py`
  is the place to adapt.
- **Entity resolution**: works best if you use Kanka's `@mention` linking
  when writing session notes — that gives exact, unambiguous matches. The
  fuzzy fallback in `mentions.py` catches plain-text name mentions but is
  intentionally conservative; tune `threshold` there if it's over- or
  under-matching.
- **LLM provider**: defaults to LM Studio (local OpenAI-compatible server).
  Set `LMSTUDIO_MODEL` for LM Studio, or set `LLM_PROVIDER=gemini` plus
  `GEMINI_API_KEY` + `GEMINI_MODEL` in `.env` to use Google Gemini instead.
  Provider logic lives in `llm_providers.py`. If you see JSON parsing failures, try a model that's
  better at structured output, or lower `temperature` further in
  `llm_client.py`.
- **Relation IDs**: Kanka's documented example response for listing
  relations doesn't explicitly show an `id` field per relation (only
  owner/target/label/attitude). If `update`/`delete` relation actions
  misbehave in your testing, print a raw relation object from
  `client.get_relations(...)` and adjust `review.py` to whatever field
  Kanka actually returns for the relation's own ID.
- **Rate limits**: Kanka allows 30 requests/minute (90/minute for
  subscribers). The client throttles to ~1 request every 2.1 seconds by
  default — lower `KANKA_REQUEST_INTERVAL` in `.env` if you're a subscriber
  and want more speed.
- **Nothing auto-publishes.** If you eventually trust it enough to skip the
  review step, you could wire `propose_update`'s output straight into the
  apply logic in `review.py` — but I'd watch it across a few real sessions
  first, since LLMs will occasionally invent a connection that "sounds
  right" but isn't actually in the notes.
