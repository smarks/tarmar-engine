"""
Wizards carry two weapons like everyone else (melee #411; Wizard p.23, rules
lines 1159-1162): "A wizard may carry two weapons plus a dagger (his staff
counts as a weapon). However, his DX is -4 with any weapon except his staff. A
wizard cannot cast a spell if he has any weapon (except his staff) ready; the
weapon must be dropped or re-slung."

Ported verbatim from melee's ``engine/tests/test_wizard_weapons.py`` at the
battle/melee unification's milestone 5 (tarmar-studio#240): imports adapted to
``tarmar_engine.classic``, wizard construction adapted from melee's
``chargen.build`` to :func:`create_wizard` (same figures, minus chargen's free
dagger, which no expectation here reads); expectations untouched. Melee's
chargen-validation tests and its ``assert_state_invariants`` sweeps stay in
melee with its chargen/invariants modules.

Everything here is deterministic — every roll is scripted through
:class:`hexarena.dice.Dice`.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (ported melee tests place figures then use positions/ready weapons;
#  Optional stays as-is — the same relaxation the milestone-3/4 ports use)
from __future__ import annotations

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import CLUB, MAIN_GAUCHE, SHORTSWORD
from tarmar_engine.classic.figure import Figure, create_human, create_wizard
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.spells import MAGIC_FIST
from tarmar_engine.classic.state import (
    BARE_HANDS_CHOICE,
    GameState,
    IllegalAction,
    cast_block_reason,
)


def _staffed_wizard(*, spells: list[str] | None = None, **gear) -> Figure:
    """Melee's ``chargen.build("Classic Melee", _wizard_spec(...))`` shape:
    ST 12 / DX 12 / IQ 8, knowing staff + magic_fist (the staff readied unless
    ``gear`` readies another weapon)."""
    return create_wizard(
        "Zed", strength=12, dexterity=12, intelligence=8, side="red",
        spells_known=spells if spells is not None else ["staff", "magic_fist"],
        **gear)



# (melee's chargen-validation tests — weapon picks, slot counts, ST
# requirements, shield/staff rejections — stay in melee with its chargen
# module.)


def _face_off(wizard: Figure, foe: Figure, *, dice: Dice) -> GameState:
    """Wizard and foe adjacent, each in the other's front hex."""
    arena = Arena(cols=11, rows=11)
    grid = arena.layout
    wizard.position, wizard.facing = Hex(4, 4), 0
    foe.position = grid.neighbor(wizard.position, 0)
    foe.facing = next(direction for direction in range(6)
                      if grid.neighbor(foe.position, direction) == wizard.position)
    return GameState(arena, [wizard, foe], dice=dice)


def _fighter(name: str = "Bruno", side: str = "blue") -> Figure:
    return create_human(name, 12, 12, side,
                        weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)


# ---- in play: -4 DX with any non-staff weapon (staff exempt) ----------------

