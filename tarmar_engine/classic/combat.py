"""Classic low-level attack primitives (Section VII) — ported from melee.

The stateless building blocks an attack is made of: the special-roll
classifier and the weapon-damage roll, plus the result/audit records. The
policy that assembles them lives in :class:`.ruleset.Ruleset`. The special
totals themselves are SJG rules data and live in :mod:`.data`.
"""

from __future__ import annotations

from dataclasses import dataclass

from hexarena.dice import Dice

from .data import (
    FOUR_DICE_SPECIALS,
    THREE_DICE,
    THREE_DICE_SPECIALS,
    DamageDice,
    Weapon,
)


@dataclass
class DamageEvent:
    """One damaging hit, tagged with both figures' sides for auditing.

    Recorded by ``GameState._apply`` every time an attack takes real hits off
    a target, so a test can attribute damage to the attacker's side and assert
    no figure is ever harmed by its own side. Purely a record.
    """

    attacker_side: str
    target_side: str
    attacker_uid: str
    target_uid: str
    damage: int
    body_damage: int = 0
    same_side_allowed: bool = False


@dataclass
class AttackResult:
    """Outcome of one attack, before its hits are applied to the target."""

    hit: bool
    rolled: int
    needed: int            # the adjDX the attacker had to roll at or under
    dice_count: int
    multiplier: int        # 1 normal, 2 double, 3 triple
    raw_damage: int        # weapon dice total x multiplier, before armor
    damage: int            # hits actually coming off the target's ST
    dropped_weapon: bool
    broke_weapon: bool
    weapon: Weapon | None
    zone: str | None
    note: str = ""
    to_hit_breakdown: str = ""   # human-readable composition of `needed`
    thrown: bool = False         # this attack was a hurled weapon
    roll_under: bool = True      # classic 3d6: hit by rolling <= needed
    auto_hit: bool = False       # the hit was forced (a weapon striking
    #                              mid-flight) — rolled/needed are not a test


def classify_roll(
    rolled: int, dice_count: int, needed: int
) -> tuple[bool, int, bool, bool]:
    """Map a dice total to ``(hit, damage_multiplier, dropped, broke)``.

    Applies the special-total table for the dice count (p.10; :mod:`.data`),
    falling back to the plain roll-under-``needed`` comparison.
    """
    specials = THREE_DICE_SPECIALS if dice_count == THREE_DICE else FOUR_DICE_SPECIALS
    if rolled in specials:
        return specials[rolled]
    return (rolled <= needed, 1, False, False)


def roll_damage(dice: Dice, damage_dice: DamageDice, multiplier: int,
                extra_dice: int = 0) -> int:
    """Roll a damage-dice spec, floor at 0, and apply the crit multiplier.

    ``extra_dice`` (the pole-charge bonus die) are rolled INTO the total
    *before* the multiplier; a caller that wants them added after the
    multiplier instead adds them itself (classic adds the charge die AFTER —
    melee #154 — so ``Ruleset.resolve_attack`` passes them separately).
    """
    total = dice.total(damage_dice.count) + damage_dice.modifier
    if extra_dice:
        total += dice.total(extra_dice)
    return max(0, total) * multiplier


def roll_weapon_damage(dice: Dice, weapon: Weapon, multiplier: int) -> int:
    """Roll a weapon's damage dice and apply the crit multiplier (pre-armor)."""
    return roll_damage(dice, weapon.damage, multiplier)
