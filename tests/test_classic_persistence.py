"""Classic ``GameState`` snapshot serialization — the round-trip contract (#1).

Ported from melee's ``board/tests/test_persistence.py`` (the layers that are
about the engine, not about Django or the board's game wrapper), plus the two
guarantees this package needs that melee's save/load deliberately did not
offer: the **dice stream** survives the trip, and a snapshot taken mid-turn
re-derives the same legal option menu. tarmar-studio serves battles out of
``Battle.state_json``, so a restored classic battle has to resolve identically
to the one it was snapshotted from — not merely start from the same board.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic import persistence
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    BROADSWORD,
    DAGGER,
    LEATHER,
    SMALL_SHIELD,
    STAFF,
    WEAPONS,
    DamageDice,
    Weapon,
    WeaponKind,
)
from tarmar_engine.classic.figure import Figure, create_human, create_wizard
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.ruleset import Ruleset
from tarmar_engine.classic.spells import MAGIC_FIST, SPELLS
from tarmar_engine.classic.state import GameState, PendingAttack, PendingCast

# Fields compared on every figure for a lossless round-trip.
_FIGURE_FIELDS = (
    "name", "side", "uid", "strength", "dexterity", "facing", "posture",
    "damage_taken", "hits_this_turn", "wounded_last_turn", "attacked_this_turn",
    "moved_this_turn", "dodging", "unconscious", "dead", "current_option",
    "dealt_st_damage_this_turn", "force_retreat_targets_this_turn",
    "missile_cooldown", "hth_opponents", "hth_drew_dagger", "shield_ready",
    "current_st", "knocked_down_this_turn", "moved_straight", "defending",
    "dropped_out", "experience", "added_st", "added_dx",
    "intelligence", "spells_known", "has_staff", "active_spells",
    "spell_protection", "cast_this_turn",
)


def _fighter(name: str, side: str) -> Figure:
    return create_human(
        name, 12, 12, side, weapons=[BROADSWORD], ready_weapon=BROADSWORD,
        armor=LEATHER, shield=SMALL_SHIELD)


def _two_figure_game(dice: Dice | None = None) -> GameState:
    """Two adjacent fighters facing off, with scripted dice for determinism."""
    red, blue = _fighter("Red", "red"), _fighter("Blue", "blue")
    arena = Arena(cols=9, rows=15)
    red.position, blue.position = Hex(2, 2), Hex(2, 3)
    red.facing = arena.layout.direction_to(red.position, blue.position)
    blue.facing = arena.layout.direction_to(blue.position, red.position)
    # A long scripted run so initiative/attacks resolve deterministically.
    return GameState(arena, [red, blue], dice=dice or Dice(scripted=[5, 3] + [2] * 40))


def _play_a_turn(state: GameState) -> None:
    """Run selection -> a faced attack -> resolve -> end turn, mutating state."""
    state.begin_selection()
    attacker, target = state.figures[0], state.figures[1]
    attacker.current_option = Option.SHIFT_ATTACK
    state.queue_attack(attacker, target)
    state.resolve_combat()
    state.end_turn()


def _round_trip(state: GameState) -> GameState:
    """Through real JSON, so the payload is proven JSON-serializable."""
    return GameState.from_dict(json.loads(json.dumps(state.to_dict())))


# ---- figure-level round trip -------------------------------------------------
def test_per_turn_flags_survive_a_round_trip() -> None:
    # Regression (melee #155): defending / moved_straight / knocked_down_this_turn
    # used to be dropped by the figure round-trip (the flag list had drifted).
    figure = _fighter("Red", "red")
    figure.defending = True
    figure.moved_straight = True
    figure.knocked_down_this_turn = True

    restored = persistence.figure_from_json(persistence.figure_to_json(figure))

    assert restored.defending is True
    assert restored.moved_straight is True
    assert restored.knocked_down_this_turn is True


def test_figure_serialization_covers_every_field() -> None:
    """Drift guard (melee #369): the persisted figure key set must equal the
    dataclass field set, so a field added to ``Figure`` can never silently drop
    from a snapshot."""
    persisted = set(persistence.figure_to_json(_fighter("Red", "red")))
    assert persisted == {field.name for field in dataclasses.fields(Figure)}


def test_monster_traits_survive_a_round_trip() -> None:
    """The nonhuman quirks (Section VIII) ride along: a tri-hex giant's size and
    scaled injury thresholds, a gargoyle's flight, a snake's all-front facing."""
    giant = _fighter("Grond", "red")
    giant.size, giant.needs_two_to_engage = 3, True
    giant.wound_hits_threshold, giant.knockdown_hits_threshold = 9, 16
    giant.flying, giant.fly_movement_allowance = True, 16
    giant.all_front, giant.hard_to_hit = True, 3
    giant.dropped_out = True

    restored = persistence.figure_from_json(persistence.figure_to_json(giant))

    assert restored.size == 3
    assert restored.needs_two_to_engage is True
    assert restored.wound_hits_threshold == 9
    assert restored.knockdown_hits_threshold == 16
    assert restored.fly_movement_allowance == 16
    assert restored.flying is True
    assert restored.all_front is True
    assert restored.hard_to_hit == 3
    assert restored.dropped_out is True


def test_non_catalog_weapon_and_armour_round_trip_by_value() -> None:
    """A weapon outside the printed table (a monster's natural attack) must come
    back by value rather than raising ``KeyError`` on a catalog lookup
    (melee #272/#303), with the ready-weapon identity preserved."""
    bite = Weapon("Snake bite", DamageDice(2, 0), 0, kind=WeaponKind.MELEE,
                  hth_damage=DamageDice(1, 2), notes="natural attack")
    snake = Figure(name="Ssss", strength=20, dexterity=12, side="blue",
                   weapons=[bite], ready_weapon=bite)
    snake.position = Hex(4, 4)

    restored = persistence.figure_from_json(persistence.figure_to_json(snake))

    assert restored.ready_weapon is not None
    assert restored.ready_weapon.name == "Snake bite"
    assert restored.ready_weapon.damage == bite.damage
    assert restored.ready_weapon.hth_damage == bite.hth_damage
    assert restored.ready_weapon in restored.weapons          # identity preserved


def test_wizard_identity_and_active_spells_round_trip() -> None:
    """A staffed wizard keeps its staff, its known spells, and the lasting-spell
    records in effect on it (melee #406/#431)."""
    wizard = create_wizard("Merlin", strength=12, dexterity=12, intelligence=10,
                           side="red", spells_known=["staff", "stone_flesh"])
    wizard.position, wizard.uid = Hex(3, 3), "wiz"
    wizard.active_spells = {
        "stone_flesh": {"st": 3, "remaining": None, "caster": "wiz"}}
    wizard.spell_protection = 3

    restored = persistence.figure_from_json(persistence.figure_to_json(wizard))

    assert restored.has_staff is True
    assert restored.spells_known == ["staff", "stone_flesh"]
    assert restored.ready_weapon is not None
    assert restored.ready_weapon.name == STAFF.name
    assert restored.ready_weapon in restored.weapons
    assert restored.active_spells == {
        "stone_flesh": {"st": 3, "remaining": None, "caster": "wiz"}}
    assert restored.spell_protection == 3
    # The restored records are the figure's own, never aliases of the decoded blob.
    restored.active_spells["stone_flesh"]["st"] = 99
    assert wizard.active_spells["stone_flesh"]["st"] == 3


def test_legacy_active_spell_integer_normalizes_to_a_record() -> None:
    """A pre-record snapshot stored ``active_spells`` as {spell_id: ST}; it loads
    as a continuing spell cast by its own wearer (melee #431)."""
    wizard = create_wizard("Merlin", strength=12, dexterity=12, intelligence=10,
                           side="red", spells_known=["staff", "stone_flesh"])
    wizard.uid = "wiz"
    payload = persistence.figure_to_json(wizard)
    payload["active_spells"] = {"stone_flesh": 2}

    restored = persistence.figure_from_json(payload)

    assert restored.active_spells == {
        "stone_flesh": {"st": 2, "remaining": None, "caster": "wiz"}}


# ---- whole-state round trip --------------------------------------------------
def _assert_figures_equal(left: GameState, right: GameState) -> None:
    for figure_left, figure_right in zip(left.figures, right.figures):
        for field_name in _FIGURE_FIELDS:
            assert getattr(figure_left, field_name) == getattr(
                figure_right, field_name), field_name
        assert figure_left.position == figure_right.position
        assert figure_left.armor == figure_right.armor
        assert figure_left.shield == figure_right.shield
        assert [weapon.name for weapon in figure_left.weapons] == \
            [weapon.name for weapon in figure_right.weapons]
        left_ready, right_ready = figure_left.ready_weapon, figure_right.ready_weapon
        assert (left_ready.name if left_ready else None) == \
            (right_ready.name if right_ready else None)
        if right_ready is not None:
            # the ready weapon is the same object as the matching carried one
            assert any(carried is right_ready for carried in figure_right.weapons)


def _assert_state_equal(left: GameState, right: GameState) -> None:
    assert left.turn_number == right.turn_number
    assert left.combat_type == right.combat_type
    assert left.initiative_order == right.initiative_order
    assert left.active_index == right.active_index
    assert left.passed == right.passed
    assert left.sides == right.sides
    assert left.log == right.log
    assert (left.arena.cols, left.arena.rows) == (right.arena.cols, right.arena.rows)
    assert left.arena.name == right.arena.name
    assert left.arena.walls == right.arena.walls
    assert type(left.rules) is type(right.rules)
    assert [(hex_position.col, hex_position.row, weapon.name)
            for hex_position, weapon in left.dropped] == \
        [(hex_position.col, hex_position.row, weapon.name)
         for hex_position, weapon in right.dropped]
    _assert_figures_equal(left, right)


def test_state_round_trips_through_json() -> None:
    state = _two_figure_game()
    _play_a_turn(state)
    _play_a_turn(state)
    state.dropped.append((Hex(3, 3), WEAPONS["Dagger"]))
    state.arena.walls = {Hex(7, 7), Hex(7, 8)}

    _assert_state_equal(state, _round_trip(state))


def test_state_serialization_covers_every_attribute() -> None:
    """Drift guard on ``GameState`` itself: every instance attribute is either
    persisted or named in the documented omission set, so a new piece of state
    cannot be added without a decision about whether it survives a snapshot."""
    state = _two_figure_game()
    state._victory_announced = True          # the one lazily-set attribute

    classified = (persistence.SERIALIZED_STATE_ATTRIBUTES
                  | persistence.OMITTED_STATE_ATTRIBUTES)
    assert set(vars(state)) == classified
    assert not (persistence.SERIALIZED_STATE_ATTRIBUTES
                & persistence.OMITTED_STATE_ATTRIBUTES)


def test_practice_mode_and_drop_out_round_trip() -> None:
    from tarmar_engine.classic.experience import CombatType

    state = _two_figure_game()
    state.combat_type = CombatType.PRACTICE
    state.figures[1].dropped_out = True            # out of the fight, alive

    restored = _round_trip(state)

    assert restored.combat_type is CombatType.PRACTICE
    assert restored.practice
    assert restored.figures[1].dropped_out
    assert restored.figures[1].collapsed and not restored.figures[1].is_dead


def test_victory_flag_round_trips() -> None:
    """The one-shot victory announcement must stay one-shot across a snapshot."""
    state = _two_figure_game()
    state._victory_announced = True

    assert getattr(_round_trip(state), "_victory_announced", False) is True


def test_dropped_weapons_use_the_same_serializer_as_carried() -> None:
    """Drift guard (melee #303): a dropped weapon goes through the same by-value
    helper carried weapons use, so a non-catalog one keeps round-tripping."""
    state = _two_figure_game()
    state.dropped.append((Hex(2, 2), WEAPONS["Dagger"]))
    state.dropped.append((Hex(5, 5), STAFF))       # not in the printed table

    payload = state.to_dict()

    assert payload["dropped"][0]["weapon"] == persistence.weapon_to_json(DAGGER)
    assert payload["dropped"][0]["weapon"] == "Dagger"
    restored = _round_trip(state)
    staff_hex, staff = restored.dropped[1]
    assert (staff_hex.col, staff_hex.row) == (5, 5)
    assert staff.name == "Staff" and staff.damage == DamageDice(1, 0)


# ---- queued attacks and casts ------------------------------------------------
def test_pending_attacks_round_trip() -> None:
    """A snapshot taken mid-combat (attacks queued, not resolved) restores
    exactly, and the queue still resolves after the load."""
    state = _two_figure_game()
    state.begin_selection()
    attacker, target = state.figures[0], state.figures[1]
    attacker.current_option = Option.SHIFT_ATTACK
    state.queue_attack(attacker, target)
    state._pending.append(PendingAttack(
        attacker=target, target=attacker, zone="rear", ignore_facing=False,
        range_penalty=0, hth_damage=DamageDice(1, -2)))
    # Every other non-default field, all of which used to drop on reload
    # (melee #245): a snapshot with shield_rush set came back as a full damaging
    # weapon attack, silently changing how the attack resolved.
    state._pending.append(PendingAttack(
        attacker=attacker, target=target, zone="front", ignore_facing=True,
        range_penalty=2, shots=2, situational=-4, situational_note="off-hand jab",
        damage_dice_bonus=1, charge_resolve_first=True, thrown=True,
        weapon=WEAPONS["Main-Gauche"], second_target=attacker, shield_rush=True))

    restored = _round_trip(state)

    assert len(restored._pending) == len(state._pending)
    for original, copy in zip(state._pending, restored._pending):
        assert copy.attacker.uid == original.attacker.uid
        assert copy.target.uid == original.target.uid
        assert copy.attacker in restored.figures    # rebound, not a stale object
        assert copy.zone == original.zone
        assert copy.ignore_facing == original.ignore_facing
        assert copy.range_penalty == original.range_penalty
        assert copy.shots == original.shots
        assert copy.situational == original.situational
        assert copy.situational_note == original.situational_note
        assert copy.damage_dice_bonus == original.damage_dice_bonus
        assert copy.charge_resolve_first == original.charge_resolve_first
        assert copy.thrown == original.thrown
        assert copy.shield_rush == original.shield_rush
        assert copy.weapon is original.weapon       # catalog singleton, by name
        assert (copy.second_target.uid if copy.second_target else None) == \
            (original.second_target.uid if original.second_target else None)
        assert copy.hth_damage == original.hth_damage
    restored.resolve_combat()


def test_pending_serialization_covers_every_field() -> None:
    """Drift guard: the persisted key set must equal ``PendingAttack``'s field
    set, so the melee #245 class of silent-drop bug cannot recur."""
    state = _two_figure_game()
    pending = PendingAttack(
        attacker=state.figures[0], target=state.figures[1], zone="front",
        ignore_facing=False, range_penalty=0)

    assert set(persistence.pending_attack_to_json(pending)) == {
        field.name for field in dataclasses.fields(PendingAttack)}


def _wizard_duel_state() -> GameState:
    """A wizard with a legal cast queued at an enemy dummy."""
    wizard = create_wizard("Merlin", strength=20, dexterity=12, intelligence=13,
                           side="red", spells_known=["magic_fist", "stone_flesh"])
    wizard.position, wizard.facing, wizard.uid = Hex(2, 2), 0, "wiz"
    wizard.current_option = Option.CAST
    dummy = Figure(name="Dummy", strength=20, dexterity=10, side="blue")
    dummy.position, dummy.uid = Hex(4, 2), "dummy"
    state = GameState(Arena(cols=12, rows=12), [wizard, dummy],
                      dice=Dice(scripted=[2, 2, 2, 6, 6]))
    state.queue_spell(wizard, MAGIC_FIST, dummy, st_used=2)
    return state


def test_pending_casts_round_trip() -> None:
    """A snapshot with a cast declared but unresolved restores it exactly — the
    caster/target rebound to the RESTORED figures, the spell to its catalog
    singleton — and the cast still resolves after the load (melee #420)."""
    state = _wizard_duel_state()
    assert len(state._pending_casts) == 1

    restored = _round_trip(state)

    assert len(restored._pending_casts) == 1
    original, copy = state._pending_casts[0], restored._pending_casts[0]
    assert copy.caster is restored.figures[0]
    assert copy.target is restored.figures[1]
    assert copy.spell is SPELLS["magic_fist"]
    assert copy.st_used == original.st_used
    assert copy.zone == original.zone
    assert copy.range_penalty == original.range_penalty
    assert copy.situational == original.situational
    assert copy.situational_note == original.situational_note
    restored.resolve_combat()
    assert restored.figures[0].cast_this_turn
    assert restored.figures[1].damage_taken > 0


def test_pending_cast_serialization_covers_every_field() -> None:
    """The ``PendingCast`` mirror of the drift guard above (melee #420)."""
    state = _wizard_duel_state()

    assert set(persistence.pending_cast_to_json(state._pending_casts[0])) == {
        field.name for field in dataclasses.fields(PendingCast)}


def test_snapshot_without_pending_casts_still_loads() -> None:
    """A snapshot missing the ``pending_casts`` key loads with no queued casts
    rather than failing."""
    payload = _wizard_duel_state().to_dict()
    del payload["pending_casts"]

    assert GameState.from_dict(json.loads(json.dumps(payload)))._pending_casts == []


# ---- the dice stream ---------------------------------------------------------
def test_scripted_dice_queue_round_trips() -> None:
    """The unconsumed scripted rolls survive, so a scripted fight resumes on the
    same value it was about to draw."""
    state = _two_figure_game(Dice(scripted=[4, 5, 6]))
    assert state.dice.roll() == 4

    restored = _round_trip(state)

    assert [restored.dice.roll(), restored.dice.roll()] == [5, 6]


def test_random_dice_stream_resumes_exactly() -> None:
    """The departure from melee's save/load, and the reason this issue exists:
    the RNG state itself round-trips, so a restored battle draws the SAME future
    rolls. tarmar-studio re-snapshots ``Battle.state_json`` every turn and
    replays from it, so a fresh random stream after each load would make a
    resumed battle unreproducible."""
    state = _two_figure_game(Dice(seed=1234))
    state.dice.roll_n(7)                       # advance the stream mid-fight
    expected = [state.dice.roll() for _ in range(20)]

    state_again = _two_figure_game(Dice(seed=1234))
    state_again.dice.roll_n(7)
    restored = _round_trip(state_again)

    assert [restored.dice.roll() for _ in range(20)] == expected


def test_snapshot_without_dice_state_loads_with_a_fresh_stream() -> None:
    """A payload carrying no RNG state (hand-written, or written by an older
    producer) still loads — with a fresh source, not an exception."""
    payload = _two_figure_game().to_dict()
    del payload["dice_state"]

    restored = GameState.from_dict(json.loads(json.dumps(payload)))

    assert 1 <= restored.dice.roll() <= 6


def test_a_resumed_turn_produces_byte_identical_results() -> None:
    """The acceptance test: snapshot -> restore -> play the same turn, and the
    log lines and the board agree with the fight that was never snapshotted."""
    original = _two_figure_game(Dice(seed=99))
    _play_a_turn(original)
    restored = _round_trip(original)
    assert restored.log == original.log

    _play_a_turn(original)
    _play_a_turn(restored)

    assert restored.log == original.log
    assert [figure.damage_taken for figure in restored.figures] == \
        [figure.damage_taken for figure in original.figures]
    assert [figure.position for figure in restored.figures] == \
        [figure.position for figure in original.figures]


def test_a_mid_turn_snapshot_re_derives_the_same_option_menu() -> None:
    """The granularity tarmar-studio's remote-play validation needs: restore in
    the middle of a turn's selection and the legal option menu — and the greyed
    entries with their reasons — come back identical."""
    original = _two_figure_game(Dice(seed=7))
    original.begin_selection()
    active = original.active_character()
    assert active is not None

    restored = _round_trip(original)
    restored_active = restored.active_character()

    assert restored_active is not None
    assert restored_active.uid == active.uid
    assert restored.initiative_order == original.initiative_order
    assert restored.active_index == original.active_index
    assert restored.legal_options(restored_active) == original.legal_options(active)
    assert restored.option_availability(restored_active) == \
        original.option_availability(active)
    assert sorted(figure.uid for figure in restored.melee_targets(restored_active)) == \
        sorted(figure.uid for figure in original.melee_targets(active))


# ---- the ruleset ------------------------------------------------------------
def test_a_custom_ruleset_must_be_registered_to_be_snapshotted() -> None:
    """A consumer's ``Ruleset`` subclass swaps real mechanics, so silently
    reloading it as the base classic ruleset would change the fight. Refuse to
    serialize an unregistered one, and honour a registered one."""
    class HouseRules(Ruleset):
        pass

    state = _two_figure_game()
    state.rules = HouseRules()
    with pytest.raises(ValueError, match="not registered"):
        state.to_dict()

    persistence.register_ruleset("house", HouseRules())
    try:
        assert state.to_dict()["ruleset"] == "house"
        assert type(_round_trip(state).rules) is HouseRules
    finally:
        del persistence.RULESETS["house"]


def test_an_unknown_ruleset_name_fails_loudly_on_load() -> None:
    payload = _two_figure_game().to_dict()
    payload["ruleset"] = "no-such-ruleset"

    with pytest.raises(KeyError):
        GameState.from_dict(payload)
