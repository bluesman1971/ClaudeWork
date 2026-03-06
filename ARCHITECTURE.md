# Trip Master — Architecture & Developer Handoff Guide

## What the app does

Trip Master is an internal tool for travel photographers and their clients. A logged-in staff member enters a destination, shoot dates, photography interests, and optionally selects a **gear profile** (camera body, lenses, filters, tripod). The app calls the Anthropic Claude API to generate curated photography shoot plans — Kelby-style cards with exact gear calls, golden-hour windows, Google Earth links, and reality-check logistics — then assembles a formatted HTML guide the consultant can review and save.

The app is a single-service, full-stack web app: the FastAPI backend serves both the API routes **and** the frontend from the same Railway URL.

---

## Repository layout

```
trip-guide-app/
│
├── app.py              # Main FastAPI application — routes, AI scout, config
├── auth.py             # Authentication router — JWT, login, rate limiting
├── models.py           # SQLAlchemy ORM models (StaffUser, Client, GearProfile, Trip)
├── clients.py          # Client CRM router — CRUD for client records
├── trips.py            # Trips router — CRUD for saved trip guides
├── schemas.py          # Pydantic v2 request/response models (validation)
├── database.py         # SQLAlchemy engine, SessionLocal, get_db dependency
├── redis_client.py     # Redis connection, SessionStore, Cache, rate limiters
├── tool_schemas.py     # Claude tool definition for structured photo scout output
├── ephemeris.py        # Sunrise/sunset/golden-hour/blue-hour/moon calculations (astral)
├── prompts.py          # All Claude prompt builder functions (Kelby-style system + user prompts)
├── manage.py           # Click CLI — create-user admin command
│
├── frontend/
│   ├── index.html      # HTML shell — all markup, no inline CSS or JS
│   └── src/
│       ├── main.js         # Entry point: imports all modules, window exports, bootstrap
│       ├── state.js        # Shared mutable state object + constants (API_URL, etc.)
│       ├── api.js          # apiFetch wrapper, checkAuth, handleLogin, handleLogout
│       ├── form.js         # Form controls, progress animation, resetForm, showError
│       ├── generate.js     # Form submit handler and async job polling
│       ├── review.js       # Review screen: Kelby card layout, toggle, edit panel, replace
│       ├── finalize.js     # Final guide generation and display
│       ├── clients.js      # Client CRM + full gear profile CRUD (list, create, edit, delete, panel)
│       ├── trips.js        # Saved trips panel: list, load, toggle
│       └── styles/
│           └── main.css    # All styles
│
├── tests/
│   ├── conftest.py         # Fixtures: StaticPool SQLite, test_user, anon_client, auth_client
│   ├── test_auth.py        # 8 tests: health, 401/403 guards, login, CSRF
│   ├── test_generate.py    # 10 tests: job_id, date validation, polling, auth
│   ├── test_ephemeris.py   # 19 tests: Barcelona/London known dates, moon phase, format block
│   ├── test_clients.py     # 12 tests: gear profile CRUD, cross-user isolation, auth
│   └── test_finalize.py    # 7 tests: session injection, subset photos, HTML output
│
├── migrations/         # Alembic migration scripts
│   ├── env.py          # Alembic environment — imports db.metadata, reads DATABASE_URL
│   └── versions/       # One file per schema revision
│
├── pytest.ini          # asyncio_mode=auto, testpaths=tests
├── wsgi.py             # Stub re-export kept for tooling compatibility
├── Procfile            # Railway/Gunicorn startup + Alembic release phase
├── runtime.txt         # Pins Python version for Railway (python-3.11.9)
├── requirements.txt    # All Python dependencies with pinned versions
│
├── .env.example        # Safe template showing all required env variables
├── .gitignore          # Excludes .env, *.db, venv/, __pycache__, etc.
```

---

