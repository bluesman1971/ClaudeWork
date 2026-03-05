"""
tool_schemas.py — Claude tool definitions for structured scout output.

Each tool accepts an *array* of items so the same schema works for both:
  - Main scouts  (N items across all days)
  - /replace     (single item — callers take items[0])

Using tool_choice={"type": "any"} forces Claude to call the tool, guaranteeing
structured JSON output and eliminating the markdown-fence stripping and
_parse_json_lines fallback that the old text-completion approach required.

Usage in a scout:
    from tool_schemas import PHOTO_TOOL

    message = await anthropic_client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=6000,
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
# Photo locations
# ---------------------------------------------------------------------------

PHOTO_TOOL: dict = {
    "name": "submit_photo_locations",
    "description": "Submit photography location recommendations for the trip guide.",
    "input_schema": {
        "type": "object",
        "properties": {
            "locations": {
                "type": "array",
                "description": "Ordered list of photography locations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "day": {
                            "type": "integer",
                            "description": "Day number (1-based)."
                        },
                        "time": {
                            "type": "string",
                            "description": "Best time range, e.g. '6:30-7:30am'."
                        },
                        "name": {
                            "type": "string",
                            "description": "Exact location name."
                        },
                        "address": {
                            "type": "string",
                            "description": "Full street address or neighbourhood."
                        },
                        "coordinates": {
                            "type": "string",
                            "description": "Latitude, longitude or area description."
                        },
                        "travel_time": {
                            "type": "string",
                            "description": (
                                "Approx travel time from accommodation, e.g. '8 min walk' "
                                "or '12 min metro'. Write 'N/A' if no accommodation was given."
                            )
                        },
                        "subject": {
                            "type": "string",
                            "description": (
                                "1-2 sentences: what you are pointing the camera at and why "
                                "it works for this client's interests. Name the specific subject."
                            )
                        },
                        "setup": {
                            "type": "string",
                            "description": (
                                "2-3 sentences: where to stand, focal length, aperture if "
                                "relevant, framing technique. Practical instructions a "
                                "photographer can act on immediately."
                            )
                        },
                        "light": {
                            "type": "string",
                            "description": (
                                "2 sentences: light direction, best window, what changes "
                                "after that window closes. Facts, not poetry."
                            )
                        },
                        "pro_tip": {
                            "type": "string",
                            "description": (
                                "1-2 sentences: one honest, actionable tip — crowd timing, "
                                "a less-obvious angle, a technical setting, or a seasonal caveat. "
                                "Personalise to the client if possible."
                            )
                        },
                    },
                    "required": [
                        "day", "time", "name", "address",
                        "subject", "setup", "light", "pro_tip",
                    ],
                },
            },
        },
        "required": ["locations"],
    },
}


# ---------------------------------------------------------------------------
# Restaurants
# ---------------------------------------------------------------------------

RESTAURANT_TOOL: dict = {
    "name": "submit_restaurants",
    "description": "Submit restaurant recommendations for the trip guide.",
    "input_schema": {
        "type": "object",
        "properties": {
            "restaurants": {
                "type": "array",
                "description": "Ordered list of restaurant recommendations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "day": {
                            "type": "integer",
                            "description": "Day number (1-based)."
                        },
                        "meal_type": {
                            "type": "string",
                            "enum": ["breakfast", "lunch", "dinner"],
                            "description": "Meal slot."
                        },
                        "name": {
                            "type": "string",
                            "description": "Restaurant name."
                        },
                        "address": {
                            "type": "string",
                            "description": "Full street address."
                        },
                        "location": {
                            "type": "string",
                            "description": "Neighbourhood."
                        },
                        "cuisine": {
                            "type": "string",
                            "description": "Cuisine type."
                        },
                        "travel_time": {
                            "type": "string",
                            "description": (
                                "Approx travel time from accommodation, e.g. '5 min walk' "
                                "or '10 min taxi'. Write 'N/A' if no accommodation was given."
                            )
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "2 sentences: what the place is and what to order. "
                                "Specific — name the dish."
                            )
                        },
                        "price": {
                            "type": "string",
                            "description": (
                                "Price tier: $ (budget/street food), $$ (moderate), "
                                "$$$ (moderately expensive), $$$$ (fine dining/splurge)."
                            )
                        },
                        "signature_dish": {
                            "type": "string",
                            "description": "The one dish most worth ordering."
                        },
                        "ambiance": {
                            "type": "string",
                            "description": (
                                "1 sentence: what you find when you walk in — "
                                "noise level, seating, clientele, formality."
                            )
                        },
                        "hours": {
                            "type": "string",
                            "description": "Hours of operation."
                        },
                        "why_this_client": {
                            "type": "string",
                            "description": (
                                "1 sentence: specifically why this pick suits this client's "
                                "profile. If no profile was given, write why it suits the "
                                "stated cuisine/budget preferences."
                            )
                        },
                        "insider_tip": {
                            "type": "string",
                            "description": (
                                "1-2 sentences: reservation advice, best seat, timing, "
                                "or one thing most visitors miss."
                            )
                        },
                    },
                    "required": [
                        "day", "meal_type", "name", "address", "cuisine",
                        "description", "price", "signature_dish", "ambiance",
                    ],
                },
            },
        },
        "required": ["restaurants"],
    },
}


# ---------------------------------------------------------------------------
# Attractions
# ---------------------------------------------------------------------------

ATTRACTION_TOOL: dict = {
    "name": "submit_attractions",
    "description": "Submit attraction recommendations for the trip guide.",
    "input_schema": {
        "type": "object",
        "properties": {
            "attractions": {
                "type": "array",
                "description": "Ordered list of attraction recommendations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "day": {
                            "type": "integer",
                            "description": "Day number (1-based)."
                        },
                        "time": {
                            "type": "string",
                            "description": "Time slot, e.g. '9:00-11:00am'."
                        },
                        "name": {
                            "type": "string",
                            "description": "Attraction name."
                        },
                        "address": {
                            "type": "string",
                            "description": "Full street address."
                        },
                        "category": {
                            "type": "string",
                            "description": (
                                "Type: museum / market / viewpoint / park / "
                                "historic site / neighbourhood / etc."
                            )
                        },
                        "location": {
                            "type": "string",
                            "description": "Neighbourhood."
                        },
                        "travel_time": {
                            "type": "string",
                            "description": (
                                "Approx travel time from accommodation, e.g. "
                                "'15 min metro' or '6 min walk'. Write 'N/A' if "
                                "no accommodation was given."
                            )
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "2 sentences: what it is and the one thing that makes it "
                                "worth this client's time. Honest — include any caveat."
                            )
                        },
                        "admission": {
                            "type": "string",
                            "description": "Free / price range."
                        },
                        "hours": {
                            "type": "string",
                            "description": "Opening hours."
                        },
                        "duration": {
                            "type": "string",
                            "description": "Realistic visit length."
                        },
                        "best_time": {
                            "type": "string",
                            "description": (
                                "Specific time advice: e.g. 'Weekday mornings before 10am' "
                                "or 'Late afternoon when tour groups leave'."
                            )
                        },
                        "why_this_client": {
                            "type": "string",
                            "description": (
                                "1 sentence: specifically why this attraction suits this "
                                "client's profile or interests."
                            )
                        },
                        "highlight": {
                            "type": "string",
                            "description": (
                                "The single best thing — be specific, not generic."
                            )
                        },
                        "insider_tip": {
                            "type": "string",
                            "description": (
                                "1-2 sentences: one piece of practical advice most "
                                "visitors don't know."
                            )
                        },
                    },
                    "required": [
                        "day", "time", "name", "address", "category",
                        "description", "admission", "hours", "duration",
                        "best_time", "highlight",
                    ],
                },
            },
        },
        "required": ["attractions"],
    },
}
