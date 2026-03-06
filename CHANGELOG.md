# Trip Master — Changelog

A record of significant changes to the app, newest first. Each entry covers what changed, why, and any migration notes.

---

## [Hotfix] Cache-Control headers — 2026-03-06

### Problem
After a Railway redeploy, browsers served stale cached JavaScript files even though the new HTML had deployed correctly. Symptoms: (1) `window.openGearProfileModal` was undefined — old `main.js` didn't have it — so clicking "+ New" on the gear profile selector did nothing; (2) form submission showed "Please select at least one Dining preference" — old `generate.js` still had dining validation that was removed in Phase 4.

### Root cause
FastAPI's `StaticFiles` sets no `Cache-Control` header by default, so browsers apply heuristic caching (often many hours). The new `index.html` was served fresh (dynamic route), but the old `/src/main.js`, `/src/generate.js`, and `/src/clients.js` were served from the browser cache.

### Fix
Added `NoCacheStaticFiles` — a `StaticFiles` subclass that overrides `get_response()` to add `Cache-Control: no-cache, must-revalidate` to every `/src/*` response. Also added the same header to the root `/` route (`FileResponse` for `index.html`). Browsers now always revalidate JS/CSS using ETags on page load and never serve stale files after a redeploy.

### Modified files
| File | Change |
|---|---|
| `app.py` | Added `NoCacheStaticFiles` class; replaced `StaticFiles` mount with it; added `Cache-Control` header to root `FileResponse` |

### Migration notes
- **Immediate fix for users already affected:** hard refresh — **Cmd+Shift+R** (Mac) or **Ctrl+Shift+F5** (Windows) — bypasses the browser cache and loads the correct JS immediately.
- No database or schema changes.

---

## [Phase 5] Test suite — 2026-03-05

### What changed
Full pytest test suite added. 59 tests across 5 modules, 0 failures.

### New files

| File | Description |
|---|---|
| `requirements.txt` | Added `pytest==8.3.5`, `pytest-asyncio==0.25.3`, `pytest-cov==6.0.0` |
| `pytest.ini` | `asyncio_mode=auto`, `asyncio_default_fixture_loop_scope=function`, `testpaths=tests` |
| `tests/__init__.py` | Empty package marker |
| `tests/conftest.py` | Shared fixtures: StaticPool SQLite, `override_get_db`, `test_user`, `anon_client`, `auth_client` |
| `tests/test_auth.py` | 8 tests: health, 401/403 guards, login success/fail/validation, CSRF |
| `tests/test_generate.py` | 10 tests: job_id response, date/duration validation, job polling, auth guards |
| `tests/test_ephemeris.py` | 19 tests: Barcelona solstice, London equinox, moon phase helpers, format_ephemeris_block |
| `tests/test_clients.py` | 12 tests: gear profile full CRUD cycle, cross-user isolation (404), invalid camera_type (422), auth guards |
| `tests/test_finalize.py` | 7 tests: session injection strategy, subset photo approval, HTML output validation, auth guards |

### Architecture notes
- **StaticPool** (SQLAlchemy) forces all test sessions to share one in-memory SQLite connection → data visible across `override_get_db` and `db_session` fixtures without commits being lost
- **No Redis** needed — app auto-falls back to in-memory `_session_store` / `_jobs` dicts
- **`PLACES_VERIFY_ENABLED = False`** (no `GOOGLE_PLACES_API_KEY`) — all Places API / map prefetch paths are bypassed
- **`app._http_client` stubbed** with `httpx.AsyncClient()` in `auth_client` fixture since `ASGITransport` does not invoke the FastAPI lifespan context manager
- **Background scout task** (`asyncio.create_task` in `/generate`) runs, fails without Claude API key, and marks the job `failed` — test only asserts the immediate `{job_id}` response

---

## [Phase 10] Frontend pivot — Photography UI — 2026-03-05