## Technology stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.11.9 | Pinned in runtime.txt for Railway |
| Web framework | FastAPI 0.115.x | Async-native, Pydantic validation built in |
| ASGI server | Gunicorn 21.2.0 + UvicornWorker | Production-grade async workers |
| HTTP client | httpx 0.28.x | Async HTTP for Places API and map image fetching |
| Request validation | Pydantic v2 | Replaces all manual `_sanitise_line`/`_clamp` calls |
| Database ORM | SQLAlchemy 2.0 (sync) | Models, query builder — sync ORM via run_in_threadpool |
| Database (prod) | PostgreSQL via Supabase | Managed, free tier available |
| Database (dev) | SQLite | Zero config for local development |
| DB driver | psycopg2-binary 2.9.10 | PostgreSQL adapter for Python |
| Auth | PyJWT 2.10.1 + bcrypt 4.2.1 | JWT tokens in httpOnly cookies |
| Session store | Redis (Railway add-on) | Cross-worker session persistence (falls back to in-memory) |
| Cache | Redis | Cross-worker scout result cache (falls back to in-memory) |
| Rate limiting | Redis sorted sets | Login + per-user AI rate limits (falls back to in-memory) |
| AI | Anthropic SDK — AsyncAnthropic (Claude Haiku 4.5) | Async photo scout |
| Ephemeris | astral 3.2 | Sunrise/sunset/golden-hour/blue-hour/moon per GPS coord + date |
| Place verification | Google Places API (optional) | Confirms photo locations are accessible |
| Env vars | python-dotenv 1.0.0 | Loads .env in development |
| Hosting | Railway.app (Hobby plan) | Git-connected, auto-deploys on push |
| Test runner | pytest + pytest-asyncio | 59 tests, asyncio_mode=auto, StaticPool SQLite |

---

## Environment variables

All configuration lives in environment variables. In development, these are loaded from a `.env` file (never committed). In production, they are set in the Railway dashboard under **Project → Variables**.

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | Anthropic API key (`sk-ant-...`). Get from console.anthropic.com |
| `JWT_SECRET_KEY` | **Yes** | Long random string for signing auth tokens. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | **Yes (prod)** | PostgreSQL connection string from Supabase. Uses SQLite by default in dev |
| `REDIS_URL` | No | Redis connection string (Railway Redis add-on sets this automatically). Falls back to in-memory if not set |
| `FLASK_ENV` | Yes | Set to `production` on Railway. Enables HSTS headers and strict cookie security |
| `GOOGLE_PLACES_API_KEY` | No | Enables real-time location verification and ephemeris geocoding. App works without it |
| `SCOUT_MODEL` | No | Anthropic model ID for the photo scout. Defaults to `claude-haiku-4-5-20251001` |
| `SCOUT_MODEL_LABEL` | No | Human-readable label, shown in the health endpoint |
| `CORS_ORIGINS` | No | Comma-separated allowed origins. Not needed when frontend and backend share the same Railway URL |
| `PORT` | Auto | Set automatically by Railway. Gunicorn binds to this |

### DATABASE_URL note
Supabase provides two connection string formats. **Always use the Transaction Pooler (port 6543)**, not the direct connection (port 5432). The pooler URL looks like:
```
postgresql://postgres.YOURPROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
```

---

## How authentication works

Authentication is handled entirely in `auth.py`.

1. The staff member posts `{ email, password }` to `POST /auth/login`
2. bcrypt verifies the password against the stored hash
3. A JWT is created containing the user ID and an 8-hour expiry
4. The JWT is written into an **httpOnly cookie** called `tm_token` — JavaScript cannot read this cookie
5. Every subsequent request from the browser automatically sends this cookie
6. The `get_current_user` FastAPI dependency validates the cookie on every protected route and slides the token expiry (re-issues a fresh 8-hour cookie on each request)

### CSRF defence
All state-changing requests (POST, PUT, DELETE) require the `X-Requested-With: XMLHttpRequest` header. The `apiFetch()` wrapper in `api.js` adds this header automatically.

**CSRF vs auth precedence:** The CSRF header check fires *before* the auth cookie check on state-changing routes. Unauthenticated POST/PUT/DELETE requests without the CSRF header return **403** (not 401). Tests must assert `in {401, 403}` for unauthenticated mutating requests.

