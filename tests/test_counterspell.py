"""Probe tests for injected Counterspell (tarmar-engine #31, split from
tarmar-studio #825).

Every number here is invented, as in ``test_magic`` and ``test_runaway``: a
2d8 opposed roll on WIS, one bonus step per 2 mana invested, ties to the
defender, and a commitment of 1 to 4 mana. The tests check the engine's
*structure*: who is offered a counter and for how much, where the opposed
roll is made whatever the DEX order, what a countered spell costs and
leaves standing, the caster's escalation and its end, and the snapshot.
"""

import functools
import json
from dataclasses import replace
from unittest import TestCase

from tarmar_engine import actions, engine, policy
from tarmar_engine.magic import CounterspellRules, MagicRules
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.state import BattleState, ContestState, CounterState, WeaponState

from .replay_scenarios import SCENARIOS
from .test_borrowing import borrowing_magic_rules
from .test_engine import ScriptedRoller, SeededStubRoller, events_of_type
from .test_magic import (
    PurposeRoller,
    example_magic_rules,
    roll_purposes,
    status_payloads,
    wizard_state,
)
from .test_runaway import example_runaway_rules
from .test_state import make_combatant


def example_counterspell_rules(**changes) -> CounterspellRules:
    """Invented Counterspell rules for the tests. Not Tarmar's numbers."""
    rules = CounterspellRules(
        opposed_dice="2d8",
        opposed_attribute="WIS",
        mana_per_bonus=2,
        ties_go_to_caster=False,
        defender_min_mana=1,
        defender_max_mana=4,
    )
    return replace(rules, **changes)


def counterspell_magic_rules(**changes) -> MagicRules:
    """The example magic rules with Counterspell, some of its fields changed."""
    return replace(
        example_magic_rules(), counterspell=example_counterspell_rules(**changes)
    )


COUNTER_PROFILE = TarmarProfile(magic=counterspell_magic_rules())
TIES_TO_CASTER_PROFILE = TarmarProfile(
    magic=counterspell_magic_rules(ties_go_to_caster=True)
)

# The caster's opposed roll (WIS 12 + its bonus), then the defender's (WIS 11
# + its bonus), as 2d8 faces.
CASTER_LOW = [1, 1]
DEFENDER_HIGH = [8, 8]
# Fire Missile after the opposed roll: a natural 9 casting roll (no tier), on
# target, 5 damage.
MISSILE_FACES = [[3, 3, 3], [2, 2, 2], [5]]


def counter_state(**defender_overrides) -> BattleState:
    """The test wizard (WIS 12, 10 mana) casting Fire Missile (level 1) at
    combatant 2, a mage (Shield known, WIS 11, 6 mana)."""
    state = wizard_state()
    caster = state.by_id(1)
    caster.chosen_letter = "h"
    caster.chosen_spell = "fire_missile"
    caster.chosen_target = 2
    defender = state.by_id(2)
    defender.spells = ["shield"]
    defender.wisdom = 11
    defender.mana = 6
    defender.max_mana = 6
    for name, value in defender_overrides.items():
        setattr(defender, name, value)
    return state


def choose_counter(state, mana, defender_id=2, caster_id=1) -> None:
    """Make the defender's chosen action a counter of ``mana``."""
    defender = state.by_id(defender_id)
    defender.chosen_letter = actions.COUNTER_OPTION
    defender.chosen_counter = (caster_id, mana)


def runner(state, faces=(), profile=COUNTER_PROFILE):
    """A turn runner on scripted faces; its events list."""
    events: list[dict] = []
    return (
        engine.TurnRunner(state, ScriptedRoller(faces), events.append, profile=profile),
        events,
    )


def countered_cast(state, faces, *, mana=3, profile=COUNTER_PROFILE, push=0):
    """The defender declares first, then the caster casts; the events."""
    choose_counter(state, mana)
    state.by_id(1).chosen_push_mana = push
    turn_runner, events = runner(state, faces, profile)
    turn_runner.declare_counter(state.by_id(2))
    turn_runner.cast_spell(state.by_id(1))
    return events