### What changed
Full frontend update to match the Phase 9 backend pivot. The form, review screen, and supporting modules are now photography-only. Dining and attractions are removed throughout. Gear profile management is fully wired up.

### Modified frontend files

| File | Key changes |
|---|---|
| `frontend/src/state.js` | Removed `restaurants`/`attractions` from `approvalState`, `sectionEnabled`, `countConfig`. Added `_gearProfiles: []` and `gear_profile_id: null`. |
| `frontend/src/form.js` | Progress animation: 2 steps only (`step-photos`, `step-building`). `resetForm()` strips all dining/attraction resets; clears `gearProfileSelect`. Removed `cuisine_other_text`/`attr_other_text`/`attr_other_text_wrapper` handling. |
| `frontend/src/generate.js` | Date validation replaces duration integer validation. Payload sends `start_date`/`end_date` and `gear_profile_id`; removes `include_dining`, `include_attractions`, `cuisines`, `restaurants_per_day`, `attractions_per_day`. |
| `frontend/src/review.js` | `showReviewScreen()` no longer initialises restaurants/attractions arrays. `buildReviewItem()` completely rewritten as a vertical Kelby card: row (toggle + name + shoot_window tag + distance tag + actions + Google Earth button + status dot) → gear badge row → 4 Kelby sections (Shot / Setup / Settings / Reality Check). `saveItemEdit()` / `replaceItem()` now photos-only. |
| `frontend/src/finalize.js` | Payload sends only `approved_photos`; removes `approved_restaurants` and `approved_attractions`. `displayResults()` subtitle shows `photo_count` only. |
| `frontend/src/trips.js` | `loadTrip()` no longer reads `raw_restaurants`/`raw_attractions` or sets `approvalState.restaurants`/`.attractions`. |
| `frontend/src/clients.js` | Added full gear profile CRUD: `refreshGearProfiles()` (GET /gear-profiles), `openGearProfileModal(id)`, `closeGearProfileModal()`, `saveGearProfile(e)` (POST or PUT), `deleteGearProfile(id)` (DELETE), `openGearPanel()`, `closeGearPanel()`. `_populateGearSelector()` fills `#gearProfileSelect`. `_renderGearProfileList()` renders gear rows in the side panel. |
| `frontend/src/main.js` | Added imports and `window.*` exports for all gear profile functions. Added `refreshGearProfiles()` call to `auth:success` listener. |
| `frontend/index.html` | Removed `#section-dining` and `#section-attractions` blocks. Replaced `#duration` integer input with `#startDate` / `#endDate` date pickers in a 3-column field row. Added gear profile selector (`#gearProfileSelect`) with "Manage Profiles" and "+ New" actions. Added gear profile CRUD modal (`#gearProfileModal`) and slide-in panel (`#gearPanel`). Removed `step-restaurants` and `step-attractions` from loading progress. Updated hero headline and dek to photography focus. |
| `frontend/src/styles/main.css` | New: `.review-item--photo` (vertical card layout), `.review-item-row` (toggle/name/tags/actions row), `.kelby-gear-row` (gear badges strip), `.gear-badge`, `.kelby-sections`, `.kelby-section`, `.kelby-label`, `.kelby-text`, `.review-earth-btn` (Google Earth link button), `.gear-panel-overlay`/`.gear-panel`/`.gear-panel-*` (slide-in panel), `.gear-profile-row`/`.gear-profile-*` (panel rows), `.gear-action-btn`, `.modal-box--wide`, `.modal-hint`, `.modal-field--checkbox`, `.checkbox-label`, `.field-row--3col`. |

### Modified backend file

| File | Key changes |
|---|---|
| `app.py` | Added `GET /gear-profiles`, `POST /gear-profiles`, `PUT /gear-profiles/{profile_id}`, `DELETE /gear-profiles/{profile_id}` endpoints. Each endpoint requires auth via `Depends(get_current_user)` and scopes queries to `staff_user_id == current_user.id`. JSON array fields (`lenses`, `has_filters`) serialised with `json.dumps`. Added `GearProfileCreate`, `GearProfileUpdate` to the `from schemas import …` line. |

