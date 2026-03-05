# Trip Master → Photography Assistant: Pivot & Security Plan

## Overview

This document captures the full implementation plan for two parallel workstreams:
1. **Security hardening & code simplification** (pre-existing recommendations)
2. **Photography pivot** — evolving the app from a general travel guide into a dedicated, technical photographer's assistant

The photography pivot removes the restaurant and attraction scouts entirely and replaces the shallow photo scout with a deep, gear-aware, ephemeris-driven photography planning engine with a conversational "Kelby-style" voice.

**Status key:** `[ ]` Not started | `[~]` In progress | `[x]` Complete

---

## Key Decisions (Locked)

- **No Vite or build tools.** Frontend stays vanilla ES modules with no build step.
- **`astral` library** for ephemeris calculations (sunrise, sunset, golden hour, blue hour, moon phase).
- **Remove restaurant and attraction scouts** entirely. Dedicate full token budget to photography.
- **Prompts externalized** to `prompts.py` — never hardcoded in `app.py`.
- **Google Earth links** constructed server-side from GPS coordinates (parameterized URL).
- **GearProfile** stored as structured data on the user/client model (not free-text).

---

## Phase 1 — Security Hardening ✅ Complete
*Independent of the pivot. Completed 2026-03-05.*

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 1.1 | Tighten CORS — split dev/prod `allow_origins` via env var, remove localhost from production | `app.py` | `[x]` |
| 1.2 | Pin `anthropic` to a specific version (was `>=0.40.0`, pinned to `==0.80.0`) | `requirements.txt` | `[x]` |
| 1.3 | Audit logging calls — found and removed `user.email` from login log in `auth.py:285`. All other log calls use IDs and reference codes only (no PII). | `auth.py` | `[x]` |
| 1.4 | Add Content Security Policy (CSP) header to security middleware. Note: `script-src` retains `'unsafe-inline'` due to inline `onclick` handlers throughout `index.html` and dynamically built `innerHTML` in `review.js`. See TODO in `app.py` comment — remove when handlers are converted to `addEventListener` in Phase 4 frontend refactor. | `app.py` | `[x]` |

---

## Phase 2 — Database Schema Pivot ✅ Complete
*Completed 2026-03-05.*

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 2.1 | Add `GearProfile` model to `models.py` with fields: `camera_type` (enum), `lenses` (JSON array of focal length strings), `has_tripod` (bool), `has_filters` (JSON array: ND, polarizer, etc.), `has_gimbal` (bool) | `models.py` | `[x]` |
| 2.2 | Update `Trip` model — `duration` kept as nullable column for backward compat; `start_date`/`end_date` added as `Date` fields; `duration_days` property returns authoritative value from dates when set, falls back to stored integer. Added `gear_profile_id` FK. | `models.py` | `[x]` |
| 2.3 | Alembic migration `dcc6439ee150` — creates `gear_profiles` table, adds `trips.start_date`, `trips.end_date`, `trips.gear_profile_id`, makes `trips.duration` nullable. Uses `batch_alter_table` for full SQLite compat. | `migrations/versions/dcc6439ee150_*.py` | `[x]` |
| 2.4 | Added `GearProfileCreate` and `GearProfileUpdate` schemas. `GenerateRequest` and `TripCreate`/`TripUpdate` updated with `start_date`, `end_date`, `gear_profile_id`. Model validator resolves duration from dates or integer; validates date ordering and 14-day max. | `schemas.py` | `[x]` |

**GearProfile field reference:**
```python
camera_type: Enum("full_frame_mirrorless", "apsc_mirrorless", "apsc_dslr", "smartphone", "film_35mm", "film_medium_format")
lenses: JSON  # e.g. ["16-35mm f/2.8", "50mm f/1.8", "70-200mm f/4"]
has_tripod: Boolean
has_filters: JSON  # e.g. ["6-stop ND", "polarizer", "graduated ND"]
has_gimbal: Boolean  # smartphone/video stabilizer
```

---