def counter_rolls(events) -> list[dict]:
    return [
        event
        for event in events_of_type(events, "roll")
        if event["payload"]["purpose"] == "counter"
    ]


def result_payload(events) -> dict:
    return status_payloads(events, "counter_result")[0]


def decide(state, actor_id, magic=None) -> policy.Decision:
    return policy.choose_option(
        state, state.by_id(actor_id), magic=magic or counterspell_magic_rules()
    )


def counters(decision) -> list[policy.Candidate]:
    return [c for c in decision.candidates if c.letter == actions.COUNTER_OPTION]


class CounterspellRulesValidationTest(TestCase):
    """Malformed Counterspell rules are refused when built, not mid-battle."""

    def test_the_dice_attribute_step_and_bounds_are_checked(self):
        for changes in (
            {"opposed_dice": "lots"},
            {"opposed_attribute": "STR"},
            {"mana_per_bonus": 0},
            {"defender_min_mana": -1},
            {"defender_max_mana": 0},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                example_counterspell_rules(**changes)

    def test_no_cap_and_a_free_counter_are_allowed(self):
        rules = example_counterspell_rules(defender_min_mana=0, defender_max_mana=None)
        self.assertEqual(list(rules.commitments(6)), [0, 1, 2, 3, 4, 5, 6])

    def test_the_arithmetic(self):
        rules = example_counterspell_rules()
        self.assertEqual(
            [rules.bonus_for(mana) for mana in range(6)], [0, 0, 1, 1, 2, 2]
        )
        self.assertEqual(list(rules.commitments(10)), [1, 2, 3, 4])
        self.assertEqual(list(rules.commitments(2)), [1, 2])
        self.assertEqual(list(rules.commitments(0)), [])
        self.assertTrue(rules.caster_gets_through(12, 11))
        self.assertFalse(rules.caster_gets_through(11, 12))
        self.assertFalse(rules.caster_gets_through(12, 12))
        self.assertTrue(
            example_counterspell_rules(ties_go_to_caster=True).caster_gets_through(
                12, 12
            )
        )

    def test_the_default_magic_rules_inject_no_counterspell(self):
        self.assertIsNone(example_magic_rules().counterspell)
        self.assertIsNone(TARMAR.magic)

    def test_the_counter_is_keyed_outside_the_lettered_tables(self):
        self.assertNotIn(actions.COUNTER_OPTION, actions.ALL_OPTIONS)
        self.assertEqual(actions.option_name(actions.COUNTER_OPTION), "COUNTER SPELL")


class CounterMenuTest(TestCase):
    """A mage is offered a counter against each enemy mage, by name."""

    def test_each_affordable_commitment_is_offered(self):
        offered = counters(decide(counter_state(), 2))
        self.assertEqual([c.counter_mana for c in offered], [1, 2, 3, 4])
        self.assertEqual({c.target_id for c in offered}, {1})
        self.assertEqual({c.score for c in offered}, {policy.PLAYER_ONLY_SCORE})
        self.assertIn("+2 on the opposed roll 2d8 + WIS 11", offered[3].rationale)
        self.assertEqual(offered[0].to_payload()["counter_mana"], 1)
        short = counters(decide(counter_state(mana=2), 2))
        self.assertEqual([c.counter_mana for c in short], [1, 2])

    def test_the_ai_never_counters(self):
        decision = decide(counter_state(), 2)
        self.assertNotEqual(decision.chosen.letter, actions.COUNTER_OPTION)
        self.assertIsNone(decision.chosen.counter_mana)

    def test_a_counter_names_a_caster_not_a_cast(self):
        # A mage the cast is not aimed at may counter its caster, and any
        # other enemy mage; a fighter who knows no spell is not offered.
        bystander = counter_state()
        bystander.combatants.append(
            make_combatant(3, q=0, r=3, spells=["shield"], mana=6)
        )
        bystander.combatants.append(make_combatant(4, q=0, r=-3))
        offered = counters(decide(bystander, 3))
        self.assertEqual({c.target_id for c in offered}, {1, 2})
        self.assertIn("Oppose Fighter 1's Fire Missile", offered[0].rationale)
        # A caster that has chosen nothing yet is countered blind.
        undecided = counter_state()
        undecided.by_id(1).chosen_letter = ""
        blind = counters(decide(undecided, 2))
        self.assertEqual([c.counter_mana for c in blind], [1, 2, 3, 4])
        self.assertIn("(not yet known)", blind[0].rationale)

    def test_no_counter_for_a_non_mage_or_an_empty_purse(self):
        self.assertEqual(counters(decide(counter_state(spells=[]), 2)), [])
        self.assertEqual(counters(decide(counter_state(mana=0), 2)), [])
        caster_without_spells = counter_state()
        caster_without_spells.by_id(1).spells = []
        self.assertEqual(counters(decide(caster_without_spells, 2)), [])

    def test_a_recorded_spell_skill_is_enough(self):
        state = counter_state(spells=[], spell_skill_levels={"shield": 1})
        self.assertEqual(len(counters(decide(state, 2))), 4)

    def test_no_counter_without_counterspell_rules(self):
        self.assertEqual(
            counters(decide(counter_state(), 2, example_magic_rules())), []
        )
        self.assertNotIn("counter_mana", decide(counter_state(), 2).chosen.to_payload())


class DeclareCounterTest(TestCase):
    def test_a_declaration_pays_and_stands(self):
        state = counter_state()
        choose_counter(state, 3)
        turn_runner, events = runner(state)
        turn_runner.declare_counter(state.by_id(2))
        defender = state.by_id(2)
        self.assertEqual(defender.mana, 6 - 3)
        self.assertEqual(defender.counter, CounterState(1, 3, state.turn))
        self.assertEqual(
            status_payloads(events, "counter_declared"),
            [{"counter_declared": True, "caster": 1, "mana": 3, "mana_left": 3}],
        )

    def test_a_defender_short_of_the_mana_declares_nothing(self):
        state = counter_state(mana=2)
        choose_counter(state, 3)
        turn_runner, events = runner(state)
        turn_runner.declare_counter(state.by_id(2))
        self.assertIsNone(state.by_id(2).counter)
        self.assertEqual(state.by_id(2).mana, 2)
        self.assertEqual(events_of_type(events, "info")[0]["payload"], {"caster": 1})

    def test_a_commitment_outside_the_bounds_or_rules_is_refused(self):
        for mana in (0, 5):
            state = counter_state()
            choose_counter(state, mana)
            with self.subTest(mana=mana), self.assertRaises(ValueError):
                runner(state)[0].declare_counter(state.by_id(2))
        state = counter_state()
        choose_counter(state, 3)
        with self.assertRaises(ValueError):
            runner(state, profile=TarmarProfile(magic=example_magic_rules()))[
                0
            ].declare_counter(state.by_id(2))
        state = counter_state()
        state.by_id(2).chosen_letter = actions.COUNTER_OPTION
        with self.assertRaises(ValueError):
            runner(state)[0].declare_counter(state.by_id(2))


class OpposedRollTest(TestCase):
    def test_a_countered_spell_makes_no_casting_roll_and_stays_paid(self):
        state = counter_state()
        events = countered_cast(state, [CASTER_LOW, DEFENDER_HIGH, *MISSILE_FACES])
        self.assertEqual(roll_purposes(events), ["counter", "counter"])
        self.assertEqual(events_of_type(events, "action"), [])
        caster, defender = state.by_id(1), state.by_id(2)
        self.assertEqual(caster.mana, 10 - 1)
        self.assertEqual(defender.mana, 6 - 3)
        self.assertIsNone(defender.counter)
        self.assertEqual(defender.fatigue, 40)
        self.assertEqual(caster.contest, ContestState("fire_missile", 2, [2], 1))
        countered = status_payloads(events, "spell_countered")[0]
        self.assertEqual(
            countered,
            {
                "spell_countered": True,
                "spell": "fire_missile",
                "defenders": [2],
                "contest_turns": 1,
                "mana_left": 9,
                "push_mana": 0,
            },
        )
        self.assertTrue(result_payload(events)["countered"])

    def test_the_bonus_arithmetic(self):
        # Cost 1 buys nothing; pushed by 3 (cost 4) it buys +2. The defender's
        # 3 mana buys +1.
        for push, caster_bonus in ((0, 0), (1, 1), (3, 2)):
            state = counter_state()
            events = countered_cast(state, [CASTER_LOW, DEFENDER_HIGH], push=push)
            caster_roll, defender_roll = counter_rolls(events)
            with self.subTest(push=push):
                self.assertEqual(caster_roll["payload"]["modifier"], 12 + caster_bonus)
                self.assertEqual(defender_roll["payload"]["modifier"], 11 + 1)
                self.assertEqual(result_payload(events)["caster_bonus"], caster_bonus)
                self.assertEqual(state.by_id(1).mana, 10 - 1 - push)

    def test_each_side_takes_its_injury_band(self):
        state = counter_state(fatigue=1)
        state.by_id(1).fatigue = 1
        caster_injury = TARMAR.reactions.injury_penalty(state.by_id(1))
        defender_injury = TARMAR.reactions.injury_penalty(state.by_id(2))
        self.assertGreater(caster_injury, 0)
        events = countered_cast(state, [CASTER_LOW, DEFENDER_HIGH])
        caster_roll, defender_roll = counter_rolls(events)
        self.assertEqual(caster_roll["payload"]["modifier"], 12 - caster_injury)
        self.assertEqual(defender_roll["payload"]["modifier"], 12 - defender_injury)

    def test_the_tie_rule_both_ways(self):
        # 8 + 12 + 1 pushed bonus = 21 against 9 + 11 + 1 = 21.
        tie = [[4, 4], [5, 4], *MISSILE_FACES]
        to_defender = counter_state()
        events = countered_cast(to_defender, tie, push=1)
        self.assertEqual(
            (
                result_payload(events)["caster_total"],
                result_payload(events)["defender_totals"][0]["total"],
            ),
            (21, 21),
        )
        self.assertTrue(result_payload(events)["countered"])
        to_caster = counter_state()
        events = countered_cast(to_caster, tie, push=1, profile=TIES_TO_CASTER_PROFILE)
        self.assertFalse(result_payload(events)["countered"])
        self.assertIn("casting", roll_purposes(events))

    def test_a_caster_who_gets_through_casts_as_ever(self):
        state = counter_state()
        events = countered_cast(state, [DEFENDER_HIGH, CASTER_LOW, *MISSILE_FACES])
        self.assertEqual(
            roll_purposes(events),
            ["counter", "counter", "casting", "spell aim", "spell damage"],
        )
        self.assertIsNone(state.by_id(1).contest)
        self.assertIsNone(state.by_id(2).counter)
        self.assertEqual(state.by_id(2).mana, 6 - 3)
        self.assertEqual(
            result_payload(events)["defender_totals"],
            [{"defender": 2, "total": 14, "mana": 3, "bonus": 1, "roll": 3}],
        )

    def test_the_caster_must_beat_the_highest_of_two_defenders(self):
        state = counter_state()
        state.combatants.append(
            make_combatant(3, q=0, r=3, spells=["shield"], mana=6, max_mana=6)
        )
        choose_counter(state, 2, defender_id=2)
        choose_counter(state, 2, defender_id=3)
        turn_runner, events = runner(state, [[4, 4], [1, 1], [8, 8]])
        turn_runner.cast_spell(state.by_id(1))
        result = result_payload(events)
        # 8 + WIS 12 = 20 beats 2 + 11 + 1 = 14, not 16 + 10 + 1 = 27.
        self.assertEqual(result["caster_total"], 20)
        self.assertEqual(
            [
                (entry["defender"], entry["total"])
                for entry in result["defender_totals"]
            ],
            [(2, 14), (3, 27)],
        )
        self.assertTrue(result["countered"])
        self.assertEqual(
            state.by_id(1).contest, ContestState("fire_missile", 2, [2, 3], 1)
        )
        self.assertEqual((state.by_id(2).mana, state.by_id(3).mana), (4, 4))

    def test_a_countered_cast_starts_no_runaway(self):
        # A natural 18 would be the casting roll's Runaway total; none is made.
        profile = TarmarProfile(
            magic=replace(counterspell_magic_rules(), runaway=example_runaway_rules())
        )
        state = counter_state()
        countered_cast(state, [CASTER_LOW, DEFENDER_HIGH, [6, 6, 6]], profile=profile)
        self.assertIsNone(state.by_id(1).runaway)

    def test_a_countered_borrowing_cast_borrows_and_rolls_its_debt(self):
        profile = TarmarProfile(
            magic=replace(
                borrowing_magic_rules(), counterspell=example_counterspell_rules()
            )
        )
        state = counter_state()
        caster = state.by_id(1)
        caster.mana = 0
        caster.chosen_borrow = True
        events = countered_cast(
            state, [CASTER_LOW, DEFENDER_HIGH, [1, 1]], profile=profile
        )
        self.assertEqual((caster.mana, caster.debt), (0, 1))
        self.assertEqual(roll_purposes(events), ["counter", "counter", "debt"])
        countered = status_payloads(events, "spell_countered")[0]
        self.assertEqual((countered["borrowed"], countered["debt"]), (1, 1))

    def test_without_counterspell_rules_a_chosen_counter_is_not_consulted(self):
        state = counter_state()
        state.by_id(2).counter = CounterState(1, 3, 0)
        turn_runner, events = runner(
            state, MISSILE_FACES, TarmarProfile(magic=example_magic_rules())
        )
        turn_runner.cast_spell(state.by_id(1))
        self.assertEqual(
            roll_purposes(events), ["casting", "spell aim", "spell damage"]
        )


class ActionsPhaseTest(TestCase):
    """The counter meets the cast at the cast, whatever the DEX order."""

    def run_actions(self, state, faces) -> list[dict]:
        turn_runner, events = runner(state, faces)
        turn_runner.phase_actions()
        return events

    def test_a_faster_and_a_slower_defender_both_resolve_at_the_cast(self):
        for defender_dexterity in (16, 8):
            state = counter_state(dexterity=defender_dexterity)
            choose_counter(state, 3)
            events = self.run_actions(state, [CASTER_LOW, DEFENDER_HIGH])
            declared = next(e for e in events if "counter_declared" in e["payload"])
            first_roll = counter_rolls(events)[0]
            with self.subTest(dexterity=defender_dexterity):
                self.assertLess(declared["sequence"], first_roll["sequence"])
                self.assertEqual(state.by_id(2).mana, 6 - 3)
                self.assertIsNotNone(state.by_id(1).contest)
                taken = [e for e in events if e["payload"].get("counter_taken_at_cast")]
                # The slower defender declared at the cast; its own action
                # comes after and pays nothing more.
                self.assertEqual(len(taken), 1 if defender_dexterity == 8 else 0)

    def test_committed_mana_is_spent_when_no_spell_comes(self):
        state = counter_state()
        state.by_id(1).chosen_letter = "c"
        choose_counter(state, 3)
        events = self.run_actions(state, [])
        self.assertEqual(state.by_id(2).mana, 6 - 3)
        self.assertIsNone(state.by_id(2).counter)
        self.assertEqual(
            status_payloads(events, "counter_lapsed"),
            [{"counter_lapsed": True, "caster": 1, "mana": 3}],
        )


class BlindCounterTurnTest(TestCase):
    """A defender that chooses before its caster counters blind; the roll is
    made at the cast, or the mana lapses if no cast comes."""

    def turn(self, caster_letter, faces_by_purpose):
        state = counter_state()
        for combatant in state.combatants:
            combatant.reset_for_turn()
        rules = counterspell_magic_rules()

        def choose(battle, actor):
            decision = policy.choose_option(battle, actor, magic=rules)
            wanted = next(
                c
                for c in decision.candidates
                if (actor.combatant_id == 2 and c.counter_mana == 3)
                or (
                    actor.combatant_id == 1
                    and c.letter == caster_letter
                    and c.spell_key in ("", "fire_missile")
                    and not c.push_mana
                )
            )
            return policy.Decision(chosen=wanted, candidates=decision.candidates)

        events: list[dict] = []
        engine.run_turn(
            state,
            # The defender wins initiative and chooses first.
            PurposeRoller({"initiative": [[1], [6]], **faces_by_purpose}),
            events.append,
            choose,
            profile=COUNTER_PROFILE,
        )
        decisions = {
            event["actor"]: event for event in events_of_type(events, "decision")
        }
        self.assertLess(
            decisions["Fighter 2"]["sequence"], decisions["Fighter 1"]["sequence"]
        )
        self.assertIn(
            "(not yet known)", decisions["Fighter 2"]["payload"]["chosen"]["rationale"]
        )
        return state, events

    def test_a_blind_counter_resolves_at_the_cast(self):
        state, events = self.turn("h", {"counter": [CASTER_LOW, DEFENDER_HIGH]})
        self.assertEqual(roll_purposes(events)[-2:], ["counter", "counter"])
        self.assertTrue(result_payload(events)["countered"])
        self.assertEqual(
            state.by_id(1).contest, ContestState("fire_missile", 2, [2], 1)
        )
        self.assertEqual(state.by_id(2).mana, 6 - 3)

    def test_a_blind_counter_against_no_cast_lapses_spent(self):
        state, events = self.turn("c", {})
        self.assertEqual(
            status_payloads(events, "counter_lapsed"),
            [{"counter_lapsed": True, "caster": 1, "mana": 3}],
        )
        self.assertEqual(state.by_id(2).mana, 6 - 3)
        self.assertIsNone(state.by_id(2).counter)
        self.assertNotIn("counter", roll_purposes(events))


class EscalationTest(TestCase):
    """The caster presses a countered spell; the defender may oppose again."""

    def contested(self, **defender_overrides) -> BattleState:
        state = counter_state(**defender_overrides)
        state.by_id(1).contest = ContestState("fire_missile", 2, [2], 1)
        for combatant in state.combatants:
            combatant.reset_for_turn()
        return state

    def test_the_menu_flags_the_escalation_and_the_ai_does_not_take_it(self):
        # Bare-handed, the caster's best choice is Fire Missile at its target.
        plain = counter_state()
        plain.by_id(1).weapon = WeaponState()
        self.assertEqual(decide(plain, 1).chosen.spell_key, "fire_missile")
        state = self.contested()
        state.by_id(1).weapon = WeaponState()
        decision = decide(state, 1)
        pressed = [c for c in decision.candidates if c.escalate]
        self.assertTrue(pressed)
        self.assertEqual(
            {(c.spell_key, c.target_id) for c in pressed}, {("fire_missile", 2)}
        )
        self.assertEqual({c.score for c in pressed}, {policy.PLAYER_ONLY_SCORE})
        self.assertTrue(pressed[0].to_payload()["escalate"])
        self.assertFalse(decision.chosen.escalate)
        self.assertEqual(decision.chosen.spell_key, "shield")

    def turn(self, state, faces_by_purpose, *, escalate, counter_mana):
        rules = counterspell_magic_rules()

        def choose(battle, actor):
            decision = policy.choose_option(battle, actor, magic=rules)
            wanted = None
            if actor.combatant_id == 1 and escalate:
                wanted = next(c for c in decision.candidates if c.escalate)
            if actor.combatant_id == 2 and counter_mana is not None:
                wanted = next(
                    c for c in decision.candidates if c.counter_mana == counter_mana
                )
            if wanted is None:
                return decision
            return policy.Decision(chosen=wanted, candidates=decision.candidates)

        events: list[dict] = []
        engine.run_turn(
            state,
            PurposeRoller({"initiative": [[6], [1]], **faces_by_purpose}),
            events.append,
            choose,
            profile=COUNTER_PROFILE,
        )
        return events

    def test_pressed_and_countered_again(self):
        state = self.contested()
        events = self.turn(
            state,
            {"counter": [CASTER_LOW, DEFENDER_HIGH]},
            escalate=True,
            counter_mana=2,
        )
        caster = state.by_id(1)
        self.assertEqual(caster.contest, ContestState("fire_missile", 2, [2], 2))
        self.assertEqual(caster.mana, 10 - 1)
        self.assertEqual(state.by_id(2).mana, 6 - 2)
        self.assertNotIn("casting", roll_purposes(events))
        decision = next(
            e for e in events_of_type(events, "decision") if e["actor"] == "Fighter 1"
        )
        self.assertTrue(decision["payload"]["chosen"]["escalate"])

    def test_the_defender_lets_it_through(self):
        state = self.contested()
        events = self.turn(
            state,
            {"casting": [[3, 3, 3]], "spell aim": [[2, 2, 2]]},
            escalate=True,
            counter_mana=None,
        )
        self.assertIsNone(state.by_id(1).contest)
        self.assertEqual(
            [p["reason"] for p in status_payloads(events, "contest_ended")],
            ["unopposed"],
        )
        self.assertIn("casting", roll_purposes(events))
        self.assertNotIn("counter", roll_purposes(events))

    def test_any_other_action_ends_the_contest(self):
        state = self.contested()
        events = self.turn(state, {}, escalate=False, counter_mana=None)
        self.assertIsNone(state.by_id(1).contest)
        ended = next(e for e in events if "contest_ended" in e["payload"])
        self.assertEqual(
            (ended["phase"], ended["payload"]["reason"]), (3, "another_action")
        )


class CounterspellSnapshotTest(TestCase):
    def test_a_counter_a_contest_and_a_choice_round_trip(self):
        state = counter_state()
        state.by_id(2).counter = CounterState(1, 3, 4)
        state.by_id(2).chosen_counter = (1, 3)
        state.by_id(1).contest = ContestState("fire_missile", 2, [2], 2)
        data = json.loads(json.dumps(state.to_dict()))
        restored = BattleState.from_dict(data)
        self.assertEqual(restored, state)
        self.assertEqual(
            data["combatants"][1]["counter"],
            {"caster_id": 1, "mana_committed": 3, "declared_turn": 4},
        )
        self.assertEqual(
            data["combatants"][0]["contest"],
            {
                "spell_key": "fire_missile",
                "target_id": 2,
                "defender_ids": [2],
                "turns": 2,
            },
        )
        self.assertEqual(data["combatants"][1]["chosen_counter"], [1, 3])

    def test_the_choice_lasts_one_turn_and_unset_nothing_is_written(self):
        state = counter_state()
        state.by_id(2).chosen_counter = (1, 3)
        state.by_id(2).reset_for_turn()
        for combatant in state.to_dict()["combatants"]:
            for name in ("counter", "contest", "chosen_counter"):
                self.assertNotIn(name, combatant)


class DefaultPathTest(TestCase):
    """With the AI choosing for everyone, Counterspell rules change nothing
    but the menu: the player's counters and flagged escalations."""

    def battle(self, scenario, seed, magic) -> tuple[list[dict], dict]:
        state = SCENARIOS[scenario]()
        roller = SeededStubRoller(seed)
        events: list[dict] = []
        chooser = functools.partial(policy.choose_option, magic=magic)
        for _ in range(8):
            engine.run_turn(
                state,
                roller,
                events.append,
                chooser,
                profile=TarmarProfile(magic=magic),
            )
        for event in events:
            event["payload"].pop("candidates", None)
        return events, state.to_dict()

    def test_the_ai_battles_the_same_with_or_without_counterspell(self):
        for scenario in sorted(SCENARIOS):
            for seed in (1, 2, 3):
                with self.subTest(scenario=scenario, seed=seed):
                    self.assertEqual(
                        self.battle(scenario, seed, counterspell_magic_rules()),
                        self.battle(scenario, seed, example_magic_rules()),
                    )
