"""
Classic casting engine tests: Magic Fist + Stone Flesh, on injected dice only.

Ported verbatim from melee's ``engine/tests/test_spells.py`` at the
battle/melee unification's milestone 5 (tarmar-studio#240): imports adapted to
``tarmar_engine.classic``; expectations untouched. Melee's chargen-validation
tests and its ``assert_state_invariants`` sweeps stay in melee with its
chargen/invariants modules (which keep running these same behaviours against
this package through melee's facades).

Every roll here is scripted through :class:`hexarena.dice.Dice`, so the outcomes
are exact and deterministic (no ``random``). The dice stream a cast draws is,
in order: the 3-dice to-hit roll, then (for a missile spell that HIT) one die per
ST invested for damage — see
:meth:`tarmar_engine.classic.ruleset.Ruleset.resolve_spell`.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (ported melee tests place figures then use positions/ready weapons;
#  Optional stays as-is — the same relaxation the milestone-3/4 ports use)
from __future__ import annotations

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import BROADSWORD, LEATHER, NO_ARMOR
from tarmar_engine.classic.figure import Figure, Posture, create_wizard
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.ruleset import Ruleset
from tarmar_engine.classic.spells import MAGIC_FIST, SPELLS, STONE_FLESH
from tarmar_engine.classic.state import GameState, IllegalAction


def _wizard(strength: int = 20, dexterity: int = 12, intelligence: int = 13,
            spells: list[str] | None = None, **gear) -> Figure:
    """A ready-to-cast wizard (hands free) at a fixed hex, facing east."""
    wizard = create_wizard(
        "Merlin", strength=strength, dexterity=dexterity,
        intelligence=intelligence, side="red",
        spells_known=spells if spells is not None else ["magic_fist", "stone_flesh"],
        **gear)
    wizard.position = Hex(2, 2)
    wizard.facing = 0
    wizard.uid = "wiz"
    wizard.current_option = Option.CAST
    return wizard


def _target(strength: int = 40, **gear) -> Figure:
    """A durable enemy dummy a few hexes to the wizard's front."""
    dummy = Figure(name="Dummy", strength=strength, dexterity=10, side="blue", **gear)
    dummy.position = Hex(4, 2)
    dummy.uid = "dummy"
    return dummy


def _game(*figures: Figure, dice: Dice) -> GameState:
    arena = Arena(cols=12, rows=12)
    return GameState(arena, list(figures), dice=dice)


# ---- Magic Fist: hit + damage per ST ---------------------------------------

@pytest.mark.parametrize(
    "st_used, damage_rolls, expected",
    [
        # base = max(st_used, sum(dice) + (-2 * st_used)); floor at ST invested.
        (1, [6], 4),        # 1d-2 at 1 ST: 6-2 = 4
        (2, [6, 5], 7),     # 2d-4 at 2 ST: 11-4 = 7
        (3, [6, 6, 6], 12),  # 3d-6 at 3 ST: 18-6 = 12
        (1, [2], 1),        # floor: 2-2 = 0 -> never less than the 1 ST used
        (3, [1, 1, 1], 3),  # floor: 3-6 = -3 -> never less than the 3 ST used
    ],
)
def test_magic_fist_damage_scales_per_st(st_used, damage_rolls, expected) -> None:
    """1d-2 per ST, floored at the ST invested (spell-ref line 16)."""
    wizard = _wizard()
    dummy = _target()
    # to-hit total 6 (a plain hit under DX 12), then the damage dice.
    dice = Dice(scripted=[2, 2, 2, *damage_rolls])
    state = _game(wizard, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=st_used)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.hit and not result.fizzled
    assert result.damage == expected
    assert dummy.damage_taken == expected
    # The caster paid the full ST it invested.
    assert wizard.damage_taken == st_used


def test_magic_fist_max_st_is_three() -> None:
    """A missile spell may invest at most 3 ST (rules line 620)."""
    wizard = _wizard()
    dummy = _target()
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2]))
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=4)


# ---- Fizzles (17/18) -------------------------------------------------------

def test_fizzle_17_charges_full_st_and_deals_no_damage() -> None:
    """A 17 fizzles, losing the FULL ST cost, harming nothing (rules line 607)."""
    wizard = _wizard()
    dummy = _target()
    dice = Dice(scripted=[6, 6, 5])          # to-hit total 17
    state = _game(wizard, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=3)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.fizzled and not result.hit and not result.knockdown
    assert result.st_spent == 3
    assert wizard.damage_taken == 3          # full invested ST lost
    assert dummy.damage_taken == 0
    assert wizard.posture == Posture.STANDING


