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
from flask_sqlalchemy import SQLAlchemy


def _utcnow():
    """Return the current UTC time as a timezone-aware datetime (replaces deprecated _utcnow)."""
    return datetime.now(timezone.utc)

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# StaffUser
# ---------------------------------------------------------------------------

class StaffUser(db.Model):
    __tablename__ = 'staff_users'

    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name     = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(20), nullable=False, default='staff')   # 'admin' | 'staff'
    is_active     = db.Column(db.Boolean, nullable=False, default=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=_utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    clients_created = db.relationship('Client', foreign_keys='Client.created_by_id',
                                      backref='created_by', lazy='dynamic')
    trips_created   = db.relationship('Trip',   foreign_keys='Trip.created_by_id',
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

class Client(db.Model):
    __tablename__ = 'clients'

    id               = db.Column(db.Integer, primary_key=True)
    reference_code   = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name             = db.Column(db.String(255), nullable=False)
    email            = db.Column(db.String(255), nullable=True)
    phone            = db.Column(db.String(50),  nullable=True)
    company          = db.Column(db.String(255), nullable=True)
    home_city        = db.Column(db.String(255), nullable=True)
    preferred_budget = db.Column(db.String(50),  nullable=True)  # e.g. 'budget', 'moderate', 'luxury'
    travel_style          = db.Column(db.String(255), nullable=True)  # free-text tags / sentence
    dietary_requirements  = db.Column(db.Text,        nullable=True)  # e.g. 'vegetarian, nut allergy'
    notes                 = db.Column(db.Text,        nullable=True)
    tags                  = db.Column(db.String(500), nullable=True)  # comma-separated labels
    created_by_id    = db.Column(db.Integer, db.ForeignKey('staff_users.id'), nullable=True)
    is_deleted       = db.Column(db.Boolean, nullable=False, default=False)  # soft-delete
    created_at       = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at       = db.Column(db.DateTime, nullable=False, default=_utcnow,
                                 onupdate=_utcnow)

    # Relationships
    trips = db.relationship('Trip', backref='client', lazy='dynamic',
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

class Trip(db.Model):
    __tablename__ = 'trips'

    id           = db.Column(db.Integer, primary_key=True)
    client_id    = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=True, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('staff_users.id'), nullable=True)

    title        = db.Column(db.String(255), nullable=True)   # auto-generated if blank
    status       = db.Column(db.String(20),  nullable=False, default='draft')  # 'draft' | 'finalized'
    is_deleted   = db.Column(db.Boolean, nullable=False, default=False)
    created_at   = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at   = db.Column(db.DateTime, nullable=False, default=_utcnow,
                             onupdate=_utcnow)

    # ── Form parameters ──────────────────────────────────────────────────────
    location            = db.Column(db.String(255), nullable=False)
    duration            = db.Column(db.Integer,     nullable=False)
    budget              = db.Column(db.String(50),  nullable=True)
    distance            = db.Column(db.String(50),  nullable=True)
    include_photos      = db.Column(db.Boolean,     default=True)
    include_dining      = db.Column(db.Boolean,     default=True)
    include_attractions = db.Column(db.Boolean,     default=True)
    photos_per_day      = db.Column(db.Integer,     default=3)
    restaurants_per_day = db.Column(db.Integer,     default=3)
    attractions_per_day = db.Column(db.Integer,     default=4)
    photo_interests     = db.Column(db.String(500), nullable=True)
    cuisines            = db.Column(db.String(500), nullable=True)
    attraction_cats     = db.Column(db.String(500), nullable=True)
    accommodation       = db.Column(db.String(500), nullable=True)  # hotel/address used as travel origin

    # ── Raw AI suggestions (full verified item dicts from /generate) ─────────
    raw_photos      = db.Column(db.Text, nullable=True)   # JSON array
    raw_restaurants = db.Column(db.Text, nullable=True)
    raw_attractions = db.Column(db.Text, nullable=True)

    # ── Approved selections (index arrays from /finalize) ───────────────────
    approved_photo_indices      = db.Column(db.Text, nullable=True)   # JSON [0,2,4,...]
    approved_restaurant_indices = db.Column(db.Text, nullable=True)
    approved_attraction_indices = db.Column(db.Text, nullable=True)

    # ── Final output ─────────────────────────────────────────────────────────
    final_html = db.Column(db.Text,        nullable=True)
    colors     = db.Column(db.String(500), nullable=True)   # JSON color dict

    # ── Session linkage ───────────────────────────────────────────────────────
    session_id = db.Column(db.String(36), nullable=True, index=True)  # UUID from /generate

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
