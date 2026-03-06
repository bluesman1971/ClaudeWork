"""
prompts.py — All Claude prompt strings for Trip Master (Photography Pivot).

Every prompt constant and builder function lives here.
app.py must not contain hardcoded prompt strings.

Public API:
    build_photo_scout_system_prompt(gear_profile)          → str
    build_photo_scout_user_prompt(...)                     → str
    build_photo_replace_system_prompt()                    → str
    build_photo_replace_user_prompt(...)                   → str
"""

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Focal range hint for each lens category — shown in the gear vault block so
# Claude understands the actual glass available without exact focal lengths.
_LENS_RANGES: dict = {
    'Ultra-Wide Angle': '10–20mm',
    'Wide to Standard': '24–70mm',
    'All-in-One Zoom':  '24–200mm+',
    'Telephoto Zoom':   '70–200mm',
    'Super Telephoto':  '200–600mm+',
    'Macro / Close-up': 'macro / close-up',
}

_CAMERA_LABELS: dict = {
    'full_frame_mirrorless': 'Full-frame mirrorless',
    'apsc_mirrorless':       'APS-C mirrorless',
    'apsc_dslr':             'APS-C DSLR',
    'full_frame_dslr':       'Full-frame DSLR',
    'smartphone':            'Smartphone',
    'film_35mm':             '35mm film',
    'film_medium_format':    'Medium-format film',
}

_FORBIDDEN_WORDS = (
    'stunning, breathtaking, magical, enchanting, iconic, world-class, vibrant, '
    'nestled, boasting, hidden gem, off the beaten path, a feast for the senses, '
    'evocative, timeless, unmissable, legendary'
)


def _gear_block(gear_profile: dict | None) -> str:
    """Format a gear profile dict as a plain-text gear vault block."""
    if not gear_profile:
        return (
            'Gear vault: unknown.\n'
            'Write settings and setup for a competent amateur '
            'using a mid-range full-frame mirrorless camera.\n'
        )

    camera_label = _CAMERA_LABELS.get(
        gear_profile.get('camera_type', ''),
        gear_profile.get('camera_type', 'unknown'),
    )

    lines = ['Gear vault:']
    lines.append(f'  Camera body: {camera_label}')

    lenses = gear_profile.get('lenses') or []
    if lenses:
        lens_strs = [
            f'{l} ({_LENS_RANGES[l]})' if l in _LENS_RANGES else l
            for l in lenses
        ]
        lines.append(f'  Lenses:      {", ".join(lens_strs)}')
    else:
        lines.append('  Lenses:      unknown')

    accessories = []
    if gear_profile.get('has_tripod'):
        accessories.append('tripod')
    if gear_profile.get('has_gimbal'):
        accessories.append('gimbal / stabiliser')
    for f in (gear_profile.get('has_filters') or []):
        accessories.append(f)
    if accessories:
        lines.append(f'  Accessories: {", ".join(accessories)}')

    if gear_profile.get('notes'):
        lines.append(f'  Notes:       {gear_profile["notes"]}')

    return '\n'.join(lines) + '\n'


def _settings_guidance(camera_type: str) -> str:
    """Return camera-type-specific settings advice for the system prompt."""
    if camera_type in ('film_35mm', 'film_medium_format'):
        return (
            'The Settings section: give film-appropriate advice — recommend a film stock, '
            'ASA rating, metering mode, and whether to bracket. '
            'No digital ISO/shutter/aperture numbers.'
        )
    if camera_type == 'smartphone':
        return (
            'The Settings section: give smartphone-specific advice. '
            'Use Pro mode values (ISO, shutter speed, white balance) where applicable. '
            'Recommend exposure lock, focus lock, and when to switch between Night mode '
            'and manual Pro mode. Do not suggest mirrorless/DSLR settings.'
        )
    return (
        'The Settings section: give a concrete starting-point exposure triangle '
        '(ISO / aperture / shutter speed) calibrated to the camera type and shot. '
        'Recommend manual or semi-auto mode. State when to bracket. '
        'One set of numbers — not a range.'
    )


def _tripod_guidance(has_tripod: bool) -> str:
    if has_tripod:
        return (
            'They have a tripod — recommend using it for any exposure under 1/30 s, '
            'all blue-hour and long-exposure shots, and stacked panoramas.'
        )
    return (
        'No tripod — keep shutter speeds high enough to hand-hold cleanly. '
        'Flag any shot that genuinely needs a tripod so they can decide whether '
        'to hire or borrow one.'
    )


# ---------------------------------------------------------------------------
# Photo scout — main guide generation
# ---------------------------------------------------------------------------

