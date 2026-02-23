# Trip Master — Architecture & Developer Handoff Guide

## What the app does

Trip Master is an internal tool for travel consultants. A logged-in staff member enters a destination, trip duration, and preferences (photography spots, restaurants, attractions). The app calls the Anthropic Claude API to generate curated recommendations for each category, optionally verifies each venue is still open via the Google Places API, then assembles a formatted HTML travel guide the consultant can review and save.

The app is a single-service, full-stack web app: the Flask backend serves both the API routes **and** the frontend HTML page from the same Railway URL.

---

## Repository layout

```
trip-guide-app/
│
├── app.py              # Main Flask application — routes, AI scouts, config
├── auth.py             # Authentication blueprint — JWT, login, rate limiting
├── models.py           # SQLAlchemy ORM models (StaffUser, Client, Trip)
├── clients.py          # Client CRM blueprint — CRUD for client records
├── trips.py            # Trips blueprint — CRUD for saved trip guides
│
├── index.html          # Single-page frontend (vanilla JS, no framework)
│
├── wsgi.py             # Gunicorn entry point — imports app as 'application'
├── Procfile            # Railway/Gunicorn startup command
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
| Web framework | Flask 2.3.3 | Lightweight, easy to reason about |
| WSGI server | Gunicorn 21.2.0 (gthread workers) | Production-grade, multi-threaded |
| Database ORM | Flask-SQLAlchemy 3.1.1 / SQLAlchemy 2.0 | Models, migrations, query builder |
| Database (prod) | PostgreSQL via Supabase | Managed, free tier available |
| Database (dev) | SQLite | Zero config for local development |
| DB driver | psycopg2-binary 2.9.10 | PostgreSQL adapter for Python |
| Auth | PyJWT 2.10.1 + bcrypt 4.2.1 | JWT tokens in httpOnly cookies |
| AI | Anthropic SDK ≥0.40.0 (Claude Haiku 4.5) | Generates recommendations |
| Place verification | Google Places API (optional) | Confirms venues are still open |
| CORS | Flask-CORS 4.0.0 | Cross-origin header management |
| Env vars | python-dotenv 1.0.0 | Loads .env in development |
| Hosting | Railway.app (Hobby plan) | Git-connected, auto-deploys on push |

---

## Environment variables

All configuration lives in environment variables. In development, these are loaded from a `.env` file (never committed). In production, they are set in the Railway dashboard under **Project → Variables**.

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | Anthropic API key (`sk-ant-...`). Get from console.anthropic.com |
| `JWT_SECRET_KEY` | **Yes** | Long random string for signing auth tokens. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | **Yes (prod)** | PostgreSQL connection string from Supabase. Uses SQLite by default in dev |
| `FLASK_ENV` | Yes | Set to `production` on Railway. Enables HSTS headers and strict cookie security |
| `GOOGLE_PLACES_API_KEY` | No | Enables real-time venue verification. App works without it (verification is skipped) |
| `SCOUT_MODEL` | No | Anthropic model ID to use for all scouts. Defaults to `claude-haiku-4-5-20251001` |
| `SCOUT_MODEL_LABEL` | No | Human-readable label for the model, shown in the health endpoint |
| `CORS_ORIGINS` | No | Comma-separated allowed origins. Not needed when frontend and backend share the same Railway URL |
| `PORT` | Auto | Set automatically by Railway. Gunicorn binds to this |

### DATABASE_URL note
Supabase provides two connection string formats. **Always use the Transaction Pooler (port 6543)**, not the direct connection (port 5432). The pooler URL looks like:
```
postgresql://postgres.YOURPROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
```
The direct connection on port 5432 is blocked by Supabase's free tier firewall for external cloud hosts.

---

## How authentication works

Authentication is handled entirely in `auth.py`.

1. The staff member posts `{ email, password }` to `POST /auth/login`
2. bcrypt verifies the password against the stored hash
3. A JWT is created containing the user ID and an 8-hour expiry
4. The JWT is written into an **httpOnly cookie** called `tm_token` — JavaScript cannot read this cookie, which protects against XSS token theft
5. Every subsequent request from the browser automatically sends this cookie
6. The `@require_auth` decorator validates the cookie on every protected route, loads the user from the database into `g.current_user`, then slides the token expiry (re-issues a fresh 8-hour cookie on each request so active sessions don't expire mid-use)

### CSRF defence
All state-changing requests (POST, PUT, DELETE, PATCH) require the `X-Requested-With: XMLHttpRequest` header. The browser never attaches this header automatically on cross-site requests, so it cannot be forged by a malicious third-party page even if it can trigger a request with the user's session cookie. The `apiFetch()` wrapper in `index.html` adds this header to every call automatically.

**Important when calling the API programmatically (e.g. curl or test scripts):** you must include both the `tm_token` cookie and the `X-Requested-With: XMLHttpRequest` header on all POST/PUT/DELETE requests, or you will receive HTTP 403.

### Creating the first admin user
There is no registration UI. Staff accounts are created by an admin using the Flask CLI:
```bash
# In the Railway shell (Railway → service → Shell tab):
flask create-user --role admin
# Follow the prompts for email, name, and password

