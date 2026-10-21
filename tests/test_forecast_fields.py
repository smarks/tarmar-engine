"""A candidate carries its forecast as fields (tarmar-engine #22).

tarmar-studio's turn menu leads each attack with "9 in 20 to hit, 2d6-1
(about 6 a hit)" and used to read those figures back out of the rationale
with a regular expression. Each test here reads the prose the same way and
holds it equal to the fields, for every kind of forecast the policy makes:
a melee swing, a shot, a throw, One Last Shot, a charge, a strike in close,
and a damaging cast, targeted or not, with the copies made of them (a
yielded charge, a borrowing cast, a cast in a grapple). Options with no
forecast carry ``None`` and no payload key.
"""

import re
from dataclasses import replace
from unittest import TestCase

from tarmar_engine import combat_math, policy
from tarmar_engine import resolution as combat
from tarmar_engine.policy import FORECAST_KEY, Candidate, Forecast
from tarmar_engine.state import BattleState

from .test_battle_rules_pass import DAGGER, SMALL_BOW
from .test_borrowing import borrower, borrowing_magic_rules
from .test_engine import duel_state
from .test_magic import example_magic_rules, wizard_state
from .test_policy import bow, make_beast_actor
from .test_state import make_combatant

#: A d20 attack's working as the rationale prints it.
D20_WORKING = re.compile(
    r"d20 (?P<bonus>[+-]\d+) vs TN (?P<target_number>\d+) "
    r"— P\(hit\) (?P<hit_chance>[\d.]+) "
    r"\((?P<winning_faces>\d+) of (?P<die_faces>\d+) faces\) "
    r"x (?P<expected_damage>[\d.]+) expected damage "
    r"\((?P<damage_expression>[^()]+?) less (?P<armour_stops>\d+) armour stops\) "
    r"= (?P<score>[\d.]+)"
)
#: A damaging cast's working as the rationale prints it.
CAST_WORKING = re.compile(
    r"P\(land\) (?P<hit_chance>[\d.]+) "
    r"x (?P<expected_damage>[\d.]+) expected damage on (?P<aimed_at>[^(]+?) "
    r"\((?P<damage_expression>[^()]+?) less (?P<armour_stops>\d+) armour stops\) "
    r"= (?P<score>[\d.]+)"
)


def menu(state: BattleState, actor_id: int = 1, **keywords) -> list[Candidate]:
    return policy.choose_option(state, state.by_id(actor_id), **keywords).candidates


def first(candidates: list[Candidate], letter: str, spell_key: str = "") -> Candidate:
    return next(
        candidate
        for candidate in candidates
        if candidate.letter == letter and candidate.spell_key == spell_key
    )


