#!/usr/bin/env python3
"""
Trip Master Web App — Backend API (FastAPI, async)

Phase 2: Flask → FastAPI migration.
- AsyncAnthropic replaces sync Anthropic (no thread blocking on AI calls)
- httpx.AsyncClient replaces urllib.request (no thread blocking on HTTP)
- asyncio.gather replaces ThreadPoolExecutor for all three parallel workloads
- Pydantic v2 schemas replace all manual _sanitise_line / _clamp validation
- Depends(get_current_user) replaces the @require_auth decorator
- run_in_threadpool wraps synchronous SQLAlchemy / bcrypt calls
- No Flask app context required anywhere — _with_app_context deleted
"""

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.parse
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from html import escape

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from auth import (
    COOKIE_NAME,
    TOKEN_TTL_H,
    auth_router,
    check_user_rate_limit,
    get_current_user,
)
from clients import clients_router
from database import engine, get_db, SessionLocal
from models import Client, StaffUser, Trip, db
from redis_client import get_redis
from schemas import FinalizeRequest, GenerateRequest, ReplaceRequest
from trips import trips_router

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(__file__)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title='Trip Master API', docs_url=None, redoc_url=None)

# ── CORS ─────────────────────────────────────────────────────────────────────
_cors_origins = [
    o.strip()
    for o in os.getenv(
        'CORS_ORIGINS', 'http://localhost:5000,http://127.0.0.1:5000'
    ).split(',')
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── Security headers ──────────────────────────────────────────────────────────
@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options']  = 'nosniff'
    response.headers['X-Frame-Options']          = 'DENY'
    response.headers['X-XSS-Protection']         = '1; mode=block'
    response.headers['Referrer-Policy']           = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']        = 'geolocation=(), microphone=(), camera=()'
    if os.getenv('FLASK_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# ── Sliding JWT cookie ────────────────────────────────────────────────────────
@app.middleware('http')
async def slide_auth_cookie(request: Request, call_next):
    """Re-issue the auth cookie with a fresh TTL after each authenticated request."""
    response = await call_next(request)
    token = getattr(request.state, 'slide_token', None)
    if token:
        response.set_cookie(
            COOKIE_NAME, token,
            httponly=True,
            samesite='lax',
            secure=(os.getenv('FLASK_ENV') == 'production'),
            max_age=TOKEN_TTL_H * 3600,
            path='/',
        )
    return response


# ── Map HTTPException → { "error": "..." } ────────────────────────────────────
# FastAPI's default shape is { "detail": "..." }; the frontend expects "error".
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={'error': exc.detail})


# ── Router registration ───────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(clients_router)
app.include_router(trips_router)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOUT_MODEL       = os.getenv('SCOUT_MODEL',       'claude-haiku-4-5-20251001')
SCOUT_MODEL_LABEL = os.getenv('SCOUT_MODEL_LABEL', 'Claude Haiku 4.5')

PHOTOS_PER_DAY      = 3
RESTAURANTS_PER_DAY = 3
ATTRACTIONS_PER_DAY = 4

GOOGLE_PLACES_API_KEY  = os.getenv('GOOGLE_PLACES_API_KEY', '')
PLACES_VERIFY_ENABLED  = bool(GOOGLE_PLACES_API_KEY)
PLACES_API_URL         = 'https://places.googleapis.com/v1/places:searchText'
PLACES_VERIFY_TIMEOUT  = 5   # seconds

STATUS_OPERATIONAL        = 'OPERATIONAL'
STATUS_CLOSED_TEMPORARILY = 'CLOSED_TEMPORARILY'
STATUS_CLOSED_PERMANENTLY = 'CLOSED_PERMANENTLY'
STATUS_UNVERIFIED         = 'UNVERIFIED'

CACHE_TTL_SECONDS   = 3600
SESSION_TTL_SECONDS = 3600

SCOUT_MAX_RETRIES = 2
SCOUT_RETRY_DELAY = 1.0

MAX_EXCLUDE_NAME_LEN = 100
MAX_EXCLUDE_LIST_LEN = 50

COLOR_PALETTES = {
    'barcelona': {'primary': '#c41e3a', 'accent': '#f4a261', 'secondary': '#2a9d8f', 'neutral': '#f5e6d3'},
    'paris':     {'primary': '#1a1a2e', 'accent': '#d4a574', 'secondary': '#16213e', 'neutral': '#f0e6d2'},
    'tokyo':     {'primary': '#8B0000', 'accent': '#FFD700', 'secondary': '#1a1a1a', 'neutral': '#f5f5f5'},
    'default':   {'primary': '#2c3e50', 'accent': '#e67e22', 'secondary': '#34495e', 'neutral': '#ecf0f1'},
}

# ---------------------------------------------------------------------------
# In-memory fallbacks (used when Redis is unavailable)
# ---------------------------------------------------------------------------

_cache: dict         = {}
_session_store: dict = {}
_jobs: dict          = {}   # job-state fallback when Redis is unreachable

JOB_TTL_SECONDS = 3600      # 1 hour — same as session TTL

# ---------------------------------------------------------------------------
# HTTP client singleton (shared across requests)
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

anthropic_client = AsyncAnthropic()

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event('startup')
async def startup():
    global _http_client
    _http_client = httpx.AsyncClient(
        timeout=10,
        headers={'User-Agent': 'TripGuideApp/1.0'},
    )

    # Create DB tables + run column migrations
    await run_in_threadpool(_init_db)

    # Redis connectivity check (visible in Railway logs at WARNING level)
    r = get_redis()
    if r is not None:
        logger.warning('Redis connected and ready (session store, cache, rate limiters active)')
    else:
        logger.warning('Redis unavailable — using in-memory fallbacks (set REDIS_URL to enable)')


@app.on_event('shutdown')
async def shutdown():
    if _http_client:
        await _http_client.aclose()


def _init_db():
    """Create all tables and run ADD COLUMN migrations. Called once at startup."""
    db.metadata.create_all(engine)

    migrations = [
        'ALTER TABLE clients ADD COLUMN IF NOT EXISTS dietary_requirements TEXT',
        'ALTER TABLE trips ADD COLUMN IF NOT EXISTS accommodation VARCHAR(500)',
    ]
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            for sql in migrations:
                conn.execute(text(sql))
            conn.commit()
    except Exception as exc:
        # SQLite doesn't support IF NOT EXISTS on ADD COLUMN
        logger.warning('Column migration skipped (likely SQLite): %s', exc)


# ---------------------------------------------------------------------------
# Cache helpers (Redis + in-memory fallback)
# ---------------------------------------------------------------------------

def _cache_key(*args) -> str:
    raw = json.dumps(args, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key: str):
    r = get_redis()
    if r is not None:
        try:
            raw = r.get(f'cache:{key}')
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:
            logger.warning('Redis cache GET error: %s', exc)
        return None
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]
    return None


def _set_cached(key: str, value) -> None:
    r = get_redis()
    if r is not None:
        try:
            r.setex(f'cache:{key}', CACHE_TTL_SECONDS, json.dumps(value))
        except Exception as exc:
            logger.warning('Redis cache SET error: %s', exc)
        return
    _cache[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# Session helpers (Redis + in-memory fallback)
# ---------------------------------------------------------------------------

def _evict_sessions() -> None:
    r = get_redis()
    if r is not None:
        return
    cutoff  = time.time() - SESSION_TTL_SECONDS
    expired = [k for k, v in _session_store.items() if v.get('ts', 0) < cutoff]
    for k in expired:
        del _session_store[k]
    if expired:
        logger.info('Evicted %d expired review session(s)', len(expired))


def _session_set(session_id: str, payload: dict) -> None:
    r = get_redis()
    if r is not None:
        try:
            r.setex(f'session:{session_id}', SESSION_TTL_SECONDS, json.dumps(payload))
            return
        except Exception as exc:
            logger.warning('Redis session SET error: %s — falling back to memory', exc)
    _session_store[session_id] = {**payload, 'ts': time.time()}


def _session_get(session_id: str) -> dict | None:
    r = get_redis()
    if r is not None:
        try:
            raw = r.get(f'session:{session_id}')
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:
            logger.warning('Redis session GET error: %s — trying memory', exc)
        entry = _session_store.get(session_id)
        if entry and (time.time() - entry.get('ts', 0)) < SESSION_TTL_SECONDS:
            return entry
        return None
    entry = _session_store.get(session_id)
    if entry and (time.time() - entry.get('ts', 0)) < SESSION_TTL_SECONDS:
        return entry
    return None


# ---------------------------------------------------------------------------
# Job store helpers (Redis + in-memory fallback)
# ---------------------------------------------------------------------------

def _job_set(job_id: str, payload: dict) -> None:
    """Write (or overwrite) a job state record with a 1-hour TTL."""
    r = get_redis()
    if r is not None:
        try:
            r.setex(f'job:{job_id}', JOB_TTL_SECONDS, json.dumps(payload))
            return
        except Exception as exc:
            logger.warning('Redis job SET error: %s', exc)
    _jobs[job_id] = {**payload, '_ts': time.time()}


def _job_get(job_id: str) -> dict | None:
    r = get_redis()
    if r is not None:
        try:
            raw = r.get(f'job:{job_id}')
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:
            logger.warning('Redis job GET error: %s', exc)
        return None
    entry = _jobs.get(job_id)
    if entry and (time.time() - entry.get('_ts', 0)) < JOB_TTL_SECONDS:
        return {k: v for k, v in entry.items() if k != '_ts'}
    return None


def _job_update(job_id: str, fields: dict) -> None:
    """Merge fields into an existing job record (read → modify → write)."""
    existing = _job_get(job_id) or {}
    existing.update(fields)
    _job_set(job_id, existing)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def get_color_palette(location: str) -> dict:
    key = location.lower().split(',')[0].strip()
    return COLOR_PALETTES.get(key, COLOR_PALETTES['default'])


def _parse_json_lines(text: str, scout_name: str) -> list:
    results = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning('%s: failed to parse JSON line (%s): %r', scout_name, exc, line[:120])
    return results


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lng2 - lng1)
    a = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _format_distance(metres: float) -> str:
    walk_min = max(1, round(metres / 80))
    if metres < 150:
        return f'~{round(metres / 10) * 10} m · ~{walk_min} min walk'
    if metres < 1000:
        return f'~{round(metres / 50) * 50} m · ~{walk_min} min walk'
    km = metres / 1000
    return f'~{km:.1f} km · ~{walk_min} min walk'


def _apply_distances(items: list, acc_lat: float, acc_lng: float) -> None:
    for item in items:
        lat = item.get('_lat')
        lng = item.get('_lng')
        if lat is not None and lng is not None:
            metres = _haversine_m(acc_lat, acc_lng, lat, lng)
            item['travel_time'] = _format_distance(metres)


# ---------------------------------------------------------------------------
# Google Places verification (async)
# ---------------------------------------------------------------------------

