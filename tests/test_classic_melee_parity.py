"""Milestone-4 parity: the melee mechanisms the milestone-3 port trimmed.

Hand-to-hand piles, the shield rush, the combat-phase general disengage, and
practice bouts — ported INTO the classic profile for unification milestone 4
(melee consumes this package), each with its melee tests. Ported from melee's
``engine/tests/test_state.py`` and ``engine/tests/test_practice.py``; imports
adapted to ``tarmar_engine.classic``, expectations untouched (the one
adaptation: melee's state-invariant sweep stays in melee with its invariants
module).
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (ported melee tests place figures then use positions; Optional stays as-is)
from __future__ import annotations

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import DEFAULT_LAYOUT as LAYOUT
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    BROADSWORD,
    LEATHER,
    NO_ARMOR,
    SHORTSWORD,
    SMALL_BOW,
)
from tarmar_engine.classic.experience import PRACTICE_DROPOUT_ST, CombatType
from tarmar_engine.classic.facing import FRONT
from tarmar_engine.classic.figure import Posture, create_human
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.ruleset import Ruleset
from tarmar_engine.classic.state import GameState, IllegalAction

RULES = Ruleset()


def _aim(figure, target) -> None:
    """Face ``figure`` toward ``target`` (a shooter aims along the line of fire)."""
    figure.facing = LAYOUT.direction_to(
        figure.position, LAYOUT.line(figure.position, target.position)[1])


def _rear_grapple(defense_roll):
    """An attacker poised behind a defender (rear = HTH-eligible), dice primed
    with the defender's defense roll then plenty of 3s for any strike."""
    from tarmar_engine.classic.data import DAGGER
    arena = Arena(cols=9, rows=15)
    attacker = create_human("Atk", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    defender = create_human("Def", 12, 12, "b", weapons=[SHORTSWORD],
                            ready_weapon=SHORTSWORD)
    defender.position = Hex(5, 5)
    defender.facing = 0                                  # back is toward direction 3
    attacker.position = LAYOUT.neighbor(Hex(5, 5), 3)
    attacker.facing = LAYOUT.direction_to(attacker.position, defender.position)
    state = GameState(arena, [attacker, defender],
                      dice=Dice(scripted=[defense_roll] + [3] * 12))
    return state, attacker, defender


def test_hth_grapple_takes_both_to_the_ground() -> None:
    state, attacker, defender = _rear_grapple(2)
    assert state.hth_attack(attacker, defender) == "grappled"
    assert defender.uid in attacker.hth_opponents
    assert attacker.uid in defender.hth_opponents
    assert attacker.posture == Posture.PRONE and defender.posture == Posture.PRONE
    assert attacker.position == defender.position        # sharing the hex
    assert defender.ready_weapon is None                 # sword dropped, bare-handed


def test_hth_defender_shrugs_off_on_a_five() -> None:
    state, attacker, defender = _rear_grapple(5)
    assert state.hth_attack(attacker, defender) == "shrugged"
    assert not attacker.in_hth and not defender.in_hth
    assert defender.ready_weapon is not None             # kept its weapon


def test_multiple_hth_gang_up_joins_without_rolling_and_scales_dice() -> None:
    from tarmar_engine.classic.data import DAGGER
    arena = Arena(cols=9, rows=15)
    defender = create_human("Def", 9, 15, "b", weapons=[DAGGER], ready_weapon=DAGGER)
    a1 = create_human("A1", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    a2 = create_human("A2", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    defender.position = Hex(5, 5)
    defender.facing = 0
    a1.position = LAYOUT.neighbor(Hex(5, 5), 3)          # behind (rear) — fresh grapple
    a1.facing = LAYOUT.direction_to(a1.position, defender.position)
    a2.position = LAYOUT.neighbor(Hex(5, 5), 1)          # adjacent — will pile on
    a2.facing = LAYOUT.direction_to(a2.position, defender.position)
    state = GameState(arena, [defender, a1, a2], dice=Dice(scripted=[2] + [3] * 20))

    # rear grapple (defender rolls 2)
    state.hth_attack(a1, defender)
    assert state.hth_attack(a2, defender) == "grappled"  # joins the brawl, no roll
    assert a1.uid in defender.hth_opponents and a2.uid in defender.hth_opponents

    a1.ready_weapon = a2.ready_weapon = defender.ready_weapon = None   # bare hands
    assert state._hth_damage(a1, defender).modifier == -3   # two on a side -> 1d-3
    assert state._hth_damage(defender, a1).modifier == -4   # lone, outmuscled 9 vs 24


def test_hth_disengage_breaks_free_on_a_good_roll() -> None:
    state, attacker, defender = _rear_grapple(2)
    state.hth_attack(attacker, defender)
    assert attacker.in_hth
    state.dice = Dice(scripted=[5])                       # equal DX -> needs a 1
    assert state.attempt_hth_disengage(attacker) is False
    assert attacker.in_hth                                # still pinned
    state.dice = Dice(scripted=[1])
    assert state.attempt_hth_disengage(attacker) is True
    assert not attacker.in_hth and attacker.posture == Posture.STANDING
    assert defender.hth_opponents == []                  # link cleared both ways


def test_hth_strike_uses_dagger_dice_at_plus_four() -> None:
    state, attacker, defender = _rear_grapple(2)
    state.hth_attack(attacker, defender)
    result = state.resolve_combat()[0]
    assert result.needed == attacker.base_adj_dx + 4     # the +4 'rear' grapple bonus
    # dagger 1d+2, die scripted to 3
    assert result.raw_damage == 5


def test_standing_attacker_misses_into_a_pile_and_cascades() -> None:
    # Hitting Your Friends (p.17-18): Bjorn hacks at a goblin down in an HTH
    # pile with his friend Ragnar. He misses the goblin, rolls on (same adjusted
    # DX) at the OTHER goblin and misses, then rolls at Ragnar — and hits him.
    from tarmar_engine.classic.data import DAGGER, SHORTSWORD
    arena = Arena(cols=9, rows=15)
    bjorn = create_human("Bjorn", 16, 8, "a",
                         weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    ragnar = create_human("Ragnar", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    g1 = create_human("G1", 12, 12, "b", weapons=[DAGGER], ready_weapon=DAGGER)
    g2 = create_human("G2", 12, 12, "b", weapons=[DAGGER], ready_weapon=DAGGER)
    pile_hex = Hex(5, 5)
    for member in (ragnar, g1, g2):
        member.position = pile_hex
        member.posture = Posture.PRONE
    bjorn.position = LAYOUT.neighbor(pile_hex, 0)
    bjorn.facing = LAYOUT.direction_to(bjorn.position, pile_hex)
    state = GameState(arena, [bjorn, ragnar, g1, g2])
    ragnar.hth_opponents = [g1.uid, g2.uid]              # the two goblins pin Ragnar
    g1.hth_opponents = [ragnar.uid]
    g2.hth_opponents = [ragnar.uid]
    # adjDX 8, +4 rear = needs 12: 13 is a clean miss, 9 a hit. The cascade rolls
    # the OTHER goblin (a miss), then Ragnar (a hit), then his damage.
    state.dice = Dice(scripted=[6, 6, 1] + [6, 6, 1] + [3, 3, 3] + [3] * 12)
    bjorn.current_option = Option.SHIFT_ATTACK
    state.queue_attack(bjorn, g1)
    state.resolve_combat()
    # the declared goblin was missed
    assert g1.damage_taken == 0
    assert g2.damage_taken == 0                          # the other goblin too
    assert ragnar.damage_taken > 0                       # the friend caught the blow
    assert any("strikes Ragnar" in line and "instead" in line for line in state.log)


def test_missile_into_an_hth_pile_strikes_a_random_member() -> None:
    # A shot aimed at a pile of grapplers (p.18): roll to hit, then roll randomly
    # to see who in the pile it caught. A scripted random roll of 2 picks the
    # second member of the pile — not the figure aimed at.
    from tarmar_engine.classic.data import DAGGER, JAVELIN
    arena = Arena(cols=9, rows=15)
    thrower = create_human("Thrower", 11, 13, "a",
                           weapons=[JAVELIN, DAGGER], ready_weapon=JAVELIN)
    aimed = create_human("Aimed", 12, 12, "b", weapons=[DAGGER], ready_weapon=DAGGER)
    other = create_human("Other", 12, 12, "c", weapons=[DAGGER], ready_weapon=DAGGER)
    thrower.position = Hex(5, 5)
    aimed.position = Hex(5, 9)
    other.position = Hex(5, 9)                           # same hex — the pile
    aimed.posture = other.posture = Posture.PRONE
    _aim(thrower, aimed)
    state = GameState(arena, [thrower, aimed, other])
    aimed.hth_opponents = [other.uid]                    # the two are grappling
    other.hth_opponents = [aimed.uid]
    # random pick = 2 (the second pile member, Other), then the to-hit and damage.
    state.dice = Dice(scripted=[2] + [3, 3, 3] + [3] * 12)
    thrower.current_option = Option.CHARGE_ATTACK
    state.queue_attack(thrower, aimed)
    state.resolve_combat()
    assert other.damage_taken > 0                        # the random roll caught Other
    assert aimed.damage_taken == 0                       # not the figure aimed at


def test_hth_free_hit_on_a_six() -> None:
    from tarmar_engine.classic.data import DAGGER, PLATE
    arena = Arena(cols=9, rows=15)
    attacker = create_human("Atk", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    # plate -> lower MA, so HTH is eligible from the front and a 6 is NOT ignored
    defender = create_human("Def", 12, 12, "b", weapons=[SHORTSWORD],
                            ready_weapon=SHORTSWORD, armor=PLATE)
    defender.position = Hex(5, 5)
    defender.facing = 3                                   # facing the attacker (front)
    attacker.position = LAYOUT.neighbor(Hex(5, 5), 3)
    attacker.facing = LAYOUT.direction_to(attacker.position, defender.position)
    # defense roll 6, then a to-hit of 18 that would auto-MISS without force_hit;
    # the free hit must land anyway (#126), so the attacker takes damage.
    state = GameState(arena, [attacker, defender], dice=Dice(scripted=[6] + [6] * 12))
    assert state.hth_attack(attacker, defender) == "free_hit"
    assert not attacker.in_hth                            # no grapple took hold
    assert attacker.damage_taken > 0                      # the automatic hit landed


def test_cannot_grapple_a_standing_equal_foe_from_the_front() -> None:
    from tarmar_engine.classic.data import DAGGER
    arena = Arena(cols=9, rows=15)
    attacker = create_human("Atk", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    defender = create_human("Def", 12, 12, "b", weapons=[SHORTSWORD],
                            ready_weapon=SHORTSWORD)
    defender.position = Hex(5, 5)
    defender.facing = 3                                   # facing the attacker
    attacker.position = LAYOUT.neighbor(Hex(5, 5), 3)
    attacker.facing = LAYOUT.direction_to(attacker.position, defender.position)
    state = GameState(arena, [attacker, defender])
    assert defender not in state.hth_targets(attacker)   # standing, equal MA, frontal


def test_hth_bare_handed_damage_scales_with_strength() -> None:
    from tarmar_engine.classic.data import DAGGER
    arena = Arena(cols=9, rows=15)
    strong = create_human("Strong", 15, 9, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    weak = create_human("Weak", 9, 15, "b", weapons=[DAGGER], ready_weapon=DAGGER)
    state = GameState(arena, [strong, weak])
    strong.ready_weapon = None                           # bare hands
    weak.ready_weapon = None
    assert state._hth_damage(strong, weak).modifier == -2   # vs a weaker foe
    assert state._hth_damage(weak, strong).modifier == -4   # vs a stronger foe
    assert state._hth_damage(strong, strong).modifier == -3  # vs an equal


def test_force_retreat_cannot_relocate_a_grappler(  # (#271 defect 3)
) -> None:
    """A figure locked in hand-to-hand may not be force-retreated: the rules give
    no way to shove a grappler out of a pile, and doing so would leave a cross-hex
    grapple (both figures striking each other across a gap). The push is refused,
    so the HTH lock is never torn apart, and the invariant stays satisfied."""
    arena = Arena(cols=9, rows=15)
    striker = create_human("Striker", 12, 12, "a", weapons=[BROADSWORD],
                           ready_weapon=BROADSWORD)
    grappled = create_human("Grappled", 12, 12, "b", weapons=[SHORTSWORD],
                            ready_weapon=SHORTSWORD)
    grappler = create_human("Grappler", 12, 12, "c", weapons=[SHORTSWORD],
                            ready_weapon=SHORTSWORD)
    striker.position = Hex(5, 5)
    grappled.position = LAYOUT.neighbor(Hex(5, 5), 0)
    grappler.position = LAYOUT.neighbor(Hex(5, 5), 0)     # same hex: the HTH pile
    grappled.posture = grappler.posture = Posture.PRONE
    state = GameState(arena, [striker, grappled, grappler])
    grappled.hth_opponents = [grappler.uid]              # uids assigned in __init__
    grappler.hth_opponents = [grappled.uid]
    # The standing striker hit the grounded grappler this turn (p.19: a floored
    # HTH figure counts as a rear target) -- so it is "armed" against it.
    striker.dealt_st_damage_this_turn = True
    striker.force_retreat_targets_this_turn = [grappled.uid]
    striker.hits_this_turn = 0

    assert not state.can_force_retreat(striker, grappled)  # in_hth -> forbidden
    with pytest.raises(IllegalAction):
        state.force_retreat(striker, grappled)
    # The grapple is intact and co-located, so the HTH invariant is happy.
    assert grappled.position == grappler.position
    # (melee also asserts its state invariants here; those live with melee)


def test_hth_back_to_the_wall_lets_a_frontal_grapple_through() -> None:
    # p.17 case (a): a figure may grapple a foe that has its "back to the wall" —
    # no hex to give ground into away from the attacker — even head-on against a
    # standing, equal-MA foe (which clauses b/c/d would otherwise forbid).
    from tarmar_engine.classic.data import DAGGER

    arena = Arena(cols=9, rows=15)
    layout = arena.layout
    attacker = create_human("Atk", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    defender = create_human("Def", 12, 12, "b",
                            weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    defender.position = Hex(5, 5)
    attacker.position = layout.neighbor(defender.position, 0)
    # faces attacker
    defender.facing = layout.direction_to(defender.position, attacker.position)
    attacker.facing = layout.direction_to(attacker.position, defender.position)
    state = GameState(arena, [attacker, defender])

    # Sanity: a frontal grapple on a standing equal-MA foe with open space is
    # NOT allowed (only clause (a) is in question here).
    from tarmar_engine.classic.facing import FRONT, attack_zone
    assert attack_zone(layout, attacker, defender) == FRONT
    assert defender.movement_allowance == attacker.movement_allowance
    assert defender not in state.hth_targets(attacker)

    # Wall off every hex the defender could give ground into (those farther from
    # the attacker) -> its back is to the wall.
    start = layout.distance(attacker.position, defender.position)
    arena.walls = {neighbor for neighbor in layout.neighbors(defender.position)
                   if layout.distance(attacker.position, neighbor) > start}
    assert state._has_back_to_wall(attacker, defender)
    assert defender in state.hth_targets(attacker)

    # Re-open one retreat hex: the defender is no longer pinned.
    arena.walls.pop()
    assert not state._has_back_to_wall(attacker, defender)
    assert defender not in state.hth_targets(attacker)


def test_end_turn_readies_a_dagger_drawn_in_a_grapple() -> None:
    from tarmar_engine.classic.data import DAGGER
    arena = Arena(cols=9, rows=15)
    grappler = create_human("Grappler", 12, 12, "a",
                            weapons=[BROADSWORD, DAGGER], ready_weapon=BROADSWORD)
    foe = create_human("Foe", 12, 12, "b", weapons=[BROADSWORD],
                       ready_weapon=BROADSWORD)
    grappler.position = Hex(5, 5)
    foe.position = Hex(5, 9)                       # apart, so nothing else triggers
    state = GameState(arena, [grappler, foe])
    grappler.hth_drew_dagger = True               # drew it on a 3-4 defense roll
    before = len(state.log)

    state.end_turn()
    assert grappler.ready_weapon is DAGGER         # dagger now in hand
    assert not grappler.hth_drew_dagger            # flag consumed
    assert any("readies" in line.lower() for line in state.log[before:])


def test_grapple_disabled_when_no_foe_in_reach() -> None:
    """The move menu must show 🤼 Grapple disabled (with a reason) unless there's
    an adjacent foe that can actually be grappled (#141 follow-up)."""
    from tarmar_engine.classic.data import PLATE
    arena = Arena(cols=9, rows=15)
    me = create_human("Me", 12, 12, "a", weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    foe = create_human("Foe", 12, 12, "b", weapons=[BROADSWORD],
                       ready_weapon=BROADSWORD)
    me.position = Hex(5, 5)

    # far off -> nothing to grapple
    foe.position = Hex(1, 1)
    state = GameState(arena, [me, foe])
    availability = dict(state.option_availability(me))
    assert availability[Option.HTH_ATTACK] == "no foe in reach to grapple"
    assert Option.HTH_ATTACK not in state.legal_options(me)

    # Bring the foe adjacent and make the grapple eligible (heavy armour -> lower MA,
    # p.17); now the option is available.
    foe.position = LAYOUT.neighbor(Hex(5, 5), 0)
    foe.armor = PLATE
    me.facing = LAYOUT.direction_to(me.position, foe.position)
    eligible = GameState(arena, [me, foe])
    assert dict(eligible.option_availability(me))[Option.HTH_ATTACK] is None
    assert Option.HTH_ATTACK in eligible.legal_options(me)


def test_grapple_bare_sheds_the_shield_so_it_cannot_absorb_hth_strikes() -> None:
    """Melee p.17 / ITL p.116: a figure dropped into hand-to-hand drops its ready
    weapon AND shield to the GROUND. Every HTH strike is forced to REAR and a
    slung shield stops rear hits, so leaving the shield in place let a "dropped"
    large shield keep absorbing every grapple blow; _grapple_bare must shed it
    (#251)."""
    from tarmar_engine.classic.data import BROADSWORD, LARGE_SHIELD
    arena = Arena(cols=9, rows=15)
    fighter = create_human("Shieldman", 13, 11, "a",
                           weapons=[BROADSWORD], ready_weapon=BROADSWORD,
                           shield=LARGE_SHIELD, armor=NO_ARMOR)
    fighter.position = Hex(5, 5)
    state = GameState(arena, [fighter])
    state._grapple_bare(fighter)
    assert fighter.shield.name == "None"                       # shed to the ground
    assert not fighter.shield_ready
    # A grapple strike is forced REAR; with the shield gone nothing absorbs it.
    assert fighter.hits_stopped(from_front=False, from_rear=True) == NO_ARMOR.stops
    assert "Broadsword" in [weapon.name for _, weapon in state.dropped]


def _shield_rush_setup(rusher_st, rusher_dx, foe_st, foe_dx, dice):
    """A shield-bearing rusher squarely facing an adjacent foe in its front."""
    from tarmar_engine.classic.data import DAGGER, SMALL_SHIELD
    arena = Arena(cols=9, rows=15)
    rusher = create_human("Rusher", rusher_st, rusher_dx, "a",
                          weapons=[DAGGER], ready_weapon=DAGGER,
                          shield=SMALL_SHIELD)
    foe = create_human("Foe", foe_st, foe_dx, "b",
                       weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    rusher.position = Hex(5, 5)
    rusher.facing = 0
    foe.position = Hex(5, 4)                              # straight ahead, in front
    state = GameState(arena, [rusher, foe], dice=Dice(scripted=dice))
    return state, rusher, foe


def _disengage_under_attack(foe_dx: int):
    """A runner (DX 12) disengaging from a foe that has queued a melee blow on it.

    The foe stands face-to-face (so its strike is a no-bonus FRONT attack) with
    the given DX. Returns ``(runner, foe, results)`` after the runner steps one
    hex away and combat resolves — the p.19 timing test fixture (#147).
    """
    from tarmar_engine.classic.data import RAPIER
    arena = Arena(cols=9, rows=15)
    runner = create_human("Runner", 12, 12, "a",
                          weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    # A human spends exactly 24 points on ST+DX, so vary the foe's ST against its
    # DX; a rapier (ST 9) stays legal at the low-ST/high-DX end.
    foe = create_human("Foe", 24 - foe_dx, foe_dx, "b",
                       weapons=[RAPIER], ready_weapon=RAPIER)
    runner.position = Hex(5, 5)
    runner.facing = 0
    foe.position = LAYOUT.neighbor(Hex(5, 5), 0)
    foe.facing = LAYOUT.direction_to(foe.position, runner.position)
    state = GameState(arena, [runner, foe], dice=Dice(scripted=[3] * 12))
    foe.current_option = Option.SHIFT_ATTACK
    state.queue_attack(foe, runner)                      # the foe declares its blow
    runner.current_option = Option.DISENGAGE
    # step away from the foe
    state.disengage_move(runner, LAYOUT.neighbor(Hex(5, 5), 3))
    results = state.resolve_combat()
    return runner, foe, results


def test_shield_rush_floors_a_weaker_foe_on_a_failed_save() -> None:
    # The rush is "an attack for all purposes" (p.13), so it queues and resolves
    # in adjDX order (#151): to-hit 3+3+3 connects; the ST-13 rusher rolls a save
    # vs ST-11 foe on three dice — a 15 beats the foe's adjDX 13, so it falls.
    state, rusher, foe = _shield_rush_setup(13, 11, 11, 13, [3, 3, 3, 6, 5, 4])
    assert foe in state.shield_rush_targets(rusher)
    assert state.shield_rush(rusher, foe) == "queued"     # declared, not yet resolved
    assert rusher.attacked_this_turn                      # the rush was its action
    # still up until combat resolves
    assert foe.posture == Posture.STANDING
    state.resolve_combat()
    assert foe.posture == Posture.PRONE                   # floored at the rusher's slot
    assert foe.damage_taken == 0                          # never inflicts hits


def test_shield_rush_leaves_a_foe_standing_on_a_made_save() -> None:
    # same hit, but a save of 3+3+3 = 9 is under the foe's adjDX 13 — it holds.
    state, rusher, foe = _shield_rush_setup(13, 11, 11, 13, [3, 3, 3, 3, 3, 3])
    assert state.shield_rush(rusher, foe) == "queued"
    state.resolve_combat()
    assert foe.posture == Posture.STANDING
    assert foe.damage_taken == 0


def test_shield_rush_has_no_effect_on_a_foe_over_twice_your_strength() -> None:
    state, rusher, foe = _shield_rush_setup(9, 15, 12, 12, [3, 3, 3])
    foe.strength = 25                                     # a giant, > 2x ST 9
    assert state.shield_rush(rusher, foe) == "no_effect"
    assert foe.posture == Posture.STANDING
    assert foe.damage_taken == 0


def test_shield_rush_requires_a_ready_shield() -> None:
    state, rusher, foe = _shield_rush_setup(13, 11, 11, 13, [3, 3, 3])
    rusher.shield_ready = False
    assert state.shield_rush_targets(rusher) == []
    try:
        state.shield_rush(rusher, foe)
    except IllegalAction:
        pass
    else:
        raise AssertionError("a shield-rush without a ready shield must be illegal")


def test_shield_rush_resolves_in_adjdx_order_so_a_faster_victim_strikes_first() -> None:
    """p.13/#151: the rush is an attack 'for all purposes', so it resolves in adjDX
    order. A low-DX rusher's higher-DX victim lands its own blow BEFORE it is
    knocked down — the reverse of the old immediate-rush bug."""
    from tarmar_engine.classic.data import SMALL_SHIELD
    arena = Arena(cols=9, rows=15)
    rusher = create_human("Rusher", 14, 10, "a",
                          weapons=[SHORTSWORD], ready_weapon=SHORTSWORD,
                          shield=SMALL_SHIELD)
    victim = create_human("Victim", 11, 13, "b",
                          weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    rusher.position = Hex(5, 5)
    victim.position = LAYOUT.neighbor(Hex(5, 5), 0)
    rusher.facing = LAYOUT.direction_to(rusher.position, victim.position)
    victim.facing = LAYOUT.direction_to(victim.position, rusher.position)
    # victim (DX 13) resolves first: to-hit 9 connects, shortsword damage 5 beats
    # the small shield. THEN the rusher (DX 10): to-hit 9 hits, a 6+6+6 save floors
    # the DX-13 victim — but only after its blow already landed.
    state = GameState(arena, [rusher, victim],
                      dice=Dice(scripted=[3, 3, 3, 3, 3, 3, 3, 3, 6, 6, 6, 3, 3]))
    victim.current_option = Option.SHIFT_ATTACK
    state.queue_attack(victim, rusher)                   # the faster victim declares
    # the rush is queued, not immediate
    assert state.shield_rush(rusher, victim) == "queued"
    state.resolve_combat()
    # its blow resolved (not skipped)
    assert victim.attacked_this_turn
    assert rusher.damage_taken > 0                       # and connected before it fell
    # only THEN was the DX-13 victim floored
    assert victim.posture == Posture.PRONE


def test_general_disengage_moves_one_hex_in_combat_without_attacking() -> None:
    """Option (n), p.19: at the attack step a disengaging figure moves one hex
    instead of attacking, breaking engagement, and may never attack that turn."""
    arena = Arena(cols=9, rows=15)
    runner = create_human("Runner", 12, 12, "a",
                          weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    foe = create_human("Foe", 12, 12, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD)
    runner.position = Hex(5, 5)
    runner.facing = 0
    foe.position = LAYOUT.neighbor(Hex(5, 5), 0)         # engaged, face to face
    foe.facing = LAYOUT.direction_to(foe.position, runner.position)
    state = GameState(arena, [runner, foe])
    runner.current_option = Option.DISENGAGE             # chosen in the movement phase
    dest = LAYOUT.neighbor(Hex(5, 5), 3)                 # step away from the foe
    assert dest in state.disengage_destinations(runner)
    state.disengage_move(runner, dest)
    assert runner.position == dest                       # relocated one hex
    assert runner.attacked_this_turn                     # the move replaced its attack
    try:
        state.queue_attack(runner, foe)                  # cannot also attack
    except IllegalAction:
        pass
    else:
        raise AssertionError("a disengaging figure must not be able to attack")


def test_a_prone_figure_cannot_disengage() -> None:
    arena = Arena(cols=9, rows=15)
    runner = create_human("Runner", 12, 12, "a",
                          weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    foe = create_human("Foe", 12, 12, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD)
    runner.position = Hex(5, 5)
    foe.position = Hex(8, 12)
    state = GameState(arena, [runner, foe])
    runner.current_option = Option.DISENGAGE
    runner.posture = Posture.PRONE                       # must stand up first
    assert state.disengage_destinations(runner) == []
    try:
        state.disengage_move(runner, LAYOUT.neighbor(Hex(5, 5), 3))
    except IllegalAction:
        pass
    else:
        raise AssertionError("a grounded figure must stand before it can disengage")


def test_disengage_a_higher_dx_foe_still_strikes_as_you_leave() -> None:
    """p.19: an enemy with a DX HIGHER than yours strikes as you disengage."""
    runner, _foe, results = _disengage_under_attack(foe_dx=14)
    # the DX-14 foe caught it leaving
    assert results[0].hit is True
    assert runner.damage_taken > 0


def test_disengage_a_lower_dx_foe_gets_no_strike() -> None:
    """p.19: an enemy with a LOWER DX gets no chance to strike when you flee."""
    runner, _foe, results = _disengage_under_attack(foe_dx=8)
    # the DX-8 foe whiffs — it was too slow
    assert results[0].hit is False
    # the runner takes the field unhurt
    assert runner.damage_taken == 0


def test_disengage_can_step_into_a_grapple() -> None:
    """General disengage may move onto an eligible adjacent enemy to start
    hand-to-hand combat that same turn (from #6, p.19)."""
    arena = Arena(cols=9, rows=15)
    runner = create_human("Runner", 12, 12, "a",
                          weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    from tarmar_engine.classic.data import PLATE
    # The foe engages the runner from the front; its heavy armour gives it a lower
    # MA, which is what makes the grapple-step onto it eligible (p.17). (Equal base
    # DX, so the foe's plate-lowered adjDX means no free strike on the disengage.)
    foe = create_human("Foe", 12, 12, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD,
                       armor=PLATE)
    foe.position = Hex(5, 5)
    # in the foe's front hex -> engaged
    runner.position = LAYOUT.neighbor(Hex(5, 5), 0)
    # foe faces the runner
    foe.facing = LAYOUT.direction_to(foe.position, runner.position)
    runner.facing = LAYOUT.direction_to(runner.position, foe.position)
    # The defender's fresh-grapple roll of 2 lets the hold take; 3s for any strike.
    state = GameState(arena, [runner, foe], dice=Dice(scripted=[2] + [3] * 12))
    # in the foe's front (one-directional)
    assert state.engaged(runner)

    runner.current_option = Option.DISENGAGE
    # offered as a grapple step
    assert foe.position in state.disengage_destinations(runner)
    state.disengage_move(runner, foe.position)
    assert runner.in_hth and foe.uid in runner.hth_opponents      # locked together
    assert runner.position == foe.position                        # moved onto the foe
    assert runner.posture == Posture.PRONE and foe.posture == Posture.PRONE


def test_a_disengage_whiff_narrates_no_fabricated_die_roll() -> None:
    """#270: a whiffed blow reached no to-hit roll, so its narration must not
    invent a needed/rolled clause — which in a Tarmar (roll-over d20) game would
    also print a classic roll-under number in the wrong direction. Pre-fix _whiff
    built rolled=needed+1 and the line read '(needed 12 or less, rolled 13)'."""
    from tarmar_engine.classic.narrative import narrate_attack
    runner, foe, results = _disengage_under_attack(foe_dx=8)
    whiff = results[0]
    assert whiff.hit is False and whiff.note == "whiff"
    line = narrate_attack(foe, runner, whiff)
    assert "needed" not in line and "rolled" not in line      # no fabricated roll
    assert "out of reach" in line                             # the truthful miss line
    # (melee also runs assert_log_truthful here; that invariant stays in melee)


def test_blunt_halves_a_blow_rounding_down() -> None:
    # The issue's worked example: a 6 becomes 3, a 5 becomes 2.
    assert Ruleset._blunt(6, True) == 3
    assert Ruleset._blunt(5, True) == 2
    assert Ruleset._blunt(1, True) == 0
    assert Ruleset._blunt(6, False) == 6      # a normal (un-blunted) blow is untouched


def test_practice_attack_halves_weapon_damage_before_armour() -> None:
    attacker = create_human("A", 12, 12, "a",
                            weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    bare = create_human("T", 12, 12, "b", armor=NO_ARMOR)
    # to-hit total 8 (a normal hit, no crit); broadsword 2d rolls 5+4 = 9 raw.
    script = [2, 3, 3, 5, 4]
    normal = RULES.resolve_attack(Dice(scripted=script), attacker, bare, zone=FRONT)
    blunt = RULES.resolve_attack(Dice(scripted=script), attacker, bare, zone=FRONT,
                                 blunted=True)
    assert normal.damage == 9
    assert blunt.damage == 4                  # 9 // 2, rounded down

    # Armour still stops hits — off the already-halved 4 (leather stops 2 -> 2).
    armoured = create_human("T", 12, 12, "b", armor=LEATHER)
    blunt_vs_armour = RULES.resolve_attack(
        Dice(scripted=script), attacker, armoured, zone=FRONT, blunted=True)
    assert blunt_vs_armour.damage == 2


def _archer_state(combat_type: CombatType) -> tuple[GameState, object]:
    arena = Arena(cols=9, rows=15)
    archer = create_human("Archer", 9, 15, "a",
                          weapons=[SMALL_BOW], ready_weapon=SMALL_BOW, armor=NO_ARMOR)
    foe = create_human("Foe", 12, 12, "b",
                       weapons=[SHORTSWORD], ready_weapon=SHORTSWORD, armor=NO_ARMOR)
    archer.position = Hex(5, 5)
    foe.position = Hex(5, 9)                   # four hexes off: in range, not engaged
    _aim(archer, foe)
    state = GameState(arena, [archer, foe], combat_type=combat_type)
    return state, archer


def test_a_missile_can_be_fired_in_a_normal_bout() -> None:
    state, archer = _archer_state(CombatType.DEATH)
    assert not state.practice
    assert Option.MISSILE_ATTACK in state.legal_options(archer)


def test_practice_bout_offers_no_missile_attack() -> None:
    state, archer = _archer_state(CombatType.PRACTICE)
    assert state.practice
    assert Option.MISSILE_ATTACK not in state.legal_options(archer)
    reasons = dict(state.option_availability(archer))
    assert reasons[Option.MISSILE_ATTACK] == "no missiles in a practice bout"


def test_practice_drop_out_at_low_strength() -> None:
    arena = Arena(cols=9, rows=15)
    layout = arena.layout
    attacker = create_human("Atk", 12, 12, "a",
                            weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    target = create_human("Tgt", 12, 12, "b", armor=NO_ARMOR)
    # worn down to ST 4, one hit from dropping out
    target.damage_taken = 8
    attacker.position = Hex(5, 5)
    target.position = layout.neighbor(Hex(5, 5), 0)
    attacker.facing = layout.direction_to(attacker.position, target.position)
    target.facing = layout.direction_to(target.position, attacker.position)
    # to-hit total 9 (normal hit); broadsword 2d rolls 1+1 = 2 raw, blunted -> 1.
    state = GameState(arena, [attacker, target],
                      dice=Dice(scripted=[3, 3, 3, 1, 1]),
                      combat_type=CombatType.PRACTICE)
    attacker.current_option = Option.SHIFT_ATTACK
    state.queue_attack(attacker, target)
    result = state.resolve_combat()[0]

    assert result.hit and result.damage == 1
    assert target.current_st == PRACTICE_DROPOUT_ST   # exactly 3
    assert target.dropped_out
    assert target.collapsed and not target.is_dead    # out of the fight, but alive
    assert target.posture == Posture.PRONE
    assert any("drops out" in line for line in state.log)
    # the bout is decided once it drops out
    assert state.victor() == "a"


def test_no_drop_out_in_a_normal_bout() -> None:
    arena = Arena(cols=9, rows=15)
    layout = arena.layout
    attacker = create_human("Atk", 12, 12, "a",
                            weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    target = create_human("Tgt", 12, 12, "b", armor=NO_ARMOR)
    target.damage_taken = 8                   # ST 4
    attacker.position = Hex(5, 5)
    target.position = layout.neighbor(Hex(5, 5), 0)
    attacker.facing = layout.direction_to(attacker.position, target.position)
    target.facing = layout.direction_to(target.position, attacker.position)
    state = GameState(arena, [attacker, target],
                      dice=Dice(scripted=[3, 3, 3, 1, 1]),
                      combat_type=CombatType.DEATH)
    attacker.current_option = Option.SHIFT_ATTACK
    state.queue_attack(attacker, target)
    state.resolve_combat()
    # ST 2, still up and fighting
    assert not target.dropped_out and not target.collapsed
    assert state.victor() is None

