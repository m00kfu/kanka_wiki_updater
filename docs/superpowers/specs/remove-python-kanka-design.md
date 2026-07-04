# Remove python-kanka — Design Spec

## Problem

The project vendors `python-kanka` (8 files, ~700 lines) as an editable install (`-e vendor/python-kanka`). This adds unnecessary complexity: the vendored library depends on `pydantic==2.13.4` and `requests-toolbelt==1.0.0`, introduces a second requests instance, and uses camelCase field names that Kanka's API rejects (requiring workarounds in relation methods).

## Goal

Replace all python-kanka usage with direct `requests` calls. Delete the vendored library entirely. Keep the outer `KankaClient` public API unchanged so no consumer code needs modification.

## Design

### kanka_client.py changes

**Before:** Wraps a single `kanka.KankaClient` instance, delegates to its entity managers (which wrap the same session internally).

**After:** Manages one `requests.Session` directly. Provides a private `_request()` method that replicates python-kanka's retry/rate-limit logic (~60 lines extracted from `vendor/python-kanka/src/kanka/client.py`).

#### New `_request(method, endpoint, **kwargs)` method
- Auth header: `Authorization: Bearer {token}`
- URL template: `{BASE_URL}/campaigns/{CAMPAIGN_ID}/{endpoint}`
- Retry on 429 with exponential backoff (1s → 15s max, up to 8 attempts)
- Parse `Retry-After` and `X-RateLimit-*` headers
- Raise `KankaError` for non-2xx responses
- Return parsed JSON response dict

#### Methods converted to raw HTTP

| Method | HTTP | Endpoint/Body |
|---|---|---|
| `get_journals(since, journal_type)` | GET | `/journals?lastSync={since}&type={journal_type}` → return `resp['data']` |
| `get_characters()` | GET | `/characters?related=1` → return `resp['data']` |
| `get_locations()` | GET | `/locations?related=1` → return `resp['data']` |
| `update_entity_entry(kind, id, text)` | PATCH | `/{kind}/{id}` with `{'entry': html}` |
| `create_character(name, entry, **extra)` | POST | `/characters` with body → shape response as dict |
| `create_location(name, entry, **extra)` | POST | `/locations` with body → shape response as dict |
| `delete_character(id)` | DELETE | `/characters/{id}` → return True |
| `delete_location(id)` | DELETE | `/locations/{id}` → return True |

Relation methods (`get_relations`, `create_relation`, `update_relation`, `delete_relation`) already use direct `_request()` calls — no changes needed.

### Files deleted
- `vendor/python-kanka/` (entire directory, 8 files)

### Files modified
- **requirements.txt** — remove `-e vendor/python-kanka`; add explicit `requests>=2.31`
- **pyproject.toml** — keep existing `exclude = ["vendor/**"]` (no change needed)
- **tests/test_kanka_client.py** — update mocks: replace `import kanka` mocking with direct session/response mocking

### Files unchanged (verified via GitNexus impact analysis)
- `review.py`, `sync_pipeline.py`, `revert.py`, `review_web.py` — all interact only with the outer `KankaClient` public API, which is unchanged in signature and return types.

## Risk Assessment
- **Medium** — 8 methods change implementation but not signatures or return shapes. Existing callers are unaffected.
- Primary risk: response shape mismatches if Kanka returns unexpected dict structures. Mitigated by keeping the same response extraction patterns used by python-kanka's managers.
