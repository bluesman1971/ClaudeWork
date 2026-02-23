#!/usr/bin/env python3
"""
Trip Master Web App - Backend API (Haiku 4.5 with Rich Photo Details)
Orchestrates Photo Scout, Restaurant Scout, and Attraction Scout via Anthropic API
"""

import os
import re
import json
import time
import uuid
import base64
import logging
import hashlib
import urllib.parse
import urllib.request
from collections import defaultdict
from html import escape
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, g, send_from_directory
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv

from models import db, Trip
from auth import auth_bp, require_auth, register_cli
from clients import clients_bp
from trips import trips_bp

# Load .env from the same directory as this file, regardless of working directory
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Database configuration ───────────────────────────────────────────────────
# Default: SQLite (trip_master.db in the project folder).
# For production, set DATABASE_URL to a PostgreSQL connection string.
#
# URL safety: special characters in the password (@, #, ?, /, +, etc.) can
# break SQLAlchemy's URL parser.  We use SQLAlchemy's own make_url() to parse
# the raw string and re-serialise it — which correctly percent-encodes the
# password component — before handing it to SQLALCHEMY_DATABASE_URI.
# This means you can paste the raw Supabase connection string into Railway
# without manually URL-encoding anything.

def _safe_db_url(raw: str) -> str:
    """
    Parse a database URL with make_url() so SQLAlchemy handles any special
    characters in the password, then return the normalised string.
    Falls through unchanged for SQLite URLs (no password to worry about).
    """
    if not raw or raw.startswith('sqlite'):
        return raw
    try:
        from sqlalchemy.engine import make_url
        u = make_url(raw)
        # Re-serialise: make_url percent-encodes the password automatically
        return u.render_as_string(hide_password=False)
    except Exception as exc:
        logger.warning("Could not parse DATABASE_URL with make_url (%s) — using raw value", exc)
        return raw

_raw_db_url = os.getenv('DATABASE_URL', f"sqlite:///{os.path.join(os.path.dirname(__file__), 'trip_master.db')}")
_db_url     = _safe_db_url(_raw_db_url)

app.config['SQLALCHEMY_DATABASE_URI']        = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SQLite concurrency tuning: raise the busy-timeout so concurrent writes queue
# instead of immediately raising "database is locked".  WAL mode is applied via
# event.listen below so every connection (not just the first) benefits.
if _db_url.startswith('sqlite'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'timeout': 15}}

# ── JWT secret key ───────────────────────────────────────────────────────────
_jwt_secret = os.getenv('JWT_SECRET_KEY', '')
if not _jwt_secret:
    if os.getenv('FLASK_ENV') == 'production':
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable must be set in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    _jwt_secret = 'dev-secret-change-me'
    logger.warning("JWT_SECRET_KEY not set — using insecure default. Set it in .env for any real use.")
app.config['JWT_SECRET_KEY'] = _jwt_secret

# Initialise extensions
db.init_app(app)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(clients_bp)
app.register_blueprint(trips_bp)

# Register CLI commands (flask create-user)
register_cli(app)

# Create DB tables and configure SQLite WAL mode.
# The app_context block is required here because db.engine is lazily bound to
# the app — accessing it outside a context raises RuntimeError.
from sqlalchemy import event as _sa_event  # local alias avoids polluting namespace

