# Trip Master — Changelog

A record of significant changes to the app, newest first. Each entry covers what changed, why, and any migration notes.

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
