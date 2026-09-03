"""
The wizard's staff (melee #406): the Staff spell, the staff weapon, and the
unarmed-wizard engagement rule (TFT: Wizard, "The Wizard's Staff", p.19).

Ported verbatim from melee's ``engine/tests/test_staff.py`` at the battle/melee
unification's milestone 5 (tarmar-studio#240): imports adapted to
``tarmar_engine.classic``; expectations untouched. Melee's chargen-validation
tests and its ``assert_state_invariants`` sweeps stay in melee with its
chargen/invariants modules.

Everything here is deterministic — every roll is scripted through
:class:`hexarena.dice.Dice`. Rulebook line references are to melee's
``docs/reference/the-fantasy-trip-wizard-rules.txt`` (and the Staff entry at
lines 25-26 of the spell reference).
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (ported melee tests place figures then use positions/ready weapons;
#  Optional stays as-is — the same relaxation the milestone-3/4 ports use)
from __future__ import annotations

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import SHORTSWORD, STAFF, WEAPONS
from tarmar_engine.classic.figure import Figure, Posture, create_human, create_wizard
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.spells import MAGIC_FIST, STAFF_SPELL
from tarmar_engine.classic.state import GameState, IllegalAction, cast_block_reason


def _staffed_wizard(spells: list[str] | None = None) -> Figure:
    """A wizard who knows the Staff spell (ST+DX+IQ = 12+12+8 = 32)."""
    return create_wizard(
        "Merlin", strength=12, dexterity=12, intelligence=8, side="red",
        spells_known=spells if spells is not None else ["staff", "magic_fist"])


def _fighter(name: str = "Bruno", side: str = "blue") -> Figure:
    return create_human(name, 12, 12, side,
                        weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)


def _face_off(wizard: Figure, foe: Figure, *, dice: Dice) -> GameState:
    """Wizard and foe adjacent, each in the other's front hex."""
    arena = Arena(cols=11, rows=11)
    grid = arena.layout
    wizard.position, wizard.facing = Hex(4, 4), 0
    foe.position = grid.neighbor(wizard.position, 0)
    foe.facing = next(direction for direction in range(6)
                      if grid.neighbor(foe.position, direction) == wizard.position)
    return GameState(arena, [wizard, foe], dice=dice)


# ---- the Staff spell grants the staff at build (p.19, lines 940-942) --------

def test_knowing_the_staff_spell_starts_the_game_with_a_staff() -> None:
    """A wizard who knows Staff begins with the staff readied, at no ST cost."""
    wizard = _staffed_wizard()
    assert wizard.has_staff
    assert wizard.ready_weapon is not None
    assert wizard.ready_weapon.name == "Staff"
    assert wizard.ready_weapon in wizard.weapons
    assert wizard.damage_taken == 0            # "without expending any ST"


def test_wizard_without_the_staff_spell_has_no_staff() -> None:
    wizard = _staffed_wizard(spells=["magic_fist"])
    assert not wizard.has_staff and wizard.ready_weapon is None


def test_staff_spell_is_iq_8() -> None:
    """Staff sits in the IQ 8 tier (spell-ref line 25). (Melee's chargen editor
    gates it like any spell; that validation test stays in melee.)"""
    assert STAFF_SPELL.iq_tier == 8


# ---- the staff weapon (p.19, lines 943-946) ---------------------------------

def test_staff_weapon_numbers() -> None:
    """1 die damage, reach 1, not throwable — and deliberately NOT in the
    fighter weapon catalog (fighters cannot carry magical staffs, line 1162)."""
    assert STAFF.damage.count == 1 and STAFF.damage.modifier == 0
    assert STAFF.reach == 1 and not STAFF.throwable and not STAFF.two_handed
    assert "Staff" not in WEAPONS


def test_staff_strike_does_one_die_of_damage() -> None:
    """Striking with the staff is a normal 1-die weapon attack (lines 943-945).
    (No engine strike costs ST under the classic ruleset, so 'costs no ST to
    strike' — line 946 — needs no special case; the caster's ST is untouched.)"""
    wizard, foe = _staffed_wizard(), _fighter()
    # 3-dice to-hit [3,3,3] = 9 (a hit under adjDX 12), then the 1 damage die [5].
    state = _face_off(wizard, foe, dice=Dice(scripted=[3, 3, 3, 5]))
    wizard.current_option = Option.ATTACK
    state.queue_attack(wizard, foe)
    state.resolve_combat()
    assert foe.damage_taken == 5               # 1d, no armour
    assert wizard.damage_taken == 0            # striking costs the wizard no ST


