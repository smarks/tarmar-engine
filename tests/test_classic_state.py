"""Classic turn engine: combat ordering, force retreat, injury (Section IV).

Ported from melee's ``engine/tests/test_state.py`` — the tests that pin the
classic behaviors the Combat Example does not itself cover (attack ordering,
posture-gated fire, the forced-retreat edge cases, the reactions-to-injury
flags). Imports adapted to ``tarmar_engine.classic``; expectations untouched.
The melee tests exercising subsystems this milestone deliberately leaves
behind (hand-to-hand piles, shield rush, spells, flight-lane blockers with
practice bouts) stay in melee until milestone 4.
"""
# pyright: reportArgumentType=false
# (ported melee tests place figures then use positions; Optional stays as-is)
from __future__ import annotations

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import DEFAULT_LAYOUT as LAYOUT
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    BROADSWORD,
    LIGHT_CROSSBOW,
    NO_ARMOR,
    SHORTSWORD,
    SMALL_BOW,
    max_missile_shots,
)
from tarmar_engine.classic.figure import Posture, create_human
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.state import GameState, IllegalAction


def _aim(figure, target) -> None:
    """Face ``figure`` toward ``target`` (a shooter aims along the line of fire)."""
    figure.facing = LAYOUT.direction_to(
        figure.position, LAYOUT.line(figure.position, target.position)[1])


