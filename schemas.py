"""
schemas.py — Pydantic v2 request/response models for Trip Master.

Replaces all manual _sanitise_line(), _clamp(), _clamp_multiline(), and
request.get_json() calls across app.py, clients.py, and trips.py.

Validation errors automatically return HTTP 422 with structured detail.
A custom exception handler in app.py maps these to {'error': '...'} to
preserve the response shape the frontend expects.
"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from models import CAMERA_TYPES


# ── Shared validator helpers ──────────────────────────────────────────────────

def _collapse(v: str | None) -> str | None:
    """Collapse all whitespace (tabs, newlines, multiple spaces) to a single
    space, strip ends. Returns None if the result is empty."""
    if v is None:
        return None
    s = re.sub(r'\s+', ' ', str(v)).strip()
    return s or None


def _strip_only(v: str | None) -> str | None:
    """Strip leading/trailing whitespace only — preserve internal newlines.
    Returns None if the result is empty."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email:    str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1)

    @field_validator('email', mode='before')
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return str(v).strip().lower()


# ── Gear Profile ──────────────────────────────────────────────────────────────

class GearProfileCreate(BaseModel):
    """Create a new gear profile for the authenticated staff user."""
    name:        str            = Field(..., min_length=1, max_length=100,
                                        description="Profile label, e.g. 'Travel Kit'")
    camera_type: str            = Field(..., description=f"One of: {', '.join(CAMERA_TYPES)}")
    lenses:      list[str]      = Field(default_factory=list,
                                        description="Focal lengths, e.g. ['16-35mm f/2.8', '50mm f/1.8']")
    has_tripod:  bool           = False
    has_filters: list[str]      = Field(default_factory=list,
                                        description="Filter types, e.g. ['6-stop ND', 'polarizer']")
    has_gimbal:  bool           = False
    notes:       str | None     = Field(default=None, max_length=500)

    @field_validator('camera_type')
    @classmethod
    def validate_camera_type(cls, v: str) -> str:
        if v not in CAMERA_TYPES:
            raise ValueError(f"camera_type must be one of: {', '.join(CAMERA_TYPES)}")
        return v

    @field_validator('name', mode='before')
    @classmethod
    def collapse_name(cls, v: str | None) -> str | None:
        return _collapse(v)

    @field_validator('notes', mode='before')
    @classmethod
    def strip_notes(cls, v: str | None) -> str | None:
        return _strip_only(v)

    @field_validator('lenses', 'has_filters', mode='before')
    @classmethod
    def coerce_to_list(cls, v) -> list:
        """Accept a JSON array string or a Python list."""
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else [v]
            except (json.JSONDecodeError, TypeError):
                return [v] if v.strip() else []
        return v if v is not None else []


class GearProfileUpdate(BaseModel):
    """All fields optional — supports partial update semantics."""
    name:        str | None     = Field(default=None, min_length=1, max_length=100)
    camera_type: str | None     = None
    lenses:      list[str] | None = None
    has_tripod:  bool | None    = None
    has_filters: list[str] | None = None
    has_gimbal:  bool | None    = None
    notes:       str | None     = Field(default=None, max_length=500)

    @field_validator('camera_type')
    @classmethod
    def validate_camera_type(cls, v: str | None) -> str | None:
        if v is not None and v not in CAMERA_TYPES:
            raise ValueError(f"camera_type must be one of: {', '.join(CAMERA_TYPES)}")
        return v

    @field_validator('name', mode='before')
    @classmethod
    def collapse_name(cls, v: str | None) -> str | None:
        return _collapse(v)

    @field_validator('notes', mode='before')
    @classmethod
    def strip_notes(cls, v: str | None) -> str | None:
        return _strip_only(v)

    @field_validator('lenses', 'has_filters', mode='before')
    @classmethod
    def coerce_to_list(cls, v) -> list | None:
        if v is None:
            return None
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else [v]
            except (json.JSONDecodeError, TypeError):
                return [v] if v.strip() else []
        return v


# ── Trip generation ───────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    location:             str            = Field(..., min_length=1, max_length=100)

    # Duration: supply either an integer number of days OR start_date + end_date.
    # The model_validator below enforces that exactly one form is present and
    # populates `duration` from dates when dates are provided.
    duration:             int | None     = Field(default=None, ge=1, le=14)
    start_date:           date | None    = None
    end_date:             date | None    = None

    budget:               str            = Field(default='Moderate', max_length=150)
    distance:             str            = Field(default='Up to 30 minutes', max_length=150)
    include_photos:       bool           = True
    include_dining:       bool           = True
    include_attractions:  bool           = True
    photos_per_day:       int            = Field(default=3, ge=1, le=10)
    restaurants_per_day:  int            = Field(default=3, ge=1, le=8)
    attractions_per_day:  int            = Field(default=4, ge=1, le=10)
    photo_interests:      str | None     = Field(default=None, max_length=500)
    cuisines:             str | None     = Field(default=None, max_length=500)
    attraction_cats:      str | None     = Field(default=None, max_length=500)
    accommodation:        str | None     = Field(default=None, max_length=150)
    pre_planned:          str | None     = Field(default=None, max_length=500)
    client_id:            int | None     = None
    gear_profile_id:      int | None     = None

    @field_validator('location', 'budget', 'distance', 'accommodation', mode='before')
    @classmethod
    def collapse_single_line(cls, v: str | None) -> str | None:
        return _collapse(v)

    @field_validator('pre_planned', mode='before')
    @classmethod
    def strip_multiline(cls, v: str | None) -> str | None:
        return _strip_only(v)

    @field_validator('photo_interests', 'cuisines', 'attraction_cats', mode='before')
    @classmethod
    def strip_interests(cls, v: str | None) -> str | None:
        return _strip_only(v)

    @model_validator(mode='after')
    def resolve_duration(self) -> 'GenerateRequest':
        """Ensure a usable duration is present, derived from dates if provided."""
        has_dates = self.start_date is not None and self.end_date is not None
        has_duration = self.duration is not None

        if has_dates:
            if self.end_date < self.start_date:
                raise ValueError('end_date must be on or after start_date')
            computed = (self.end_date - self.start_date).days + 1
            if computed > 14:
                raise ValueError('Trip duration derived from dates cannot exceed 14 days')
            # Dates take precedence; overwrite any supplied duration
            self.duration = computed
        elif not has_duration:
            raise ValueError(
                'Provide either duration (integer days) or both start_date and end_date'
            )
        return self

    @model_validator(mode='after')
    def at_least_one_section(self) -> 'GenerateRequest':
        if not (self.include_photos or self.include_dining or self.include_attractions):
            raise ValueError('At least one section must be enabled')
        return self


