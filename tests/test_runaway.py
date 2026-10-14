"""Probe tests for Runaway's later turns (tarmar-engine #30, split from
tarmar-studio #825).

Every number here is invented, as in ``test_magic`` and ``test_borrowing``:
a casting roll's Runaway on a natural 14 (``test_magic``'s casting failure
rules), a drain of 2 x the original cost + 1 a turn, moving to fatigue once the mana
is gone, death at 2 x the fatigue maximum below zero, a 2d8 self anchor
against WIS, a 2d8 helper anchor against INT at 3 x the original cost, a
failed helper caught, and a growth of 2 a turn. The tests check the engine's
*structure*: where a Runaway begins, how it feeds across turns, the collapse
and the death line, and the anchor rolls on the menu and in phase 5.
"""

import json
from dataclasses import replace
from functools import partial
from unittest import TestCase

from tarmar_engine import engine, policy
from tarmar_engine.magic import MagicRules, RollUnderRule, RunawayRules
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.state import BattleState, RunawayState

from .test_borrowing import (
    BOLT_FACES,
    SURGE,
    borrow_cast,
    borrower,
    borrowing_magic_rules,
)
from .test_engine import ScriptedRoller, events_of_type
from .test_magic import (
    PurposeRoller,
    cast,
    control_payload,
    example_magic_rules,
    status_payloads,
    with_casting_failure,
    wizard_state,
)


def example_runaway_rules(**changes) -> RunawayRules:
    """Invented Runaway rules for the tests. Not Tarmar's numbers."""
    rules = RunawayRules(
        drain_multiplier_of_cost=2,
        drain_flat=1,
        overflow_pool="fatigue",
        death_multiplier_of_max=2,
        self_anchor=RollUnderRule(dice="2d8", attribute="WIS"),
        helper_anchor=RollUnderRule(dice="2d8", attribute="INT"),
        helper_cost_multiplier_of_cost=3,
        helper_failure_catches_helper=True,
        effect_bonus_per_turn=2,
    )
    return replace(rules, **changes)


def runaway_magic_rules(**changes) -> MagicRules:
    """The example magic rules with Runaway, some of its fields changed."""
    return replace(example_magic_rules(), runaway=example_runaway_rules(**changes))


RUNAWAY_PROFILE = TarmarProfile(magic=runaway_magic_rules())
#: Runaway rules with the example casting failure rules beside them, so the
#: casting roll's injected Runaway total starts one.
FAILING_RUNAWAY_PROFILE = TarmarProfile(
    magic=with_casting_failure(runaway_magic_rules())
)
#: The example casting roll's Runaway total, 14.
RUNAWAY_TOTAL_FACES = [5, 5, 4]
#: Borrowing whose failure is a Runaway, with Runaway rules beside it.
SURGE_PROFILE = TarmarProfile(
    magic=replace(borrowing_magic_rules(failure=SURGE), runaway=example_runaway_rules())
)


def running_state(*, original_cost=2, **caster_overrides) -> BattleState:
    """The test wizard (INT 14, WIS 12, 10 mana, 40 fatigue) with Fire
    Missile running away at an original cost of ``original_cost``: a drain of
    2 x that + 1 a turn under the example rules."""
    state = wizard_state(**caster_overrides)
    state.by_id(1).runaway = RunawayState("fire_missile", original_cost)
    return state


def with_helper(state, **helper_overrides) -> BattleState:
    """Make combatant 2 a mage (INT 13, Shield known, 10 mana)."""
    helper = state.by_id(2)
    helper.spells = ["shield"]
    helper.intelligence = 13
    helper.mana = 10
    helper.max_mana = 10
    for name, value in helper_overrides.items():
        setattr(helper, name, value)
    return state


def runner(state, faces=(), profile=RUNAWAY_PROFILE):
    """A turn runner on scripted faces; its events list."""
    events: list[dict] = []
    return engine.TurnRunner(
        state, ScriptedRoller(faces), events.append, profile=profile
    ), events


def feed(state, profile=RUNAWAY_PROFILE) -> list[dict]:
    """Run phase 2 once; the events."""
    turn_runner, events = runner(state, profile=profile)
    turn_runner.phase_renew_spells()
    return events