def _duel(dice=None):
    arena = Arena(cols=9, rows=15)
    a = create_human("A", 12, 12, "a", weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    b = create_human("B", 12, 12, "b", weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    a.position = Hex(5, 5)
    b.position = LAYOUT.neighbor(Hex(5, 5), 0)
    a.facing = LAYOUT.direction_to(a.position, b.position)
    b.facing = LAYOUT.direction_to(b.position, a.position)
    state = GameState(arena, [a, b], dice=dice or Dice())
    return state, a, b


def test_drop_prone_to_fire_a_crossbow_at_plus_one() -> None:
    arena = Arena(cols=9, rows=15)
    shooter = create_human("Bow", 12, 12, "a",
                           weapons=[LIGHT_CROSSBOW], ready_weapon=LIGHT_CROSSBOW)
    foe = create_human("Foe", 12, 12, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD)
    shooter.position = Hex(5, 5)
    foe.position = Hex(5, 9)
    _aim(shooter, foe)  # aim along the line of fire
    state = GameState(arena, [shooter, foe])
    assert Option.GO_PRONE in state.legal_options(shooter)  # a missile holder
    state.move(shooter, Option.GO_PRONE)
    assert shooter.posture == Posture.PRONE
    shooter.current_option = Option.MISSILE_ATTACK
    state.queue_attack(shooter, foe)
    assert "+1 prone" in state.resolve_combat()[0].to_hit_breakdown


def test_stand_down_clears_the_attack_option_and_cancels_a_queued_shot() -> None:
    # stand_down is the combat-phase "hold fire" — a committed attacker flips
    # to DO_NOTHING and any shot it already queued this step is cancelled,
    # without re-running movement.
    arena = Arena(cols=9, rows=15)
    shooter = create_human("Bow", 12, 12, "a",
                           weapons=[LIGHT_CROSSBOW], ready_weapon=LIGHT_CROSSBOW)
    foe = create_human("Foe", 12, 12, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD)
    shooter.position = Hex(5, 5)
    foe.position = Hex(5, 9)
    _aim(shooter, foe)
    state = GameState(arena, [shooter, foe])
    shooter.current_option = Option.MISSILE_ATTACK
    state.queue_attack(shooter, foe)
    assert any(pending.attacker is shooter for pending in state._pending)

    state.stand_down(shooter)
    assert shooter.current_option == Option.DO_NOTHING
    assert not any(pending.attacker is shooter for pending in state._pending)
    # A stood-down figure holds its position — no movement was re-run.
    assert shooter.position == Hex(5, 5)


def test_a_missile_only_figure_cannot_defend() -> None:
    # p.20: "A figure may only defend with a non-missile weapon ready, to parry."
    arena = Arena(cols=9, rows=15)
    archer = create_human("Archer", 9, 15, "a", weapons=[SMALL_BOW],
                          ready_weapon=SMALL_BOW, armor=NO_ARMOR)
    foe = create_human("Foe", 14, 10, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD, armor=NO_ARMOR)
    archer.position = Hex(5, 5)
    archer.facing = 0
    foe.position = LAYOUT.neighbor(Hex(5, 5), 0)
    foe.facing = 3    # adjacent, engaged
    state = GameState(arena, [archer, foe])
    assert Option.SHIFT_DEFEND not in state.legal_options(archer)   # only a bow ready
    assert dict(state.option_availability(archer))[Option.SHIFT_DEFEND] is not None
    assert Option.SHIFT_DEFEND in state.legal_options(foe)  # a swordsman may parry


def test_one_last_shot_looses_a_single_arrow() -> None:
    # p.7 option l: One Last Shot is *one* shot, even for a bow that gets two
    # on unhindered fire (p.14, option f).
    arena = Arena(cols=9, rows=15)
    archer = create_human("Archer", 9, 15, "a", weapons=[SMALL_BOW],
                          ready_weapon=SMALL_BOW, armor=NO_ARMOR)
    foe = create_human("Foe", 14, 10, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD, armor=NO_ARMOR)
    archer.position = Hex(5, 5)
    foe.position = LAYOUT.neighbor(Hex(5, 5), 0)   # adjacent -> the parting shot
    _aim(archer, foe)
    state = GameState(arena, [archer, foe])
    assert max_missile_shots(SMALL_BOW, archer.base_adj_dx) == 2    # two on option f
    archer.current_option = Option.ONE_LAST_SHOT
    state.queue_attack(archer, foe)
    assert state._pending[0].shots == 1                    # but the parting shot is one


def test_a_missile_hit_does_not_arm_a_force_retreat() -> None:
    # p.20: "missile or thrown weapon hits ... don't count" toward forcing a retreat.
    arena = Arena(cols=9, rows=15)
    archer = create_human("Archer", 9, 15, "a", weapons=[SMALL_BOW],
                          ready_weapon=SMALL_BOW, armor=NO_ARMOR)
    foe = create_human("Foe", 14, 10, "b", weapons=[SHORTSWORD],
                       ready_weapon=SHORTSWORD, armor=NO_ARMOR)
    archer.position = Hex(5, 5)
    foe.position = LAYOUT.neighbor(Hex(5, 5), 0)            # adjacent
    _aim(archer, foe)
    state = GameState(arena, [archer, foe], dice=Dice(scripted=[1, 1, 1] + [3] * 12))
    archer.current_option = Option.ONE_LAST_SHOT
    state.queue_attack(archer, foe)
    result = state.resolve_combat()[0]
    assert result.hit and result.damage > 0                # the arrow landed and hurt
    assert not state.can_force_retreat(archer, foe)  # a missile hit doesn't arm it


def test_initiative_order_is_adjdx_desc_then_uid() -> None:
    # Per-character initiative selection: order by adjusted DX highest
    # first, ties broken by uid — deterministic, and drawing zero dice.
    from tarmar_engine.classic.data import DAGGER
    arena = Arena(cols=9, rows=15)
    fast = create_human("Fast", 10, 14, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    slow = create_human("Slow", 14, 10, "b", weapons=[DAGGER], ready_weapon=DAGGER)
    tie_a = create_human("TieA", 12, 12, "a", weapons=[DAGGER], ready_weapon=DAGGER)
    tie_b = create_human("TieB", 12, 12, "b", weapons=[DAGGER], ready_weapon=DAGGER)
    state = GameState(arena, [slow, tie_b, fast, tie_a])
    by_uid = {f.name: f.uid for f in state.figures}
    order = state.initiative()
    # Fast (DX 14) first, Slow (DX 10) last; the two DX-12 figures between
    # them in uid order (tie_a was created before tie_b -> f2 < f3).
    assert order[0] == by_uid["Fast"]
    assert order[-1] == by_uid["Slow"]
    assert order[1:3] == sorted([by_uid["TieA"], by_uid["TieB"]])


def test_attack_ordering_is_highest_adjdx_first() -> None:
    # Both declared, but 'a' has higher adjDX and lands a lethal triple before
    # 'b' (lower adjDX) gets to strike, so 'b''s attack never resolves.
    state, a, b = _duel(Dice(scripted=[1, 1, 1, 6, 6]))  # a: total 3 -> triple, 12x3
    b.wounded_last_turn = True  # -2 DX, so 'b' is slower
    a.current_option = Option.SHIFT_ATTACK
    b.current_option = Option.SHIFT_ATTACK
    state.queue_attack(b, a)   # declared first, but lower adjDX
    state.queue_attack(a, b)   # higher adjDX -> resolves first
    results = state.resolve_combat()
    assert len(results) == 1            # 'b' was slain before it could strike
    assert b.is_dead
    assert a.damage_taken == 0


def test_knockdown_on_eight_plus_hits() -> None:
    # 8 hits in one turn fells (but does not kill) the unarmored target.
    state, a, b = _duel(Dice(scripted=[
        2, 3, 3,   # a to-hit total 8 -> hit
        4, 4,      # broadsword 2d = 8, b unarmored -> 8 hits, ST 12 -> 4
    ]))
    a.current_option = Option.SHIFT_ATTACK
    state.queue_attack(a, b)
    state.resolve_combat()
    assert b.hits_this_turn == 8
    assert not b.collapsed
    assert b.posture == Posture.PRONE


def test_force_retreat_pushes_enemy_and_can_advance() -> None:
    state, a, b = _duel(Dice(scripted=[2, 3, 3, 5, 4]))  # a hits b for some ST
    a.current_option = Option.SHIFT_ATTACK
    state.queue_attack(a, b)
    state.resolve_combat()
    assert a.dealt_st_damage_this_turn and a.hits_this_turn == 0
    vacated = b.position
    new_pos = state.force_retreat(a, b, advance=True)
    assert state.arena.distance(a.position, new_pos) == 1
    assert a.position == vacated  # advanced into the vacated hex


def test_force_retreat_breaks_ties_deterministically() -> None:
    """With several legal retreat hexes the choice is stable: the hex furthest
    from the attacker, settled on (col, row) — never dependent on neighbour-
    iteration or set ordering."""
    state, attacker, target = _duel()
    # Arm a force retreat directly, isolating the destination choice from combat.
    attacker.dealt_st_damage_this_turn = True
    attacker.force_retreat_targets_this_turn = [target.uid]
    attacker.hits_this_turn = 0
    assert state.can_force_retreat(attacker, target)

    layout = state.arena.layout
    start_distance = layout.distance(attacker.position, target.position)
    occupied = set(state.occupied(exclude=target))
    candidates = [
        hex_position
        for hex_position in state.arena.neighbors(target.position)
        if hex_position not in occupied
        and layout.distance(attacker.position, hex_position) > start_distance
    ]
    assert len(candidates) > 1                       # the multi-candidate case

    def tie_break_key(hex_position):
        return (layout.distance(attacker.position, hex_position),
                hex_position.col, hex_position.row)

    expected = max(candidates, key=tie_break_key)
    destination = state.force_retreat(attacker, target)
    assert destination == expected
    # Furthest hex, and order-independent (reversing the candidate list is same).
    assert (layout.distance(attacker.position, destination)
            == max(layout.distance(attacker.position, c) for c in candidates))
    assert destination == max(reversed(candidates), key=tie_break_key)


def test_force_retreat_is_spent_and_cannot_chain() -> None:
    """One qualifying melee hit grants exactly ONE push, even with advance=True.

    p.20 grants "force the enemy to retreat one hex at the end of the turn" --
    a single shove, not an unbounded walk.
    """
    state, attacker, target = _duel(Dice(scripted=[2, 3, 3, 5, 4]))  # a clean hit
    attacker.current_option = Option.SHIFT_ATTACK
    state.queue_attack(attacker, target)
    state.resolve_combat()
    assert state.can_force_retreat(attacker, target)          # armed by the hit
    state.force_retreat(attacker, target, advance=True)       # spend the one push
    assert not state.can_force_retreat(attacker, target)      # ...and it is gone
    assert target.uid not in attacker.force_retreat_targets_this_turn
    with pytest.raises(IllegalAction):                        # no second shove
        state.force_retreat(attacker, target, advance=True)


def test_force_retreat_rejects_targets_the_menu_never_offers() -> None:
    """Execution mirrors the menu: only a living, opposing foe the attacker
    actually struck this turn can be pushed -- never a teammate, an untouched
    enemy, or a fallen body."""
    arena = Arena(cols=9, rows=15)
    attacker = create_human("Atk", 12, 12, "a", weapons=[BROADSWORD],
                            ready_weapon=BROADSWORD)
    struck_foe = create_human("Struck", 12, 12, "b", weapons=[BROADSWORD],
                              ready_weapon=BROADSWORD)
    other_foe = create_human("Other", 12, 12, "b", weapons=[BROADSWORD],
                             ready_weapon=BROADSWORD)
    teammate = create_human("Mate", 12, 12, "a", weapons=[BROADSWORD],
                            ready_weapon=BROADSWORD)
    attacker.position = Hex(5, 5)
    struck_foe.position = LAYOUT.neighbor(Hex(5, 5), 0)
    other_foe.position = LAYOUT.neighbor(Hex(5, 5), 2)
    teammate.position = LAYOUT.neighbor(Hex(5, 5), 4)
    state = GameState(arena, [attacker, struck_foe, other_foe, teammate])
    # The attacker dealt qualifying melee damage to struck_foe only.
    attacker.dealt_st_damage_this_turn = True
    attacker.force_retreat_targets_this_turn = [struck_foe.uid]
    attacker.hits_this_turn = 0

    assert state.can_force_retreat(attacker, struck_foe)      # the one it may push
    # Everything the menu (enemies_of + can_force_retreat) never offers is refused:
    for illegal_target in (teammate, other_foe):
        assert not state.can_force_retreat(attacker, illegal_target)
        with pytest.raises(IllegalAction):
            state.force_retreat(attacker, illegal_target)
    # A struck foe knocked unconscious this turn is a fallen body: no longer pushable.
    struck_foe.unconscious = True
    struck_foe.damage_taken = struck_foe.strength         # ST 0 -> collapsed
    assert struck_foe.collapsed
    assert not state.can_force_retreat(attacker, struck_foe)
    with pytest.raises(IllegalAction):
        state.force_retreat(attacker, struck_foe)


def test_end_turn_rolls_wound_flag_forward() -> None:
    state, a, b = _duel()
    b.hits_this_turn = 6
    state.end_turn()
    assert b.wounded_last_turn  # 5+ hits last turn -> -2 next turn
    assert b.hits_this_turn == 0


def test_one_attack_per_turn_rejects_a_second_declaration() -> None:
    # Section VII: a figure attacks once per turn. A second declaration — whether
    # queued in the same combat phase or attempted after resolving — is illegal.
    state, a, b = _duel(Dice(scripted=[3] * 12))
    a.current_option = Option.SHIFT_ATTACK
    state.queue_attack(a, b)

    with pytest.raises(IllegalAction):
        state.queue_attack(a, b)                  # already queued this phase

    assert len(state.resolve_combat()) == 1       # exactly one swing resolves
    assert a.attacked_this_turn

    with pytest.raises(IllegalAction):
        state.queue_attack(a, b)                  # already attacked this turn
