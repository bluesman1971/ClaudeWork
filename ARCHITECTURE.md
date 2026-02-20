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

---

## How the AI scouts work

The core feature is the `/generate` endpoint in `app.py`. When a consultant clicks "Generate", the app:

1. **Validates the request** — checks location, duration (1–14 days), and which categories are enabled (photos, restaurants, attractions)
2. **Runs up to 3 scouts in parallel** using `ThreadPoolExecutor(max_workers=3)`:
   - `call_photo_scout` — photography locations with timing, setup, and pro tips
   - `call_restaurant_scout` — dining recommendations with cuisine, price range, and booking notes
   - `call_attraction_scout` — sightseeing with practical visit info
3. **Each scout** sends a structured prompt to Claude Haiku and parses the response as JSON lines (one JSON object per line)
4. **Google Places verification** (if `GOOGLE_PLACES_API_KEY` is set) — each venue is verified via the Places Text Search API. Permanently closed venues are filtered out. This runs concurrently inside each scout using another `ThreadPoolExecutor`
5. **Retry logic** — if a scout returns 0 results (parse failure or all venues filtered), it retries up to 2 more times with a 1-second delay between attempts
6. **Session store** — verified results are stored in a server-side in-memory dict (`_session_store`) keyed by a UUID, with a 1-hour TTL. This lets `/finalize` retrieve the results without re-running the scouts
7. **`/finalize`** — takes the session ID, assembles the full HTML travel guide (including fetching Google Static Map images as base64 data URIs), and returns it to the browser

### In-memory caching
Scout results are cached in `_cache` (a plain dict) for 1 hour keyed on the combination of location, duration, and preferences. Empty results are never cached so a failed parse always retries fresh on the next request.

### Thread safety note
All three executor sites wrap their callables in `_with_app_context(fn, *args)`. This ensures each thread has its own independent Flask application context. Sharing a single AppContext across threads is not safe — each thread must push and pop its own.

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
Travel client records managed by staff.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| reference_code | String(20) | Auto-generated: CLT-001, CLT-002… |
| name | String(255) | Required |
| email, phone, company | String | Optional contact details |
| home_city | String(100) | For context in recommendations |
| preferred_budget | String(50) | e.g. "mid-range", "luxury" |
| travel_style | String(100) | e.g. "adventure", "relaxation" |
| notes, tags | Text/String | Freeform |
| is_deleted | Boolean | Soft-delete flag |
| created_by_id | FK → StaffUser | Which staff member created the record |
| created_at, updated_at | DateTime | UTC |

### `Trip`
Saved travel guide records.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK | Auto-increment |
| client_id | FK → Client | Optional (can be ungrouped) |
| created_by_id | FK → StaffUser | Which staff member generated it |
| location | String(200) | Destination name |
| duration_days | Integer | Trip length |
| status | String(20) | `draft`, `review`, `approved`, `delivered` |
| guide_data | JSON | Raw structured scout output |
| final_html | Text | Rendered HTML guide |
| is_deleted | Boolean | Soft-delete flag |
| created_at, updated_at | DateTime | UTC |

### Database configuration
`app.py` reads `DATABASE_URL` from the environment. A `_safe_db_url()` helper parses it through SQLAlchemy's `make_url()` before use — this correctly percent-encodes any special characters in the password, so the raw Supabase connection string can be pasted directly into Railway without manual URL-encoding.

For SQLite (local dev only), WAL (Write-Ahead Logging) mode is enabled on every new connection via `sqlalchemy.event.listen`. This allows concurrent readers and a single writer simultaneously, which prevents "database is locked" errors during local testing.

---

## Security measures