# To create a regular staff account:
flask create-user --role staff
```

### Login rate limiting
Failed login attempts are tracked per IP address in memory. After 10 failures within 5 minutes, the IP is blocked for 10 minutes and receives HTTP 429. This resets on server restart (acceptable for defence-in-depth; a Redis-backed store would be needed for persistent rate limiting across multiple workers).

### Per-user AI endpoint rate limiting
Authenticated users are rate-limited on the two expensive AI endpoints to prevent runaway API cost from a single account:

| Endpoint | Limit | Window |
|---|---|---|
| `POST /generate` | 20 requests | 10 minutes |
| `POST /replace` | 60 requests | 10 minutes |

Implemented in `auth.py` via `check_user_rate_limit(user_id, endpoint)`, using the same sliding-window in-memory pattern as login rate limiting. The limit is keyed by `(user_id, endpoint)` so each user has an independent budget per endpoint. Returns HTTP 429 with a `retry_after` count in the error message. Like the login limiter, this is per-worker and resets on restart — a Redis-backed store would be needed for strict enforcement in a multi-worker public deployment.

---

## How the AI scouts work

The core feature is the `/generate` endpoint in `app.py`. When a consultant clicks "Generate", the app:

1. **Validates the request** — checks location, duration (1–14 days), and which categories are enabled (photos, restaurants, attractions)
2. **Loads the client profile** — if a `client_id` is sent with the request, the client's `home_city`, `preferred_budget`, `travel_style`, `dietary_requirements`, and `notes` are loaded from the DB and injected into each scout prompt for personalisation
3. **Reads optional trip context** — `accommodation` (hotel name/address used as the travel origin for distance estimates) and `pre_planned` (already-committed events the guide should work around) are accepted as request fields and passed to each scout
4. **Runs up to 3 scouts in parallel** using `ThreadPoolExecutor(max_workers=3)`:
   - `call_photo_scout` — photography locations with timing, setup, and pro tips
   - `call_restaurant_scout` — dining recommendations with cuisine, price range, and booking notes; respects dietary requirements as a hard constraint
   - `call_attraction_scout` — sightseeing with practical visit info; avoids duplicating pre-planned commitments
5. **Each scout** sends a structured prompt to Claude Haiku and parses the response as JSON lines (one JSON object per line). Scout results include a `travel_time` field (estimated travel from the accommodation) and a `why_this_client` field (personalisation rationale)
6. **Google Places verification** (if `GOOGLE_PLACES_API_KEY` is set) — each venue is verified via the Places Text Search API. Permanently closed venues are filtered out. This runs concurrently inside each scout using another `ThreadPoolExecutor`
7. **Retry logic** — if a scout returns 0 results (parse failure or all venues filtered), it retries up to 2 more times with a 1-second delay between attempts
8. **Session store** — verified results are stored in a server-side in-memory dict (`_session_store`) keyed by a UUID, with a 1-hour TTL. Results are also saved to the DB `Trip` record immediately so `/replace` can reconstruct context on any worker
9. **`/finalize`** — takes the session ID, assembles the full HTML travel guide (including fetching Google Static Map images as base64 data URIs), and returns it to the browser

### Travel time estimates
If the consultant enters an accommodation address on the generate form, the app:
1. Geocodes the address once via the Places API (same key as venue verification — no additional API needed)
2. After verifying each venue, computes the straight-line (haversine) distance from the accommodation coordinates to the venue's `_lat`/`_lng`
3. Formats it as a human-readable estimate, e.g. `~650 m · ~8 min walk` or `~2.1 km · ~26 min walk`
4. Writes this into the `travel_time` field, overwriting the Claude-generated text

Walking speed used: **80 m/min** — a comfortable urban pace that accounts for pavements and crossings. Straight-line distance is always shorter than the actual walking route, so the estimate is a lower bound. All values are labelled `~` to signal they are approximate.

**Fallback chain:** If Places verification is disabled (no API key), or if accommodation geocoding fails, or if an item has no `_lat`/`_lng` (unverified), the Claude-generated text estimate is preserved unchanged. The feature degrades silently — no errors are surfaced to the user.

The accommodation string is also stored on the `Trip` DB record so `/replace` can geocode it for replacement items.

### In-memory caching
Scout results are cached in `_cache` (a plain dict) for 1 hour keyed on the combination of location, duration, preferences, accommodation, pre_planned, and client profile. Empty results are never cached so a failed parse always retries fresh on the next request.

### Thread safety note
All three executor sites wrap their callables in `_with_app_context(fn, *args)`. This ensures each thread has its own independent Flask application context. Sharing a single AppContext across threads is not safe — each thread must push and pop its own.

---

## Per-item replacement (`/replace`)

The review screen lets consultants request one alternative for any item they dislike. This is handled by `POST /replace` in `app.py`.

**How it works:**

1. The frontend sends `session_id`, `trip_id`, `type` (photos/restaurants/attractions), `index`, `day`, `meal_type`, and `exclude_names` (names of current items — used as a fallback only; see step 4)
2. The endpoint resolves the original trip parameters (location, budget, distance, cuisine/interest preferences) from the DB `Trip` record — this is intentional for multi-worker safety (avoids relying on the in-memory session store which may not be present on the worker handling this request)
3. The client profile is reloaded from the DB if the trip has a `client_id`
4. **Exclusion list is rebuilt server-side:** `_names_from_raw()` parses the relevant `raw_*` JSON column from the DB `Trip` record and extracts the `"name"` field from each stored item. This means the exclusion list is always built from server-generated, verified data — not client-supplied text. The client's `exclude_names` payload is only used as a sanitised fallback when no DB trip record is available (rare in-memory-only case). This eliminates the injection surface that existed when client-supplied names were placed verbatim into the prompt.
5. The Anthropic API is called with `max_tokens=1200`
6. **Response parsing:** The model sometimes wraps output in markdown code fences (` ```json ... ``` `). The endpoint strips these before attempting JSON parsing. It first tries `_parse_json_lines()` (for responses where the JSON is on a single line), then falls back to `json.loads()` (for pretty-printed multi-line responses)
7. Google Places verification runs on the replacement if enabled
8. The DB `Trip` record's relevant `raw_*` JSON array is updated at the given index
9. The in-memory session store is also updated if still alive
10. Returns `{ "item": { ...scout item dict... } }`

**Frontend integration (`index.html`):**

- `buildReviewItem(type, item, idx)` — builds a wrapper div containing the item row and a hidden inline edit panel. Each item row has two action buttons: **Edit** and **Alt**
- `toggleEditPanel(type, idx)` — opens/closes the inline edit panel; focuses the name input on open
- `saveItemEdit(type, idx)` — reads name and consultant notes from the panel, writes back to `rawData`, updates the visible name in the DOM
- `replaceItem(type, idx)` — calls `POST /replace`, swaps the item in `rawData`, rebuilds the wrapper in-place via `buildReviewItem()`, applies a brief green flash animation, preserves the item's approval/rejection state

---

## Database models (`models.py`)

Three tables, all with soft-delete and UTC timestamps.

### `StaffUser`
Travel consultant / admin accounts. Passwords are stored as bcrypt hashes (12 rounds). Never stores plaintext passwords.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| email | String(255) | Unique, lowercase |
| full_name | String(255) | Display name |
| password_hash | String(255) | bcrypt hash |
| role | String(20) | `admin` or `staff` |
| is_active | Boolean | Soft-disable without deleting |
| last_login_at | DateTime | UTC, updated on each login |
| created_at | DateTime | UTC |

### `Client`
Travel client records managed by staff. Reference codes are auto-generated as CLT-001, CLT-002, etc.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| reference_code | String(20) | Auto-generated: CLT-001, CLT-002… Unique, indexed |
| name | String(255) | Required |
| email | String(255) | Optional |
| phone | String(50) | Optional |
| company | String(255) | Optional |
| home_city | String(255) | Used to personalise scout prompts |
| preferred_budget | String(50) | e.g. "budget", "moderate", "luxury" — injected into prompts |
| travel_style | String(255) | Free-text, e.g. "adventure traveller, prefers off-the-beaten-path" |
| dietary_requirements | Text | Hard constraint in restaurant scout, e.g. "vegetarian, nut allergy" |
| notes | Text | General freeform notes about the client |
| tags | String(500) | Comma-separated labels for filtering |
| is_deleted | Boolean | Soft-delete flag |
| created_by_id | FK → StaffUser | Which staff member created the record |
| created_at, updated_at | DateTime | UTC |

### `Trip`
Saved travel guide records. Stores both the raw AI output and the final HTML.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| client_id | FK → Client | Optional — links trip to a client record |
| created_by_id | FK → StaffUser | Which staff member generated it |
| title | String(255) | Optional, auto-generated if blank |
| status | String(20) | `draft` or `finalized` |
| location | String(255) | Destination name |
| duration | Integer | Trip length in days |
| budget | String(50) | Budget preference used in prompts |
| distance | String(50) | Travel radius used in prompts |
| include_photos/dining/attractions | Boolean | Which scout categories were enabled |
| photos/restaurants/attractions_per_day | Integer | Counts per day for each category |
| photo_interests | String(500) | Photography style preferences |
| cuisines | String(500) | Cuisine preferences for restaurant scout |
| attraction_cats | String(500) | Attraction category preferences |
| accommodation | String(500) | Hotel/address used as travel origin for distance estimates |
| raw_photos | Text (JSON) | Full verified item dicts from photo scout |
| raw_restaurants | Text (JSON) | Full verified item dicts from restaurant scout |
| raw_attractions | Text (JSON) | Full verified item dicts from attraction scout |
| approved_photo/restaurant/attraction_indices | Text (JSON) | Index arrays from `/finalize` review step |
| final_html | Text | Rendered HTML guide |
| colors | String(500) (JSON) | Color theme dict |
| session_id | String(36) | UUID from `/generate` — links DB record to in-memory session |
| is_deleted | Boolean | Soft-delete flag |
| created_at, updated_at | DateTime | UTC |

### Database configuration
`app.py` reads `DATABASE_URL` from the environment. A `_safe_db_url()` helper parses it through SQLAlchemy's `make_url()` before use — this correctly percent-encodes any special characters in the password, so the raw Supabase connection string can be pasted directly into Railway without manual URL-encoding.

For SQLite (local dev only), WAL (Write-Ahead Logging) mode is enabled on every new connection via `sqlalchemy.event.listen`. This allows concurrent readers and a single writer simultaneously, which prevents "database is locked" errors during local testing.

### Schema migrations
`db.create_all()` runs at startup and creates any tables that don't yet exist. It does **not** add new columns to existing tables.

To handle this, `app.py` runs a list of `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements immediately after `db.create_all()`. This is safe to re-run on every startup — PostgreSQL's `IF NOT EXISTS` clause is a no-op if the column already exists. SQLite doesn't support `IF NOT EXISTS` on `ADD COLUMN`, so the migration block catches and logs the exception without crashing.

