"""
tests/test_generate.py — /generate endpoint and /jobs/{job_id} polling.

The background scout task that runs after /generate will fail in the test
environment (no Claude API key) and mark the job as 'failed'.  This is
expected and does not affect these tests, which only verify the *immediate*
synchronous response from /generate and the shape of the /jobs poll response.

Covers:
  • POST /generate  (valid body, auth)        → 200, {job_id: str}
  • POST /generate  (no cookie)               → 401
  • POST /generate  (reversed dates)          → 422
  • POST /generate  (duration > 14 days)      → 422
  • POST /generate  (missing location)        → 422
  • POST /generate  (missing duration+dates)  → 422
  • GET  /jobs/{job_id}  (just-created job)   → 200, status in known set
  • GET  /jobs/nonexistent-uuid               → 404
"""
import asyncio

import pytest


VALID_BODY = {
    "location": "Barcelona",
    "duration": 3,
}

VALID_STATUS_SET = {"pending", "running", "done", "failed"}


# ---------------------------------------------------------------------------
# Successful generate → immediate job_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_job_id(auth_client):
    response = await auth_client.post("/generate", json=VALID_BODY)
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert isinstance(data["job_id"], str)
    assert len(data["job_id"]) > 0


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_requires_auth(anon_client):
    # CSRF guard (403) fires before auth cookie check (401) for POST without
    # X-Requested-With header.  Either code means "not allowed".
    response = await anon_client.post("/generate", json=VALID_BODY)
    assert response.status_code in {401, 403}


# ---------------------------------------------------------------------------
# Validation: reversed dates → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_reversed_dates(auth_client):
    response = await auth_client.post(
        "/generate",
        json={
            "location": "Paris",
            "start_date": "2025-06-10",
            "end_date": "2025-06-05",   # end before start
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Validation: duration > 14 days → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_duration_exceeds_max(auth_client):
    response = await auth_client.post(
        "/generate",
        json={"location": "Tokyo", "duration": 15},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Validation: dates span > 14 days → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_dates_span_too_long(auth_client):
    response = await auth_client.post(
        "/generate",
        json={
            "location": "Tokyo",
            "start_date": "2025-06-01",
            "end_date": "2025-06-20",   # 20 days
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Validation: missing location → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_missing_location(auth_client):
    response = await auth_client.post(
        "/generate",
        json={"duration": 3},   # no location
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Validation: neither duration nor dates → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_no_duration_or_dates(auth_client):
    response = await auth_client.post(
        "/generate",
        json={"location": "Madrid"},   # no duration, no dates
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Job polling — just-created job should be known
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_job_known_status(auth_client):
    # Generate a job first
    gen_response = await auth_client.post("/generate", json=VALID_BODY)
    assert gen_response.status_code == 200
    job_id = gen_response.json()["job_id"]

    # Poll immediately — job should exist with a known status
    poll_response = await auth_client.get(f"/jobs/{job_id}")
    assert poll_response.status_code == 200
    data = poll_response.json()
    assert "status" in data
    assert data["status"] in VALID_STATUS_SET
    assert "progress" in data
    assert "message" in data


# ---------------------------------------------------------------------------
# Job polling — nonexistent job → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_nonexistent_job(auth_client):
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await auth_client.get(f"/jobs/{fake_id}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Job polling — requires auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_job_requires_auth(auth_client, anon_client):
    gen_response = await auth_client.post("/generate", json=VALID_BODY)
    job_id = gen_response.json()["job_id"]

    response = await anon_client.get(f"/jobs/{job_id}")
    assert response.status_code == 401