class ForecastAgreesWithProse(TestCase):
    """Assertions that a candidate's fields are what its rationale prints."""

    def assert_d20_forecast_printed(self, candidate: Candidate) -> Forecast:
        forecast = candidate.forecast
        self.assertIsNotNone(forecast, candidate.rationale)
        assert forecast is not None
        printed = D20_WORKING.search(candidate.rationale)
        self.assertIsNotNone(printed, candidate.rationale)
        assert printed is not None
        self.assertEqual(int(printed["bonus"]), forecast.bonus)
        self.assertEqual(int(printed["target_number"]), forecast.target_number)
        # The parsed figures equal the fields exactly: a field carried at
        # more precision than the line prints would differ here.
        self.assertEqual(float(printed["hit_chance"]), forecast.hit_chance)
        self.assertEqual(int(printed["winning_faces"]), forecast.winning_faces)
        self.assertEqual(int(printed["die_faces"]), combat.DIE_FACES)
        self.assertEqual(float(printed["expected_damage"]), forecast.expected_damage)
        self.assertEqual(printed["damage_expression"], forecast.damage_expression)
        self.assertEqual(int(printed["armour_stops"]), forecast.armour_stops)
        self.assertEqual(float(printed["score"]), forecast.score)
        # The faces are the chance counted on the die, as the engine counts.
        self.assertEqual(
            forecast.winning_faces, round(forecast.hit_chance * combat.DIE_FACES)
        )
        self.assert_payload_carries(candidate, forecast)
        return forecast

    def assert_cast_forecast_printed(
        self, candidate: Candidate, aimed_at: str
    ) -> Forecast:
        forecast = candidate.forecast
        self.assertIsNotNone(forecast, candidate.rationale)
        assert forecast is not None
        printed = CAST_WORKING.search(candidate.rationale)
        self.assertIsNotNone(printed, candidate.rationale)
        assert printed is not None
        # The parsed figures equal the fields exactly: a field carried at
        # more precision than the line prints would differ here.
        self.assertEqual(float(printed["hit_chance"]), forecast.hit_chance)
        self.assertEqual(float(printed["expected_damage"]), forecast.expected_damage)
        self.assertEqual(printed["aimed_at"], aimed_at)
        self.assertEqual(printed["damage_expression"], forecast.damage_expression)
        self.assertEqual(int(printed["armour_stops"]), forecast.armour_stops)
        self.assertEqual(float(printed["score"]), forecast.score)
        # A cast's chance comes from 3d6 roll-unders, not a d20.
        self.assertIsNone(forecast.target_number)
        self.assertIsNone(forecast.bonus)
        self.assertIsNone(forecast.winning_faces)
        self.assert_payload_carries(candidate, forecast)
        return forecast

    def assert_payload_carries(self, candidate: Candidate, forecast: Forecast) -> None:
        self.assertEqual(
            candidate.to_payload()[FORECAST_KEY],
            {
                "hit_chance": forecast.hit_chance,
                "damage_expression": forecast.damage_expression,
                "armour_stops": forecast.armour_stops,
                "expected_damage": forecast.expected_damage,
                "target_number": forecast.target_number,
                "bonus": forecast.bonus,
                "winning_faces": forecast.winning_faces,
            },
        )