**When adding a new column to an existing model:** add the column to `models.py` as usual, then add a corresponding `ALTER TABLE <table> ADD COLUMN IF NOT EXISTS <col> <type>` entry to the `_migrations` list in `app.py`. The column will be created on the next deploy.

Current migrations (as of this version):
```python
_migrations = [
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS dietary_requirements TEXT",
    "ALTER TABLE trips   ADD COLUMN IF NOT EXISTS accommodation VARCHAR(500)",
]
```

---

## Security measures

| Measure | Where | What it does |
|---|---|---|
| httpOnly JWT cookie | `auth.py` | JavaScript cannot read the auth token — prevents XSS token theft |
| `SameSite=Lax` cookie | `auth.py` | Browser won't send cookie on cross-site requests — primary CSRF defence |
| `X-Requested-With` CSRF header | `auth.py` + `index.html` | Secondary CSRF defence — all state-changing requests require this custom header, which browsers never attach automatically on cross-site requests |
| bcrypt password hashing | `auth.py` | Passwords stored as bcrypt hashes with 12 rounds — slow enough to resist brute force |
| Login rate limiting | `auth.py` | Max 10 failures per IP per 5 minutes, then 10-minute lockout — in-memory, per-worker |
| Per-user AI rate limiting | `auth.py` | `/generate`: 20 req/10 min per user; `/replace`: 60 req/10 min per user — returns HTTP 429 with `retry_after` seconds |
| Generic auth error messages | `auth.py` | "Invalid email or password" — never reveals whether the email exists |
| HTTP security headers | `app.py` | `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`, HSTS (production only) |
| SSRF guard | `app.py` | `_fetch_static_map_as_base64()` only fetches from `https://maps.googleapis.com/` — blocks server-side request forgery |
| Generic error messages in API | `app.py` | `/generate` and `/finalize` return generic errors on failure — never leaks internal exception details to the browser |
| Prompt injection defence — system/user role separation | `app.py` | All Anthropic API calls use the `system` parameter for role definitions, output format schemas, and writing rules. User-supplied fields (location, accommodation, pre_planned, dietary_requirements, notes, exclude_names, etc.) are placed only in the `messages` user turn. Claude treats system content as more authoritative than user-message content, making it significantly harder for injected text in user fields to override the real instructions. |
| `.gitignore` | repo root | Excludes `.env`, `*.db`, `venv/`, `__pycache__/`, `.DS_Store`, backup files |