### URL bug fixes (applied in same session, between Phase 9 and 10)

| Bug | Fix |
|---|---|
| **Google Maps wrong building** | Changed coordinate URL from `/maps/search/{lat},{lng}` (nearest-place search) to `?q={lat},{lng}` (exact pin drop). |
| **Google Earth not zoomed in** | Fixed two bugs: (1) spurious `35y` parameter removed — correct Google Earth Web URL format is `@lat,lng,altA,rangeD,tiltT,headingH,rollR` (5 params, no `y`); (2) range changed from `800d` (800 m, neighbourhood view) to `150d` (150 m, building/street level). |

### Migration notes
- No database changes. All schema changes were in Phase 8 (Phase 2 of pivot plan).
- Existing finalized trips load correctly — `trips.js` reads `raw_photos` which is always present; `raw_restaurants`/`raw_attractions` gracefully default to `[]` from the backend `to_dict()`.

---

## [Phase 9] Backend pivot — Photography assistant — 2026-03-05

### What changed
Full backend pivot from general travel guide to dedicated photography assistant.
Restaurant and attraction scouts are removed. The photo scout is completely rewritten
with Kelby-style technical output, gear-profile awareness, and ephemeris-driven light data.

### New files
| File | Purpose |
|---|---|
| `ephemeris.py` | Sunrise/sunset/golden hour/blue hour/moon calculations via `astral 3.2`. Inputs: GPS coords + list of dates. Outputs: per-day dict of UTC-aware datetimes + moon phase data. `format_ephemeris_block()` serialises to a plain-text prompt block. |
| `prompts.py` | All Claude prompt strings. `build_photo_scout_system_prompt(gear_profile)` builds a Kelby-style system prompt from the gear vault. `build_photo_scout_user_prompt(...)` builds the user prompt injecting ephemeris, client profile, and accommodation blocks. Replace-endpoint equivalents included. |

### Removed from `tool_schemas.py`
`RESTAURANT_TOOL` and `ATTRACTION_TOOL` deleted. `PHOTO_TOOL` fully rewritten with
Kelby-style output fields: `the_shot`, `the_setup`, `the_settings`, `the_reality_check`,
`shoot_window`, `required_gear`, `distance_from_accommodation`, `lat`, `lng`.
`google_earth_url` is constructed server-side (not in the schema).

### Modified: `app.py`
| Change | Detail |
|---|---|
| **Lifespan (3.9)** | Replaced deprecated `@app.on_event('startup'/'shutdown')` with `@asynccontextmanager _lifespan()` — proper FastAPI lifecycle management. |
| **`google_earth_url()` (3.8)** | New helper: `f"https://earth.google.com/web/@{lat},{lng},{altitude}a,800d,35y,0h,45t,0r"`. Attached to each location after the photo scout call. |
| **`call_photo_scout()` (3.6)** | Completely rewritten. Now accepts `gear_profile: dict`, `ephemeris_data: list`, `start_date: date`. Uses `build_photo_scout_system_prompt()` and `build_photo_scout_user_prompt()` from `prompts.py`. Attaches `google_earth_url` and mirrors `lat`/`lng` to `_lat`/`_lng` for Places verification compatibility. Cache key version bumped to `photo_v2`. |
| **`call_restaurant_scout()` removed** | Deleted in full. |
| **`call_attraction_scout()` removed** | Deleted in full. |
| **`_run_scouts_background()` (3.10)** | Scout tasks dict now contains only `photos`. Added `GearProfile` loading from DB (via `gear_profile_id`). Added ephemeris computation: geocodes destination via Places API, runs `get_daily_ephemeris()` in threadpool, injects into photo scout. Session store key simplified (no `restaurants`/`attractions`). Trip saved with `gear_profile_id`, `start_date`, `end_date`; `include_dining`/`include_attractions` forced to `False`. |
| **`generate_master_html()` (4-field photo cards)** | Photo cards rewritten to render `the_shot`, `the_setup`, `the_settings`, `the_reality_check`, `shoot_window`, required gear badges, Google Earth link. `restaurants`/`attractions` params now optional (default empty list) for backward compat. |
| **`/finalize`** | Session dict key access uses `.get()` with empty-list default so new photo-only sessions work alongside old trips that included restaurants/attractions. |
| **`/replace`** | Removed `restaurants` and `attractions` branches — raises HTTP 400 if non-`photos` type is requested. Uses `build_photo_replace_system_prompt()` and `build_photo_replace_user_prompt()` from `prompts.py`. Attaches `google_earth_url` and `distance_from_accommodation` to replacement item. |
| **Imports** | Added `contextlib`, `date`, `timedelta` from stdlib. Added `ephemeris`, `prompts`, `GearProfile` imports. Removed `ATTRACTION_TOOL`, `RESTAURANT_TOOL` imports. |

