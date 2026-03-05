# Trip Master — Changelog

A record of significant changes to the app, newest first. Each entry covers what changed, why, and any migration notes.

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