### Creating the first admin user
```bash
# In the Railway shell (Railway → service → Shell tab):
python manage.py create-user --role admin
python manage.py create-user --role staff
```

### Rate limiting
- **Login:** 10 failures per IP per 5 minutes → 10-minute lockout → HTTP 429
- **`/generate`:** 20 requests per user per 10 minutes → HTTP 429 with `retry_after`
- **`/replace`:** 60 requests per user per 10 minutes → HTTP 429 with `retry_after`
- Redis-backed, shared across workers. Falls back to in-memory (per-worker) if Redis unreachable.

---

## How the photo scout works

The core feature is the `/generate` endpoint in `app.py`. When a consultant clicks "Generate Guide":

1. **Validates the request** — location, start/end dates (1–14 days, non-reversed), photography interests
2. **Loads the gear profile** — if `gear_profile_id` is sent, the photographer's gear vault (camera body, lenses, filters, tripod) is loaded from the DB and injected into the scout prompt for tailored settings and setup advice
3. **Loads the client profile** — if `client_id` is sent, the client's `home_city`, `preferred_budget`, `travel_style`, and `notes` are loaded and injected for personalisation
4. **Computes ephemeris data** — geocodes the destination via Google Places API, then runs `get_daily_ephemeris()` from `ephemeris.py` for each shoot day. Returns sunrise/sunset/golden-hour/blue-hour windows and moon phase per day using the `astral` library
5. **Runs the photo scout** via `asyncio.create_task` as a background job:
   - Sends a Kelby-style system prompt (from `prompts.py`) + ephemeris block + client/gear context to Claude Haiku
   - Uses structured tool use (`tools=[PHOTO_TOOL], tool_choice={"type": "any"}`) — Claude is forced to call the tool; `block.input` is a ready-to-use Python dict (no JSON parsing or markdown-fence stripping)
   - Each location card has: `name`, `lat/lng`, `the_shot`, `the_setup`, `the_settings`, `the_reality_check`, `shoot_window`, `required_gear`, `distance_from_accommodation`
6. **Google Places verification** (if `GOOGLE_PLACES_API_KEY` is set) — each location verified via Places Text Search API
7. **Session store** — results stored in Redis (UUID key, 1-hour TTL). Trip record saved to DB with `gear_profile_id`, `start_date`, `end_date`
8. **`/finalize`** — takes session ID + approved photo indices, assembles full HTML guide (including Google Static Map images as base64 data URIs), returns to browser

### Ephemeris engine (`ephemeris.py`)
Input: GPS coordinates + list of dates
Output: per-day dict with UTC-aware datetimes for sunrise, sunset, golden-hour start/end, blue-hour start/end, moon phase name, moon illumination fraction.

`format_ephemeris_block()` serialises this to a compact plain-text block injected into the scout prompt so Claude knows the exact light available each shoot day.

### Kelby-style output per location
```
The Shot         — why this location, what makes it special at this time of year and light
The Setup        — specific gear from their vault, exact position, filter call-outs
The Settings     — concrete ISO/aperture/shutter starting point (or film/phone equiv)
The Reality Check— crowds, parking, access, sun direction at their specific shoot time/date
```

### Accommodation-based distance estimates
If the consultant enters an accommodation address, the app geocodes it once, computes haversine distance to each photo location, and formats a human-readable estimate (e.g. `~650 m · ~8 min walk`). Walking speed: 80 m/min. Degrades silently if geocoding fails.

### Redis-backed caching
Scout results cached for 1 hour, keyed on hash of location, dates, gear profile, accommodation, pre_planned, and client profile. Shared across all workers. Empty results are never cached.

---

## Per-item replacement (`/replace`)

The review screen lets consultants request one alternative for any photo location they dislike.

1. Frontend sends `session_id`, `trip_id`, `type` (`"photos"`), `index`, `day`, `exclude_names`
2. Endpoint resolves original trip parameters from the DB `Trip` record (multi-worker safe)
3. Exclusion list is **rebuilt server-side** from `db_trip.raw_photos` — client-supplied `exclude_names` is only a sanitised fallback when no DB record exists
4. Calls Claude with the same `PHOTO_TOOL` schema, `tool_choice={"type": "any"}`, `max_tokens=1200`
5. Google Places verification runs on the replacement if enabled
6. Updates both the DB `Trip` record and the in-memory session store