def test_wizard_swings_a_non_staff_weapon_at_minus_four() -> None:
    """adjDX 12, roll [4,4,3]=11: a fighter's hit, but the wizard's -4 makes the
    needed number 8 — a miss. The same roll with the STAFF ready hits."""
    wizard = _staffed_wizard(weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    foe = _fighter()
    state = _face_off(wizard, foe, dice=Dice(scripted=[4, 4, 3]))
    wizard.current_option = Option.ATTACK
    state.queue_attack(wizard, foe)
    results = state.resolve_combat()
    assert not results[0].hit, "11 vs adjDX 12 - 4 = 8 must miss"
    assert "-4 wizard weapon" in results[0].to_hit_breakdown
    assert foe.damage_taken == 0


def test_wizard_staff_strike_takes_no_penalty() -> None:
    wizard = _staffed_wizard()                                # staff readied
    foe = _fighter()
    # Same 11 to-hit, then the staff's 1 damage die.
    state = _face_off(wizard, foe, dice=Dice(scripted=[4, 4, 3, 5]))
    wizard.current_option = Option.ATTACK
    state.queue_attack(wizard, foe)
    results = state.resolve_combat()
    assert results[0].hit, "the staff is exempt from the wizard's -4 (p.23)"
    assert "wizard weapon" not in results[0].to_hit_breakdown
    assert foe.damage_taken == 5


def test_fighter_to_hit_is_unchanged() -> None:
    """The -4 is wizard-only: the same roll from a plain fighter still hits."""
    fighter = create_human("Axel", 12, 12, "red",
                           weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    foe = _fighter()
    state = _face_off(fighter, foe, dice=Dice(scripted=[4, 4, 3, 1, 1]))
    fighter.current_option = Option.ATTACK
    state.queue_attack(fighter, foe)
    results = state.resolve_combat()
    assert results[0].hit
    assert "wizard weapon" not in results[0].to_hit_breakdown


def test_wizard_thrown_weapon_also_takes_the_penalty() -> None:
    """"Any weapon except his staff" includes a hurled one."""
    wizard = _staffed_wizard(weapons=[CLUB], ready_weapon=CLUB)
    foe = _fighter()
    arena = Arena(cols=11, rows=11)
    grid = arena.layout
    wizard.position, wizard.facing = Hex(4, 4), 0
    foe.position = grid.neighbor(grid.neighbor(wizard.position, 0), 0)  # 2 away
    foe.facing = 3
    # 11 to-hit: adjDX 12 - 2 range - 4 wizard weapon = 6 needed -> miss.
    state = GameState(arena, [wizard, foe], dice=Dice(scripted=[4, 4, 3]))
    wizard.current_option = Option.ATTACK
    state.queue_attack(wizard, foe)
    results = state.resolve_combat()
    assert not results[0].hit
    assert "-4 wizard weapon" in results[0].to_hit_breakdown


def test_wizard_main_gauche_jab_stacks_the_penalty() -> None:
    """The off-hand jab's own -4 (p.13) stacks with the wizard's -4 (p.23)."""
    wizard = create_wizard(
        "Zed", strength=12, dexterity=12, intelligence=8, side="red",
        spells_known=["magic_fist"],
        weapons=[CLUB, MAIN_GAUCHE], ready_weapon=CLUB)
    foe = _fighter()
    state = _face_off(wizard, foe, dice=Dice(scripted=[3] * 12))
    wizard.current_option = Option.ATTACK
    state.queue_attack(wizard, foe, with_main_gauche=True)
    jab = next(p for p in state._pending if p.weapon is MAIN_GAUCHE)
    assert jab.situational == -8
    assert "-4 main-gauche" in jab.situational_note
    assert "-4 wizard weapon" in jab.situational_note


# ---- the cast gate: ready a sword, re-sling it, cast ------------------------

def test_sword_ready_blocks_casting_until_reslung_then_casts() -> None:
    """The #411 flow: a wizard fielded sword-in-hand cannot cast (the #409/#406
    gate holds); Change Weapons back to the staff, and next turn it casts."""
    wizard = _staffed_wizard(weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    foe = _fighter()
    # Cast to-hit [3,3,3]=9 (hit vs adjDX 12), then Magic Fist's damage die.
    state = _face_off(wizard, foe, dice=Dice(scripted=[3, 3, 3, 6]))

    # Turn 1: sword in hand — the cast gate blocks.
    assert cast_block_reason(wizard) == "cannot cast with a weapon ready"
    reasons = dict(state.option_availability(wizard))
    assert reasons[Option.CAST] == "cannot cast with a weapon ready"
    wizard.current_option = Option.CAST
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, MAGIC_FIST, foe, st_used=1)
    wizard.current_option = None

    # Re-sling: engaged, so the Change Weapons option swaps sword for staff.
    assert Option.CHANGE_WEAPONS in state.legal_options(wizard)
    state.move(wizard, Option.CHANGE_WEAPONS, ready="Staff")
    assert wizard.ready_weapon.name == "Staff"
    assert "Shortsword" in [w.name for w in wizard.weapons]   # slung, not dropped
    assert cast_block_reason(wizard) is None

    # Next turn: staff in hand — the cast queues and resolves.
    state.end_turn()
    wizard.current_option = Option.CAST
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=1)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.hit and foe.damage_taken > 0


def test_wizard_can_ready_the_sword_back() -> None:
    """The same machinery swaps back to sword-mode (fighters' paths, reused)."""
    wizard = _staffed_wizard(weapons=[SHORTSWORD])       # staff readied
    foe = _fighter()
    state = _face_off(wizard, foe, dice=Dice(seed=1))
    state.move(wizard, Option.CHANGE_WEAPONS, ready="Shortsword")
    assert wizard.ready_weapon.name == "Shortsword"
    assert "Staff" in [w.name for w in wizard.weapons]
    assert cast_block_reason(wizard) == "cannot cast with a weapon ready"


# ---- #425: a STAFFLESS wizard re-slings to bare hands and casts -------------

def test_staffless_wizard_readies_bare_hands_then_casts() -> None:
    """The #425 flow: a staffless wizard fielded sword-in-hand has no staff to
    swap to — readying BARE_HANDS_CHOICE re-slings the sword (still carried,
    nothing hits the ground), the cast gate clears, and next turn it casts."""
    wizard = _staffed_wizard(
        spells=["magic_fist"], weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    assert not wizard.has_staff
    foe = _fighter()
    # Cast to-hit [3,3,3]=9 (hit vs adjDX 12), then Magic Fist's damage die.
    state = _face_off(wizard, foe, dice=Dice(scripted=[3, 3, 3, 6]))

    # Sword in hand — blocked; and the only carried swap targets are no help.
    assert cast_block_reason(wizard) == "cannot cast with a weapon ready"
    # Bare hands is offered exactly because something is in hand to re-sling.
    assert BARE_HANDS_CHOICE in state.ready_choices(wizard)

    state.move(wizard, Option.CHANGE_WEAPONS, ready=BARE_HANDS_CHOICE)
    assert wizard.ready_weapon is None
    assert "Shortsword" in [w.name for w in wizard.weapons]   # re-slung, kept
    assert state.dropped == []                                # nothing dropped
    assert cast_block_reason(wizard) is None
    assert any("re-slings" in line for line in state.log)     # narrated clearly

    # Next turn: hands free — the cast queues and resolves.
    state.end_turn()
    wizard.current_option = Option.CAST
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=1)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.hit and foe.damage_taken > 0


def test_ready_choices_offers_bare_hands_only_with_a_weapon_in_hand() -> None:
    """ready_choices lists every carried weapon, plus bare hands only when
    something is readied to re-sling — never a phantom no-op (#425)."""
    fighter = _fighter()
    arena = Arena(cols=9, rows=15)
    fighter.position = Hex(5, 5)
    state = GameState(arena, [fighter])
    assert state.ready_choices(fighter) == ["Shortsword", BARE_HANDS_CHOICE]
    fighter.ready_weapon = None                # e.g. disarmed on a fumbled 17
    assert state.ready_choices(fighter) == ["Shortsword"]
    with pytest.raises(IllegalAction, match="nothing readied"):
        state.move(fighter, Option.READY_WEAPON, ready=BARE_HANDS_CHOICE)


def test_any_figure_may_ready_bare_hands() -> None:
    """No class gate (#425): the rulebook's re-sling has no wizard-only clause,
    so a plain fighter may clear its hands too (it has no reason, no bar)."""
    fighter = _fighter()
    arena = Arena(cols=9, rows=15)
    fighter.position = Hex(5, 5)
    state = GameState(arena, [fighter])
    state.move(fighter, Option.READY_WEAPON, ready=BARE_HANDS_CHOICE)
    assert fighter.ready_weapon is None
    assert "Shortsword" in [w.name for w in fighter.weapons]  # slung, not dropped
    assert state.dropped == []
