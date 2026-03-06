"""
tests/conftest.py — Shared pytest fixtures for the Trip Guide test suite.

Database strategy
-----------------
StaticPool (shared in-memory SQLite) → all sessions share one underlying
connection so data committed by one session is immediately visible to any
other session.  A *session-scoped* fixture creates tables once at test-session
start and drops them on exit.  Each test gets a fresh DB session; in-flight
changes are rolled back on teardown (committed data from test_user persists for
the session, but unique emails prevent collisions).

Auth strategy
-------------
test_user  — StaffUser created with bcrypt rounds=4 (fast for CI) and a
             random hex suffix in the email so concurrent tests don't clash.
auth_client — AsyncClient preconfigured with the JWT cookie + X-Requested-With
              header that all protected POST/PUT/DELETE routes require.
              app.dependency_overrides[get_db] routes all routes to the test DB.
              app._http_client is stubbed so the lifespan context manager is not
              required (ASGITransport does not run the FastAPI lifespan).
"""
import time
import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app as app_module
from app import app
from auth import COOKIE_NAME, _encode_token
from database import get_db
from models import StaffUser
from models import db as Base

# ---------------------------------------------------------------------------
# In-memory test database (StaticPool keeps a single shared connection)
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# ---------------------------------------------------------------------------
# Table lifecycle (once per pytest session)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def init_test_db():
    """Create all ORM tables once before any test; drop them on exit."""
    Base.metadata.create_all(test_engine)
    yield
    Base.metadata.drop_all(test_engine)


# ---------------------------------------------------------------------------
# Per-test DB session
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(init_test_db):
    """Yield a fresh SQLAlchemy session; roll back any uncommitted work on teardown."""
    session = TestingSessionLocal()
    yield session
    session.rollback()
    session.close()


# ---------------------------------------------------------------------------
# FastAPI get_db override
# ---------------------------------------------------------------------------


def override_get_db():
    """Replace the production DB session with the test DB session."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Test user (function-scoped — unique email prevents collisions)
# ---------------------------------------------------------------------------

TEST_PASSWORD = "testpass123"


@pytest.fixture
def test_user(db_session):
    """Create a StaffUser in the test DB and yield it.  Unique email per test."""
    unique_tag = uuid.uuid4().hex[:8]
    pw_hash = bcrypt.hashpw(
        TEST_PASSWORD.encode("utf-8"),
        bcrypt.gensalt(rounds=4),   # rounds=4 is fast in CI; production uses 12
    ).decode("utf-8")

    user = StaffUser(
        email=f"test-{unique_tag}@example.com",
        full_name="Test User",
        password_hash=pw_hash,
        role="staff",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    yield user


# ---------------------------------------------------------------------------
# Unauthenticated async client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def anon_client():
    """AsyncClient with no auth cookie — for testing 401 / public endpoints."""
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Authenticated async client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def auth_client(test_user):
    """AsyncClient authenticated with a valid JWT cookie and CSRF header.

    Also stubs app._http_client (normally initialised in the lifespan context
    manager which ASGITransport does not invoke) to prevent AttributeError in
    code paths that reference the client even when PLACES_VERIFY_ENABLED=False.
    """
    app.dependency_overrides[get_db] = override_get_db
    # Stub the shared HTTP client (lifespan is not triggered by ASGITransport)
    app_module._http_client = httpx.AsyncClient()

    token = _encode_token(test_user.id)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={COOKIE_NAME: token},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as client:
        yield client

    await app_module._http_client.aclose()
    app_module._http_client = None
    app.dependency_overrides.clear()
