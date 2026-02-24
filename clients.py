import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from database import get_db
from auth import get_current_user
from schemas import ClientCreate, ClientUpdate
from models import Client, StaffUser

logger = logging.getLogger(__name__)

clients_router = APIRouter(prefix='/clients', tags=['clients'])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_reference_code(db: Session) -> str:
    last = (
        db.query(Client)
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
        return f'CLT-{db.query(Client).count() + 1:03d}'


def _client_or_404(db: Session, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if not client or client.is_deleted:
        raise HTTPException(status_code=404, detail='Client not found')
    return client


# ── Routes ────────────────────────────────────────────────────────────────────

@clients_router.get('')
async def list_clients(
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    clients = await run_in_threadpool(
        lambda: (
            db.query(Client)
            .filter_by(is_deleted=False)
            .order_by(Client.created_at.desc())
            .all()
        )
    )
    return {'clients': [c.to_dict() for c in clients]}


@clients_router.post('', status_code=201)
async def create_client(
    body: ClientCreate,
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    def _create():
        client = Client(
            reference_code       = _next_reference_code(db),
            name                 = body.name,
            email                = body.email,
            phone                = body.phone,
            company              = body.company,
            home_city            = body.home_city,
            preferred_budget     = body.preferred_budget,
            travel_style         = body.travel_style,
            dietary_requirements = body.dietary_requirements,
            notes                = body.notes,
            tags                 = body.tags,
            created_by_id        = current_user.id,
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        return client

    client = await run_in_threadpool(_create)
    logger.info("Client created: %s by staff %d", client.reference_code, current_user.id)
    return {'client': client.to_dict()}


@clients_router.get('/{client_id}')
async def get_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    client = await run_in_threadpool(lambda: _client_or_404(db, client_id))
    return {'client': client.to_dict(include_trips=True)}


@clients_router.put('/{client_id}')
async def update_client(
    client_id: int,
    body: ClientUpdate,
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    def _update():
        client = _client_or_404(db, client_id)

        # Only update fields that were actually sent in the request body.
        for field in body.model_fields_set:
            setattr(client, field, getattr(body, field))

        if not client.name:
            raise HTTPException(status_code=400, detail='Client name cannot be empty')

        client.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(client)
        return client

    client = await run_in_threadpool(_update)
    logger.info("Client updated: %s by staff %d", client.reference_code, current_user.id)
    return {'client': client.to_dict()}


@clients_router.delete('/{client_id}')
async def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: StaffUser = Depends(get_current_user),
):
    def _delete():
        client = _client_or_404(db, client_id)
        client.is_deleted = True
        client.updated_at = datetime.now(timezone.utc)
        db.commit()
        return client

    client = await run_in_threadpool(_delete)
    logger.info("Client soft-deleted: %s by staff %d", client.reference_code, current_user.id)
    return {'status': 'ok', 'message': f'Client {client.reference_code} deleted'}