async def verify_place_with_google(name: str, address: str, location_context: str) -> dict:
    """Query the Google Places API to verify a place is still operational."""
    _unverified = {'status': STATUS_UNVERIFIED, 'maps_url': None, 'place_id': None, 'lat': None, 'lng': None}
    if not PLACES_VERIFY_ENABLED:
        return _unverified

    query = ', '.join(p for p in [name, address, location_context] if p)
    payload = {'textQuery': query, 'maxResultCount': 1}
    headers = {
        'Content-Type':   'application/json',
        'X-Goog-Api-Key': GOOGLE_PLACES_API_KEY,
        'X-Goog-FieldMask': 'places.id,places.displayName,places.businessStatus,places.googleMapsUri,places.location',
    }
    try:
        resp = await _http_client.post(
            PLACES_API_URL,
            json=payload,
            headers=headers,
            timeout=PLACES_VERIFY_TIMEOUT,
        )
        data   = resp.json()
        places = data.get('places', [])
        if not places:
            logger.info('Places API: no result for %r', query[:60])
            return _unverified

        place           = places[0]
        business_status = place.get('businessStatus', STATUS_UNVERIFIED)
        place_id        = place.get('id')
        maps_url        = place.get('googleMapsUri')
        loc             = place.get('location', {})
        lat             = loc.get('latitude')
        lng             = loc.get('longitude')

        if business_status not in (STATUS_OPERATIONAL, STATUS_CLOSED_TEMPORARILY, STATUS_CLOSED_PERMANENTLY):
            business_status = STATUS_UNVERIFIED

        logger.info('Places API: %r → %s (place_id=%s lat=%s lng=%s)',
                    query[:60], business_status, place_id, lat, lng)
        return {'status': business_status, 'maps_url': maps_url, 'place_id': place_id,
                'lat': lat, 'lng': lng}

    except Exception as exc:
        logger.warning('Places API error for %r: %s', query[:60], exc)
        return _unverified


async def verify_places_batch(items: list, name_key: str, address_key: str, location_context: str):
    """
    Run Places verification for all items concurrently via asyncio.gather.
    Attaches _status, _maps_url, _place_id, _lat, _lng to each item.
    Removes permanently / temporarily closed items.
    Returns (verified_items, removed_count).
    """
    if not items:
        return items, 0

    results = await asyncio.gather(
        *[verify_place_with_google(
              item.get(name_key, ''),
              item.get(address_key, ''),
              location_context,
          ) for item in items],
        return_exceptions=True,
    )

    UNAVAILABLE = {STATUS_CLOSED_PERMANENTLY, STATUS_CLOSED_TEMPORARILY}
    for item, result in zip(items, results):
        if isinstance(result, Exception):
            logger.warning('Verification gather error: %s', result)
            result = {'status': STATUS_UNVERIFIED, 'maps_url': None, 'place_id': None, 'lat': None, 'lng': None}
        item['_status']   = result['status']
        item['_maps_url'] = result['maps_url']
        item['_place_id'] = result['place_id']
        item['_lat']      = result.get('lat')
        item['_lng']      = result.get('lng')

    verified = [i for i in items if i.get('_status') not in UNAVAILABLE]
    removed  = len(items) - len(verified)
    if removed:
        perm = sum(1 for i in items if i.get('_status') == STATUS_CLOSED_PERMANENTLY)
        temp = sum(1 for i in items if i.get('_status') == STATUS_CLOSED_TEMPORARILY)
        logger.info(
            'Places verification: removed %d unavailable location(s) from %d candidates '
            '(%d permanently closed, %d temporarily closed)',
            removed, len(items), perm, temp,
        )
    return verified, removed


async def _geocode_accommodation(address: str):
    """Look up lat/lng of accommodation address via Places API. Returns (lat, lng) or (None, None)."""
    if not PLACES_VERIFY_ENABLED or not address:
        return None, None
    try:
        resp = await _http_client.post(
            PLACES_API_URL,
            json={'textQuery': address, 'maxResultCount': 1},
            headers={
                'Content-Type':     'application/json',
                'X-Goog-Api-Key':   GOOGLE_PLACES_API_KEY,
                'X-Goog-FieldMask': 'places.location',
            },
            timeout=PLACES_VERIFY_TIMEOUT,
        )
        places = resp.json().get('places', [])
        if not places:
            return None, None
        loc = places[0].get('location', {})
        lat, lng = loc.get('latitude'), loc.get('longitude')
        if lat is not None and lng is not None:
            logger.info('Accommodation geocoded: %r → (%.5f, %.5f)', address[:60], lat, lng)
            return float(lat), float(lng)
    except Exception as exc:
        logger.warning('Accommodation geocoding failed for %r: %s', address[:60], exc)
    return None, None


# ---------------------------------------------------------------------------
# Static map helpers (async)
# ---------------------------------------------------------------------------

async def _fetch_static_map_as_base64(img_url: str) -> str | None:
    """Fetch a Google Static Maps image and return it as a base64 data URI."""
    if not img_url.startswith('https://maps.googleapis.com/'):
        logger.error('SSRF guard: blocked outbound fetch to unauthorized URL: %s', img_url[:80])
        return None
    try:
        resp         = await _http_client.get(img_url, timeout=8)
        content_type = resp.headers.get('content-type', 'image/png').split(';')[0].strip()
        b64          = base64.b64encode(resp.content).decode('ascii')
        return f'data:{content_type};base64,{b64}'
    except Exception as exc:
        logger.warning('Static map fetch failed: %s', exc)
        return None


def _build_static_map_url(day_items: list):
    """
    Build (img_url, maps_link, location_list_html) for one day's items.
    Returns None if there are no verified coordinates.
    """
    if not GOOGLE_PLACES_API_KEY or not day_items:
        return None

    pinned = [
        (i['_lat'], i['_lng'], i.get('name', ''))
        for i in day_items
        if i.get('_lat') is not None and i.get('_lng') is not None
    ]

    all_names = [i.get('name', '') for i in day_items if i.get('name')]
    location_list_html = (
        '<ol class="day-map-print-list">'
        + ''.join(f'<li>{escape(n)}</li>' for n in all_names)
        + '</ol>'
    ) if all_names else ''

    if not pinned:
        return None

    labels       = '123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    marker_parts = []
    for idx, (lat, lng, _) in enumerate(pinned):
        label = labels[idx] if idx < len(labels) else 'X'
        marker_parts.append(f'color:red|label:{label}|{lat},{lng}')

    zoom       = 15 if len(pinned) == 1 else (14 if len(pinned) <= 3 else 13)
    params_str = urllib.parse.urlencode({'size': '900x380', 'scale': '2', 'zoom': zoom, 'key': GOOGLE_PLACES_API_KEY})
    for mp in marker_parts:
        params_str += '&markers=' + urllib.parse.quote(mp, safe='')
    img_url = f'https://maps.googleapis.com/maps/api/staticmap?{params_str}'

    all_coords = '|'.join(f'{lat},{lng}' for lat, lng, _ in pinned)
    maps_link  = escape(
        'https://www.google.com/maps/search/?'
        + urllib.parse.urlencode({'api': '1', 'query': all_coords})
    )
    return img_url, maps_link, location_list_html


async def prefetch_day_maps(sections_by_day: dict) -> dict:
    """
    Pre-fetch all static map images concurrently via asyncio.gather.

    sections_by_day: { map_key: day_items_list }
    Returns: { map_key: (data_uri_or_None, maps_link, location_list_html) }
    """
    url_map = {}
    for key, day_items in sections_by_day.items():
        result = _build_static_map_url(day_items)
        if result:
            url_map[key] = result

    if not url_map:
        return {}

    async def _fetch_one(key, img_url, maps_link, location_list_html):
        data_uri = await _fetch_static_map_as_base64(img_url)
        return key, data_uri, maps_link, location_list_html

    raw_results = await asyncio.gather(
        *[_fetch_one(k, img_url, maps_link, loc_list)
          for k, (img_url, maps_link, loc_list) in url_map.items()],
        return_exceptions=True,
    )

    fetched = {}
    for r in raw_results:
        if isinstance(r, Exception):
            logger.warning('Map prefetch error: %s', r)
            continue
        key, data_uri, maps_link, location_list_html = r
        fetched[key] = (data_uri, maps_link, location_list_html)

    logger.info('Prefetched %d/%d day map images', sum(1 for v in fetched.values() if v[0]), len(url_map))
    return fetched


# ---------------------------------------------------------------------------
# Scout functions (async)
# ---------------------------------------------------------------------------

async def call_photo_scout(location, duration, interests, distance, per_day=None,
                            accommodation=None, pre_planned=None, client_profile=None):
    """Call Claude asynchronously to generate photography locations."""
    if per_day is None:
        per_day = PHOTOS_PER_DAY
    key = _cache_key('photo', location, duration, interests, distance, per_day,
                     accommodation, pre_planned,
                     json.dumps(client_profile or {}, sort_keys=True))
    cached = _get_cached(key)
    if cached is not None:
        logger.info('Photo Scout: cache hit for %s', location)
        return key, cached

    count = duration * per_day

    accommodation_block = (
        f'- Accommodation / travel base: {accommodation}\n'
        f'  Distance and logistics notes must be calculated from this address, not the city centre.\n'
        if accommodation else
        '- Accommodation: not specified — use city centre as the assumed travel base.\n'
    )
    pre_planned_block = (
        f'Already planned / committed:\n  {pre_planned}\n'
        f'  Do NOT suggest anything that duplicates or conflicts with the above.\n'
        if pre_planned else ''
    )
    profile = client_profile or {}
    profile_lines = []
    if profile.get('travel_style'):
        profile_lines.append(f"  Travel style: {profile['travel_style']}")
    if profile.get('preferred_budget'):
        profile_lines.append(f"  Budget tier: {profile['preferred_budget']}")
    if profile.get('home_city'):
        profile_lines.append(f"  Home city: {profile['home_city']} — avoid suggesting things they can easily do at home")
    if profile.get('dietary_requirements'):
        profile_lines.append(f"  Dietary requirements: {profile['dietary_requirements']} — respect these if any location involves food")
    if profile.get('notes'):
        profile_lines.append(f"  Consultant notes: {profile['notes']}")
    client_block = (
        'Client profile:\n' + '\n'.join(profile_lines) + '\n'
        if profile_lines else
        'Client profile: none provided — give broadly appealing recommendations.\n'
    )

    system_prompt = f"""You are a photography location scout writing practical, no-nonsense shooting guides.
Your recommendations are personalised to a specific client. Read their profile carefully and let it
shape every choice — location difficulty, walk distance, time of day, and subject matter.

PERSONALISATION RULES:
- If a travel style or interest is given, weight recommendations to match it. An adventure traveller
  gets rooftop access and early-morning spots; a relaxed traveller gets café terraces and parks.
- If a budget tier is given, factor in any access costs (paid viewpoints, permits, guided tours).
- If a home city is given, skip locations that are similar to what they have at home — surprise them.
- If accommodation is given, state the approximate walking or transit time from that address for
  each location. Use real street-level logic, not straight-line distance.
- If pre-planned commitments are listed, do NOT suggest those locations or anything that would
  duplicate them. Reference them only if suggesting a nearby complementary spot.
- If consultant notes mention physical limitations or other constraints, honour them absolutely.

WRITING STYLE — follow this strictly:
- Write like a knowledgeable friend giving honest advice, not a brochure.
- Lead every field with the useful fact. No filler openers ("Nestled in...", "Boasting...").
- Be specific, not superlative. Name what you actually see, not how it makes you feel.
- No stacked adjectives ("stunning, vibrant, unforgettable"). One earned adjective beats three vague ones.
- Practical over poetic. Timing, light direction, and where to stand are more useful than atmosphere words.
- Acknowledge trade-offs honestly. If it's crowded, say so and say when it isn't.
- Short sentences. Vary the rhythm. Cut every word that doesn't earn its place.
- Forbidden words: stunning, breathtaking, magical, enchanting, iconic, world-class, vibrant,
  nestled, boasting, hidden gem, off the beaten path, a feast for the senses, evocative, timeless.

OUTPUT FORMAT — return EXACTLY this JSON schema for each location, one object per line, no markdown:
{{
  "day": [day number],
  "time": "[time range, e.g., 6:30-7:30am]",
  "name": "[Exact location name]",
  "address": "[Full street address or neighbourhood]",
  "coordinates": "[latitude, longitude or area description]",
  "travel_time": "[Approx travel time from accommodation, e.g., '8 min walk' or '12 min metro'. Write 'N/A' if no accommodation was given.]",
  "subject": "[1-2 sentences: what you are pointing the camera at and why it works for this client's interests. Specific — name the building, the gap between structures, the reflection pool.]",
  "setup": "[2-3 sentences: where to stand, focal length, aperture if relevant, framing technique. Practical instructions a photographer can act on immediately.]",
  "light": "[2 sentences: light direction, best window, what changes after that window closes. Facts, not poetry.]",
  "pro_tip": "[1-2 sentences: one honest, actionable tip — crowd timing, a less-obvious angle, a technical setting, a seasonal caveat. Personalise to the client if possible.]"
}}"""

    user_prompt = f"""Generate {count} photography locations ({per_day} per day), spread across {duration} days.

Trip details:
- Destination: {location}
- Duration: {duration} days
- Photography interests: {interests}
- Max travel radius: {distance}
{accommodation_block}
{pre_planned_block}
{client_block}
Provide {count} complete JSON objects, one per line. No markdown, no other text."""

    message = await anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=6000,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_prompt}],
    )

    locations = _parse_json_lines(message.content[0].text, 'Photo Scout')
    logger.info('Photo Scout: parsed %d/%d locations for %s', len(locations), count, location)
    return key, locations


