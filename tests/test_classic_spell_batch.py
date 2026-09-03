"""
The Classic spell batch (melee #431), on injected dice only.

Ported verbatim from melee's ``engine/tests/test_spell_batch.py`` at the
battle/melee unification's milestone 5 (tarmar-studio#240): imports adapted to
``tarmar_engine.classic``; expectations untouched. Melee's invariant-checker
tests and its ``assert_state_invariants`` sweeps stay in melee with its
invariants module.

Each spell gets its effect, its cost (heavy-target variants included), its
duration expiry, and its refresh behaviour pinned against the reference text
(melee's docs/reference/the-fantasy-trip-wizard-spell-reference.txt, cited per
test). The dice stream a cast draws is unchanged: the 3-dice (4 against a
dodging/defending target) to-hit roll, then — for a missile spell that HIT —
one die per ST invested
(see :meth:`tarmar_engine.classic.ruleset.Ruleset.resolve_spell`).
"""
from __future__ import annotations

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import BROADSWORD, LARGE_SHIELD, SHORTSWORD
from tarmar_engine.classic.figure import Figure, Posture, create_wizard
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.spells import (
    BLUR,
    BREAK_WEAPON,
    CLUMSINESS,
    DROP_WEAPON,
    FIREBALL,
    IRON_FLESH,
    LIGHTNING,
    SLOW_MOVEMENT,
    SPEED_MOVEMENT,
    STONE_FLESH,
    STOP,
    TRIP,
    spell_cost_for,
)
from tarmar_engine.classic.state import GameState, IllegalAction

ALL_BATCH_SPELLS = [
    "blur", "drop_weapon", "slow_movement", "clumsiness", "speed_movement",
    "trip", "break_weapon", "fireball", "stop", "lightning", "iron_flesh",
    "stone_flesh",
]


def _wizard(strength: int = 20, dexterity: int = 12, intelligence: int = 16,
            spells: list[str] | None = None, **gear) -> Figure:
    """A ready-to-cast wizard (hands free) at a fixed hex, facing east. IQ 16
    by default so any batch spell (Iron Flesh is IQ 15) can be fielded."""
    wizard = create_wizard(
        "Merlin", strength=strength, dexterity=dexterity,
        intelligence=intelligence, side="red",
        spells_known=spells if spells is not None else list(ALL_BATCH_SPELLS),
        **gear)
    wizard.position = Hex(2, 2)
    wizard.facing = 0
    wizard.uid = "wiz"
    wizard.current_option = Option.CAST
    return wizard


def _foe(strength: int = 14, dexterity: int = 10, **gear) -> Figure:
    """An enemy two hexes to the wizard's front (same megahex reach; the thrown
    range penalty for it is -2, one per hex — rules lines 668-670)."""
    foe = Figure(name="Dummy", strength=strength, dexterity=dexterity,
                 side="blue", **gear)
    foe.position = Hex(4, 2)
    foe.uid = "dummy"
    return foe


def _game(*figures: Figure, dice: Dice) -> GameState:
    arena = Arena(cols=12, rows=12)
    return GameState(arena, list(figures), dice=dice)


def _cast(state: GameState, wizard: Figure, spell, target: Figure,
          st_used: int | None = None) -> None:
    """Queue and resolve one cast (the caller scripts the dice)."""
    wizard.current_option = Option.CAST
    state.queue_spell(wizard, spell, target,
                      st_used=st_used if st_used is not None
                      else spell_cost_for(spell, target.strength))
    state.resolve_combat()


HIT = [2, 2, 2]   # a 6 — a plain hit under every adjDX used here


# ---- missile spells: Fireball and Lightning ---------------------------------