### Prompt injection — current posture and remaining work

The system/user role separation is the primary defence. Additional hardening steps that would be required before any public-facing exposure:

| Risk | Status | What's needed |
|---|---|---|
| System/user role separation | ✅ Implemented | Done — all scouts and `/replace` use `system` parameter |
| Length caps on free-text fields | ✅ Implemented | Constants `MAX_FIELD_SHORT` (150), `MAX_FIELD_MEDIUM` (500), `MAX_EXCLUDE_NAME_LEN` (100), `MAX_EXCLUDE_LIST_LEN` (50) in `app.py`. Applied to `accommodation`, `pre_planned`, `budget`, `distance` in `/generate`; each `exclude_names` entry and the list itself in `/replace`; all string fields in `POST /clients` and `PUT /clients/<id>`. |
| Newline stripping on single-line fields | ✅ Implemented | `_sanitise_line()` in `app.py` and `_clamp()` in `clients.py` run `re.sub(r'\s+', ' ', ...)` before truncation. Applied to all single-line fields: location, accommodation, budget, distance, each `exclude_names` entry; and all client fields except `notes`. Multi-line fields (`pre_planned`, `notes`) strip ends only — internal newlines are intentional. |
| `exclude_names` server-side validation | ✅ Implemented | `/replace` now ignores the client-supplied `exclude_names` when a DB trip is found. Instead it calls `_names_from_raw()` which parses `db_trip.raw_photos/raw_restaurants/raw_attractions` and extracts the `"name"` field from each stored item — data that was generated and verified by the server at `/generate` time. The client list is only used as a sanitised fallback for in-memory-only sessions (no DB record). |
| Per-user rate limiting on `/generate` and `/replace` | ✅ Implemented | `check_user_rate_limit(user_id, endpoint)` in `auth.py`. Limits: 20 `/generate` calls per user per 10 min; 60 `/replace` calls per user per 10 min. Returns HTTP 429 with seconds-to-retry in the error message. In-memory, per-worker — sufficient for an internal tool. |