def anchor(state, anchorer_id, faces, profile=RUNAWAY_PROFILE, owner_id=1):
    """Anchor ``owner_id``'s Runaway as ``anchorer_id``'s action; the events."""
    anchorer = state.by_id(anchorer_id)
    anchorer.chosen_anchor = owner_id
    turn_runner, events = runner(state, faces, profile)
    turn_runner.anchor_runaway(anchorer)
    return events


def anchor_payload(events) -> dict:
    return status_payloads(events, "anchor_target")[0]


def anchor_roll(events) -> dict:
    return next(
        event["payload"]
        for event in events_of_type(events, "roll")
        if event["payload"]["purpose"] == "anchor"
    )


class RunawayRulesValidationTest(TestCase):
    """Malformed Runaway rules are refused when built, not mid-battle."""

    def test_the_pool_multipliers_drain_and_rolls_are_checked(self):
        for changes in (
            {"overflow_pool": "mana"},
            {"drain_multiplier_of_cost": -1},
            {"drain_flat": -1},
            {"drain_multiplier_of_cost": 0, "drain_flat": 0},
            {"death_multiplier_of_max": -1},
            {"helper_cost_multiplier_of_cost": -1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                example_runaway_rules(**changes)
        with self.assertRaises(ValueError):
            RollUnderRule(dice="lots", attribute="WIS")
        with self.assertRaises(ValueError):
            RollUnderRule(dice="2d8", attribute="STR")

    def test_a_flat_drain_or_no_death_line_is_allowed(self):
        rules = example_runaway_rules(
            drain_multiplier_of_cost=0, death_multiplier_of_max=None
        )
        self.assertEqual(rules.drain(5), 1)
        self.assertFalse(rules.past_death_line(wizard_state(fatigue=-999).by_id(1)))

    def test_the_arithmetic(self):
        rules = example_runaway_rules()
        self.assertEqual(rules.drain(3), 7)
        self.assertEqual(rules.helper_cost(3), 9)
        self.assertEqual(rules.effect_bonus(4), 8)
        caster = wizard_state().by_id(1)
        self.assertEqual(rules.overflow_maximum(caster), 40)
        caster.fatigue = -79
        self.assertFalse(rules.past_death_line(caster))
        caster.fatigue = -80
        self.assertTrue(rules.past_death_line(caster))

    def test_the_default_magic_rules_inject_no_runaway(self):
        self.assertIsNone(example_magic_rules().runaway)
        self.assertIsNone(TARMAR.magic)


class RunawayBeginsTest(TestCase):
    """Each path that flags a Runaway starts one, with Runaway rules only."""

    def begins(self, events) -> list[dict]:
        return status_payloads(events, "runaway_begins")

    def test_an_automatic_runaway_on_the_control_roll(self):
        state = wizard_state()
        events = cast(state, [[3, 3, 3], [1, 1]], profile=RUNAWAY_PROFILE, push=1)
        self.assertTrue(control_payload(events)["runaway"])
        self.assertEqual(state.by_id(1).runaway, RunawayState("fire_missile", 2))
        self.assertEqual(
            self.begins(events),
            [{"runaway_begins": True, "spell": "fire_missile", "original_cost": 2}],
        )

    def test_a_runaway_band_on_the_control_roll(self):
        state = wizard_state()
        # WIS 12 less 2 x 3 pushed = 6; a 12 misses by 6, the Runaway band.
        events = cast(state, [[3, 3, 3], [6, 6]], profile=RUNAWAY_PROFILE, push=3)
        self.assertEqual(control_payload(events)["control_band"], "torrent")
        self.assertEqual(state.by_id(1).runaway, RunawayState("fire_missile", 4))

    def test_a_borrowing_failure_that_is_a_runaway(self):
        state = borrower()
        events = borrow_cast(state, [*BOLT_FACES, [8, 8]], profile=SURGE_PROFILE)
        self.assertTrue(status_payloads(events, "debt_failure")[0]["runaway"])
        self.assertEqual(state.by_id(1).runaway, RunawayState("lightning_bolt", 3))
        self.assertEqual(len(self.begins(events)), 1)

    def test_the_casting_rolls_runaway_total(self):
        state = wizard_state()
        events = cast(state, [RUNAWAY_TOTAL_FACES], profile=FAILING_RUNAWAY_PROFILE)
        self.assertEqual(state.by_id(1).runaway, RunawayState("fire_missile", 1))
        self.assertEqual(self.begins(events)[0]["original_cost"], 1)
        # The other mana-lost failure, 12, starts nothing.
        lost = wizard_state(intelligence=18)
        cast(lost, [[4, 4, 4]], profile=FAILING_RUNAWAY_PROFILE)
        self.assertIsNone(lost.by_id(1).runaway)
        self.assertEqual(lost.by_id(1).mana, 9)

    def test_without_casting_failure_rules_no_cast_starts_a_runaway(self):
        # INT 10: every total here fails, and none is a Runaway total.
        for faces in ([4, 4, 4], RUNAWAY_TOTAL_FACES, [6, 6, 5], [6, 6, 6]):
            with self.subTest(faces=faces):
                state = wizard_state(intelligence=10)
                events = cast(state, [faces], profile=RUNAWAY_PROFILE)
                self.assertIsNone(state.by_id(1).runaway)
                self.assertEqual(self.begins(events), [])

    def test_without_runaway_rules_the_flag_is_all(self):
        cases = (
            (wizard_state(), [[3, 3, 3], [1, 1]], 1, example_magic_rules()),
            (
                wizard_state(),
                [RUNAWAY_TOTAL_FACES],
                0,
                with_casting_failure(example_magic_rules()),
            ),
        )
        for state, faces, push, rules in cases:
            with self.subTest(faces=faces):
                events = cast(
                    state, faces, profile=TarmarProfile(magic=rules), push=push
                )
                self.assertIsNone(state.by_id(1).runaway)
                self.assertEqual(self.begins(events), [])
        surge = borrower()
        events = borrow_cast(
            surge,
            [*BOLT_FACES, [8, 8]],
            profile=TarmarProfile(magic=borrowing_magic_rules(failure=SURGE)),
        )
        self.assertTrue(status_payloads(events, "debt_failure")[0]["runaway"])
        self.assertIsNone(surge.by_id(1).runaway)
        self.assertEqual(self.begins(events), [])

    def test_a_second_runaway_keeps_the_first(self):
        state = running_state()
        events = cast(
            state, [RUNAWAY_TOTAL_FACES], profile=FAILING_RUNAWAY_PROFILE, spell="heal"
        )
        self.assertEqual(state.by_id(1).runaway, RunawayState("fire_missile", 2))
        self.assertEqual(self.begins(events), [])
        self.assertEqual(
            events_of_type(events, "info")[-1]["payload"],
            {"spell": "heal", "runaway_kept": "fire_missile"},
        )


class RunawayDrainTest(TestCase):
    """Phase 2: mana first, then the collapse and the overflow pool."""

    def drains(self, events) -> list[dict]:
        return status_payloads(events, "runaway_drain")

    def test_the_drain_across_turns(self):
        state = running_state(mana=7)
        caster = state.by_id(1)
        first = feed(state)
        self.assertEqual(
            self.drains(first),
            [
                {
                    "runaway_drain": True,
                    "spell": "fire_missile",
                    "turn": 1,
                    "drained_mana": 5,
                    "drained_pool": 0,
                    "pool": "fatigue",
                    "pool_left": 40,
                    "mana": 2,
                    "unconscious": False,
                    "effect_bonus_per_turn": 2,
                }
            ],
        )
        self.assertTrue(caster.conscious)
        second = feed(state)
        drain = self.drains(second)[0]
        self.assertEqual(
            (drain["turn"], drain["drained_mana"], drain["drained_pool"]), (2, 2, 3)
        )
        self.assertEqual((caster.mana, caster.fatigue), (0, 37))
        self.assertTrue(drain["unconscious"])
        self.assertFalse(caster.conscious)
        self.assertTrue(caster.prone)
        collapse = status_payloads(second, "unconscious")
        # The drain, then the engine's own collapse event.
        self.assertEqual(collapse[-1], {"unconscious": True, "chain": []})
        self.assertIn(
            "falls unconscious", events_of_type(second, "status")[0]["message"]
        )
        third = feed(state)
        drain = self.drains(third)[0]
        self.assertEqual((drain["drained_mana"], drain["drained_pool"]), (0, 5))
        self.assertEqual(caster.fatigue, 32)
        # The collapse is logged once.
        self.assertEqual(len(status_payloads(third, "chain")), 0)
        self.assertEqual(caster.runaway, RunawayState("fire_missile", 2, 3))

    def test_the_drain_comes_before_the_renewals(self):
        state = running_state(mana=6, active_spells=["shield"])
        events = feed(state)
        drain = self.drains(events)[0]
        renewal = next(event for event in events if event["event_type"] == "action")
        self.assertLess(
            events.index(next(e for e in events if e["payload"] is drain)),
            events.index(renewal),
        )
        self.assertEqual(state.by_id(1).mana, 0)

    def test_the_overflow_pool_is_injected(self):
        state = running_state(mana=0)
        feed(state, TarmarProfile(magic=runaway_magic_rules(overflow_pool="body")))
        caster = state.by_id(1)
        self.assertEqual((caster.fatigue, caster.body), (40, 22))

    def test_the_death_line(self):
        state = running_state(mana=0, fatigue=-76, conscious=False)
        events = feed(state)
        caster = state.by_id(1)
        self.assertFalse(caster.alive)
        self.assertIsNone(caster.runaway)
        self.assertIsNone(BattleState.from_dict(state.to_dict()).by_id(1).runaway)
        death = status_payloads(events, "runaway_death")[0]
        self.assertEqual(death["pool_left"], -81)
        drain_sequence = next(
            event["sequence"]
            for event in events
            if event["payload"].get("runaway_drain")
        )
        self.assertEqual(
            events_of_type(events, "death")[0]["payload"],
            {"fatal_chain": [drain_sequence]},
        )
        # A dead caster's Runaway feeds no more.
        self.assertEqual(self.drains(feed(state)), [])

    def test_the_death_chain_keeps_what_came_before(self):
        state = running_state(mana=0, fatigue=-76, conscious=False, fatal_chain=[3, 4])
        events = feed(state)
        chain = events_of_type(events, "death")[0]["payload"]["fatal_chain"]
        self.assertEqual(chain[:2], [3, 4])
        self.assertEqual(len(chain), 3)

    def test_a_drain_that_spares_the_pool_checks_no_death_line(self):
        # Already past the line from other harm, but this drain takes mana only.
        state = running_state(mana=10, fatigue=-90, conscious=False)
        events = feed(state)
        self.assertTrue(state.by_id(1).alive)
        self.assertEqual(status_payloads(events, "runaway_death"), [])

    def test_any_death_ends_the_runaway(self):
        state = running_state()
        turn_runner, _events = runner(state)
        turn_runner._die(state.by_id(1))
        self.assertIsNone(state.by_id(1).runaway)

    def test_no_death_line(self):
        state = running_state(mana=0, fatigue=-76, conscious=False)
        feed(
            state,
            TarmarProfile(magic=runaway_magic_rules(death_multiplier_of_max=None)),
        )
        self.assertTrue(state.by_id(1).alive)

    def test_without_runaway_rules_nothing_feeds(self):
        state = running_state(mana=7)
        self.assertEqual(self.drains(feed(state, TARMAR)), [])
        self.assertEqual(state.by_id(1).mana, 7)


class AnchorTest(TestCase):
    """Phase 5's anchor, by the caster and by a helper."""

    def test_a_held_self_anchor(self):
        state = running_state(active_spells=[])
        state.by_id(1).runaway = RunawayState("shield", 1, 3)
        events = anchor(state, 1, [[2, 2]])
        caster = state.by_id(1)
        self.assertIsNone(caster.runaway)
        self.assertEqual(caster.active_spells, ["shield"])
        self.assertEqual(caster.mana, 10)
        roll = anchor_roll(events)
        self.assertEqual(
            (roll["specification"], roll["versus"], roll["target_number"]),
            ("2d8", "WIS", 12),
        )
        self.assertEqual(roll["outcome"], "held")
        payload = anchor_payload(events)
        self.assertEqual(
            (payload["anchor_target"], payload["helper"], payload["held"]),
            (1, None, True),
        )
        self.assertEqual(payload["runaway_turns_running"], 3)

    def test_a_failed_self_anchor(self):
        state = running_state()
        events = anchor(state, 1, [[7, 6]])
        self.assertEqual(state.by_id(1).runaway, RunawayState("fire_missile", 2))
        self.assertEqual(anchor_roll(events)["outcome"], "failed")
        payload = anchor_payload(events)
        self.assertEqual((payload["held"], payload["caught"]), (False, False))
        self.assertEqual(status_payloads(events, "runaway_begins"), [])

    def test_a_held_helper_anchor(self):
        state = with_helper(running_state(active_spells=["shield"]))
        state.by_id(1).runaway = RunawayState("shield", 2)
        events = anchor(state, 2, [[1, 1]])
        caster, helper = state.by_id(1), state.by_id(2)
        self.assertIsNone(caster.runaway)
        self.assertEqual(helper.mana, 10 - 3 * 2)
        self.assertEqual(caster.active_spells, [])
        self.assertEqual(helper.active_spells, ["shield"])
        roll = anchor_roll(events)
        self.assertEqual((roll["versus"], roll["target_number"]), ("INT", 13))
        payload = anchor_payload(events)
        self.assertEqual(
            (payload["helper"], payload["paid"], payload["held"]), (2, 6, True)
        )

    def test_a_held_anchor_of_a_spell_that_is_not_continuing_just_ends_it(self):
        state = running_state()
        anchor(state, 1, [[1, 1]])
        caster = state.by_id(1)
        self.assertIsNone(caster.runaway)
        self.assertEqual(caster.active_spells, [])
        events = feed(state)
        self.assertEqual(events_of_type(events, "action"), [])
        self.assertEqual(caster.mana, 10)

    def test_a_caught_helper_already_feeding_one_keeps_it(self):
        state = with_helper(running_state())
        state.by_id(2).runaway = RunawayState("shield", 1)
        events = anchor(state, 2, [[8, 8]])
        self.assertEqual(state.by_id(2).runaway, RunawayState("shield", 1))
        self.assertEqual(
            events_of_type(events, "info")[-1]["payload"],
            {"spell": "fire_missile", "runaway_kept": "shield"},
        )

    def test_a_failed_helper_is_caught(self):
        state = with_helper(running_state())
        events = anchor(state, 2, [[8, 8]])
        helper = state.by_id(2)
        self.assertEqual(helper.mana, 4)
        self.assertEqual(state.by_id(1).runaway, RunawayState("fire_missile", 2))
        self.assertEqual(
            helper.runaway, RunawayState("fire_missile", 2, 0, caught_from=1)
        )
        self.assertTrue(anchor_payload(events)["caught"])
        self.assertEqual(status_payloads(events, "runaway_begins")[0]["caught_from"], 1)

    def test_a_failed_helper_is_not_caught_when_the_rules_say_so(self):
        state = with_helper(running_state())
        profile = TarmarProfile(
            magic=runaway_magic_rules(helper_failure_catches_helper=False)
        )
        events = anchor(state, 2, [[8, 8]], profile=profile)
        self.assertIsNone(state.by_id(2).runaway)
        self.assertEqual(state.by_id(2).mana, 4)
        self.assertFalse(anchor_payload(events)["caught"])

    def test_a_helper_short_of_mana_rolls_nothing(self):
        state = with_helper(running_state(), mana=5)
        events = anchor(state, 2, [[1, 1]])
        self.assertEqual(events_of_type(events, "roll"), [])
        self.assertEqual(state.by_id(2).mana, 5)
        self.assertIsNotNone(state.by_id(1).runaway)

    def test_nothing_left_to_anchor(self):
        state = wizard_state()
        events = anchor(state, 1, [[1, 1]])
        self.assertEqual(events_of_type(events, "roll"), [])
        self.assertEqual(
            events_of_type(events, "info")[0]["payload"], {"anchor_target": 1}
        )

    def test_an_anchor_needs_runaway_rules(self):
        state = running_state()
        with self.assertRaisesRegex(ValueError, "no Runaway rules"):
            anchor(
                state, 1, [[1, 1]], profile=TarmarProfile(magic=example_magic_rules())
            )

    def test_the_anchored_spell_is_the_anchorers_to_renew(self):
        state = with_helper(running_state())
        state.by_id(1).runaway = RunawayState("shield", 1)
        anchor(state, 2, [[1, 1]])
        helper = state.by_id(2)
        self.assertEqual((helper.active_spells, helper.mana), (["shield"], 7))
        events = feed(state)
        self.assertEqual(helper.mana, 6)
        self.assertEqual(
            events_of_type(events, "action")[0]["payload"]["spell"], "shield"
        )
        # Short of the cost, it lapses like any active spell.
        helper.mana = 0
        feed(state)
        self.assertEqual(helper.active_spells, [])


class AnchoredGrowthTest(TestCase):
    """The growth a Runaway earned lands on the anchorer's recast."""

    # Fire Missile: a natural 9 (no tier), on target, a damage roll of 5.
    MISSILE_FACES = [[3, 3, 3], [2, 2, 2], [5]]

    def anchored_state(self) -> BattleState:
        state = running_state()
        state.by_id(1).runaway = RunawayState("fire_missile", 1, 3)
        anchor(state, 1, [[1, 1]])
        return state

    def raw_damage(self, state) -> int:
        events = cast(state, self.MISSILE_FACES, profile=RUNAWAY_PROFILE)
        return events_of_type(events, "damage")[0]["payload"]["raw"]

    def test_the_bonus_lands_on_the_next_cast_and_is_used_up(self):
        state = self.anchored_state()
        caster = state.by_id(1)
        self.assertEqual(caster.anchored_turns_running, {"fire_missile": 3})
        self.assertEqual(caster.active_spells, [])
        # Turns later, with no time limit.
        feed(state)
        feed(state)
        self.assertEqual(self.raw_damage(state), 5 + 2 * 3)
        self.assertEqual(caster.anchored_turns_running, {})
        self.assertEqual(self.raw_damage(state), 5)

    def test_a_pushed_recast_the_control_roll_stops_keeps_the_growth(self):
        state = self.anchored_state()
        # WIS 12 less 2 x 2 pushed = 8; a 12 misses by 4, a band that stops it.
        stopped = cast(state, [[3, 3, 3], [6, 6]], profile=RUNAWAY_PROFILE, push=2)
        self.assertFalse(control_payload(stopped)["takes_effect"])
        self.assertEqual(state.by_id(1).anchored_turns_running, {"fire_missile": 3})
        self.assertEqual(self.raw_damage(state), 5 + 2 * 3)

    def test_a_continuing_spells_record_goes_when_it_lapses(self):
        state = running_state()
        caster = state.by_id(1)
        caster.runaway = RunawayState("shield", 1, 2)
        anchor(state, 1, [[1, 1]])
        self.assertEqual(caster.anchored_turns_running, {"shield": 2})
        feed(state)  # renewed: the record stays
        self.assertEqual(caster.anchored_turns_running, {"shield": 2})
        caster.mana = 0
        feed(state)
        self.assertEqual(caster.active_spells, [])
        self.assertEqual(caster.anchored_turns_running, {})

    def test_a_helper_takes_the_growth_and_the_caster_loses_it(self):
        state = with_helper(running_state(active_spells=["shield"]))
        state.by_id(1).anchored_turns_running = {"shield": 1}
        state.by_id(1).runaway = RunawayState("shield", 1, 2)
        anchor(state, 2, [[1, 1]])
        self.assertEqual(state.by_id(1).anchored_turns_running, {})
        self.assertEqual(state.by_id(2).anchored_turns_running, {"shield": 2})
        self.assertEqual(state.by_id(2).active_spells, ["shield"])

    def test_the_growth_round_trips(self):
        state = self.anchored_state()
        data = json.loads(json.dumps(state.to_dict()))
        self.assertEqual(
            data["combatants"][0]["anchored_turns_running"], {"fire_missile": 3}
        )
        self.assertNotIn("anchored_turns_running", data["combatants"][1])
        self.assertEqual(BattleState.from_dict(data), state)


class AnchorMenuTest(TestCase):
    """The menu offers the anchors the rules allow; the AI takes its own."""

    def decide(self, state, actor_id, rules=None):
        rules = runaway_magic_rules() if rules is None else rules
        return policy.choose_option(state, state.by_id(actor_id), magic=rules)

    def anchors(self, decision) -> list[policy.Candidate]:
        return [c for c in decision.candidates if c.letter == policy.ANCHOR_OPTION]

    def test_the_ai_anchors_its_own(self):
        decision = self.decide(running_state(), 1)
        self.assertEqual(decision.chosen.letter, "anchor")
        self.assertEqual(decision.chosen.anchor_target, 1)
        self.assertEqual(decision.chosen.name, "Anchor Fire Missile")
        self.assertEqual(decision.chosen.to_payload()["anchor_target"], 1)
        self.assertIn("2d8 ≤ WIS 12", decision.chosen.rationale)

    def test_a_helper_is_offered_another_mages_runaway_at_zero(self):
        state = with_helper(running_state())
        decision = self.decide(state, 2)
        offered = self.anchors(decision)
        self.assertEqual([c.anchor_target for c in offered], [1])
        self.assertEqual(offered[0].score, policy.PLAYER_ONLY_SCORE)
        self.assertIsNot(decision.chosen, offered[0])
        self.assertIn("Pay 6 mana", offered[0].rationale)

    def test_no_anchor_for_a_non_mage_or_a_short_purse(self):
        state = running_state()
        self.assertEqual(self.anchors(self.decide(state, 2)), [])
        short = with_helper(running_state(), mana=5)
        self.assertEqual(self.anchors(self.decide(short, 2)), [])
        skilled = with_helper(running_state(), spells=[], spell_skill_levels={"x": 1})
        self.assertEqual(len(self.anchors(self.decide(skilled, 2))), 1)

    def test_no_anchor_without_runaway_rules(self):
        state = running_state()
        self.assertEqual(self.anchors(self.decide(state, 1, example_magic_rules())), [])
        self.assertNotIn(
            "anchor_target", self.decide(wizard_state(), 1).chosen.to_payload()
        )


class RunawayTurnTest(TestCase):
    """A full turn: the drain in phase 2, the choice in 3, the anchor in 5."""

    def test_a_runaway_fed_then_anchored(self):
        state = running_state()
        events: list[dict] = []
        engine.run_turn(
            state,
            PurposeRoller({"anchor": [[1, 1]]}),
            events.append,
            partial(policy.choose_option, magic=runaway_magic_rules()),
            profile=RUNAWAY_PROFILE,
        )
        drain = next(e for e in events if e["payload"].get("runaway_drain"))
        decision = next(
            e for e in events_of_type(events, "decision") if e["actor"] == "Fighter 1"
        )
        anchored = next(
            e
            for e in events
            if "anchor_target" in e["payload"] and e["event_type"] == "status"
        )
        self.assertEqual(
            (drain["phase"], decision["phase"], anchored["phase"]), (2, 3, 5)
        )
        self.assertEqual(decision["payload"]["chosen"]["anchor_target"], 1)
        caster = state.by_id(1)
        self.assertIsNone(caster.runaway)
        self.assertEqual(caster.mana, 10 - 5)
        self.assertEqual(caster.chosen_anchor, 1)
        caster.reset_for_turn()
        self.assertIsNone(caster.chosen_anchor)


class RunawaySnapshotTest(TestCase):
    def test_a_runaway_and_an_anchor_round_trip(self):
        state = running_state()
        state.by_id(1).runaway = RunawayState("fire_missile", 2, 4, caught_from=0)
        state.by_id(2).chosen_anchor = 0
        data = json.loads(json.dumps(state.to_dict()))
        restored = BattleState.from_dict(data)
        self.assertEqual(restored, state)
        self.assertEqual(
            data["combatants"][0]["runaway"],
            {
                "spell_key": "fire_missile",
                "original_cost": 2,
                "turns_running": 4,
                "caught_from": 0,
            },
        )
        self.assertEqual(data["combatants"][1]["chosen_anchor"], 0)

    def test_unset_they_are_not_written(self):
        combatant = wizard_state().to_dict()["combatants"][0]
        self.assertNotIn("runaway", combatant)
        self.assertNotIn("chosen_anchor", combatant)
        self.assertNotIn("anchored_turns_running", combatant)
