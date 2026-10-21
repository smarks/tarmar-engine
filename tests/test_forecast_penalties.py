"""The menu forecasts the roll the engine makes (tarmar-engine #56).

The engine takes the injury band and an owed off-balance penalty off every
roll a figure makes as its action (``TurnRunner._action_roll_penalty``,
#43), and a targeted spell's aim roll takes a dodging target's -4 besides.
The policy's forecasts used to print the roll without them, so a person
reading the menu saw odds better than the dice gave. Each test here puts a
hurt, off-balance figure on the board, reads its menu, then has the engine
roll the same option with scripted dice and holds the two equal: the d20
bonus and Target Number of a swing, a shot and a strike in close, an
adjacent enemy's swing in DEFEND's threat, and the casting and aim rolls'
targets.
"""

from unittest import TestCase

from tarmar_engine import combat_math, engine, policy
from tarmar_engine import resolution as combat
from tarmar_engine.policy import Candidate
from tarmar_engine.spells import DODGE_DEX_CHECK_PENALTY
from tarmar_engine.state import BattleState, CombatantState

from .test_battle_rules_pass import DAGGER
from .test_engine import ScriptedRoller, duel_state, events_of_type
from .test_hth_table_and_destinations import in_hand_to_hand
from .test_magic import wizard_state
from .test_policy import bow
from .test_state import make_combatant

#: Fatigue 5 of 40: the severe injury band, -2 (injury-thresholds-death.md).
SEVERELY_HURT_FATIGUE = 5
#: What a severely hurt, off-balance figure's action roll loses.
HURT_AND_OFF_BALANCE = 2 + combat_math.OFF_BALANCE_PENALTY
#: How the rationale names those two penalties.
PENALTIES_NAMED = "2 injury and 2 off-balance"


def hurt_and_off_balance(figure: CombatantState) -> CombatantState:
    figure.fatigue = SEVERELY_HURT_FATIGUE
    figure.off_balance = True
    return figure


def option(
    state: BattleState, letter: str, actor_id: int = 1, spell_key: str = ""
) -> Candidate:
    decision = policy.choose_option(state, state.by_id(actor_id))
    return next(
        candidate
        for candidate in decision.candidates
        if candidate.letter == letter and candidate.spell_key == spell_key
    )


def engine_rolls(
    state: BattleState, candidate: Candidate, faces: list[list[int]], actor_id: int = 1
) -> dict[str, dict]:
    """Take ``candidate`` as the actor's action with scripted dice; each roll
    event's payload by its purpose."""
    actor = state.by_id(actor_id)
    actor.chosen_letter = candidate.letter
    actor.chosen_target = candidate.target_id
    actor.chosen_spell = candidate.spell_key
    events: list[dict] = []
    engine.TurnRunner(state, ScriptedRoller(faces), events.append).execute_action(
        actor
    )
    return {
        event["payload"]["purpose"]: event["payload"]
        for event in events_of_type(events, "roll")
    }


