"""Attack arithmetic shared by the engine and the AI policy.

One function computes the Target Number and to-hit bonus for a prospective
attack — the policy scores candidates with it (via
``tarmar_rules.hit_probability``) and the engine resolves the real roll
with the very same numbers, so what the AI believed and what the dice faced
can never drift apart.

All base resolution comes from :mod:`.resolution` (the engine's face of the
drift-guarded
``tarmar_rules`` d20 core); this module only assembles its inputs from the battle state:
facing arcs and range bands (``tarmar_engine.hexes``), Defend/Dodge TN
bonuses (``special-combat-situations.md``), and active spell effects
(``battle.spells``).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import hexes
from . import resolution as combat
from .dice import parse_dice_expression
from .spells import get_spell
from .state import BattleState, CombatantState

# attack-rolls.md §7 fumble table: off-balance is −2 to your next action.
OFF_BALANCE_PENALTY = 2


@dataclass(frozen=True)
class AttackNumbers:
    """Everything known about an attack before any die is thrown."""

    target_number: int
    bonus: int
    arc: str
    situational: int
    range_penalty: int
    distance: int


def figure_distance(a: CombatantState, b: CombatantState) -> int:
    """Hex distance between two combatants' footprints (closest edge)."""
    return hexes.figure_distance(a.footprint, b.footprint)


def figures_adjacent(a: CombatantState, b: CombatantState) -> bool:
    """Are two combatants in melee reach (footprints one hex apart)?"""
    return hexes.figures_adjacent(a.footprint, b.footprint)


def is_engaged(state: BattleState, actor: CombatantState) -> bool:
    """Is ``actor`` engaged per movement.md's table, footprints included?"""
    enemies = [
        (enemy.front_hexes, enemy.size_hexes) for enemy in state.enemies_of(actor)
    ]
    return hexes.figure_engaged(actor.footprint, actor.size_hexes, enemies)


def spell_tn_bonus(defender: CombatantState) -> int:
    """TN added by the defender's active continuing spells (e.g. Shield)."""
    return sum(get_spell(key).tn_bonus for key in defender.active_spells)


def spell_attacker_penalty(defender: CombatantState) -> int:
    """To-hit penalty active spells impose on attacks at the defender (Blur)."""
    return sum(get_spell(key).attacker_penalty for key in defender.active_spells)


