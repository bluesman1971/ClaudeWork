"""
SQLAlchemy ORM models for Trip Master.

Three models:
  StaffUser  — internal team accounts (admin creates via CLI, no self-registration)
  Client     — CRM-style client record; staff run trips on behalf of clients
  Trip       — stores form params + raw suggestions so trips can be reloaded/revised

Default database: SQLite (trip_master.db).
Production: set DATABASE_URL env var to a PostgreSQL connection string and the
app will use that instead — no code changes required.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import declarative_base, relationship


def _utcnow():
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


# db is kept as a module-level name so external imports (database.py, manage.py)
# can reference db.metadata for table creation.
db = declarative_base()


# ---------------------------------------------------------------------------
# StaffUser
# ---------------------------------------------------------------------------

class StaffUser(db):
    __tablename__ = 'staff_users'

    id            = Column(Integer, primary_key=True)
    email         = Column(String(255), unique=True, nullable=False, index=True)
    full_name     = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role          = Column(String(20), nullable=False, default='staff')   # 'admin' | 'staff'
    is_active     = Column(Boolean, nullable=False, default=True)
    created_at    = Column(DateTime, nullable=False, default=_utcnow)
    last_login_at = Column(DateTime, nullable=True)

    # Relationships
    clients_created = relationship('Client', foreign_keys='Client.created_by_id',
                                   backref='created_by', lazy='dynamic')
    trips_created   = relationship('Trip',   foreign_keys='Trip.created_by_id',
                                   backref='created_by', lazy='dynamic')

    def to_dict(self):
        return {
            'id':            self.id,
            'email':         self.email,
            'full_name':     self.full_name,
            'role':          self.role,
            'is_active':     self.is_active,
            'created_at':    self.created_at.isoformat() if self.created_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
        }

    def __repr__(self):
        return f'<StaffUser {self.email} role={self.role}>'


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class Client(db):
    __tablename__ = 'clients'

    id               = Column(Integer, primary_key=True)
    reference_code   = Column(String(20), unique=True, nullable=False, index=True)
    name             = Column(String(255), nullable=False)
    email            = Column(String(255), nullable=True)
    phone            = Column(String(50),  nullable=True)
    company          = Column(String(255), nullable=True)
    home_city        = Column(String(255), nullable=True)
    preferred_budget = Column(String(50),  nullable=True)  # e.g. 'budget', 'moderate', 'luxury'
    travel_style          = Column(String(255), nullable=True)  # free-text tags / sentence
    dietary_requirements  = Column(Text,        nullable=True)  # e.g. 'vegetarian, nut allergy'
    notes                 = Column(Text,        nullable=True)
    tags                  = Column(String(500), nullable=True)  # comma-separated labels
    created_by_id    = Column(Integer, ForeignKey('staff_users.id'), nullable=True)
    is_deleted       = Column(Boolean, nullable=False, default=False)  # soft-delete
    created_at       = Column(DateTime, nullable=False, default=_utcnow)
    updated_at       = Column(DateTime, nullable=False, default=_utcnow,
                              onupdate=_utcnow)

    # Relationships
    trips = relationship('Trip', backref='client', lazy='dynamic',
                         foreign_keys='Trip.client_id')

    def to_dict(self, include_trips=False):
        d = {
            'id':               self.id,
            'reference_code':   self.reference_code,
            'name':             self.name,
            'email':            self.email,
            'phone':            self.phone,
            'company':          self.company,
            'home_city':        self.home_city,
            'preferred_budget': self.preferred_budget,
            'travel_style':         self.travel_style,
            'dietary_requirements': self.dietary_requirements,
            'notes':                self.notes,
            'tags':             self.tags,
            'created_by_id':    self.created_by_id,
            'is_deleted':       self.is_deleted,
            'created_at':       self.created_at.isoformat()  if self.created_at  else None,
            'updated_at':       self.updated_at.isoformat()  if self.updated_at  else None,
        }
        if include_trips:
            d['trips'] = [t.to_dict() for t in self.trips.filter_by(is_deleted=False)
                          .order_by(Trip.created_at.desc()).all()]
        return d

    def __repr__(self):
        return f'<Client {self.reference_code} {self.name!r}>'


# ---------------------------------------------------------------------------
# Trip
# ---------------------------------------------------------------------------

class Trip(db):
    __tablename__ = 'trips'

    id            = Column(Integer, primary_key=True)
    client_id     = Column(Integer, ForeignKey('clients.id'), nullable=True, index=True)
    created_by_id = Column(Integer, ForeignKey('staff_users.id'), nullable=True)

    title      = Column(String(255), nullable=True)   # auto-generated if blank
    status     = Column(String(20),  nullable=False, default='draft')  # 'draft' | 'finalized'
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    # ── Form parameters ──────────────────────────────────────────────────────
    location            = Column(String(255), nullable=False)
    duration            = Column(Integer,     nullable=False)
    budget              = Column(String(50),  nullable=True)
    distance            = Column(String(50),  nullable=True)
    include_photos      = Column(Boolean,     default=True)
    include_dining      = Column(Boolean,     default=True)
    include_attractions = Column(Boolean,     default=True)
    photos_per_day      = Column(Integer,     default=3)
    restaurants_per_day = Column(Integer,     default=3)
    attractions_per_day = Column(Integer,     default=4)
    photo_interests     = Column(String(500), nullable=True)
    cuisines            = Column(String(500), nullable=True)
    attraction_cats     = Column(String(500), nullable=True)
    accommodation       = Column(String(500), nullable=True)  # hotel/address used as travel origin

    # ── Raw AI suggestions (full verified item dicts from /generate) ─────────
    raw_photos      = Column(Text, nullable=True)   # JSON array
    raw_restaurants = Column(Text, nullable=True)
    raw_attractions = Column(Text, nullable=True)

    # ── Approved selections (index arrays from /finalize) ───────────────────
    approved_photo_indices      = Column(Text, nullable=True)   # JSON [0,2,4,...]
    approved_restaurant_indices = Column(Text, nullable=True)
    approved_attraction_indices = Column(Text, nullable=True)

    # ── Final output ─────────────────────────────────────────────────────────
    final_html = Column(Text,        nullable=True)
    colors     = Column(String(500), nullable=True)   # JSON color dict

    # ── Session linkage ───────────────────────────────────────────────────────
    session_id = Column(String(36), nullable=True, index=True)  # UUID from /generate

    def to_dict(self, include_html=False):
        import json as _json
        d = {
            'id':           self.id,
            'client_id':    self.client_id,
            'created_by_id': self.created_by_id,
            'title':        self.title,
            'status':       self.status,
            'is_deleted':   self.is_deleted,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
            'updated_at':   self.updated_at.isoformat() if self.updated_at else None,
            # form params
            'location':             self.location,
            'duration':             self.duration,
            'budget':               self.budget,
            'distance':             self.distance,
            'include_photos':       self.include_photos,
            'include_dining':       self.include_dining,
            'include_attractions':  self.include_attractions,
            'photos_per_day':       self.photos_per_day,
            'restaurants_per_day':  self.restaurants_per_day,
            'attractions_per_day':  self.attractions_per_day,
            'photo_interests':      self.photo_interests,
            'cuisines':             self.cuisines,
            'attraction_cats':      self.attraction_cats,
            'accommodation':        self.accommodation,
            # suggestions
            'raw_photos':           _json.loads(self.raw_photos)      if self.raw_photos      else [],
            'raw_restaurants':      _json.loads(self.raw_restaurants) if self.raw_restaurants else [],
            'raw_attractions':      _json.loads(self.raw_attractions) if self.raw_attractions else [],
            # approved indices
            'approved_photo_indices':       _json.loads(self.approved_photo_indices)       if self.approved_photo_indices       else None,
            'approved_restaurant_indices':  _json.loads(self.approved_restaurant_indices)  if self.approved_restaurant_indices  else None,
            'approved_attraction_indices':  _json.loads(self.approved_attraction_indices)  if self.approved_attraction_indices  else None,
            # misc
            'colors':    _json.loads(self.colors) if self.colors else None,
            'session_id': self.session_id,
        }
        if include_html:
            d['final_html'] = self.final_html
        return d

    def __repr__(self):
        return f'<Trip #{self.id} {self.location!r} status={self.status}>'
