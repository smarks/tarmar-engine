"""
Reproduction of the rulebook Combat Example (p.23-24): Flavius vs Wulf.

This is the engine's gold-standard validation -- the same nine-turn fight the
booklet narrates, driven through the real GameState with the exact dice the
rulebook rolls, asserting the documented hits and remaining ST after each turn.

Flavius: ST 12, DX 12, Roman armor (= chainmail, stops 3, -3 DX), large shield
(stops 2, -1 DX), gladius (= shortsword). adjDX 8, stops 5 frontally, MA 6.
Wulf: ST 14, DX 10, no armor, longbow then a two-handed sword. adjDX 10.

To keep the test about combat math (movement is covered elsewhere), the two are
kept adjacent and face one another; each turn's option is set directly and the
rulebook's dice are fed in resolution order.

Imported verbatim from melee's ``engine/tests/test_combat_example.py``
(milestone 3 of the battle/melee unification): only the imports are adapted to
``tarmar_engine.classic``; every expectation is untouched. This test is the
classic profile's acceptance bar.
"""
from __future__ import annotations

from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    CHAINMAIL,
    LARGE_SHIELD,
    LONGBOW,
    NO_ARMOR,
    SHORTSWORD,
    TWO_HANDED_SWORD,
)
from tarmar_engine.classic.figure import create_human
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.state import GameState


def _aim(state: GameState, a, b) -> None:
    """Keep the duellists adjacent and facing each other."""
    a.facing = state.arena.layout.direction_to(a.position, b.position)
    b.facing = state.arena.layout.direction_to(b.position, a.position)


