import os
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from redis_client import get_redis

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import get_db
from models import StaffUser

logger = logging.getLogger(__name__)

COOKIE_NAME   = 'tm_token'
TOKEN_TTL_H   = 8
BCRYPT_ROUNDS = 12

LOGIN_MAX_ATTEMPTS   = 10
LOGIN_WINDOW_SECONDS = 300

_login_attempts: dict = defaultdict(list)
_login_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Login rate limiting (Redis sorted sets + in-memory fallback)
# ---------------------------------------------------------------------------

def _check_login_rate_limit(ip: str) -> bool:
    now = time.time()
    r = get_redis()
    if r is not None:
        try:
            key = f"ratelimit:login:{ip}"
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, '-inf', now - LOGIN_WINDOW_SECONDS)
            pipe.zcard(key)
            pipe.expire(key, LOGIN_WINDOW_SECONDS)
            _, count, _ = pipe.execute()
            return count < LOGIN_MAX_ATTEMPTS
        except Exception as exc:
            logger.warning("Redis login rate-limit check error: %s — falling back", exc)
    with _login_lock:
        _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_WINDOW_SECONDS]
        return len(_login_attempts[ip]) < LOGIN_MAX_ATTEMPTS


def _record_login_failure(ip: str):
    now = time.time()
    r = get_redis()
    if r is not None:
        try:
            key = f"ratelimit:login:{ip}"
            pipe = r.pipeline()
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, LOGIN_WINDOW_SECONDS)
            pipe.execute()
            return
        except Exception as exc:
            logger.warning("Redis login failure record error: %s — falling back", exc)
    with _login_lock:
        _login_attempts[ip].append(now)


# ---------------------------------------------------------------------------
# Per-user endpoint rate limiting (Redis sorted sets + in-memory fallback)
# ---------------------------------------------------------------------------

RATE_LIMIT_RULES: dict[str, tuple[int, int]] = {
    'generate': (20, 600),
    'replace':  (60, 600),
}

_user_requests: dict = defaultdict(list)
_user_rate_lock = threading.Lock()


def check_user_rate_limit(user_id: int, endpoint: str) -> tuple[bool, int]:
    rule = RATE_LIMIT_RULES.get(endpoint)
    if rule is None:
        return True, 0
    max_requests, window = rule
    now = time.time()
    r = get_redis()
    if r is not None:
        try:
            rkey = f"ratelimit:user:{user_id}:{endpoint}"
            pipe = r.pipeline()
            pipe.zremrangebyscore(rkey, '-inf', now - window)
            pipe.zrange(rkey, 0, -1, withscores=True)
            pipe.expire(rkey, window)
            _, entries, _ = pipe.execute()
            count = len(entries)
            if count >= max_requests:
                oldest_score = min(score for _, score in entries)
                retry_after = int(window - (now - oldest_score)) + 1
                return False, retry_after
            r.zadd(rkey, {str(now): now})
            r.expire(rkey, window)
            return True, 0
        except Exception as exc:
            logger.warning("Redis user rate-limit error: %s — falling back", exc)
    mem_key = (user_id, endpoint)
    with _user_rate_lock:
        _user_requests[mem_key] = [t for t in _user_requests[mem_key] if now - t < window]
        if len(_user_requests[mem_key]) >= max_requests:
            oldest = min(_user_requests[mem_key])
            retry_after = int(window - (now - oldest)) + 1
            return False, retry_after
        _user_requests[mem_key].append(now)
        return True, 0


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _secret() -> str:
    return os.getenv('JWT_SECRET_KEY', 'dev-secret-change-me')


def _encode_token(user_id: int) -> str:
    payload = {
        'sub': str(user_id),
        'iat': datetime.now(timezone.utc),
        'exp': datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_H),
    }
    return jwt.encode(payload, _secret(), algorithm='HS256')


def _decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=['HS256'])


# ---------------------------------------------------------------------------
# Cookie helper
# ---------------------------------------------------------------------------

def _set_auth_cookie(response: Response, token: str) -> None:
    is_prod = os.getenv('ENVIRONMENT', os.getenv('FLASK_ENV', '')) == 'production'
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite='lax',
        secure=is_prod,
        max_age=TOKEN_TTL_H * 3600,
        path='/',
    )


# ---------------------------------------------------------------------------
# FastAPI dependency: get_current_user
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    db_session: Session = Depends(get_db),
) -> StaffUser:
    """
    FastAPI dependency that authenticates the incoming request.

    - Enforces X-Requested-With header on mutating methods (CSRF guard).
    - Reads the JWT from the session cookie.
    - Decodes and validates the token.
    - Fetches the StaffUser from the database via run_in_threadpool.
    - Stores a refreshed token on request.state.slide_token for middleware
      to attach to the response cookie.
    """
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            raise HTTPException(
                status_code=403,
                detail='Forbidden — missing required request header',
            )

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail='Authentication required')

    try:
        payload = _decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Session expired — please log in again')
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail='Invalid token — please log in again')

    user_id = int(payload['sub'])

    def _fetch_user() -> StaffUser | None:
        return db_session.get(StaffUser, user_id)

    user = await run_in_threadpool(_fetch_user)

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='Account not found or disabled')

    # Store a freshly-minted token so slide-refresh middleware can set it.
    request.state.slide_token = _encode_token(user.id)

    return user


# ---------------------------------------------------------------------------
# Pydantic request schema
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

auth_router = APIRouter(prefix='/auth', tags=['auth'])


@auth_router.post('/login')
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db_session: Session = Depends(get_db),
):
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    client_ip = (
        forwarded_for.split(',')[0].strip()
        if forwarded_for
        else (request.client.host if request.client else '')
    )

    if not _check_login_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail='Too many login attempts. Please wait a few minutes.',
        )

    email    = body.email.strip().lower()
    password = body.password

    if not email or not password:
        raise HTTPException(status_code=400, detail='Email and password are required')

    def _query_user() -> StaffUser | None:
        return db_session.query(StaffUser).filter_by(email=email).first()

    user = await run_in_threadpool(_query_user)

    def _check_pw() -> bool:
        if not user:
            return False
        try:
            return bcrypt.checkpw(
                password.encode('utf-8'),
                user.password_hash.encode('utf-8'),
            )
        except Exception:
            return False

    pw_ok = await run_in_threadpool(_check_pw)

    if not user or not pw_ok or not user.is_active:
        _record_login_failure(client_ip)
        raise HTTPException(status_code=401, detail='Invalid email or password')

    def _update_last_login():
        user.last_login_at = datetime.now(timezone.utc)
        db_session.commit()

    await run_in_threadpool(_update_last_login)

    token = _encode_token(user.id)
    _set_auth_cookie(response, token)

    logger.info("Login: user %d (%s) authenticated", user.id, user.email)
    return {'status': 'ok', 'user': user.to_dict()}


@auth_router.post('/logout')
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path='/')
    return {'status': 'ok'}


@auth_router.get('/me')
async def me(current_user: StaffUser = Depends(get_current_user)):
    return {'user': current_user.to_dict()}