class FinalizeRequest(BaseModel):
    session_id:           str
    trip_id:              int | None = None
    approved_photos:      list[int] | None = None
    approved_restaurants: list[int] | None = None
    approved_attractions: list[int] | None = None


class ReplaceRequest(BaseModel):
    session_id:    str
    trip_id:       int | None = None
    type:          Literal['photos', 'restaurants', 'attractions']
    index:         int = Field(..., ge=0)
    day:           int = Field(..., ge=1)
    meal_type:     Literal['breakfast', 'lunch', 'dinner'] | None = None
    exclude_names: list[str] = Field(default_factory=list)


# ── Clients ───────────────────────────────────────────────────────────────────

class ClientCreate(BaseModel):
    name:                 str       = Field(..., min_length=1, max_length=200)
    email:                str | None = Field(default=None, max_length=150)
    phone:                str | None = Field(default=None, max_length=150)
    company:              str | None = Field(default=None, max_length=150)
    home_city:            str | None = Field(default=None, max_length=150)
    preferred_budget:     str | None = Field(default=None, max_length=150)
    travel_style:         str | None = Field(default=None, max_length=150)
    dietary_requirements: str | None = Field(default=None, max_length=150)
    notes:                str | None = Field(default=None, max_length=500)
    tags:                 str | None = Field(default=None, max_length=150)

    @field_validator('name', 'email', 'phone', 'company', 'home_city',
                     'preferred_budget', 'travel_style', 'dietary_requirements',
                     'tags', mode='before')
    @classmethod
    def collapse_single_line(cls, v: str | None) -> str | None:
        return _collapse(v)

    @field_validator('notes', mode='before')
    @classmethod
    def strip_notes(cls, v: str | None) -> str | None:
        return _strip_only(v)


class ClientUpdate(BaseModel):
    """All fields optional — supports partial update semantics."""
    name:                 str | None = Field(default=None, min_length=1, max_length=200)
    email:                str | None = Field(default=None, max_length=150)
    phone:                str | None = Field(default=None, max_length=150)
    company:              str | None = Field(default=None, max_length=150)
    home_city:            str | None = Field(default=None, max_length=150)
    preferred_budget:     str | None = Field(default=None, max_length=150)
    travel_style:         str | None = Field(default=None, max_length=150)
    dietary_requirements: str | None = Field(default=None, max_length=150)
    notes:                str | None = Field(default=None, max_length=500)
    tags:                 str | None = Field(default=None, max_length=150)

    @field_validator('name', 'email', 'phone', 'company', 'home_city',
                     'preferred_budget', 'travel_style', 'dietary_requirements',
                     'tags', mode='before')
    @classmethod
    def collapse_single_line(cls, v: str | None) -> str | None:
        return _collapse(v)

    @field_validator('notes', mode='before')
    @classmethod
    def strip_notes(cls, v: str | None) -> str | None:
        return _strip_only(v)


# ── Trips ─────────────────────────────────────────────────────────────────────

class TripCreate(BaseModel):
    client_id:            int | None  = None
    gear_profile_id:      int | None  = None
    session_id:           str | None  = None
    title:                str | None  = Field(default=None, max_length=255)
    location:             str         = Field(..., min_length=1, max_length=255)
    duration:             int | None  = Field(default=None, ge=1)
    start_date:           date | None = None
    end_date:             date | None = None
    budget:               str | None  = Field(default=None, max_length=50)
    distance:             str | None  = Field(default=None, max_length=50)
    include_photos:       bool        = True
    include_dining:       bool        = True
    include_attractions:  bool        = True
    photos_per_day:       int         = Field(default=3, ge=1)
    restaurants_per_day:  int         = Field(default=3, ge=1)
    attractions_per_day:  int         = Field(default=4, ge=1)
    photo_interests:      str | None  = Field(default=None, max_length=500)
    cuisines:             str | None  = Field(default=None, max_length=500)
    attraction_cats:      str | None  = Field(default=None, max_length=500)
    accommodation:        str | None  = Field(default=None, max_length=500)
    raw_photos:           list        = Field(default_factory=list)
    raw_restaurants:      list        = Field(default_factory=list)
    raw_attractions:      list        = Field(default_factory=list)
    colors:               dict        = Field(default_factory=dict)


class TripUpdate(BaseModel):
    """All fields optional — supports partial update semantics."""
    status:                       Literal['draft', 'finalized'] | None = None
    title:                        str | None = Field(default=None, max_length=255)
    client_id:                    int | None = None
    gear_profile_id:              int | None = None
    approved_photo_indices:       list[int] | None = None
    approved_restaurant_indices:  list[int] | None = None
    approved_attraction_indices:  list[int] | None = None
    final_html:                   str | None = None