class D20ForecastTakesThePenaltiesTest(TestCase):
    def assert_forecast_is_the_engine_roll(
        self, state: BattleState, letter: str
    ) -> Candidate:
        candidate = option(state, letter)
        forecast = candidate.forecast
        assert forecast is not None
        # A d20 of 2 misses whatever the bonus, and is no fumble: no more dice.
        rolled = engine_rolls(state, candidate, [[2]])["attack"]
        self.assertEqual(forecast.bonus, rolled["modifier"])
        self.assertEqual(forecast.target_number, rolled["target_number"])
        self.assertEqual(
            forecast.hit_chance,
            policy.round_score(
                combat.hit_probability(rolled["target_number"], rolled["modifier"])
            ),
        )
        self.assertIn(
            f"to hit less {PENALTIES_NAMED}, d20 {rolled['modifier']:+d} vs TN",
            candidate.rationale,
        )
        return candidate

    def test_a_hurt_off_balance_swing_forecasts_the_bonus_the_engine_rolls(self):
        state = duel_state()
        unhurt = option(state, "j").forecast
        hurt_and_off_balance(state.by_id(1))
        swing = self.assert_forecast_is_the_engine_roll(state, "j")
        assert unhurt is not None and unhurt.bonus is not None
        assert swing.forecast is not None
        self.assertEqual(swing.forecast.bonus, unhurt.bonus - HURT_AND_OFF_BALANCE)
        self.assertLess(swing.forecast.hit_chance, unhurt.hit_chance)

    def test_a_hurt_off_balance_shot_forecasts_the_bonus_the_engine_rolls(self):
        state = BattleState(
            combatants=[
                hurt_and_off_balance(make_combatant(1, q=0, r=0, weapon=bow())),
                make_combatant(2, q=6, r=0, stops=2),
            ]
        )
        self.assert_forecast_is_the_engine_roll(state, "f")

    def test_a_hurt_off_balance_strike_in_close_forecasts_the_engine_roll(self):
        state = duel_state()
        hurt_and_off_balance(state.by_id(1)).weapon = DAGGER
        state.by_id(2).prone = True  # "the enemy ... is down": a way in
        self.assert_forecast_is_the_engine_roll(state, "t")

    def test_an_unhurt_figure_names_no_penalty(self):
        state = duel_state()
        swing = option(state, "j")
        self.assertNotIn("to hit less", swing.rationale)
        assert swing.forecast is not None
        rolled = engine_rolls(state, swing, [[2]])["attack"]
        self.assertEqual(swing.forecast.bonus, rolled["modifier"])


class StrikeExpectedDamageTest(TestCase):
    def test_a_strike_in_close_counts_a_natural_20s_extra_damage_roll(self):
        # The engine rolls a strike's damage dice twice on a natural 20, as
        # it does a swing's; the strike's forecast now counts it as a
        # swing's does (combat_math.expected_landed_damage).
        state = duel_state()
        state.by_id(1).weapon = DAGGER
        state.by_id(2).prone = True
        forecast = option(state, "t").forecast
        assert forecast is not None
        stops = forecast.armour_stops
        with_criticals = combat_math.expected_landed_damage(
            forecast.damage_expression, stops
        )
        self.assertEqual(forecast.expected_damage, policy.round_score(with_criticals))
        self.assertGreater(
            with_criticals,
            combat_math.expected_damage(forecast.damage_expression, stops),
        )

    def test_a_swing_reads_the_same_helper(self):
        state = duel_state()
        actor, defender = state.by_id(1), state.by_id(2)
        stops = combat.applied_armour_stops(
            defender.stops, actor.weapon.weapon_class, defender.armour_tier
        )
        self.assertEqual(
            combat_math.expected_attack_damage(actor, defender),
            combat_math.expected_landed_damage(actor.weapon.damage, stops),
        )


class ThreatTakesTheEnemysPenaltiesTest(TestCase):
    def test_defends_threat_is_the_hurt_off_balance_enemys_real_swing(self):
        state = duel_state()
        enemy = hurt_and_off_balance(state.by_id(2))
        actor = state.by_id(1)
        defend = option(state, "k")
        # The enemy's swing as the engine rolls it.
        swing = option(state, "j", actor_id=2)
        rolled = engine_rolls(state, swing, [[2]], actor_id=2)["attack"]
        incoming = combat.hit_probability(
            rolled["target_number"], rolled["modifier"]
        ) * combat_math.expected_attack_damage(enemy, actor)
        self.assertIn(
            f"({policy.score_text(incoming)} expected incoming damage / 2",
            defend.rationale,
        )
        unpenalised = combat_math.attack_numbers(enemy, actor, ranged=False)
        self.assertEqual(rolled["modifier"], unpenalised.bonus - HURT_AND_OFF_BALANCE)