def test_fizzle_18_knocks_the_caster_down() -> None:
    """An 18 fizzles AND the shock knocks the caster down (rules line 609-610)."""
    wizard = _wizard()
    dummy = _target()
    dice = Dice(scripted=[6, 6, 6])          # to-hit total 18
    state = _game(wizard, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=2)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.fizzled and result.knockdown
    assert result.st_spent == 2
    assert wizard.posture == Posture.PRONE
    assert wizard.knocked_down_this_turn


def test_plain_miss_costs_one_st() -> None:
    """A 16 auto-miss (not a fizzle) loses just 1 ST (rules line 682)."""
    wizard = _wizard()
    dummy = _target()
    dice = Dice(scripted=[6, 6, 4])          # to-hit total 16
    state = _game(wizard, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=3)
    state.resolve_combat()
    result = state.spell_results[0]
    assert not result.hit and not result.fizzled
    assert result.st_spent == 1
    assert wizard.damage_taken == 1
    assert dummy.damage_taken == 0


# ---- ST-affordability + hands-free gating ----------------------------------

def test_cast_below_zero_st_is_rejected() -> None:
    """A cast may bring ST to exactly 0 but never below (p.3-4)."""
    wizard = _wizard(strength=8)
    wizard.damage_taken = 7                  # current ST 1
    dummy = _target()
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2]))
    # 2 ST is one more than it has -> rejected.
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=2)
    # Exactly its last ST is legal.
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)


def test_cast_with_a_non_staff_weapon_ready_is_illegal() -> None:
    """A wizard cannot cast with a non-staff weapon in hand (Wizard p.23)."""
    wizard = _wizard(weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    dummy = _target()
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2]))
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    # And the CAST option is greyed with that reason.
    reasons = dict(state.option_availability(wizard))
    assert reasons.get(Option.CAST) == "cannot cast with a weapon ready"


def test_one_cast_per_turn() -> None:
    """Only one new spell may be cast per turn (rules line 620/p.11)."""
    wizard = _wizard()
    dummy = _target()
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2, 6]))
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)


def test_stand_down_cancels_a_queued_cast_and_clears_the_cast_option() -> None:
    # #409: "Don't cast" is the cast gate's explicit decline. stand_down (the same
    # hold_fire machinery as #397) flips the declared caster to DO_NOTHING and
    # cancels its already-queued spell, so resolving spends no mana and the wizard
    # leaves the Resolve gate.
    wizard = _wizard()
    dummy = _target()
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2, 6]))
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    assert any(pending.caster is wizard for pending in state._pending_casts)

    state.stand_down(wizard)
    assert wizard.current_option == Option.DO_NOTHING
    assert not any(pending.caster is wizard for pending in state._pending_casts)
    st_before = wizard.current_st
    state.resolve_combat()
    assert wizard.current_st == st_before      # the cancelled cast spent nothing


# ---- Stone Flesh: protection folds into absorbed() -------------------------

def test_stone_flesh_adds_protection_that_absorbed_applies() -> None:
    """Stone Flesh grants +4 hit-stopping, composing with worn armour (p.19)."""
    wizard = _wizard(spells=["stone_flesh"], armor=LEATHER)  # leather stops 2
    state = _game(wizard, dice=Dice(scripted=[2, 2, 2]))     # a plain hit
    state.queue_spell(wizard, STONE_FLESH, wizard, st_used=2)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.hit and result.stops_granted == 4
    assert wizard.spell_protection == 4
    assert "stone_flesh" in wizard.active_spells
    assert wizard.damage_taken == 2          # the 2-ST cast cost
    rules = Ruleset()
    # Composes with armour: leather (2) + Stone Flesh (4) = 6 hits stopped/attack.
    assert rules.absorbed(wizard, zone=None) == LEATHER.stops + 4


def test_stone_flesh_stops_four_hits_of_an_incoming_blow() -> None:
    """The 4 hits Stone Flesh stops come off a real attack's damage (p.19)."""
    wizard = _wizard(spells=["stone_flesh"], armor=NO_ARMOR)
    state = _game(wizard, dice=Dice(scripted=[2, 2, 2]))
    state.queue_spell(wizard, STONE_FLESH, wizard, st_used=2)
    state.resolve_combat()
    rules = Ruleset()
    raw = 10
    # With no armour, exactly 4 hits are stopped by the spell alone.
    stopped = rules.absorbed(wizard, zone=None)
    assert stopped == 4
    assert max(0, raw - stopped) == 6


# ---- create_wizard + chargen validation ------------------------------------

def test_create_wizard_sets_wizard_fields() -> None:
    wizard = create_wizard(
        "Gala", strength=10, dexterity=10, intelligence=12, side="red",
        spells_known=["magic_fist"])
    assert wizard.intelligence == 12
    assert wizard.spells_known == ["magic_fist"]
    assert wizard.spell_protection == 0 and wizard.active_spells == {}
    # A wizard is the same Figure class as a fighter.
    assert isinstance(wizard, Figure)