## Phase 3 — Backend Pivot ✅ Complete
*Completed 2026-03-05.*

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 3.1 | Remove `call_restaurant_scout`, `call_attraction_scout` and all related endpoints/job logic from `app.py` | `app.py` | `[x]` |
| 3.2 | Remove restaurant and attraction tool schemas | `tool_schemas.py` | `[x]` |
| 3.3 | Create `prompts.py` — all prompt strings as module-level constants + builder functions | `prompts.py` (new) | `[x]` |
| 3.4 | Install `astral==3.2` and add to `requirements.txt` (pinned) | `requirements.txt` | `[x]` |
| 3.5 | Create `ephemeris.py` — GPS coords + date list → per-day sunrise/sunset/golden-hour/blue-hour/moon data via `astral`. `format_ephemeris_block()` serialises to prompt text. | `ephemeris.py` (new) | `[x]` |
| 3.6 | Rewrite photo scout prompt in `prompts.py` using Kelby-style 4-section structure | `prompts.py` | `[x]` |
| 3.7 | Update Claude tool schema — new fields: `lat`, `lng`, `the_shot`, `the_setup`, `the_settings`, `the_reality_check`, `shoot_window`, `required_gear`, `distance_from_accommodation` | `tool_schemas.py` | `[x]` |
| 3.8 | Add `google_earth_url(lat, lng, altitude=500)` helper — constructs parameterized Google Earth Web URL | `app.py` | `[x]` |
| 3.9 | Fix `httpx` client lifecycle — replaced `@app.on_event` with `@asynccontextmanager _lifespan()` passed to `FastAPI(lifespan=...)` | `app.py` | `[x]` |
| 3.10 | Wire ephemeris + gear profile into `_run_scouts_background`; update `/finalize` session key access; update `/replace` to photo-only | `app.py` | `[x]` |

**Note on 3.10 original scope:** "Remove unused Redis in-memory fallback code" was deferred — the fallback is still useful for local dev without Redis. Replaced with wiring ephemeris into the job runner, which was the more important work.  `_evict_sessions` kept for now.

**Kelby-style output structure per location:**
```
The Shot      — why this location, what makes it special at this time of year and light
The Setup     — specific gear from their vault, exact position, filter call-outs
The Settings  — concrete ISO/aperture/shutter starting point (or film equiv / phone mode)
The Reality Check — crowds, parking, access, sun direction at their specific shoot time/date
```

**Updated photo scout tool schema fields:**
```python
"location_name": str
"coordinates": {"lat": float, "lng": float}
"the_shot": str          # conversational intro
"the_setup": str         # gear-specific positioning
"the_settings": str      # technical settings calibrated to camera_type
"the_reality_check": str # logistics, crowds, access
"shoot_window": str      # e.g. "30 min before sunrise to 45 min after"
"required_gear": [str]   # items from vault needed for this shot
"google_earth_url": str  # constructed server-side
"distance_from_accommodation": str
```

**Google Earth URL format:**
```python
def google_earth_url(lat: float, lng: float, altitude: int = 500) -> str:
    return f"https://earth.google.com/web/@{lat},{lng},{altitude}a,800d,35y,0h,45t,0r"
```

---

## Phase 4 — Frontend Updates
*Vanilla JS edits only — no build tool. Estimated: 2–3 days.*

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 4.1 | Update `form.js` — replace duration stepper with `start_date`/`end_date` date pickers; add gear profile selector (load from account or inline quick-entry) | `frontend/src/form.js` | `[ ]` |
| 4.2 | Update `review.js` — render new 4-section card layout (Shot / Setup / Settings / Reality Check), add Google Earth link button, add ephemeris summary per day (golden hour time) | `frontend/src/review.js` | `[ ]` |
| 4.3 | Update `clients.js` — add gear profile CRUD (view, create, edit vault) | `frontend/src/clients.js` | `[ ]` |
| 4.4 | Update `main.css` — style new card layout, Google Earth button, ephemeris info strip, gear badge indicators | `frontend/src/main.css` | `[ ]` |
| 4.5 | Update `index.html` — remove dining/attractions section from form, add dates section, add gear profile section | `index.html` | `[ ]` |