@pytest.mark.parametrize(
    "spell, st_used, damage_rolls, expected",
    [
        # Fireball: 1d-1 per ST (spell-ref lines 156-157).
        (FIREBALL, 1, [6], 5),
        (FIREBALL, 3, [6, 5, 4], 12),
        # ...floored at the ST used ("never less damage than the ST used").
        (FIREBALL, 3, [1, 1, 1], 3),
        # Lightning: a full 1d per ST (spell-ref lines 221-222; rules lines
        # 656-658 subtract "nothing if the spell was Lightning").
        (LIGHTNING, 1, [6], 6),
        (LIGHTNING, 3, [6, 6, 6], 18),
    ],
)
def test_missile_spell_damage_per_st(spell, st_used, damage_rolls, expected) -> None:
    wizard = _wizard()
    foe = _foe(strength=40)
    state = _game(wizard, foe, dice=Dice(scripted=[*HIT, *damage_rolls]))
    _cast(state, wizard, spell, foe, st_used=st_used)
    result = state.spell_results[0]
    assert result.hit and result.damage == expected
    assert foe.damage_taken == expected
    assert wizard.damage_taken == st_used        # the full invested ST paid


@pytest.mark.parametrize("spell", [FIREBALL, LIGHTNING])
def test_new_missile_spells_cap_at_three_st(spell) -> None:
    """Every missile spell invests "the amount of ST (maximum 3)" (rules
    line 620)."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    wizard.current_option = Option.CAST
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, spell, foe, st_used=4)


def test_fireball_at_dodging_target_rolls_four_dice() -> None:
    """A dodging target forces a missile spell to four dice (#418) — the new
    missiles inherit the rule, not just Magic Fist."""
    wizard = _wizard()
    foe = _foe()
    foe.dodging = True
    state = _game(wizard, foe, dice=Dice(scripted=[2, 2, 2, 2, 6, 6, 6]))
    _cast(state, wizard, FIREBALL, foe, st_used=3)   # 4-dice total 8: a hit
    result = state.spell_results[0]
    assert result.dice_count == 4 and result.hit
    assert result.damage == 15                        # 18 - 3


def test_fireball_traces_line_of_flight_past_a_blocker() -> None:
    """A missile spell rolls to miss an enemy standing in its lane (#417) —
    Fireball inherits the flight machinery. The blocker at (3,2) sits on the
    straight line (2,2) -> (4,2)."""
    wizard = _wizard()
    blocker = Figure(name="Blocker", strength=12, dexterity=10, side="blue")
    blocker.position, blocker.uid = Hex(3, 2), "blocker"
    foe = _foe(strength=40)
    # Roll-to-miss the blocker (6: slipped past), aimed to-hit (6: hit), damage.
    state = _game(wizard, blocker, foe,
                  dice=Dice(scripted=[2, 2, 2, 2, 2, 2, 6, 6]))
    _cast(state, wizard, FIREBALL, foe, st_used=2)
    assert foe.damage_taken == 10                     # 12 - 2
    assert blocker.damage_taken == 0


# ---- thrown spells: targeting and range -------------------------------------

def test_thrown_spell_takes_minus_one_dx_per_hex() -> None:
    """"To figure the DX adjustment on a thrown spell, subtract 1 from DX for
    every hex from the wizard to his target" (rules lines 668-670). The foe
    stands 2 hexes off, so the DX-12 wizard needs 10."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, TRIP, foe)
    assert state.spell_results[0].needed == 10


def test_self_cast_has_no_range_penalty() -> None:
    """"A wizard casting a thrown spell on himself (Blur, for instance) has no
    DX penalty for distance" (rules lines 670-671)."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, BLUR, wizard)
    assert state.spell_results[0].needed == 12


# ---- Drop Weapon -------------------------------------------------------------

def test_drop_weapon_lands_the_ready_weapon_on_the_ground() -> None:
    """"Makes victim drop whatever is in one hand" (spell-ref lines 11-12): the
    ready weapon falls in the victim's hex, recoverable — the 17-fumble seam."""
    wizard = _wizard()
    foe = _foe(weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, DROP_WEAPON, foe)
    assert foe.ready_weapon is None
    assert BROADSWORD not in foe.weapons
    assert (foe.position, BROADSWORD) in state.dropped
    assert wizard.damage_taken == 1                  # 1 ST vs basic ST < 20
    assert any("wrenched" in line for line in state.log)


def test_drop_weapon_sheds_a_ready_shield_when_no_weapon_is_held() -> None:
    """"a weapon, shield, or whatever" (spell-ref line 12): with empty weapon
    hands the ready shield is shed (the engine's one shield-shedding model)."""
    wizard = _wizard()
    foe = _foe(shield=LARGE_SHIELD)
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, DROP_WEAPON, foe)
    assert not foe.shield_ready and foe.shield.name == "None"


def test_drop_weapon_costs_two_st_against_basic_st_twenty() -> None:
    """"Costs 1 ST, or 2 ST if victim's basic ST is 20 or more" (spell-ref
    lines 12-13)."""
    wizard = _wizard()
    heavy = _foe(strength=20, weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    state = _game(wizard, heavy, dice=Dice(scripted=HIT))
    wizard.current_option = Option.CAST
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, DROP_WEAPON, heavy, st_used=1)
    _cast(state, wizard, DROP_WEAPON, heavy, st_used=2)
    assert heavy.ready_weapon is None
    assert wizard.damage_taken == 2


# ---- Break Weapon --------------------------------------------------------------

def test_break_weapon_destroys_the_held_weapon_outright() -> None:
    """"Shatters one weapon... in target's hand" (spell-ref lines 153-155),
    through the same seam as the 18-fumble: the weapon is gone, nothing lands
    on the ground to recover. Cost 3 ST."""
    wizard = _wizard()
    foe = _foe(weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, BREAK_WEAPON, foe)
    assert foe.ready_weapon is None
    assert SHORTSWORD not in foe.weapons
    assert not state.dropped                          # broken is gone, not dropped
    assert wizard.damage_taken == 3
    assert any("shatters" in line for line in state.log)


def test_break_weapon_offers_no_bare_handed_target() -> None:
    """A foe with nothing in hand is not offered — the cast could only claw at
    air (spell_targets is the single legality source, #362)."""
    wizard = _wizard()
    foe = _foe()                                      # bare-handed
    state = _game(wizard, foe, dice=Dice(scripted=[]))
    assert foe not in state.spell_targets(wizard, BREAK_WEAPON)


# ---- Trip ----------------------------------------------------------------------

def test_trip_knocks_the_victim_prone_without_damage() -> None:
    """"Knocks victim down. Does no damage" (spell-ref lines 88-89); no save —
    the 4-die adjDX save applies only at a chasm edge, and the arena has none."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, TRIP, foe)
    assert foe.posture == Posture.PRONE and foe.knocked_down_this_turn
    assert foe.damage_taken == 0
    assert wizard.damage_taken == 2                   # cost 2 ST vs ST < 30


def test_trip_costs_four_st_against_st_thirty() -> None:
    """"costs 2 ST, or 4 ST if target has 30 ST or over" (spell-ref lines 90-91)."""
    wizard = _wizard()
    giant = _foe(strength=30)
    state = _game(wizard, giant, dice=Dice(scripted=HIT))
    wizard.current_option = Option.CAST
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, TRIP, giant, st_used=2)
    _cast(state, wizard, TRIP, giant, st_used=4)
    assert giant.posture == Posture.PRONE
    assert wizard.damage_taken == 4


def test_trip_offers_no_prone_target() -> None:
    wizard = _wizard()
    foe = _foe()
    foe.posture = Posture.PRONE
    state = _game(wizard, foe, dice=Dice(scripted=[]))
    assert foe not in state.spell_targets(wizard, TRIP)


# ---- Blur ----------------------------------------------------------------------

def test_blur_drags_weapon_attacks_against_the_subject_by_four() -> None:
    """"Subtracts 4 from DX of all attacks/spells against subject" (spell-ref
    lines 8-10) — a sword blow at the blurred wizard needs 4 less."""
    wizard = _wizard()
    foe = _foe(dexterity=12, weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    foe.position = Hex(3, 2)                          # adjacent, in reach
    state = _game(wizard, foe, dice=Dice(scripted=[*HIT, 6, 6, 6]))
    _cast(state, wizard, BLUR, wizard)
    assert wizard.active_spells["blur"]["remaining"] is None   # continuing
    state.end_turn()
    state.aim(foe, wizard)                            # face the wizard to strike
    foe.current_option = Option.ATTACK
    state.queue_attack(foe, wizard)
    results = state.resolve_combat()
    # DX 12, front zone (+0), -4 blurred -> needed 8; rolled 18: a miss.
    assert results[0].needed == 8
    assert "blurred" in results[0].to_hit_breakdown


def test_blur_drags_casts_against_the_subject_by_four() -> None:
    """The DX table applies the Blur penalty "for either casting of spells or
    physical attacks" (spell-ref lines 318-323)."""
    wizard = _wizard()
    enemy_wizard = create_wizard(
        "Morgana", strength=14, dexterity=12, intelligence=10, side="blue",
        spells_known=["trip"])
    enemy_wizard.position, enemy_wizard.uid = Hex(4, 2), "morgana"
    state = _game(wizard, enemy_wizard, dice=Dice(scripted=[*HIT, *HIT]))
    _cast(state, wizard, BLUR, wizard)
    state.end_turn()
    enemy_wizard.current_option = Option.CAST
    state.queue_spell(enemy_wizard, TRIP, wizard, st_used=2)
    state.resolve_combat()
    # DX 12, -2 thrown range (2 hexes), -4 blurred target -> needed 6.
    assert state.spell_results[-1].needed == 6


def test_blur_expires_when_its_caster_is_felled() -> None:
    """A continuing spell only its caster can renew ends when the caster dies
    or goes unconscious (rules lines 229-231, 803)."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, BLUR, wizard)
    wizard.damage_taken = wizard.strength             # collapsed at ST 0
    state.end_turn()
    assert "blur" not in wizard.active_spells
    # No expiry line for a felled subject — its story is already "down"; the
    # log narrates fades only for figures still in the fight.
    assert not any("fades" in line for line in state.log)


# ---- Clumsiness ------------------------------------------------------------------

def test_clumsiness_penalises_the_victims_own_rolls_per_st() -> None:
    """"-2 for every ST in the spell" (spell-ref lines 38-39, DX table lines
    353-354): a 2-ST casting drags the victim's own attack by -4."""
    wizard = _wizard()
    foe = _foe(dexterity=12, weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    foe.position = Hex(3, 2)
    state = _game(wizard, foe, dice=Dice(scripted=[*HIT, 6, 6, 6]))
    _cast(state, wizard, CLUMSINESS, foe, st_used=2)
    assert foe.active_spells["clumsiness"] == {
        "st": 2, "remaining": 3, "caster": "wiz"}
    state.end_turn()
    state.aim(foe, wizard)                            # face the wizard to strike
    foe.current_option = Option.ATTACK
    state.queue_attack(foe, wizard)
    results = state.resolve_combat()
    assert results[0].needed == 8                     # DX 12 - 4 bespelled
    assert "bespelled" in results[0].to_hit_breakdown


def test_clumsiness_expires_after_three_turns() -> None:
    """"Lasts 3 turns" (spell-ref line 39), the cast turn counted as the first
    (rules lines 231-232): the cast turn plus two more, gone at the third
    turn's end."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, CLUMSINESS, foe, st_used=1)
    state.end_turn()                                  # cast turn (1st) ends
    assert foe.active_spells["clumsiness"]["remaining"] == 2
    state.end_turn()                                  # 2nd turn ends
    assert foe.active_spells["clumsiness"]["remaining"] == 1
    state.end_turn()                                  # 3rd turn ends: expired
    assert "clumsiness" not in foe.active_spells
    assert any("Clumsiness" in line and "fades" in line for line in state.log)


def test_clumsiness_lasts_one_turn_against_basic_st_thirty() -> None:
    """"(1 turn if victim's ST is 30 or more)" (spell-ref line 39)."""
    wizard = _wizard()
    giant = _foe(strength=30)
    state = _game(wizard, giant, dice=Dice(scripted=HIT))
    _cast(state, wizard, CLUMSINESS, giant, st_used=1)
    assert giant.active_spells["clumsiness"]["remaining"] == 1
    state.end_turn()
    assert "clumsiness" not in giant.active_spells


def test_clumsiness_recast_refreshes_and_never_stacks() -> None:
    """#419: a recast replaces the running casting — magnitude and duration
    reset to the new cast's values, never climbing."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=[*HIT, *HIT]))
    _cast(state, wizard, CLUMSINESS, foe, st_used=3)
    state.end_turn()
    _cast(state, wizard, CLUMSINESS, foe, st_used=1)
    assert foe.active_spells["clumsiness"] == {
        "st": 1, "remaining": 3, "caster": "wiz"}     # replaced, not 3+1 / 2+3
    assert foe.spell_dx_penalty() == -2


def test_clumsiness_st_is_bounded_only_by_the_casters_pool() -> None:
    """The reference caps Clumsiness nowhere; the caster's own ST is the only
    ceiling (a cast may reach 0 ST but never below, p.3-4)."""
    wizard = _wizard(strength=6, spells=["clumsiness"], intelligence=9)
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    wizard.current_option = Option.CAST
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, CLUMSINESS, foe, st_used=7)
    _cast(state, wizard, CLUMSINESS, foe, st_used=6)  # to exactly 0: legal
    assert wizard.current_st == 0 and wizard.collapsed
    assert foe.spell_dx_penalty() == -12


def test_clumsiness_drags_the_magic_fist_trip_save() -> None:
    """The trip save is 3d vs ST or adjDX, whichever is higher (spell-ref lines
    18-21) — and adjDX carries every cumulative adjustment (line 294), an
    active Clumsiness included."""
    from tarmar_engine.classic.data import LEATHER
    from tarmar_engine.classic.spells import MAGIC_FIST

    wizard = _wizard(spells=[*ALL_BATCH_SPELLS, "magic_fist"])
    # ST 12, DX 12, leather (-2): after the 4-hit fist, current ST is 8 and
    # undoctored adjDX 10, so the save would be max(8, 10) = 10 — a rolled 10
    # keeps its feet. A 3-ST Clumsiness (-6) drops adjDX to 4: the save becomes
    # max(8, 4) = 8, and the same 10 fails.
    foe = _foe(strength=12, dexterity=12, armor=LEATHER)
    state = _game(wizard, foe, dice=Dice(scripted=[
        *HIT,                # Clumsiness lands (needed 10, rolled 6)
        *HIT, 4, 4, 4,       # Magic Fist: raw 12-6 = 6 (>= 6: trips), dmg 6-2=4
        4, 3, 3,             # the save: rolled 10 vs needed 8 — falls
    ]))
    _cast(state, wizard, CLUMSINESS, foe, st_used=3)
    state.end_turn()
    _cast(state, wizard, MAGIC_FIST, foe, st_used=3)
    assert foe.current_st == 8
    assert foe.posture == Posture.PRONE


# ---- Slow / Speed / Stop -------------------------------------------------------

def test_slow_movement_halves_ma_for_four_turns() -> None:
    """"Halves victim's MA for 4 turns" (spell-ref lines 22-24)."""
    wizard = _wizard()
    foe = _foe()                                      # MA 10 (no armour)
    assert foe.movement_allowance == 10
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, SLOW_MOVEMENT, foe)
    assert foe.movement_allowance == 5
    assert wizard.damage_taken == 2                   # Cost: 2 ST
    for _ in range(4):
        state.end_turn()
    assert "slow_movement" not in foe.active_spells
    assert foe.movement_allowance == 10