# (melee's chargen-validation tests stay in melee with its chargen module.)


def test_spell_reference_numbers() -> None:
    """Pin the exact reference values encoded in the classic spell catalog."""
    from tarmar_engine.classic.spells import STAFF_SPELL

    assert MAGIC_FIST.type == "M" and MAGIC_FIST.iq_tier == 8
    assert MAGIC_FIST.damage_per_st == -2 and MAGIC_FIST.max_st == 3
    assert STONE_FLESH.type == "T" and STONE_FLESH.iq_tier == 13
    assert STONE_FLESH.stops == 4 and STONE_FLESH.st_cost == 2
    assert STONE_FLESH.continuing and STONE_FLESH.renew_cost == 1
    # Staff (spell-reference lines 25-26): IQ 8, Special, 5 ST if cast in-game.
    assert STAFF_SPELL.type == "S" and STAFF_SPELL.iq_tier == 8
    assert STAFF_SPELL.st_cost == 5 and not STAFF_SPELL.continuing
    assert set(SPELLS) == {
        "magic_fist", "staff", "stone_flesh",
        # the #431 Classic batch
        "blur", "drop_weapon", "slow_movement", "clumsiness", "speed_movement",
        "trip", "break_weapon", "fireball", "stop", "lightning", "iron_flesh",
    }


def test_spell_reference_numbers_431_batch() -> None:
    """Pin the exact reference values for the #431 spells, each against its line
    in melee's docs/reference/the-fantasy-trip-wizard-spell-reference.txt."""
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
        STOP,
        TRIP,
    )

    # Blur (lines 8-10): IQ 8, -4 vs subject, 1 ST + 1/turn, continuing.
    assert BLUR.iq_tier == 8 and BLUR.type == "T" and BLUR.st_cost == 1
    assert BLUR.defense_dx_penalty == -4
    assert BLUR.continuing and BLUR.renew_cost == 1 and BLUR.targets_self
    # Drop Weapon (lines 11-13): IQ 8, 1 ST (2 vs basic ST 20+), instant.
    assert DROP_WEAPON.iq_tier == 8 and DROP_WEAPON.st_cost == 1
    assert DROP_WEAPON.st_cost_heavy == 2 and DROP_WEAPON.heavy_st == 20
    assert DROP_WEAPON.drops_weapon and not DROP_WEAPON.has_lasting_effect
    # Slow Movement (lines 22-24): IQ 8, 2 ST, halves MA 4 turns, adds.
    assert SLOW_MOVEMENT.iq_tier == 8 and SLOW_MOVEMENT.st_cost == 2
    assert SLOW_MOVEMENT.ma_percent == 50 and SLOW_MOVEMENT.duration == 4
    assert SLOW_MOVEMENT.durations_add
    # Clumsiness (lines 38-39): IQ 9, -2 DX/ST, 3 turns (1 vs basic ST 30+).
    assert CLUMSINESS.iq_tier == 9 and CLUMSINESS.dx_penalty_per_st == -2
    assert CLUMSINESS.duration == 3 and CLUMSINESS.duration_heavy == 1
    assert CLUMSINESS.heavy_st == 30 and CLUMSINESS.variable_st
    # Speed Movement (lines 82-84): IQ 10, 2 ST, doubles MA 4 turns, adds.
    assert SPEED_MOVEMENT.iq_tier == 10 and SPEED_MOVEMENT.st_cost == 2
    assert SPEED_MOVEMENT.ma_percent == 200 and SPEED_MOVEMENT.duration == 4
    assert SPEED_MOVEMENT.durations_add and SPEED_MOVEMENT.targets_self
    # Trip (lines 88-91): IQ 10, 2 ST (4 vs ST 30+), knocks down, instant.
    assert TRIP.iq_tier == 10 and TRIP.st_cost == 2
    assert TRIP.st_cost_heavy == 4 and TRIP.heavy_st == 30 and TRIP.knocks_down
    # Break Weapon (lines 153-155): IQ 12, 3 ST, instant.
    assert BREAK_WEAPON.iq_tier == 12 and BREAK_WEAPON.st_cost == 3
    assert BREAK_WEAPON.breaks_weapon
    # Fireball (lines 156-157): IQ 12, missile, 1d-1 per ST, max 3 (rules l.620).
    assert FIREBALL.iq_tier == 12 and FIREBALL.type == "M"
    assert FIREBALL.damage_per_st == -1 and FIREBALL.max_st == 3
    # Stop (lines 209-211): IQ 13, 3 ST, MA 0 for 4 turns.
    assert STOP.iq_tier == 13 and STOP.st_cost == 3
    assert STOP.ma_percent == 0 and STOP.duration == 4
    # Lightning (lines 221-224): IQ 14, missile, a full 1d per ST, max 3.
    assert LIGHTNING.iq_tier == 14 and LIGHTNING.type == "M"
    assert LIGHTNING.damage_per_st == 0 and LIGHTNING.max_st == 3
    # Iron Flesh (lines 255-256): IQ 15, stops 6, 3 ST + 1/turn, continuing;
    # mutually exclusive with Stone Flesh (lines 204-206).
    assert IRON_FLESH.iq_tier == 15 and IRON_FLESH.stops == 6
    assert IRON_FLESH.st_cost == 3 and IRON_FLESH.continuing
    assert IRON_FLESH.exclusive_with == ("stone_flesh",)
    assert STONE_FLESH.exclusive_with == ("iron_flesh",)


