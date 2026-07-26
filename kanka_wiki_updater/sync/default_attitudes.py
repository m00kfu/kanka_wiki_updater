"""Default starting attitudes for new relations (guideline for LLM prompts).

This module provides a static lookup table and a formatting function that
produces human-readable guidance text. The output is injected into the LLM
system prompt so it can choose an informed starting attitude when creating
brand-new relations with no prior score in Kanka.
"""


DEFAULT_ATTITUDES: dict[str, int] = {
    # Nemesis-tier hostility
    "nemesis": -80,
    "captor_of": -80,
    "mortal_enemy": -80,
    # Rivalry-tier animosity
    "rival": -30,
    "opponent": -30,
    "debtor": -30,
    # Neutral / business
    "business_partner": 0,
    "employer": 0,
    "member_of": 0,
    # Acquaintance-tier goodwill
    "acquaintance": 15,
    "informant": 15,
    # Ally-tier warmth
    "ally": 50,
    "friend": 50,
    "sibling": 50,
    # Devotion-tier deep bond
    "devoted_follower": 85,
    "spouse": 85,
    "sworn_protector": 85,
}


def attitude_guidance_text() -> str:
    """Return a formatted block for inclusion in LLM prompts.

    Groups relation types by their suggested starting attitude value and
    presents them compactly so the LLM can quickly find the right baseline.
    """
    lines = [
        "For **new relations** (action: create) with no prior attitude, use these suggested starting points as your baseline before applying any shift:"
    ]

    # Group by attitude value for compactness
    groups: dict[int, list[str]] = {}
    for key, val in DEFAULT_ATTITUDES.items():
        label = key.replace("_", " ")
        groups.setdefault(val, []).append(label)

    for val in sorted(groups):
        labels = ", ".join(sorted(groups[val]))
        sign = "+" if val > 0 else ""
        lines.append(f"  {sign}{val}: {labels}")

    return "\n".join(lines) + (
        "\n\nThen output `attitude_delta` relative to that baseline."
    )
