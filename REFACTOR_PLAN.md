# Trip Master — Refactor Plan
## Goal: Scalable, Secure, Future-Proof — Minimal Disruption

---

## Guiding principles

1. **Never break the working app.** Every phase ships a fully functional application.
2. **What isn't broken, we don't replace.** Flask-SQLAlchemy, bcrypt/JWT, the scout prompts, the HTML generator, and the security headers are all solid. They stay.
3. **Smallest change for the largest gain first.** Each phase is ordered by impact-to-risk ratio.
4. **The live Railway deployment redeploys on every push to `main`.** All phases must be backwards-compatible deploys — no manual DB migrations, no scheduled downtime.

---

## Phase 1 — Redis foundation ✅ COMPLETE (deployed 2026-02-24)
### Fix the real production bug and remove all in-memory state

**Why first:** Every other improvement (rate limiting, session continuity, caching) sits on top of this. Doing it now means every subsequent phase gets it for free.

**What we change:**

### 1a. Add Redis to Railway
- Add the Railway Redis plugin (one click in the dashboard)
- Railway auto-sets a `REDIS_URL` env var — no manual config needed
- Add `redis==5.0.4` to `requirements.txt`

### 1b. Replace `_session_store` in `app.py`
Current code:
```python
_session_store = {}   # plain dict — dies on redeploy, not shared across workers
```
New approach: a thin `SessionStore` wrapper that uses `redis.Redis.setex()` with JSON serialisation.
- Key: `session:{uuid}` with 1-hour TTL (matches current behaviour)
- Falls back gracefully if Redis is unreachable (log warning, continue with empty)

### 1c. Replace `_cache` in `app.py`
Current code:
```python
_cache = {}   # plain dict — same problems as above
```
New approach: same Redis connection, key `cache:{md5_hash}` with 1-hour TTL.

### 1d. Replace in-memory rate limiters in `auth.py`
Both `_login_attempts` and `_user_requests` become Redis sorted-set sliding windows.
- Login limiter key: `ratelimit:login:{ip}`
- AI limiter key: `ratelimit:user:{user_id}:{endpoint}`
- TTL = window duration, so Redis auto-cleans expired keys

### 1e. Backwards compatibility / graceful degradation
- If `REDIS_URL` is not set (local dev without Redis), fall back to the existing in-memory dicts with a startup warning
- This means local dev still works identically — no Redis required to run the app locally
- Add `REDIS_URL` to `.env.example` with a comment

**Files changed:** `app.py`, `auth.py`, `requirements.txt`, `.env.example`
**Risk:** Low. Redis is additive — the fallback preserves current behaviour.
**Deploy:** Single Railway push. Zero downtime.

---

## Phase 2 — FastAPI migration ✅ COMPLETE (deployed 2026-02-25)
### Replace Flask with an async framework; add Pydantic validation

**Why second:** This is the biggest structural change. Doing it after Redis means we're migrating to a clean foundation, not carrying the in-memory state problems into the new framework.

**What we change:**

### 2a. Install FastAPI and dependencies
```
fastapi==0.115.0
uvicorn[standard]==0.32.0    # replaces Gunicorn's Flask runner
httpx==0.28.0                # async HTTP (replaces urllib.request in scouts)
pydantic==2.10.0             # request/response validation (replaces _sanitise_line, _clamp)
```
Gunicorn stays in `requirements.txt` — we run `gunicorn -k uvicorn.workers.UvicornWorker` in production (same `Procfile` change).

### 2b. Pydantic schemas replace manual validation
Create `schemas.py`:
```python
class GenerateRequest(BaseModel):
    location:      str = Field(..., max_length=100, pattern=r'^[\w\s,\-\.]+$')
    duration:      int = Field(..., ge=1, le=14)
    budget:        str = Field(default='Moderate', max_length=150)
    accommodation: str | None = Field(default=None, max_length=150)
    ...

class ClientCreate(BaseModel):
    name:   str = Field(..., max_length=200)
    email:  EmailStr | None = None          # proper email validation, free
    phone:  str | None = Field(default=None, pattern=r'^[\d\s\+\-\(\)]+$')
    ...
```
This replaces `_sanitise_line`, `_clamp`, `_clamp_multiline`, and all the manual `str(data.get(...)).strip()[:N]` calls in `app.py`, `clients.py`, and `trips.py`. Validation errors return a structured 422 automatically.

### 2c. Convert scouts to async
The three scout functions become `async def` using `asyncio.gather()`:
```python
photos, restaurants, attractions = await asyncio.gather(
    call_photo_scout(...),
    call_restaurant_scout(...),
    call_attraction_scout(...)
)
```
This replaces `ThreadPoolExecutor(max_workers=3)` and the `_with_app_context` wrapper entirely. No thread overhead, no app-context juggling.

The Anthropic SDK has an async client (`AsyncAnthropic`) — a one-line swap.

