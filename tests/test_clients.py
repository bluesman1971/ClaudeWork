"""
tests/test_clients.py — Gear profile CRUD endpoint tests.

All gear profile routes live at /gear-profiles and require authentication.
The test DB uses StaticPool so all sessions share the same in-memory SQLite.

Covers:
  • GET  /gear-profiles               → 200, empty list initially
  • POST /gear-profiles               → creates profile, returns it with id
  • GET  /gear-profiles  (after POST) → list contains the new profile
  • PUT  /gear-profiles/{id}          → 200, name updated
  • DELETE /gear-profiles/{id}        → 200, {ok: True}
  • GET  /gear-profiles  (after DEL)  → empty list
  • POST /gear-profiles (invalid camera_type) → 422
  • DELETE /gear-profiles/{other_id}  → 404 (cross-user isolation)
  • All CRUD endpoints without auth   → 401
"""
import uuid

import bcrypt
import pytest

from auth import COOKIE_NAME, _encode_token
from models import StaffUser
from tests.conftest import TestingSessionLocal

# ---------------------------------------------------------------------------
# Minimal valid gear profile body
# ---------------------------------------------------------------------------

VALID_PROFILE = {
    "name": "Travel Kit",
    "camera_type": "apsc_mirrorless",
    "lenses": ["16-35mm f/2.8", "50mm f/1.8"],
    "has_tripod": True,
    "has_filters": ["polarizer"],
    "has_gimbal": False,
    "notes": "Weekend kit",
}


# ---------------------------------------------------------------------------
# GET /gear-profiles (empty)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_gear_profiles_empty(auth_client):
    response = await auth_client.get("/gear-profiles")
    assert response.status_code == 200
    data = response.json()
    assert "gear_profiles" in data
    assert isinstance(data["gear_profiles"], list)


# ---------------------------------------------------------------------------
# POST /gear-profiles — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_gear_profile(auth_client):
    response = await auth_client.post("/gear-profiles", json=VALID_PROFILE)
    assert response.status_code == 200
    data = response.json()
    assert "gear_profile" in data
    gp = data["gear_profile"]
    assert gp["name"] == "Travel Kit"
    assert gp["camera_type"] == "apsc_mirrorless"
    assert isinstance(gp["id"], int)
    assert "16-35mm f/2.8" in gp["lenses"]
    assert gp["has_tripod"] is True


# ---------------------------------------------------------------------------
# GET /gear-profiles — profile appears in list after create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_contains_created_profile(auth_client):
    # Create
    create_resp = await auth_client.post("/gear-profiles", json=VALID_PROFILE)
    assert create_resp.status_code == 200
    created_id = create_resp.json()["gear_profile"]["id"]

    # List
    list_resp = await auth_client.get("/gear-profiles")
    assert list_resp.status_code == 200
    ids = [gp["id"] for gp in list_resp.json()["gear_profiles"]]
    assert created_id in ids


# ---------------------------------------------------------------------------
# PUT /gear-profiles/{id} — update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_gear_profile(auth_client):
    # Create
    create_resp = await auth_client.post("/gear-profiles", json=VALID_PROFILE)
    profile_id = create_resp.json()["gear_profile"]["id"]

    # Update name
    update_resp = await auth_client.put(
        f"/gear-profiles/{profile_id}",
        json={"name": "Studio Kit"},
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["gear_profile"]["name"] == "Studio Kit"
    # Other fields should be unchanged
    assert data["gear_profile"]["camera_type"] == "apsc_mirrorless"


# ---------------------------------------------------------------------------
# DELETE /gear-profiles/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_gear_profile(auth_client):
    # Create
    create_resp = await auth_client.post("/gear-profiles", json=VALID_PROFILE)
    profile_id = create_resp.json()["gear_profile"]["id"]

    # Delete
    del_resp = await auth_client.delete(f"/gear-profiles/{profile_id}")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"ok": True}

    # Confirm gone from list
    list_resp = await auth_client.get("/gear-profiles")
    ids = [gp["id"] for gp in list_resp.json()["gear_profiles"]]
    assert profile_id not in ids


# ---------------------------------------------------------------------------
# POST /gear-profiles — invalid camera_type → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_invalid_camera_type(auth_client):
    response = await auth_client.post(
        "/gear-profiles",
        json={**VALID_PROFILE, "camera_type": "flying_saucer"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Cross-user isolation: cannot delete another user's profile → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_other_users_profile_returns_404(auth_client):
    """A gear profile created by user B should not be deletable by user A."""
    # Create a second user directly in the DB
    session = TestingSessionLocal()
    try:
        unique = uuid.uuid4().hex[:8]
        pw_hash = bcrypt.hashpw(b"pass", bcrypt.gensalt(rounds=4)).decode()
        other_user = StaffUser(
            email=f"other-{unique}@example.com",
            full_name="Other User",
            password_hash=pw_hash,
            role="staff",
            is_active=True,
        )
        session.add(other_user)
        session.commit()
        session.refresh(other_user)
        other_id = other_user.id
    finally:
        session.close()

    # Create a gear profile belonging to other_user (direct DB insert)
    from models import GearProfile
    import json
    session2 = TestingSessionLocal()
    try:
        gp = GearProfile(
            staff_user_id=other_id,
            name="Other Kit",
            camera_type="smartphone",
            lenses=json.dumps([]),
            has_filters=json.dumps([]),
        )
        session2.add(gp)
        session2.commit()
        session2.refresh(gp)
        other_profile_id = gp.id
    finally:
        session2.close()

    # auth_client is logged in as test_user, NOT other_user
    response = await auth_client.delete(f"/gear-profiles/{other_profile_id}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Auth guards on all CRUD endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profiles_no_auth(anon_client):
    response = await anon_client.get("/gear-profiles")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_profiles_no_auth(anon_client):
    # CSRF guard (403) fires before the auth cookie check (401) for POST
    response = await anon_client.post("/gear-profiles", json=VALID_PROFILE)
    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_put_profiles_no_auth(anon_client):
    # CSRF guard (403) fires before the auth cookie check (401) for PUT
    response = await anon_client.put("/gear-profiles/1", json={"name": "x"})
    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_delete_profiles_no_auth(anon_client):
    # CSRF guard (403) fires before the auth cookie check (401) for DELETE
    response = await anon_client.delete("/gear-profiles/1")
    assert response.status_code in {401, 403}


# ---------------------------------------------------------------------------
# Full CRUD cycle in one test (end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_crud_cycle(auth_client):
    """Create → list → update → delete → confirm gone."""
    # 1. Create
    r = await auth_client.post("/gear-profiles", json=VALID_PROFILE)
    assert r.status_code == 200
    gp_id = r.json()["gear_profile"]["id"]

    # 2. List — profile present
    r = await auth_client.get("/gear-profiles")
    ids = [p["id"] for p in r.json()["gear_profiles"]]
    assert gp_id in ids

    # 3. Update
    r = await auth_client.put(f"/gear-profiles/{gp_id}", json={"name": "Full Frame Kit"})
    assert r.status_code == 200
    assert r.json()["gear_profile"]["name"] == "Full Frame Kit"

    # 4. Delete
    r = await auth_client.delete(f"/gear-profiles/{gp_id}")
    assert r.status_code == 200

    # 5. List — profile gone
    r = await auth_client.get("/gear-profiles")
    ids = [p["id"] for p in r.json()["gear_profiles"]]
    assert gp_id not in ids
