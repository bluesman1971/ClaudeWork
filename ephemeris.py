"""
ephemeris.py — Sunrise, sunset, golden hour, blue hour, and moon data.

Uses the `astral` library (3.x) to compute all times from GPS coordinates + dates.
All returned datetimes are UTC-aware; the prompt instructs Claude to interpret
them in the local timezone for the destination.

Key public API:
    get_daily_ephemeris(lat, lng, dates)  → list of dicts, one per date
    format_ephemeris_block(ephemeris_data) → plain-text string for prompt injection
"""

import math
from datetime import date, timedelta

from astral import Observer
from astral.moon import phase as _moon_phase_days
from astral.sun import sun as _sun


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _moon_illumination(phase_days: float) -> float:
    """Approximate illumination fraction (0.0–1.0) from days since new moon.

    Uses a cosine model: illumination = (1 - cos(2π·p/29.5)) / 2
    where p is days since new moon (0 = new, ~14.77 = full).
    """
    return (1 - math.cos(2 * math.pi * phase_days / 29.5)) / 2


def _phase_name(phase_days: float) -> str:
    """Return a human-readable moon phase name from days since the last new moon."""
    p = phase_days % 29.5
    if p < 1.85:   return 'New Moon'
    if p < 7.38:   return 'Waxing Crescent'
    if p < 9.22:   return 'First Quarter'
    if p < 14.77:  return 'Waxing Gibbous'
    if p < 16.61:  return 'Full Moon'
    if p < 22.15:  return 'Waning Gibbous'
    if p < 23.99:  return 'Last Quarter'
    return 'Waning Crescent'


def _fmt_utc(dt) -> str:
    """Format a UTC-aware datetime as 'HH:MM UTC', or 'N/A' if None."""
    if dt is None:
        return 'N/A'
    return dt.strftime('%H:%M UTC')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_daily_ephemeris(lat: float, lng: float, dates: list) -> list:
    """Return per-day ephemeris data for the given GPS coordinates.

    Args:
        lat:   Latitude in decimal degrees.
        lng:   Longitude in decimal degrees.
        dates: List of datetime.date objects (e.g. one per trip day).

    Returns:
        List of dicts, one per date, with keys:
            date                        ISO date string
            sunrise                     UTC-aware datetime (or None: polar night/day)
            sunset                      UTC-aware datetime (or None)
            golden_hour_morning_start   = sunrise
            golden_hour_morning_end     = sunrise + 1 h
            golden_hour_evening_start   = sunset  - 1 h
            golden_hour_evening_end     = sunset
            blue_hour_morning_start     = civil dawn  (6° below horizon)
            blue_hour_morning_end       = sunrise
            blue_hour_evening_start     = sunset
            blue_hour_evening_end       = civil dusk  (6° below horizon)
            moon_phase_days             float 0–29.5
            moon_phase_name             str
            moon_illumination           float 0.0–1.0
    """
    observer = Observer(latitude=lat, longitude=lng)
    results  = []

    for d in dates:
        row = {'date': d.isoformat()}

        # ── Sun events ────────────────────────────────────────────────────────
        try:
            s = _sun(observer, date=d)
            row['sunrise'] = s['sunrise']
            row['sunset']  = s['sunset']

            # Golden hour: 60 minutes either side of sunrise/sunset
            row['golden_hour_morning_start'] = s['sunrise']
            row['golden_hour_morning_end']   = s['sunrise'] + timedelta(hours=1)
            row['golden_hour_evening_start'] = s['sunset']  - timedelta(hours=1)
            row['golden_hour_evening_end']   = s['sunset']

            # Blue hour: civil twilight window (dawn ↔ sunrise, sunset ↔ dusk)
            row['blue_hour_morning_start'] = s['dawn']
            row['blue_hour_morning_end']   = s['sunrise']
            row['blue_hour_evening_start'] = s['sunset']
            row['blue_hour_evening_end']   = s['dusk']

        except Exception:
            # Polar night, midnight sun, or calculation error — fill with None
            for key in (
                'sunrise', 'sunset',
                'golden_hour_morning_start', 'golden_hour_morning_end',
                'golden_hour_evening_start', 'golden_hour_evening_end',
                'blue_hour_morning_start',   'blue_hour_morning_end',
                'blue_hour_evening_start',   'blue_hour_evening_end',
            ):
                row[key] = None

        # ── Moon ─────────────────────────────────────────────────────────────
        try:
            p = float(_moon_phase_days(d))
        except Exception:
            p = 0.0

        row['moon_phase_days']   = round(p, 1)
        row['moon_phase_name']   = _phase_name(p)
        row['moon_illumination'] = round(_moon_illumination(p), 2)

        results.append(row)

    return results


def format_ephemeris_block(ephemeris_data: list) -> str:
    """Format ephemeris data as a plain-text block for prompt injection.

    Times are shown as HH:MM UTC.  The system prompt instructs Claude to convert
    these to local destination time when setting shoot_window values.

    Returns an empty string if ephemeris_data is empty or None.
    """
    if not ephemeris_data:
        return ''

    lines = ['Ephemeris data (all times UTC — convert to local destination time):']
    for day_idx, row in enumerate(ephemeris_data, 1):
        lines.append(f'\nDay {day_idx} — {row["date"]}')
        lines.append(f'  Sunrise:             {_fmt_utc(row.get("sunrise"))}')
        lines.append(f'  Sunset:              {_fmt_utc(row.get("sunset"))}')
        lines.append(
            f'  Golden hour AM:      {_fmt_utc(row.get("golden_hour_morning_start"))} '
            f'– {_fmt_utc(row.get("golden_hour_morning_end"))}'
        )
        lines.append(
            f'  Golden hour PM:      {_fmt_utc(row.get("golden_hour_evening_start"))} '
            f'– {_fmt_utc(row.get("golden_hour_evening_end"))}'
        )
        lines.append(
            f'  Blue hour AM:        {_fmt_utc(row.get("blue_hour_morning_start"))} '
            f'– {_fmt_utc(row.get("blue_hour_morning_end"))}'
        )
        lines.append(
            f'  Blue hour PM:        {_fmt_utc(row.get("blue_hour_evening_start"))} '
            f'– {_fmt_utc(row.get("blue_hour_evening_end"))}'
        )
        lines.append(
            f'  Moon:                {row["moon_phase_name"]} '
            f'({int(row["moon_illumination"] * 100)}% illuminated)'
        )

    return '\n'.join(lines)