| Measure | Where | What it does |
|---|---|---|
| httpOnly JWT cookie | `auth.py` | JavaScript cannot read the auth token — prevents XSS token theft |
| `SameSite=Lax` cookie | `auth.py` | Browser won't send cookie on cross-site requests — primary CSRF defence |
| `X-Requested-With` CSRF header | `auth.py` + `index.html` | Secondary CSRF defence — all state-changing requests require this custom header, which browsers never attach automatically on cross-site requests |
| bcrypt password hashing | `auth.py` | Passwords stored as bcrypt hashes with 12 rounds — slow enough to resist brute force |
| Login rate limiting | `auth.py` | Max 10 failures per IP per 5 minutes, then 10-minute lockout |
| Generic auth error messages | `auth.py` | "Invalid email or password" — never reveals whether the email exists |
| HTTP security headers | `app.py` | `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`, HSTS (production only) |
| SSRF guard | `app.py` | `_fetch_static_map_as_base64()` only fetches from `https://maps.googleapis.com/` — blocks server-side request forgery |
| Generic error messages in API | `app.py` | `/generate` and `/finalize` return generic errors on failure — never leaks internal exception details to the browser |
| `.gitignore` | repo root | Excludes `.env`, `*.db`, `venv/`, `__pycache__/`, `.DS_Store`, backup files |

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
| GET | `/clients` | Lists all active clients |
| POST | `/clients` | Creates a new client |
| GET | `/clients/<id>` | Gets one client with their trips |
| PUT | `/clients/<id>` | Updates client fields |
| DELETE | `/clients/<id>` | Soft-deletes a client |
| GET | `/trips` | Lists all trips for current user |
| POST | `/trips` | Saves a trip guide |
| GET | `/trips/<id>` | Gets one trip |
| PUT | `/trips/<id>` | Updates trip fields/status |
| DELETE | `/trips/<id>` | Soft-deletes a trip |

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
4. `app.py` module-level code runs: loads env vars, configures the DB, registers blueprints, creates tables if they don't exist
5. Gunicorn starts serving on the Railway-assigned `$PORT`

### Gunicorn configuration explained
- `--workers 2` — 2 separate processes (each loads the full app)
- `--threads 4` — 4 threads per worker (handles concurrent requests within each process)
- `--worker-class gthread` — thread-based worker (required for `--threads` to work)
- `--timeout 120` — 120-second request timeout (needed because Claude API calls can take 30–60 seconds)
- `--access-logfile -` / `--error-logfile -` — logs to stdout/stderr so Railway captures them

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

- **In-memory session store and cache** — `_session_store` and `_cache` in `app.py` are plain Python dicts. They reset on every deploy and are not shared between Gunicorn workers. For a multi-worker setup this can cause "session not found" errors if the `/finalize` request hits a different worker than `/generate`. A Redis store (e.g. Railway Redis add-on) would fix this properly.

- **In-memory login rate limiter** — resets on restart, and is per-worker (not shared across workers). Good enough for defence-in-depth, but a Redis-backed limiter would be more robust.

- **No database migrations** — `db.create_all()` creates tables on startup but does not handle schema changes to existing tables. If you add a column to a model, you will need to either drop and recreate the table (losing data) or run an `ALTER TABLE` manually in the Supabase SQL editor. A proper migration tool (Flask-Migrate / Alembic) would be the right long-term solution.

- **Single-file frontend** — `index.html` is a large single file containing all HTML, CSS, and JavaScript. It works well but would benefit from being split into components if the UI grows significantly.

- **No email delivery** — there is no password reset flow. Forgotten passwords require an admin to create a new account or update the hash directly in the database.

- **AI provider is single-vendor and single-model** — all three scouts use the same Anthropic model, set via the `SCOUT_MODEL` environment variable. Switching to a different Anthropic model is trivial (just update the env var). Switching to a different provider (OpenAI, Google Gemini, etc.) requires changing the client initialisation in `app.py` and updating the API call and response extraction in each of the three scout functions — roughly 6–8 lines of code plus a `requirements.txt` change. The prompts, retry logic, and JSON parsing are all provider-agnostic and would not need to change. If per-scout model selection is ever needed (e.g. a cheaper model for restaurants, a smarter one for photos), separate env vars (`PHOTO_SCOUT_MODEL`, `RESTAURANT_SCOUT_MODEL`, etc.) would be the clean way to add that.

- **API cost** — Claude Haiku is the cheapest Anthropic model and was chosen deliberately to keep per-generation costs low, but each "Generate" click makes three separate API calls (one per scout), each with a large prompt and up to ~6,000 output tokens. High-volume usage will accumulate meaningful API costs. Monitor usage in the Anthropic console. If cost becomes a concern, options include: reducing `PHOTOS_PER_DAY`, `RESTAURANTS_PER_DAY`, and `ATTRACTIONS_PER_DAY` constants in `app.py`; capping `max_tokens` on each scout call; switching to a smaller/cheaper model via `SCOUT_MODEL`; or implementing a stricter server-side cache (currently results are cached 1 hour per location+duration+preferences combination, so repeat searches are free).
