"""
auth.py — Authentication Blueprint for Trip Master

Provides:
  - JWT helpers (encode / decode / slide)
  - require_auth decorator  (attach to any route that needs a logged-in user)
  - Routes: POST /auth/login, POST /auth/logout, GET /auth/me
  - Flask CLI: flask create-user (admin creates staff accounts)

JWT lives in an httpOnly cookie called 'tm_token'.
Token TTL: 8 hours, sliding (re-issued on every authenticated request).
"""

import os
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import wraps

from redis_client import get_redis

import bcrypt
import jwt
from flask import Blueprint, request, jsonify, g, make_response, current_app
import click

from models import db, StaffUser

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# ── Constants ────────────────────────────────────────────────────────────────

COOKIE_NAME   = 'tm_token'
TOKEN_TTL_H   = 8          # hours
BCRYPT_ROUNDS = 12

# ── Login rate limiting ───────────────────────────────────────────────────────
# Tracks failed login attempts per IP.  After LOGIN_MAX_ATTEMPTS failures within
# LOGIN_WINDOW_SECONDS, further attempts are blocked.
#
# Redis path:  sorted set  ratelimit:login:{ip}
#              members are timestamps (score = timestamp, member = timestamp string)
#              ZREMRANGEBYSCORE prunes old entries; ZCARD counts remaining.
# Fallback:    in-memory dict per-worker (resets on restart).
LOGIN_MAX_ATTEMPTS   = 10   # allowed failures before lockout
LOGIN_WINDOW_SECONDS = 300  # 5-minute sliding window

_login_attempts: dict = defaultdict(list)  # ip -> [timestamp, ...] (fallback only)
_login_lock = threading.Lock()


def _check_login_rate_limit(ip: str) -> bool:
    """
    Return True if the request should be allowed, False if the IP is rate-limited.
    Uses a Redis sorted-set sliding window when Redis is available, otherwise
    falls back to the in-memory dict.
    """
    now = time.time()
    r = get_redis()

    if r is not None:
        try:
            key = f"ratelimit:login:{ip}"
            pipe = r.pipeline()
            # Remove timestamps older than the window
            pipe.zremrangebyscore(key, '-inf', now - LOGIN_WINDOW_SECONDS)
            pipe.zcard(key)
            pipe.expire(key, LOGIN_WINDOW_SECONDS)
            _, count, _ = pipe.execute()
            return count < LOGIN_MAX_ATTEMPTS
        except Exception as exc:
            logger.warning("Redis login rate-limit check error: %s — falling back", exc)

    # In-memory fallback
    with _login_lock:
        _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_WINDOW_SECONDS]
        return len(_login_attempts[ip]) < LOGIN_MAX_ATTEMPTS


def _record_login_failure(ip: str):
    """Record a failed login attempt for the given IP."""
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

    # In-memory fallback
    with _login_lock:
        _login_attempts[ip].append(now)


# ── Per-user AI endpoint rate limiting ───────────────────────────────────────
# Limits authenticated users from hammering the expensive AI scout endpoints.
# Keyed by (user_id, endpoint) so /generate and /replace have independent budgets.
#
# Redis path:  sorted set  ratelimit:user:{user_id}:{endpoint}
#              members are timestamps; ZREMRANGEBYSCORE prunes the window.
# Fallback:    in-memory dict per-worker (resets on restart).

RATE_LIMIT_RULES: dict[str, tuple[int, int]] = {
    # endpoint_key -> (max_requests, window_seconds)
    'generate': (20, 600),   # 20 calls per 10 minutes
    'replace':  (60, 600),   # 60 calls per 10 minutes
}

_user_requests: dict = defaultdict(list)  # (user_id, endpoint) -> [timestamp, ...] (fallback)
_user_rate_lock = threading.Lock()


def check_user_rate_limit(user_id: int, endpoint: str) -> tuple[bool, int]:
    """
    Check whether user_id is within their rate limit for the given endpoint key.

    Returns (allowed: bool, retry_after_seconds: int).
    If allowed, also records this request timestamp.
    If not allowed, retry_after_seconds is the number of seconds until the
    oldest request in the window expires (i.e. when a slot opens up).

    Uses Redis sorted-set sliding window when available; falls back to the
    in-memory dict otherwise.
    """
    rule = RATE_LIMIT_RULES.get(endpoint)
    if rule is None:
        return True, 0   # unknown endpoint — let it through

    max_requests, window = rule
    now = time.time()
    r = get_redis()

    if r is not None:
        try:
            rkey = f"ratelimit:user:{user_id}:{endpoint}"
            pipe = r.pipeline()
            # Prune entries outside the sliding window
            pipe.zremrangebyscore(rkey, '-inf', now - window)
            pipe.zrange(rkey, 0, -1, withscores=True)
            pipe.expire(rkey, window)
            _, entries, _ = pipe.execute()

            count = len(entries)
            if count >= max_requests:
                oldest_score = min(score for _, score in entries)
                retry_after = int(window - (now - oldest_score)) + 1
                return False, retry_after

            # Record this request
            r.zadd(rkey, {str(now): now})
            r.expire(rkey, window)
            return True, 0
        except Exception as exc:
            logger.warning("Redis user rate-limit error: %s — falling back", exc)

    # In-memory fallback
    mem_key = (user_id, endpoint)
    with _user_rate_lock:
        _user_requests[mem_key] = [t for t in _user_requests[mem_key] if now - t < window]

        if len(_user_requests[mem_key]) >= max_requests:
            oldest = min(_user_requests[mem_key])
            retry_after = int(window - (now - oldest)) + 1
            return False, retry_after

        _user_requests[mem_key].append(now)
        return True, 0


