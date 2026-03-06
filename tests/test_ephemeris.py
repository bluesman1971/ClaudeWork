"""
tests/test_ephemeris.py — Pure unit tests for the ephemeris module.

No HTTP calls, no DB, no fixtures required.
All times are UTC-aware datetimes returned by astral.

Covers:
  • Barcelona (41.38°N, 2.16°E) on 2024-06-21 (summer solstice)
      – sunrise between 03:00–06:00 UTC
      – sunset  between 18:00–22:00 UTC
      – golden_hour_morning_end = golden_hour_morning_start + 1 h
      – golden_hour_evening_start = sunset - 1 h
  • London (51.5°N, -0.12°E) on 2024-03-20 (spring equinox)
      – sunrise between 05:00–07:00 UTC
      – sunset  between 17:00–19:00 UTC
  • Moon phase helpers
      – moon_phase_name in the 8 known phase names
      – moon_illumination in [0.0, 1.0]
  • format_ephemeris_block
      – empty input → empty string
      – single-day input → contains the date string
      – output contains 'Sunrise' and 'Sunset' labels
"""
from datetime import date, timedelta, timezone

import pytest

from ephemeris import format_ephemeris_block, get_daily_ephemeris

# ---------------------------------------------------------------------------
# Known phase names (matches _phase_name() in ephemeris.py)
# ---------------------------------------------------------------------------

KNOWN_PHASE_NAMES = {
    "New Moon",
    "Waxing Crescent",
    "First Quarter",
    "Waxing Gibbous",
    "Full Moon",
    "Waning Gibbous",
    "Last Quarter",
    "Waning Crescent",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_hour(dt) -> float:
    """Return the UTC hour (float) of a timezone-aware datetime."""
    assert dt is not None, "datetime must not be None"
    assert dt.tzinfo is not None, "datetime must be timezone-aware"
    utc = dt.astimezone(timezone.utc)
    return utc.hour + utc.minute / 60.0


# ---------------------------------------------------------------------------
# Barcelona — summer solstice
# ---------------------------------------------------------------------------


class TestBarcelonaSummerSolstice:
    LAT = 41.38
    LNG = 2.16
    DATE = date(2024, 6, 21)

    @pytest.fixture(scope="class")
    def data(self):
        return get_daily_ephemeris(self.LAT, self.LNG, [self.DATE])

    @pytest.fixture(scope="class")
    def day(self, data):
        assert len(data) == 1
        return data[0]

    def test_date_key(self, day):
        assert day["date"] == "2024-06-21"

    def test_sunrise_range(self, day):
        hour = _utc_hour(day["sunrise"])
        assert 3.0 <= hour <= 6.0, f"Sunrise at {hour:.2f} UTC not in expected window"

    def test_sunset_range(self, day):
        hour = _utc_hour(day["sunset"])
        assert 18.0 <= hour <= 22.0, f"Sunset at {hour:.2f} UTC not in expected window"

    def test_golden_hour_morning_duration(self, day):
        start = day["golden_hour_morning_start"]
        end = day["golden_hour_morning_end"]
        diff = (end - start).total_seconds()
        assert abs(diff - 3600) < 10, "Golden hour morning must be exactly 1 h"

    def test_golden_hour_evening_start_is_sunset_minus_1h(self, day):
        sunset = day["sunset"]
        gh_start = day["golden_hour_evening_start"]
        diff = (sunset - gh_start).total_seconds()
        assert abs(diff - 3600) < 10, "Golden hour evening must start 1 h before sunset"

    def test_blue_hour_morning_start_before_sunrise(self, day):
        """Blue hour starts at civil dawn, which is before sunrise."""
        assert day["blue_hour_morning_start"] < day["sunrise"]

    def test_blue_hour_evening_end_after_sunset(self, day):
        """Blue hour ends at civil dusk, which is after sunset."""
        assert day["blue_hour_evening_end"] > day["sunset"]

    def test_moon_illumination_range(self, day):
        ill = day["moon_illumination"]
        assert 0.0 <= ill <= 1.0, f"moon_illumination {ill} out of range"

    def test_moon_phase_name_known(self, day):
        assert day["moon_phase_name"] in KNOWN_PHASE_NAMES

    def test_moon_phase_days_range(self, day):
        p = day["moon_phase_days"]
        assert 0.0 <= p <= 29.5, f"moon_phase_days {p} out of range"


# ---------------------------------------------------------------------------
# London — spring equinox
# ---------------------------------------------------------------------------


class TestLondonSpringEquinox:
    LAT = 51.5
    LNG = -0.12
    DATE = date(2024, 3, 20)

    @pytest.fixture(scope="class")
    def day(self):
        data = get_daily_ephemeris(self.LAT, self.LNG, [self.DATE])
        assert len(data) == 1
        return data[0]

    def test_date_key(self, day):
        assert day["date"] == "2024-03-20"

    def test_sunrise_near_equinox(self, day):
        """At the equinox, sunrise should be close to 06:00 UTC ± 60 min."""
        hour = _utc_hour(day["sunrise"])
        assert 5.0 <= hour <= 7.0, f"Sunrise at {hour:.2f} UTC not near 06:00"

    def test_sunset_near_equinox(self, day):
        """At the equinox, sunset should be close to 18:00 UTC ± 60 min."""
        hour = _utc_hour(day["sunset"])
        assert 17.0 <= hour <= 19.0, f"Sunset at {hour:.2f} UTC not near 18:00"

    def test_moon_illumination_range(self, day):
        assert 0.0 <= day["moon_illumination"] <= 1.0

    def test_moon_phase_name_known(self, day):
        assert day["moon_phase_name"] in KNOWN_PHASE_NAMES


# ---------------------------------------------------------------------------
# Multiple dates
# ---------------------------------------------------------------------------


def test_multiple_dates_length():
    dates = [date(2024, 7, 1) + timedelta(days=i) for i in range(5)]
    data = get_daily_ephemeris(41.38, 2.16, dates)
    assert len(data) == 5


def test_multiple_dates_ordered():
    dates = [date(2024, 7, 1) + timedelta(days=i) for i in range(3)]
    data = get_daily_ephemeris(41.38, 2.16, dates)
    for i, row in enumerate(data):
        assert row["date"] == dates[i].isoformat()


# ---------------------------------------------------------------------------
# format_ephemeris_block
# ---------------------------------------------------------------------------


def test_format_ephemeris_block_empty_input():
    result = format_ephemeris_block([])
    assert result == "", f"Expected empty string, got: {result!r}"


def test_format_ephemeris_block_none_input():
    result = format_ephemeris_block(None)
    assert result == ""


def test_format_ephemeris_block_contains_date():
    data = get_daily_ephemeris(41.38, 2.16, [date(2024, 6, 21)])
    block = format_ephemeris_block(data)
    assert "2024-06-21" in block


def test_format_ephemeris_block_contains_labels():
    data = get_daily_ephemeris(41.38, 2.16, [date(2024, 6, 21)])
    block = format_ephemeris_block(data)
    assert "Sunrise" in block
    assert "Sunset" in block
    assert "Golden hour" in block


def test_format_ephemeris_block_multi_day_labels():
    dates = [date(2024, 6, 21) + timedelta(days=i) for i in range(3)]
    data = get_daily_ephemeris(41.38, 2.16, dates)
    block = format_ephemeris_block(data)
    assert "Day 1" in block
    assert "Day 2" in block
    assert "Day 3" in block
