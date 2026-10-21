"""The classic attack forecast (tarmar-engine #23).

``GameState.attack_forecast`` must say what ``queue_attack`` declares and what
the roll then does. Every check here compares the forecast with the engine's
own path rather than with a second copy of the rules: the to-hit number with
the declared ``PendingAttack`` run through ``Ruleset.to_hit_number`` (and with
the resolved ``AttackResult``), the chance to hit with every dice roll put
through ``Ruleset.classify_roll``, and the damage of a hit with
``Ruleset.resolve_attack`` driven by every die face in turn. The sweep at the
end does that for every attack the existing classic boards declare.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (figures are placed on the board before any attack; positions are not None)

from __future__ import annotations

import inspect
import itertools
from collections.abc import Callable
from types import ModuleType

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic.arena import DEFAULT_LAYOUT as LAYOUT
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.combat import AttackForecast
from tarmar_engine.classic.data import (
    BROADSWORD,
    CHAINMAIL,
    DAGGER,
    DIE_FACES,
    LARGE_SHIELD,
    LONGBOW,
    MAIN_GAUCHE,
    SHORTSWORD,
    SPEAR,
    THREE_DICE,
    WeaponKind,
)
from tarmar_engine.classic.experience import CombatType
from tarmar_engine.classic.facing import FRONT, REAR
from tarmar_engine.classic.figure import Figure, create_human
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.ruleset import Ruleset
from tarmar_engine.classic.state import GameState, IllegalAction, PendingAttack
from tests import (
    test_classic_ai,
    test_classic_melee_parity,
    test_classic_persistence,
    test_classic_spell_batch,
    test_classic_spells,
    test_classic_staff,
    test_classic_state,
    test_classic_wizard_weapons,
    test_combat_example,
)

FACES = range(1, DIE_FACES + 1)
EXACT = 1e-12


# ---- the engine's own path, rolled face by face ------------------------------


def _hits_by_multiplier(
    rules: Ruleset, dice_count: int, needed: int
) -> dict[int, tuple[int, tuple[int, ...]]]:
    """Every to-hit roll ``dice_count`` dice can make, put through the roll's
    own classifier: for each multiplier a hit carries, how many rolls land it
    and the first such roll."""
    hits: dict[int, tuple[int, tuple[int, ...]]] = {}
    for roll in itertools.product(FACES, repeat=dice_count):
        hit, multiplier, _dropped, _broke = rules.classify_roll(
            sum(roll), dice_count, needed
        )
        if hit:
            landed, first_roll = hits.get(multiplier, (0, roll))
            hits[multiplier] = (landed + 1, first_roll)
    return hits


def _rolled_hit_chance(rules: Ruleset, dice_count: int, needed: int) -> float:
    """The share of all to-hit rolls that hit, counted roll by roll."""
    hits = _hits_by_multiplier(rules, dice_count, needed)
    return sum(landed for landed, _roll in hits.values()) / DIE_FACES**dice_count


def _rolled_damage_per_hit(
    game: GameState, pending: PendingAttack, dice_count: int, *, ranged: bool
) -> float:
    """The average damage of a hit, by rolling the declared attack through
    ``Ruleset.resolve_attack`` on every damage face for each kind of hit."""
    weapon = pending.weapon or pending.attacker.ready_weapon
    needed = _declared_needed(game, pending)
    damage_dice = weapon.damage.count + pending.damage_dice_bonus
    weighted = 0.0
    hit_rolls = 0
    for multiplier, (landed, roll) in _hits_by_multiplier(
        game.rules, dice_count, needed
    ).items():
        damages = []
        for damage_roll in itertools.product(FACES, repeat=damage_dice):
            result = game.rules.resolve_attack(
                Dice(scripted=[*roll, *damage_roll]),
                pending.attacker,
                pending.target,
                zone=pending.zone,
                weapon=weapon,
                dice_count=dice_count,
                ignore_facing=pending.ignore_facing,
                range_penalty=pending.range_penalty,
                situational=pending.situational,
                situational_note=pending.situational_note,
                extra_dice=pending.damage_dice_bonus,
                ranged=ranged,
                blunted=game.practice,
            )
            assert result.hit and result.multiplier == multiplier
            damages.append(result.damage)
        weighted += landed * sum(damages) / len(damages)
        hit_rolls += landed
    return weighted / hit_rolls


def _declared_needed(game: GameState, pending: PendingAttack) -> int:
    """The to-hit number the roll computes from a declared attack."""
    return game.rules.to_hit_number(
        pending.attacker,
        zone=pending.zone,
        ignore_facing=pending.ignore_facing,
        range_penalty=pending.range_penalty,
        situational=pending.situational,
    )


def _mismatches(
    game: GameState, forecast: AttackForecast, pending: PendingAttack
) -> list[str]:
    """Every way ``forecast`` differs from the attack ``queue_attack``
    declared and the roll it makes; empty when they agree."""
    weapon = pending.weapon or pending.attacker.ready_weapon
    ranged = pending.thrown or weapon.kind == WeaponKind.MISSILE
    dice_count = game.rules.attack_dice_count(pending.target, ranged=ranged)
    expected = {
        "needed": _declared_needed(game, pending),
        "dice_count": dice_count,
        "zone": pending.zone,
        "ignore_facing": pending.ignore_facing,
        "range_penalty": pending.range_penalty,
        "situational": pending.situational,
        "situational_note": pending.situational_note,
        "extra_dice": pending.damage_dice_bonus,
        "thrown": pending.thrown,
        "ranged": ranged,
        "shots": pending.shots,
        "weapon": weapon,
    }
    found = [
        f"{field}: forecast {getattr(forecast, field)!r}, declared {value!r}"
        for field, value in expected.items()
        if getattr(forecast, field) != value
    ]
    hit_chance = _rolled_hit_chance(game.rules, dice_count, expected["needed"])
    if forecast.hit_chance != pytest.approx(hit_chance, abs=EXACT):
        found.append(f"hit_chance: forecast {forecast.hit_chance}, rolled {hit_chance}")
    damage = _rolled_damage_per_hit(game, pending, dice_count, ranged=ranged)
    if forecast.expected_damage != pytest.approx(damage, abs=EXACT):
        found.append(
            f"expected_damage: forecast {forecast.expected_damage}, rolled {damage}"
        )
    return found


def _declare(game: GameState, attacker: Figure, target: Figure) -> PendingAttack:
    """Queue ``attacker``'s attack on ``target`` and return what was declared."""
    declared_before = len(game._pending)
    game.queue_attack(attacker, target)
    return game._pending[declared_before]


