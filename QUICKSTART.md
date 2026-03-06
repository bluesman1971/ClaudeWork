# Quick Start Guide (5 minutes)

## TL;DR Setup

### Prerequisites
- Python 3.11+ installed
- Anthropic API key (from https://console.anthropic.com/)

### Run It (one terminal)

```bash
cd trip-guide-app

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and JWT_SECRET_KEY at minimum

# Create your admin account
python manage.py create-user --role admin

# Start the server (backend + frontend in one process)
uvicorn app:app --reload --port 8000
```

**Browser:** `http://localhost:8000`

---

## File Structure

```
trip-guide-app/
├── app.py                 ← FastAPI backend (serves frontend too)
├── auth.py                ← JWT auth + rate limiting
├── models.py              ← SQLAlchemy models (StaffUser, Client, GearProfile, Trip)
├── clients.py             ← Client CRM router
├── trips.py               ← Saved trips router
├── schemas.py             ← Pydantic request validation
├── ephemeris.py           ← Sunrise/golden-hour calculations
├── prompts.py             ← Claude prompt builders (Kelby-style)
├── tool_schemas.py        ← Claude structured output schema
├── redis_client.py        ← Redis session store + cache
├── manage.py              ← CLI for creating user accounts
├── frontend/
│   ├── index.html         ← HTML shell
│   └── src/               ← ES modules (main.js, clients.js, review.js, etc.)
├── tests/                 ← pytest test suite (59 tests)
├── requirements.txt       ← Python packages
├── .env                   ← Your config (created from .env.example)
└── ARCHITECTURE.md        ← Full developer reference
```

---

## Environment Setup

Open `.env` and fill in at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...your-key-here...
JWT_SECRET_KEY=any-long-random-string-here
```

Optional (but unlocks more features):
```bash
GOOGLE_PLACES_API_KEY=...    # enables location verification + ephemeris geocoding
REDIS_URL=redis://...         # shared session store (falls back to in-memory without it)
```

Generate a strong JWT secret:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## What Should Happen

1. Visit `http://localhost:8000`
2. Sign in with the account you created via `manage.py`
3. (Optional) Click **+ New** next to "Gear Profile" to add your camera kit
4. Fill in: destination, start/end dates, photography interests
5. Click **Generate Guide** — wait ~30–60 seconds (calling Anthropic API)
6. Review the Kelby-style photo location cards (toggle on/off)
7. Click **Generate Final Guide** → see your photography guide
8. Use **Save as PDF** to print it

---

## Common Issues

| Issue | Fix |
|-------|-----|
| `Connection refused :8000` | Is `uvicorn app:app --reload` running? |
| `Login failed` | Run `python manage.py create-user --role admin` to create an account |
| `Invalid API key` | Check `.env` has real key starting with `sk-ant-` |
| Guide generation hangs | Anthropic API call in progress — wait up to 60 seconds |
| Modal/form not opening | Hard refresh: **Cmd+Shift+R** (Mac) or **Ctrl+Shift+R** (Windows) |
| DB error on startup | Run `alembic upgrade head` to apply any pending migrations |

---

## Running Tests

```bash
source venv/bin/activate
pytest                    # all 59 tests (no API keys needed)
pytest -v                 # verbose output
pytest tests/test_ephemeris.py   # single module
```

Tests use an in-memory SQLite database and stub out Claude/Redis — no real API calls.

---

## Creating Additional Users

```bash
# Admin (can manage everything):
python manage.py create-user --role admin

# Regular staff:
python manage.py create-user --role staff
```

---

## Commands Reference

| Task | Command |
|------|---------|
| Start dev server | `uvicorn app:app --reload --port 8000` |
| Run tests | `pytest` |
| Create user | `python manage.py create-user --role admin` |
| Apply DB migrations | `alembic upgrade head` |
| Activate venv (Mac/Linux) | `source venv/bin/activate` |
| Activate venv (Windows) | `venv\Scripts\activate` |
| Deactivate venv | `deactivate` |

---

## Deploying to Railway

1. Push to `main` branch → Railway auto-deploys
2. Set env vars in Railway dashboard: `ANTHROPIC_API_KEY`, `JWT_SECRET_KEY`, `DATABASE_URL`, `FLASK_ENV=production`
3. Add the Railway Redis add-on (optional but recommended)
4. Leave the Railway **Start Command** field blank — the `Procfile` handles it

See `ARCHITECTURE.md` for full deployment details.
