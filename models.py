"""
SQLAlchemy ORM models for Trip Master.

Four models:
  StaffUser    — internal team accounts (admin creates via CLI, no self-registration)
  Client       — CRM-style client record; staff run trips on behalf of clients
  GearProfile  — photographer's gear vault (camera body, lenses, accessories)
  Trip         — stores form params + raw suggestions so trips can be reloaded/revised

Default database: SQLite (trip_master.db).
Production: set DATABASE_URL env var to a PostgreSQL connection string and the
app will use that instead — no code changes required.
"""

import json as _json
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text,
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
    gear_profiles   = relationship('GearProfile', back_populates='staff_user',
                                   lazy='dynamic', cascade='all, delete-orphan')

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
# GearProfile
# ---------------------------------------------------------------------------

# Valid camera type values — stored as strings for DB portability.
CAMERA_TYPES = (
    'full_frame_mirrorless',
    'apsc_mirrorless',
    'apsc_dslr',
    'full_frame_dslr',
    'smartphone',
    'film_35mm',
    'film_medium_format',
)

# Valid lens category values — broad focal-range buckets shown as checkboxes in
# the gear profile form.  Stored as a JSON array of these strings in GearProfile.lenses.
LENS_CATEGORIES = (
    'Ultra-Wide Angle',   # 10–20mm — landscapes, architecture, tight interiors
    'Wide to Standard',   # 24–70mm — street photography, events, groups
    'All-in-One Zoom',    # 24–200mm+ — walk-around travel convenience
    'Telephoto Zoom',     # 70–200mm — portraits, weddings, medium-distance subjects
    'Super Telephoto',    # 200–600mm+ — wildlife, birds, field sports
    'Macro / Close-up',   # any focal length (specialised) — insects, flowers, fine detail
)

# Update comment on GearProfile.lenses column docstring to reflect category names.


class GearProfile(db):
    """A photographer's gear vault linked to a staff user account.

    A staff user may have multiple named profiles (e.g. "Travel Kit",
    "Full Studio") so the most appropriate one can be selected per shoot.

    lenses and has_filters are stored as JSON text arrays for DB portability
    (SQLite has no native array type; PostgreSQL would also work fine with
    this approach via the Text column).
    """
    __tablename__ = 'gear_profiles'

    id            = Column(Integer, primary_key=True)
    staff_user_id = Column(Integer, ForeignKey('staff_users.id'),
                           nullable=False, index=True)
    name          = Column(String(100), nullable=False)  # e.g. "Travel Kit"

    # Camera body
    camera_type   = Column(String(50), nullable=False)   # see CAMERA_TYPES above

    # Lenses: JSON array of LENS_CATEGORIES values, e.g. ["Ultra-Wide Angle", "Telephoto Zoom"]
    lenses        = Column(Text, nullable=True)

    # Accessories
    has_tripod    = Column(Boolean, nullable=False, default=False)
    # Filters: JSON array, e.g. ["6-stop ND", "polarizer", "graduated ND"]
    has_filters   = Column(Text, nullable=True)
    has_gimbal    = Column(Boolean, nullable=False, default=False)  # phone/video stabilizer

    notes         = Column(Text, nullable=True)  # free-text notes about the kit
    created_at    = Column(DateTime, nullable=False, default=_utcnow)
    updated_at    = Column(DateTime, nullable=False, default=_utcnow,
                           onupdate=_utcnow)

    # Relationships
    staff_user = relationship('StaffUser', back_populates='gear_profiles')
    trips      = relationship('Trip', back_populates='gear_profile')

    def to_dict(self):
        return {
            'id':            self.id,
            'staff_user_id': self.staff_user_id,
            'name':          self.name,
            'camera_type':   self.camera_type,
            'lenses':        _json.loads(self.lenses)      if self.lenses      else [],
            'has_tripod':    self.has_tripod,
            'has_filters':   _json.loads(self.has_filters) if self.has_filters else [],
            'has_gimbal':    self.has_gimbal,
            'notes':         self.notes,
            'created_at':    self.created_at.isoformat() if self.created_at else None,
            'updated_at':    self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<GearProfile #{self.id} {self.name!r} user={self.staff_user_id}>'


# ---------------------------------------------------------------------------
# Trip
# ---------------------------------------------------------------------------

class Trip(db):
    __tablename__ = 'trips'

    id            = Column(Integer, primary_key=True)
    client_id     = Column(Integer, ForeignKey('clients.id'), nullable=True, index=True)
    created_by_id = Column(Integer, ForeignKey('staff_users.id'), nullable=True)
    gear_profile_id = Column(Integer, ForeignKey('gear_profiles.id'), nullable=True,
                             index=True)

    title      = Column(String(255), nullable=True)   # auto-generated if blank
    status     = Column(String(20),  nullable=False, default='draft')  # 'draft' | 'finalized'
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    # ── Form parameters ──────────────────────────────────────────────────────
    location            = Column(String(255), nullable=False)

    # Duration can be supplied directly (legacy) or derived from start/end dates.
    # duration_days property (below) returns the authoritative value.
    duration            = Column(Integer,     nullable=True)   # nullable: new trips use dates
    start_date          = Column(Date,        nullable=True)   # exact shoot start date
    end_date            = Column(Date,        nullable=True)   # exact shoot end date

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

    # Relationships
    gear_profile = relationship('GearProfile', back_populates='trips')

    @property
    def duration_days(self) -> int | None:
        """Authoritative trip duration in days.

        Returns the number of days derived from start/end dates when both are
        set (inclusive of both endpoints), otherwise falls back to the legacy
        `duration` integer column. Returns None if neither is available.
        """
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            return delta.days + 1   # inclusive: 5-Mar to 7-Mar = 3 days
        return self.duration

    def to_dict(self, include_html=False):
        d = {
            'id':           self.id,
            'client_id':    self.client_id,
            'created_by_id': self.created_by_id,
            'gear_profile_id': self.gear_profile_id,
            'title':        self.title,
            'status':       self.status,
            'is_deleted':   self.is_deleted,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
            'updated_at':   self.updated_at.isoformat() if self.updated_at else None,
            # form params
            'location':             self.location,
            'duration':             self.duration_days,   # always use computed value
            'start_date':           self.start_date.isoformat() if self.start_date else None,
            'end_date':             self.end_date.isoformat()   if self.end_date   else None,
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