def test_wizard_can_cast_with_the_staff_in_hand() -> None:
    """The staff is the one weapon that does not block a cast (p.19/p.23,
    lines 947-948): cast_block_reason clears it and the cast resolves."""
    wizard, foe = _staffed_wizard(), _fighter()
    state = _face_off(wizard, foe, dice=Dice(scripted=[3, 3, 3, 6]))
    assert wizard.ready_weapon.name == "Staff"
    assert cast_block_reason(wizard) is None
    reasons = dict(state.option_availability(wizard))
    assert reasons.get(Option.CAST) is None
    wizard.current_option = Option.CAST
    state.queue_spell(wizard, MAGIC_FIST, foe, st_used=1)
    state.resolve_combat()
    result = state.spell_results[0]
    assert result.hit and foe.damage_taken > 0


def test_staff_spell_is_not_castable_in_game() -> None:
    """The Staff spell's modeled effect is the start-of-game grant; the rare
    in-game re-creation of a broken staff (spell-ref line 26, 5 ST) is
    deliberately unmodeled — it has no legal targets and cannot be queued, so
    the cast menu never offers it."""
    wizard, foe = _staffed_wizard(), _fighter()
    state = _face_off(wizard, foe, dice=Dice(seed=1))
    assert state.spell_targets(wizard, STAFF_SPELL) == []
    wizard.current_option = Option.CAST
    with pytest.raises(IllegalAction):
        state.queue_spell(wizard, STAFF_SPELL, wizard, st_used=5)


# ---- engagement: a staffless wizard is unarmed (p.9, line 536) --------------

def test_a_staffed_wizard_engages_but_a_staffless_one_does_not() -> None:
    """"The only 'unarmed' enemy in this game is a wizard who has no staff": a
    foe in a staff-armed wizard's front is engaged; strip the staff and the foe
    goes free. NARROW: only the wizard side changes — the (armed) fighter still
    engages the wizard either way."""
    wizard, foe = _staffed_wizard(), _fighter()
    state = _face_off(wizard, foe, dice=Dice(seed=1))
    assert state.engaged(foe), "a foe in a staff-armed wizard's front is engaged"
    assert state.engaged(wizard), "the armed fighter engages the wizard"

    wizard.ready_weapon = None                 # staff gone from the hand
    assert wizard.unarmed_wizard
    assert not state.engaged(foe), "a staffless wizard engages no one (p.9)"
    assert state.engaged(wizard), "the armed fighter still engages the wizard"


def test_disarmed_fighter_still_engages() -> None:
    """The unarmed-figure rule is wizard-only: a fumble-disarmed FIGHTER keeps
    engaging (Melee-side behaviour, deliberately untouched)."""
    one, other = _fighter("One", "red"), _fighter("Two", "blue")
    state = _face_off(one, other, dice=Dice(seed=1))
    one.ready_weapon = None
    assert not one.unarmed_wizard              # no spells -> not a wizard
    assert state.engaged(other), "a disarmed fighter still engages its foe"


def test_staffless_wizard_front_hexes_do_not_stop_a_mover() -> None:
    """Movement stop-hexes mirror engagement: an armed enemy's front hexes halt
    a mover, an unarmed (staffless) wizard's do not."""
    wizard, foe = _staffed_wizard(), _fighter()
    state = _face_off(wizard, foe, dice=Dice(seed=1))
    with_staff = state._enemy_front_hexes(foe)
    assert with_staff, "a staff-armed wizard projects front stop-hexes"
    wizard.ready_weapon = None
    assert state._enemy_front_hexes(foe) == set()


def test_staffless_wizard_cannot_defend() -> None:
    """Defending needs a physical weapon in hand to parry with (p.20/23): a
    staffless wizard's SHIFT_DEFEND is greyed; a staffed one may defend."""
    wizard, foe = _staffed_wizard(), _fighter()
    state = _face_off(wizard, foe, dice=Dice(seed=1))
    assert dict(state.option_availability(wizard)).get(Option.SHIFT_DEFEND) is None
    wizard.ready_weapon = None
    reason = dict(state.option_availability(wizard)).get(Option.SHIFT_DEFEND)
    assert reason is not None and "parry" in reason