def _face(figure: Figure, toward: Hex) -> None:
    figure.facing = LAYOUT.direction_to(
        figure.position, LAYOUT.line(figure.position, toward)[1]
    )


def _duel(
    attacker_gear: dict, target_gear: dict, *, combat_type=CombatType.DEATH
) -> tuple[GameState, Figure, Figure]:
    """Two humans adjacent and facing each other."""
    attacker = create_human("Attacker", 12, 12, "a", **attacker_gear)
    target = create_human("Target", 12, 12, "b", **target_gear)
    attacker.position = Hex(5, 5)
    target.position = LAYOUT.neighbor(Hex(5, 5), 0)
    _face(attacker, target.position)
    _face(target, attacker.position)
    game = GameState(
        Arena(cols=9, rows=15), [attacker, target], combat_type=combat_type
    )
    return game, attacker, target


BROADSWORD_GEAR = {"weapons": [BROADSWORD], "ready_weapon": BROADSWORD}


# ---- the numbers, worked by hand ---------------------------------------------


def test_a_broadsword_against_an_unarmoured_front() -> None:
    game, attacker, target = _duel(BROADSWORD_GEAR, {})
    forecast = game.attack_forecast(attacker, target, Option.ATTACK)
    # adjDX 12 on 3d6: the 160 rolls of 12 or less hit (3 triple, 4 double).
    assert forecast.needed == 12
    assert forecast.dice_count == THREE_DICE
    assert forecast.hit_chance == pytest.approx(160 / 216, abs=EXACT)
    assert forecast.hit_multipliers == pytest.approx(
        {3: 1 / 216, 2: 3 / 216, 1: 156 / 216}, abs=EXACT
    )
    # 2d6 averages 7; one hit in 160 is tripled and three are doubled.
    assert forecast.expected_damage == pytest.approx(7 * 165 / 160, abs=EXACT)
    assert forecast.armour_stops == 0
    assert forecast.zone == FRONT
    assert forecast.to_hit_breakdown == "DX 12"


