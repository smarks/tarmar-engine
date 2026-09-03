"""SJG-DERIVED CLASSIC *MELEE* RULES DATA — SEGREGATED. DO NOT RE-EXPORT.

This module is the ONE home for rules data derived from Steve Jackson Games'
*The Fantasy Trip: Melee* (3rd ed.): the printed Weapon Table (p.14), the Armor
and Shields tables (p.14), the figure-creation constants (Section III), the
Reactions to Injury thresholds (p.20), and the special to-hit totals (p.10).
Per the unification plan's copyright note, this data must never leak into the
Tarmar-canon modules: nothing outside ``tarmar_engine.classic`` may import it
(guarded by ``tests/test_classic_profile.py``), and the Tarmar profile never
reads it.

Ported faithfully from melee's ``engine/rules_data.py`` and the special-total
tables in its ``engine/combat.py``; page references are to the SJGames PDF
edition supplied with the melee project.

Damage is written in the rulebook as ``Nd+M`` / ``Nd-M`` (roll N six-sided
dice and add the modifier), modeled as :class:`DamageDice(count, modifier)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..reactions import HitCountReactions


@dataclass(frozen=True)
class DamageDice:
    """A weapon damage expression: roll ``count`` d6 and add ``modifier``."""

    count: int
    modifier: int

    def __str__(self) -> str:
        if self.modifier > 0:
            return f"{self.count}d+{self.modifier}"
        if self.modifier < 0:
            return f"{self.count}d{self.modifier}"
        return f"{self.count}d"


class WeaponKind(StrEnum):
    MELEE = "melee"
    POLE = "pole"
    THROWN = "thrown"   # a melee weapon that may also be thrown
    MISSILE = "missile"


@dataclass(frozen=True)
class Weapon:
    """A weapon from the Weapon Table (p.14).

    Attributes:
        name: display name.
        damage: damage dice when it hits.
        min_strength: minimum *starting* ST to wield it (0 = no requirement).
        kind: melee / pole / thrown / missile.
        two_handed: occupies both hands (a shield must be slung to use it).
        hth_damage: damage when used in hand-to-hand (only daggers differ).
        throwable: may be thrown with the thrown-weapon rules.
        notes: rulebook note text.
        reload: base turns to reload after firing (crossbows; 0 = fires every
            turn). Reduced by 1 once adjDX reaches ``fast_reload_dx`` — see
            :func:`missile_reload_turns`.
        fast_reload_dx: adjDX at/above which reload drops by one turn (0 = never).
        double_shot_dx: adjDX at/above which a bow fires twice/turn (0 = never).
        reach: hexes it can strike (pole weapons jab at 2; p.12).
    """

    name: str
    damage: DamageDice
    min_strength: int
    kind: WeaponKind = WeaponKind.MELEE
    two_handed: bool = False
    hth_damage: DamageDice | None = None
    throwable: bool = False
    notes: str = ""
    reload: int = 0
    fast_reload_dx: int = 0
    double_shot_dx: int = 0
    reach: int = 1


# ---- Weapon Table (p.14) ----------------------------------------------------
# hand weapons
DAGGER = Weapon("Dagger", DamageDice(1, -1), 0, throwable=True,
                hth_damage=DamageDice(1, 2), notes="1d+2 in HTH combat")
MAIN_GAUCHE = Weapon("Main-Gauche", DamageDice(1, -1), 0,
                     hth_damage=DamageDice(1, 2), notes="parries 1 hit/attack")
RAPIER = Weapon("Rapier", DamageDice(1, 0), 9)
CLUB = Weapon("Club", DamageDice(1, 0), 9, throwable=True)
HAMMER = Weapon("Hammer", DamageDice(1, 1), 10, throwable=True)
SABER = Weapon("Saber", DamageDice(2, -2), 10)
SHORTSWORD = Weapon("Shortsword", DamageDice(2, -1), 11)
MACE = Weapon("Mace", DamageDice(2, -1), 11, throwable=True)
SMALL_AX = Weapon("Small ax", DamageDice(1, 2), 11, throwable=True)
BROADSWORD = Weapon("Broadsword", DamageDice(2, 0), 12)
MORNINGSTAR = Weapon("Morningstar", DamageDice(2, 1), 13)
TWO_HANDED_SWORD = Weapon("Two-handed sword", DamageDice(3, -1), 14, two_handed=True)
BATTLEAXE = Weapon("Battleaxe", DamageDice(3, 0), 15, two_handed=True)

# The wizard's staff (TFT: Wizard p.19): 1d damage, no ST requirement. NOT in
# the WEAPONS catalog — "Only wizards may carry magical staffs" (p.23), and the
# catalog is exactly what a fighter build may pick, so keeping the staff out
# makes a fighter spec naming it fail as "unknown weapon". A wizard gets one
# from the consumer's spell layer (knowing the Staff spell grants it at build).
STAFF = Weapon("Staff", DamageDice(1, 0), 0,
               notes="a wizard's magical staff — wizards only (Wizard p.19)")

# pole weapons (Pole Weapon rules, p.12)
JAVELIN = Weapon("Javelin", DamageDice(1, -1), 9, kind=WeaponKind.POLE,
                 throwable=True, notes="too short to jab")
SPEAR = Weapon("Spear", DamageDice(1, 1), 11, kind=WeaponKind.POLE,
               two_handed=True, throwable=True, reach=2)
HALBERD = Weapon("Halberd", DamageDice(2, 0), 13, kind=WeaponKind.POLE,
                 two_handed=True, reach=2)
PIKE_AXE = Weapon("Pike axe", DamageDice(2, 2), 15, kind=WeaponKind.POLE,
                  two_handed=True, reach=2)

# missile weapons (Missile Weapon rules, p.16)
THROWN_ROCK = Weapon("Thrown rock", DamageDice(1, -4), 0, kind=WeaponKind.MISSILE,
                     throwable=True, notes="you can always pick up a rock")
SLING = Weapon("Sling", DamageDice(1, -2), 0, kind=WeaponKind.MISSILE,
               two_handed=True)
SMALL_BOW = Weapon("Small bow", DamageDice(1, -1), 9, kind=WeaponKind.MISSILE,
                   two_handed=True, double_shot_dx=15,
                   notes="2 shots/turn if adjDX 15+")
HORSE_BOW = Weapon("Horse bow", DamageDice(1, 0), 10, kind=WeaponKind.MISSILE,
                   two_handed=True, double_shot_dx=16,
                   notes="2 shots/turn if adjDX 16+")
LONGBOW = Weapon("Longbow", DamageDice(1, 2), 11, kind=WeaponKind.MISSILE,
                 two_handed=True, double_shot_dx=18, notes="2 shots/turn if adjDX 18+")
LIGHT_CROSSBOW = Weapon("Light crossbow", DamageDice(2, 0), 12,
                        kind=WeaponKind.MISSILE, two_handed=True, reload=1,
                        fast_reload_dx=14,
                        notes="fires every other turn, or every turn if adjDX 14+")
HEAVY_CROSSBOW = Weapon("Heavy crossbow", DamageDice(3, 0), 15,
                        kind=WeaponKind.MISSILE, two_handed=True, reload=2,
                        fast_reload_dx=16,
                        notes="fires every 3rd turn, or every other if adjDX 16+")


def max_missile_shots(weapon: Weapon | None, adj_dx: int) -> int:
    """Shots a missile weapon may fire in one turn — bows fire twice at a high
    enough adjDX (p.14); everything else fires once."""
    if weapon is None or weapon.kind != WeaponKind.MISSILE:
        return 0
    return 2 if weapon.double_shot_dx and adj_dx >= weapon.double_shot_dx else 1


def missile_reload_turns(weapon: Weapon | None, adj_dx: int) -> int:
    """Turns a missile weapon needs to reload before it can fire again (p.16).

    A crossbow reloads one turn faster once adjDX reaches its own
    ``fast_reload_dx`` threshold (light crossbow 14, heavy crossbow 16 per the
    ITL p.109 Weapon Table); bows reload as they fire (0).
    """
    if weapon is None or weapon.kind != WeaponKind.MISSILE:
        return 0
    fast = weapon.fast_reload_dx and adj_dx >= weapon.fast_reload_dx
    return max(0, weapon.reload - (1 if fast else 0))


WEAPONS: dict[str, Weapon] = {
    weapon.name: weapon
    for weapon in (
        DAGGER, MAIN_GAUCHE, RAPIER, CLUB, HAMMER, SABER, SHORTSWORD, MACE,
        SMALL_AX, BROADSWORD, MORNINGSTAR, TWO_HANDED_SWORD, BATTLEAXE,
        JAVELIN, SPEAR, HALBERD, PIKE_AXE,
        THROWN_ROCK, SLING, SMALL_BOW, HORSE_BOW, LONGBOW,
        LIGHT_CROSSBOW, HEAVY_CROSSBOW,
    )
}


# ---- Armor (p.14) -----------------------------------------------------------
@dataclass(frozen=True)
class Armor:
    """Body armor. ``stops`` hits are absorbed from each attack."""

    name: str
    stops: int
    movement_allowance: int
    dx_penalty: int  # negative


NO_ARMOR = Armor("None", 0, 10, 0)
CLOTH = Armor("Cloth", 1, 10, -1)
LEATHER = Armor("Leather", 2, 8, -2)
CHAINMAIL = Armor("Chainmail", 3, 6, -3)
HALF_PLATE = Armor("Half-plate", 4, 6, -4)
PLATE = Armor("Plate", 5, 6, -5)

ARMORS: dict[str, Armor] = {
    armor.name: armor
    for armor in (NO_ARMOR, CLOTH, LEATHER, CHAINMAIL, HALF_PLATE, PLATE)
}


# ---- Shields (p.14) ---------------------------------------------------------
@dataclass(frozen=True)
class Shield:
    """A shield. When ready it stops ``stops`` hits from frontal attacks."""

    name: str
    stops: int
    dx_penalty: int  # negative; applied only while the shield is ready


NO_SHIELD = Shield("None", 0, 0)
SMALL_SHIELD = Shield("Small shield", 1, 0)
LARGE_SHIELD = Shield("Large shield", 2, -1)

SHIELDS: dict[str, Shield] = {
    shield.name: shield
    for shield in (NO_SHIELD, SMALL_SHIELD, LARGE_SHIELD)
}


# ---- Figure creation constants (Section III) --------------------------------
HUMAN_START_TOTAL = 24       # ST + DX points for a fresh human (8/8 + 8 extra)
HUMAN_MIN_ATTRIBUTE = 8      # neither ST nor DX may begin below 8
THREE_DICE = 3               # a "3/DX" to-hit roll is 3d6 under adjDX

# Reactions to Injury (p.20) thresholds.
WOUND_DX_PENALTY = -2        # took 5+ hits since last attack
WOUND_HITS_THRESHOLD = 5
LOW_ST_DX_PENALTY = -3       # ST reduced to 3 or less
LOW_ST_THRESHOLD = 3
KNOCKDOWN_HITS = 8           # 8+ hits in one turn -> fall down


# ---- Special to-hit totals (p.10) -------------------------------------------
# Special three-dice totals -> (hit?, damage multiplier, drop, break).
THREE_DICE_SPECIALS: dict[int, tuple[bool, int, bool, bool]] = {
    3: (True, 3, False, False),
    4: (True, 2, False, False),
    5: (True, 1, False, False),
    16: (False, 0, False, False),
    17: (False, 0, True, False),
    18: (False, 0, False, True),
}
# Special four-dice totals (vs a dodging/defending target).
FOUR_DICE_SPECIALS: dict[int, tuple[bool, int, bool, bool]] = {
    4: (True, 3, False, False),
    5: (True, 2, False, False),
    20: (False, 0, False, False),
    21: (False, 0, True, False),
    22: (False, 0, True, False),
    23: (False, 0, False, True),
    24: (False, 0, False, True),
}


def classic_reactions() -> HitCountReactions:
    """Milestone 2's reaction *mechanism* wired with the rulebook's *numbers*.

    The shared :class:`~tarmar_engine.reactions.HitCountReactions` ships every
    threshold injected precisely so the SJG values could stay here: 5+ hits in
    a turn wound (-2 DX next turn), 8+ knock down, ST 0 fells, ST -1 kills,
    and ST 3 or below is a lasting -3 DX (p.20 / p.3).
    """
    return HitCountReactions(
        knockdown_hits=KNOCKDOWN_HITS,
        wound_hits=WOUND_HITS_THRESHOLD,
        wound_dx_penalty=-WOUND_DX_PENALTY,
        unconscious_at=0,
        dead_at=-1,
        low_pool_threshold=LOW_ST_THRESHOLD,
        low_pool_dx_penalty=-LOW_ST_DX_PENALTY,
    )