# ---- missile-spell line-of-flight (#417) -------------------------------------
# Geometry used throughout: same-row hexes are a straight line ((2,2) -> (6,2)
# passes (3,2), (4,2), (5,2)), and every hex out to (7,2) is within 1 MH of the
# wizard, so the range penalty is 0 and needed == DX 12 for every roll.

def _blocker(position: Hex, side: str = "blue", **overrides) -> Figure:
    fig = Figure(name="Bystander", strength=30, dexterity=10, side=side,
                 **overrides)
    fig.position = position
    fig.uid = f"by-{position.col}-{position.row}"
    return fig


def test_lane_blocker_is_rolled_to_miss_and_slipped_past() -> None:
    """An enemy in the lane draws a roll-to-miss; adjDX or less slips the spell
    past to strike the aimed target (rules lines 639-646)."""
    wizard = _wizard()
    blocker = _blocker(Hex(4, 2))
    dummy = _target()
    dummy.position = Hex(6, 2)
    # roll-to-miss 6 (<= 12: slipped past), aimed to-hit 7 (hit), damage 6.
    dice = Dice(scripted=[2, 2, 2, 2, 2, 3, 6])
    state = _game(wizard, blocker, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    assert blocker.damage_taken == 0
    assert dummy.damage_taken == 4               # 6 - 2, past the blocker
    assert len(state.spell_results) == 1         # only the aimed cast recorded
    assert len(dice._scripted) == 0              # the blocker's roll WAS drawn


@pytest.mark.parametrize(
    "miss_roll, multiplier",
    [
        ([6, 6, 2], 1),   # 14: an automatic hit on a roll to miss
        ([6, 6, 3], 2),   # 15: a double-damage hit
        ([6, 6, 6], 3),   # 18: a triple-damage hit
    ],
)
def test_lane_blocker_struck_by_the_roll_to_miss_special_table(
    miss_roll, multiplier
) -> None:
    """On a roll to miss, 14 is an automatic hit, 15-16 double damage, 17-18
    triple (rules lines 646-648) — the blocker is struck, the aimed target never
    rolled, and the full invested ST is charged (a hit)."""
    wizard = _wizard()
    blocker = _blocker(Hex(4, 2))
    dummy = _target()
    dummy.position = Hex(6, 2)
    dice = Dice(scripted=[*miss_roll, 6])        # then 1 damage die (1 ST)
    state = _game(wizard, blocker, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    assert blocker.damage_taken == 4 * multiplier   # (6-2) x multiplier
    assert dummy.damage_taken == 0
    result = state.spell_results[0]
    assert result.hit and result.note == "struck_in_lane"
    assert result.target_uid == blocker.uid
    assert result.st_spent == 1 and wizard.damage_taken == 1
    assert len(dice._scripted) == 0


def test_failed_roll_to_miss_an_enemy_fizzles_in_that_hex() -> None:
    """A missed roll-to-miss an enemy "is not a hit... just fizzles in that hex"
    (rules lines 650-652): nobody is harmed, the aimed target is never rolled,
    and only the plain-miss 1 ST is lost."""
    wizard = _wizard()
    blocker = _blocker(Hex(4, 2))
    dummy = _target()
    dummy.position = Hex(6, 2)
    # 13 > DX 12 and not a special: the roll to miss failed. Sentinel dice after
    # prove nothing further was drawn (no aimed roll, no damage).
    dice = Dice(scripted=[4, 4, 5, 6, 6, 6])
    state = _game(wizard, blocker, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=3)
    state.resolve_combat()
    assert blocker.damage_taken == 0 and dummy.damage_taken == 0
    result = state.spell_results[0]
    assert not result.hit and result.note == "fizzled_in_lane"
    assert wizard.damage_taken == 1              # the plain-miss charge
    assert wizard.posture == Posture.STANDING    # not a 17/18 casting fizzle
    assert len(dice._scripted) == 3              # the sentinels went undrawn
    assert any("fizzles out against" in line for line in state.log)


def test_missed_spell_flies_on_and_strikes_the_figure_behind() -> None:
    """A missed missile spell continues along the caster-target line, making a
    fresh to-hit roll at each enemy hex it enters (rules lines 624-628) — and a
    fly-on hit charges the FULL invested ST (it is a hit)."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(4, 2)
    behind = _blocker(Hex(6, 2))                 # two hexes past the target
    # aimed 15 (> 12: a plain miss), fly-on 6 (<= 12: strikes), 2d damage 6+6.
    dice = Dice(scripted=[5, 5, 5, 2, 2, 2, 6, 6])
    state = _game(wizard, dummy, behind, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=2)
    state.resolve_combat()
    assert dummy.damage_taken == 0
    assert behind.damage_taken == 8              # 12 - 4
    aimed, flown = state.spell_results
    assert not aimed.hit and flown.hit and flown.note == "flew_on"
    assert flown.target_uid == behind.uid
    assert wizard.damage_taken == 2              # full ST: the spell HIT someone
    assert len(dice._scripted) == 0


def test_missed_spell_that_misses_the_fly_on_figure_too_costs_one_st() -> None:
    """A spell that misses its target AND everyone on the fly-on line spends
    itself unhit — the plain-miss 1 ST charge stands."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(4, 2)
    behind = _blocker(Hex(6, 2))
    # aimed 15 (miss), fly-on 15 (misses the figure behind too); nothing more.
    dice = Dice(scripted=[5, 5, 5, 5, 5, 5])
    state = _game(wizard, dummy, behind, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=2)
    state.resolve_combat()
    assert dummy.damage_taken == 0 and behind.damage_taken == 0
    assert wizard.damage_taken == 1
    assert len(dice._scripted) == 0


def test_friendly_figure_in_the_lane_is_never_rolled_and_never_harmed() -> None:
    """A friend standing in the lane keeps the weapon flight's friendly-fire
    guard (#229): no roll-to-miss dice are drawn for it and it cannot be
    struck — the spell resolves against the aimed target as if the lane were
    clear."""
    wizard = _wizard()
    friend = _blocker(Hex(4, 2), side="red")     # the wizard's own side
    dummy = _target()
    dummy.position = Hex(6, 2)
    # ONLY the aimed to-hit (7: hit) and damage (6) — a sentinel proves no
    # roll-to-miss was drawn for the friend.
    dice = Dice(scripted=[2, 2, 3, 6, 1, 1, 1])
    state = _game(wizard, friend, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    assert friend.damage_taken == 0
    assert dummy.damage_taken == 4
    assert len(dice._scripted) == 3              # sentinels undrawn


def test_fly_on_stops_at_the_casters_basic_st_in_megahexes() -> None:
    """The fly-on travels at most a number of megahexes equal to the caster's
    basic ST (rules lines 628-630): a figure beyond that is never rolled."""
    wizard = _wizard(strength=2)                 # a 2-MH flight cap
    dummy = _target()
    dummy.position = Hex(4, 2)
    far = _blocker(Hex(11, 2))                   # 3 MH out: past the cap
    # aimed 15 (miss); sentinels prove the far figure drew no fly-on roll.
    dice = Dice(scripted=[5, 5, 5, 6, 6, 6])
    state = _game(wizard, dummy, far, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    assert far.damage_taken == 0 and dummy.damage_taken == 0
    assert len(dice._scripted) == 3


def test_aimed_fizzle_does_not_fly_on() -> None:
    """A 17/18 on the aimed roll dies in the caster's hands — nothing flies on
    to threaten the figure behind (the weapon-flight fumble parallel)."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(4, 2)
    behind = _blocker(Hex(6, 2))
    dice = Dice(scripted=[6, 6, 5, 1, 1, 1])     # 17: fizzle; sentinels after
    state = _game(wizard, dummy, behind, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=2)
    state.resolve_combat()
    assert behind.damage_taken == 0
    assert wizard.damage_taken == 2              # the fizzle's full-ST charge
    assert len(dice._scripted) == 3


# ---- dodging forces four dice against a missile spell (#418) -----------------

def test_dodging_target_forces_four_dice_on_a_missile_spell() -> None:
    """"To hit a figure who is dodging... four dice instead of three... Dodging
    is effective only against missile spells (and thrown and missile weapons)"
    (wizard-rules lines 996-1004)."""
    wizard = _wizard()
    dummy = _target()
    dummy.dodging = True
    # Four dice drawn: 2+2+2+2 = 8 (a hit under 12 — but on FOUR dice), then 6.
    dice = Dice(scripted=[2, 2, 2, 2, 6])
    state = _game(wizard, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.dice_count == 4
    assert result.hit and dummy.damage_taken == 4
    assert len(dice._scripted) == 0              # the 4th die really was drawn


@pytest.mark.parametrize(
    "rolls, fizzle, knockdown",
    [
        ([6, 6, 4, 4], False, False),   # 20: an automatic miss
        ([6, 6, 6, 3], True, False),    # 21: fizzle (the drop analogue)
        ([6, 6, 6, 6], True, True),     # 24: fizzle + knockdown (break analogue)
    ],
)
def test_four_dice_spell_specials_shift_with_the_dodge(
    rolls, fizzle, knockdown
) -> None:
    """The four-dice special bands (20+ miss, 21-22 drop, 23-24 break; rules
    lines 998-1001) map onto a spell's fizzle tiers, as 17/18 do on three."""
    wizard = _wizard()
    dummy = _target()
    dummy.dodging = True
    state = _game(wizard, dummy, dice=Dice(scripted=rolls))
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=2)
    state.resolve_combat()
    result = state.spell_results[0]
    assert not result.hit
    assert result.fizzled is fizzle and result.knockdown is knockdown
    assert wizard.damage_taken == (2 if fizzle else 1)
    assert (wizard.posture == Posture.PRONE) is knockdown


def test_non_dodging_target_still_rolls_three_dice() -> None:
    """The dodge is the only thing that shifts the count — a plain target keeps
    the three-dice table (16 is still the automatic miss)."""
    wizard = _wizard()
    dummy = _target()
    dice = Dice(scripted=[6, 6, 4, 1, 1])        # 16 on three dice: auto miss
    state = _game(wizard, dummy, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.dice_count == 3 and not result.hit and not result.fizzled
    assert len(dice._scripted) == 2


# ---- Stone Flesh does not stack (#419) ----------------------------------------

def test_stone_flesh_recast_refreshes_and_never_stacks() -> None:
    """"Only one Blur, one Stone Flesh, one Shock Shield, etc., can be cast on
    any given figure at a time. These spells are not cumulative." (rules lines
    683-684.) A recast replaces the running spell: protection stays 4."""
    wizard = _wizard(spells=["stone_flesh"])
    state = _game(wizard, dice=Dice(scripted=[2, 2, 2, 2, 2, 2]))
    state.queue_spell(wizard, STONE_FLESH, wizard, st_used=2)
    state.resolve_combat()
    assert wizard.spell_protection == 4
    state.end_turn()
    wizard.current_option = Option.CAST
    state.queue_spell(wizard, STONE_FLESH, wizard, st_used=2)
    state.resolve_combat()
    assert wizard.spell_protection == 4          # refreshed, not 8
    assert wizard.active_spells == {
        "stone_flesh": {"st": 2, "remaining": None, "caster": wizard.uid}}
    assert wizard.damage_taken == 4              # both casts' ST was still paid


# ---- Magic Fist's trip effect (#421) ------------------------------------------

def _trippable(strength: int = 10, **overrides) -> Figure:
    """A low-ST, low-DX foe whose trip save is max(current ST, adjDX) = 8."""
    fig = Figure(name="Wobbly", strength=strength, dexterity=8, side="blue",
                 **overrides)
    fig.position = Hex(4, 2)
    fig.uid = "wobbly"
    return fig


def test_magic_fist_six_pre_armor_hits_trips_on_a_failed_save() -> None:
    """"A Magic Fist that does 6 or more hits before armor/shield protection
    will also trip its target... unless he/she makes a 3-die roll on ST or DX,
    whichever is higher" (spell-ref lines 18-21). 6 hits is below the generic
    8-hit knockdown, so only the trip can floor this target."""
    wizard = _wizard()
    foe = _trippable()
    # to-hit 6 (hit), 3d damage 4+4+4 = 12-6 = 6 raw; save 11 > 8: falls.
    dice = Dice(scripted=[2, 2, 2, 4, 4, 4, 4, 4, 3])
    state = _game(wizard, foe, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=3)
    state.resolve_combat()
    assert foe.damage_taken == 6                 # unarmoured: raw == taken < 8
    assert foe.posture == Posture.PRONE and foe.knocked_down_this_turn
    assert any("off its feet" in line for line in state.log)


def test_magic_fist_trip_save_keeps_the_target_standing() -> None:
    """The save made — 3 dice at or under max(ST, DX) — keeps its feet."""
    wizard = _wizard()
    foe = _trippable()
    dice = Dice(scripted=[2, 2, 2, 4, 4, 4, 2, 2, 2])   # save 6 <= 8: stands
    state = _game(wizard, foe, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=3)
    state.resolve_combat()
    assert foe.damage_taken == 6
    assert foe.posture == Posture.STANDING and not foe.knocked_down_this_turn
    assert any("keeps its feet" in line for line in state.log)


def test_magic_fist_trip_keys_on_pre_armor_damage() -> None:
    """The 6-hit threshold is BEFORE armour: leather (stops 2) cuts the damage
    to 4 — far below the 8-hit knockdown — yet the trip still fires."""
    wizard = _wizard()
    foe = _trippable(armor=LEATHER)
    dice = Dice(scripted=[2, 2, 2, 4, 4, 4, 4, 4, 3])   # raw 6, taken 4; save 11
    state = _game(wizard, foe, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=3)
    state.resolve_combat()
    assert foe.damage_taken == 4                 # armour stopped 2
    assert foe.posture == Posture.PRONE          # tripped on the PRE-armour 6


def test_magic_fist_below_six_pre_armor_hits_draws_no_save() -> None:
    """Five pre-armour hits trip nothing — and draw no save dice."""
    wizard = _wizard()
    foe = _trippable()
    dice = Dice(scripted=[2, 2, 2, 4, 4, 3, 6, 6, 6])   # raw 11-6 = 5; sentinels
    state = _game(wizard, foe, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=3)
    state.resolve_combat()
    assert foe.damage_taken == 5
    assert foe.posture == Posture.STANDING
    assert len(dice._scripted) == 3              # no save was rolled


def test_magic_fist_eight_hit_knockdown_masks_the_trip_save() -> None:
    """When the generic 8-hit knockdown already floored the target, no trip
    save is drawn — the figure is down either way, and pre-#421 dice streams
    stay byte-identical."""
    wizard = _wizard()
    foe = _trippable(strength=20)                # survives 12 hits, still falls
    dice = Dice(scripted=[2, 2, 2, 6, 6, 6, 6, 6, 6])   # raw 12 >= 8; sentinels
    state = _game(wizard, foe, dice=dice)
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=3)
    state.resolve_combat()
    assert foe.posture == Posture.PRONE          # the generic knockdown
    assert len(dice._scripted) == 3              # no save on top of it


# ---- the CAST option's one-hex move / shift (#422) ----------------------------

def test_cast_option_permits_a_one_hex_move_and_the_cast() -> None:
    """Option (h): "Move one hex or stand still, and attempt any spell"
    (wizard-rules line 286) — the step and the cast happen on one turn."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(8, 2)
    dice = Dice(scripted=[2, 2, 3, 6])
    state = _game(wizard, dummy, dice=dice)
    wizard.current_option = None
    state.move(wizard, Option.CAST, path=[Hex(3, 2)])
    assert wizard.position == Hex(3, 2)
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    assert dummy.damage_taken == 4               # the stepped cast landed


def test_cast_option_rejects_a_two_hex_move() -> None:
    """One hex is the cap — two is a different option."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(8, 2)
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2]))
    wizard.current_option = None
    with pytest.raises(IllegalAction, match="at most 1 MA"):
        state.move(wizard, Option.CAST, path=[Hex(3, 2), Hex(4, 2)])