# ---- HTH drops the staff (p.23, line 1142) ----------------------------------

def test_wizard_entering_hth_drops_his_staff() -> None:
    """"A wizard involved in HTH must drop his staff": initiating a grapple
    sheds the staff to the ground, recoverable afterwards."""
    wizard, foe = _staffed_wizard(), _fighter()
    state = _face_off(wizard, foe, dice=Dice(scripted=[1] + [3] * 12))
    foe.posture = Posture.KNEELING             # a down foe can be grappled (p.17)
    wizard.current_option = Option.HTH_ATTACK
    state.hth_attack(wizard, foe)
    assert wizard.in_hth
    assert wizard.ready_weapon is None
    assert ("Staff" in [weapon.name for _, weapon in state.dropped]
            ), "the staff must lie on the ground where the grapple began"


def test_wizard_dragged_into_hth_drops_his_staff() -> None:
    """The defender sheds its staff too: a wizard grappled by a foe (defense
    roll 1 — no shrug, no dagger) loses the staff to the ground."""
    wizard, foe = _staffed_wizard(), _fighter()
    state = _face_off(wizard, foe, dice=Dice(scripted=[1] + [3] * 12))
    wizard.posture = Posture.KNEELING          # grapplable head-on (p.17)
    foe.current_option = Option.HTH_ATTACK
    state.hth_attack(foe, wizard)
    assert wizard.in_hth
    assert wizard.ready_weapon is None
    assert "Staff" in [weapon.name for _, weapon in state.dropped]


# ---- fumbles: 17 drops, 18 breaks (p.11, lines 611-612) ---------------------

def _fumbled_staff_strike(total: int) -> tuple[GameState, Figure, Figure]:
    wizard, foe = _staffed_wizard(), _fighter()
    rolls = {17: [6, 6, 5], 18: [6, 6, 6]}[total]
    state = _face_off(wizard, foe, dice=Dice(scripted=rolls + [3] * 12))
    wizard.current_option = Option.ATTACK
    state.queue_attack(wizard, foe)
    state.resolve_combat()
    return state, wizard, foe


def test_staff_fumble_17_drops_it_to_the_ground() -> None:
    """"a roll of 17 is a dropped weapon/staff" — it lands in the wizard's own
    hex, intact and recoverable."""
    state, wizard, _foe = _fumbled_staff_strike(17)
    assert wizard.ready_weapon is None
    assert (wizard.position, "Staff") in [
        (hex_pos, weapon.name) for hex_pos, weapon in state.dropped]
    assert wizard.unarmed_wizard               # staffless until recovered


def test_staff_fumble_18_breaks_it_for_good() -> None:
    """"a roll of 18 is a broken weapon/staff"; "a broken staff does not work"
    (line 957) — like any broken weapon it is gone: not in hand, not carried,
    nothing on the ground to recover."""
    state, wizard, _foe = _fumbled_staff_strike(18)
    assert wizard.ready_weapon is None
    assert "Staff" not in [weapon.name for weapon in wizard.weapons]
    assert "Staff" not in [weapon.name for _, weapon in state.dropped]


# ---- staff pick-up is owner-only (p.19, lines 950-952) ----------------------

def test_owner_picks_his_dropped_staff_back_up_but_a_foe_cannot() -> None:
    """A dropped staff is recovered by its owner like any weapon; anyone else
    is never offered it and is rejected if they ask (the occult-zap rule, cut
    to "others simply can't take it")."""
    state, wizard, foe = _fumbled_staff_strike(17)          # staff at wizard's feet
    # The foe stands adjacent to the dropped staff but is never offered it...
    assert "Staff" not in [weapon.name for weapon in state.dropped_in_reach(foe)]
    # ...and a direct grab is rejected.
    with pytest.raises(IllegalAction, match="staff"):
        state.pick_up_weapon(foe, "Staff")
    # The owner recovers it and is armed (and engaging) again.
    assert "Staff" in [weapon.name for weapon in state.dropped_in_reach(wizard)]
    state.pick_up_weapon(wizard, "Staff")
    assert wizard.ready_weapon is not None
    assert wizard.ready_weapon.name == "Staff"
    assert not wizard.unarmed_wizard
    assert state.engaged(foe)