---

## API routes

All routes except `/`, `/health`, and `/auth/login` require authentication (valid `tm_token` cookie). All state-changing routes (POST, PUT, DELETE) also require the `X-Requested-With: XMLHttpRequest` header.

### Public
| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves `index.html` (the frontend SPA) |
| GET | `/health` | Health check — returns JSON with status and model name |
| POST | `/auth/login` | Login — `{ email, password }` → sets httpOnly cookie |
| POST | `/auth/logout` | Clears the auth cookie |

### Authenticated
| Method | Path | Description |
|---|---|---|
| GET | `/auth/me` | Returns current user's profile |
| POST | `/generate` | Runs scouts, returns structured recommendations + session ID |
| POST | `/finalize` | Assembles and returns final HTML guide from session data |
| POST | `/replace` | Replaces a single review item with an alternative — see below |
| GET | `/clients` | Lists all active clients (newest first) |
| POST | `/clients` | Creates a new client |
| GET | `/clients/<id>` | Gets one client with their trips |
| PUT | `/clients/<id>` | Updates client fields (partial update supported) |
| DELETE | `/clients/<id>` | Soft-deletes a client (trips are preserved) |
| GET | `/trips` | Lists saved trips for current user |
| POST | `/trips` | Saves a trip guide |
| GET | `/trips/<id>` | Gets one trip |
| PUT | `/trips/<id>` | Updates trip fields/status |
| DELETE | `/trips/<id>` | Soft-deletes a trip |