def test_engaged_cast_hex_is_a_shift_that_keeps_adjacency() -> None:
    """Option (r): "Shift one hex or stand still, and attempt any spell" (line
    312) — the engaged caster's hex may not break away from its engager."""
    wizard = _wizard()
    foe = _target(weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    foe.position = Hex(3, 2)
    state = _game(wizard, foe, dice=Dice(scripted=[2, 2, 2]))
    foe.facing = state.arena.layout.direction_to(foe.position, wizard.position)
    wizard.current_option = None
    with pytest.raises(IllegalAction, match="stay adjacent"):
        state.move(wizard, Option.CAST, path=[Hex(1, 2)])   # 2 hexes from the foe
    state.move(wizard, Option.CAST, path=[Hex(2, 1)])       # a true shift: legal
    assert wizard.position == Hex(2, 1)


def test_one_hex_mover_may_still_be_stamped_into_a_cast() -> None:
    """The #413 queue-time gate now keys on the ONE-hex cap: a figure that
    stepped a single hex under another option may still have its option
    overwritten into a legal cast (it did nothing CAST forbids)."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(8, 2)
    dice = Dice(scripted=[2, 2, 3, 6])
    state = _game(wizard, dummy, dice=dice)
    wizard.current_option = None
    state.move(wizard, Option.MOVE, path=[Hex(3, 2)])   # one hex, option (a)
    wizard.current_option = Option.CAST                 # what _act_cast_spell stamps
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    state.resolve_combat()
    assert dummy.damage_taken == 4


# ---- one action per turn / option integrity (#413, #414) --------------------

def test_cast_after_moving_two_hexes_is_rejected() -> None:
    """#413/#422: the CAST option moves at most ONE hex (wizard-rules line 286
    "Move one hex or stand still"), so a figure that spent MORE movement this
    turn took a different option and cannot have it overwritten into a cast
    (wizard-rules lines 271-274)."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(8, 2)                  # far enough not to engage
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2]))
    wizard.current_option = None
    state.move(wizard, Option.MOVE, path=[Hex(3, 2), Hex(4, 2)])  # two hexes
    wizard.current_option = Option.CAST         # what _act_cast_spell stamps
    with pytest.raises(IllegalAction, match="moved"):
        state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)