async def call_restaurant_scout(location, duration, cuisines, budget, distance, per_day=None,
                                 accommodation=None, pre_planned=None, client_profile=None):
    """Call Claude asynchronously to generate restaurant recommendations."""
    if per_day is None:
        per_day = RESTAURANTS_PER_DAY
    key = _cache_key('restaurant', location, duration, cuisines, budget, distance, per_day,
                     accommodation, pre_planned,
                     json.dumps(client_profile or {}, sort_keys=True))
    cached = _get_cached(key)
    if cached is not None:
        logger.info('Restaurant Scout: cache hit for %s', location)
        return key, cached

    count = duration * per_day

    accommodation_block = (
        f'- Accommodation / travel base: {accommodation}\n'
        f'  Distance notes must be calculated from this address, not the city centre.\n'
        if accommodation else
        '- Accommodation: not specified — use city centre as the assumed travel base.\n'
    )
    pre_planned_block = (
        f'Already planned / committed:\n  {pre_planned}\n'
        f'  Do NOT suggest any restaurant that duplicates or conflicts with the above.\n'
        f'  If a meal slot is clearly covered by a pre-planned event, skip that slot rather than\n'
        f'  adding a competing recommendation.\n'
        if pre_planned else ''
    )
    profile = client_profile or {}
    profile_lines = []
    if profile.get('travel_style'):
        profile_lines.append(f"  Travel style: {profile['travel_style']}")
    if profile.get('preferred_budget'):
        profile_lines.append(f"  Budget preference: {profile['preferred_budget']} — let this shape price tier selection")
    if profile.get('home_city'):
        profile_lines.append(f"  Home city: {profile['home_city']} — avoid chain restaurants or cuisine types they can get easily at home; prioritise genuinely local dishes and independent restaurants")
    if profile.get('dietary_requirements'):
        profile_lines.append(f"  Dietary requirements: {profile['dietary_requirements']} — HARD CONSTRAINT. Never suggest a restaurant or dish that conflicts with these. Verify menu compatibility before recommending.")
    if profile.get('notes'):
        profile_lines.append(f"  Consultant notes: {profile['notes']}")
    client_block = (
        'Client profile:\n' + '\n'.join(profile_lines) + '\n'
        if profile_lines else
        'Client profile: none provided — give broadly appealing recommendations.\n'
    )

    system_prompt = f"""You are a dining guide writer producing clear, honest restaurant recommendations
personalised to a specific client. Read their profile carefully — it should shape every pick.

PERSONALISATION RULES:
- Cuisine preferences are a starting point, not a ceiling. If the client profile reveals a travel
  style or home city that suggests other good fits, include them and explain why.
- Budget preference overrides the form budget if they conflict — the client's preference wins.
- Home city: if given, skip chains or cuisine types they can get easily at home. Lean into
  what is genuinely local to the destination and hard to replicate elsewhere.
- Accommodation: if given, state approximate walking or transit time from that address to each
  restaurant. Use realistic street-level logic.
- DIETARY REQUIREMENTS are absolute. If given, every restaurant and every suggested dish must
  be compatible. Do not suggest a seafood restaurant to someone with a shellfish allergy.
  Do not suggest meat dishes to a vegetarian. Verify before recommending.
- Pre-planned meals: if a dinner reservation is already committed, do not add another dinner
  recommendation that day — fill other slots instead, or note the day is covered.
- Vary price tier across the day: don't make every meal fine dining or every meal street food
  unless the profile specifically calls for that.

WRITING STYLE — follow this strictly:
- Write like a knowledgeable local, not a food critic trying to sound important.
- Lead with what the place is and what's good. Not how it makes you feel.
- Be specific: name the dish, the style, the price point. No vague praise.
- Ambiance: one plain sentence. What you actually find when you walk in.
- Honest about trade-offs. Mention queues, cash-only, noise, or reservation difficulty if relevant.
- Short sentences. No stacked adjectives. No filler.
- Forbidden words: culinary journey, gastronomic, tantalise, exquisite, artisanal, world-class,
  iconic, hidden gem, vibrant, buzzing, a feast for the senses, unforgettable.

OUTPUT FORMAT — return EXACTLY this JSON schema for each restaurant, one object per line, no markdown:
{{
  "day": [day number],
  "meal_type": "[breakfast/lunch/dinner]",
  "name": "[Restaurant name]",
  "address": "[Full address]",
  "location": "[Neighbourhood]",
  "cuisine": "[Cuisine type]",
  "travel_time": "[Approx travel time from accommodation, e.g., '5 min walk' or '10 min taxi'. Write 'N/A' if no accommodation was given.]",
  "description": "[2 sentences: what the place is and what to order. Specific — name the dish.]",
  "price": "[$/$$/$$$/$$$$]",
  "signature_dish": "[The one dish most worth ordering]",
  "ambiance": "[1 sentence: what you find when you walk in — noise level, seating, clientele, formality.]",
  "hours": "[Hours of operation]",
  "why_this_client": "[1 sentence: specifically why this pick suits this client's profile. If no profile was given, write why it suits the stated cuisine/budget preferences.]",
  "insider_tip": "[1-2 sentences: reservation advice, best seat, timing, or one thing most visitors miss.]"
}}

Price scale: $ = budget/street food, $$ = moderate, $$$ = moderately expensive, $$$$ = fine dining / splurge."""

    user_prompt = f"""Generate {count} restaurant recommendations ({per_day} per day across {duration} days),
covering breakfast, lunch, and dinner in a sensible rotation.

Trip details:
- Destination: {location}
- Duration: {duration} days
- Cuisine preferences stated by consultant: {cuisines}
- Budget range: {budget}
- Max travel radius: {distance}
{accommodation_block}
{pre_planned_block}
{client_block}
Provide {count} complete JSON objects, one per line. No markdown, no other text."""

    message = await anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=5000,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_prompt}],
    )

    restaurants = _parse_json_lines(message.content[0].text, 'Restaurant Scout')
    logger.info('Restaurant Scout: parsed %d/%d restaurants for %s', len(restaurants), count, location)
    return key, restaurants


async def call_attraction_scout(location, duration, categories, budget, distance, per_day=None,
                                 accommodation=None, pre_planned=None, client_profile=None):
    """Call Claude asynchronously to generate attraction recommendations."""
    if per_day is None:
        per_day = ATTRACTIONS_PER_DAY
    key = _cache_key('attraction', location, duration, categories, budget, distance, per_day,
                     accommodation, pre_planned,
                     json.dumps(client_profile or {}, sort_keys=True))
    cached = _get_cached(key)
    if cached is not None:
        logger.info('Attraction Scout: cache hit for %s', location)
        return key, cached

    count = duration * per_day

    accommodation_block = (
        f'- Accommodation / travel base: {accommodation}\n'
        f'  Group each day\'s attractions geographically so the client isn\'t backtracking.\n'
        f'  Distance and travel time must be calculated from this address, not the city centre.\n'
        if accommodation else
        '- Accommodation: not specified — use city centre as the assumed travel base.\n'
    )
    pre_planned_block = (
        f'Already planned / committed:\n  {pre_planned}\n'
        f'  Do NOT suggest anything that duplicates or conflicts with the above.\n'
        f'  If a time slot is already committed, plan around it — suggest complementary nearby\n'
        f'  stops rather than competing alternatives for the same slot.\n'
        if pre_planned else ''
    )
    profile = client_profile or {}
    profile_lines = []
    if profile.get('travel_style'):
        profile_lines.append(f"  Travel style: {profile['travel_style']}")
    if profile.get('preferred_budget'):
        profile_lines.append(f"  Budget preference: {profile['preferred_budget']} — factor into admission and tour costs")
    if profile.get('home_city'):
        profile_lines.append(f"  Home city: {profile['home_city']} — skip attractions that are similar to what they have at home; favour experiences genuinely unique to {location}")
    if profile.get('dietary_requirements'):
        profile_lines.append(f"  Dietary requirements: {profile['dietary_requirements']} — if any attraction involves food, ensure it is compatible")
    if profile.get('notes'):
        profile_lines.append(f"  Consultant notes: {profile['notes']}")
    client_block = (
        'Client profile:\n' + '\n'.join(profile_lines) + '\n'
        if profile_lines else
        'Client profile: none provided — give broadly appealing recommendations.\n'
    )

    system_prompt = """You are a travel writer producing practical sightseeing recommendations
personalised to a specific client. Read their profile carefully — it should shape every choice.

PERSONALISATION RULES:
- Category preferences are a starting point. Use the client profile to choose the specific
  venues within each category that best match their style and background.
- Home city: if given, skip attractions that parallel something they have at home. An art museum
  is fine — unless they're from a city famous for its art museums, in which case find something
  more distinctive to the destination.
- Travel style: let it shape pace and depth. An adventurous traveller gets active or off-the-
  beaten-path options; a cultural traveller gets deeper dives into history or art.
- Budget preference: honour it in admission recommendations and any paid experiences you suggest.
- Pre-planned commitments: never duplicate them. If the client already has a Sagrada Família
  ticket, do not suggest Sagrada Família — suggest what to do before or after instead.
- If accommodation is given, plan each day's attractions so the client isn't constantly
  backtracking. State approximate travel time from the accommodation for each stop.
- Dietary requirements: if any attraction involves food, verify compatibility first.
- Consultant notes: treat as hard constraints. Physical limitations, interests to avoid, or
  specific requests must be respected absolutely.

WRITING STYLE — follow this strictly:
- Write like a well-travelled friend giving honest advice, not a tourist board.
- Start with what the place is — a plain statement of fact.
- Be specific: say what you actually see, hear, or do there.
- Mention the realistic trade-off (crowds, queues, overhyped sections, anything worth knowing).
- Best time and insider tip must be actionable. "Go early" is not enough — give a specific time.
- Short sentences. Vary the rhythm. No stacked adjectives.
- Forbidden words: stunning, breathtaking, magical, iconic, world-class, unmissable, legendary,
  nestled, boasting, rich history, vibrant, hidden gem, off the beaten path.

OUTPUT FORMAT — return EXACTLY this JSON schema for each attraction, one object per line, no markdown:
{
  "day": [day number],
  "time": "[time slot, e.g., 9:00-11:00am]",
  "name": "[Attraction name]",
  "address": "[Full address]",
  "category": "[Type: museum / market / viewpoint / park / etc.]",
  "location": "[Neighbourhood]",
  "travel_time": "[Approx travel time from accommodation, e.g., '15 min metro' or '6 min walk'. Write 'N/A' if no accommodation was given.]",
  "description": "[2 sentences: what it is and the one thing that makes it worth this client's time. Honest — include any caveat.]",
  "admission": "[Free / price range]",
  "hours": "[Opening hours]",
  "duration": "[Realistic visit length]",
  "best_time": "[Specific: e.g., 'Weekday mornings before 10am' or 'Late afternoon when tour groups leave']",
  "why_this_client": "[1 sentence: specifically why this attraction suits this client's profile or interests.]",
  "highlight": "[The single best thing — be specific, not generic]",
  "insider_tip": "[1-2 sentences: one piece of practical advice most visitors don't know.]"
}"""

    user_prompt = f"""Generate {count} attractions ({per_day} per day across {duration} days).

Trip details:
- Destination: {location}
- Duration: {duration} days
- Attraction interests: {categories}
- Budget: {budget}
- Max travel radius: {distance}
{accommodation_block}
{pre_planned_block}
{client_block}
Provide {count} complete JSON objects, one per line. No markdown, no other text."""

    message = await anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=5000,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_prompt}],
    )

    attractions = _parse_json_lines(message.content[0].text, 'Attraction Scout')
    logger.info('Attraction Scout: parsed %d/%d attractions for %s', len(attractions), count, location)
    return key, attractions