### `POST /replace` — request body
```json
{
  "session_id":    "uuid-from-generate",
  "trip_id":       5,
  "type":          "restaurants",
  "index":         0,
  "day":           1,
  "meal_type":     "lunch",
  "exclude_names": ["Restaurant A", "Restaurant B"]
}
```
**Note on `exclude_names`:** when a DB trip record is found (the normal case), this field is ignored — the server rebuilds the exclusion list from `db_trip.raw_restaurants` (or `raw_photos`/`raw_attractions`) directly. The field is only used as a sanitised fallback for in-memory-only sessions.

Returns `{ "item": { ...full scout item dict... } }` on success, or `{ "error": "..." }` with HTTP 422 if the model fails to produce a parseable alternative. Returns HTTP 429 if the user has exceeded their rate limit.

---

## Production deployment (Railway)

### How deploys work
Railway watches the `main` branch of the GitHub repository (`bluesman1971/ClaudeWork`). Every `git push origin main` triggers an automatic redeploy — no manual action needed.

### Startup sequence
1. Railway builds the container from `runtime.txt` (Python 3.11.9) and installs `requirements.txt`
2. Railway runs the command in `Procfile`:
   ```
   gunicorn wsgi:application --workers 2 --threads 4 --worker-class gthread --bind 0.0.0.0:$PORT --timeout 120
   ```
3. Gunicorn imports `wsgi.py`, which imports `app.py` as `application`
4. `app.py` module-level code runs: loads env vars, configures the DB, registers blueprints, creates tables if they don't exist, runs column migrations
5. Gunicorn starts serving on the Railway-assigned `$PORT`

### Gunicorn configuration explained
- `--workers 2` — 2 separate processes (each loads the full app)
- `--threads 4` — 4 threads per worker (handles concurrent requests within each process)
- `--worker-class gthread` — thread-based worker (required for `--threads` to work)
- `--timeout 120` — 120-second request timeout (needed because Claude API calls can take 30–60 seconds)

### Railway variables to set
```
ANTHROPIC_API_KEY      = sk-ant-...
JWT_SECRET_KEY         = (generate with: python -c "import secrets; print(secrets.token_hex(32))")
DATABASE_URL           = postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
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
# Edit .env and fill in ANTHROPIC_API_KEY and JWT_SECRET_KEY at minimum
# DATABASE_URL defaults to SQLite (trip_master.db) if not set — fine for dev

# 5. Create the first admin user
flask create-user --role admin

# 6. Run the development server
python app.py
# App runs at http://localhost:5001
```