---

## Database models (`models.py`)

### `StaffUser`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| email | String(255) | Unique, lowercase |
| full_name | String(255) | Display name |
| password_hash | String(255) | bcrypt hash (12 rounds) |
| role | String(20) | `admin` or `staff` |
| is_active | Boolean | Soft-disable |
| last_login_at | DateTime | UTC |
| created_at | DateTime | UTC |

### `Client`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| reference_code | String(20) | Auto-generated: CLT-001, CLT-002… |
| name | String(255) | Required |
| email / phone / company | String | Optional |
| home_city | String(255) | Injected into scout prompts |
| preferred_budget | String(50) | e.g. "moderate", "luxury" |
| travel_style | String(255) | Free-text, injected into prompts |
| notes | Text | General freeform notes |
| tags | String(500) | Comma-separated labels |
| is_deleted | Boolean | Soft-delete |
| created_by_id | FK → StaffUser | |
| created_at, updated_at | DateTime | UTC |

### `GearProfile`
Photographer's gear vault. One user can have many profiles (e.g. "Travel Kit", "Full Studio").

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| staff_user_id | FK → StaffUser | Indexed; cascade delete |
| name | String(100) | e.g. "Travel Kit" |
| camera_type | String(50) | One of 7 enum values (see `CAMERA_TYPES` in `models.py`) |
| lenses | Text (JSON) | Array of focal-length strings |
| has_tripod | Boolean | |
| has_filters | Text (JSON) | Array of filter strings |
| has_gimbal | Boolean | |
| notes | Text | Free-text kit notes |
| created_at, updated_at | DateTime | UTC |

**Allowed `camera_type` values:** `full_frame_mirrorless`, `apsc_mirrorless`, `apsc_dslr`, `full_frame_dslr`, `smartphone`, `film_35mm`, `film_medium_format`

### `Trip`
| Column | Type | Notes |
|---|---|---|
| id | Integer PK | |
| client_id | FK → Client | Optional |
| created_by_id | FK → StaffUser | |
| gear_profile_id | FK → GearProfile | Optional |
| title | String(255) | Optional |
| status | String(20) | `draft` or `finalized` |
| location | String(255) | Destination name |
| duration | Integer | Nullable — legacy only; new trips use start/end dates |
| start_date | Date | Shoot start date |
| end_date | Date | Shoot end date |
| budget | String(50) | Budget preference |
| distance | String(50) | Travel radius |
| include_photos | Boolean | Always True for new trips |
| photos_per_day | Integer | Photo spots per day |
| photo_interests | String(500) | Photography style preferences |
| accommodation | String(500) | Hotel/address for distance estimates |
| raw_photos | Text (JSON) | Full verified item dicts from photo scout |
| approved_photo_indices | Text (JSON) | Index array from `/finalize` review |
| final_html | Text | Rendered HTML guide |
| session_id | String(36) | UUID from `/generate` |
| is_deleted | Boolean | Soft-delete |
| created_at, updated_at | DateTime | UTC |

**`duration_days` property:** returns `(end_date - start_date).days + 1` when dates are set, falls back to stored `duration` integer for legacy trips.

### Schema migrations
Managed by **Alembic**. The `Procfile` `release:` phase runs `alembic upgrade head` automatically on every Railway deploy.

```bash
# Adding a new column:
# 1. Edit models.py
alembic revision --autogenerate -m "add foo column to trips"
# 2. Review the generated migration file
alembic upgrade head
# 3. Push — Railway handles production automatically
```

---

## Security measures

