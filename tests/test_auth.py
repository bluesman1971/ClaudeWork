"""
tests/test_auth.py — Authentication flow tests.

Covers:
  • GET  /health                      → 200, no auth required
  • GET  /gear-profiles  (no cookie)  → 401
  • POST /auth/login  (valid creds)   → 200, sets tm_token cookie
  • POST /auth/login  (bad password)  → 401
  • POST /auth/login  (wrong email)   → 401
  • POST /auth/login  (empty body)    → 422
"""
import pytest
from auth import COOKIE_NAME
from tests.conftest import TEST_PASSWORD


# ---------------------------------------------------------------------------
# Health endpoint (public — no auth required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_ok(anon_client):
    response = await anon_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "message" in data


# ---------------------------------------------------------------------------
# Protected endpoint — no cookie → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gear_profiles_requires_auth(anon_client):
    """GET /gear-profiles without a cookie must return 401."""
    response = await anon_client.get("/gear-profiles")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Login — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success(anon_client, test_user):
    """POST /auth/login with correct credentials returns 200 and sets the cookie."""
    response = await anon_client.post(
        "/auth/login",
        json={"email": test_user.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "user" in data
    assert data["user"]["email"] == test_user.email
    # The JWT cookie must be present
    assert COOKIE_NAME in response.cookies


# ---------------------------------------------------------------------------
# Login — wrong password → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_wrong_password(anon_client, test_user):
    response = await anon_client.post(
        "/auth/login",
        json={"email": test_user.email, "password": "completely-wrong"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Login — non-existent email → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_nonexistent_email(anon_client):
    response = await anon_client.post(
        "/auth/login",
        json={"email": "nobody@nowhere.example", "password": "irrelevant"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Login — empty body → 422 (Pydantic validation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_empty_body(anon_client):
    response = await anon_client.post("/auth/login", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Authenticated endpoint — auth_client can reach /gear-profiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticated_can_list_gear_profiles(auth_client):
    """An authenticated client must receive 200 from GET /gear-profiles."""
    response = await auth_client.get("/gear-profiles")
    assert response.status_code == 200
    data = response.json()
    assert "gear_profiles" in data
    assert isinstance(data["gear_profiles"], list)


# ---------------------------------------------------------------------------
# CSRF guard on a protected mutating endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_guard_on_protected_post(anon_client, test_user):
    """POST /gear-profiles without X-Requested-With header returns 403.

    The CSRF guard in get_current_user fires before auth, so the client needs
    a valid cookie but NO X-Requested-With header to trigger the 403.
    """
    from auth import _encode_token, COOKIE_NAME
    token = _encode_token(test_user.id)
    # Override just the cookie; deliberately omit the CSRF header
    response = await anon_client.post(
        "/gear-profiles",
        json={
            "name": "Test Kit",
            "camera_type": "apsc_mirrorless",
        },
        cookies={COOKIE_NAME: token},
        # No X-Requested-With header
    )
    assert response.status_code == 403