---

## Common maintenance tasks

### Add a new staff user
```bash
# In Railway shell (Railway → service → Shell):
flask create-user --role staff
```

### Rotate the JWT secret key
1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update `JWT_SECRET_KEY` in Railway variables
3. **Note:** all existing sessions will be invalidated — every user will be logged out

### Rotate the Anthropic API key
1. Generate a new key at console.anthropic.com
2. Update `ANTHROPIC_API_KEY` in Railway variables
3. Railway will redeploy automatically

### Change the Claude model
Update `SCOUT_MODEL` in Railway variables (e.g. `claude-sonnet-4-5-20250929`). All three scouts use the same model. No code change needed.

### Reset the Supabase database password
If you need to reset the DB password:
1. Go to Supabase → Project Settings → Database → Reset password
2. Copy the new password
3. Rebuild the `DATABASE_URL` using the **Transaction Pooler** format (port 6543)
4. Update `DATABASE_URL` in Railway variables

### View live logs
Railway → your service → **Deployments** → click the active deployment → scroll through logs. Each request is logged with method, path, status code, and response time. Scout results and errors are logged at INFO/ERROR level.

---

## Known limitations and future work

- **In-memory session store and cache** — `_session_store` and `_cache` in `app.py` are plain Python dicts. They reset on every deploy and are not shared between Gunicorn workers. For a multi-worker setup this can cause "session not found" errors if the `/finalize` request hits a different worker than `/generate`. The `/replace` endpoint works around this by always resolving trip parameters from the DB `Trip` record rather than the session store — but `/finalize` still uses the session store and could be affected. A Redis store (e.g. Railway Redis add-on) would fix this properly.

- **In-memory rate limiters** — both the login rate limiter (`auth.py`) and the per-user AI endpoint limiter (`auth.py`) are plain in-memory dicts. They reset on every deploy and are not shared between Gunicorn workers. For a strictly enforced public deployment, back them with Redis (e.g. the Railway Redis add-on). For the current internal-staff use case they are adequate.

- **Schema migrations are append-only** — the `_migrations` list in `app.py` handles adding new columns to existing tables automatically on startup. However, renaming or removing columns, adding constraints, or changing column types still requires manual intervention (run the SQL directly in the Supabase dashboard). For complex schema evolution, adopt Flask-Migrate / Alembic.

- **Single-file frontend** — `index.html` is a large single file containing all HTML, CSS, and JavaScript. It works well but would benefit from being split into components if the UI grows significantly.

- **No email delivery** — there is no password reset flow. Forgotten passwords require an admin to create a new account or update the hash directly in the database.

- **AI provider is single-vendor and single-model** — all three scouts use the same Anthropic model, set via the `SCOUT_MODEL` environment variable. Switching to a different Anthropic model is trivial (just update the env var). Switching to a different provider (OpenAI, Google Gemini, etc.) requires changing the client initialisation in `app.py` and updating the API call and response extraction in each of the three scout functions and the `/replace` endpoint — roughly 8–10 lines of code plus a `requirements.txt` change. The prompts, retry logic, and JSON parsing are all provider-agnostic and would not need to change.

- **API cost** — Claude Haiku is the cheapest Anthropic model and was chosen deliberately to keep per-generation costs low, but each "Generate" click makes three separate API calls (one per scout), each with a large prompt and up to ~6,000 output tokens. High-volume usage will accumulate meaningful API costs. Monitor usage in the Anthropic console. If cost becomes a concern, options include: reducing `PHOTOS_PER_DAY`, `RESTAURANTS_PER_DAY`, and `ATTRACTIONS_PER_DAY` constants in `app.py`; capping `max_tokens` on each scout call; switching to a smaller/cheaper model via `SCOUT_MODEL`; or implementing a stricter server-side cache (currently results are cached 1 hour per location+duration+preferences+client profile combination, so repeat searches are free).
