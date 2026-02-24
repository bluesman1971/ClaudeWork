"""
database.py — SQLAlchemy engine and session management for Trip Master.

Provides:
  engine       — the shared SQLAlchemy engine
  SessionLocal — sessionmaker bound to the engine
  get_db()     — FastAPI dependency that yields a scoped session per request

Usage in routes:
    from database import get_db
    from sqlalchemy.orm import Session
    from fastapi import Depends

    @router.get('/example')
    async def example(db_session: Session = Depends(get_db)):
        ...

All SQLAlchemy calls remain synchronous. Use starlette.concurrency.run_in_threadpool
to call blocking DB operations from async route handlers without blocking the
event loop.
"""

import os
import logging
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

# ── Database URL ──────────────────────────────────────────────────────────────
# Mirrors the _safe_db_url logic from the old app.py to handle special chars
# in Railway PostgreSQL passwords.
_raw_db_url = os.getenv('DATABASE_URL', 'sqlite:///trip_master.db')


def _safe_db_url(url: str) -> str:
    """
    Ensure PostgreSQL URLs use the psycopg2 dialect prefix and that
    special characters in passwords are percent-encoded.
    Railway sometimes injects postgres:// instead of postgresql://.
    """
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    return url


_db_url = _safe_db_url(_raw_db_url)

# ── Engine ────────────────────────────────────────────────────────────────────
_connect_args: dict = {}
if _db_url.startswith('sqlite'):
    _connect_args = {'timeout': 15, 'check_same_thread': False}

engine = create_engine(
    _db_url,
    connect_args=_connect_args,
    pool_pre_ping=True,   # detect stale connections — important for Railway restarts
)

# ── SQLite WAL mode ───────────────────────────────────────────────────────────
# WAL allows concurrent readers + one writer simultaneously.
# Registered as a connection event so every pooled connection gets it.
# This is a no-op for PostgreSQL.
if _db_url.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def _set_sqlite_wal(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.close()

    # Prime WAL on the first connection at startup
    try:
        with engine.connect() as conn:
            conn.execute(text('PRAGMA journal_mode=WAL'))
        logger.info("SQLite WAL mode enabled")
    except Exception as exc:
        logger.warning("Could not prime SQLite WAL mode: %s", exc)

# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,   # prevents lazy-load errors after commit in async context
)


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """
    Yield a SQLAlchemy session for the duration of a request, then close it.

    Usage:
        from fastapi import Depends
        from database import get_db

        async def my_route(db_session: Session = Depends(get_db)):
            ...
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