def test_cast_while_dodging_is_rejected() -> None:
    """#413: dodge/defend permits neither an attack nor a cast (wizard-rules
    lines 1010-1011) — the dodging flag outlives an option overwrite."""
    wizard = _wizard()
    dummy = _target()
    dummy.position = Hex(8, 2)
    state = _game(wizard, dummy, dice=Dice(scripted=[2, 2, 2]))
    wizard.current_option = None
    state.move(wizard, Option.DODGE)            # sets the dodging flag
    wizard.current_option = Option.CAST         # what _act_cast_spell stamps
    with pytest.raises(IllegalAction, match="dodging"):
        state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)


def test_staff_blow_then_cast_same_turn_is_rejected() -> None:
    """#414: one option per turn (wizard-rules lines 262-263) — a wizard with a
    queued staff blow may not also queue a cast."""
    wizard = _wizard(spells=["staff", "magic_fist"])    # staff readied in hand
    dummy = _target()
    dummy.position = Hex(3, 2)                  # adjacent, in the wizard's front
    state = _game(wizard, dummy, dice=Dice(scripted=[3] * 10))
    wizard.current_option = Option.SHIFT_ATTACK
    state.queue_attack(wizard, dummy)           # staff blow queued
    wizard.current_option = Option.CAST         # what _act_cast_spell stamps
    with pytest.raises(IllegalAction, match="already attacked"):
        state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)