| Measure | Where | What it does |
|---|---|---|
| httpOnly JWT cookie | `auth.py` | JavaScript cannot read the auth token |
| `SameSite=Lax` cookie | `auth.py` | Browser won't send cookie on cross-site requests |
| `X-Requested-With` CSRF header | `auth.py` + `api.js` | All state-changing requests require this custom header |
| bcrypt password hashing | `auth.py` | 12 rounds — brute-force resistant |
| Login rate limiting | `auth.py` | 10 failures per IP per 5 min → 10-min lockout → HTTP 429 |
| Per-user AI rate limiting | `auth.py` | `/generate`: 20 req/10 min; `/replace`: 60 req/10 min → HTTP 429 |
| Generic auth error messages | `auth.py` | Never reveals whether the email exists |
| HTTP security headers | `app.py` | `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, HSTS (prod) |
| Content Security Policy | `app.py` | CSP with `'unsafe-inline'` in `script-src` (required for inline onclick handlers) |
| SSRF guard | `app.py` | Map fetcher only connects to `maps.googleapis.com` |
| Prompt injection defence | `app.py` | System/user role separation — user input in `messages` user turn only |
| Length caps + newline stripping | `app.py`, `schemas.py` | All free-text fields sanitised before prompt injection |
| Server-side exclusion rebuild | `app.py` | `/replace` ignores client-supplied `exclude_names` when a DB trip exists |
| Cache-Control: no-cache | `app.py` | `NoCacheStaticFiles` class sets `no-cache, must-revalidate` on all `/src/*` responses — prevents stale JS/CSS after redeploy |
| `.gitignore` | repo root | Excludes `.env`, `*.db`, `venv/`, `__pycache__/` |

---

## API routes

All routes except `/`, `/src/*`, `/health`, and `/auth/login` require authentication (valid `tm_token` cookie). All state-changing routes also require `X-Requested-With: XMLHttpRequest`.

### Public
| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves `frontend/index.html` (`Cache-Control: no-cache`) |
| GET | `/src/*` | Static mount — `frontend/src/` ES modules and CSS (`Cache-Control: no-cache`) |
| GET | `/health` | Returns `{ status, message }` with model name |
| POST | `/auth/login` | `{ email, password }` → sets httpOnly cookie |
| POST | `/auth/logout` | Clears the auth cookie |

### Authenticated
| Method | Path | Description |
|---|---|---|
| GET | `/auth/me` | Returns current user profile |
| POST | `/generate` | Enqueues photo scout job → returns `{ job_id }` immediately |
| GET | `/jobs/{job_id}` | Polls job status → `{ status, progress, message, results, error }` |
| POST | `/finalize` | Assembles and returns final HTML guide |
| POST | `/replace` | Replaces a single photo location with an alternative |
| GET | `/clients` | Lists all active clients |
| POST | `/clients` | Creates a new client |
| GET | `/clients/{id}` | Gets one client |
| PUT | `/clients/{id}` | Updates client fields |
| DELETE | `/clients/{id}` | Soft-deletes a client |
| GET | `/gear-profiles` | Lists all gear profiles for the current user |
| POST | `/gear-profiles` | Creates a new gear profile |
| PUT | `/gear-profiles/{id}` | Updates a gear profile (scoped to current user) |
| DELETE | `/gear-profiles/{id}` | Deletes a gear profile (scoped to current user) |
| GET | `/trips` | Lists saved trips for current user |
| POST | `/trips` | Saves a trip guide |
| GET | `/trips/{id}` | Gets one trip |
| PUT | `/trips/{id}` | Updates trip fields/status |
| DELETE | `/trips/{id}` | Soft-deletes a trip |

### `POST /generate` — request body
```json
{
  "location":        "Barcelona, Spain",
  "start_date":      "2026-06-15",
  "end_date":        "2026-06-18",
  "photo_interests": ["Architecture & Buildings", "Sunrise & Sunset (Golden Hour)"],
  "photos_per_day":  3,
  "budget":          "Moderate",
  "distance":        "Up to 15 minutes",
  "accommodation":   "Hotel Arts, Carrer de la Marina 19-21",
  "pre_planned":     "Sagrada Família visit booked for Day 1 morning",
  "client_id":       5,
  "gear_profile_id": 2
}
```
Returns `{ "job_id": "uuid" }` immediately. Poll `GET /jobs/{job_id}` for results.

### `POST /replace` — request body
```json
{
  "session_id":    "uuid-from-generate",
  "trip_id":       5,
  "type":          "photos",
  "index":         0,
  "day":           1,
  "exclude_names": ["Park Güell", "Barceloneta Beach"]
}
```

---

## Production deployment (Railway)

### How deploys work
Railway watches the `main` branch of `bluesman1971/ClaudeWork`. Every `git push origin main` triggers an automatic redeploy.

### Startup sequence
1. Railway builds container from `runtime.txt` (Python 3.11.9) and installs `requirements.txt`
2. `release:` phase in `Procfile` runs `alembic upgrade head`
3. Gunicorn starts 2 Uvicorn workers:
   ```
   gunicorn app:app -k uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:$PORT --timeout 120
   ```
4. FastAPI `@asynccontextmanager _lifespan()` runs: creates `httpx.AsyncClient`, initialises DB tables, checks Redis

> **Important:** Ensure the Railway dashboard **Start Command** is blank so the `Procfile` is used. The dashboard field takes precedence over `Procfile`.

### Railway variables to set
```
ANTHROPIC_API_KEY      = sk-ant-...
JWT_SECRET_KEY         = (python -c "import secrets; print(secrets.token_hex(32))")
DATABASE_URL           = postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
REDIS_URL              = (set automatically by Railway Redis add-on)
FLASK_ENV              = production
GOOGLE_PLACES_API_KEY  = (optional)
```

---

## Local development setup

```bash
# 1. Clone the repo
git clone https://github.com/bluesman1971/ClaudeWork.git
cd ClaudeWork

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and JWT_SECRET_KEY at minimum

# 5. Create the first admin user
python manage.py create-user --role admin

# 6. Run the development server (backend serves frontend too — single terminal)
uvicorn app:app --reload --port 8000
# App runs at http://localhost:8000
```

### Running the test suite
```bash
source venv/bin/activate
pytest                                           # run all 59 tests
pytest tests/test_clients.py -v                  # run one module
pytest --cov=. --cov-report=term-missing         # with coverage
```

---

## Common maintenance tasks

### Add a new staff user
```bash
# In Railway shell (Railway → service → Shell):
python manage.py create-user --role staff
```

### Rotate the JWT secret key
1. Generate: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update `JWT_SECRET_KEY` in Railway variables
3. **All existing sessions will be invalidated — every user will be logged out**

### Change the Claude model
Update `SCOUT_MODEL` in Railway variables (e.g. `claude-sonnet-4-5-20250929`). No code change needed.

### Reset the Supabase database password
1. Supabase → Project Settings → Database → Reset password
2. Rebuild `DATABASE_URL` using the Transaction Pooler format (port 6543)
3. Update `DATABASE_URL` in Railway variables

---

## Known limitations and future work

- **Inline onclick handlers prevent full CSP** — `script-src` retains `'unsafe-inline'` because `index.html` and dynamically generated `innerHTML` in `review.js` use inline `onclick` attributes. Converting all inline handlers to `addEventListener` calls would allow removing `'unsafe-inline'` for full XSS protection.

- **No email delivery** — no password reset flow. Forgotten passwords require an admin to create a new account or update the hash directly in the database.

- **Single-vendor AI** — all photo scouting uses Anthropic Claude Haiku via `SCOUT_MODEL`. Switching models within Anthropic is trivial (env var only). Switching providers requires updating the client initialisation, tool schemas, and response extraction in `app.py` and `tool_schemas.py` — roughly 15–20 lines.

- **API cost** — each "Generate" click makes one API call (the photo scout). High-volume usage accumulates meaningful API costs. Monitor in the Anthropic console. To reduce cost: lower `photos_per_day`, cap `max_tokens`, or switch models via `SCOUT_MODEL`.

- **Static map images** — Google Static Maps API embeds base64 map images in the final HTML guide. Requires `GOOGLE_PLACES_API_KEY`. If not set, maps are omitted gracefully.