def test_flavius_versus_wulf_full_combat_example() -> None:
    arena = Arena(cols=9, rows=15)
    dice = Dice()
    flavius = create_human(
        "Flavius", 12, 12, "rome",
        armor=CHAINMAIL, shield=LARGE_SHIELD,
        weapons=[SHORTSWORD], ready_weapon=SHORTSWORD,
    )
    wulf = create_human(
        "Wulf", 14, 10, "tribe", armor=NO_ARMOR,
        weapons=[LONGBOW, TWO_HANDED_SWORD], ready_weapon=LONGBOW,
    )
    flavius.position = Hex(5, 8)
    wulf.position = arena.layout.neighbor(Hex(5, 8), 0)
    state = GameState(arena, [flavius, wulf], dice=dice)

    assert flavius.base_adj_dx == 8
    assert wulf.base_adj_dx == 10

    # --- Turn 1: Wulf's longbow hits (9), 1d+2 rolls 5 -> 7, armor+shield stop 5
    _aim(state, flavius, wulf)
    wulf.current_option = Option.MISSILE_ATTACK
    dice.feed(3, 3, 3, 5)               # to-hit total 9, damage die 5
    state.queue_attack(wulf, flavius)
    state.resolve_combat()
    assert flavius.damage_taken == 2    # 7 - 5
    assert flavius.current_st == 10
    state.end_turn()

    # --- Turn 2: Flavius dodges; Wulf's bow misses on four dice (16)
    _aim(state, flavius, wulf)
    flavius.dodging = True
    wulf.current_option = Option.MISSILE_ATTACK
    dice.feed(6, 6, 3, 1)               # four-dice total 16 -> miss
    state.queue_attack(wulf, flavius)
    results = state.resolve_combat()
    assert results[0].dice_count == 4 and not results[0].hit
    assert flavius.damage_taken == 2
    state.end_turn()

    # --- Turn 3: Wulf's last shot hits (8), 1d+2 rolls 2 -> 4, armor stops all
    _aim(state, flavius, wulf)
    wulf.current_option = Option.ONE_LAST_SHOT
    dice.feed(3, 3, 2, 2)               # to-hit total 8, damage die 2 -> 4
    state.queue_attack(wulf, flavius)
    state.resolve_combat()
    assert flavius.damage_taken == 2    # 4 - 5, no new damage
    state.end_turn()

    # --- Turn 4: Wulf readies the two-handed sword (no attack); Flavius misses (16)
    _aim(state, flavius, wulf)
    wulf.ready_weapon = TWO_HANDED_SWORD
    flavius.current_option = Option.SHIFT_ATTACK
    dice.feed(6, 6, 4)                  # total 16 -> miss
    state.queue_attack(flavius, wulf)
    state.resolve_combat()
    assert wulf.damage_taken == 0
    state.end_turn()

    # --- Turn 5: Wulf misses (13); Flavius hits (8), shortsword 2d-1 rolls 7 -> 6
    _aim(state, flavius, wulf)
    flavius.current_option = Option.SHIFT_ATTACK
    wulf.current_option = Option.SHIFT_ATTACK
    # Flavius adjDX 8 vs Wulf adjDX 10 -> Wulf strikes first
    dice.feed(6, 4, 3)                  # Wulf to-hit total 13 -> miss
    dice.feed(3, 3, 2, 3, 4)            # Flavius to-hit total 8, dmg 2d (3,4)=7 -> 6
    state.queue_attack(flavius, wulf)
    state.queue_attack(wulf, flavius)
    state.resolve_combat()
    assert wulf.damage_taken == 6
    assert wulf.current_st == 8
    state.end_turn()
    assert wulf.wounded_last_turn       # took 6 -> -2 next turn

    # --- Turn 6: Flavius (8) misses first; Wulf (8, wounded) rolls 4 -> double,
    #             3d-1 rolls 6 -> 5 doubled = 10, armor+shield stop 5 -> 5
    _aim(state, flavius, wulf)
    flavius.current_option = Option.SHIFT_ATTACK
    wulf.current_option = Option.SHIFT_ATTACK
    assert wulf.base_adj_dx + wulf.wound_dx_penalty() == 8   # 10 - 2
    dice.feed(4, 3, 3)                  # Flavius to-hit total 10 -> miss
    dice.feed(1, 1, 2, 2, 2, 2)    # Wulf to-hit 4 (double), dmg 3d (2,2,2)=6 ->5
    state.queue_attack(flavius, wulf)   # tie at adjDX 8 -> declared first strikes first
    state.queue_attack(wulf, flavius)
    state.resolve_combat()
    assert flavius.damage_taken == 7    # 2 + 5
    assert flavius.current_st == 5
    state.end_turn()
    assert flavius.wounded_last_turn

    # --- Turn 7: Flavius defends (adjDX 6, -2); Wulf's swing misses on four dice
    _aim(state, flavius, wulf)
    assert flavius.base_adj_dx + flavius.wound_dx_penalty() == 6
    flavius.defending = True              # engaged: defend forces 4 dice in melee
    wulf.current_option = Option.SHIFT_ATTACK
    dice.feed(5, 5, 4, 2)              # four-dice total 16 -> miss
    state.queue_attack(wulf, flavius)
    state.resolve_combat()
    assert flavius.damage_taken == 7
    state.end_turn()

    # --- Turn 8: Wulf (10) misses (13); Flavius (8) hits (6), 2d-1 rolls 8 -> 7
    _aim(state, flavius, wulf)
    flavius.current_option = Option.SHIFT_ATTACK
    wulf.current_option = Option.SHIFT_ATTACK
    dice.feed(6, 4, 3)                 # Wulf (higher adjDX) first: total 13 -> miss
    dice.feed(2, 2, 2, 4, 4)          # Flavius to-hit total 6, dmg 2d (4,4)=8 -> 7
    state.queue_attack(flavius, wulf)
    state.queue_attack(wulf, flavius)
    state.resolve_combat()
    assert wulf.damage_taken == 13    # 6 + 7
    assert wulf.current_st == 1
    # Flavius hit and took nothing -> forces Wulf back and follows
    assert state.can_force_retreat(flavius, wulf)
    state.force_retreat(flavius, wulf, advance=True)
    state.end_turn()
    assert wulf.wounded_last_turn

    # --- Turn 9: Wulf's adjDX is 5 (-2 hits, -3 low ST); Flavius hits (7) and kills
    _aim(state, flavius, wulf)
    assert wulf.base_adj_dx + wulf.wound_dx_penalty() == 5   # 10 - 2 - 3
    flavius.current_option = Option.SHIFT_ATTACK
    dice.feed(3, 2, 2, 3, 3)          # to-hit total 7, dmg 2d (3,3)=6 -> 5
    state.queue_attack(flavius, wulf)
    state.resolve_combat()
    assert wulf.is_dead