Google Places calls inside each scout also become `async` using `httpx.AsyncClient`, replacing `urllib.request.urlopen`.

### 2d. Route translation
All existing Flask routes translate 1:1 to FastAPI with `APIRouter`. The URL structure, HTTP methods, and response shapes stay identical — the frontend doesn't change at all.

Auth stays JWT-in-httpOnly-cookie: FastAPI's dependency injection replaces the `@require_auth` decorator with a `get_current_user` dependency.

### 2e. SQLAlchemy stays synchronous
We keep Flask-SQLAlchemy's sync ORM. SQLAlchemy 2.0 supports both sync and async; for our access pattern (short, infrequent DB calls in a small internal tool) sync-with-connection-pool is fine and avoids `asyncpg` complexity. We run DB calls with `run_in_executor` or keep them in regular sync functions called from async routes.

### 2f. `Procfile` update
```
web: gunicorn app:app -k uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:$PORT --timeout 120
```

**Files changed:** `app.py` (full rewrite), `auth.py` (rewrite to FastAPI), `clients.py` (rewrite), `trips.py` (rewrite), new `schemas.py`, `requirements.txt`, `Procfile`
**`models.py` and `index.html` are untouched.**
**Risk:** Medium. Mitigate by running the new FastAPI app on a Railway preview branch before merging to `main`.
**Deploy:** Single Railway push after preview testing. No DB changes.

---

## Phase 3 — Async job queue for /generate ✅ COMPLETE (deployed 2026-02-25)
### Decouple AI generation from the HTTP connection

**Why third:** Requires Phase 2 (async FastAPI) to be in place for clean integration. Fixes the most visible UX pain point: the 30–60 second hanging request.

**Approach chosen:** `asyncio.create_task()` + Redis job store — *not* Celery.

All scout work is async I/O (Claude API + httpx). `asyncio.create_task()` runs the coroutine concurrently in the same Uvicorn event loop without blocking other HTTP requests. No separate worker process is needed. Redis stores job state so any Gunicorn worker can answer polling requests.

**What changed:**

### 3a. `POST /generate` — thin enqueue endpoint
Returns `{ job_id }` in < 200 ms. Validates the request and checks the rate limit before queuing so bad requests are rejected immediately.

### 3b. New `_run_scouts_background()` async function in `app.py`
Full scout pipeline (geocode → parallel scouts → verification → retries → session store → DB save) runs as a background coroutine. Updates job state in Redis at each step so the polling endpoint always has fresh status.

### 3c. New `GET /jobs/{job_id}` endpoint in `app.py`
Returns the current job state:
```json
{ "status": "pending|running|done|failed",
  "progress": 0-100,
  "message":  "Generating recommendations for Paris…",
  "results":  { ...full /generate response... },
  "error":    null }
```

### 3d. Job store helpers in `app.py`
`_job_set()`, `_job_get()`, `_job_update()` — same Redis-with-in-memory-fallback pattern as session/cache helpers. Key: `job:{job_id}`, TTL: 1 hour.

### 3e. Frontend polling in `index.html`
Added `sleep()` and `pollJobUntilDone(jobId)` functions. The form submit handler now does:
```javascript
// Step 1: submit — returns { job_id } immediately
const { job_id } = await apiFetch('/generate', { method: 'POST', ... });
// Step 2: poll until done
const result = await pollJobUntilDone(job_id);
showReviewScreen(result);   // same as before
```
A `<p id="loadingMessage">` element below the progress steps shows server-side status messages as they arrive.

**Files changed:** `app.py` (job helpers, background task, new endpoints), `index.html` (polling UI)
**No Procfile change** — no extra Railway worker process needed.
**No new dependency** — no Celery required.
**Risk:** Low. Uses only standard Python asyncio. Falls back to in-memory job store if Redis is unreachable.
**Deploy:** Single Railway push. Zero downtime.

## Phase 4 — Flask-Migrate / Alembic (half a day)
### Professional schema version control

**Why fourth:** Low risk, low urgency, but needs to happen before the schema grows further. Best done after Phase 2 so we're setting it up in the FastAPI context.

**What we change:**

