"""
tool_schemas.py — Claude tool definitions for structured photo scout output.

PHOTO_TOOL accepts an *array* of locations so the same schema works for both:
  - Main scout  (N locations across all days)
  - /replace    (single location — callers take locations[0])

Using tool_choice={"type": "any"} forces Claude to call the tool, guaranteeing
structured JSON output.

Field notes:
  - lat / lng are decimal-degree numbers supplied by Claude from its geographic
    knowledge.  Server-side code constructs google_earth_url from these after
    the call; the field is NOT in the schema (Claude doesn't construct URLs).
  - distance_from_accommodation: Claude fills this when accommodation was given;
    server-side haversine overwrites it if Places verification provides _lat/_lng.
  - required_gear: items from the photographer's gear vault needed for this shot.

Usage:
    from tool_schemas import PHOTO_TOOL

    message = await anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=8000,
        tools=[PHOTO_TOOL],
        tool_choice={"type": "any"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    locations = []
    for block in message.content:
        if block.type == "tool_use" and block.name == PHOTO_TOOL["name"]:
            locations = block.input.get("locations", [])
            break
"""

# ---------------------------------------------------------------------------
# Photography locations (Kelby-style)
# ---------------------------------------------------------------------------

PHOTO_TOOL: dict = {
    "name": "submit_photo_locations",
    "description": (
        "Submit photography location recommendations in Kelby-style four-section "
        "format for the trip guide. Each location includes technical gear-specific "
        "setup instructions, concrete camera settings, and honest logistics."
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

                        "the_shot": {
                            "type": "string",
                            "description": (
                                "One sharp paragraph: what you are pointing at, why it works "
                                "at this time of year and in this light. Lead with the subject. "
                                "State what makes the location compelling right now. No filler."
                            )
                        },

                        "the_setup": {
                            "type": "string",
                            "description": (
                                "Exact shooting position, exact focal length from the "
                                "photographer's lens list, framing technique. "
                                "E.g. 'Stand at the north end of the bridge. Use the 16-35mm "
                                "at 24mm. Fill the bottom third with the wet cobblestones...'"
                            )
                        },

                        "the_settings": {
                            "type": "string",
                            "description": (
                                "Concrete starting-point camera settings calibrated to the "
                                "photographer's camera type. For digital: ISO / aperture / shutter "
                                "speed + mode. For film: stock, ASA, metering mode. "
                                "For smartphone: Pro mode values or specific modes. "
                                "One set of numbers — not a range."
                            )
                        },

                        "the_reality_check": {
                            "type": "string",
                            "description": (
                                "Honest logistics: crowds and when they thin out, sun direction "
                                "at the exact shoot time, parking/access, permit requirements, "
                                "seasonal caveats. Flag any missing gear the photographer will need."
                            )
                        },

                        "required_gear": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List ONLY items from the photographer's vault that this specific "
                                "shot genuinely requires. E.g. ['tripod', '6-stop ND', '16-35mm f/2.8']. "
                                "Empty array if only the camera body is needed."
                            )
                        },

                        "distance_from_accommodation": {
                            "type": "string",
                            "description": (
                                "Approximate walking or transit time from the accommodation, "
                                "e.g. '12 min walk' or '8 min metro'. "
                                "Write 'N/A' if no accommodation was provided."
                            )
                        },

                    },
                    "required": [
                        "day", "name", "address", "lat", "lng",
                        "shoot_window", "the_shot", "the_setup",
                        "the_settings", "the_reality_check",
                    ],
                },
            },
        },
        "required": ["locations"],
    },
}