def test_the_special_totals_hit_whatever_the_number_needed() -> None:
    rules = Ruleset()
    assert rules.hit_odds(THREE_DICE, 2) == pytest.approx(
        {3: 1 / 216, 2: 3 / 216, 1: 6 / 216}, abs=EXACT
    )
    # A 16, 17 or 18 misses even at adjDX 20.
    assert sum(rules.hit_odds(THREE_DICE, 20).values()) == pytest.approx(
        206 / 216, abs=EXACT
    )


def test_armour_and_a_frontal_shield_stop_hits_off_the_average() -> None:
    game, attacker, target = _duel(
        BROADSWORD_GEAR, {"armor": CHAINMAIL, "shield": LARGE_SHIELD}
    )
    forecast = game.attack_forecast(attacker, target, Option.ATTACK)
    assert forecast.armour_stops == 5
    # The average counts only the rolls that get past the 5 stops, so it is
    # more than the plain 7 - 5 a clamped mean would give.
    assert forecast.expected_damage > 2
    assert forecast.expected_damage == pytest.approx(
        _rolled_damage_per_hit(
            game,
            _declare_as(game, attacker, target, Option.ATTACK),
            THREE_DICE,
            ranged=False,
        ),
        abs=EXACT,
    )


def _declare_as(
    game: GameState, attacker: Figure, target: Figure, option: Option
) -> PendingAttack:
    attacker.current_option = option
    return _declare(game, attacker, target)


# ---- what queue_attack declares, and what the roll then does -----------------


def test_a_flank_blow_matches_its_declaration_and_its_roll() -> None:
    game, attacker, target = _duel(BROADSWORD_GEAR, {"armor": CHAINMAIL})
    target.facing = (target.facing + 2) % 6  # turned: the attacker is at its side
    forecast = game.attack_forecast(attacker, target, Option.ATTACK)
    pending = _declare_as(game, attacker, target, Option.ATTACK)
    assert forecast.needed == 14  # +2 flank
    assert _mismatches(game, forecast, pending) == []
    game.dice.feed(6, 6, 2, 3, 3)  # a 14 hits; 3 + 3 of damage
    (result,) = game.resolve_combat()
    assert (result.needed, result.dice_count) == (forecast.needed, forecast.dice_count)
    assert result.to_hit_breakdown == forecast.to_hit_breakdown


def test_a_defending_target_is_struck_on_four_dice() -> None:
    game, attacker, target = _duel(BROADSWORD_GEAR, BROADSWORD_GEAR)
    target.defending = True
    forecast = game.attack_forecast(attacker, target, Option.ATTACK)
    assert forecast.dice_count == 4
    assert (
        _mismatches(game, forecast, _declare_as(game, attacker, target, Option.ATTACK))
        == []
    )


def test_dodging_does_not_make_a_melee_blow_four_dice() -> None:
    game, attacker, target = _duel(BROADSWORD_GEAR, BROADSWORD_GEAR)
    target.dodging = True
    assert game.attack_forecast(attacker, target, Option.ATTACK).dice_count == 3


def test_a_ready_main_gauche_parries_one_hit_of_the_forecast() -> None:
    game, attacker, target = _duel(
        BROADSWORD_GEAR,
        {"weapons": [SHORTSWORD, MAIN_GAUCHE], "ready_weapon": SHORTSWORD},
    )
    forecast = game.attack_forecast(attacker, target, Option.ATTACK)
    assert forecast.armour_stops == 1
    assert (
        _mismatches(game, forecast, _declare_as(game, attacker, target, Option.ATTACK))
        == []
    )


def test_a_practice_bout_halves_the_damage_of_a_hit() -> None:
    game, attacker, target = _duel(BROADSWORD_GEAR, {}, combat_type=CombatType.PRACTICE)
    forecast = game.attack_forecast(attacker, target, Option.ATTACK)
    # Halved and rounded down (p.22): under half of the full 7 * 165 / 160.
    assert forecast.expected_damage < 7 * 165 / 160 / 2
    assert (
        _mismatches(game, forecast, _declare_as(game, attacker, target, Option.ATTACK))
        == []
    )


