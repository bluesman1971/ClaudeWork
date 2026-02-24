"""
trips.py — Trip persistence router for Trip Master (FastAPI)

Routes (all require authentication):
  GET    /trips              — list trips (optional ?client_id=N filter)
  POST   /trips              — create a trip record (draft, from /generate data)
  GET    /trips/{id}         — get one trip (optional ?include_html=true)
  PUT    /trips/{id}         — partial update (approved selections, final HTML, etc.)
  DELETE /trips/{id}         — soft-delete a trip

A trip is first saved as 'draft' when /generate returns results.
When /finalize completes, the trip is updated to 'finalized' with approved
indices and final HTML.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from database import get_db
from auth import get_current_user
from schemas import TripCreate, TripUpdate
from models import Trip, Client, StaffUser

logger = logging.getLogger(__name__)

trips_router = APIRouter(prefix='/trips', tags=['trips'])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trip_or_404(db: Session, trip_id: int) -> Trip:
    trip = db.get(Trip, trip_id)
    if not trip or trip.is_deleted:
        raise HTTPException(status_code=404, detail='Trip not found')
    return trip


def _auto_title(location: str, duration: int) -> str:
    return f"{location} — {duration} day{'s' if duration != 1 else ''}"


# ── Routes ────────────────────────────────────────────────────────────────────

@trips_router.get('')
async def list_trips(
    client_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    """GET /trips[?client_id=N] — list non-deleted trips, newest first."""
    def _query():
        q = db.query(Trip).filter_by(is_deleted=False)
        if client_id is not None:
            q = q.filter_by(client_id=client_id)
        return q.order_by(Trip.created_at.desc()).all()

    trips = await run_in_threadpool(_query)
    return {'trips': [t.to_dict() for t in trips]}


@trips_router.post('', status_code=201)
async def create_trip(
    body: TripCreate,
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    """
    POST /trips — save a trip draft immediately after /generate returns.
    """
    def _create():
        if body.client_id is not None:
            client = db.get(Client, body.client_id)
            if not client or client.is_deleted:
                raise HTTPException(status_code=404, detail='Client not found')

        title = body.title or _auto_title(body.location, body.duration)

        trip = Trip(
            client_id            = body.client_id,
            created_by_id        = current_user.id,
            title                = title,
            status               = 'draft',
            location             = body.location,
            duration             = body.duration,
            budget               = body.budget,
            distance             = body.distance,
            include_photos       = body.include_photos,
            include_dining       = body.include_dining,
            include_attractions  = body.include_attractions,
            photos_per_day       = body.photos_per_day,
            restaurants_per_day  = body.restaurants_per_day,
            attractions_per_day  = body.attractions_per_day,
            photo_interests      = body.photo_interests,
            cuisines             = body.cuisines,
            attraction_cats      = body.attraction_cats,
            raw_photos           = json.dumps(body.raw_photos),
            raw_restaurants      = json.dumps(body.raw_restaurants),
            raw_attractions      = json.dumps(body.raw_attractions),
            colors               = json.dumps(body.colors),
            session_id           = body.session_id,
        )
        db.add(trip)
        db.commit()
        db.refresh(trip)
        return trip

    trip = await run_in_threadpool(_create)
    logger.info("Trip draft created: id=%d %r by staff %d", trip.id, trip.title, current_user.id)
    return {'trip': trip.to_dict()}


@trips_router.get('/{trip_id}')
async def get_trip(
    trip_id: int,
    include_html: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    """GET /trips/{id} — full trip including raw suggestions."""
    trip = await run_in_threadpool(lambda: _trip_or_404(db, trip_id))
    return {'trip': trip.to_dict(include_html=include_html)}


@trips_router.put('/{trip_id}')
async def update_trip(
    trip_id: int,
    body: TripUpdate,
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    """
    PUT /trips/{id} — partial update.

    Used by the frontend after /finalize to save:
      - approved_photo_indices, approved_restaurant_indices, approved_attraction_indices
      - final_html
      - status → 'finalized'
    Also used to re-assign a client, update the title, etc.
    """
    def _update():
        trip = _trip_or_404(db, trip_id)
        sent = body.model_fields_set

        # Status update
        if 'status' in sent and body.status is not None:
            trip.status = body.status

        # Title
        if 'title' in sent and body.title is not None:
            trip.title = body.title or trip.title

        # Client re-assignment
        if 'client_id' in sent:
            if body.client_id is None:
                trip.client_id = None
            else:
                client = db.get(Client, body.client_id)
                if not client or client.is_deleted:
                    raise HTTPException(status_code=404, detail='Client not found')
                trip.client_id = body.client_id

        # Approved index arrays (stored as JSON strings)
        if 'approved_photo_indices' in sent and body.approved_photo_indices is not None:
            trip.approved_photo_indices = json.dumps(body.approved_photo_indices)
        if 'approved_restaurant_indices' in sent and body.approved_restaurant_indices is not None:
            trip.approved_restaurant_indices = json.dumps(body.approved_restaurant_indices)
        if 'approved_attraction_indices' in sent and body.approved_attraction_indices is not None:
            trip.approved_attraction_indices = json.dumps(body.approved_attraction_indices)

        # Final HTML (potentially large)
        if 'final_html' in sent:
            trip.final_html = body.final_html

        trip.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(trip)
        return trip

    trip = await run_in_threadpool(_update)
    logger.info("Trip updated: id=%d status=%s by staff %d", trip.id, trip.status, current_user.id)
    return {'trip': trip.to_dict()}


@trips_router.delete('/{trip_id}')
async def delete_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    """DELETE /trips/{id} — soft-delete."""
    def _delete():
        trip = _trip_or_404(db, trip_id)
        trip.is_deleted = True
        trip.updated_at = datetime.now(timezone.utc)
        db.commit()
        return trip

    trip = await run_in_threadpool(_delete)
    logger.info("Trip soft-deleted: id=%d by staff %d", trip.id, current_user.id)
    return {'status': 'ok', 'message': f'Trip #{trip.id} deleted'}
