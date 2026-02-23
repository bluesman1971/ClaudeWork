"""
clients.py — Client CRM Blueprint for Trip Master

Routes (all require authentication):
  GET    /clients          — list all active clients (newest first)
  POST   /clients          — create a new client
  GET    /clients/<id>     — get one client (with their trips)
  PUT    /clients/<id>     — update client fields
  DELETE /clients/<id>     — soft-delete a client

Reference codes are auto-generated as CLT-001, CLT-002, etc.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g

from models import db, Client, StaffUser
from auth import require_auth

# Field length caps — keep in sync with MAX_FIELD_* constants in app.py
_MAX_SHORT  = 150   # single-line profile fields
_MAX_MEDIUM = 500   # multi-line fields (notes)
_MAX_NAME   = 200   # client name

import re as _re


def _clamp(value, max_len: int) -> str | None:
    """
    Sanitise a single-line field: collapse all whitespace (including newlines
    and tabs) to a single space, strip ends, truncate to max_len.
    Returns None if the result is empty.
    """
    s = _re.sub(r'\s+', ' ', str(value)).strip()[:max_len]
    return s or None


def _clamp_multiline(value, max_len: int) -> str | None:
    """
    Sanitise a multi-line field: strip leading/trailing whitespace only
    (internal newlines are legitimate). Truncate to max_len.
    Returns None if the result is empty.
    """
    s = str(value).strip()[:max_len]
    return s or None

logger = logging.getLogger(__name__)

clients_bp = Blueprint('clients', __name__, url_prefix='/clients')


# ── Helpers ──────────────────────────────────────────────────────────────────

def _next_reference_code() -> str:
    """Auto-increment CLT-NNN reference codes."""
    last = (
        Client.query
        .filter(Client.reference_code.like('CLT-%'))
        .order_by(Client.id.desc())
        .first()
    )
    if not last:
        return 'CLT-001'
    try:
        n = int(last.reference_code.split('-')[1])
        return f'CLT-{n + 1:03d}'
    except (IndexError, ValueError):
        return f'CLT-{Client.query.count() + 1:03d}'


def _client_or_404(client_id: int):
    client = db.session.get(Client, client_id)
    if not client or client.is_deleted:
        return None
    return client


# ── Routes ───────────────────────────────────────────────────────────────────

@clients_bp.route('', methods=['GET'])
@require_auth
def list_clients():
    """GET /clients — all active clients, newest first."""
    clients = (
        Client.query
        .filter_by(is_deleted=False)
        .order_by(Client.created_at.desc())
        .all()
    )
    return jsonify({'clients': [c.to_dict() for c in clients]})


@clients_bp.route('', methods=['POST'])
@require_auth
def create_client():
    """POST /clients — create a new client record."""
    data = request.get_json(force=True, silent=True) or {}

    name = _clamp(data.get('name', ''), _MAX_NAME)
    if not name:
        return jsonify({'error': 'Client name is required'}), 400

    client = Client(
        reference_code       = _next_reference_code(),
        name                 = name,
        email                = _clamp(data.get('email',               ''), _MAX_SHORT),
        phone                = _clamp(data.get('phone',               ''), _MAX_SHORT),
        company              = _clamp(data.get('company',             ''), _MAX_SHORT),
        home_city            = _clamp(data.get('home_city',           ''), _MAX_SHORT),
        preferred_budget     = _clamp(data.get('preferred_budget',    ''), _MAX_SHORT),
        travel_style         = _clamp(data.get('travel_style',        ''), _MAX_SHORT),
        dietary_requirements = _clamp(data.get('dietary_requirements',''), _MAX_SHORT),
        notes                = _clamp_multiline(data.get('notes',      ''), _MAX_MEDIUM),
        tags                 = _clamp(data.get('tags',                ''), _MAX_SHORT),
        created_by_id        = g.current_user.id,
    )
    db.session.add(client)
    db.session.commit()

    logger.info("Client created: %s by staff %d", client.reference_code, g.current_user.id)
    return jsonify({'client': client.to_dict()}), 201


@clients_bp.route('/<int:client_id>', methods=['GET'])
@require_auth
def get_client(client_id):
    """GET /clients/<id> — one client with their trips."""
    client = _client_or_404(client_id)
    if not client:
        return jsonify({'error': 'Client not found'}), 404
    return jsonify({'client': client.to_dict(include_trips=True)})


@clients_bp.route('/<int:client_id>', methods=['PUT'])
@require_auth
def update_client(client_id):
    """PUT /clients/<id> — update client fields (partial update supported)."""
    client = _client_or_404(client_id)
    if not client:
        return jsonify({'error': 'Client not found'}), 404

    data = request.get_json(force=True, silent=True) or {}

    # Updateable fields with per-field length caps
    # Only overwrite if the key is present in the payload
    _field_caps = {
        'name':                 _MAX_NAME,
        'email':                _MAX_SHORT,
        'phone':                _MAX_SHORT,
        'company':              _MAX_SHORT,
        'home_city':            _MAX_SHORT,
        'preferred_budget':     _MAX_SHORT,
        'travel_style':         _MAX_SHORT,
        'dietary_requirements': _MAX_SHORT,
        'notes':                None,        # multi-line — handled separately below
        'tags':                 _MAX_SHORT,
    }
    for field, max_len in _field_caps.items():
        if field in data:
            if max_len is None:
                # multi-line field — preserve internal newlines
                setattr(client, field, _clamp_multiline(data[field], _MAX_MEDIUM))
            else:
                setattr(client, field, _clamp(data[field], max_len))

    # Protect required field
    if not client.name:
        return jsonify({'error': 'Client name cannot be empty'}), 400

    client.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    logger.info("Client updated: %s by staff %d", client.reference_code, g.current_user.id)
    return jsonify({'client': client.to_dict()})


@clients_bp.route('/<int:client_id>', methods=['DELETE'])
@require_auth
def delete_client(client_id):
    """DELETE /clients/<id> — soft-delete (trips are preserved)."""
    client = _client_or_404(client_id)
    if not client:
        return jsonify({'error': 'Client not found'}), 404

    client.is_deleted = True
    client.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    logger.info("Client soft-deleted: %s by staff %d", client.reference_code, g.current_user.id)
    return jsonify({'status': 'ok', 'message': f'Client {client.reference_code} deleted'})
