"""Classic low-level attack primitives (Section VII) — ported from melee.

The stateless building blocks an attack is made of: the special-roll
classifier and the weapon-damage roll, plus the result/audit records — and,
as of the battle/melee unification's milestone 5 (tarmar-studio#240), the
SPELL-roll layer (TFT: Wizard) ported verbatim from melee: the cast
classifiers, :class:`SpellResult`, and the missile-spell damage roll. The
policy that assembles them lives in :class:`.ruleset.Ruleset`. The special
totals themselves are SJG rules data and live in :mod:`.data`.
"""

from __future__ import annotations

from dataclasses import dataclass

from hexarena.dice import Dice

from .data import (
    FOUR_DICE_SPECIALS,
    SPELL_FOUR_DICE_SPECIALS,
    SPELL_THREE_DICE_SPECIALS,
    THREE_DICE,
    THREE_DICE_SPECIALS,
    DamageDice,
    Weapon,
)

# Outcomes of a "roll to miss" — a missile spell trying to slip past a figure
# standing in its lane (Wizard p.12, rules lines 639-652).
SPELL_MISSED_PAST = "missed_past"   # slipped by; the spell flies on
SPELL_LANE_HIT = "lane_hit"         # the special table struck it anyway
SPELL_LANE_FIZZLE = "lane_fizzle"   # failed roll-to-miss: fizzles in that hex


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
    body_hit: bool = False       # crit reaching a deeper pool (a Fatigue/Body
    #                              stat model's crits); read by apply_damage
    roll_under: bool = True      # classic 3d6: hit by rolling <= needed;
    #                              False: hit by rolling >= needed (a d20
    #                              roll-over model). Read by narration.
    auto_hit: bool = False       # the hit was forced (a weapon striking
    #                              mid-flight, an HTH free hit) — rolled/needed
    #                              are not a test and must not narrate as one
    confirm_roll: int = 0        # a d20 model's §7 confirm roll for a natural-
    #                              20 crit (0 = no confirm rolled)
    severe_crit: bool = False    # the confirm hit — triple damage, the blow
    #                              reaches Body, and the wound bleeds
    fumble_effect: str = ""      # a d20 model's fumble-table outcome for a
    #                              natural 1: "off_balance" / "drop" / "stress"
    #                              / "break". Read by narration and
    #                              apply_attack_side_effects.


@dataclass(frozen=True)
class AttackForecast:
    """One weapon attack as it stands before a die is thrown.

    What :meth:`GameState.attack_forecast` returns: the to-hit number
    ``queue_attack`` would declare the blow at, the dice it is rolled on, the
    chance the roll hits, and what a hit does on average past the target's
    armor. Every term comes from the same code the declaration and the roll
    use, so the forecast and the attack cannot drift apart.

    Attributes:
        needed: The adjDX to roll at or under (``Ruleset.to_hit_number``):
            DX with armor and wounds, spells, the facing bonus, the range
            penalty and the situational modifiers.
        to_hit_breakdown: How ``needed`` was reached, as the attack's
            result will print it ("DX 12 +2 flank -1 range").
        dice_count: Dice rolled to hit: 3, or 4 against a target dodging a
            missile or thrown weapon or defending against a melee blow.
        hit_chance: The probability the roll hits, special totals counted
            (an automatic hit on 3, 4, 5 on three dice; a 16 to 18 always
            misses), through ``Ruleset.classify_roll``.
        hit_multipliers: The chance of a hit at each damage multiplier
            (1 plain, 2 double, 3 triple); the values sum to ``hit_chance``.
        weapon: The weapon the blow is struck with (its ``damage`` dice).
        extra_dice: Extra damage dice a pole weapon earns in or against a
            charge, added after the multiplier (p.12).
        armour_stops: Hits the target's armor, shield, protection spell and
            ready main-gauche take off this blow.
        expected_damage: The average hits a hit takes off the target's ST:
            the weapon's dice, the special totals' multipliers weighted by
            their chance among hits, the extra dice, a practice bout's
            halving, then the armor, floored at 0 per roll.
        zone: The target's zone the blow lands on (front, side or rear), as
            declared.
        ignore_facing: True for a missile, which never takes a facing bonus.
        range_penalty: The DX penalty for range: by megahex for a missile,
            by hex for a thrown weapon, 0 for a melee blow.
        situational: The circumstantial DX modifiers, positive easier.
        situational_note: Their labels ("+2 vs charge -2 over body").
        ranged: True for a missile or a thrown weapon.
        thrown: True when a throwable weapon is hurled at a foe past reach.
        shots: Shots the attack looses this turn (2 for a high-adjDX bow).
    """

    needed: int
    to_hit_breakdown: str
    dice_count: int
    hit_chance: float
    hit_multipliers: dict[int, float]
    weapon: Weapon
    extra_dice: int
    armour_stops: int
    expected_damage: float
    zone: str | None
    ignore_facing: bool
    range_penalty: int
    situational: int
    situational_note: str
    ranged: bool
    thrown: bool
    shots: int


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


def damage_from_total(rolled: int, damage_dice: DamageDice, multiplier: int) -> int:
    """The pre-armor hits a damage roll's dice total makes.

    The one definition of the damage formula: the expression's modifier
    added to the dice ``rolled``, floored at 0, then the crit multiplier.
    :func:`roll_damage` rolls the dice into it; the attack forecast
    (``Ruleset.expected_hit_damage``) weighs every total it can make.
    """
    return max(0, rolled + damage_dice.modifier) * multiplier