def attack_numbers(
    attacker: CombatantState,
    defender: CombatantState,
    *,
    ranged: bool = False,
    weapon_class: str | None = None,
    extra_situational: int = 0,
    ignore_attacker_skill: bool = False,
    ignore_defender_bonuses: bool = False,
) -> AttackNumbers:
    """TN and to-hit bonus for attacker striking defender, situation included.

    Situational modifiers applied (dex-adjustments.md): the defender's arc
    (side +2 / rear +4; a prone defender counts every hex as rear), range for
    missile and thrown attacks, and active-spell penalties. Defend and Dodge
    add their +4 to the Target Number against the attack types they cover
    (special-combat-situations.md): Defend vs melee, Dodge vs missiles/thrown.

    Multi-hex figures: distance runs footprint edge to footprint edge, and a
    multi-hex defender's arc is read from its head hex — the rules publish no
    per-body-hex arc for large figures, so the head's facing governs.

    The HTH grapple sub-flow (hand-to-hand-and-grappling.md) reuses this same
    arithmetic through four overrides, all off by default so every existing
    caller is unaffected:

    * ``weapon_class`` resolves the Target Number against a class other than
      the attacker's readied weapon — a grapple attempt checks the
      Flexible/Snare row instead of the weapon's own class.
    * ``extra_situational`` adds a flat to-hit bonus outside the normal
      arc/range/spell arithmetic — the HTH +4 both sides get once sharing a
      hex (:data:`hexes.HTH_TO_HIT_BONUS`).
    * ``ignore_attacker_skill`` drops the attacker's weapon skill and
      strength-fit penalty — "No Weapon skill applies" to a bare-handed HTH
      action, whatever the attacker's readied-weapon skill level.
    * ``ignore_defender_bonuses`` drops the defender's shield, active-spell
      TN bonus, dodge modifier, and Defend/Dodge state — a Squeeze target
      "gets no Dodge/Defend bonus against it — they're already held," and a
      grappled figure gains no benefit from a shield per the same section.
    """
    distance = figure_distance(attacker, defender)
    arc = hexes.arc_of(defender.position, defender.facing, attacker.position)
    situational = hexes.arc_to_hit_bonus(arc, defender_prone=defender.prone)

    range_penalty = 0
    if ranged:
        if attacker.weapon.is_missile:
            range_penalty = hexes.missile_range_penalty(distance)
        else:
            range_penalty = hexes.thrown_range_penalty(distance)
    situational += range_penalty
    if not ignore_defender_bonuses:
        situational -= spell_attacker_penalty(defender)
    situational += extra_situational

    bonus = combat.to_hit_bonus(
        effective_dexterity=attacker.dexterity,
        skill_level=0 if ignore_attacker_skill else attacker.weapon_skill_level,
        effective_strength=attacker.strength,
        str_req=None if ignore_attacker_skill else (attacker.weapon.str_req or None),
        situational=situational,
    )

    defend_dodge = 0
    if not ignore_defender_bonuses:
        if ranged and defender.dodging:
            defend_dodge = hexes.DEFEND_DODGE_TN_BONUS
        if not ranged and defender.defending:
            defend_dodge = hexes.DEFEND_DODGE_TN_BONUS

    if ignore_defender_bonuses:
        shield_and_spell_bonus = 0
        defender_dodge = 0
    else:
        shield_and_spell_bonus = defender.shield_bonus + spell_tn_bonus(defender)
        defender_dodge = combat.dodge_modifier(defender.dexterity)

    target = (
        combat.target_number(
            weapon_class or attacker.weapon.weapon_class,
            defender.armour_tier,
            shield_bonus=shield_and_spell_bonus,
            defender_dodge=defender_dodge,
        )
        + defend_dodge
    )
    return AttackNumbers(
        target_number=target,
        bonus=bonus,
        arc=arc,
        situational=situational,
        range_penalty=range_penalty,
        distance=distance,
    )


def _sum_distribution(count: int, sides: int) -> dict[int, float]:
    """Probability of each possible sum of ``count`` dice, by convolution."""
    distribution = {0: 1.0}
    for _die in range(count):
        next_distribution: dict[int, float] = {}
        for total, probability in distribution.items():
            for face in range(1, sides + 1):
                next_distribution[total + face] = (
                    next_distribution.get(total + face, 0.0) + probability / sides
                )
        distribution = next_distribution
    return distribution


def expected_damage(expression: str, stops_applied: int, repetitions: int = 1) -> float:
    """Exact E[max(damage − stops, 0)] for a dice expression rolled N times.

    The truncation matters: a saber (2d6−2) against 5 stops has a *mean*
    below the stops but still gets damage through on high rolls, and an AI
    scoring with a clamped mean would (wrongly) never attack heavy armour.
    ``repetitions`` models a critical's multiple damage rolls — the dice
    multiply, armour comes off the summed total once (§7/§8).
    """
    count, sides, modifier = parse_dice_expression(expression)
    distribution = _sum_distribution(count * repetitions, sides)
    total_modifier = modifier * repetitions - stops_applied
    return sum(
        max(0, total + total_modifier) * probability
        for total, probability in distribution.items()
    )


def expected_attack_damage(attacker: CombatantState, defender: CombatantState) -> float:
    """Expected post-armour damage of one landed swing, crits included.

    E[one damage roll] plus the natural-20 path's extra roll (a nat 20 is
    1-in-20 of all *hits'* faces and rolls the dice
    :data:`tarmar_rules.CRIT_DAMAGE_ROLLS` times). Severe-critical
    tripling is left out — the confirm branch is small and the policy only
    needs a ranking.
    """
    stops = combat.applied_armour_stops(
        defender.stops, attacker.weapon.weapon_class, defender.armour_tier
    )
    single = expected_damage(attacker.weapon.damage, stops)
    crit = expected_damage(
        attacker.weapon.damage, stops, repetitions=combat.CRIT_DAMAGE_ROLLS
    )
    return single + (crit - single) / combat.DIE_FACES