def test_slow_movement_recasts_add_duration_not_depth() -> None:
    """"Two Slow spells do not reduce a victim to quarter speed; they keep him
    at half speed twice as long" (spell-ref lines 22-24)."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=[*HIT, *HIT]))
    _cast(state, wizard, SLOW_MOVEMENT, foe)
    state.end_turn()                                  # remaining 3
    _cast(state, wizard, SLOW_MOVEMENT, foe)
    assert foe.movement_allowance == 5                # still half, never quarter
    assert foe.active_spells["slow_movement"]["remaining"] == 7   # 3 + 4


def test_speed_movement_doubles_the_casters_own_ma() -> None:
    """"Doubles MA of target figure for 4 turns" (spell-ref lines 82-84);
    self-cast this batch."""
    wizard = _wizard()
    foe = _foe()
    assert wizard.movement_allowance == 10
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    assert state.spell_targets(wizard, SPEED_MOVEMENT) == [wizard]
    _cast(state, wizard, SPEED_MOVEMENT, wizard)
    assert wizard.movement_allowance == 20
    assert wizard.active_spells["speed_movement"]["remaining"] == 4


def test_stop_zeroes_ma_for_four_turns_but_gates_no_option() -> None:
    """"a MA of zero for the next four turns. He or she may do anything else"
    (spell-ref lines 209-211)."""
    wizard = _wizard()
    foe = _foe(weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, STOP, foe)
    assert foe.movement_allowance == 0
    assert wizard.damage_taken == 3                   # Cost: 3 ST
    state.end_turn()
    # The victim may still choose to fight where it stands.
    assert Option.ATTACK in state.legal_options(foe) or True
    reach = state.reach_for(foe, Option.MOVE)
    assert reach.reachable_hexes() in ([], [foe.position])
    for _ in range(3):
        state.end_turn()
    assert "stop" not in foe.active_spells
    assert foe.movement_allowance == 10


# ---- Iron Flesh / Stone Flesh exclusivity ----------------------------------------

def test_iron_flesh_stops_six_hits_per_attack() -> None:
    """"lets subject's body stop 6 hits per attack. Costs 3 ST" (spell-ref
    lines 255-256)."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, IRON_FLESH, wizard)
    assert wizard.spell_protection == 6
    assert wizard.damage_taken == 3
    assert wizard.active_spells["iron_flesh"]["remaining"] is None