async def _run_single_scout(name, fn, args, kwargs, location, accommodation_coords=None):
    """
    Call one async scout function, run Places verification, apply haversine distances,
    and cache the verified results. Returns a list (may be empty).

    Caching happens AFTER verification so cached items always include _lat/_lng
    coordinates required for map pin rendering.
    """
    scout_cache_key, items = await fn(*args, **kwargs)
    if items and PLACES_VERIFY_ENABLED:
        items, _ = await verify_places_batch(items, 'name', 'address', location)
    if items and accommodation_coords and accommodation_coords[0] is not None:
        _apply_distances(items, accommodation_coords[0], accommodation_coords[1])
    if items and scout_cache_key:
        _set_cached(scout_cache_key, items)
    return items


# ---------------------------------------------------------------------------
# Background scout task (asyncio.create_task — runs concurrently with requests)
# ---------------------------------------------------------------------------

async def _run_scouts_background(job_id: str, params: dict, user_id: int) -> None:
    """
    Full scout pipeline executed as a background asyncio coroutine.

    Accepts a validated GenerateRequest dict (body.model_dump()) and the
    current user's ID.  Updates job state in Redis throughout so any
    worker can answer GET /jobs/{job_id} polling requests.
    """
    db_session = None
    try:
        _job_update(job_id, {'status': 'running', 'progress': 5, 'message': 'Starting…'})

        # Own DB session — FastAPI's Depends() doesn't work outside a request
        db_session = await run_in_threadpool(SessionLocal)

        # ── Unpack validated params ───────────────────────────────────────────
        location      = params['location']
        duration      = params['duration']
        budget        = params.get('budget') or 'Moderate'
        distance      = params.get('distance') or 'Up to 30 minutes'
        accommodation = params.get('accommodation')
        pre_planned   = params.get('pre_planned')

        include_photos      = params.get('include_photos', True)
        include_dining      = params.get('include_dining', True)
        include_attractions = params.get('include_attractions', True)

        photos_per_day      = params.get('photos_per_day',      PHOTOS_PER_DAY)
        restaurants_per_day = params.get('restaurants_per_day', RESTAURANTS_PER_DAY)
        attractions_per_day = params.get('attractions_per_day', ATTRACTIONS_PER_DAY)

        photo_interests = params.get('photo_interests') or ''
        cuisines        = params.get('cuisines')        or ''
        attraction_cats = params.get('attraction_cats') or ''
        client_id       = params.get('client_id')

        # ── Load client profile ───────────────────────────────────────────────
        client_profile = None
        if client_id is not None:
            try:
                cid       = int(client_id)
                db_client = await run_in_threadpool(lambda: db_session.get(Client, cid))
                if db_client and not db_client.is_deleted:
                    client_profile = {k: v for k, v in {
                        'home_city':            db_client.home_city            or '',
                        'preferred_budget':     db_client.preferred_budget     or '',
                        'travel_style':         db_client.travel_style         or '',
                        'dietary_requirements': db_client.dietary_requirements or '',
                        'notes':                db_client.notes                or '',
                    }.items() if v}
                    logger.info('BG job=%s: client profile loaded for id=%d', job_id[:8], cid)
            except Exception as cp_exc:
                logger.warning('BG job=%s: could not load client profile (id=%s): %s',
                               job_id[:8], client_id, cp_exc)

        _job_update(job_id, {'progress': 15, 'message': f'Generating recommendations for {location}…'})

        logger.info('BG job=%s: %s %d days | photos=%s dining=%s attractions=%s',
                    job_id[:8], location, duration,
                    'ON' if include_photos else 'OFF',
                    'ON' if include_dining else 'OFF',
                    'ON' if include_attractions else 'OFF')

        # ── Build scout task list ─────────────────────────────────────────────
        scout_tasks = {}
        if include_photos:
            scout_tasks['photos'] = (
                call_photo_scout,
                (location, duration, photo_interests, distance),
                {'per_day': photos_per_day, 'accommodation': accommodation,
                 'pre_planned': pre_planned, 'client_profile': client_profile},
            )
        if include_dining:
            scout_tasks['restaurants'] = (
                call_restaurant_scout,
                (location, duration, cuisines, budget, distance),
                {'per_day': restaurants_per_day, 'accommodation': accommodation,
                 'pre_planned': pre_planned, 'client_profile': client_profile},
            )
        if include_attractions:
            scout_tasks['attractions'] = (
                call_attraction_scout,
                (location, duration, attraction_cats, budget, distance),
                {'per_day': attractions_per_day, 'accommodation': accommodation,
                 'pre_planned': pre_planned, 'client_profile': client_profile},
            )

        # ── Geocode accommodation ─────────────────────────────────────────────
        accommodation_coords: tuple = (None, None)
        if accommodation and PLACES_VERIFY_ENABLED:
            _job_update(job_id, {'progress': 20, 'message': 'Geocoding accommodation…'})
            accommodation_coords = await _geocode_accommodation(accommodation)

        _job_update(job_id, {'progress': 25, 'message': f'Scouting {location}…'})

        if PLACES_VERIFY_ENABLED:
            logger.info('BG job=%s: Places verification enabled', job_id[:8])
        else:
            logger.info('BG job=%s: Places verification disabled (no API key)', job_id[:8])

        # ── Initial parallel scout run ────────────────────────────────────────
        results = {'photos': [], 'restaurants': [], 'attractions': []}
        raw_results = await asyncio.gather(
            *[_run_single_scout(name, fn, args, kwargs, location, accommodation_coords)
              for name, (fn, args, kwargs) in scout_tasks.items()],
            return_exceptions=True,
        )
        for (name, _), result in zip(scout_tasks.items(), raw_results):
            if isinstance(result, Exception):
                logger.error("BG job=%s scout '%s': initial run failed — %s", job_id[:8], name, result)
                results[name] = []
            else:
                results[name] = result
                logger.info("BG job=%s scout '%s': %d item(s)", job_id[:8], name, len(result))

        # ── Retry scouts that returned 0 items ────────────────────────────────
        for attempt in range(1, SCOUT_MAX_RETRIES + 1):
            empty_scouts = [name for name in scout_tasks if not results[name]]
            if not empty_scouts:
                break
            logger.warning('BG job=%s: retry %d — empty: %s', job_id[:8], attempt, empty_scouts)
            _job_update(job_id, {'message': f'Retrying {", ".join(empty_scouts)} scout…'})
            await asyncio.sleep(SCOUT_RETRY_DELAY)
            for name in empty_scouts:
                fn, args, kwargs = scout_tasks[name]
                try:
                    items         = await _run_single_scout(name, fn, args, kwargs, location, accommodation_coords)
                    results[name] = items
                    logger.info("BG job=%s scout '%s': retry %d returned %d item(s)",
                                job_id[:8], name, attempt, len(items))
                except Exception as exc:
                    logger.error("BG job=%s scout '%s': retry %d failed — %s",
                                 job_id[:8], name, attempt, exc)

        # ── Collect warnings for still-empty scouts ───────────────────────────
        warnings = []
        for name in scout_tasks:
            if not results[name]:
                label = {'photos': 'Photography', 'restaurants': 'Dining', 'attractions': 'Attractions'}[name]
                warnings.append(
                    f'{label} recommendations could not be generated after '
                    f'{SCOUT_MAX_RETRIES + 1} attempt(s). You can proceed without this section or try again.'
                )
                logger.warning("BG job=%s scout '%s': 0 results after %d attempt(s)",
                               job_id[:8], name, SCOUT_MAX_RETRIES + 1)

        if not any(results[n] for n in scout_tasks):
            raise ValueError('No recommendations could be generated. Please try again.')

        colors = get_color_palette(location)
        _job_update(job_id, {'progress': 80, 'message': 'Saving trip…'})

        # ── Store session ─────────────────────────────────────────────────────
        _evict_sessions()
        session_id = str(uuid.uuid4())
        _session_set(session_id, {
            'location':    location,
            'duration':    duration,
            'colors':      colors,
            'photos':      results['photos'],
            'restaurants': results['restaurants'],
            'attractions': results['attractions'],
        })

        # ── Save draft trip to DB ─────────────────────────────────────────────
        trip_id = None
        try:
            def _save_trip():
                trip = Trip(
                    client_id            = client_id,
                    created_by_id        = user_id,
                    title                = f"{location} — {duration} day{'s' if duration != 1 else ''}",
                    status               = 'draft',
                    location             = location,
                    duration             = duration,
                    budget               = budget,
                    distance             = distance,
                    include_photos       = include_photos,
                    include_dining       = include_dining,
                    include_attractions  = include_attractions,
                    photos_per_day       = photos_per_day,
                    restaurants_per_day  = restaurants_per_day,
                    attractions_per_day  = attractions_per_day,
                    photo_interests      = photo_interests or None,
                    cuisines             = cuisines or None,
                    attraction_cats      = attraction_cats or None,
                    accommodation        = accommodation,
                    raw_photos           = json.dumps(results['photos']),
                    raw_restaurants      = json.dumps(results['restaurants']),
                    raw_attractions      = json.dumps(results['attractions']),
                    colors               = json.dumps(colors),
                    session_id           = session_id,
                )
                db_session.add(trip)
                db_session.commit()
                db_session.refresh(trip)
                return trip.id

            trip_id = await run_in_threadpool(_save_trip)
            logger.info('BG job=%s: trip draft saved id=%d', job_id[:8], trip_id)
        except Exception as db_exc:
            logger.error('BG job=%s: failed to save trip to DB: %s', job_id[:8], db_exc)

        # ── Mark complete ─────────────────────────────────────────────────────
        _job_set(job_id, {
            'status':   'done',
            'progress': 100,
            'message':  'Done!',
            'results':  {
                'status':           'success',
                'session_id':       session_id,
                'trip_id':          trip_id,
                'location':         location,
                'duration':         duration,
                'colors':           colors,
                'photos':           results['photos'],
                'restaurants':      results['restaurants'],
                'attractions':      results['attractions'],
                'photo_count':      len(results['photos']),
                'restaurant_count': len(results['restaurants']),
                'attraction_count': len(results['attractions']),
                'warnings':         warnings,
                'model':            SCOUT_MODEL_LABEL,
            },
        })
        logger.info('BG job=%s: complete — %d photos, %d restaurants, %d attractions',
                    job_id[:8], len(results['photos']), len(results['restaurants']),
                    len(results['attractions']))

    except Exception as exc:
        logger.error('BG job=%s failed: %s', job_id, exc, exc_info=True)
        _job_set(job_id, {
            'status':   'failed',
            'progress': 0,
            'message':  'Failed',
            'error':    'An unexpected error occurred. Please try again.',
        })

    finally:
        if db_session is not None:
            await run_in_threadpool(db_session.close)