def _shooter_and_mark(weapon, distance: int) -> tuple[GameState, Figure, Figure]:
    shooter = create_human(
        "Shooter", 12, 12, "a", weapons=[weapon], ready_weapon=weapon
    )
    mark = create_human("Mark", 12, 12, "b", armor=CHAINMAIL)
    shooter.position = Hex(4, 2)
    mark.position = Hex(4, 2 + distance)
    _face(shooter, mark.position)
    _face(mark, shooter.position)
    mark.facing = (mark.facing + 3) % 6  # its back to the shooter
    return GameState(Arena(cols=9, rows=15), [shooter, mark]), shooter, mark


def test_a_missile_takes_its_range_and_no_facing_bonus() -> None:
    game, shooter, mark = _shooter_and_mark(LONGBOW, 11)
    forecast = game.attack_forecast(shooter, mark, Option.MISSILE_ATTACK)
    assert forecast.ignore_facing and forecast.ranged and not forecast.thrown
    assert forecast.range_penalty < 0
    assert forecast.needed == 12 + forecast.range_penalty  # no +4 for the rear
    assert (
        _mismatches(
            game, forecast, _declare_as(game, shooter, mark, Option.MISSILE_ATTACK)
        )
        == []
    )


def test_a_dodging_mark_is_shot_at_on_four_dice() -> None:
    game, shooter, mark = _shooter_and_mark(LONGBOW, 4)
    mark.dodging = True
    forecast = game.attack_forecast(shooter, mark, Option.MISSILE_ATTACK)
    assert forecast.dice_count == 4
    assert (
        _mismatches(
            game, forecast, _declare_as(game, shooter, mark, Option.MISSILE_ATTACK)
        )
        == []
    )


def test_a_thrown_dagger_takes_a_penalty_per_hex_and_the_rear_bonus() -> None:
    game, thrower, mark = _shooter_and_mark(DAGGER, 3)
    forecast = game.attack_forecast(thrower, mark, Option.MISSILE_ATTACK)
    assert forecast.thrown and forecast.ranged and not forecast.ignore_facing
    assert forecast.range_penalty == -3
    assert forecast.zone == REAR
    assert forecast.needed == 12 - 3 + 4
    assert (
        _mismatches(game, forecast, _declare_as(game, thrower, mark, Option.ATTACK))
        == []
    )


def _two_shot_archer() -> tuple[GameState, Figure, Figure]:
    """A longbow archer at the adjDX that looses two arrows a turn (p.14)."""
    game, archer, mark = _shooter_and_mark(LONGBOW, 4)
    archer.dexterity = LONGBOW.double_shot_dx
    return game, archer, mark


def test_a_parting_shot_forecast_looses_one_arrow_whatever_was_chosen() -> None:
    game, archer, mark = _two_shot_archer()
    archer.current_option = Option.MISSILE_ATTACK
    forecast = game.attack_forecast(archer, mark, Option.ONE_LAST_SHOT)
    assert forecast.shots == 1
    assert _declare_as(game, archer, mark, Option.ONE_LAST_SHOT).shots == 1


def test_a_missile_attack_forecast_looses_two_arrows_whatever_was_chosen() -> None:
    game, archer, mark = _two_shot_archer()
    archer.current_option = Option.ONE_LAST_SHOT
    forecast = game.attack_forecast(archer, mark, Option.MISSILE_ATTACK)
    assert forecast.shots == 2
    assert _declare_as(game, archer, mark, Option.MISSILE_ATTACK).shots == 2


# ---- a pole weapon and a charge (p.12) ---------------------------------------


SPEAR_GEAR = {"weapons": [SPEAR], "ready_weapon": SPEAR}


def test_a_braced_pole_against_a_charger_gets_plus_two_and_a_die() -> None:
    game, pikeman, charger = _duel(SPEAR_GEAR, BROADSWORD_GEAR)
    charger.current_option = Option.CHARGE_ATTACK
    forecast = game.attack_forecast(pikeman, charger, Option.ATTACK)
    assert forecast.situational == 2 and forecast.situational_note == "+2 vs charge"
    assert forecast.extra_dice == 1
    pending = _declare_as(game, pikeman, charger, Option.ATTACK)
    assert _mismatches(game, forecast, pending) == []


def test_a_pole_that_charges_a_charger_loses_the_plus_two_not_the_die() -> None:
    game, pikeman, charger = _duel(SPEAR_GEAR, BROADSWORD_GEAR)
    charger.current_option = Option.CHARGE_ATTACK
    forecast = game.attack_forecast(pikeman, charger, Option.CHARGE_ATTACK)
    assert forecast.situational == 0
    assert forecast.extra_dice == 1
    assert (
        _mismatches(
            game, forecast, _declare_as(game, pikeman, charger, Option.CHARGE_ATTACK)
        )
        == []
    )