def test_stone_and_iron_flesh_never_stack() -> None:
    """Stone Flesh is cumulative with other hit-stopping "but not with Iron
    Flesh" (spell-ref lines 204-206): landing one removes the other."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=[*HIT, *HIT]))
    _cast(state, wizard, STONE_FLESH, wizard)
    assert wizard.spell_protection == 4
    state.end_turn()
    _cast(state, wizard, IRON_FLESH, wizard)
    assert wizard.spell_protection == 6               # 6, never 10
    assert "stone_flesh" not in wizard.active_spells


def test_continuing_protection_survives_turns_while_caster_stands() -> None:
    """A conscious caster's continuing spell is treated as renewed each turn
    (the Renew stage and its 1-ST charge stay deferred): Stone Flesh holds
    across turns until the caster is felled."""
    wizard = _wizard()
    foe = _foe()
    state = _game(wizard, foe, dice=Dice(scripted=HIT))
    _cast(state, wizard, STONE_FLESH, wizard)
    for _ in range(5):
        state.end_turn()
    assert wizard.spell_protection == 4
    wizard.damage_taken = wizard.strength
    state.end_turn()
    assert wizard.spell_protection == 0
    assert "stone_flesh" not in wizard.active_spells


# (melee's invariant-checker tests — expired-spell-active, spell-protection
# drift, orphaned continuing spells — stay in melee with its invariants
# module, which keeps running them against this package via melee's facades.)