class AttackForecastFieldsTest(ForecastAgreesWithProse):
    def test_a_melee_swing_carries_the_figures_it_prints(self):
        state = duel_state()
        strike = first(menu(state), "j")
        forecast = self.assert_d20_forecast_printed(strike)
        actor, defender = state.by_id(1), state.by_id(2)
        numbers = combat_math.attack_numbers(actor, defender, ranged=False)
        probability = combat.hit_probability(numbers.target_number, numbers.bonus)
        self.assertEqual(forecast.target_number, numbers.target_number)
        self.assertEqual(forecast.bonus, numbers.bonus)
        self.assertEqual(forecast.hit_chance, policy.round_score(probability))
        self.assertEqual(forecast.damage_expression, "2d6")
        self.assertEqual(
            forecast.expected_damage,
            policy.round_score(combat_math.expected_attack_damage(actor, defender)),
        )
        self.assertEqual(strike.score, forecast.score)

    def test_a_swing_into_armour_carries_its_figures_at_printed_precision(self):
        # 2d6 less 3 stops has a long expected damage (4.37…); the field is
        # what the line prints, not the long figure.
        state = duel_state()
        state.by_id(2).stops = 3
        strike = first(menu(state), "j")
        forecast = self.assert_d20_forecast_printed(strike)
        unrounded = combat_math.expected_attack_damage(state.by_id(1), state.by_id(2))
        self.assertNotEqual(unrounded, policy.round_score(unrounded))
        self.assertEqual(forecast.expected_damage, policy.round_score(unrounded))
        self.assertEqual(forecast.armour_stops, 3)

    def test_a_shot_at_range_carries_the_figures_it_prints(self):
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, weapon=bow()),
                make_combatant(2, q=6, r=0, stops=2),
            ]
        )
        shot = first(menu(state), "f")
        forecast = self.assert_d20_forecast_printed(shot)
        self.assertEqual(forecast.damage_expression, "1d6+2")
        self.assertEqual(shot.score, forecast.score)

    def test_a_throw_of_the_only_hand_weapon_keeps_its_forecast(self):
        # Scored 0 so the AI keeps the dagger, but the throw's odds still
        # stand for a player who takes it (#812).
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, weapon=DAGGER),
                make_combatant(2, q=4, r=0),
            ]
        )
        throw = first(menu(state), "f")
        forecast = self.assert_d20_forecast_printed(throw)
        self.assertEqual(throw.score, policy.PLAYER_ONLY_SCORE)
        self.assertGreater(forecast.score, 0)

    def test_one_last_shot_carries_the_figures_it_prints(self):
        state = duel_state()
        state.by_id(1).weapon = SMALL_BOW
        shot = first(menu(state), "l")
        forecast = self.assert_d20_forecast_printed(shot)
        self.assertEqual(forecast.damage_expression, "1d6-1")

    def test_a_charge_that_reaches_carries_the_swing_it_ends_in(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=4, r=0, facing=3),
            ],
        )
        candidates = menu(state)
        charge = first(candidates, "b")
        forecast = self.assert_d20_forecast_printed(charge)
        self.assertEqual(charge.score, forecast.score)
        # The yielded charge is the same swing, moved in Final Movement.
        self.assertEqual(first(candidates, "b_yield").forecast, forecast)

    def test_a_charge_out_of_reach_has_no_forecast(self):
        state = BattleState(
            arena_radius=20,
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=15, r=0, facing=3),
            ],
        )
        charge = first(menu(state), "b")
        self.assertEqual(charge.score, 0.0)
        self.assertIsNone(charge.forecast)
        self.assertNotIn(FORECAST_KEY, charge.to_payload())

    def test_a_strike_in_close_carries_the_figures_it_prints(self):
        state = duel_state()
        state.by_id(1).weapon = DAGGER
        state.by_id(2).prone = True  # "the enemy ... is down"
        strike = first(menu(state), "t")
        forecast = self.assert_d20_forecast_printed(strike)
        self.assertEqual(forecast.damage_expression, "1d6-1")
        self.assertEqual(strike.score, forecast.score)

    def test_a_wounded_beasts_caution_scales_the_score_not_the_forecast(self):
        state = BattleState(
            combatants=[
                make_beast_actor(1, q=0, r=0, facing=0, body=5),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )
        bite = first(menu(state), "j")
        forecast = self.assert_d20_forecast_printed(bite)
        self.assertIn("body left", bite.rationale)
        self.assertLess(bite.score, forecast.score)


class CastForecastFieldsTest(ForecastAgreesWithProse):
    def test_a_targeted_spell_carries_the_figures_it_prints(self):
        state = wizard_state()
        state.by_id(2).stops = 1
        cast = first(menu(state), "h", "fire_missile")
        forecast = self.assert_cast_forecast_printed(cast, "Fighter 2")
        self.assertEqual(forecast.damage_expression, "1d6")
        self.assertEqual(forecast.armour_stops, 1)
        self.assertEqual(cast.score, forecast.score)
        self.assertIn("P(aim)", cast.rationale)

    def test_a_cast_carries_its_chance_at_printed_precision(self):
        # 3d6 <= 12 is 0.7407…, and so is the aim roll at DEX 12: the field
        # is the chance the line prints, not the long product.
        state = wizard_state(intelligence=12, dexterity=12)
        cast = first(menu(state), "h", "fire_missile")
        forecast = self.assert_cast_forecast_printed(cast, "Fighter 2")
        unrounded = policy.three_d6_at_most(12) ** 2
        self.assertNotEqual(unrounded, policy.round_score(unrounded))
        self.assertEqual(forecast.hit_chance, policy.round_score(unrounded))

    def test_a_spell_that_ignores_armour_carries_no_stops(self):
        state = wizard_state(spells=["fatigue"])
        state.by_id(2).stops = 3
        cast = first(menu(state), "h", "fatigue")
        forecast = self.assert_cast_forecast_printed(cast, "Fighter 2")
        self.assertEqual(forecast.armour_stops, 0)
        self.assertEqual(forecast.damage_expression, "1d6+1")

    def test_a_borrowing_cast_carries_the_cast_it_borrows_for(self):
        state = borrower()
        candidates = menu(state, magic=replace(borrowing_magic_rules(), push=None))
        borrowed = next(
            candidate
            for candidate in candidates
            if candidate.borrow and candidate.spell_key == "lightning_bolt"
        )
        self.assert_cast_forecast_printed(borrowed, "Fighter 2")
        self.assertEqual(borrowed.score, policy.PLAYER_ONLY_SCORE)

    def test_a_pushed_cast_carries_no_forecast(self):
        # A push changes what the spell does, and its line prints the
        # Control Roll, not a forecast.
        state = wizard_state()
        pushed = [
            candidate
            for candidate in menu(state, magic=example_magic_rules())
            if candidate.push_mana
        ]
        self.assertTrue(pushed)
        for candidate in pushed:
            self.assertIsNone(candidate.forecast)
            self.assertNotIn(FORECAST_KEY, candidate.to_payload())

    def test_a_cast_in_a_grapple_carries_its_forecast(self):
        state = duel_state()
        held, captor = state.by_id(1), state.by_id(2)
        held.grappled_by, captor.grappling = 2, 1
        held.spells, held.mana, held.max_mana = ["fire_missile"], 5, 5
        held.spell_mastery = {"fire_missile": 3}
        cast = first(menu(state), "r", "fire_missile")
        self.assert_cast_forecast_printed(cast, "Fighter 2")


class NoForecastTest(TestCase):
    def assert_no_forecast(self, candidate: Candidate) -> None:
        self.assertIsNone(candidate.forecast, candidate.letter)
        self.assertNotIn(FORECAST_KEY, candidate.to_payload())

    def test_defend_disengage_and_grapple_carry_none(self):
        # The foe engages from the arena's edge, its back to the wall, so a
        # grapple is on the menu too.
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=5, r=0, facing=0),
                make_combatant(2, q=6, r=0, facing=3),
            ],
        )
        candidates = menu(state)
        for letter in ("k", "n", "o"):
            self.assert_no_forecast(first(candidates, letter))

    def test_moves_and_dodge_carry_none(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=6, r=0, weapon=bow()),
            ],
        )
        candidates = menu(state)
        for letter in ("a", "c"):
            self.assert_no_forecast(first(candidates, letter))

    def test_a_buff_and_a_heal_carry_none(self):
        state = wizard_state(fatigue=30)
        candidates = menu(state)
        for spell_key in ("shield", "heal"):
            self.assert_no_forecast(first(candidates, "h", spell_key))

    def test_breaking_away_from_hand_to_hand_carries_none(self):
        state = duel_state()
        state.by_id(1).hth_with.append(2)
        state.by_id(2).hth_with.append(1)
        self.assert_no_forecast(first(menu(state), "v"))

    def test_a_candidate_built_without_one_logs_as_before(self):
        plain = Candidate("k", "DEFEND", 0.3, "test")
        self.assertIsNone(plain.forecast)
        self.assertNotIn(FORECAST_KEY, plain.to_payload())