def roll_damage(dice: Dice, damage_dice: DamageDice, multiplier: int,
                extra_dice: int = 0) -> int:
    """Roll a damage-dice spec, floor at 0, and apply the crit multiplier.

    ``extra_dice`` (the pole-charge bonus die) are rolled INTO the total
    *before* the multiplier; a caller that wants them added after the
    multiplier instead adds them itself (classic adds the charge die AFTER —
    melee #154 — so ``Ruleset.resolve_attack`` passes them separately).
    """
    rolled = dice.total(damage_dice.count)
    if extra_dice:
        rolled += dice.total(extra_dice)
    return damage_from_total(rolled, damage_dice, multiplier)


def roll_weapon_damage(dice: Dice, weapon: Weapon, multiplier: int) -> int:
    """Roll a weapon's damage dice and apply the crit multiplier (pre-armor)."""
    return roll_damage(dice, weapon.damage, multiplier)


@dataclass
class SpellResult:
    """Outcome of one cast, before its effect (damage/protection) is applied.

    Parallel to :class:`AttackResult` but keyed to a spell: it carries the ST
    actually spent, whether the cast fizzled (a 17/18, which drains the full ST
    cost) and whether an 18 knocked the caster down, plus a missile spell's rolled
    damage. A protection spell (Stone Flesh) lands its hit-stopping via
    ``spell_protection`` rather than ``damage``.
    """

    hit: bool
    rolled: int
    needed: int              # the adjDX the caster had to roll at or under
    dice_count: int
    multiplier: int          # 1 normal, 2 double, 3 triple (a 4/3 auto-crit)
    st_spent: int            # ST drained by this cast (see apply_spell_cost)
    damage: int              # hits coming off the target's ST (missile spells)
    raw_damage: int = 0      # pre-armour damage rolled (missile spells)
    fizzled: bool = False    # a 17/18: the spell failed and lost its full ST cost
    knockdown: bool = False  # an 18: the shock knocked the CASTER down
    spell_id: str = ""
    target_uid: str = ""
    caster_uid: str = ""     # who cast it (a continuing spell dies with its caster)
    stops_granted: int = 0   # protection added to the target (Stone Flesh)
    save_made: bool = False  # a control spell's victim saved (unused this gate)
    to_hit_breakdown: str = ""
    note: str = ""
    auto_hit: bool = False   # the hit was forced (a test/scripted resolution),
    #                          so `rolled`/`needed` are not a hit/miss test


def classify_spell_roll(
    rolled: int, needed: int, dice_count: int = THREE_DICE
) -> tuple[bool, int, bool, bool]:
    """Map a cast total to ``(hit, multiplier, fizzle, knockdown)`` (Wizard p.11).

    A cast is normally three dice; a dodging target forces a MISSILE spell to
    four (and a defending one a non-missile spell — "Dodging is effective only
    against missile spells... Defending is effective only against non-missile
    spells", wizard-rules lines 996-1007, melee #418), with the four-dice
    special table. The three-dice specials: 3/4/5 are automatic hits (triple/
    double/plain); 16 an automatic miss; 17 a fizzle that loses the full ST
    cost; 18 a fizzle that also knocks the caster down (rules lines 594-612).
    Any other total falls back to rolling at or under ``needed``.
    """
    specials = (SPELL_THREE_DICE_SPECIALS if dice_count == THREE_DICE
                else SPELL_FOUR_DICE_SPECIALS)
    if rolled in specials:
        return specials[rolled]
    return (rolled <= needed, 1, False, False)


def classify_spell_roll_to_miss(rolled: int, needed: int) -> tuple[str, int]:
    """Classify a missile spell's "roll to miss" a figure in its lane (melee #417).

    Wizard p.12 (rules lines 639-652): the caster rolls its adjDX or less —
    adjusted for the range to the figure it wants to miss — to slip the spell
    past. The special table overrides the plain roll: "On a roll to miss, a 14
    is an automatic hit, 15 and 16 are double-damage hits, and 17 and 18 are
    triple-damage hits" (lines 646-648). Any other failed roll "is not a hit...
    a missed 'roll to miss' an enemy just fizzles in that hex" (lines 650-652)
    — and the engine only ever rolls to miss ENEMY figures, since a friend in
    the lane is guarded from harm outright (melee #229).

    Returns ``(outcome, damage_multiplier)`` with outcome one of
    :data:`SPELL_MISSED_PAST`, :data:`SPELL_LANE_HIT`, :data:`SPELL_LANE_FIZZLE`.
    """
    if rolled == 14:
        return SPELL_LANE_HIT, 1
    if rolled in (15, 16):
        return SPELL_LANE_HIT, 2
    if rolled in (17, 18):
        return SPELL_LANE_HIT, 3
    if rolled <= needed:
        return SPELL_MISSED_PAST, 0
    return SPELL_LANE_FIZZLE, 0


def roll_missile_spell_damage(dice, spell, st_used: int, multiplier: int) -> int:
    """Roll a missile spell's pre-armour damage (rules lines 653-661).

    One die per ST invested, plus ``spell.damage_per_st`` per ST (Magic Fist is
    1d-2 per ST), floored at the ST invested — "The spell always does at least
    as much damage as was put into it" (line 660-661) — then the crit
    multiplier. The single damage formula for an aimed strike, a lane strike,
    and a fly-on strike (melee #417), so the three paths can never drift.
    """
    base = dice.total(st_used) + spell.damage_per_st * st_used
    return max(st_used, base) * multiplier
