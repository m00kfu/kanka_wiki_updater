# Plan: Refactor Directory Structure

> **Date:** 2026-07-24
> **Status:** ✅ Complete
> **Goal:** Group the flat package into logical subpackages that reflect existing architectural boundaries.

## Why

The package has 20+ modules at one level with no filesystem-level separation between layers. The architecture doc already documents clear boundaries (core library → LLM layer → sync pipeline → review/CLI), but the filesystem doesn't reflect them. This makes navigation, testing, and future growth harder than necessary.

## Target Structure

```
kanka_wiki_updater/
├── kanka_wiki_updater/              ← main package
│   ├── __init__.py
│   │
│   ├── core/                        ← library code (importable, no web/CLI deps)
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── kanka_client.py          ← HTTP wrapper around Kanka API v1
│   │   ├── mentions.py              ← entity resolution utilities
│   │   ├── prompts.py               ← prompt templates (constants)
│   │   ├── progress.py              ← CLI Unicode progress bar
│   │   └── state.py                 ← plain JSON file helpers
│   │
│   ├── llm/                         ← LLM integration layer
│   │   ├── __init__.py
│   │   ├── providers.py             ← lmstudio, gemini, opencode implementations
│   │   └── client.py                ← re-export + dispatcher (chat_json)
│   │
│   ├── sync/                        ← sync pipeline (orchestration + apply)
│   │   ├── __init__.py
│   │   ├── ingest_journal.py        ← shared ingest core
│   │   ├── synopsis_generator.py    ← LLM-driven proposal building
│   │   ├── sync_engine.py           ← apply proposals to Kanka API
│   │   ├── sync_orchestrator.py     ← background job lifecycle (threading)
│   │   ├── sync_events.py           ← event type constants
│   │   └── relation_conflicts.py    ← conflict detection/resolution
│   │
│   ├── review/                      ← review layer (human-in-the-loop)
│   │   ├── __init__.py
│   │   ├── queue_manager.py         ← pending_changes.json I/O + in-memory helpers
│   │   └── web/                     ← Flask app sub-package
│   │       ├── __init__.py          ← app factory (was review_web.py)
│   │       ├── static/              ← CSS, JS for the web UI
│   │       └── templates/           ← HTML templates
│   │
│   └── cli/                         ← CLI entry points (thin wrappers)
│       ├── __init__.py
│       ├── revert.py                ← one-step undo of most recent batch
│       └── reset_to_first.py        ← nuclear undo to first recorded state
│
├── tests/                           ← mirrors source structure
│   ├── test_core/
│   │   ├── conftest.py              ← shared fixtures (move from root)
│   │   ├── test_config.py
│   │   ├── test_kanka_client.py
│   │   ├── test_mentions.py
│   │   └── test_state.py
│   ├── test_llm/
│   │   ├── conftest.py
│   │   └── test_providers.py
│   ├── test_sync/
│   │   ├── conftest.py
│   │   ├── test_ingest_journal.py
│   │   ├── test_synopsis_generator.py
│   │   ├── test_sync_engine.py
│   │   ├── test_sync_orchestrator.py
│   │   └── test_relation_conflicts.py
│   ├── test_review/
│   │   ├── conftest.py
│   │   ├── test_queue_manager.py
│   │   └── test_web.py              ← Flask app tests (was test_review_web)
│   └── test_cli/
│       ├── conftest.py
│       ├── test_revert.py
│       └── test_reset_to_first.py
│
├── docs/                            ← single source of truth for docs
│   ├── plans/                       ← active plans (date-prefixed)
│   ├── brainstorms/                 ← brainstorming notes
│   └── architecture.md              ← keep this, it's good
│
├── pyproject.toml                   ← add [project.scripts] entry points
├── requirements.txt
├── .env.example
└── README.md                        ← update run instructions
```

## Migration Steps

### Phase 0: Cleanup (no behavior change)

1. **Remove orphaned directories** — `static/` and `templates/` at the package root appear unused (assets live under `review_web/static/` and `review_web/templates/`). Archive if needed, then delete.
2. **Consolidate docs** — merge `.docs/plans/` into `docs/plans/`. Decide whether `docs/superpowers/` is still relevant; archive or remove.
3. **Verify tests pass** before touching anything:
   ```bash
   python -m pytest tests/ -v
   ```

### Phase 1: Create subpackages (no behavior change)

Create each subpackage directory with an empty `__init__.py`:

```bash
mkdir -p kanka_wiki_updater/{core,llm,sync,review/web,cli}
mkdir -p tests/test_{core,llm,sync,review,cli}
```

### Phase 2: Move files into logical groups

Move each file to its new location. The mapping is straightforward — see the target structure above. Key decisions:

| Old Path | New Path | Rationale |
|---|---|---|
| `config.py` | `core/config.py` | Foundation layer, no deps on anything else |
| `kanka_client.py` | `core/kanka_client.py` | HTTP wrapper — core dependency of everything |
| `mentions.py` | `core/mentions.py` | Pure utility, used by many modules |
| `prompts.py` | `core/prompts.py` | Constants only |
| `progress.py` | `core/progress.py` | CLI-only utility, but no other home |
| `state.py` | `core/state.py` | Plain JSON I/O helpers |
| `llm_providers.py` | `llm/providers.py` | LLM implementations |
| `llm_client.py` | `llm/client.py` | Re-export + dispatcher (thin wrapper) |
| `ingest_journal.py` | `sync/ingest_journal.py` | Shared ingest core |
| `synopsis_generator.py` | `sync/synopsis_generator.py` | LLM-driven proposal building |
| `sync_engine.py` | `sync/sync_engine.py` | Apply proposals to Kanka |
| `sync_orchestrator.py` | `sync/sync_orchestrator.py` | Background job lifecycle |
| `sync_events.py` | `sync/sync_events.py` | Event type constants |
| `relation_conflicts.py` | `sync/relation_conflicts.py` | Conflict detection (sync-specific) |
| `queue_manager.py` | `review/queue_manager.py` | Queue I/O — belongs to review layer |
| `review_web.py` + assets | `review/web/__init__.py` + subdirs | Flask app becomes a proper sub-package |
| `revert.py` | `cli/revert.py` | CLI entry point |
| `reset_to_first.py` | `cli/reset_to_first.py` | CLI entry point |