def build_photo_scout_system_prompt(gear_profile: dict | None = None) -> str:
    """Build the system prompt for the Kelby-style photography location scout."""
    gear_section = _gear_block(gear_profile)
    camera_type  = (gear_profile or {}).get('camera_type', '')

    settings_rule = _settings_guidance(camera_type)
    tripod_rule   = _tripod_guidance(bool((gear_profile or {}).get('has_tripod')))

    return f"""You are Scott Kelby — the world's most practical photography instructor.
You write location guides for real photographers: specific, honest, technically exact.
No fluff. No travel-brochure language. Just what the photographer needs to nail the shot.

{gear_section}
GEAR RULES:
- The Setup section MUST reference the lens category and its focal range from the vault.
  "Use the Wide to Standard (24–70mm) zoom at the wide end" — not just "use a wide lens".
  If they have Ultra-Wide Angle (10–20mm), call it out specifically for architecture/interiors.
- {settings_rule}
- {tripod_rule}
- Only recommend filters the photographer actually owns.
- required_gear must list ONLY items from their vault that any shot at this location needs.
  Use category names (e.g. "Telephoto Zoom", "tripod") — not focal lengths.
  If they don't have a critical item, say so in the_reality_check.

MULTI-SHOT FORMAT — 'shots' array per location:
Each location gets 2–3 distinct shooting approaches in the 'shots' array.
Each shot must be a genuinely different creative take:
  - Different lens category (wide vs telephoto)
  - Different subject element (full building vs architectural detail)
  - Different vantage point (ground level vs elevated)
  - Different light window (sunrise interior vs blue-hour exterior)
Do NOT paraphrase the same shot. If a shot requires a lens category they don't have, skip it.
Minimum 1 shot per location, maximum 3.

STRUCTURE per location:
shots (array of 2–3):
  title:         Short label — "Full facade at golden hour" or "Tower detail — telephoto"
  the_shot:      One sharp paragraph. What are you pointing at and why does it work?
                 Lead with the subject. State what makes this specific angle compelling.
  the_setup:     Exact position. Lens category + focal range from vault. Framing technique.
                 "Stand at the north end. Use the Wide to Standard (24–70mm) at 24mm..."
  the_settings:  {settings_rule}  One concrete starting point per shot.

the_reality_check (shared for all shots at this location):
  Honest logistics. Crowds, sun direction at the actual shoot time, parking,
  access restrictions, permit requirements, seasonal caveats.
  Use the ephemeris data to confirm sun direction and timing.
  Flag any gear gaps for any of the shots above.

shoot_window (shared):
  A specific time range — e.g. "5:45–7:00 AM (Day 2)".
  Use the actual ephemeris times provided, converted to local destination time.
  Golden hour = 60 min either side of sunrise/sunset. Blue hour = civil dawn/dusk window.

distance_from_accommodation (shared):
  Walking or transit time from starting point. Write "N/A" if none was given.

WRITING RULES:
- Every sentence must earn its place. Cut filler ruthlessly.
- Short sentences. Concrete nouns. Active verbs.
- Forbidden words: {_FORBIDDEN_WORDS}.
- Be honest about trade-offs. If it's crowded, say so and when it isn't.

Use the submit_photo_locations tool to return all locations."""


def build_photo_scout_user_prompt(
    location:            str,
    duration:            int,
    per_day:             int,
    interests:           str,
    distance:            str,
    accommodation_block: str,
    pre_planned_block:   str,
    client_block:        str,
    ephemeris_block:     str,
    start_date:          str | None = None,
) -> str:
    """Build the user prompt for the Kelby-style photography location scout."""
    count     = duration * per_day
    date_line = f'- Trip starts: {start_date}\n' if start_date else ''

    return f"""Plan {count} photography shoots ({per_day} per day across {duration} days).

Trip details:
- Destination: {location}
- Duration: {duration} days
{date_line}- Photography interests: {interests or 'general — landscapes, architecture, street'}
- Max travel radius: {distance}
{accommodation_block}
{pre_planned_block}
{client_block}
{ephemeris_block}
Return exactly {count} locations, spread across days 1–{duration}.
For each location, set shoot_window using the actual ephemeris times above, \
converted to local destination time."""


# ---------------------------------------------------------------------------
# Photo scout — /replace endpoint (single swap)
# ---------------------------------------------------------------------------

def build_photo_replace_system_prompt() -> str:
    """System prompt for the /replace endpoint — single photo location swap."""
    return (
        'You are a photography location scout. Find ONE real, currently accessible '
        'photography location that has NOT already been suggested for this trip. '
        'Be specific: name the exact spot and include 2–3 distinct shooting approaches '
        'in the shots array (different lens categories, vantage points, or subject elements). '
        'Each shot must reference lens categories by name and focal range. '
        'Shared logistics go in the_reality_check at location level.'
    )


def build_photo_replace_user_prompt(
    location:      str,
    day:           int,
    duration:      int,
    interests:     str,
    distance:      str,
    exclude_block: str,
) -> str:
    """User prompt for the /replace endpoint."""
    return f"""Find one photography location in {location}.

{exclude_block}Context: Day {day} of a {duration}-day trip.
Photography interests: {interests or 'general'}
Max travel radius: {distance}
Set day={day} and distance_from_accommodation="N/A"."""
