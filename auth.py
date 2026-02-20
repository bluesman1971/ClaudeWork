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
# LOGIN_WINDOW_SECONDS, further attempts are blocked for LOGIN_LOCKOUT_SECONDS.
# Uses an in-memory store — resets on server restart (acceptable for defence-in-depth;
# a Redis-backed store would be needed for multi-worker persistent rate limiting).
LOGIN_MAX_ATTEMPTS    = 10   # allowed failures before lockout
LOGIN_WINDOW_SECONDS  = 300  # 5 minutes — sliding window for counting failures
LOGIN_LOCKOUT_SECONDS = 600  # 10 minutes — lockout duration after exceeding limit

_login_attempts: dict = defaultdict(list)  # ip -> [timestamp, ...]
_login_lock = threading.Lock()


def _check_login_rate_limit(ip: str) -> bool:
    """
    Return True if the request should be allowed, False if the IP is rate-limited.
    Prunes old entries and checks the failure count within the sliding window.
    """
    now = time.time()
    with _login_lock:
        # Remove attempts outside the sliding window
        _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_WINDOW_SECONDS]
        if len(_login_attempts[ip]) >= LOGIN_MAX_ATTEMPTS:
            return False
        return True


def _record_login_failure(ip: str):
    """Record a failed login attempt for the given IP."""
    with _login_lock:
        _login_attempts[ip].append(time.time())


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