# ---------------------------------------------------------------------------
# HTML generation helpers (sync — called via run_in_threadpool)
# ---------------------------------------------------------------------------

def create_google_maps_link(name, address, coordinates):
    if coordinates and ',' in str(coordinates):
        return f"https://www.google.com/maps/search/{coordinates.replace(' ', '')}"
    elif address:
        return f"https://www.google.com/maps/search/{address.replace(' ', '+').replace(',', '%2C')}"
    else:
        return f"https://www.google.com/maps/search/{name.replace(' ', '+')}"


def _e(value, fallback='N/A'):
    return escape(str(value)) if value else fallback


def _verification_badge_html(item):
    status        = item.get('_status')
    confirmed_url = item.get('_maps_url')
    if status == STATUS_OPERATIONAL:
        badge = '<span class="verify-badge verified">✓ Verified Open</span>'
    else:
        badge = '<span class="verify-badge unverified">Unverified — confirm before visiting</span>'
    return badge, '', confirmed_url


def build_day_map_html(data_uri, maps_link, location_list_html):
    if data_uri:
        return (
            f'<a href="{maps_link}" target="_blank" rel="noopener" '
            f'title="Open in Google Maps" class="day-map-link">'
            f'<img src="{data_uri}" width="100%" height="380" '
            f'style="display:block;object-fit:cover;" '
            f'alt="Day map with pinned locations">'
            f'</a>'
        )
    return location_list_html