### 4a. Install and initialise
```
alembic==1.14.0
```
```bash
alembic init migrations
# Edit alembic/env.py to point at our SQLAlchemy models
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### 4b. Remove `_migrations` list from `app.py`
The manual `ALTER TABLE IF NOT EXISTS` block is deleted. Future column additions go through `alembic revision --autogenerate`.

### 4c. Add `alembic upgrade head` to Railway startup
Update `Procfile` or add a Railway start command:
```
release: alembic upgrade head
web: gunicorn ...
```
Railway's "release phase" command runs before the web process starts on every deploy — the standard pattern for managed migrations.

**Files changed:** new `migrations/` directory, `app.py` (remove `_migrations`), `Procfile`, `requirements.txt`
**Risk:** Low. Alembic reads the existing schema on first run and creates a baseline migration. No data is touched.

---

## Phase 5 — Claude tool use for structured output (1 day)
### Eliminate fragile JSON parsing in /replace

**Why fifth:** Requires Phase 2 (FastAPI + async Anthropic client) to be in place. Low-risk improvement to reliability.

**What we change:**

### 5a. Define tool schemas for each scout type
```python
PHOTO_TOOL = {
    "name": "submit_photo_location",
    "description": "Submit a single photography location",
    "input_schema": {
        "type": "object",
        "properties": {
            "day":        {"type": "integer"},
            "time":       {"type": "string"},
            "name":       {"type": "string"},
            "address":    {"type": "string"},
            "subject":    {"type": "string"},
            "setup":      {"type": "string"},
            "light":      {"type": "string"},
            "pro_tip":    {"type": "string"},
            "travel_time":{"type": "string"},
        },
        "required": ["day","time","name","address","subject","setup","light","pro_tip"]
    }
}
```

### 5b. Use tool_choice="any" in /replace only (first)
The `/replace` endpoint is the only place with the fragile markdown-fence-stripping fallback. Switch it to tool use first — lower blast radius, validates the approach.

### 5c. Migrate main scouts in Phase 5b (optional)
Once `/replace` is stable, apply the same pattern to the three main scouts. The existing `_parse_json_lines` fallback can be removed.

**Files changed:** `app.py` (replace and scout functions), new `tool_schemas.py`
**Risk:** Low. The tool schemas mirror the exact JSON structure already expected — no changes to downstream processing.

---

## Phase 6 — Frontend split (2–3 days)
### Separate the ~3000-line index.html into a proper frontend project

**Why last:** Zero impact on reliability or security. Pure developer experience and maintainability improvement. By this phase the backend API is stable, so the frontend can be rebuilt against a clean contract.

**What we change:**

### 6a. Create a `frontend/` directory with Vite + vanilla JS
```
frontend/
├── index.html          (stripped shell)
├── src/
│   ├── main.js         (app entry point)
│   ├── api.js          (apiFetch wrapper, currently inline in index.html)
│   ├── generate.js     (generate form + polling)
│   ├── review.js       (review screen, replace, edit panel)
│   ├── finalize.js     (finalize + HTML preview)
│   ├── clients.js      (client CRM)
│   ├── trips.js        (saved trips list)
│   └── styles/
│       └── main.css    (extracted from <style> block)
```

No framework. Same vanilla JS, same API calls, same CSS — just split into logical files and bundled by Vite.

### 6b. Update Flask/FastAPI to serve the Vite build
```python
@app.get("/")
async def index():
    return FileResponse("frontend/dist/index.html")

@app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")
```

### 6c. Add a `build` step to Railway
```
build: cd frontend && npm install && npm run build
```
Railway runs this before starting the web process.

**Files changed:** New `frontend/` directory, `app.py` (static file serving), `Procfile`
**`index.html` is retired** (contents moved to `frontend/src/`)
**Risk:** Low. The Vite build output is a standard `dist/index.html` — the backend change is two lines.

---

## What stays exactly as-is throughout all phases

| Component | Why it doesn't change |
|---|---|
| `models.py` | SQLAlchemy models are framework-agnostic |
| All scout prompts | The prompt text is the IP — it's not a tech debt issue |
| bcrypt + JWT in httpOnly cookies | Correct auth pattern, just re-expressed in FastAPI dependency |
| `SameSite=Lax` + `X-Requested-With` CSRF | Stays, just moved to FastAPI middleware |
| HTTP security headers | Moves to FastAPI `@app.middleware("http")` |
| SSRF guard on static maps | Unchanged |
| Prompt injection defences (system/user separation, length caps) | Unchanged, now enforced by Pydantic schemas |
| Supabase PostgreSQL + Railway hosting | Unchanged |
| Google Places verification logic | Unchanged (just becomes async) |

---

## Summary timeline

| Phase | Work | Risk | Outcome |
|---|---|---|---|
| 1 — Redis | ✅ done | Low | Session store, cache, and rate limiters survive redeploys and work across workers |
| 2 — FastAPI | ✅ done | Medium | Async scouts, Pydantic validation, eliminates manual sanitisation scattered across files |
| 3 — Async job queue | ✅ done | Low | /generate returns instantly; frontend polls /jobs/{id}; server streams status messages |
| 4 — Alembic | 0.5 days | Low | Schema changes tracked in version control |
| 5 — Tool use | 1 day | Low | Structured AI output; eliminates /replace parsing fragility |
| 6 — Frontend split | 2–3 days | Low | Maintainable, testable JS; easy to add mobile or white-label surface later |
| **Total** | **~2.5 weeks** | | **Production-grade, future-proof architecture** |

Each phase ships independently. If priorities shift after Phase 2, Phases 3–6 can be reordered or deferred without leaving the app in a broken state.