### Phase 3: Update all imports

Every file that uses relative or absolute imports needs updating. The pattern is mechanical:

- `from config import ...` → `from kanka_wiki_updater.core.config import ...`
- `from kanka_client import ...` → `from kanka_wiki_updater.core.kanka_client import ...`
- `import llm_providers` → `from kanka_wiki_updater.llm import providers as llm_providers`
- Files within the same subpackage can keep relative imports (`from .mentions import ...`)

**Import dependency map (what needs updating):**

```
core/       — no internal imports to update (only stdlib + third-party)
llm/        — config → core.config
sync/       — config, kanka_client, mentions, prompts, llm_providers, llm_client → core.*, llm.*
review/     — state, config, synopsis_generator, mentions, kanka_client, sync_engine, sync_orchestrator → core.*, sync.*, review.queue_manager
cli/        — kanka_client, config, queue_manager, sync_engine → core.*, review.*, cli.*
```

### Phase 4: Mirror tests

Move each test file to match the new source structure. Rename where needed:

| Old Test | New Path | Notes |
|---|---|---|
| `test_config.py` | `test_core/test_config.py` | Direct move |
| `test_kanka_client.py` | `test_core/test_kanka_client.py` | Direct move |
| `test_mentions.py` | `test_core/test_mentions.py` | Direct move |
| `test_state.py` | `test_core/test_state.py` | Direct move |
| `test_colors.py` | `test_core/test_colors.py` | colors.py → core/ (or delete if unused) |
| `test_llm_providers.py` | `test_llm/test_providers.py` | Rename to match module rename |
| `test_sync_orchestrator.py` | `test_sync/test_sync_orchestrator.py` | Direct move |
| `test_sync_pipeline.py` + `test_sync_pipeline_main.py` | `test_sync/test_ingest_journal.py` | Both test ingest logic — consider merging |
| `test_review.py` + `test_review_main.py` | `test_review/` | Split: unit tests → `test_queue_manager.py`, Flask tests → `test_web.py` |
| `test_review_web.py` | `test_review/test_web.py` | Direct move (Flask app) |
| `test_progress.py` | `test_core/test_progress.py` | Direct move |
| `test_relation_conflicts.py` | `test_sync/test_relation_conflicts.py` | Direct move |
| `test_revert.py` | `test_cli/test_revert.py` | Direct move |
| `test_sse_event_schema.py` | `test_sync/test_sync_events.py` | Tests sync event types — rename to match |
| `conftest.py` | `test_core/conftest.py` + others | Split shared fixtures per subpackage |

### Phase 5: Update pyproject.toml entry points

Add `[project.scripts]` so CLI commands work as proper package commands:

```toml
[project.scripts]
kanka-wiki-updater-review = "kanka_wiki_updater.review.web:create_app"
kanka-wiki-updater-revert = "kanka_wiki_updater.cli.revert:main"
kanka-wiki-updater-reset  = "kanka_wiki_updater.cli.reset_to_first:main"
```

Or use `python -m` style in README instead (simpler, no scripts table needed):

```bash
python -m kanka_wiki_updater.review.web    # Flask dev server
python -m kanka_wiki_updater.cli.revert     # One-step undo
python -m kanka_wiki_updater.cli.reset_to_first  # Nuclear undo
```

### Phase 6: Update README.md

Change the run instructions from:

```bash
python -m kanka_wiki_updater.review_web
python -m kanka_wiki_updater.revert
python -m kanka_wiki_updater.reset_to_first
```

To:

```bash
python -m kanka_wiki_updater.review.web
python -m kanka_wiki_updater.cli.revert
python -m kanka_wiki_updater.cli.reset_to_first
```

### Phase 7: Verify everything works

```bash
# Run all tests
python -m pytest tests/ -v

# Test each CLI entry point
python -m kanka_wiki_updater.review.web --help   # or just start it
python -m kanka_wiki_updater.cli.revert --help
python -m kanka_wiki_updater.cli.reset_to_first --help

# Verify imports work from a fresh Python session
python -c "from kanka_wiki_updater.core.config import *; print('OK')"
```

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Circular imports after moving files | Review dependency graph before moving; `core/` must have no deps on other subpackages |
| Broken relative imports within same package | Use absolute imports consistently after migration; test with `python -m pytest` |
| Flask static/template path resolution breaks when moved under `review/web/` | Use `pkg_resources` or `importlib.resources` for paths, or pass explicit `static_folder`/`template_folder` to Flask constructor |
| Tests import from source modules directly (not via installed package) | This already works with pytest; no change needed if running from project root |

## What Stays the Same

- **Data directory** (`~/.local/share/kanka_wiki_updater/`) — unchanged, controlled by `DATA_DIR` env var
- **`.env` variables** — all existing env vars work identically
- **External API contracts** — Kanka API calls, LLM provider interfaces unchanged
- **Pending changes format** (`pending_changes.json`) — schema unchanged
