"""
tool_schemas.py — Claude tool definitions for structured photo scout output.

Three tools, three purposes:

  LOCATION_TOOL  Phase 1 — geography only; one call per trip day, run in parallel.
                 Finds photogenic locations: name, address, coordinates, distance.
                 No shot details — those come in Phase 2.

  SHOT_TOOL      Phase 2 — creative / technical detail; one call per location, parallel.
                 Generates shoot window, 1–3 Kelby-style shots, reality check, gear list.
                 Emits the plan directly (not wrapped in a locations array).

  PHOTO_TOOL     Legacy — used by the /replace endpoint (single location swap).
                 Accepts a locations array so one schema covers both one and many items.

Using tool_choice={"type": "any"} forces Claude to call the tool, guaranteeing
structured JSON output.

Field notes:
  - lat / lng are decimal-degree numbers supplied by Claude from its geographic
    knowledge.  Server-side code constructs google_earth_url from these after
    the call; the field is NOT in the schema (Claude doesn't construct URLs).
  - distance_from_accommodation: Claude fills this when accommodation was given;
    server-side haversine overwrites it if Places verification provides _lat/_lng.
  - required_gear: items from the photographer's gear vault needed for this shot.
"""

# ---------------------------------------------------------------------------
# Phase 1 — Location Discovery (geography only, one call per trip day)
# ---------------------------------------------------------------------------

LOCATION_TOOL: dict = {
    "name": "submit_locations",
    "description": (
        "Submit photography location discoveries for a single trip day. "
        "Each entry is a real, accessible, photogenic place with accurate coordinates. "
        "Do NOT include shot details — those are planned separately."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "locations": {
                "type": "array",
                "description": "Photography locations for this day, ordered by shoot priority.",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "day": {
                            "type": "integer",
                            "description": "Day number (1-based) this location is planned for.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Exact location name — specific enough to find on a map.",
                        },
                        "address": {
                            "type": "string",
                            "description": (
                                "Full street address or the most precise location description "
                                "available (neighbourhood + landmark if no street address exists)."
                            ),
                        },
                        "lat": {
                            "type": "number",
                            "description": "Latitude in decimal degrees (e.g. 41.3851).",
                        },
                        "lng": {
                            "type": "number",
                            "description": "Longitude in decimal degrees (e.g. 2.1734).",
                        },
                        "distance_from_accommodation": {
                            "type": "string",
                            "description": (
                                "Approximate walking or transit time from the starting point, "
                                "e.g. '12 min walk' or '8 min metro'. "
                                "Write 'N/A' if no starting point was provided."
                            ),
                        },
                    },
                    "required": ["day", "name", "address", "lat", "lng",
                                 "distance_from_accommodation"],
                },
            },
        },
        "required": ["locations"],
    },
}


# ---------------------------------------------------------------------------
# Phase 2 — Shot Planning (creative/technical detail, one call per location)
# ---------------------------------------------------------------------------

SHOT_TOOL: dict = {
    "name": "submit_shot_plan",
    "description": (
        "Submit a Kelby-style shot plan for a single photography location. "
        "Includes the recommended shoot window, 1–3 distinct shots with full technical "
        "detail, honest logistics, and a gear list. "
        "All fields are at the location level — not nested inside a locations array."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "shoot_window": {
                "type": "string",
                "description": (
                    "Recommended shoot time range in local destination time, "
                    "e.g. '5:45–7:00 AM (Day 2 — golden hour)' or '6:30–8:00 PM (blue hour)'. "
                    "Derived from the ephemeris data provided in the prompt."
                ),
            },
            "shots": {
                "type": "array",
                "description": (
                    "1–3 distinct shooting approaches for this location. "
                    "Each must be achievable with the photographer's actual gear vault. "
                    "Each must be a genuinely different creative take — different lens "
                    "category, vantage point, subject element, or light condition. "
                    "Do NOT repeat the same shot with different wording."
                ),
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": ["title", "the_shot", "the_setup", "the_settings"],
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": (
                                "Short label for this approach, "
                                "e.g. 'Full facade at golden hour' or 'Tower detail — telephoto'."
                            ),
                        },
                        "the_shot": {
                            "type": "string",
                            "description": (
                                "One sharp paragraph: what you are pointing at, why it works "
                                "at this time of year and in this light. Lead with the subject. "
                                "State what makes this specific angle compelling. No filler."
                            ),
                        },
                        "the_setup": {
                            "type": "string",
                            "description": (
                                "Exact shooting position, lens category and focal range from the "
                                "photographer's vault, framing technique. "
                                "E.g. 'Stand at the north end of the bridge. Use the Wide to "
                                "Standard (24–70mm) zoom at 24mm. Fill the bottom third with "
                                "the wet cobblestones...'"
                            ),
                        },
                        "the_settings": {
                            "type": "string",
                            "description": (
                                "Concrete starting-point camera settings calibrated to the "
                                "photographer's camera type and this specific shot. "
                                "For digital: ISO / aperture / shutter speed + mode. "
                                "For film: stock, ASA, metering mode. "
                                "For smartphone: Pro mode values or specific modes. "
                                "One set of numbers — not a range."
                            ),
                        },
                    },
                },
            },
            "the_reality_check": {
                "type": "string",
                "description": (
                    "Honest logistics shared across all shots: crowds and when they thin out, "
                    "sun direction at the exact shoot time, parking/access, permit requirements, "
                    "seasonal caveats. Flag any missing gear for any of the shots above."
                ),
            },
            "required_gear": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List ONLY items from the photographer's vault that any shot genuinely "
                    "requires. Use category names (e.g. 'Telephoto Zoom', 'tripod', '6-stop ND'). "
                    "Empty array if only the camera body is needed."
                ),
            },
        },
        "required": ["shoot_window", "shots", "the_reality_check"],
    },
}


