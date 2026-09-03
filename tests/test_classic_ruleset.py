"""Swapping classic mechanics via a custom Ruleset.

Ported verbatim from melee's ``engine/tests/test_ruleset.py`` (imports
adapted; expectations untouched): the proof that classic mechanics are
pluggable — each subclass overrides a single focused hook and drives the
*same* GameState/turn engine to a different outcome.
"""
from __future__ import annotations

from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import DEFAULT_LAYOUT as LAYOUT
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import BROADSWORD, CHAINMAIL
from tarmar_engine.classic.figure import Posture, create_human
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.ruleset import KNOCKDOWN, Ruleset
from tarmar_engine.classic.state import GameState


def _duel(ruleset: Ruleset | None, dice: Dice, *, target_armor=CHAINMAIL):
    arena = Arena(cols=9, rows=15)
    attacker = create_human("A", 12, 12, "a",
                            weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    target = create_human("B", 12, 12, "b", armor=target_armor)
    attacker.position = Hex(5, 5)
    target.position = LAYOUT.neighbor(Hex(5, 5), 0)
    attacker.facing = LAYOUT.direction_to(attacker.position, target.position)
    target.facing = LAYOUT.direction_to(target.position, attacker.position)
    state = GameState(arena, [attacker, target], dice=dice, ruleset=ruleset)
    return state, attacker, target


def _hit_once(state: GameState, attacker, target) -> None:
    attacker.current_option = Option.SHIFT_ATTACK
    state.queue_attack(attacker, target)
    state.resolve_combat()


def test_default_ruleset_when_none_supplied() -> None:
    state, _, _ = _duel(None, Dice())
    assert isinstance(state.rules, Ruleset)


def test_swap_armor_mechanic_ignore_armor() -> None:
    class IgnoreArmor(Ruleset):
        def absorbed(self, target, *, zone):
            return 0

    # broadsword 2d -> (4,3)=7 raw, to-hit total 8 (<=12). Chainmail stops 3.
    script = [2, 3, 3, 4, 3]
    default_state, a1, b1 = _duel(None, Dice(scripted=list(script)))
    _hit_once(default_state, a1, b1)
    assert b1.damage_taken == 4            # 7 - 3 armor

    custom_state, a2, b2 = _duel(IgnoreArmor(), Dice(scripted=list(script)))
    _hit_once(custom_state, a2, b2)
    assert b2.damage_taken == 7            # armor ignored


def test_swap_to_hit_table_always_crits() -> None:
    class AlwaysTriple(Ruleset):
        def classify_roll(self, rolled, dice_count, needed):
            return (True, 3, False, False)

    # A normally-missing total of 15 (>12) lands, tripling the broadsword.
    state, attacker, target = _duel(
        AlwaysTriple(), Dice(scripted=[6, 6, 3, 6, 6]), target_armor=CHAINMAIL
    )
    _hit_once(state, attacker, target)
    assert target.damage_taken == 12 * 3 - 3   # (2d=12)*3 - chainmail 3


def test_swap_injury_thresholds_easier_knockdown() -> None:
    class GlassJaw(Ruleset):
        def status_after_hit(self, target):
            if target.hits_this_turn >= 4:
                return KNOCKDOWN
            return super().status_after_hit(target)

    # 5 hits would not knock down under classic rules (threshold 8).
    state, attacker, target = _duel(
        GlassJaw(), Dice(scripted=[2, 3, 3, 4, 4])  # to-hit 8, 2d=8 -> 8-3=5
    )
    _hit_once(state, attacker, target)
    assert target.hits_this_turn == 5
    assert target.posture == Posture.PRONE      # felled by the swapped threshold


def test_swap_movement_economy() -> None:
    class Sprint(Ruleset):
        def movement_budget(self, movement_allowance, option_cap):
            # every option gets the full movement allowance
            return movement_allowance

    arena = Arena(cols=9, rows=15)
    runner = create_human("R", 12, 12, "a")
    runner.position = Hex(5, 8)
    runner.facing = 0
    default = GameState(arena, [runner], dice=Dice())
    sprinter = GameState(arena, [runner], dice=Dice(), ruleset=Sprint())

    # under classic rules a charge is capped at half MA; sprint gives full MA
    default_reach = max(arena.distance(runner.position, h)
                        for h in default.reachable(runner, Option.CHARGE_ATTACK))
    sprint_reach = max(arena.distance(runner.position, h)
                       for h in sprinter.reachable(runner, Option.CHARGE_ATTACK))
    assert sprint_reach > default_reach
