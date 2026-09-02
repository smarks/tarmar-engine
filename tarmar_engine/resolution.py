"""The engine's face of the shared d20 resolution core (``tarmar-rules``).

Everything the turn engine and AI policy need from attack resolution comes
through this one module: the primitives re-exported from ``tarmar_rules``
(the drift-guarded package tarmar-studio and melee both consume), plus the
§8 Hybrid-armour decomposition helpers the engine's expected-damage math
reads. In tarmar-studio these names were reached via ``characters.combat``;
the package cannot import the Django app, so this module is the seam.

The catalog-aware adapters (``weapon_skill_bonus``/``to_hit_bonus`` resolving
weapon *ids*) stay in tarmar-studio's ``characters.combat`` — the engine
always passes class strings and plain skill levels, which is exactly the
package-level contract.
"""

from __future__ import annotations

from tarmar_rules import (
    CRIT_DAMAGE_ROLLS,
    DIE_FACES,
    HEAVY_CLASSES,
    damage_after_armour,
    dex_modifier,
    dodge_modifier,
    hit_probability,
    resolve_attack,
    target_number,
    to_hit_bonus,
)
from tarmar_rules import fumble_table_lookup as fumble_result

__all__ = [
    "CRIT_DAMAGE_ROLLS",
    "DIE_FACES",
    "HEAVY_CLASSES",
    "applied_armour_stops",
    "damage_after_armour",
    "dex_modifier",
    "dodge_modifier",
    "fumble_result",
    "hit_probability",
    "hybrid_bypass_applies",
    "resolve_attack",
    "target_number",
    "to_hit_bonus",
]


def hybrid_bypass_applies(weapon_class: str, armour_tier: str) -> bool:
    """Does the §8 Hybrid rule halve this pairing's stops?

    True for a Heavy Striking / Heavy Thrusting weapon against Heavy armour.
    Split out so a caller that has to *show* the rule reads it from here
    rather than restating the condition. Mirrors
    ``tarmar_rules.damage_after_armour``'s inline condition exactly.
    """
    return weapon_class in HEAVY_CLASSES and armour_tier == "Heavy"


def applied_armour_stops(stops: int, weapon_class: str, armour_tier: str) -> int:
    """The stops that actually come off a blow, after the §8 Hybrid rule."""
    if hybrid_bypass_applies(weapon_class, armour_tier):
        return stops // 2
    return stops