# ---------------------------------------------------------------------------
# Legacy — /replace endpoint (single location swap, kept intact)
# ---------------------------------------------------------------------------

PHOTO_TOOL: dict = {
    "name": "submit_photo_locations",
    "description": (
        "Submit photography location recommendations for the trip guide. "
        "Each location contains 2–3 distinct shooting approaches (the 'shots' array), "
        "each calibrated to the photographer's gear vault. "
        "Shared logistics (reality check, shoot window, required gear) are at location level."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "locations": {
                "type": "array",
                "description": "Ordered list of photography locations, one per planned shoot.",
                "items": {
                    "type": "object",
                    "properties": {

                        "day": {
                            "type": "integer",
                            "description": "Day number (1-based) this shoot is planned for."
                        },

                        "name": {
                            "type": "string",
                            "description": "Exact location name — specific enough to find on a map."
                        },

                        "address": {
                            "type": "string",
                            "description": (
                                "Full street address or the most precise location description "
                                "available (neighbourhood + landmark if no street address exists)."
                            )
                        },

                        "lat": {
                            "type": "number",
                            "description": "Latitude in decimal degrees (e.g. 41.3851)."
                        },

                        "lng": {
                            "type": "number",
                            "description": "Longitude in decimal degrees (e.g. 2.1734)."
                        },

                        "shoot_window": {
                            "type": "string",
                            "description": (
                                "Recommended shoot time range in local destination time, "
                                "e.g. '5:45–7:00 AM (Day 2 — golden hour)' or "
                                "'6:30–8:00 PM (blue hour)'. "
                                "Derived from the ephemeris data provided in the prompt."
                            )
                        },

                        "shots": {
                            "type": "array",
                            "description": (
                                "2–3 distinct shooting approaches for this location. "
                                "Each must be achievable with the photographer's actual gear vault. "
                                "Each must be a genuinely different creative take — different lens "
                                "category, vantage point, subject element, or light condition. "
                                "Do NOT repeat the same shot with different wording. "
                                "Minimum 1, maximum 3."
                            ),
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {
                                "type": "object",
                                "required": ["title", "the_shot", "the_setup", "the_settings"],
                                "properties": {
                                    "title": {
                                        "type": "string",
                                        "description": (
                                            "Short label for this shooting approach, "
                                            "e.g. 'Full facade at golden hour' or 'Tower detail — telephoto'."
                                        )
                                    },
                                    "the_shot": {
                                        "type": "string",
                                        "description": (
                                            "One sharp paragraph: what you are pointing at, why it works "
                                            "at this time of year and in this light. Lead with the subject. "
                                            "State what makes this specific angle compelling. No filler."
                                        )
                                    },
                                    "the_setup": {
                                        "type": "string",
                                        "description": (
                                            "Exact shooting position, lens category and focal range from the "
                                            "photographer's vault, framing technique. "
                                            "E.g. 'Stand at the north end of the bridge. Use the Wide to "
                                            "Standard (24–70mm) zoom at 24mm. Fill the bottom third with "
                                            "the wet cobblestones...'"
                                        )
                                    },
                                    "the_settings": {
                                        "type": "string",
                                        "description": (
                                            "Concrete starting-point camera settings calibrated to the "
                                            "photographer's camera type and this specific shot. "
                                            "For digital: ISO / aperture / shutter speed + mode. "
                                            "For film: stock, ASA, metering mode. "
                                            "For smartphone: Pro mode values or specific modes. "
                                            "One set of numbers — not a range."
                                        )
                                    },
                                }
                            }
                        },

                        "the_reality_check": {
                            "type": "string",
                            "description": (
                                "Shared logistics for this location: crowds and when they thin out, "
                                "sun direction at the exact shoot time, parking/access, permit "
                                "requirements, seasonal caveats. Flag any missing gear the "
                                "photographer will need for any of the shots above."
                            )
                        },

                        "required_gear": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List ONLY items from the photographer's vault that any of the shots "
                                "genuinely require. E.g. ['tripod', '6-stop ND']. "
                                "Use lens category names, not focal lengths. "
                                "Empty array if only the camera body is needed."
                            )
                        },

                        "distance_from_accommodation": {
                            "type": "string",
                            "description": (
                                "Approximate walking or transit time from the starting point, "
                                "e.g. '12 min walk' or '8 min metro'. "
                                "Write 'N/A' if no starting point was provided."
                            )
                        },

                    },
                    "required": [
                        "day", "name", "address", "lat", "lng",
                        "shoot_window", "shots", "the_reality_check",
                    ],
                },
            },
        },
        "required": ["locations"],
    },
}