class ForecastWorkingTest(TestCase):
    def test_a_d20_forecast_prints_its_own_figures(self):
        forecast = Forecast(
            hit_chance=0.45,
            damage_expression="2d6-1",
            armour_stops=0,
            expected_damage=6.35,
            target_number=15,
            bonus=3,
            winning_faces=9,
        )
        self.assertEqual(
            forecast.working(),
            "d20 +3 vs TN 15 — P(hit) 0.450 (9 of 20 faces) x 6.350 expected "
            "damage (2d6-1 less 0 armour stops) = 2.857",
        )

    def test_a_cast_forecast_prints_whom_it_lands_on(self):
        forecast = Forecast(
            hit_chance=0.375,
            damage_expression="1d6",
            armour_stops=1,
            expected_damage=2.5,
        )
        self.assertEqual(
            forecast.working(" on Foe"),
            "P(land) 0.375 x 2.500 expected damage on Foe (1d6 less 1 armour "
            "stops) = 0.938",
        )

    def test_the_score_is_the_product_of_the_printed_factors(self):
        forecast = Forecast(
            hit_chance=0.333,
            damage_expression="1d6",
            armour_stops=0,
            expected_damage=3.0,
        )
        self.assertEqual(forecast.score, policy.product_of(0.333, 3.0))