class CastForecastTakesThePenaltiesTest(TestCase):
    def cast_and_rolls(self, state: BattleState) -> tuple[Candidate, dict[str, dict]]:
        cast = option(state, "h", spell_key="fire_missile")
        # 1, 1, 1 casts whatever the target; 6, 6, 6 then misses the aim.
        return cast, engine_rolls(state, cast, [[1, 1, 1], [6, 6, 6]])

    def test_a_hurt_off_balance_casters_odds_are_the_engines_rolls(self):
        state = wizard_state()
        hurt_and_off_balance(state.by_id(1))
        cast, rolls = self.cast_and_rolls(state)
        casting = rolls["casting"]["target_number"]
        aim = rolls["spell aim"]["target_number"]
        self.assertEqual(casting, 14 - HURT_AND_OFF_BALANCE)
        self.assertIn(
            f"3d6 ≤ INT 14 less {PENALTIES_NAMED} = {casting} — P(cast) "
            f"{policy.score_text(policy.cast_success_chance(casting, None))}",
            cast.rationale,
        )
        dexterity = state.by_id(1).dexterity
        self.assertIn(
            f"P(aim) {policy.score_text(policy.three_d6_at_most(aim))} "
            f"(3d6 ≤ DEX {dexterity} less {PENALTIES_NAMED} = {aim})",
            cast.rationale,
        )
        assert cast.forecast is not None
        self.assertEqual(
            cast.forecast.hit_chance,
            policy.round_score(
                policy.three_d6_at_most(casting) * policy.three_d6_at_most(aim)
            ),
        )

    def test_the_aim_at_a_dodging_target_takes_the_dodge_too(self):
        state = wizard_state()
        hurt_and_off_balance(state.by_id(1))
        state.by_id(2).dodging = True
        cast, rolls = self.cast_and_rolls(state)
        aim = rolls["spell aim"]["target_number"]
        dexterity = state.by_id(1).dexterity
        self.assertEqual(
            aim, dexterity - HURT_AND_OFF_BALANCE - DODGE_DEX_CHECK_PENALTY
        )
        self.assertIn(
            f"(3d6 ≤ DEX {dexterity} less 2 injury, 2 off-balance and "
            f"{DODGE_DEX_CHECK_PENALTY} for the target's dodge = {aim})",
            cast.rationale,
        )

    def test_an_unhurt_caster_reads_as_before(self):
        state = wizard_state()
        cast, rolls = self.cast_and_rolls(state)
        self.assertIn("3d6 ≤ INT 14 — P(cast)", cast.rationale)
        self.assertEqual(rolls["casting"]["target_number"], 14)


class BreakingAwayForecastsNextTurnTest(TestCase):
    def test_next_turns_blows_owe_no_off_balance(self):
        # The break-away spends this turn's action, and the off-balance with
        # it; next turn's blows take the injury band alone. The strike the
        # figure could make this turn owes both.
        state = in_hand_to_hand(duel_state())
        figure = hurt_and_off_balance(state.by_id(1))
        partner = state.by_id(2)
        decision = policy.choose_option(state, figure)
        scores = {candidate.letter: candidate for candidate in decision.candidates}
        next_turn = policy._hth_strike_forecast(
            state, figure, partner, next_turn=True
        )[0]
        this_turn = scores["t"].forecast
        assert this_turn is not None and this_turn.bonus is not None
        self.assertEqual(
            next_turn.bonus, this_turn.bonus + combat_math.OFF_BALANCE_PENALTY
        )
        self.assertIn(
            f"{policy.score_text(next_turn.score)} striking in close if not",
            scores["v"].rationale,
        )

    def test_once_free_the_weapon_owes_no_off_balance(self):
        # "the Broadsword X if free" is next turn's swing: the injury band
        # stays, the off-balance this turn's break-away spends does not.
        state = in_hand_to_hand(duel_state())
        figure = hurt_and_off_balance(state.by_id(1))
        partner = state.by_id(2)
        break_away = option(state, "v")
        next_turn = policy._attack_forecast(
            figure, partner, ranged=False, next_turn=True
        )[0]
        this_turn = policy._attack_forecast(figure, partner, ranged=False)[0]
        self.assertGreater(next_turn.score, this_turn.score)
        self.assertIn(
            f"the {figure.weapon.name} {policy.score_text(next_turn.score)} if free",
            break_away.rationale,
        )
