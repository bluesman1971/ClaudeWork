"""
trips.py — Trip persistence Blueprint for Trip Master

Routes (all require authentication):
  GET    /trips                  — list trips (optionally filter by client_id)
  POST   /trips                  — create a trip record (draft, from /generate data)
  GET    /trips/<id>             — get one trip (with full raw suggestions)
  PUT    /trips/<id>             — update a trip (e.g. save approved selections / finalized HTML)
  DELETE /trips/<id>             — soft-delete a trip

A trip is first saved as 'draft' when /generate returns results.
When /finalize completes, the trip is updated to 'finalized' with approved
indices and final HTML.
"""

import json
import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g

from models import db, Trip, Client
from auth import require_auth

logger = logging.getLogger(__name__)

trips_bp = Blueprint('trips', __name__, url_prefix='/trips')


# ── Helpers ──────────────────────────────────────────────────────────────────

def _trip_or_404(trip_id: int):
    trip = db.session.get(Trip, trip_id)
    if not trip or trip.is_deleted:
        return None
    return trip


def _auto_title(location: str, duration: int) -> str:
    return f"{location} — {duration} day{'s' if duration != 1 else ''}"


# ── Routes ───────────────────────────────────────────────────────────────────

@trips_bp.route('', methods=['GET'])
@require_auth
def list_trips():
    """GET /trips[?client_id=N] — list non-deleted trips, newest first."""
    client_id = request.args.get('client_id', type=int)

    q = Trip.query.filter_by(is_deleted=False)
    if client_id is not None:
        q = q.filter_by(client_id=client_id)
    trips = q.order_by(Trip.created_at.desc()).all()

    return jsonify({'trips': [t.to_dict() for t in trips]})


@trips_bp.route('', methods=['POST'])
@require_auth
def create_trip():
    """
    POST /trips — save a trip draft immediately after /generate returns.

    Expected body:
    {
      "client_id": 3,             // optional
      "session_id": "uuid",
      "location": "Paris, France",
      "duration": 3,
      "budget": "moderate",
      "distance": "walking",
      "include_photos": true,
      "include_dining": true,
      "include_attractions": true,
      "photos_per_day": 3,
      "restaurants_per_day": 3,
      "attractions_per_day": 4,
      "photo_interests": "...",
      "cuisines": "...",
      "attraction_cats": "...",
      "raw_photos": [...],
      "raw_restaurants": [...],
      "raw_attractions": [...],
      "colors": {...}
    }
    """
    data = request.get_json(force=True, silent=True) or {}

    location = str(data.get('location', '')).strip()
    if not location:
        return jsonify({'error': 'location is required'}), 400

    try:
        duration = int(data.get('duration', 1))
    except (ValueError, TypeError):
        return jsonify({'error': 'duration must be an integer'}), 400

    client_id = data.get('client_id')
    if client_id is not None:
        client_id = int(client_id)
        client = db.session.get(Client, client_id)
        if not client or client.is_deleted:
            return jsonify({'error': 'Client not found'}), 404

    title = str(data.get('title', '')).strip() or _auto_title(location, duration)

    trip = Trip(
        client_id            = client_id,
        created_by_id        = g.current_user.id,
        title                = title,
        status               = 'draft',
        location             = location,
        duration             = duration,
        budget               = str(data.get('budget',   '')).strip() or None,
        distance             = str(data.get('distance', '')).strip() or None,
        include_photos       = bool(data.get('include_photos',       True)),
        include_dining       = bool(data.get('include_dining',       True)),
        include_attractions  = bool(data.get('include_attractions',  True)),
        photos_per_day       = int(data.get('photos_per_day',      3)),
        restaurants_per_day  = int(data.get('restaurants_per_day', 3)),
        attractions_per_day  = int(data.get('attractions_per_day', 4)),
        photo_interests      = str(data.get('photo_interests',  '')).strip() or None,
        cuisines             = str(data.get('cuisines',         '')).strip() or None,
        attraction_cats      = str(data.get('attraction_cats',  '')).strip() or None,
        raw_photos           = json.dumps(data.get('raw_photos',      [])),
        raw_restaurants      = json.dumps(data.get('raw_restaurants', [])),
        raw_attractions      = json.dumps(data.get('raw_attractions', [])),
        colors               = json.dumps(data.get('colors', {})),
        session_id           = str(data.get('session_id', '')).strip() or None,
    )
    db.session.add(trip)
    db.session.commit()

    logger.info("Trip draft created: id=%d %r by staff %d", trip.id, title, g.current_user.id)
    return jsonify({'trip': trip.to_dict()}), 201


@trips_bp.route('/<int:trip_id>', methods=['GET'])
@require_auth
def get_trip(trip_id):
    """GET /trips/<id> — full trip including raw suggestions."""
    trip = _trip_or_404(trip_id)
    if not trip:
        return jsonify({'error': 'Trip not found'}), 404
    return jsonify({'trip': trip.to_dict(include_html=True)})


@trips_bp.route('/<int:trip_id>', methods=['PUT'])
@require_auth
def update_trip(trip_id):
    """
    PUT /trips/<id> — partial update.

    Used by the frontend after /finalize to save:
      - approved_photo_indices, approved_restaurant_indices, approved_attraction_indices
      - final_html
      - status → 'finalized'
    Also used to re-assign a client, update the title, etc.
    """
    trip = _trip_or_404(trip_id)
    if not trip:
        return jsonify({'error': 'Trip not found'}), 404

    data = request.get_json(force=True, silent=True) or {}

    # Status update
    if 'status' in data:
        if data['status'] in ('draft', 'finalized'):
            trip.status = data['status']

    # Title
    if 'title' in data:
        trip.title = str(data['title']).strip() or trip.title

    # Client re-assignment
    if 'client_id' in data:
        cid = data['client_id']
        if cid is None:
            trip.client_id = None
        else:
            cid = int(cid)
            client = db.session.get(Client, cid)
            if not client or client.is_deleted:
                return jsonify({'error': 'Client not found'}), 404
            trip.client_id = cid

    # Approved index arrays (JSON lists)
    if 'approved_photo_indices' in data:
        trip.approved_photo_indices = json.dumps(data['approved_photo_indices'])
    if 'approved_restaurant_indices' in data:
        trip.approved_restaurant_indices = json.dumps(data['approved_restaurant_indices'])
    if 'approved_attraction_indices' in data:
        trip.approved_attraction_indices = json.dumps(data['approved_attraction_indices'])

    # Final HTML (potentially large)
    if 'final_html' in data:
        trip.final_html = data['final_html']

    trip.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    logger.info("Trip updated: id=%d status=%s by staff %d", trip.id, trip.status, g.current_user.id)
    return jsonify({'trip': trip.to_dict()})


@trips_bp.route('/<int:trip_id>', methods=['DELETE'])
@require_auth
def delete_trip(trip_id):
    """DELETE /trips/<id> — soft-delete."""
    trip = _trip_or_404(trip_id)
    if not trip:
        return jsonify({'error': 'Trip not found'}), 404

    trip.is_deleted = True
    trip.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    logger.info("Trip soft-deleted: id=%d by staff %d", trip.id, g.current_user.id)
    return jsonify({'status': 'ok', 'message': f'Trip #{trip.id} deleted'})