with app.app_context():
    # Enable WAL (Write-Ahead Logging) for SQLite on every new connection.
    # WAL allows concurrent readers and a single writer simultaneously; the
    # default journal mode serialises all connections.  Registering via
    # event.listen ensures every pooled connection — not just the first —
    # gets WAL automatically.  This is a no-op for PostgreSQL and other DBs.
    if _db_url.startswith('sqlite'):
        @_sa_event.listens_for(db.engine, 'connect')
        def _set_sqlite_wal(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.close()

        # Also convert any existing DB file to WAL and prime the connection pool.
        # We use a raw DBAPI cursor to bypass SQLAlchemy's transaction wrapping —
        # PRAGMA journal_mode=WAL must be committed outside a transaction to
        # take effect on the persistent file.
        _raw_conn = db.engine.raw_connection()
        try:
            _cur = _raw_conn.cursor()
            _cur.execute('PRAGMA journal_mode=WAL')
            _cur.close()
            _raw_conn.commit()
        finally:
            _raw_conn.close()

    db.create_all()

    # ── Column migrations for existing databases ──────────────────────────
    # db.create_all() adds new *tables* but not new *columns* to existing ones.
    # Run raw ALTER TABLE ... ADD COLUMN IF NOT EXISTS for each column added
    # after the initial schema was deployed.  Safe to re-run on every startup.
    _migrations = [
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS dietary_requirements TEXT",
        "ALTER TABLE trips ADD COLUMN IF NOT EXISTS accommodation VARCHAR(500)",
    ]
    try:
        with db.engine.connect() as _conn:
            for _sql in _migrations:
                _conn.execute(db.text(_sql))
            _conn.commit()
    except Exception as _mig_exc:
        # SQLite doesn't support IF NOT EXISTS on ADD COLUMN — fall back to
        # checking the column exists first.
        logger.warning("Column migration skipped (likely SQLite): %s", _mig_exc)

# CORS origins — comma-separated list read from the environment variable so
# production deployments don't need code changes.  Falls back to localhost for
# local development.  Set CORS_ORIGINS in .env or the server environment, e.g.:
#   CORS_ORIGINS=https://tripmaster.example.com,https://www.tripmaster.example.com
_cors_origins = [
    o.strip() for o in
    os.getenv('CORS_ORIGINS', 'http://localhost:5000,http://127.0.0.1:5000').split(',')
    if o.strip()
]
CORS(app, origins=_cors_origins, supports_credentials=True)

# ── HTTP security headers ─────────────────────────────────────────────────────
# Applied to every response.  These headers are defence-in-depth that don't
# require any application logic changes and have negligible performance cost.
@app.after_request
def _set_security_headers(response):
    # Prevent browsers from MIME-sniffing the content-type
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Block the page from being embedded in an iframe (clickjacking protection)
    response.headers['X-Frame-Options'] = 'DENY'
    # Enable browser XSS filter (legacy browsers)
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Only send the origin (no path/query) in the Referer header when navigating away
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Restrict powerful browser features that this app doesn't need
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    # HSTS: tell browsers to always use HTTPS (production only — dev may use HTTP)
    if os.getenv('FLASK_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# Initialize Anthropic client (renamed to avoid clash with 'client' variable in route bodies)
# Use the plain default client — curl confirms Railway can reach api.anthropic.com
# fine, so no custom transport or proxy configuration is needed.
anthropic_client = Anthropic()

# --- Constants ---

# ── Scout model ───────────────────────────────────────────────────────────────
# Change SCOUT_MODEL in .env to switch all three scouts to a new model without
# touching any other code.  The display name is derived automatically so the
# health endpoint and response JSON stay in sync.
SCOUT_MODEL        = os.getenv('SCOUT_MODEL', 'claude-haiku-4-5-20251001')
SCOUT_MODEL_LABEL  = os.getenv('SCOUT_MODEL_LABEL', 'Claude Haiku 4.5')

PHOTOS_PER_DAY = 3
RESTAURANTS_PER_DAY = 3
ATTRACTIONS_PER_DAY = 4
MAX_LOCATION_LENGTH   = 100
MAX_FIELD_SHORT       = 150   # single-line fields: accommodation, travel_style, home_city, dietary_requirements
MAX_FIELD_MEDIUM      = 500   # multi-line fields: pre_planned, notes
MAX_EXCLUDE_NAME_LEN  = 100   # each name in exclude_names
MAX_EXCLUDE_LIST_LEN  = 50    # total items in exclude_names list
MIN_DURATION = 1
MAX_DURATION = 14


def _sanitise_line(value, max_len: int) -> str | None:
    """
    Sanitise a single-line text field before prompt interpolation.
    - Collapses all whitespace (including \\n, \\r, \\t) to a single space
    - Strips leading/trailing whitespace
    - Truncates to max_len characters
    - Returns None if the result is empty

    Use this for every field that appears on a single logical line in a
    prompt (location, accommodation, dietary_requirements, etc.).
    Multi-line fields (pre_planned, notes) should only be stripped, not
    collapsed, since embedded newlines are intentional there.
    """
    s = re.sub(r'\s+', ' ', str(value)).strip()[:max_len]
    return s or None

# Google Places API
GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')
PLACES_VERIFY_ENABLED = bool(GOOGLE_PLACES_API_KEY)
PLACES_API_URL = 'https://places.googleapis.com/v1/places:searchText'
PLACES_VERIFY_TIMEOUT = 5  # seconds per request

# Verification status constants
STATUS_OPERATIONAL         = 'OPERATIONAL'
STATUS_CLOSED_TEMPORARILY  = 'CLOSED_TEMPORARILY'
STATUS_CLOSED_PERMANENTLY  = 'CLOSED_PERMANENTLY'
STATUS_UNVERIFIED          = 'UNVERIFIED'

# Simple in-memory cache: key -> (timestamp, result)
_cache = {}
CACHE_TTL_SECONDS = 3600  # 1 hour

# Review session store: session_id -> { ts, location, duration, colors, photos, restaurants, attractions }
# Holds raw verified item data between /generate and /finalize so we don't re-run scouts.
_session_store = {}
SESSION_TTL_SECONDS = 3600  # 1 hour

# Scout retry settings — if a scout returns 0 items (malformed response or Places wipeout),
# retry up to SCOUT_MAX_RETRIES times before surfacing a warning to the user.
# Empty results are never cached, so each retry always hits the API fresh.
SCOUT_MAX_RETRIES = 2    # attempts after the initial run
SCOUT_RETRY_DELAY = 1.0  # seconds between retry attempts

# Color palettes for different locations
COLOR_PALETTES = {
    "barcelona": {
        "primary": "#c41e3a",
        "accent": "#f4a261",
        "secondary": "#2a9d8f",
        "neutral": "#f5e6d3"
    },
    "paris": {
        "primary": "#1a1a2e",
        "accent": "#d4a574",
        "secondary": "#16213e",
        "neutral": "#f0e6d2"
    },
    "tokyo": {
        "primary": "#8B0000",
        "accent": "#FFD700",
        "secondary": "#1a1a1a",
        "neutral": "#f5f5f5"
    },
    "default": {
        "primary": "#2c3e50",
        "accent": "#e67e22",
        "secondary": "#34495e",
        "neutral": "#ecf0f1"
    }
}


def verify_place_with_google(name, address, location_context):
    """
    Query the Google Places API (Text Search) to verify a place is still
    operational. Returns a dict with:
      - status: OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY | UNVERIFIED
      - maps_url: confirmed Google Maps URL (or None)
      - place_id: Places place ID (or None)
    Falls back to UNVERIFIED on any error.
    """
    if not PLACES_VERIFY_ENABLED:
        return {'status': STATUS_UNVERIFIED, 'maps_url': None, 'place_id': None, 'lat': None, 'lng': None}

    # Build a specific search query: "Name, Address, City"
    query_parts = [p for p in [name, address, location_context] if p]
    query = ', '.join(query_parts)

    payload = json.dumps({
        'textQuery': query,
        'maxResultCount': 1,
    }).encode('utf-8')

    req = urllib.request.Request(
        PLACES_API_URL,
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': GOOGLE_PLACES_API_KEY,
            'X-Goog-FieldMask': 'places.id,places.displayName,places.businessStatus,places.googleMapsUri,places.location',
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=PLACES_VERIFY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        places = data.get('places', [])
        if not places:
            logger.info("Places API: no result for %r", query)
            return {'status': STATUS_UNVERIFIED, 'maps_url': None, 'place_id': None, 'lat': None, 'lng': None}

        place = places[0]
        business_status = place.get('businessStatus', STATUS_UNVERIFIED)
        place_id  = place.get('id')
        maps_url  = place.get('googleMapsUri')

        # Extract lat/lng if provided
        loc = place.get('location', {})
        lat = loc.get('latitude')
        lng = loc.get('longitude')

        # Normalise: API returns e.g. "OPERATIONAL" or "CLOSED_PERMANENTLY"
        if business_status not in (STATUS_OPERATIONAL, STATUS_CLOSED_TEMPORARILY, STATUS_CLOSED_PERMANENTLY):
            business_status = STATUS_UNVERIFIED

        logger.info("Places API: %r → %s (place_id=%s lat=%s lng=%s)",
                    query[:60], business_status, place_id, lat, lng)
        return {'status': business_status, 'maps_url': maps_url, 'place_id': place_id,
                'lat': lat, 'lng': lng}

    except Exception as exc:
        logger.warning("Places API error for %r: %s", query[:60], exc)
        return {'status': STATUS_UNVERIFIED, 'maps_url': None, 'place_id': None, 'lat': None, 'lng': None}


def verify_places_batch(items, name_key, address_key, location_context):
    """
    Run Places verification for a list of items in parallel.
    Attaches '_status', '_maps_url', '_place_id' to each item in-place.
    Permanently closed items are removed from the returned list.
    Returns (verified_items, removed_count).
    """
    if not items:
        return items, 0

    # Launch all verifications concurrently.
    # Each future is wrapped in _with_app_context so that any future developer
    # who adds DB or current_app calls inside verify_place_with_google won't
    # hit a RuntimeError: Working outside of application context.
    with ThreadPoolExecutor(max_workers=min(10, len(items))) as executor:
        futures = {
            executor.submit(
                _with_app_context,
                verify_place_with_google,
                item.get(name_key, ''),
                item.get(address_key, ''),
                location_context
            ): item
            for item in items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.warning("Verification future error: %s", exc)
                result = {'status': STATUS_UNVERIFIED, 'maps_url': None, 'place_id': None, 'lat': None, 'lng': None}
            item['_status']   = result['status']
            item['_maps_url'] = result['maps_url']
            item['_place_id'] = result['place_id']
            item['_lat']      = result.get('lat')
            item['_lng']      = result.get('lng')

    # Filter out any place that verification explicitly marks as unavailable.
    # Only OPERATIONAL and UNVERIFIED items are kept — temporarily closed is
    # treated the same as permanently closed (removed silently, never shown).
    UNAVAILABLE = {STATUS_CLOSED_PERMANENTLY, STATUS_CLOSED_TEMPORARILY}
    verified = [i for i in items if i.get('_status') not in UNAVAILABLE]
    removed  = len(items) - len(verified)

    if removed:
        perm  = sum(1 for i in items if i.get('_status') == STATUS_CLOSED_PERMANENTLY)
        temp  = sum(1 for i in items if i.get('_status') == STATUS_CLOSED_TEMPORARILY)
        logger.info(
            "Places verification: removed %d unavailable location(s) from %d candidates "
            "(%d permanently closed, %d temporarily closed)",
            removed, len(items), perm, temp
        )
    return verified, removed


def _geocode_accommodation(address: str):
    """
    Look up the lat/lng of the accommodation address using the Places API
    text search (same key and endpoint already used for venue verification).
    Returns (lat, lng) floats, or (None, None) on failure / no API key.
    """
    if not PLACES_VERIFY_ENABLED or not address:
        return None, None
    try:
        payload = json.dumps({'textQuery': address, 'maxResultCount': 1}).encode()
        req = urllib.request.Request(
            PLACES_API_URL,
            data=payload,
            headers={
                'Content-Type':    'application/json',
                'X-Goog-Api-Key':  GOOGLE_PLACES_API_KEY,
                'X-Goog-FieldMask': 'places.location',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=PLACES_VERIFY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        places = data.get('places', [])
        if not places:
            return None, None
        loc = places[0].get('location', {})
        lat, lng = loc.get('latitude'), loc.get('longitude')
        if lat is not None and lng is not None:
            logger.info("Accommodation geocoded: %r → (%.5f, %.5f)", address[:60], lat, lng)
            return float(lat), float(lng)
    except Exception as exc:
        logger.warning("Accommodation geocoding failed for %r: %s", address[:60], exc)
    return None, None


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance in metres between two lat/lng points."""
    import math
    R = 6_371_000  # Earth radius in metres
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lng2 - lng1)
    a = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _format_distance(metres: float) -> str:
    """
    Format a straight-line distance as a human-readable travel-time estimate.
    Uses 80 m/min walking speed — a comfortable urban pace that accounts for
    pavements and crossings without being as pessimistic as 60 m/min.
    Straight-line distances are always shorter than actual routes, so the
    walking-time estimate is intentionally a lower bound; the label says
    '~' throughout to signal it is approximate.
    """
    walk_min = max(1, round(metres / 80))
    if metres < 150:
        return f"~{round(metres / 10) * 10} m · ~{walk_min} min walk"
    if metres < 1000:
        return f"~{round(metres / 50) * 50} m · ~{walk_min} min walk"
    km = metres / 1000
    return f"~{km:.1f} km · ~{walk_min} min walk"


def _apply_distances(items: list, acc_lat: float, acc_lng: float) -> None:
    """
    Overwrite the `travel_time` field on each item that has verified
    coordinates (_lat / _lng) with a haversine-derived distance estimate.
    Items without coordinates (unverified) keep their Claude-generated text.
    Mutates items in-place — no return value.
    """
    for item in items:
        lat = item.get('_lat')
        lng = item.get('_lng')
        if lat is not None and lng is not None:
            metres = _haversine_m(acc_lat, acc_lng, lat, lng)
            item['travel_time'] = _format_distance(metres)


def get_color_palette(location):
    """Get color palette based on location"""
    location_key = location.lower().split(",")[0].strip()
    return COLOR_PALETTES.get(location_key, COLOR_PALETTES["default"])


def _cache_key(*args):
    """Generate a stable cache key from arguments."""
    raw = json.dumps(args, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key):
    """Return cached value if still valid, else None."""
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]
    return None


def _set_cached(key, value):
    """Store value in cache with current timestamp."""
    _cache[key] = (time.time(), value)


def _evict_sessions():
    """Remove expired review sessions from memory."""
    cutoff = time.time() - SESSION_TTL_SECONDS
    expired = [k for k, v in _session_store.items() if v['ts'] < cutoff]
    for k in expired:
        del _session_store[k]
    if expired:
        logger.info("Evicted %d expired review session(s)", len(expired))


def _parse_json_lines(text, scout_name):
    """
    Parse newline-delimited JSON from an API response.
    Logs a warning for any line that fails to parse.
    """
    results = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("%s: failed to parse JSON line (%s): %r", scout_name, exc, line[:120])
    return results


def call_photo_scout(location, duration, interests, distance, per_day=None,
                     accommodation=None, pre_planned=None, client_profile=None):
    """Call Claude to generate detailed photography locations with coordinates.

    accommodation:  optional hotel/address string — travel origin for distance advice.
    pre_planned:    optional free-text — already-booked or must-see items the client
                    has committed to; scouts avoid duplicating these.
    client_profile: optional dict with keys home_city, preferred_budget,
                    travel_style, dietary_requirements, notes.
    """
    if per_day is None:
        per_day = PHOTOS_PER_DAY
    key = _cache_key("photo", location, duration, interests, distance, per_day,
                     accommodation, pre_planned,
                     json.dumps(client_profile or {}, sort_keys=True))
    cached = _get_cached(key)
    if cached is not None:
        logger.info("Photo Scout: cache hit for %s", location)
        return cached

    count = duration * per_day

    # ── Build contextual blocks ──────────────────────────────────────────────
    accommodation_block = (
        f"- Accommodation / travel base: {accommodation}\n"
        f"  Distance and logistics notes must be calculated from this address, not the city centre.\n"
        if accommodation else
        "- Accommodation: not specified — use city centre as the assumed travel base.\n"
    )

    pre_planned_block = (
        f"Already planned / committed:\n  {pre_planned}\n"
        f"  Do NOT suggest anything that duplicates or conflicts with the above.\n"
        if pre_planned else ""
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
        profile_lines.append(f"  Dietary requirements: {profile['dietary_requirements']} — respect these if any location involves food (e.g. café stops, food markets)")
    if profile.get('notes'):
        profile_lines.append(f"  Consultant notes: {profile['notes']}")

    client_block = (
        "Client profile:\n" + "\n".join(profile_lines) + "\n"
        if profile_lines else
        "Client profile: none provided — give broadly appealing recommendations.\n"
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

    message = anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=6000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    locations = _parse_json_lines(message.content[0].text, "Photo Scout")
    logger.info("Photo Scout: parsed %d/%d locations for %s", len(locations), count, location)
    if locations:  # never cache empty — a failed parse should retry next time
        _set_cached(key, locations)
    return locations


def call_restaurant_scout(location, duration, cuisines, budget, distance, per_day=None,
                          accommodation=None, pre_planned=None, client_profile=None):
    """Call Claude to generate restaurant recommendations.

    accommodation:  optional hotel/address string — travel origin for distance advice.
    pre_planned:    optional free-text — already-booked or must-see items the client
                    has committed to; scouts avoid duplicating these.
    client_profile: optional dict with keys home_city, preferred_budget,
                    travel_style, dietary_requirements, notes.
    """
    if per_day is None:
        per_day = RESTAURANTS_PER_DAY
    key = _cache_key("restaurant", location, duration, cuisines, budget, distance, per_day,
                     accommodation, pre_planned,
                     json.dumps(client_profile or {}, sort_keys=True))
    cached = _get_cached(key)
    if cached is not None:
        logger.info("Restaurant Scout: cache hit for %s", location)
        return cached

    count = duration * per_day

    # ── Build contextual blocks ──────────────────────────────────────────────
    accommodation_block = (
        f"- Accommodation / travel base: {accommodation}\n"
        f"  Distance notes must be calculated from this address, not the city centre.\n"
        if accommodation else
        "- Accommodation: not specified — use city centre as the assumed travel base.\n"
    )

    pre_planned_block = (
        f"Already planned / committed:\n  {pre_planned}\n"
        f"  Do NOT suggest any restaurant that duplicates or conflicts with the above.\n"
        f"  If a meal slot is clearly covered by a pre-planned event, skip that slot rather than\n"
        f"  adding a competing recommendation.\n"
        if pre_planned else ""
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
        "Client profile:\n" + "\n".join(profile_lines) + "\n"
        if profile_lines else
        "Client profile: none provided — give broadly appealing recommendations.\n"
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

    message = anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=5000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    restaurants = _parse_json_lines(message.content[0].text, "Restaurant Scout")
    logger.info("Restaurant Scout: parsed %d/%d restaurants for %s", len(restaurants), count, location)
    if restaurants:
        _set_cached(key, restaurants)
    return restaurants


def call_attraction_scout(location, duration, categories, budget, distance, per_day=None,
                          accommodation=None, pre_planned=None, client_profile=None):
    """Call Claude to generate attractions with location details.

    accommodation:  optional hotel/address string — travel origin for distance advice.
    pre_planned:    optional free-text — already-booked or must-see items the client
                    has committed to; scouts avoid duplicating these.
    client_profile: optional dict with keys home_city, preferred_budget,
                    travel_style, dietary_requirements, notes.
    """
    if per_day is None:
        per_day = ATTRACTIONS_PER_DAY
    key = _cache_key("attraction", location, duration, categories, budget, distance, per_day,
                     accommodation, pre_planned,
                     json.dumps(client_profile or {}, sort_keys=True))
    cached = _get_cached(key)
    if cached is not None:
        logger.info("Attraction Scout: cache hit for %s", location)
        return cached

    count = duration * per_day

    # ── Build contextual blocks ──────────────────────────────────────────────
    accommodation_block = (
        f"- Accommodation / travel base: {accommodation}\n"
        f"  Group each day's attractions geographically so the client isn't backtracking.\n"
        f"  Distance and travel time must be calculated from this address, not the city centre.\n"
        if accommodation else
        "- Accommodation: not specified — use city centre as the assumed travel base.\n"
    )

    pre_planned_block = (
        f"Already planned / committed:\n  {pre_planned}\n"
        f"  Do NOT suggest anything that duplicates or conflicts with the above.\n"
        f"  If a time slot is already committed, plan around it — suggest complementary nearby\n"
        f"  stops rather than competing alternatives for the same slot.\n"
        if pre_planned else ""
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
        profile_lines.append(f"  Dietary requirements: {profile['dietary_requirements']} — if any attraction involves food (food markets, cooking classes, winery tours), ensure it is compatible")
    if profile.get('notes'):
        profile_lines.append(f"  Consultant notes: {profile['notes']}")

    client_block = (
        "Client profile:\n" + "\n".join(profile_lines) + "\n"
        if profile_lines else
        "Client profile: none provided — give broadly appealing recommendations.\n"
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

    message = anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=5000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    attractions = _parse_json_lines(message.content[0].text, "Attraction Scout")
    logger.info("Attraction Scout: parsed %d/%d attractions for %s", len(attractions), count, location)
    if attractions:
        _set_cached(key, attractions)
    return attractions


def create_google_maps_link(name, address, coordinates):
    """Create a Google Maps link from location data"""
    if coordinates and ',' in str(coordinates):
        return f"https://www.google.com/maps/search/{coordinates.replace(' ', '')}"
    elif address:
        return f"https://www.google.com/maps/search/{address.replace(' ', '+').replace(',', '%2C')}"
    else:
        return f"https://www.google.com/maps/search/{name.replace(' ', '+')}"


def _e(value, fallback='N/A'):
    """HTML-escape a string value from untrusted API data."""
    return escape(str(value)) if value else fallback


def _fetch_static_map_as_base64(img_url):
    """
    Fetch a Google Static Maps image and return it as a base64 data URI string.
    Returns None on any error or if the URL fails the domain whitelist check.

    Security: strictly validates the URL before making any outbound request to
    prevent Server-Side Request Forgery (SSRF).  An attacker who can influence
    the URL could otherwise redirect the server to fetch internal metadata
    endpoints (e.g. http://169.254.169.254) or internal services.
    """
    # SSRF guard — only ever fetch from the Google Static Maps API over HTTPS.
    if not img_url.startswith('https://maps.googleapis.com/'):
        logger.error("SSRF guard: blocked outbound fetch to unauthorized URL: %s", img_url[:80])
        return None
    try:
        req = urllib.request.Request(img_url, headers={'User-Agent': 'TripGuideApp/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            content_type = resp.headers.get('Content-Type', 'image/png').split(';')[0].strip()
        b64 = base64.b64encode(raw).decode('ascii')
        return f"data:{content_type};base64,{b64}"
    except Exception as exc:
        logger.warning("Static map fetch failed: %s", exc)
        return None


def _build_static_map_url(day_items):
    """
    Given a list of items for one day, return (img_url, maps_link, location_list_html)
    or None if there are no verified coordinates.

    img_url      — Static Maps API URL (not yet fetched)
    maps_link    — Google Maps deep-link with all pins
    location_list_html — fallback numbered list as HTML
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

    labels = '123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    marker_parts = []
    for idx, (lat, lng, _) in enumerate(pinned):
        label = labels[idx] if idx < len(labels) else 'X'
        marker_parts.append(f"color:red|label:{label}|{lat},{lng}")

    zoom = 15 if len(pinned) == 1 else (14 if len(pinned) <= 3 else 13)
    params_str = urllib.parse.urlencode({
        'size': '900x380', 'scale': '2', 'zoom': zoom, 'key': GOOGLE_PLACES_API_KEY,
    })
    for mp in marker_parts:
        params_str += '&markers=' + urllib.parse.quote(mp, safe='')

    img_url = f"https://maps.googleapis.com/maps/api/staticmap?{params_str}"

    all_coords = '|'.join(f"{lat},{lng}" for lat, lng, _ in pinned)
    maps_link = escape(
        "https://www.google.com/maps/search/?"
        + urllib.parse.urlencode({'api': '1', 'query': all_coords})
    )

    return img_url, maps_link, location_list_html


def build_day_map_html(data_uri, maps_link, location_list_html):
    """
    Given a pre-fetched base64 data_uri (or None), build the map HTML snippet.
    Falls back to the location list if no image is available.
    """
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


def prefetch_day_maps(sections_by_day):
    """
    Pre-fetch all static map images in parallel.

    sections_by_day: dict of { map_key: day_items_list }
      where map_key is any hashable identifier (e.g. ('photos', 1))

    Returns: dict of { map_key: (data_uri_or_None, maps_link, location_list_html) }

    Thread safety note: the worker closure only performs external HTTP I/O
    (_fetch_static_map_as_base64).  If you ever add Flask current_app access or
    SQLAlchemy DB calls inside this closure, push an app context first:
        with app.app_context():
            ...db work...
    Failure to do so with multiple Gunicorn workers will cause silent RuntimeErrors.
    """
    # Build URLs first (pure computation, no I/O)
    url_map = {}  # map_key -> (img_url, maps_link, location_list_html)
    for key, day_items in sections_by_day.items():
        result = _build_static_map_url(day_items)
        if result:
            url_map[key] = result

    if not url_map:
        return {}

    # Fetch all images in parallel.
    # _with_app_context ensures each thread has a Flask app context for any
    # future additions (DB lookups, current_app.config access, etc.).
    fetched = {}
    def _fetch(key, img_url, maps_link, location_list_html):
        data_uri = _fetch_static_map_as_base64(img_url)
        return key, data_uri, maps_link, location_list_html

    with ThreadPoolExecutor(max_workers=min(8, len(url_map))) as executor:
        futures = [
            executor.submit(_with_app_context, _fetch, k, img_url, maps_link, loc_list)
            for k, (img_url, maps_link, loc_list) in url_map.items()
        ]
        for future in as_completed(futures):
            try:
                key, data_uri, maps_link, location_list_html = future.result()
                fetched[key] = (data_uri, maps_link, location_list_html)
            except Exception as exc:
                logger.warning("Map prefetch error: %s", exc)

    logger.info("Prefetched %d/%d day map images", sum(1 for v in fetched.values() if v[0]), len(url_map))
    return fetched


def _verification_badge_html(item):
    """Return the badge HTML and best maps URL for a verified item.

    Only OPERATIONAL and UNVERIFIED items reach this point — temporarily and
    permanently closed items are filtered out in verify_places_batch before
    HTML generation, so we don't need to handle those states here.
    """
    status = item.get('_status')
    confirmed_url = item.get('_maps_url')

    if status == STATUS_OPERATIONAL:
        badge = '<span class="verify-badge verified">✓ Verified Open</span>'
    else:
        # UNVERIFIED or any unexpected state — neutral prompt
        badge = '<span class="verify-badge unverified">Unverified — confirm before visiting</span>'

    return badge, '', confirmed_url


def generate_master_html(location, duration, photos, restaurants, attractions, colors, prefetched_maps=None):
    """Generate unified HTML master document — Editorial theme.
    Sections with empty lists are omitted entirely; section numbers are
    assigned dynamically (I, II, III) based on what's present."""

    safe_location = escape(location)
    generated_date = datetime.now().strftime('%B %d, %Y')

    # Derive a readable city name for the cover (strip country suffix)
    city_name = escape(location.split(',')[0].strip())

    # Determine which sections to render and their roman numeral index
    roman = ['I', 'II', 'III']
    active_sections = []
    if photos:
        active_sections.append('photos')
    if restaurants:
        active_sections.append('restaurants')
    if attractions:
        active_sections.append('attractions')

    # Build a dynamic cover footer text and cover dek
    section_names = {
        'photos': 'Photography',
        'restaurants': 'Dining',
        'attractions': 'Attractions',
    }
    cover_footer_sections = ' &middot; '.join(section_names[s] for s in active_sections) or 'Custom Guide'
    cover_dek_parts = []
    if photos:       cover_dek_parts.append('photography locations')
    if restaurants:  cover_dek_parts.append('dining recommendations')
    if attractions:  cover_dek_parts.append('attractions worth seeking out')
    cover_dek_text = ', '.join(cover_dek_parts[:-1]) + (
        f' and {cover_dek_parts[-1]}' if len(cover_dek_parts) > 1 else (cover_dek_parts[0] if cover_dek_parts else 'highlights')
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

        /* Compass SVG decoration */
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

        .section-title-group {{}}

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

        /* Text fallback list when no map image is available */
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
            /* Map image is a base64 data URI — no external fetch needed, prints cleanly */
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
            /* Hide the hover "Open in Google Maps" badge in print */
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

    # ── Build each active section ──
    section_idx = 0  # 0-based index into roman numerals

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
        # Group photos by day for per-day map embeds
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

            # Embed the per-day pinned locations map (pre-fetched, no inline HTTP)
            _map_data = (prefetched_maps or {}).get(('photos', day_num))
            map_img = build_day_map_html(*_map_data) if _map_data else ''
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
                    photo.get('name', ''),
                    photo.get('address', ''),
                    photo.get('coordinates', '')
                )
                badge, notice, confirmed_url = _verification_badge_html(photo)
                maps_url = escape(confirmed_url or fallback_maps_url)
                card_class = 'item-card'
                travel_time = photo.get('travel_time', '')
                travel_time_html = (
                    f'<div class="meta-cell"><span class="meta-label">From Accommodation</span>'
                    f'<span class="meta-value">{_e(travel_time)}</span></div>'
                ) if travel_time and travel_time.upper() != 'N/A' else ''
                html += f"""
        <div class="{card_class}">
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
                    {travel_time_html}
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
                restaurant.get('name', ''),
                restaurant.get('address', ''),
                ''
            )
            badge, notice, confirmed_url = _verification_badge_html(restaurant)
            maps_url = escape(confirmed_url or fallback_maps_url)
            card_class = 'item-card'
            meal  = _e(restaurant.get('meal_type'), '').title()
            price = _e(restaurant.get('price'), '')
            r_travel_time  = restaurant.get('travel_time', '')
            r_why_client   = restaurant.get('why_this_client', '')
            r_travel_html  = (
                f'<div class="meta-cell"><span class="meta-label">From Accommodation</span>'
                f'<span class="meta-value">{_e(r_travel_time)}</span></div>'
            ) if r_travel_time and r_travel_time.upper() != 'N/A' else ''
            r_why_html = (
                f'<div class="tip-box" style="border-left-color:var(--primary)">'
                f'<span class="meta-label">Why For This Client</span>'
                f'{_e(r_why_client)}</div>'
            ) if r_why_client else ''
            html += f"""
        <div class="{card_class}">
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
        # Group attractions by day for per-day map embeds
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

            # Embed the per-day pinned locations map (pre-fetched, no inline HTTP)
            _map_data = (prefetched_maps or {}).get(('attractions', day_num))
            map_img = build_day_map_html(*_map_data) if _map_data else ''
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
                    attraction.get('name', ''),
                    attraction.get('address', ''),
                    ''
                )
                badge, notice, confirmed_url = _verification_badge_html(attraction)
                maps_url = escape(confirmed_url or fallback_maps_url)
                card_class = 'item-card'
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
        <div class="{card_class}">
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


def _with_app_context(fn, *args, **kwargs):
    """
    Run fn inside a fresh Flask application context, then return its result.

    This wrapper is used when submitting work to a ThreadPoolExecutor.  Each
    thread needs its own independent AppContext — sharing a single AppContext
    object across threads is NOT safe.  We capture the module-level `app`
    object (which IS thread-safe) and let each invocation open its own context.

    Currently our thread workers only do HTTP I/O and don't need Flask context,
    but wrapping them now means any future developer who adds current_app access
    or SQLAlchemy calls inside a worker won't hit a silent RuntimeError.
    """
    with app.app_context():
        return fn(*args, **kwargs)


def _run_single_scout(name, fn, args, kwargs, location, accommodation_coords=None):
    """
    Call one scout function and, when Google Places verification is enabled,
    immediately verify the returned items and filter out closed places.

    Combining the scout call and Places verification into one step lets the
    retry loop in /generate treat "scout returned items but Places wiped them
    all" the same as "scout returned nothing" — both cases result in an empty
    list that triggers a retry.

    If accommodation_coords is a (lat, lng) tuple, haversine distances are
    computed from that point to each verified item and written into travel_time.
    Unverified items (no _lat/_lng) keep the Claude-generated text estimate.

    Raises on API/network errors so the caller can catch and decide whether to
    retry.  Returns a list that may be empty if the scout produced no parseable
    results or all results were filtered out by Places.
    """
    items = fn(*args, **kwargs)
    if items and PLACES_VERIFY_ENABLED:
        items, _ = verify_places_batch(items, 'name', 'address', location)
    if items and accommodation_coords and accommodation_coords[0] is not None:
        _apply_distances(items, accommodation_coords[0], accommodation_coords[1])
    return items


@app.route('/', methods=['GET'])
def index():
    """Serve the frontend single-page app."""
    return send_from_directory(os.path.dirname(__file__), 'index.html')


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok", "message": f"Trip Guide API is running on {SCOUT_MODEL_LABEL}"})


@app.route('/generate', methods=['POST'])
@require_auth
def generate_trip_guide():
    """Generate complete trip guide (requires login)."""
    try:
        data = request.json

        # Validate required base fields
        required_fields = ['location', 'duration', 'budget', 'distance']
        if not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        # Validate and sanitize location (single-line — collapse whitespace)
        location = _sanitise_line(data['location'], MAX_LOCATION_LENGTH)
        if not location:
            return jsonify({"error": "Location cannot be empty"}), 400
        if len(location) > MAX_LOCATION_LENGTH:
            return jsonify({"error": f"Location must be {MAX_LOCATION_LENGTH} characters or fewer"}), 400

        # Validate duration
        try:
            duration = int(data['duration'])
        except (ValueError, TypeError):
            return jsonify({"error": "Duration must be a number"}), 400
        if not (MIN_DURATION <= duration <= MAX_DURATION):
            return jsonify({"error": f"Duration must be between {MIN_DURATION} and {MAX_DURATION} days"}), 400

        # Single-line fields: collapse whitespace + truncate
        budget        = _sanitise_line(data['budget'],                    MAX_FIELD_SHORT) or 'Moderate'
        distance      = _sanitise_line(data['distance'],                  MAX_FIELD_SHORT) or 'Up to 30 minutes'
        accommodation = _sanitise_line(data.get('accommodation', '') or '', MAX_FIELD_SHORT)
        # pre_planned is multi-line: strip ends only, preserve internal newlines
        pre_planned   = str(data.get('pre_planned', '') or '').strip()[:MAX_FIELD_MEDIUM] or None

        # Section enable/disable flags — accept both JSON booleans and string "true"/"false"
        def _parse_bool(val, default=True):
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() not in ('false', '0', 'no', 'off', '')
            if val is None:
                return default
            return bool(val)

        include_photos      = _parse_bool(data.get('include_photos'),      True)
        include_dining      = _parse_bool(data.get('include_dining'),      True)
        include_attractions = _parse_bool(data.get('include_attractions'), True)

        if not (include_photos or include_dining or include_attractions):
            return jsonify({"error": "At least one section must be enabled"}), 400

        # Per-section daily counts — validate and clamp
        def _parse_count(key, default, min_v, max_v):
            try:
                v = int(data.get(key, default))
                return max(min_v, min(max_v, v))
            except (ValueError, TypeError):
                return default

        photos_per_day      = _parse_count('photos_per_day',      PHOTOS_PER_DAY,      1, 10)
        restaurants_per_day = _parse_count('restaurants_per_day', RESTAURANTS_PER_DAY, 1, 8)
        attractions_per_day = _parse_count('attractions_per_day', ATTRACTIONS_PER_DAY, 1, 10)

        photo_interests = str(data.get('photo_interests', '')).strip()
        cuisines        = str(data.get('cuisines',        '')).strip()
        attractions     = str(data.get('attractions',     '')).strip()

        # ── Load client profile for personalised scouting ────────────────────
        # If a client_id was supplied, pull the profile fields from the DB so
        # the scouts can personalise recommendations to this specific traveller.
        # Failure is non-fatal — scouts work fine without a profile.
        client_profile = None
        raw_client_id  = data.get('client_id')
        if raw_client_id is not None:
            try:
                from models import Client as _Client
                cid = int(raw_client_id)
                db_client = db.session.get(_Client, cid)
                if db_client and not db_client.is_deleted:
                    client_profile = {
                        'home_city':            db_client.home_city            or '',
                        'preferred_budget':     db_client.preferred_budget     or '',
                        'travel_style':         db_client.travel_style         or '',
                        'dietary_requirements': db_client.dietary_requirements or '',
                        'notes':                db_client.notes                or '',
                    }
                    # Strip empty strings so prompts don't mention blank fields
                    client_profile = {k: v for k, v in client_profile.items() if v}
                    logger.info(
                        "Client profile loaded for id=%d: %s",
                        cid, list(client_profile.keys())
                    )
            except Exception as cp_exc:
                logger.warning("Could not load client profile (id=%s): %s", raw_client_id, cp_exc)

        logger.info(
            "Generating trip guide for %s, %d days | photos=%s(%d/d) dining=%s(%d/d) "
            "attractions=%s(%d/d) | accommodation=%s | pre_planned=%s | client_profile=%s",
            location, duration,
            'ON' if include_photos else 'OFF', photos_per_day,
            'ON' if include_dining else 'OFF', restaurants_per_day,
            'ON' if include_attractions else 'OFF', attractions_per_day,
            'yes' if accommodation else 'no',
            'yes' if pre_planned   else 'no',
            'yes' if client_profile else 'no',
        )

        # Build scout tasks — ONLY for sections the user explicitly enabled
        scout_tasks = {}
        if include_photos:
            scout_tasks['photos'] = (
                call_photo_scout,
                (location, duration, photo_interests, distance),
                {'per_day': photos_per_day,
                 'accommodation': accommodation,
                 'pre_planned':   pre_planned,
                 'client_profile': client_profile}
            )
        if include_dining:
            scout_tasks['restaurants'] = (
                call_restaurant_scout,
                (location, duration, cuisines, budget, distance),
                {'per_day': restaurants_per_day,
                 'accommodation': accommodation,
                 'pre_planned':   pre_planned,
                 'client_profile': client_profile}
            )
        if include_attractions:
            scout_tasks['attractions'] = (
                call_attraction_scout,
                (location, duration, attractions, budget, distance),
                {'per_day': attractions_per_day,
                 'accommodation': accommodation,
                 'pre_planned':   pre_planned,
                 'client_profile': client_profile}
            )
        logger.info("Active scout tasks: %s", list(scout_tasks.keys()))

        # ── Geocode accommodation for distance calculations ─────────────────
        # If the consultant supplied a hotel / address, look it up once via the
        # Places API to get coordinates.  _run_single_scout then computes a
        # straight-line (haversine) distance from those coordinates to each
        # verified item and writes it into the travel_time field.  This is
        # intentionally an approximation — it does not require the Distance
        # Matrix API and uses no additional quota beyond the Places key already
        # in use for venue verification.
        accommodation_coords = (None, None)
        if accommodation and PLACES_VERIFY_ENABLED:
            accommodation_coords = _geocode_accommodation(accommodation)
            if accommodation_coords[0] is not None:
                logger.info(
                    "Accommodation geocoded for distance calc: (%.5f, %.5f)",
                    *accommodation_coords
                )
            else:
                logger.info("Accommodation geocoding returned no result — travel_time from Claude")

        # ── Initial parallel scout run ─────────────────────────────────────
        # Each future calls _run_single_scout, which runs the Claude scout and
        # then immediately runs Google Places verification on the results.
        # Combining both steps means Places wipeouts (all places closed) look
        # identical to parse failures — both yield [] and trigger a retry.
        results = {'photos': [], 'restaurants': [], 'attractions': []}
        errors  = []

        if PLACES_VERIFY_ENABLED:
            logger.info("Google Places verification enabled — running scout + verify in one step")
        else:
            logger.info("Google Places verification disabled (no API key)")

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(
                    _with_app_context, _run_single_scout,
                    name, fn, args, kwargs, location, accommodation_coords
                ): name
                for name, (fn, args, kwargs) in scout_tasks.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                    logger.info("Scout '%s': initial run returned %d item(s)", name, len(results[name]))
                except Exception as exc:
                    logger.error("Scout '%s': initial run failed — %s", name, exc)
                    errors.append(f"{name}: {exc}")
                    results[name] = []

        # ── Retry any scout that returned 0 items ─────────────────────────
        # Empty results are never cached, so each retry always hits the API
        # fresh. Retries run sequentially (not in parallel) to avoid hammering
        # the API when one scout is having transient issues.
        for attempt in range(1, SCOUT_MAX_RETRIES + 1):
            empty_scouts = [name for name in scout_tasks if not results[name]]
            if not empty_scouts:
                break  # All enabled scouts have results — nothing to retry

            logger.warning(
                "Retry attempt %d/%d — scouts with 0 results: %s",
                attempt, SCOUT_MAX_RETRIES, empty_scouts,
            )
            time.sleep(SCOUT_RETRY_DELAY)

            for name in empty_scouts:
                fn, args, kwargs = scout_tasks[name]
                try:
                    items = _run_single_scout(name, fn, args, kwargs, location, accommodation_coords)
                    results[name] = items
                    logger.info(
                        "Scout '%s': retry %d returned %d item(s)",
                        name, attempt, len(items),
                    )
                except Exception as exc:
                    logger.error("Scout '%s': retry %d failed — %s", name, attempt, exc)
                    # results[name] stays [] — will appear in warnings below

        # ── Build per-scout warnings for categories still empty after retries ──
        warnings = []
        for name in scout_tasks:
            if not results[name]:
                label = {
                    'photos':      'Photography',
                    'restaurants': 'Dining',
                    'attractions': 'Attractions',
                }[name]
                warnings.append(
                    f"{label} recommendations could not be generated for this destination "
                    f"after {SCOUT_MAX_RETRIES + 1} attempt(s). "
                    f"You can proceed without this section or try again."
                )
                logger.warning(
                    "Scout '%s': 0 results after %d attempt(s) — surfacing warning to user",
                    name, SCOUT_MAX_RETRIES + 1,
                )

        # ── Hard fail only if every enabled scout is still empty ──────────
        enabled_scouts = list(scout_tasks.keys())
        if not any(results[n] for n in enabled_scouts):
            return jsonify({
                "error":    "No recommendations could be generated. Please try again.",
                "warnings": warnings,
            }), 500

        colors = get_color_palette(location)

        # ── Store raw results in session for /finalize ──
        # HTML generation (including slow map image fetches) is deferred until
        # the user has reviewed and approved their selections.
        _evict_sessions()
        session_id = str(uuid.uuid4())
        _session_store[session_id] = {
            'ts':          time.time(),
            'location':    location,
            'duration':    duration,
            'colors':      colors,
            'photos':      results['photos'],
            'restaurants': results['restaurants'],
            'attractions': results['attractions'],
        }
        logger.info(
            "Session %s created — %d photos, %d restaurants, %d attractions",
            session_id[:8], len(results['photos']), len(results['restaurants']), len(results['attractions'])
        )

        # ── Persist draft trip to database ──────────────────────────────────
        # client_id is optional — the frontend passes it when a client is selected.
        # raw_client_id was already parsed above for profile loading; reuse it.
        trip_client_id = None
        if raw_client_id is not None:
            try:
                trip_client_id = int(raw_client_id)
            except (ValueError, TypeError):
                pass

        try:
            trip = Trip(
                client_id            = trip_client_id,
                created_by_id        = g.current_user.id,
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
                attraction_cats      = attractions or None,
                accommodation        = accommodation,
                raw_photos           = json.dumps(results['photos']),
                raw_restaurants      = json.dumps(results['restaurants']),
                raw_attractions      = json.dumps(results['attractions']),
                colors               = json.dumps(colors),
                session_id           = session_id,
            )
            db.session.add(trip)
            db.session.commit()
            trip_id = trip.id
            logger.info("Trip draft saved: id=%d session=%s", trip_id, session_id[:8])
        except Exception as db_exc:
            # DB failure is non-fatal — the review flow still works via session store
            logger.error("Failed to save trip draft to DB: %s", db_exc)
            trip_id = None

        return jsonify({
            "status":           "success",
            "session_id":       session_id,
            "trip_id":          trip_id,       # DB record id (None if DB failed)
            "location":         location,
            "duration":         duration,
            "colors":           colors,
            "photos":           results['photos'],
            "restaurants":      results['restaurants'],
            "attractions":      results['attractions'],
            "photo_count":      len(results['photos']),
            "restaurant_count": len(results['restaurants']),
            "attraction_count": len(results['attractions']),
            "warnings":         warnings,      # [] on full success; non-empty if any scout failed
            "model":            SCOUT_MODEL_LABEL
        })

    except Exception as e:
        logger.error("Unhandled error in /generate: %s", e, exc_info=True)
        # Return a generic message — never expose internal exception text to clients
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500


@app.route('/finalize', methods=['POST'])
@require_auth
def finalize_guide():
    """
    Take a session_id and arrays of approved item indices.
    Filter the stored raw items to approved only, run map pre-fetch,
    generate the final HTML guide, and return it. Requires login.

    Session resolution order:
      1. In-memory _session_store (fast path — same process/worker)
      2. Trip DB record matched by session_id (multi-worker safe fallback)
    This means /finalize works correctly even when running under Gunicorn
    with multiple workers, where the request may land on a different worker
    than the one that handled /generate.
    """
    try:
        _evict_sessions()

        data = request.get_json(force=True)
        session_id = str(data.get('session_id', '')).strip()

        # ── Resolve session: in-memory first, DB fallback ──────────────────
        if session_id and session_id in _session_store:
            session     = _session_store[session_id]
            location    = session['location']
            duration    = session['duration']
            colors      = session['colors']
            all_photos  = session['photos']
            all_rests   = session['restaurants']
            all_attrs   = session['attractions']
            logger.info("Finalize: session %s resolved from memory store", session_id[:8])
        elif session_id:
            # Memory miss — try to reconstruct from the saved Trip record.
            db_trip = Trip.query.filter_by(session_id=session_id, is_deleted=False).first()
            if not db_trip:
                return jsonify({
                    "error": "Session expired — please click Start Over to generate a new guide."
                }), 404
            location    = db_trip.location
            duration    = db_trip.duration
            colors      = json.loads(db_trip.colors)    if db_trip.colors           else {}
            all_photos  = json.loads(db_trip.raw_photos)      if db_trip.raw_photos      else []
            all_rests   = json.loads(db_trip.raw_restaurants) if db_trip.raw_restaurants else []
            all_attrs   = json.loads(db_trip.raw_attractions) if db_trip.raw_attractions else []
            logger.info("Finalize: session %s resolved from DB (trip id=%d)", session_id[:8], db_trip.id)
        else:
            return jsonify({
                "error": "Session expired — please click Start Over to generate a new guide."
            }), 404

        # Parse approved index arrays — default to all if not provided
        def _parse_indices(key, total):
            raw = data.get(key)
            if raw is None:
                return list(range(total))
            return [int(i) for i in raw if 0 <= int(i) < total]

        approved_photo_idx = _parse_indices('approved_photos',      len(all_photos))
        approved_rest_idx  = _parse_indices('approved_restaurants',  len(all_rests))
        approved_attr_idx  = _parse_indices('approved_attractions',  len(all_attrs))

        photos      = [all_photos[i] for i in approved_photo_idx]
        restaurants = [all_rests[i]  for i in approved_rest_idx]
        attractions = [all_attrs[i]  for i in approved_attr_idx]

        logger.info(
            "Finalizing session %s — approved: %d photos, %d restaurants, %d attractions",
            session_id[:8], len(photos), len(restaurants), len(attractions)
        )

        # Pre-fetch map images for approved items only
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
                logger.info("Pre-fetching %d day map image(s) for final guide...", len(sections_by_day))
                prefetched_maps = prefetch_day_maps(sections_by_day)

        logger.info("Generating final HTML...")
        html_content = generate_master_html(
            location, duration, photos, restaurants, attractions,
            colors, prefetched_maps=prefetched_maps
        )

        # ── Update trip record to 'finalized' ────────────────────────────────
        trip_id = data.get('trip_id')
        if trip_id is not None:
            try:
                saved_trip = db.session.get(Trip, int(trip_id))
                if saved_trip and not saved_trip.is_deleted:
                    saved_trip.status                      = 'finalized'
                    saved_trip.approved_photo_indices      = json.dumps(approved_photo_idx)
                    saved_trip.approved_restaurant_indices = json.dumps(approved_rest_idx)
                    saved_trip.approved_attraction_indices = json.dumps(approved_attr_idx)
                    saved_trip.final_html                  = html_content
                    saved_trip.updated_at                  = datetime.now(timezone.utc)
                    db.session.commit()
                    logger.info("Trip finalized in DB: id=%d", saved_trip.id)
            except Exception as db_exc:
                logger.error("Failed to finalize trip in DB: %s", db_exc)

        return jsonify({
            "status":           "success",
            "html":             html_content,
            "location":         location,
            "duration":         duration,
            "photo_count":      len(photos),
            "restaurant_count": len(restaurants),
            "attraction_count": len(attractions),
            "model":            SCOUT_MODEL_LABEL
        })

    except Exception as e:
        logger.error("Unhandled error in /finalize: %s", e, exc_info=True)
        # Return a generic message — never expose internal exception text to clients
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500


@app.route('/replace', methods=['POST'])
@require_auth
def replace_item():
    """
    Replace a single item in the review screen with an alternative.

    The endpoint reconstructs the original scout parameters from the saved Trip
    record, runs a focused single-item scout that explicitly excludes all current
    items by name, and returns the single best alternative.

    Request body:
        session_id    — str, the active review session
        trip_id       — int|null, the DB trip record
        type          — 'photos' | 'restaurants' | 'attractions'
        index         — int, position of the item to replace (used for DB update)
        day           — int, day number the item belongs to
        meal_type     — str|null, 'breakfast'|'lunch'|'dinner' (restaurants only)
        exclude_names — list[str], names of all current items to avoid

    Returns:
        { "item": { ...scout item dict... } }
    """
    try:
        data       = request.get_json(force=True) or {}
        session_id = str(data.get('session_id', '')).strip()
        trip_id    = data.get('trip_id')
        item_type  = str(data.get('type', '')).strip()
        item_idx   = int(data.get('index', 0))
        day        = int(data.get('day', 1))
        meal_type  = data.get('meal_type') or None
        raw_excludes  = (data.get('exclude_names') or [])
        exclude_names = [
            s for s in (
                _sanitise_line(n, MAX_EXCLUDE_NAME_LEN) for n in raw_excludes if n
            ) if s
        ][:MAX_EXCLUDE_LIST_LEN]

        if item_type not in ('photos', 'restaurants', 'attractions'):
            return jsonify({'error': 'Invalid type'}), 400

        # ── Resolve scout parameters from DB trip ──────────────────────────
        # We need location, budget, distance, interests/cuisines/categories
        # to run a coherent replacement scout.
        location = budget = distance = None
        interests = cuisines_str = categories = ''
        duration  = 1

        db_trip = None
        if trip_id is not None:
            try:
                db_trip = db.session.get(Trip, int(trip_id))
            except Exception:
                pass
        if db_trip is None and session_id:
            db_trip = Trip.query.filter_by(session_id=session_id, is_deleted=False).first()

        if db_trip:
            location   = db_trip.location
            duration   = db_trip.duration
            budget     = db_trip.budget     or 'Moderate'
            distance   = db_trip.distance   or 'Up to 30 minutes'
            interests  = db_trip.photo_interests  or ''
            cuisines_str = db_trip.cuisines or ''
            categories = db_trip.attraction_cats  or ''
        elif session_id and session_id in _session_store:
            sess     = _session_store[session_id]
            location = sess['location']
            duration = sess['duration']
            budget   = 'Moderate'
            distance = 'Up to 30 minutes'
        else:
            return jsonify({'error': 'Session not found — please start over.'}), 404

        if not location:
            return jsonify({'error': 'Could not resolve trip location.'}), 400

        # ── Load client profile if trip has one ────────────────────────────
        client_profile = None
        if db_trip and db_trip.client_id:
            try:
                from models import Client as _Client
                db_client = db.session.get(_Client, db_trip.client_id)
                if db_client and not db_client.is_deleted:
                    client_profile = {k: v for k, v in {
                        'home_city':            db_client.home_city            or '',
                        'preferred_budget':     db_client.preferred_budget     or '',
                        'travel_style':         db_client.travel_style         or '',
                        'dietary_requirements': db_client.dietary_requirements or '',
                        'notes':                db_client.notes                or '',
                    }.items() if v}
            except Exception as cp_exc:
                logger.warning("Replace: could not load client profile: %s", cp_exc)

        # ── Build an exclusion-aware prompt for a single replacement item ──
        exclude_block = (
            "IMPORTANT — Do NOT suggest any of the following (already in the guide):\n"
            + "\n".join(f"  - {n}" for n in exclude_names)
            + "\n"
        ) if exclude_names else ""

        day_context = f"Day {day} of a {duration}-day trip."

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
            meal_hint = f"This should be a {meal_type} option." if meal_type else ""
            diet_hint = ""
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

        logger.info(
            "Replace: type=%s idx=%d day=%d location=%s excluded=%d",
            item_type, item_idx, day, location, len(exclude_names)
        )

        message = anthropic_client.messages.create(
            model=SCOUT_MODEL,
            max_tokens=1200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        # Extract text from the first text-type content block
        raw_text = ''
        for block in message.content:
            block_text = getattr(block, 'text', None)
            if block_text is not None:
                raw_text = str(block_text).strip()
                if raw_text:
                    break

        # Strip markdown code fences if the model wrapped the output (e.g. ```json ... ```)
        if raw_text.startswith('```'):
            # split('```', 2) → ['', '<lang>\n<content>\n', ''] — take middle part
            parts = raw_text.split('```', 2)
            inner = parts[1] if len(parts) >= 2 else raw_text
            if inner.startswith('json'):
                inner = inner[4:]
            raw_text = inner.strip()

        # Parse the single returned JSON object
        items = _parse_json_lines(raw_text, "Replace Scout")
        if not items:
            # Try bare json.loads for pretty-printed (multi-line) objects
            try:
                items = [json.loads(raw_text)]
            except Exception:
                pass

        if not items:
            logger.warning("Replace Scout: failed to parse response for %s idx=%d", item_type, item_idx)
            return jsonify({'error': 'Could not find an alternative. Try again or toggle this item off.'}), 422

        new_item = items[0]

        # ── Run Google Places verification on the replacement ──────────────
        if PLACES_VERIFY_ENABLED:
            verified, _ = verify_places_batch([new_item], 'name', 'address', location)
            if verified:
                new_item = verified[0]
            # If Places returns nothing the item is still usable — just unverified

        # ── Apply haversine distance from accommodation if available ────────
        if db_trip and db_trip.accommodation and PLACES_VERIFY_ENABLED:
            acc_lat, acc_lng = _geocode_accommodation(db_trip.accommodation)
            if acc_lat is not None:
                _apply_distances([new_item], acc_lat, acc_lng)

        # ── Update the raw item in the DB trip record ──────────────────────
        if db_trip:
            try:
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
                    db.session.commit()
                    logger.info("Replace: DB trip %d updated — %s[%d] replaced", db_trip.id, item_type, item_idx)
            except Exception as db_exc:
                logger.error("Replace: DB update failed: %s", db_exc)

        # ── Update the in-memory session store if still alive ──────────────
        if session_id and session_id in _session_store:
            sess_arr = _session_store[session_id].get(item_type, [])
            if item_idx < len(sess_arr):
                sess_arr[item_idx] = new_item

        return jsonify({'item': new_item})

    except Exception as e:
        logger.error("Unhandled error in /replace: %s", e, exc_info=True)
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


if __name__ == '__main__':
    print("Trip Master API - Local Development")
    print("=" * 50)
    print(f"Model: {SCOUT_MODEL_LABEL} ({SCOUT_MODEL})")
    print("Backend running on: http://localhost:5001")
    print("Open frontend: http://localhost:5000")
    print("=" * 50)
    app.run(debug=False, port=5001, host='0.0.0.0')
