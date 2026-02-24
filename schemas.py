"""
schemas.py — Pydantic v2 request/response models for Trip Master.

Replaces all manual _sanitise_line(), _clamp(), _clamp_multiline(), and
request.get_json() calls across app.py, clients.py, and trips.py.

Validation errors automatically return HTTP 422 with structured detail.
A custom exception handler in app.py maps these to {'error': '...'} to
preserve the response shape the frontend expects.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


# ── Trip generation ───────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    location:             str       = Field(..., min_length=1, max_length=100)
    duration:             int       = Field(..., ge=1, le=14)
    budget:               str       = Field(default='Moderate', max_length=150)
    distance:             str       = Field(default='Up to 30 minutes', max_length=150)
    include_photos:       bool      = True
    include_dining:       bool      = True
    include_attractions:  bool      = True
    photos_per_day:       int       = Field(default=3, ge=1, le=10)
    restaurants_per_day:  int       = Field(default=3, ge=1, le=8)
    attractions_per_day:  int       = Field(default=4, ge=1, le=10)
    photo_interests:      str | None = Field(default=None, max_length=500)
    cuisines:             str | None = Field(default=None, max_length=500)
    attraction_cats:      str | None = Field(default=None, max_length=500)
    accommodation:        str | None = Field(default=None, max_length=150)
    pre_planned:          str | None = Field(default=None, max_length=500)
    client_id:            int | None = None

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
    client_id:            int | None = None
    session_id:           str | None = None
    title:                str | None = Field(default=None, max_length=255)
    location:             str        = Field(..., min_length=1, max_length=255)
    duration:             int        = Field(..., ge=1)
    budget:               str | None = Field(default=None, max_length=50)
    distance:             str | None = Field(default=None, max_length=50)
    include_photos:       bool       = True
    include_dining:       bool       = True
    include_attractions:  bool       = True
    photos_per_day:       int        = Field(default=3, ge=1)
    restaurants_per_day:  int        = Field(default=3, ge=1)
    attractions_per_day:  int        = Field(default=4, ge=1)
    photo_interests:      str | None = Field(default=None, max_length=500)
    cuisines:             str | None = Field(default=None, max_length=500)
    attraction_cats:      str | None = Field(default=None, max_length=500)
    accommodation:        str | None = Field(default=None, max_length=500)
    raw_photos:           list       = Field(default_factory=list)
    raw_restaurants:      list       = Field(default_factory=list)
    raw_attractions:      list       = Field(default_factory=list)
    colors:               dict       = Field(default_factory=dict)


class TripUpdate(BaseModel):
    """All fields optional — supports partial update semantics."""
    status:                       Literal['draft', 'finalized'] | None = None
    title:                        str | None = Field(default=None, max_length=255)
    client_id:                    int | None = None
    approved_photo_indices:       list[int] | None = None
    approved_restaurant_indices:  list[int] | None = None
    approved_attraction_indices:  list[int] | None = None
    final_html:                   str | None = None