# ── JWT helpers ──────────────────────────────────────────────────────────────

def _secret():
    """Read JWT secret from app config (set from env)."""
    return current_app.config['JWT_SECRET_KEY']


def _encode_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        'sub':  str(user_id),   # PyJWT 2.x requires sub to be a string
        'iat':  now,
        'exp':  now + timedelta(hours=TOKEN_TTL_H),
    }
    return jwt.encode(payload, _secret(), algorithm='HS256')


def _decode_token(token: str) -> dict:
    """Raise jwt.PyJWTError if invalid or expired."""
    return jwt.decode(token, _secret(), algorithms=['HS256'])


def _set_auth_cookie(response, token: str):
    """Write the JWT into an httpOnly cookie on the response."""
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite='Lax',
        secure=os.getenv('FLASK_ENV', 'development') == 'production',
        max_age=TOKEN_TTL_H * 3600,
        path='/',
    )


def _clear_auth_cookie(response):
    response.delete_cookie(COOKIE_NAME, path='/')


# ── require_auth decorator ───────────────────────────────────────────────────

def require_auth(f):
    """
    Decorator — validates JWT cookie, loads the user into g.current_user,
    slides the token expiry, and attaches a fresh cookie to the response.

    Usage:
        @app.route('/generate', methods=['POST'])
        @require_auth
        def generate_trip_guide():
            user = g.current_user   # StaffUser ORM object
            ...
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Anti-CSRF defence: state-changing requests must include a custom header.
        # Browsers will never automatically attach X-Requested-With on cross-site
        # requests, so this header cannot be forged by a third-party page — even
        # if it manages to trigger a request using the user's session cookie.
        # This is an additive defence on top of SameSite=Lax cookies.
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
                return jsonify({'error': 'Forbidden — missing required request header'}), 403

        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return jsonify({'error': 'Authentication required'}), 401

        try:
            payload = _decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Session expired — please log in again'}), 401
        except jwt.PyJWTError:
            return jsonify({'error': 'Invalid token — please log in again'}), 401

        user = db.session.get(StaffUser, int(payload['sub']))
        if not user or not user.is_active:
            return jsonify({'error': 'Account not found or disabled'}), 401

        g.current_user = user

        # Call the actual route
        result = f(*args, **kwargs)

        # Slide the token: attach a fresh cookie with a new expiry
        # Always materialise the response via make_response so we preserve
        # the HTTP status code (e.g. 201 for POST /clients).
        new_token = _encode_token(user.id)
        if isinstance(result, tuple):
            resp_obj = make_response(*result)
        else:
            resp_obj = make_response(result)
        _set_auth_cookie(resp_obj, new_token)
        return resp_obj

    return decorated


# ── Routes ───────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['POST'])
def login():
    """POST /auth/login — { email, password } → sets httpOnly cookie."""
    # Rate limit by client IP to slow brute-force attacks
    client_ip = request.remote_addr or '0.0.0.0'
    if not _check_login_rate_limit(client_ip):
        logger.warning("Login rate limit exceeded for IP %s", client_ip)
        return jsonify({'error': 'Too many login attempts. Please wait and try again.'}), 429

    data = request.get_json(force=True, silent=True) or {}
    email    = str(data.get('email',    '')).strip().lower()
    password = str(data.get('password', '')).strip()

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    user = StaffUser.query.filter_by(email=email).first()

    if not user or not user.is_active:
        # Generic message — don't reveal whether the email exists
        _record_login_failure(client_ip)
        return jsonify({'error': 'Invalid email or password'}), 401

    try:
        if not bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
            _record_login_failure(client_ip)
            return jsonify({'error': 'Invalid email or password'}), 401
    except Exception as exc:
        logger.warning("bcrypt check error: %s", exc)
        _record_login_failure(client_ip)
        return jsonify({'error': 'Invalid email or password'}), 401

    # Update last login timestamp
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()

    token = _encode_token(user.id)
    resp  = make_response(jsonify({
        'status': 'ok',
        'user':   user.to_dict(),
    }))
    _set_auth_cookie(resp, token)
    logger.info("Login: user_id=%d", user.id)
    return resp


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """POST /auth/logout — clears the auth cookie."""
    resp = make_response(jsonify({'status': 'ok'}))
    _clear_auth_cookie(resp)
    return resp


@auth_bp.route('/me', methods=['GET'])
@require_auth
def me():
    """GET /auth/me — returns the current user's profile."""
    return jsonify({'user': g.current_user.to_dict()})


# ── Flask CLI: flask create-user ─────────────────────────────────────────────

def register_cli(app):
    """Call this from app.py after creating the Flask app to register the CLI command."""

    @app.cli.command('create-user')
    @click.option('--email',     prompt='Email',     help='Staff email address')
    @click.option('--name',      prompt='Full name', help='Staff full name')
    @click.option('--password',  prompt='Password',  hide_input=True,
                  confirmation_prompt='Confirm password', help='Login password')
    @click.option('--role',      default='staff',
                  type=click.Choice(['admin', 'staff']), help='Role (default: staff)')
    def create_user(email, name, password, role):
        """Create a new staff user account."""
        with app.app_context():
            email = email.strip().lower()
            existing = StaffUser.query.filter_by(email=email).first()
            if existing:
                click.echo(f'ERROR: A user with email {email!r} already exists.')
                raise SystemExit(1)

            pw_hash = bcrypt.hashpw(
                password.encode('utf-8'),
                bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
            ).decode('utf-8')

            user = StaffUser(
                email=email,
                full_name=name.strip(),
                password_hash=pw_hash,
                role=role,
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            click.echo(f'✓ Created {role} account for {email!r} (id={user.id})')