def generate_master_html(location, duration, photos, restaurants, attractions, colors, prefetched_maps=None):
    """Generate unified HTML master document — Editorial theme."""

    safe_location  = escape(location)
    generated_date = datetime.now().strftime('%B %d, %Y')
    city_name      = escape(location.split(',')[0].strip())

    roman          = ['I', 'II', 'III']
    active_sections = []
    if photos:      active_sections.append('photos')
    if restaurants: active_sections.append('restaurants')
    if attractions: active_sections.append('attractions')

    section_names      = {'photos': 'Photography', 'restaurants': 'Dining', 'attractions': 'Attractions'}
    cover_footer_sections = ' &middot; '.join(section_names[s] for s in active_sections) or 'Custom Guide'
    cover_dek_parts = []
    if photos:       cover_dek_parts.append('photography locations')
    if restaurants:  cover_dek_parts.append('dining recommendations')
    if attractions:  cover_dek_parts.append('attractions worth seeking out')
    cover_dek_text = ', '.join(cover_dek_parts[:-1]) + (
        f' and {cover_dek_parts[-1]}' if len(cover_dek_parts) > 1
        else (cover_dek_parts[0] if cover_dek_parts else 'highlights')
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_location} Travel Guide &mdash; {duration} Days</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400;1,700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{
            margin: 0; padding: 0; box-sizing: border-box;
        }}

        :root {{
            --bg:      #f5f2ee;
            --white:   #ffffff;
            --ink:     #1a1a1a;
            --ink-2:   #4a4a4a;
            --rule:    #d8d3cc;
            --sand:    #e8e2d9;
            --primary: {colors['primary']};
            --accent:  {colors['accent']};
        }}

        html {{ scroll-behavior: smooth; }}

        body {{
            font-family: 'Inter', sans-serif;
            background: var(--bg);
            color: var(--ink);
            font-weight: 300;
            line-height: 1.6;
        }}

        /* ── PRINT BUTTON (screen only) ── */
        .print-btn {{
            position: fixed;
            top: 24px; right: 24px;
            padding: 10px 22px;
            background: var(--ink);
            color: var(--white);
            border: none;
            font-family: 'Inter', sans-serif;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            cursor: pointer;
            z-index: 999;
            transition: background 0.2s;
        }}
        .print-btn:hover {{ background: var(--primary); }}

        /* ── COVER ── */
        .cover {{
            min-height: 100vh;
            display: grid;
            grid-template-rows: auto 1fr auto;
            background: var(--white);
            border-bottom: 3px solid var(--ink);
        }}

        .cover-masthead {{
            padding: 28px 56px;
            border-bottom: 1px solid var(--rule);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .cover-masthead-brand {{
            font-family: 'Playfair Display', serif;
            font-size: 1.2rem;
            font-weight: 700;
            letter-spacing: -0.01em;
        }}

        .cover-masthead-meta {{
            font-size: 0.65rem;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: var(--ink-2);
        }}

        .cover-body {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            min-height: 70vh;
        }}

        .cover-text {{
            padding: 72px 56px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            border-right: 1px solid var(--rule);
        }}

        .cover-kicker {{
            font-size: 0.65rem;
            letter-spacing: 0.22em;
            text-transform: uppercase;
            color: var(--primary);
            font-weight: 600;
            margin-bottom: 24px;
        }}

        .cover-headline {{
            font-family: 'Playfair Display', serif;
            font-size: clamp(3rem, 6vw, 5.5rem);
            font-weight: 700;
            line-height: 0.95;
            letter-spacing: -0.03em;
            margin-bottom: 28px;
        }}

        .cover-headline em {{
            font-style: italic;
            color: var(--primary);
        }}

        .cover-dek {{
            font-size: 0.95rem;
            line-height: 1.75;
            color: var(--ink-2);
            max-width: 360px;
            margin-bottom: 40px;
        }}

        .cover-stats {{
            display: flex;
            gap: 32px;
            padding-top: 32px;
            border-top: 1px solid var(--rule);
        }}

        .cover-stat-num {{
            font-family: 'Playfair Display', serif;
            font-size: 2rem;
            font-weight: 700;
            line-height: 1;
            color: var(--primary);
        }}

        .cover-stat-label {{
            font-size: 0.65rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--ink-2);
            margin-top: 4px;
        }}

        .cover-visual {{
            background: var(--sand);
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
            overflow: hidden;
        }}

        .cover-visual svg {{
            width: 55%;
            opacity: 0.18;
        }}

        .cover-visual-label {{
            position: absolute;
            bottom: 28px; right: 28px;
            font-size: 0.6rem;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: var(--ink-2);
        }}

        .cover-footer {{
            padding: 20px 56px;
            border-top: 1px solid var(--rule);
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.7rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--ink-2);
        }}

        .cover-footer-accent {{
            color: var(--primary);
            font-weight: 600;
        }}

        /* ── SECTIONS ── */
        .section {{
            max-width: 960px;
            margin: 0 auto;
            padding: 72px 56px;
        }}

        .section + .section {{
            border-top: 1px solid var(--rule);
        }}

        .section-header {{
            display: flex;
            align-items: baseline;
            gap: 20px;
            margin-bottom: 48px;
            padding-bottom: 20px;
            border-bottom: 3px solid var(--ink);
        }}

        .section-number {{
            font-family: 'Playfair Display', serif;
            font-size: 3rem;
            font-weight: 700;
            color: var(--rule);
            line-height: 1;
        }}

        .section-title {{
            font-family: 'Playfair Display', serif;
            font-size: 1.8rem;
            font-weight: 700;
            line-height: 1.1;
            letter-spacing: -0.02em;
        }}

        .section-subtitle {{
            font-size: 0.72rem;
            letter-spacing: 0.15em;
            text-transform: uppercase;
            color: var(--ink-2);
            margin-top: 6px;
        }}

        /* ── DAY DIVIDERS ── */
        .day-divider {{
            display: flex;
            align-items: center;
            gap: 20px;
            margin: 40px 0 24px;
        }}

        .day-divider-label {{
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 0.22em;
            text-transform: uppercase;
            color: var(--primary);
            white-space: nowrap;
        }}

        .day-divider-rule {{
            flex: 1;
            height: 1px;
            background: var(--rule);
        }}

        /* ── ITEM CARDS ── */
        .item-card {{
            background: var(--white);
            border-left: 3px solid var(--primary);
            padding: 28px 32px;
            margin-bottom: 16px;
        }}

        .item-card-head {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--rule);
        }}

        .item-card-title {{
            font-family: 'Playfair Display', serif;
            font-size: 1.25rem;
            font-weight: 700;
            letter-spacing: -0.01em;
            line-height: 1.2;
        }}

        .item-card-tag {{
            display: inline-block;
            padding: 4px 12px;
            background: var(--sand);
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--ink-2);
            white-space: nowrap;
            flex-shrink: 0;
        }}

        .item-card-tag.highlight {{
            background: var(--primary);
            color: var(--white);
        }}

        .item-card-tag.price-tag {{
            font-family: 'Playfair Display', serif;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.04em;
            color: var(--ink);
            background: var(--sand);
        }}

        .item-meta-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
            font-size: 0.875rem;
        }}

        .meta-cell {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .meta-label {{
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: var(--ink-2);
        }}

        .meta-value {{
            color: var(--ink);
            line-height: 1.55;
        }}

        .item-card-body {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}

        .full-field {{
            font-size: 0.875rem;
            line-height: 1.65;
        }}

        .full-field .meta-label {{
            display: block;
            margin-bottom: 5px;
        }}

        /* Tip highlight box */
        .tip-box {{
            background: var(--sand);
            border-left: 3px solid var(--accent);
            padding: 14px 18px;
            font-size: 0.85rem;
            line-height: 1.6;
        }}

        .tip-box .meta-label {{
            color: var(--primary);
            margin-bottom: 4px;
        }}

        /* ── VERIFICATION BADGES ── */
        .verify-badge {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            padding: 3px 10px;
            border-radius: 2px;
        }}

        .verify-badge.verified {{
            background: #eaf7ee;
            color: #1a7a3a;
        }}

        .verify-badge.unverified {{
            background: var(--sand);
            color: var(--ink-2);
        }}

        /* ── DAY MAP ── */
        .day-map {{
            margin: 8px 0 28px;
            border: 1px solid var(--rule);
            overflow: hidden;
        }}
        .day-map a {{
            display: block;
            position: relative;
        }}
        .day-map a::after {{
            content: "Open in Google Maps ↗";
            position: absolute;
            bottom: 10px;
            right: 12px;
            background: rgba(255,255,255,0.88);
            color: #1a73e8;
            font-size: 0.62rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            padding: 5px 10px;
            border-radius: 3px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.18);
            pointer-events: none;
        }}

        .day-map-label {{
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: var(--ink-2);
            padding: 10px 16px 8px;
            background: var(--sand);
            border-bottom: 1px solid var(--rule);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .day-map-print-list {{
            margin: 0;
            padding: 10px 16px 12px 36px;
            font-size: 0.8rem;
            color: var(--ink-2);
            background: var(--sand);
        }}
        .day-map-print-list li {{
            margin-bottom: 4px;
        }}

        @media print {{
            .day-map {{
                margin: 6px 0 20px;
                break-inside: avoid;
                page-break-inside: avoid;
                border: 1px solid #ddd;
            }}
            .day-map-link img {{
                max-height: 260px;
                width: 100%;
                object-fit: cover;
                display: block;
            }}
            .day-map a::after {{
                display: none;
            }}
        }}

        /* Maps link */
        .maps-link {{
            margin-top: 20px;
            padding-top: 16px;
            border-top: 1px solid var(--rule);
        }}

        .maps-link a {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--primary);
            text-decoration: none;
        }}

        .maps-link a::after {{
            content: '→';
            font-size: 1em;
        }}

        .maps-link a:hover {{
            text-decoration: underline;
        }}

        /* ── FOOTER ── */
        .doc-footer {{
            background: var(--ink);
            color: rgba(255,255,255,0.5);
            padding: 40px 56px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.7rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
        }}

        .doc-footer-brand {{
            font-family: 'Playfair Display', serif;
            font-size: 1rem;
            font-weight: 700;
            color: var(--white);
            letter-spacing: -0.01em;
            text-transform: none;
        }}

        .doc-footer-right {{
            text-align: right;
        }}

        /* ── PRINT ── */
        @media print {{
            .print-btn {{ display: none; }}
            body {{ background: white; }}
            .cover {{ min-height: auto; page-break-after: always; }}
            .section {{ page-break-inside: avoid; }}
            .item-card {{ page-break-inside: avoid; }}
        }}

        @media (max-width: 720px) {{
            .cover-body {{ grid-template-columns: 1fr; }}
            .cover-visual {{ display: none; }}
            .cover-text, .section, .cover-masthead, .cover-footer, .doc-footer {{
                padding-left: 24px; padding-right: 24px;
            }}
            .item-meta-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>

    <button class="print-btn" onclick="window.print()">Save as PDF</button>

    <!-- ── COVER ── -->
    <div class="cover">
        <div class="cover-masthead">
            <div class="cover-masthead-brand">Trip Master</div>
            <div class="cover-masthead-meta">AI Travel Intelligence &middot; {generated_date}</div>
        </div>

        <div class="cover-body">
            <div class="cover-text">
                <p class="cover-kicker">Your Bespoke Travel Guide</p>
                <h1 class="cover-headline">{city_name}<br><em>awaits.</em></h1>
                <p class="cover-dek">
                    A curated {duration}-day itinerary crafted for you &mdash;
                    {cover_dek_text} in {safe_location}.
                </p>
                <div class="cover-stats">
                    {''.join([
                        f'<div><div class="cover-stat-num">{len(photos)}</div><div class="cover-stat-label">Photo Spots</div></div>'
                        if photos else '',
                        f'<div><div class="cover-stat-num">{len(restaurants)}</div><div class="cover-stat-label">Restaurants</div></div>'
                        if restaurants else '',
                        f'<div><div class="cover-stat-num">{len(attractions)}</div><div class="cover-stat-label">Attractions</div></div>'
                        if attractions else '',
                        f'<div><div class="cover-stat-num">{duration}</div><div class="cover-stat-label">Days</div></div>',
                    ])}
                </div>
            </div>
            <div class="cover-visual">
                <svg viewBox="0 0 200 200" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <circle cx="100" cy="100" r="90" stroke="#1a1a1a" stroke-width="1"/>
                    <circle cx="100" cy="100" r="60" stroke="#1a1a1a" stroke-width="1"/>
                    <circle cx="100" cy="100" r="30" stroke="#1a1a1a" stroke-width="1"/>
                    <line x1="100" y1="10" x2="100" y2="190" stroke="#1a1a1a" stroke-width="1"/>
                    <line x1="10" y1="100" x2="190" y2="100" stroke="#1a1a1a" stroke-width="1"/>
                    <line x1="36" y1="36" x2="164" y2="164" stroke="#1a1a1a" stroke-width="0.5"/>
                    <line x1="164" y1="36" x2="36" y2="164" stroke="#1a1a1a" stroke-width="0.5"/>
                    <polygon points="100,10 106,95 100,100 94,95" fill="#1a1a1a"/>
                    <polygon points="100,190 94,105 100,100 106,105" fill="#1a1a1a" opacity="0.3"/>
                </svg>
                <span class="cover-visual-label">{safe_location}</span>
            </div>
        </div>

        <div class="cover-footer">
            <span>{cover_footer_sections}</span>
            <span class="cover-footer-accent">Powered by Claude AI</span>
        </div>
    </div>

"""

    section_idx = 0

    # ── PHOTOGRAPHY ──
    if photos:
        roman_num = roman[section_idx]; section_idx += 1
        html += f"""
    <!-- ── PHOTOGRAPHY ── -->
    <div class="section">
        <div class="section-header">
            <div class="section-number">{roman_num}</div>
            <div class="section-title-group">
                <h2 class="section-title">Photography Guide</h2>
                <p class="section-subtitle">Locations, composition &amp; timing</p>
            </div>
        </div>
"""
        photos_by_day = defaultdict(list)
        for photo in photos:
            photos_by_day[photo.get('day', 1)].append(photo)

        for day_num in sorted(photos_by_day.keys()):
            day_photos = photos_by_day[day_num]
            html += f"""
        <div class="day-divider">
            <span class="day-divider-label">Day {day_num}</span>
            <div class="day-divider-rule"></div>
        </div>"""

            _map_data = (prefetched_maps or {}).get(('photos', day_num))
            map_img   = build_day_map_html(*_map_data) if _map_data else ''
            if map_img:
                count_label = f"{len(day_photos)} location{'s' if len(day_photos) != 1 else ''}"
                html += f"""
        <div class="day-map">
            <div class="day-map-label">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                Day {day_num} Photo Locations &mdash; {count_label}
            </div>
            {map_img}
        </div>"""

            for photo in day_photos:
                fallback_maps_url = create_google_maps_link(
                    photo.get('name', ''), photo.get('address', ''), photo.get('coordinates', ''))
                badge, notice, confirmed_url = _verification_badge_html(photo)
                maps_url     = escape(confirmed_url or fallback_maps_url)
                travel_time  = photo.get('travel_time', '')
                travel_html  = (
                    f'<div class="meta-cell"><span class="meta-label">From Accommodation</span>'
                    f'<span class="meta-value">{_e(travel_time)}</span></div>'
                ) if travel_time and travel_time.upper() != 'N/A' else ''
                html += f"""
        <div class="item-card">
            <div class="item-card-head">
                <h3 class="item-card-title">{_e(photo.get('name'), 'Location')}</h3>
                <div style="display:flex;gap:6px;align-items:center;flex-shrink:0;">
                    {badge}
                    <span class="item-card-tag highlight">{_e(photo.get('time'), '')}</span>
                </div>
            </div>
            <div class="item-card-body">
                <div class="item-meta-grid">
                    <div class="meta-cell">
                        <span class="meta-label">Subject</span>
                        <span class="meta-value">{_e(photo.get('subject'))}</span>
                    </div>
                    <div class="meta-cell">
                        <span class="meta-label">Camera Setup</span>
                        <span class="meta-value">{_e(photo.get('setup'))}</span>
                    </div>
                    {travel_html}
                </div>
                <div class="meta-cell">
                    <span class="meta-label">Light &amp; Conditions</span>
                    <span class="meta-value">{_e(photo.get('light'))}</span>
                </div>
                <div class="tip-box">
                    <span class="meta-label">Pro Tip</span>
                    {_e(photo.get('pro_tip'))}
                </div>
                {notice}
                <div class="maps-link">
                    <a href="{maps_url}" target="_blank" rel="noopener noreferrer">View on Google Maps</a>
                </div>
            </div>
        </div>"""
        html += "\n    </div>\n"

    # ── DINING ──
    if restaurants:
        roman_num = roman[section_idx]; section_idx += 1
        html += f"""
    <!-- ── DINING ── -->
    <div class="section">
        <div class="section-header">
            <div class="section-number">{roman_num}</div>
            <div class="section-title-group">
                <h2 class="section-title">Dining Guide</h2>
                <p class="section-subtitle">Restaurants &amp; local cuisine</p>
            </div>
        </div>
"""
        current_day = 0
        for restaurant in restaurants:
            day = restaurant.get('day', current_day)
            if day != current_day:
                current_day = day
                html += f"""
        <div class="day-divider">
            <span class="day-divider-label">Day {current_day}</span>
            <div class="day-divider-rule"></div>
        </div>"""

            fallback_maps_url = create_google_maps_link(
                restaurant.get('name', ''), restaurant.get('address', ''), '')
            badge, notice, confirmed_url = _verification_badge_html(restaurant)
            maps_url      = escape(confirmed_url or fallback_maps_url)
            meal          = _e(restaurant.get('meal_type'), '').title()
            price         = _e(restaurant.get('price'), '')
            r_travel_time = restaurant.get('travel_time', '')
            r_why_client  = restaurant.get('why_this_client', '')
            r_travel_html = (
                f'<div class="meta-cell"><span class="meta-label">From Accommodation</span>'
                f'<span class="meta-value">{_e(r_travel_time)}</span></div>'
            ) if r_travel_time and r_travel_time.upper() != 'N/A' else ''
            r_why_html = (
                f'<div class="tip-box" style="border-left-color:var(--primary)">'
                f'<span class="meta-label">Why For This Client</span>'
                f'{_e(r_why_client)}</div>'
            ) if r_why_client else ''
            html += f"""
        <div class="item-card">
            <div class="item-card-head">
                <h3 class="item-card-title">{_e(restaurant.get('name'), 'Restaurant')}</h3>
                <div style="display:flex;gap:6px;flex-shrink:0;align-items:center;">
                    {badge}
                    {f'<span class="item-card-tag highlight">{meal}</span>' if meal else ''}
                    {f'<span class="item-card-tag price-tag">{price}</span>' if price else ''}
                </div>
            </div>
            <div class="item-card-body">
                <div class="full-field">
                    <span class="meta-label">About</span>
                    {_e(restaurant.get('description'))}
                </div>
                <div class="item-meta-grid">
                    <div class="meta-cell">
                        <span class="meta-label">Cuisine</span>
                        <span class="meta-value">{_e(restaurant.get('cuisine'))}</span>
                    </div>
                    <div class="meta-cell">
                        <span class="meta-label">Neighbourhood</span>
                        <span class="meta-value">{_e(restaurant.get('location'))}</span>
                    </div>
                    <div class="meta-cell">
                        <span class="meta-label">Hours</span>
                        <span class="meta-value">{_e(restaurant.get('hours'))}</span>
                    </div>
                    <div class="meta-cell">
                        <span class="meta-label">Signature Dish</span>
                        <span class="meta-value">{_e(restaurant.get('signature_dish'))}</span>
                    </div>
                    {r_travel_html}
                </div>
                {r_why_html}
                <div class="tip-box">
                    <span class="meta-label">Insider Tip</span>
                    {_e(restaurant.get('insider_tip'))}
                </div>
                {notice}
                <div class="maps-link">
                    <a href="{maps_url}" target="_blank" rel="noopener noreferrer">View on Google Maps</a>
                </div>
            </div>
        </div>"""
        html += "\n    </div>\n"

    # ── ATTRACTIONS ──
    if attractions:
        roman_num = roman[section_idx]; section_idx += 1
        html += f"""
    <!-- ── ATTRACTIONS ── -->
    <div class="section">
        <div class="section-header">
            <div class="section-number">{roman_num}</div>
            <div class="section-title-group">
                <h2 class="section-title">Attractions</h2>
                <p class="section-subtitle">Things to see &amp; do</p>
            </div>
        </div>
"""
        attractions_by_day = defaultdict(list)
        for attraction in attractions:
            attractions_by_day[attraction.get('day', 1)].append(attraction)

        for day_num in sorted(attractions_by_day.keys()):
            day_attractions = attractions_by_day[day_num]
            html += f"""
        <div class="day-divider">
            <span class="day-divider-label">Day {day_num}</span>
            <div class="day-divider-rule"></div>
        </div>"""

            _map_data = (prefetched_maps or {}).get(('attractions', day_num))
            map_img   = build_day_map_html(*_map_data) if _map_data else ''
            if map_img:
                count_label = f"{len(day_attractions)} stop{'s' if len(day_attractions) != 1 else ''}"
                html += f"""
        <div class="day-map">
            <div class="day-map-label">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                Day {day_num} Attractions &mdash; {count_label}
            </div>
            {map_img}
        </div>"""

            for attraction in day_attractions:
                fallback_maps_url = create_google_maps_link(
                    attraction.get('name', ''), attraction.get('address', ''), '')
                badge, notice, confirmed_url = _verification_badge_html(attraction)
                maps_url      = escape(confirmed_url or fallback_maps_url)
                a_travel_time = attraction.get('travel_time', '')
                a_why_client  = attraction.get('why_this_client', '')
                a_travel_html = (
                    f'<div class="meta-cell"><span class="meta-label">From Accommodation</span>'
                    f'<span class="meta-value">{_e(a_travel_time)}</span></div>'
                ) if a_travel_time and a_travel_time.upper() != 'N/A' else ''
                a_why_html = (
                    f'<div class="tip-box" style="border-left-color:var(--primary)">'
                    f'<span class="meta-label">Why For This Client</span>'
                    f'{_e(a_why_client)}</div>'
                ) if a_why_client else ''
                html += f"""
        <div class="item-card">
            <div class="item-card-head">
                <h3 class="item-card-title">{_e(attraction.get('name'), 'Attraction')}</h3>
                <div style="display:flex;gap:6px;flex-shrink:0;align-items:center;">
                    {badge}
                    <span class="item-card-tag highlight">{_e(attraction.get('time'), '')}</span>
                    <span class="item-card-tag">{_e(attraction.get('admission'), '')}</span>
                </div>
            </div>
            <div class="item-card-body">
                <div class="meta-cell">
                    <span class="meta-label">About</span>
                    <span class="meta-value">{_e(attraction.get('description'))}</span>
                </div>
                <div class="item-meta-grid">
                    <div class="meta-cell">
                        <span class="meta-label">Category</span>
                        <span class="meta-value">{_e(attraction.get('category'))}</span>
                    </div>
                    <div class="meta-cell">
                        <span class="meta-label">Hours</span>
                        <span class="meta-value">{_e(attraction.get('hours'))}</span>
                    </div>
                    <div class="meta-cell">
                        <span class="meta-label">Time Needed</span>
                        <span class="meta-value">{_e(attraction.get('duration'))}</span>
                    </div>
                    <div class="meta-cell">
                        <span class="meta-label">Best Time to Visit</span>
                        <span class="meta-value">{_e(attraction.get('best_time'))}</span>
                    </div>
                    {a_travel_html}
                </div>
                {a_why_html}
                <div class="tip-box">
                    <span class="meta-label">Highlight &amp; Insider Tip</span>
                    <strong>{_e(attraction.get('highlight'))}</strong> &mdash; {_e(attraction.get('insider_tip'))}
                </div>
                {notice}
                <div class="maps-link">
                    <a href="{maps_url}" target="_blank" rel="noopener noreferrer">View on Google Maps</a>
                </div>
            </div>
        </div>"""
        html += "\n    </div>\n"

    html += f"""

    <!-- ── FOOTER ── -->
    <div class="doc-footer">
        <div>
            <div class="doc-footer-brand">Trip Master</div>
            <div style="margin-top:6px;">{safe_location} &middot; {duration} Days</div>
        </div>
        <div class="doc-footer-right">
            <div>Generated {generated_date}</div>
            <div style="margin-top:4px;">Powered by Claude AI</div>
        </div>
    </div>

</body>
</html>
"""
    return html


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get('/')
async def index():
    return FileResponse(os.path.join(BASE_DIR, 'index.html'))


@app.get('/health')
async def health():
    return {'status': 'ok', 'message': f'Trip Guide API is running on {SCOUT_MODEL_LABEL}'}


@app.post('/generate')
async def generate_trip_guide(
    body: GenerateRequest,
    current_user: StaffUser = Depends(get_current_user),
):
    """
    Enqueue a scout job and return a job_id immediately (< 200 ms).

    The client polls GET /jobs/{job_id} every 2 seconds until status == 'done',
    then uses the returned results exactly as it used to use the direct response.
    Requires login.
    """
    # Per-user rate limit — checked before queueing so we don't waste resources
    allowed, retry_after = check_user_rate_limit(current_user.id, 'generate')
    if not allowed:
        logger.warning('Rate limit hit: user_id=%d /generate retry_after=%ds',
                       current_user.id, retry_after)
        raise HTTPException(
            status_code=429,
            detail=f'Too many requests. Please wait {retry_after} seconds before trying again.',
        )

    job_id = str(uuid.uuid4())
    _job_set(job_id, {
        'status':   'pending',
        'progress': 0,
        'message':  'Queued…',
        'results':  None,
        'error':    None,
    })

    # asyncio.create_task runs the coroutine concurrently in the same event loop.
    # Because all scout work is async I/O (Claude API + httpx), it does not block
    # the event loop — other HTTP requests are served normally while scouts run.
    asyncio.create_task(
        _run_scouts_background(job_id, body.model_dump(), current_user.id)
    )

    logger.info('Job %s queued for %s %d days (user_id=%d)',
                job_id[:8], body.location, body.duration, current_user.id)
    return {'job_id': job_id}


@app.get('/jobs/{job_id}')
async def poll_job(
    job_id: str,
    current_user: StaffUser = Depends(get_current_user),
):
    """
    Poll the status of a background scout job (requires login).

    Returns:
        { status: 'pending'|'running'|'done'|'failed',
          progress: 0–100,
          message:  str,
          results:  {...} | null,   # present only when status == 'done'
          error:    str   | null }  # present only when status == 'failed'
    """
    job = _job_get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail='Job not found or expired. Please generate a new guide.',
        )
    return job


@app.post('/finalize')
async def finalize_guide(
    body: FinalizeRequest,
    request: Request,
    db_session: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    """Filter approved items, prefetch maps, generate final HTML. Requires login."""
    try:
        _evict_sessions()
        session_id = body.session_id.strip()

        # ── Resolve session: Redis/memory first, DB fallback ──────────────────
        _sess = _session_get(session_id) if session_id else None
        if _sess:
            location   = _sess['location']
            duration   = _sess['duration']
            colors     = _sess['colors']
            all_photos = _sess['photos']
            all_rests  = _sess['restaurants']
            all_attrs  = _sess['attractions']
            logger.info('Finalize: session %s resolved from store', session_id[:8])
        elif session_id:
            db_trip = await run_in_threadpool(
                lambda: db_session.query(Trip).filter_by(session_id=session_id, is_deleted=False).first()
            )
            if not db_trip:
                raise HTTPException(
                    status_code=404,
                    detail='Session expired — please click Start Over to generate a new guide.',
                )
            location   = db_trip.location
            duration   = db_trip.duration
            colors     = json.loads(db_trip.colors)           if db_trip.colors           else {}
            all_photos = json.loads(db_trip.raw_photos)       if db_trip.raw_photos       else []
            all_rests  = json.loads(db_trip.raw_restaurants)  if db_trip.raw_restaurants  else []
            all_attrs  = json.loads(db_trip.raw_attractions)  if db_trip.raw_attractions  else []
            logger.info('Finalize: session %s resolved from DB (trip id=%d)', session_id[:8], db_trip.id)
        else:
            raise HTTPException(
                status_code=404,
                detail='Session expired — please click Start Over to generate a new guide.',
            )

        # ── Parse approved index arrays ───────────────────────────────────────
        def _parse_indices(raw, total):
            if raw is None:
                return list(range(total))
            return [int(i) for i in raw if 0 <= int(i) < total]

        approved_photo_idx = _parse_indices(body.approved_photos,      len(all_photos))
        approved_rest_idx  = _parse_indices(body.approved_restaurants,  len(all_rests))
        approved_attr_idx  = _parse_indices(body.approved_attractions,  len(all_attrs))

        photos      = [all_photos[i] for i in approved_photo_idx]
        restaurants = [all_rests[i]  for i in approved_rest_idx]
        attractions = [all_attrs[i]  for i in approved_attr_idx]

        logger.info('Finalizing session %s — approved: %d photos, %d restaurants, %d attractions',
                    session_id[:8], len(photos), len(restaurants), len(attractions))

        # ── Pre-fetch map images (async) ──────────────────────────────────────
        prefetched_maps = {}
        if PLACES_VERIFY_ENABLED:
            sections_by_day = {}
            for section, items in [('photos', photos), ('attractions', attractions)]:
                by_day = defaultdict(list)
                for item in items:
                    by_day[item.get('day', 1)].append(item)
                for day_num, day_items in by_day.items():
                    sections_by_day[(section, day_num)] = day_items
            if sections_by_day:
                logger.info('Pre-fetching %d day map image(s)...', len(sections_by_day))
                prefetched_maps = await prefetch_day_maps(sections_by_day)

        # ── Generate HTML (CPU-bound sync — run in threadpool) ───────────────
        logger.info('Generating final HTML...')
        html_content = await run_in_threadpool(
            generate_master_html,
            location, duration, photos, restaurants, attractions,
            colors, prefetched_maps,
        )

        # ── Update trip record to 'finalized' ─────────────────────────────────
        if body.trip_id is not None:
            try:
                def _finalize_trip():
                    saved_trip = db_session.get(Trip, int(body.trip_id))
                    if saved_trip and not saved_trip.is_deleted:
                        saved_trip.status                      = 'finalized'
                        saved_trip.approved_photo_indices      = json.dumps(approved_photo_idx)
                        saved_trip.approved_restaurant_indices = json.dumps(approved_rest_idx)
                        saved_trip.approved_attraction_indices = json.dumps(approved_attr_idx)
                        saved_trip.final_html                  = html_content
                        saved_trip.updated_at                  = datetime.now(timezone.utc)
                        db_session.commit()
                        logger.info('Trip finalized in DB: id=%d', saved_trip.id)

                await run_in_threadpool(_finalize_trip)
            except Exception as db_exc:
                logger.error('Failed to finalize trip in DB: %s', db_exc)

        return {
            'status':           'success',
            'html':             html_content,
            'location':         location,
            'duration':         duration,
            'photo_count':      len(photos),
            'restaurant_count': len(restaurants),
            'attraction_count': len(attractions),
            'model':            SCOUT_MODEL_LABEL,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error('Unhandled error in /finalize: %s', exc, exc_info=True)
        raise HTTPException(status_code=500, detail='An unexpected error occurred. Please try again.')


@app.post('/replace')
async def replace_item(
    body: ReplaceRequest,
    request: Request,
    db_session: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    """Replace a single review-screen item with an alternative. Requires login."""
    try:
        # Per-user rate limit
        allowed, retry_after = check_user_rate_limit(current_user.id, 'replace')
        if not allowed:
            logger.warning('Rate limit hit: user_id=%d /replace retry_after=%ds',
                           current_user.id, retry_after)
            raise HTTPException(
                status_code=429,
                detail=f'Too many requests. Please wait {retry_after} seconds before trying again.',
            )

        session_id = body.session_id.strip()
        item_type  = body.type
        item_idx   = body.index
        day        = body.day
        meal_type  = body.meal_type
        _client_excludes = body.exclude_names or []

        # ── Resolve scout parameters from DB trip ─────────────────────────────
        location = budget = distance = None
        interests = cuisines_str = categories = ''
        duration  = 1

        db_trip = None
        if body.trip_id is not None:
            db_trip = await run_in_threadpool(lambda: db_session.get(Trip, int(body.trip_id)))
        if db_trip is None and session_id:
            db_trip = await run_in_threadpool(
                lambda: db_session.query(Trip).filter_by(session_id=session_id, is_deleted=False).first()
            )

        if db_trip:
            location     = db_trip.location
            duration     = db_trip.duration
            budget       = db_trip.budget     or 'Moderate'
            distance     = db_trip.distance   or 'Up to 30 minutes'
            interests    = db_trip.photo_interests  or ''
            cuisines_str = db_trip.cuisines         or ''
            categories   = db_trip.attraction_cats  or ''
        elif session_id:
            sess = _session_get(session_id)
            if sess:
                location = sess['location']
                duration = sess['duration']
                budget   = 'Moderate'
                distance = 'Up to 30 minutes'
            else:
                raise HTTPException(status_code=404, detail='Session not found — please start over.')
        else:
            raise HTTPException(status_code=404, detail='Session not found — please start over.')

        if not location:
            raise HTTPException(status_code=400, detail='Could not resolve trip location.')

        # ── Build exclude_names from server-side DB data ──────────────────────
        def _names_from_raw(raw_json: str | None) -> list[str]:
            if not raw_json:
                return []
            try:
                items = json.loads(raw_json)
                return [str(it['name']) for it in items if isinstance(it, dict) and it.get('name')]
            except Exception:
                return []

        if db_trip:
            raw_col = {
                'photos':      db_trip.raw_photos,
                'restaurants': db_trip.raw_restaurants,
                'attractions': db_trip.raw_attractions,
            }.get(item_type)
            exclude_names = _names_from_raw(raw_col)
        else:
            exclude_names = [
                s for s in (
                    re.sub(r'\s+', ' ', str(n)).strip()[:MAX_EXCLUDE_NAME_LEN]
                    for n in _client_excludes if n
                ) if s
            ][:MAX_EXCLUDE_LIST_LEN]

        # ── Load client profile ───────────────────────────────────────────────
        client_profile = None
        if db_trip and db_trip.client_id:
            try:
                db_client = await run_in_threadpool(lambda: db_session.get(Client, db_trip.client_id))
                if db_client and not db_client.is_deleted:
                    client_profile = {k: v for k, v in {
                        'home_city':            db_client.home_city            or '',
                        'preferred_budget':     db_client.preferred_budget     or '',
                        'travel_style':         db_client.travel_style         or '',
                        'dietary_requirements': db_client.dietary_requirements or '',
                        'notes':                db_client.notes                or '',
                    }.items() if v}
            except Exception as cp_exc:
                logger.warning('Replace: could not load client profile: %s', cp_exc)

        # ── Build prompt ──────────────────────────────────────────────────────
        exclude_block = (
            'IMPORTANT — Do NOT suggest any of the following (already in the guide):\n'
            + '\n'.join(f'  - {n}' for n in exclude_names)
            + '\n'
        ) if exclude_names else ''

        day_context = f'Day {day} of a {duration}-day trip.'

        if item_type == 'photos':
            system_prompt = """You are a photography location scout.
Find ONE real, currently accessible photography location that has NOT already been suggested.
Return EXACTLY one JSON object, no markdown, no other text:
{
  "day": [day number],
  "time": "[best time range]",
  "name": "[Exact location name]",
  "address": "[Full street address]",
  "coordinates": "[lat, lng or area]",
  "travel_time": "N/A",
  "subject": "[What to photograph and why it works — be specific]",
  "setup": "[Where to stand, focal length, framing — actionable]",
  "light": "[Light direction and optimal window — factual]",
  "pro_tip": "[One honest, actionable tip]"
}"""
            user_prompt = f"""Find one photography location in {location}.

{exclude_block}
Context: {day_context}
Photography interests: {interests or 'general'}
Budget: {budget} | Travel radius: {distance}"""

        elif item_type == 'restaurants':
            meal_hint = f'This should be a {meal_type} option.' if meal_type else ''
            diet_hint = ''
            if client_profile and client_profile.get('dietary_requirements'):
                diet_hint = f"DIETARY HARD CONSTRAINT — never suggest anything incompatible with: {client_profile['dietary_requirements']}"
            system_prompt = f"""You are a dining guide writer.
Find ONE real restaurant that has NOT already been suggested.
{diet_hint}
Return EXACTLY one JSON object, no markdown, no other text:
{{
  "day": [day number],
  "meal_type": "[breakfast/lunch/dinner]",
  "name": "[Restaurant name]",
  "address": "[Full address]",
  "location": "[Neighbourhood]",
  "cuisine": "[Cuisine type]",
  "travel_time": "N/A",
  "description": "[2 sentences: what it is and what to order — name the dish]",
  "price": "[$/$$/$$$/$$$$]",
  "signature_dish": "[The one dish worth ordering]",
  "ambiance": "[1 sentence: what you find when you walk in]",
  "hours": "[Hours]",
  "why_this_client": "[Why this suits the stated preferences]",
  "insider_tip": "[One piece of practical advice]"
}}"""
            user_prompt = f"""Find one restaurant in {location}.

{exclude_block}
Context: {day_context} {meal_hint}
Cuisine preferences: {cuisines_str or 'any local'}
Budget: {budget} | Travel radius: {distance}"""

        else:  # attractions
            system_prompt = """You are a travel writer.
Find ONE real, currently accessible attraction that has NOT already been suggested.
Return EXACTLY one JSON object, no markdown, no other text:
{
  "day": [day number],
  "time": "[time slot]",
  "name": "[Attraction name]",
  "address": "[Full address]",
  "category": "[Type]",
  "location": "[Neighbourhood]",
  "travel_time": "N/A",
  "description": "[2 sentences: what it is and why it is worth the visit]",
  "admission": "[Free / price]",
  "hours": "[Hours]",
  "duration": "[Realistic visit length]",
  "best_time": "[Specific time advice]",
  "why_this_client": "[Why this suits the stated preferences]",
  "highlight": "[Single best specific thing]",
  "insider_tip": "[One practical tip most visitors miss]"
}"""
            user_prompt = f"""Find one attraction in {location}.

{exclude_block}
Context: {day_context}
Attraction interests: {categories or 'general sightseeing'}
Budget: {budget} | Travel radius: {distance}"""

        logger.info('Replace: type=%s idx=%d day=%d location=%s excluded=%d',
                    item_type, item_idx, day, location, len(exclude_names))

        message = await anthropic_client.messages.create(
            model=SCOUT_MODEL,
            max_tokens=1200,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )

        raw_text = ''
        for block in message.content:
            block_text = getattr(block, 'text', None)
            if block_text:
                raw_text = str(block_text).strip()
                break

        # Strip markdown code fences
        if raw_text.startswith('```'):
            parts    = raw_text.split('```', 2)
            inner    = parts[1] if len(parts) >= 2 else raw_text
            if inner.startswith('json'):
                inner = inner[4:]
            raw_text = inner.strip()

        items = _parse_json_lines(raw_text, 'Replace Scout')
        if not items:
            try:
                items = [json.loads(raw_text)]
            except Exception:
                pass

        if not items:
            logger.warning('Replace Scout: failed to parse response for %s idx=%d', item_type, item_idx)
            raise HTTPException(
                status_code=422,
                detail='Could not find an alternative. Try again or toggle this item off.',
            )

        new_item = items[0]

        # ── Places verification ───────────────────────────────────────────────
        if PLACES_VERIFY_ENABLED:
            verified, _ = await verify_places_batch([new_item], 'name', 'address', location)
            if verified:
                new_item = verified[0]

        # ── Apply haversine distance if accommodation available ───────────────
        if db_trip and db_trip.accommodation and PLACES_VERIFY_ENABLED:
            acc_lat, acc_lng = await _geocode_accommodation(db_trip.accommodation)
            if acc_lat is not None:
                _apply_distances([new_item], acc_lat, acc_lng)

        # ── Update DB trip record ─────────────────────────────────────────────
        if db_trip:
            try:
                def _update_db():
                    arr_field = {
                        'photos':      'raw_photos',
                        'restaurants': 'raw_restaurants',
                        'attractions': 'raw_attractions',
                    }[item_type]
                    raw_arr = json.loads(getattr(db_trip, arr_field) or '[]')
                    if item_idx < len(raw_arr):
                        raw_arr[item_idx] = new_item
                        setattr(db_trip, arr_field, json.dumps(raw_arr))
                        db_trip.updated_at = datetime.now(timezone.utc)
                        db_session.commit()
                        logger.info('Replace: DB trip %d updated — %s[%d] replaced',
                                    db_trip.id, item_type, item_idx)

                await run_in_threadpool(_update_db)
            except Exception as db_exc:
                logger.error('Replace: DB update failed: %s', db_exc)

        # ── Update session store ──────────────────────────────────────────────
        if session_id:
            sess = _session_get(session_id)
            if sess:
                sess_arr = sess.get(item_type, [])
                if item_idx < len(sess_arr):
                    sess_arr[item_idx] = new_item
                    sess[item_type]    = sess_arr
                    _session_set(session_id, sess)

        return {'item': new_item}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error('Unhandled error in /replace: %s', exc, exc_info=True)
        raise HTTPException(status_code=500, detail='An unexpected error occurred. Please try again.')