def test_cast_then_staff_blow_same_turn_is_rejected() -> None:
    """#414, the reverse order: a wizard with a queued cast may not also queue a
    weapon attack."""
    wizard = _wizard(spells=["staff", "magic_fist"])
    dummy = _target()
    dummy.position = Hex(3, 2)
    state = _game(wizard, dummy, dice=Dice(scripted=[3] * 10))
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=1)
    wizard.current_option = Option.SHIFT_ATTACK  # what _ensure_attack_option does
    with pytest.raises(IllegalAction, match="cannot also attack"):
        state.queue_attack(wizard, dummy)


# ---- resolve-time re-checks (#415, #416) ------------------------------------

def _duel(wizard: Figure, foe: Figure, dice: Dice) -> GameState:
    """Wizard at (2,2) and an adjacent armed foe at (3,2), each facing the other."""
    foe.position = Hex(3, 2)
    state = _game(wizard, foe, dice=dice)
    foe.facing = state.arena.layout.direction_to(foe.position, wizard.position)
    return state


def test_wounded_caster_cast_fizzles_harmlessly_instead_of_self_kill() -> None:
    """#415: "A wizard cannot cast a spell which would reduce his ST below 0"
    (rules lines 167-169). A cast declared legally but no longer affordable at
    resolution (the caster was wounded first) fizzles harmlessly: no damage
    dealt, no ST drained, and never a self-kill."""
    wizard = _wizard(strength=8)
    wizard.damage_taken = 5                     # current ST 3: a 3-ST cast is legal
    foe = _target(strength=12, weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    # Foe's blow: to-hit 9 (hit under adjDX 10), broadsword 2d rolls 1+1 = 2.
    state = _duel(wizard, foe, Dice(scripted=[3, 3, 3, 1, 1]))
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=3)
    foe.current_option = Option.ATTACK
    state.queue_attack(foe, wizard)

    state.resolve_combat()

    assert wizard.current_st == 1               # the foe's 2 landed; the cast cost 0
    assert not wizard.is_dead                   # the old bug drove ST to -2 (dead)
    assert foe.damage_taken == 0                # the fizzled cast harmed nothing
    assert state.spell_results == []            # no cast was rolled at all
    assert wizard.cast_this_turn                # the action is still spent
    assert any("too weakened" in line for line in state.log)


def test_knocked_down_caster_loses_its_cast() -> None:
    """#416: "If any figure is killed or knocked down before its turn to act
    comes, it does not get to act that turn" (rules lines 250-251) — the cast
    mirror of _can_strike_now's knocked-down gate on weapon blows."""
    wizard = _wizard()                          # ST 20
    foe = _target(strength=14, weapons=[BROADSWORD], ready_weapon=BROADSWORD)
    # Foe's blow: to-hit 9 (hit), broadsword 2d rolls 4+4 = 8 -> KNOCKDOWN.
    state = _duel(wizard, foe, Dice(scripted=[3, 3, 3, 4, 4]))
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=2)
    foe.current_option = Option.ATTACK
    state.queue_attack(foe, wizard)

    state.resolve_combat()

    assert wizard.posture == Posture.PRONE and wizard.knocked_down_this_turn
    assert foe.damage_taken == 0                # the lost cast harmed nothing
    assert wizard.current_st == 12              # only the blow: no cast ST drained
    assert state.spell_results == []            # no cast was rolled at all
    assert wizard.cast_this_turn                # the action is still spent
    assert any("uncast" in line for line in state.log)
