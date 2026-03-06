"""
tests/test_finalize.py — /finalize endpoint tests.

PLACES_VERIFY_ENABLED is False in the test environment (no Google API key),
so map prefetching is skipped and _http_client is never called.

Session injection strategy
--------------------------
The session store (app._session_store) is a plain dict used as the Redis
fallback.  We inject fake sessions directly before calling /finalize so no
background scout task or real API call is needed.

Covers:
  • POST /finalize  (no cookie)                      → 401
  • POST /finalize  (missing session_id field)        → 422
  • POST /finalize  (nonexistent session_id)          → 404
  • POST /finalize  (injected session, all photos)    → 200, html key present
  • POST /finalize  (injected session, subset photos) → 200, correct photo_count
"""
import time
import uuid

import pytest

import app as app_module


# ---------------------------------------------------------------------------
# Minimal fake photo items (all required fields from the PHOTO_TOOL schema)
# ---------------------------------------------------------------------------

_FAKE_PHOTOS = [
    {
        "day": 1,
        "name": "Park Güell",
        "address": "Carrer d'Olot, Barcelona",
        "lat": 41.4145,
        "lng": 2.1527,
        "shoot_window": "06:00–07:30 AM (golden hour)",
        "the_shot": "Mosaic terrace at dawn; warm side-light on the ceramic bench.",
        "the_setup": "Stand at the west end of the main terrace. 16-35mm at 24mm.",
        "the_settings": "ISO 100, f/8, 1/60s, Aperture Priority.",
        "the_reality_check": "Free zone closes at 08:00 — arrive early. No tripods on paid terraces.",
        "required_gear": ["tripod"],
        "distance_from_accommodation": "25 min metro",
    },
    {
        "day": 1,
        "name": "Barceloneta Beach",
        "address": "Barceloneta, Barcelona",
        "lat": 41.3795,
        "lng": 2.1896,
        "shoot_window": "07:00–08:00 PM (blue hour)",
        "the_shot": "Long-exposure surf wash on the sand, city lights behind.",
        "the_setup": "Low angle, 16-35mm at 16mm. Polarizer to cut glare.",
        "the_settings": "ISO 64, f/11, 2s, Manual.",
        "the_reality_check": "Crowds thin after 19:30 on weekdays.",
        "required_gear": ["tripod", "polarizer"],
        "distance_from_accommodation": "15 min walk",
    },
]


def _inject_session(session_id: str, photos=None) -> None:
    """Write a fake session into the in-memory session store."""
    app_module._session_store[session_id] = {
        "location": "Barcelona",
        "duration": 2,
        "colors": {
            "primary": "#c41e3a",
            "accent": "#f4a261",
            "secondary": "#2a9d8f",
            "neutral": "#f5e6d3",
        },
        "photos": photos if photos is not None else _FAKE_PHOTOS,
        "ts": time.time(),
    }


def _clear_session(session_id: str) -> None:
    app_module._session_store.pop(session_id, None)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_requires_auth(anon_client):
    # CSRF guard (403) fires before auth cookie check (401) for POST without
    # X-Requested-With header.  Either code means "not allowed".
    session_id = str(uuid.uuid4())
    response = await anon_client.post(
        "/finalize",
        json={"session_id": session_id},
    )
    assert response.status_code in {401, 403}


# ---------------------------------------------------------------------------
# Validation: missing session_id field → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_missing_session_id(auth_client):
    response = await auth_client.post(
        "/finalize",
        json={"approved_photos": [0]},   # no session_id
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Nonexistent session → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_nonexistent_session(auth_client):
    fake_session = str(uuid.uuid4())
    response = await auth_client.post(
        "/finalize",
        json={"session_id": fake_session},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Valid injected session — all photos approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_all_photos(auth_client):
    session_id = str(uuid.uuid4())
    _inject_session(session_id)
    try:
        response = await auth_client.post(
            "/finalize",
            json={
                "session_id": session_id,
                "approved_photos": [0, 1],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "html" in data, f"Expected 'html' key, got: {list(data.keys())}"
        assert isinstance(data["html"], str)
        assert len(data["html"]) > 100, "HTML response seems too short"
        assert data.get("photo_count") == 2
        assert data.get("location") == "Barcelona"
    finally:
        _clear_session(session_id)


# ---------------------------------------------------------------------------
# Valid injected session — subset of photos approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_subset_photos(auth_client):
    session_id = str(uuid.uuid4())
    _inject_session(session_id)
    try:
        response = await auth_client.post(
            "/finalize",
            json={
                "session_id": session_id,
                "approved_photos": [0],   # only first photo
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("photo_count") == 1
        assert "html" in data
    finally:
        _clear_session(session_id)


# ---------------------------------------------------------------------------
# Empty approved_photos list (all omitted → finalize uses all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_omitted_approved_photos(auth_client):
    """When approved_photos is not sent, all photos are approved by default."""
    session_id = str(uuid.uuid4())
    _inject_session(session_id)
    try:
        response = await auth_client.post(
            "/finalize",
            json={"session_id": session_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("photo_count") == len(_FAKE_PHOTOS)
    finally:
        _clear_session(session_id)


# ---------------------------------------------------------------------------
# HTML content sanity check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_html_is_valid_html(auth_client):
    session_id = str(uuid.uuid4())
    _inject_session(session_id)
    try:
        response = await auth_client.post(
            "/finalize",
            json={"session_id": session_id, "approved_photos": [0]},
        )
        assert response.status_code == 200
        html = response.json()["html"]
        # Should be a full HTML document
        assert "<!DOCTYPE html>" in html or "<html" in html
        assert "Barcelona" in html
    finally:
        _clear_session(session_id)
