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

    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'Client name is required'}), 400

    client = Client(
        reference_code   = _next_reference_code(),
        name             = name,
        email            = str(data.get('email',            '')).strip() or None,
        phone            = str(data.get('phone',            '')).strip() or None,
        company          = str(data.get('company',          '')).strip() or None,
        home_city        = str(data.get('home_city',        '')).strip() or None,
        preferred_budget = str(data.get('preferred_budget', '')).strip() or None,
        travel_style     = str(data.get('travel_style',     '')).strip() or None,
        notes            = str(data.get('notes',            '')).strip() or None,
        tags             = str(data.get('tags',             '')).strip() or None,
        created_by_id    = g.current_user.id,
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

    # Updateable fields — only overwrite if the key is present in the payload
    str_fields = ['name', 'email', 'phone', 'company', 'home_city',
                  'preferred_budget', 'travel_style', 'notes', 'tags']
    for field in str_fields:
        if field in data:
            setattr(client, field, str(data[field]).strip() or None)

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
