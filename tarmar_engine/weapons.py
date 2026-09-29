"""Missile rate of fire and reload, read off weapons.md (tarmar-studio #781).

weapons.md's Missile Weapons table carries the rates in its Notes column as
prose — "2 shots/turn if adjDEX 15+", "Every other turn; every if 14+",
"Every 3rd turn; every other 16+". The engine keeps no catalog (games own
theirs), so this module reads the published note instead: a game passes the
note from its catalog row and gets the numbers back, and the engine's drift
test parses the page through the same function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .state import CombatantState, WeaponState

_DOUBLE_SHOT = re.compile(r"2 shots/turn if adjDEX (\d+)\+")
_CYCLE_WORDS = {"every": 1, "every other": 2, "every 3rd": 3}
_CYCLE = r"(every(?: other| 3rd)?)"
_RELOAD = re.compile(
    rf"^{_CYCLE} turn; {_CYCLE}(?: turn)? (?:if )?(\d+)\+$", re.IGNORECASE
)


@dataclass(frozen=True)
class MissileRate:
    """The four rate numbers a :class:`WeaponState` carries."""

    double_shot_dex: int = 0
    reload_turns: int = 1
    quick_reload_dex: int = 0
    quick_reload_turns: int = 1


def missile_rate_from_note(note: str | None) -> MissileRate:
    """The rate a weapons.md Notes cell states; the plain rate for any other.

    Args:
        note: The Notes cell, e.g. ``"Every 3rd turn; every other 16+"``.

    Returns:
        The rate numbers. A note that states no rate (``"Including rocks"``,
        empty) is one shot, every turn.
    """
    text = (note or "").strip()
    double = _DOUBLE_SHOT.search(text)
    if double:
        return MissileRate(double_shot_dex=int(double.group(1)))
    reload = _RELOAD.match(text)
    if reload:
        return MissileRate(
            reload_turns=_CYCLE_WORDS[reload.group(1).lower()],
            quick_reload_dex=int(reload.group(3)),
            quick_reload_turns=_CYCLE_WORDS[reload.group(2).lower()],
        )
    return MissileRate()


def reload_cycle(weapon: WeaponState, dexterity: int) -> int:
    """Turns per shot for this weapon in these hands (1 = every turn)."""
    if weapon.quick_reload_dex and dexterity >= weapon.quick_reload_dex:
        return weapon.quick_reload_turns
    return weapon.reload_turns


def shots_per_turn(weapon: WeaponState, dexterity: int) -> int:
    """2 for a bow in hands quick enough for its note, else 1."""
    if weapon.double_shot_dex and dexterity >= weapon.double_shot_dex:
        return 2
    return 1


def is_fired_missile(weapon: WeaponState) -> bool:
    """A bow, sling or crossbow — fired, not thrown (One Last Shot's
    "Fire missile")."""
    return weapon.is_missile and not weapon.is_thrown


def can_shoot(combatant: CombatantState) -> bool:
    """Holds a missile or throwable weapon that is ready to loose now."""
    weapon = combatant.weapon
    if not (weapon.is_missile or weapon.is_thrown):
        return False
    return weapon.reload_turns_left == 0


def is_armed(combatant: CombatantState) -> bool:
    """Holds a weapon, or fights with natural ones (a beast).

    movement.md engages "In an armed enemy's front hex" (tarmar-studio #820).
    """
    return combatant.is_beast or combatant.weapon.item_id != ""


def has_melee_weapon(combatant: CombatantState) -> bool:
    """Can make option j/b's "Melee attack (non-missile)".

    A beast's natural weapons count; bare hands do not (bare-handed fighting
    is HTH, option t — tarmar-studio #815), and neither does a bow or
    crossbow held in hand.
    """
    if combatant.is_beast:
        return True
    weapon = combatant.weapon
    return weapon.item_id != "" and not weapon.is_missile
