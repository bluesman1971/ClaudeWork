# Trip Master — Photography Guide Builder

An internal tool for travel photographers. Enter a destination, shoot dates, photography interests, and your gear kit — get a Kelby-style photography guide with exact shoot windows, gear-specific settings, Google Earth links, and reality-check logistics. Generated in under a minute, powered by Claude AI.

## Architecture

```
Browser (ES modules)       FastAPI backend             Anthropic API
  :8000                      :8000
┌──────────────────┐    ┌────────────────────┐    ┌──────────────┐
│  Form + Review   │───>│  Photo Scout       │───>│ Claude Haiku │
│  Kelby Cards     │    │  Ephemeris Engine  │    │ (tool use)   │
│  Gear Profiles   │    │  Places Verify     │    └──────────────┘
└──────────────────┘    │  Finalize + HTML   │
                        └────────────────────┘
                               │
                         PostgreSQL (Supabase)
                         Redis (session store)
```

Backend and frontend are served from the same Railway URL — no separate deployment needed.

## Quick Start

See **[QUICKSTART.md](QUICKSTART.md)** for the 5-minute local setup.

**Short version:**
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add ANTHROPIC_API_KEY + JWT_SECRET_KEY
python manage.py create-user --role admin
uvicorn app:app --reload --port 8000
# Open http://localhost:8000
```

## Features

- **Kelby-style photo plans** — every location gets four sections: The Shot, The Setup, The Settings, The Reality Check
- **Gear-aware advice** — connect your camera body, lenses, and filters; get tailored settings and filter call-outs
- **Ephemeris-driven timing** — sunrise, sunset, golden hour, blue hour, and moon phase for each shoot day
- **Google Earth integration** — one-click street-level preview of every location
- **Client management** — save client profiles with preferences; guide is personalised to their travel style
- **Review + approve** — toggle locations on/off before generating the final guide
- **Saved trips** — finalized guides saved to database; reload any trip from the panel
- **PDF export** — browser-native print to PDF from the in-page preview

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.11) + Gunicorn/UvicornWorker |
| AI | Anthropic Claude Haiku via structured tool use |
| Ephemeris | `astral` 3.2 — sunrise/golden-hour/moon calculations |
| Database | PostgreSQL (Supabase) / SQLite (local dev) |
| Migrations | Alembic |
| Session store | Redis (with in-memory fallback) |
| Frontend | Vanilla ES modules, no build step |
| Auth | JWT in httpOnly cookie + CSRF header |
| Hosting | Railway.app |
| Tests | pytest + pytest-asyncio (59 tests) |

## Project Structure

```
trip-guide-app/
├── app.py              # Main FastAPI app — routes, photo scout, config
├── auth.py             # JWT auth, login, rate limiting
├── models.py           # StaffUser, Client, GearProfile, Trip
├── schemas.py          # Pydantic request/response validation
├── ephemeris.py        # Sunrise/golden-hour/moon calculations
├── prompts.py          # Claude prompt builder functions
├── tool_schemas.py     # Claude structured output schema (PHOTO_TOOL)
├── clients.py          # Client CRM router
├── trips.py            # Saved trips router
├── redis_client.py     # Redis session store + cache
├── database.py         # SQLAlchemy engine + get_db dependency
├── manage.py           # CLI: python manage.py create-user
├── frontend/
│   ├── index.html      # App shell
│   └── src/            # ES modules: main.js, clients.js, review.js, etc.
├── tests/              # pytest suite — 59 tests, no real API calls
├── migrations/         # Alembic migration versions
├── Procfile            # Railway: release (alembic) + web (gunicorn)
├── requirements.txt    # Pinned Python dependencies
└── .env.example        # Environment variable template
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | From console.anthropic.com |
| `JWT_SECRET_KEY` | **Yes** | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | Prod | PostgreSQL (Supabase Transaction Pooler, port 6543). SQLite default in dev |
| `REDIS_URL` | No | Railway Redis add-on sets this automatically |
| `FLASK_ENV` | Prod | Set to `production` on Railway (enables HSTS, secure cookies) |
| `GOOGLE_PLACES_API_KEY` | No | Location verification + ephemeris geocoding |
| `SCOUT_MODEL` | No | Defaults to `claude-haiku-4-5-20251001` |

## Running Tests

```bash
source venv/bin/activate
pytest               # 59 tests, all passing
pytest -v            # verbose
```

Tests use in-memory SQLite and stub all external APIs — no Anthropic key needed.

## Deployment (Railway)

1. Connect the GitHub repository to a Railway project
2. Set environment variables in the Railway dashboard
3. Add the Railway Redis add-on
4. Leave the **Start Command** field blank — the `Procfile` handles startup
5. Every `git push origin main` triggers an automatic redeploy

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the complete developer reference including all API routes, database schema, security measures, and maintenance runbook.

## API Overview

```
POST /auth/login          — sign in, returns httpOnly JWT cookie
GET  /auth/me             — current user profile
POST /generate            — start photo scout → { job_id }
GET  /jobs/{job_id}       — poll job status
POST /finalize            — build final HTML guide
POST /replace             — replace one photo location
GET/POST /gear-profiles   — manage gear vaults
PUT/DELETE /gear-profiles/{id}
GET/POST /clients         — client CRM
GET/POST /trips           — saved trip guides
GET /health               — health check
```

All state-changing routes require `X-Requested-With: XMLHttpRequest` (CSRF defence).