def test_a_pole_charge_of_three_straight_hexes_earns_the_die() -> None:
    game, pikeman, foe = _duel(SPEAR_GEAR, BROADSWORD_GEAR)
    pikeman.moved_this_turn = 3
    pikeman.moved_straight = True
    assert game.attack_forecast(pikeman, foe, Option.CHARGE_ATTACK).extra_dice == 1
    assert game.attack_forecast(pikeman, foe, Option.ATTACK).extra_dice == 0
    pikeman.moved_straight = False
    assert game.attack_forecast(pikeman, foe, Option.CHARGE_ATTACK).extra_dice == 0


def test_the_option_given_stands_in_for_the_one_chosen() -> None:
    game, pikeman, charger = _duel(SPEAR_GEAR, BROADSWORD_GEAR)
    charger.current_option = Option.CHARGE_ATTACK
    pikeman.current_option = Option.CHARGE_ATTACK
    forecast = game.attack_forecast(pikeman, charger, Option.ATTACK)
    assert forecast.situational == 2
    assert pikeman.current_option == Option.CHARGE_ATTACK  # left as chosen


# ---- a forecast changes nothing ----------------------------------------------


def test_a_forecast_draws_no_dice_and_changes_no_state() -> None:
    game, attacker, target = _duel(BROADSWORD_GEAR, {"armor": CHAINMAIL})
    game.dice.feed(1, 2, 3)
    before = game.to_dict()
    game.attack_forecast(attacker, target, Option.ATTACK)
    assert game.to_dict() == before
    assert game.dice.roll_n(3) == [1, 2, 3]


def test_a_figure_without_a_ready_weapon_has_no_forecast() -> None:
    game, attacker, target = _duel({}, BROADSWORD_GEAR)
    with pytest.raises(IllegalAction, match="no ready weapon"):
        game.attack_forecast(attacker, target, Option.ATTACK)


def test_a_figure_off_the_field_has_no_forecast() -> None:
    game, attacker, target = _duel(BROADSWORD_GEAR, BROADSWORD_GEAR)
    target.position = None
    with pytest.raises(IllegalAction, match="on the field"):
        game.attack_forecast(attacker, target, Option.ATTACK)


# ---- every attack the existing classic boards declare ------------------------


BOARD_MODULES = [
    test_combat_example,
    test_classic_state,
    test_classic_ai,
    test_classic_melee_parity,
    test_classic_spells,
    test_classic_spell_batch,
    test_classic_wizard_weapons,
    test_classic_staff,
    test_classic_persistence,
]


def _boards(module: ModuleType) -> list[Callable[[], None]]:
    """The module's tests that take no fixtures: boards that run as they stand."""
    return [
        function
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if name.startswith("test_")
        and function.__module__ == module.__name__
        and not inspect.signature(function).parameters
    ]


@pytest.mark.parametrize(
    "module", BOARD_MODULES, ids=lambda module: module.__name__.rsplit(".", 1)[-1]
)
def test_every_declared_attack_matches_its_forecast(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the module's boards with every ``queue_attack`` forecast first: the
    forecast under the attacker's chosen option must equal what the attack was
    declared with and what its roll does."""
    queue_attack = GameState.queue_attack
    declarations: list[str] = []
    mismatches: list[str] = []

    def forecast_then_queue(self, attacker, target, **options) -> None:
        refusal = ""
        try:
            forecast = self.attack_forecast(attacker, target, attacker.current_option)
        except IllegalAction as error:
            forecast, refusal = None, str(error)
        declared_before = len(self._pending)
        queue_attack(self, attacker, target, **options)
        where = f"{attacker.name} at {target.name}"
        declarations.append(where)
        if forecast is None:
            mismatches.append(f"{where}: declared, but the forecast refused: {refusal}")
            return
        pending = self._pending[declared_before]
        mismatches.extend(
            f"{where}: {problem}" for problem in _mismatches(self, forecast, pending)
        )

    monkeypatch.setattr(GameState, "queue_attack", forecast_then_queue)
    for board in _boards(module):
        board()
    assert declarations, f"{module.__name__} declared no attack to compare"
    assert mismatches == []