### Modified: `requirements.txt`
Added `astral==3.2`.

### Backward compatibility
- Existing finalized trips (with restaurants/attractions in DB) can still be re-finalized — `generate_master_html` accepts those lists as optional args and renders them if present.
- The `/replace` endpoint returns HTTP 400 (not 500) for old restaurant/attraction replace requests — the frontend should handle this gracefully in Phase 4.
- Session keys `restaurants` and `attractions` are read with `.get()` defaults — no crash if missing.

### No migration required
No DB schema changes in Phase 3. All schema changes were in Phase 8 (Phase 2 of the pivot plan).

---

## [Phase 8] Database schema pivot — GearProfile + Trip dates — 2026-03-05

### What changed
Introduces the `GearProfile` model (photographer's gear vault) and adds exact
shoot date support to trips, in preparation for the photography assistant pivot.

### New model: `GearProfile`
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `staff_user_id` | Integer FK → staff_users | indexed |
| `name` | String(100) | e.g. "Travel Kit", "Full Studio" |
| `camera_type` | String(50) | one of 7 enum values in `CAMERA_TYPES` |
| `lenses` | Text | JSON array of focal-length strings |
| `has_tripod` | Boolean | |
| `has_filters` | Text | JSON array of filter-type strings |
| `has_gimbal` | Boolean | phone/video stabilizer |
| `notes` | Text | free-text kit notes |
| `created_at` / `updated_at` | DateTime | |

A `StaffUser` may have many `GearProfile` records (cascade delete). A `Trip` may
link to one `GearProfile` via `gear_profile_id` (nullable FK).

### Modified model: `Trip`
| Change | Detail |
|---|---|
| `duration` | Made nullable — new trips use `start_date`/`end_date` instead |
| `start_date` | New `Date` column (nullable) |
| `end_date` | New `Date` column (nullable) |
| `gear_profile_id` | New nullable FK → `gear_profiles` |
| `duration_days` | New Python `@property` — returns `(end_date - start_date).days + 1` when dates set, otherwise falls back to stored `duration` integer |
| `to_dict()` | Now outputs `start_date`, `end_date`, `gear_profile_id`; `duration` key uses `duration_days` |

### New migration: `dcc6439ee150`
Creates `gear_profiles` table and modifies `trips` in a single `batch_alter_table`
block for full SQLite compatibility (SQLite does not support `ALTER COLUMN` or
inline FK additions). Railway/PostgreSQL runs the same code path cleanly.

### Modified files
| File | Key changes |
|---|---|
| `models.py` | New `GearProfile` class + `CAMERA_TYPES` constant; `StaffUser.gear_profiles` relationship added; `Trip` updated with new columns, FK, and `duration_days` property |
| `schemas.py` | New `GearProfileCreate` + `GearProfileUpdate` with `camera_type` enum validation and JSON array coercion for `lenses`/`has_filters`. `GenerateRequest` accepts `start_date`/`end_date` or `duration` (model validator resolves and validates both forms, enforces ≤14 days, rejects reversed date ranges). `TripCreate`/`TripUpdate` updated with `gear_profile_id` and date fields. Import of `date` from `datetime` added. |
| `migrations/versions/dcc6439ee150_phase2_gear_profile_and_trip_dates.py` | New Alembic migration (see above) |

### Migration notes
- Run `alembic upgrade head` locally after pulling this change.
- Railway release phase runs `alembic upgrade head` automatically on deploy — no manual action needed in production.
- Existing trip records retain their `duration` value; `start_date`/`end_date` default to NULL.
- No frontend changes in this phase — form still submits `duration` as integer; date picker UI is Phase 4.

---

## [Phase 7] Security hardening — 2026-03-05

### What changed
Four targeted security improvements applied before the photography pivot work begins.

### Modified files
| File | Key changes |
|---|---|
| `app.py` | CORS: replaced hardcoded localhost default with environment-aware logic — localhost origins only allowed when `FLASK_ENV != 'production'`; production with no `CORS_ORIGINS` set defaults to deny-all. Added `Content-Security-Policy` header to the existing security middleware. Added `_is_production` module-level flag (reused by both CORS and HSTS). |
| `auth.py` | Removed `user.email` from the login success log message — replaced with `user_id` only to prevent PII appearing in Railway log streams. |
| `requirements.txt` | Pinned `anthropic` from `>=0.40.0` to `==0.80.0` (version confirmed from local venv). |

### Content Security Policy details
```
default-src 'self';
script-src 'self' 'unsafe-inline';   ← see note below
style-src 'self' 'unsafe-inline';    ← required: srcdoc iframe inherits parent CSP; guide HTML has <style> blocks
img-src 'self' data: https://maps.googleapis.com https://maps.gstatic.com;
font-src 'self';
connect-src 'self';
frame-src 'self';
object-src 'none';
base-uri 'self';
form-action 'self';
```
`'unsafe-inline'` in `script-src` is required because `index.html` and `review.js` use inline `onclick`/`onsubmit` attributes. A TODO comment is in place — when Phase 4 converts all inline handlers to `addEventListener` calls, `'unsafe-inline'` can be removed from `script-src` for full XSS protection.

### Migration notes
- **CORS in production**: ensure `CORS_ORIGINS` is set in your Railway environment variables to your production domain (e.g. `https://myapp.railway.app`). If left unset in production, cross-origin requests will be denied (safe default — the frontend is same-origin).
- No database or schema changes.
- No frontend changes.

---

## [Phase 6] Frontend split — 2026-03-05

### What changed
The 2721-line monolithic `index.html` has been split into a proper `frontend/` directory using native ES modules (no build step required). The HTML, CSS, and JavaScript are now in separate, logical files served directly by FastAPI.

### New files
| File | Purpose |
|---|---|
| `frontend/index.html` | HTML shell — all markup, no inline CSS or JS |
| `frontend/src/styles/main.css` | All CSS (1388 lines) extracted from `index.html` |
| `frontend/src/state.js` | Shared mutable state object + constants |
| `frontend/src/api.js` | `apiFetch`, `checkAuth`, `handleLogin`, `handleLogout` |
| `frontend/src/form.js` | Form controls, progress animation, `resetForm`, `showError` |
| `frontend/src/clients.js` | Client CRM: list, create modal |
| `frontend/src/review.js` | Review screen: toggle, bulk select, edit panel, replace |
| `frontend/src/trips.js` | Saved trips panel: list, load, toggle |
| `frontend/src/finalize.js` | Final guide generation and display |
| `frontend/src/generate.js` | Form submit handler and job polling |
| `frontend/src/main.js` | Entry point: imports all modules, `window.*` exports, `checkAuth()` bootstrap |

### Modified files
| File | Key changes |
|---|---|
| `app.py` | `@app.get('/')` now serves `frontend/index.html`; added `app.mount('/src', StaticFiles(...))` to serve ES modules |

### Architecture decisions
- **Native ES modules, no Vite**: `<script type="module" src="/src/main.js">` — zero build infrastructure, no Node.js on Railway.
- **Shared state via object**: `state.js` exports a single mutable object; all modules mutate its properties directly (ES `export let` bindings are read-only from other modules).
- **Circular dep resolution**: `api.js` fires DOM `CustomEvent('auth:success')` / `CustomEvent('auth:logout')` instead of importing clients/trips; `finalize.js` uses a dynamic `import('./trips.js')` to avoid the `trips ↔ finalize` static cycle.
- **Window exports**: All functions called from inline `onclick` handlers or dynamically generated HTML strings are assigned to `window.*` in `main.js`.

### Migration notes
- `index.html` in the root is retained for backwards compatibility but is no longer served. It can be archived or deleted.
- No database, environment, or API changes. The frontend/backend contract is identical.

---

## [Phase 5] Claude tool use for structured scout output — 2026-03-05

### What changed
All three scout functions (`call_photo_scout`, `call_restaurant_scout`, `call_attraction_scout`) and the `/replace` endpoint now use Claude's structured tool use instead of text-completion with embedded JSON schemas. This eliminates the markdown-fence stripping and `_parse_json_lines` fallback that were the main sources of silent parse failures.

### New file
| File | Purpose |
|---|---|
| `tool_schemas.py` | Three tool definitions (`PHOTO_TOOL`, `RESTAURANT_TOOL`, `ATTRACTION_TOOL`). Each tool accepts an array of items so the same schema works for both the main scouts (N items) and `/replace` (one item, callers take `[0]`). |

### Modified files
| File | Key changes |
|---|---|
| `app.py` | Added `from tool_schemas import …`; replaced all three scout `messages.create()` calls to include `tools=[…], tool_choice={"type": "any"}`; replaced `_parse_json_lines(…)` with `for block in message.content: if block.type == "tool_use" …` extraction; replaced `/replace` inline JSON schemas with compact persona prompts and the same tool-use pattern; deleted `_parse_json_lines` (now dead code) |

### Why tool use over text-completion
- **Guaranteed structure**: `tool_choice={"type": "any"}` forces Claude to call the tool — no text preamble, no markdown fences, no trailing prose to strip.
- **No parse fallbacks needed**: `block.input` is already a Python dict; `json.loads` and the two-stage fallback are gone.
- **Schema as documentation**: Field descriptions in `tool_schemas.py` replace the inline JSON examples that were duplicated across system prompts.
- **Zero new dependencies**: Uses the existing `AsyncAnthropic` client.

### Migration notes
- No database or environment changes. Drop-in replacement — callers see identical item dicts.
- `_parse_json_lines` is deleted. If you need to parse legacy cached responses, the function was: `[json.loads(l) for l in text.split('\n') if l.strip().startswith('{')]`.

---

## [Phase 4] Alembic schema migrations — 2026-03-01

### What changed
Added Alembic for proper schema version control. All future column additions go through `alembic revision --autogenerate` instead of the manual `ALTER TABLE IF NOT EXISTS` list that was in `app.py`.

### New files
| File | Purpose |
|---|---|
| `alembic.ini` | Alembic config — script location, logging. DB URL is set programmatically from `DATABASE_URL` env var |
| `migrations/env.py` | Alembic environment — imports `db.metadata` from `models.py` for autogenerate; reads `DATABASE_URL`; handles `postgres://` → `postgresql://` rewrite |
| `migrations/versions/5d6c6ab024b5_initial_schema.py` | Baseline migration capturing all three tables (`staff_users`, `clients`, `trips`) with all columns, indexes, and foreign keys |

### Modified files
| File | Key changes |
|---|---|
| `requirements.txt` | Added `alembic==1.14.0` |
| `app.py` | `_init_db()` — removed `migrations` list and the `ALTER TABLE` loop; just `db.metadata.create_all(engine)` now |
| `Procfile` | Added `release: alembic upgrade head` — Railway runs this before the web process on every deploy |

### How to add a future schema change
```bash
# 1. Edit models.py (add/modify Column)
# 2. Generate migration
alembic revision --autogenerate -m "add foo column to trips"
# 3. Review the generated file in migrations/versions/
# 4. Apply locally
alembic upgrade head
# 5. Push — Railway release phase runs alembic upgrade head automatically
```

### Migration notes
- No manual action needed on Railway — the `release` phase runs `alembic upgrade head` before every deploy.
- Existing production data is unaffected. Alembic stamps the current schema revision on first run.
- For local dev: `alembic upgrade head` after cloning. SQLite default is used if `DATABASE_URL` is not set.

---

## [Phase 3] Async job queue for /generate — 2026-02-25

### What changed
`POST /generate` now returns a `{ job_id }` immediately (< 200 ms) instead of blocking for 30–60 seconds while Claude runs. The frontend polls `GET /jobs/{job_id}` every 2 seconds until the job is done, then proceeds exactly as before (review screen, replace, finalize).

### Why asyncio instead of Celery
All scout work is async I/O (Claude API + httpx). `asyncio.create_task()` runs the background coroutine concurrently in the same Uvicorn event loop — it never blocks other HTTP requests. No extra process, no new dependency, no Procfile change needed. Redis stores job state so any Gunicorn worker can answer polling requests.

### New API endpoints
| Method | Path | Description |
|---|---|---|
| `POST /generate` | (modified) | Enqueues scout job, returns `{ job_id }` in < 200 ms |
| `GET /jobs/{job_id}` | (new) | Returns `{ status, progress, message, results, error }` |

### Modified files
| File | Key changes |
|---|---|
| `app.py` | `_job_set`, `_job_get`, `_job_update` helpers; `_run_scouts_background()` async function; thin `POST /generate`; new `GET /jobs/{job_id}` |
| `index.html` | `sleep()`, `pollJobUntilDone()` functions; form submit uses two-step submit+poll; `<p id="loadingMessage">` shows server status messages |

### No changes to
- `Procfile` — no Celery worker process needed
- `requirements.txt` — no new packages
- All other routes (`/finalize`, `/replace`, `/trips`, `/clients`) — unchanged
- Database schema — unchanged

### Migration notes
- No action needed. The new endpoints are additive and backwards-compatible.
- Existing sessions stored in Redis are unaffected.

---

## [Phase 2] FastAPI Migration — 2026-02-25

### What changed
Full replacement of Flask with FastAPI across all backend files. The frontend (`index.html`) and database models (`models.py`) are unchanged. All API routes, HTTP methods, and response shapes are identical — no frontend changes required.

### New files
| File | Purpose |
|---|---|
| `schemas.py` | Pydantic v2 request models — replaces all manual `_sanitise_line`, `_clamp`, and `str(data.get(...)).strip()[:N]` validation calls |
| `database.py` | SQLAlchemy engine, `SessionLocal`, and `get_db` FastAPI dependency — DB config extracted from `app.py` |
| `manage.py` | Click CLI for creating staff accounts — replaces `flask create-user` |

### Modified files
| File | Key changes |
|---|---|
| `app.py` | Full rewrite: Flask → FastAPI, `Anthropic` → `AsyncAnthropic`, `urllib.request` → `httpx.AsyncClient`, `ThreadPoolExecutor` → `asyncio.gather`, `@require_auth` → `Depends(get_current_user)` |
| `auth.py` | Rewrite: Blueprint → `APIRouter`, Flask `g` / `@require_auth` → `get_current_user` dependency, `current_app.config` → `os.getenv` |
| `clients.py` | Rewrite: Blueprint → `APIRouter`, request parsing → Pydantic body models, DB calls wrapped in `run_in_threadpool` |
| `trips.py` | Same pattern as `clients.py` |
| `models.py` | **2-line fix:** `DeclarativeBase` instance pattern → `declarative_base()` function (fixes `AttributeError: '_Base' object has no attribute 'Model'` crash) |
| `Procfile` | `gunicorn wsgi:application --worker-class gthread` → `gunicorn app:app -k uvicorn.workers.UvicornWorker` |
| `wsgi.py` | Reduced to a stub re-export (`from app import app`) for tooling compatibility |
| `requirements.txt` | Swapped Flask stack for FastAPI stack (see below) |

### Dependency changes
**Removed:**
- `Flask`, `Flask-CORS`, `Flask-SQLAlchemy`, `Werkzeug`

**Added:**
- `fastapi`, `uvicorn[standard]`, `httpx`, `python-multipart`, `anyio`, `sqlalchemy` (plain, not Flask-SQLAlchemy)

**Kept unchanged:** `anthropic`, `python-dotenv`, `bcrypt`, `PyJWT`, `gunicorn`, `psycopg2-binary`, `redis`

### Deployment gotcha discovered
Railway's dashboard **Start Command** field takes absolute precedence over the `Procfile`. The old `gunicorn wsgi:application --worker-class gthread` command had been hardcoded there from the original Flask deployment. Even after Procfile updates, Railway kept running the old command. Fixed by clearing the Start Command in the Railway dashboard (Railway → Service → Settings → Start Command → leave blank to use Procfile).

### Migration notes
- Run `python manage.py create-user --role admin` instead of `flask create-user --role admin`
- Run `uvicorn app:app --reload` instead of `python app.py` for local development (serves on port 8000, not 5001)
- No DB changes — schema is identical

---

## [Phase 1] Redis Foundation — 2026-02-24

### What changed
Added Redis as a shared backing store for session data, scout result caching, and rate limiting. Previously all three were plain in-memory Python dicts, which meant data was lost on every redeploy and not shared between Gunicorn workers (causing "session not found" errors when `/finalize` hit a different worker than `/generate`).

### New files
| File | Purpose |
|---|---|
| `redis_client.py` | `get_redis()` connection, `SessionStore` (1-hour TTL), `Cache` (1-hour TTL), `check_login_rate_limit` / `record_login_failure`, `check_user_rate_limit` / `record_user_request` — all with in-memory fallback when Redis is unreachable |

### Modified files
| File | Key changes |
|---|---|
| `app.py` | `_session_store = {}` → `SessionStore` from `redis_client.py`; `_cache = {}` → `Cache` from `redis_client.py` |
| `auth.py` | `_login_attempts = {}` → Redis sorted-set sliding window; `_user_requests = {}` → same pattern |
| `requirements.txt` | Added `redis==5.0.4` |
| `.env.example` | Added `REDIS_URL` comment |

### Migration notes
- Add the Railway Redis plugin (one click in the Railway dashboard). Railway auto-sets `REDIS_URL` — no manual config needed.
- Redis is optional locally. If `REDIS_URL` is not set, the app falls back to in-memory dicts with a startup warning. No Redis installation required for local development.
- All existing sessions and cache entries are in-memory and will be lost on the first deploy with Redis enabled (expected and acceptable — it's a one-time transition).

---

## [Initial] Flask Baseline — pre-2026-02-24

Original Flask-based application with:
- Flask 2.3.x + Flask-SQLAlchemy + Flask-CORS
- Gunicorn gthread workers (`--worker-class gthread --threads 4`)
- In-memory session store, cache, and rate limiters (plain Python dicts)
- `urllib.request` for Google Places API calls
- `ThreadPoolExecutor(max_workers=3)` for parallel scout execution
- `flask create-user` CLI command
- Sync Anthropic SDK