---

## Phase 5 — Test Suite
*Start scaffold in Phase 3; fill out through Phase 4. Ongoing.*

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 5.1 | Set up `pytest` + `httpx.ASGITransport` scaffold | `tests/` (new dir), `tests/conftest.py` | `[ ]` |
| 5.2 | Mock `AsyncAnthropic` client with fixture responses (avoids live API costs in tests) | `tests/conftest.py` | `[ ]` |
| 5.3 | Auth flow tests — login, token refresh, rate limiting lockout | `tests/test_auth.py` | `[ ]` |
| 5.4 | Generate endpoint tests — returns job_id, background task queued | `tests/test_generate.py` | `[ ]` |
| 5.5 | Ephemeris unit tests — known location + date → expected golden hour time | `tests/test_ephemeris.py` | `[ ]` |
| 5.6 | Gear profile CRUD tests | `tests/test_clients.py` | `[ ]` |
| 5.7 | Finalize endpoint test — produces valid HTML with expected structure | `tests/test_finalize.py` | `[ ]` |

---

## Deferred / Rejected

| Item | Decision |
|------|----------|
| Unify scout logic (3 → 1 function) | **Moot** — restaurant and attraction scouts removed in pivot |
| Deepen Pydantic for scout outputs | **Deferred** — Claude tool-use already validates schema; revisit if bugs emerge |
| Move map handling to client-side | **Rejected** — base64 embedding is required for self-contained PDF output |
| Introduce Vite or build tooling | **Rejected** — vanilla ES modules are working; no build step is a feature |

---

## Session Notes

*Update this section at the end of each working session.*

| Date | Session Summary | Next Steps |
|------|----------------|------------|
| 2026-03-05 | Plan created. Security + photography pivot architecture finalized. | Start Phase 1 (CORS, pin deps) |
| 2026-03-05 | Phase 1 complete. CORS split dev/prod, `anthropic` pinned to `==0.80.0`, PII removed from login log, CSP header added to security middleware. `unsafe-inline` retained in `script-src` due to inline onclick handlers — flagged as TODO for Phase 4. | Start Phase 2 (DB schema: GearProfile model + Trip date fields) |
| 2026-03-05 | Phase 2 complete. `GearProfile` model + migration added. `Trip` model updated with `start_date`/`end_date`/`gear_profile_id`. `duration` made nullable (backward compat via `duration_days` property). `GearProfileCreate`/`Update` schemas added. `GenerateRequest` updated with date/gear fields + model validator. Migration uses `batch_alter_table` throughout for SQLite compat. | Start Phase 3 (backend pivot: remove restaurant/attraction scouts, add ephemeris engine, rewrite photo scout) |
| 2026-03-05 | Phase 3 complete. Restaurant/attraction scouts and tool schemas removed. `ephemeris.py` created (astral 3.2: sunrise/sunset/golden-hour/blue-hour/moon per day). `prompts.py` created (Kelby-style system + user prompt builders, gear-aware settings guidance, replace-endpoint prompts). `PHOTO_TOOL` rewritten with 12 Kelby-style fields. `google_earth_url()` helper added. `call_photo_scout()` rewritten with gear+ephemeris injection. `_run_scouts_background` updated (gear profile loaded from DB, ephemeris geocoded via Places API, photo-only scout tasks). FastAPI lifespan context manager replaces deprecated `on_event`. `/finalize` session key access hardened with `.get()` defaults. `/replace` restricted to photo type only. `generate_master_html` photo cards rewritten with 4-section Kelby layout + Google Earth links + gear badges. `astral==3.2` added to requirements.txt. | Start Phase 4 (frontend: date pickers, gear selector, Kelby card layout, Google Earth button) |

---

## Cost & Token Notes

- Current cost: ~$0.45/guide at ~150k tokens
- Deep photography guides (3–4 locations/day, Kelby-style detail) will likely run **2–4x** higher per request
- Gear-aware prompts + ephemeris data injection will increase prompt size
- Monitor actual token usage after first live runs and adjust item counts accordingly
