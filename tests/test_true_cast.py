"""A True cast of a known spell (tarmar-engine #77): instant, in any
element, its Channel derived by the True kind, and each point pushed past the
spell's cost drained from its caster once the casting is done.

Every number and name here is invented, as in ``test_true_magic``: the "knot"
kind gives a Channel of 3 x the "knotwork" level + 1, a casting that goes off
drains 3 Body per mana, and a casting that is countered, fails or is stopped
drains 5, 4 and 2. Push, the casting failure totals and Counterspell are the
invented ones of ``test_magic`` and ``test_counterspell``. The tests check the
engine's *structure*: what a True cast may be of, which Channel bounds it,
the order its events come in, and that each injected value is the one used.

The menu does not offer a True cast yet, so each test marks the caster's
choice itself (``chosen_true_cast``), or hands a whole turn a candidate marked
``true_cast``.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine import engine, policy
from tarmar_engine.magic import CastingEnding, MagicRules
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.spells import ELEMENTS
from tarmar_engine.state import BODY_POOL, BattleState, CombatantState

from .test_borrowing import example_borrowing_rules
from .test_cast_pieces import adjacent_pair, hand_to_hand, runner_on
from .test_channel import cast_payload
from .test_counterspell import (
    CASTER_LOW,
    DEFENDER_HIGH,
    MISSILE_FACES,
    choose_counter,
    counter_state,
    example_counterspell_rules,
)
from .test_engine import ScriptedRoller, events_of_type
from .test_magic import (
    MAGIC_PROFILE,
    control_payload,
    example_casting_failure_rules,
    example_push_rules,
    roll_purposes,
    rolls_of,
    status_payloads,
    wizard_state,
)
from .test_true_magic import (
    KNOT_KIND,
    KNOT_SKILL,
    example_true_magic_rules,
    true_magic_rules,
)

#: What a lost casting drains per mana, by how it ended. Not Tarmar's numbers.
LOST_DRAINS = {
    CastingEnding.COUNTERED: 5,
    CastingEnding.FAILED: 4,
    CastingEnding.STOPPED: 2,
}
#: What a casting that goes off drains per mana, as the example rules have it.
DRAIN_PER_MANA = 3
#: The caster's "knotwork" level, and the Channel the "knot" kind gives it.
KNOTWORK_LEVEL = 2
KNOT_CHANNEL = 3 * KNOTWORK_LEVEL + 1

#: A casting roll that succeeds, and three that fail under the example
#: casting failure rules: one that spends the mana, one that keeps it and
#: the one that also starts a Runaway.
CAST_SUCCEEDS = [3, 3, 3]
CAST_FAILS_MANA_LOST = [4, 4, 4]
CAST_FAILS_MANA_KEPT = [4, 4, 5]
CAST_FAILS_RUNAWAY = [4, 5, 5]
#: A pushed missile's Control Roll, held and missed by 3 (the band that stops
#: the spell), then its aim and its damage.
CONTROL_HELD = [3, 3]
CONTROL_STOPS = [6, 5]
AIM_HITS = [2, 2, 2]
AIM_MISSES = [6, 6, 6]
DAMAGE = [5]


def true_cast_rules(**changes) -> MagicRules:
    """True Magic with Push, the casting failure totals and Counterspell
    beside it, some of the rules changed."""
    rules = replace(
        true_magic_rules(lost_drain_per_mana=LOST_DRAINS),
        push=example_push_rules(),
        casting_failure=example_casting_failure_rules(),
        counterspell=example_counterspell_rules(),
    )
    return replace(rules, **changes)


TRUE_CAST_PROFILE = TarmarProfile(magic=true_cast_rules())


def true_mage(**caster_overrides) -> BattleState:
    """The test wizard (INT 14, WIS 12, 10 mana; Fire Missile, Shield and
    Heal known) at "knotwork" 2, and a target well out of reach."""
    fields = {"skill_levels": {KNOT_SKILL: KNOTWORK_LEVEL}}
    fields.update(caster_overrides)
    return wizard_state(**fields)


def choose(
    state: BattleState,
    spell: str,
    *,
    push: int = 0,
    true_cast: bool = True,
    borrow: bool = False,
) -> CombatantState:
    """Mark the caster's choice: a cast of the spell at figure 2."""
    caster = state.by_id(1)
    caster.chosen_spell = spell
    caster.chosen_target = 2
    caster.chosen_push_mana = push
    caster.chosen_borrow = borrow
    caster.chosen_true_cast = true_cast
    return caster


def cast(state: BattleState, faces, profile=TRUE_CAST_PROFILE) -> list[dict]:
    """Run the caster's chosen cast on scripted dice; the events."""
    turn_runner, events = runner_on(state, faces, profile)
    turn_runner.cast_spell(state.by_id(1))
    return events


def drains(events) -> list[dict]:
    """The payload of each drain an event stream took."""
    return [
        event["payload"]
        for event in events_of_type(events, "status")
        if event["payload"].get("reason") == engine.DRAIN_REASON
    ]


class TrueCastInAnyElementTest(TestCase):
    """Knowing one rung of an element's ladder, a True mage casts that rung
    of every element."""

    def test_the_known_rung_is_cast_in_every_element(self):
        for element in ELEMENTS:
            with self.subTest(element=element):
                state = true_mage()
                caster = choose(state, f"{element}_missile")
                events = cast(state, [CAST_SUCCEEDS, AIM_HITS, DAMAGE])
                payload = cast_payload(events)
                self.assertEqual(payload["spell"], f"{element}_missile")
                self.assertIs(payload["success"], True)
                self.assertIs(payload["true_cast"], True)
                self.assertEqual(caster.mana, 10 - 1)
                self.assertEqual(state.by_id(2).fatigue, 40 - 5)

    def test_an_unstudied_sibling_needs_no_level_of_its_own(self):
        # Water Missile is neither known nor recorded at any level: by its
        # own kind its Channel would be 0. The True kind's bounds it.
        state = true_mage(spell_skill_levels={"fire_missile": 3})
        caster = choose(state, "water_missile")
        self.assertNotIn("water_missile", caster.spells)
        payload = cast_payload(cast(state, [CAST_SUCCEEDS, AIM_HITS, DAMAGE]))
        self.assertEqual(
            (payload["channel"], payload["channel_kind"]), (KNOT_CHANNEL, KNOT_KIND)
        )
        # The same spell as a recipe cast is past its own Channel.
        choose(state, "water_missile", true_cast=False)
        with self.assertRaisesRegex(ValueError, "Channel for it is 0"):
            cast(state, [CAST_SUCCEEDS])

    def test_a_known_spell_with_no_element_is_cast_as_itself(self):
        state = true_mage(fatigue=30)
        caster = choose(state, "heal")
        events = cast(state, [[3, 3, 3], [4]])
        payload = cast_payload(events)
        self.assertEqual((payload["spell"], payload["true_cast"]), ("heal", True))
        self.assertEqual(payload["channel_kind"], KNOT_KIND)
        self.assertEqual(caster.fatigue, 34)

    def test_an_unpushed_true_cast_costs_only_its_mana(self):
        state = true_mage()
        caster = choose(state, "wind_missile")
        events = cast(state, [CAST_SUCCEEDS, AIM_HITS, DAMAGE])
        self.assertEqual(drains(events), [])
        self.assertEqual(caster.body, 27)
        self.assertEqual(caster.mana, 9)
        self.assertEqual(
            roll_purposes(events), ["casting", "spell aim", "spell damage"]
        )

    def test_a_recipe_cast_says_nothing_of_a_true_cast(self):
        state = true_mage(spell_skill_levels={"fire_missile": 3})
        choose(state, "fire_missile", true_cast=False)
        payload = cast_payload(cast(state, [CAST_SUCCEEDS, AIM_HITS, DAMAGE]))
        self.assertNotIn("true_cast", payload)
        self.assertEqual(payload["channel_kind"], "loom")


class TrueCastRefusedTest(TestCase):
    """The menu offers none of these, so each is a caller's error: nothing is
    rolled, spent or logged."""

    def assert_refused(self, state, pattern, profile=TRUE_CAST_PROFILE):
        caster = state.by_id(1)
        turn_runner, events = runner_on(state, [CAST_SUCCEEDS], profile)
        with self.assertRaisesRegex(ValueError, pattern):
            turn_runner.cast_spell(caster)
        self.assertEqual(events, [])
        self.assertEqual(turn_runner.roller.faces_queue, [CAST_SUCCEEDS])
        self.assertEqual(caster.mana, 10)

    def test_with_no_true_magic_rules(self):
        for profile in (TARMAR, MAGIC_PROFILE):
            with self.subTest(magic=profile.magic is not None):
                state = true_mage()
                choose(state, "fire_missile")
                self.assert_refused(state, "needs True Magic rules", profile)

    def test_with_no_level_in_the_true_skill(self):
        for skill_levels in ({}, {KNOT_SKILL: 0}, {"tapestry": 5}):
            with self.subTest(skill_levels=skill_levels):
                state = true_mage(skill_levels=skill_levels)
                choose(state, "fire_missile")
                self.assert_refused(state, "no level in Knotwork")

    def test_a_spell_neither_known_nor_a_sibling_of_one(self):
        # Fire Missile is known: another rung of its own ladder, a rung of
        # another element's that is not its own, and a spell with no element
        # are none of them the same rung of another ladder.
        for spell in ("fire_ball", "water_bolt", "wound", "blur"):
            with self.subTest(spell=spell):
                state = true_mage()
                choose(state, spell)
                self.assert_refused(state, "neither a spell they know")

    def test_a_generic_counter_and_a_working_are_never_cast(self):
        state = true_mage(spells=["fire_missile", "counter_2"])
        choose(state, "counter_2")
        self.assert_refused(state, "learned and never cast")
        state = true_mage()
        choose(state, "working:scorch:1")
        self.assert_refused(state, "crafted and never cast")

    def test_past_the_true_kinds_channel_whatever_the_spells_own_allows(self):
        # Fire Missile pushed by 3 takes 4 mana. "knotwork" 1 gives a Channel
        # of 4, and pushed by 3 is the cap; one level lower there is none.
        state = true_mage(
            skill_levels={KNOT_SKILL: 1}, spell_skill_levels={"water_missile": 9}
        )
        choose(state, "water_missile", push=3)
        events = cast(state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, DAMAGE])
        self.assertEqual(cast_payload(events)["channel"], 4)
        state = true_mage(skill_levels={KNOT_SKILL: 1}, spell_skill_levels={"wound": 9})
        state.by_id(1).spells.append("wound")
        # Wound costs 3; pushed by 2 it takes 5, past a Channel of 4, though
        # its own kind would give it 17.
        choose(state, "wound", push=2)
        self.assert_refused(state, "with 5 mana.*Channel for it is 4")

    def test_where_no_cast_may_be_made_the_refusal_is_logged_not_raised(self):
        # A sibling has no mastery entry and reads at level 1: in
        # hand-to-hand with a weapon in hand it is refused, as a cast is.
        state = hand_to_hand(
            adjacent_pair(
                spells=["fire_missile"], skill_levels={KNOT_SKILL: KNOTWORK_LEVEL}
            )
        )
        caster = choose(state, "water_missile")
        events = cast(state, [CAST_SUCCEEDS])
        self.assertEqual([event["event_type"] for event in events], ["info"])
        self.assertEqual(
            events[0]["payload"], {"hth_cast_refused": True, "spell": "water_missile"}
        )
        self.assertEqual(caster.mana, 10)


class DeeperTrueCastTest(TestCase):
    """Casting deeper is a push: Push's Control Roll and bonus, and a drain
    for each point pushed, taken last."""

    def test_a_deeper_cast_makes_the_control_roll_and_gains_the_push_bonus(self):
        state = true_mage()
        caster = choose(state, "earth_missile", push=2)
        events = cast(state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, DAMAGE])
        self.assertEqual(
            roll_purposes(events), ["casting", "control", "spell aim", "spell damage"]
        )
        payload = cast_payload(events)
        self.assertEqual((payload["push_mana"], payload["true_cast"]), (2, True))
        control = control_payload(events)
        # WIS 12 less 2 a point pushed; the push adds 4 a point to the damage.
        self.assertIs(control["held"], True)
        self.assertEqual(control["push_bonus"], 4 * 2)
        self.assertEqual(state.by_id(2).fatigue, 40 - (5 + 8))
        self.assertEqual(caster.mana, 10 - (1 + 2))

    def test_each_point_pushed_is_drained_once_the_casting_is_done(self):
        state = true_mage()
        caster = choose(state, "earth_missile", push=2)
        events = cast(state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, DAMAGE])
        self.assertEqual(
            drains(events),
            [
                {
                    BODY_POOL: 27 - DRAIN_PER_MANA * 2,
                    "cost": DRAIN_PER_MANA * 2,
                    "reason": engine.DRAIN_REASON,
                }
            ],
        )
        self.assertEqual(caster.body, 27 - 6)
        # The drain is the last thing logged, and cites the casting roll.
        self.assertEqual(events[-1]["payload"].get("reason"), engine.DRAIN_REASON)
        self.assertEqual(caster.fatal_chain, [cast_payload(events)["casting_roll"]])

    def test_the_drain_counts_the_push_not_the_spells_cost(self):
        # Lightning Bolt is known and costs 3. Wind Bolt pushed by 1 spends 4
        # mana and drains for the one point pushed.
        state = true_mage(spells=["lightning_bolt"])
        caster = choose(state, "wind_bolt", push=1)
        events = cast(state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, [2, 2, 1]])
        self.assertEqual(caster.mana, 10 - 4)
        self.assertEqual([drain["cost"] for drain in drains(events)], [DRAIN_PER_MANA])
        self.assertEqual(caster.body, 27 - DRAIN_PER_MANA)

    def test_a_deeper_cast_that_misses_its_aim_went_off_and_drains_as_one(self):
        state = true_mage()
        caster = choose(state, "fire_missile", push=1)
        events = cast(state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_MISSES])
        self.assertEqual(state.by_id(2).fatigue, 40)
        self.assertEqual([drain["cost"] for drain in drains(events)], [DRAIN_PER_MANA])
        self.assertEqual(caster.body, 27 - DRAIN_PER_MANA)

    def test_the_drain_can_fell_the_caster_and_cites_the_casting_roll(self):
        state = true_mage(body=5)
        caster = choose(state, "fire_missile", push=2)
        events = cast(state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, DAMAGE])
        self.assertEqual(caster.body, 5 - 6)
        self.assertFalse(caster.conscious)
        # The spell had already landed.
        self.assertEqual(state.by_id(2).fatigue, 40 - 13)
        self.assertEqual(caster.fatal_chain, [cast_payload(events)["casting_roll"]])

    def test_the_drain_follows_the_debt_roll(self):
        # 2 mana against a cost of 3: the cast borrows 1, and its debt roll
        # (WIS 12 less 2 a point owed) is made before the drain is taken.
        profile = TarmarProfile(
            magic=true_cast_rules(borrowing=example_borrowing_rules())
        )
        state = true_mage(mana=2)
        caster = choose(state, "fire_missile", push=2, borrow=True)
        events = cast(
            state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, DAMAGE, [4, 4]], profile
        )
        self.assertEqual((caster.mana, caster.debt), (0, 1))
        statuses = [event["payload"] for event in events_of_type(events, "status")]
        debt_roll_at = next(
            index for index, payload in enumerate(statuses) if "debt_roll" in payload
        )
        drain_at = next(
            index
            for index, payload in enumerate(statuses)
            if payload.get("reason") == engine.DRAIN_REASON
        )
        self.assertLess(debt_roll_at, drain_at)
        self.assertEqual(drain_at, len(statuses) - 1)
        # The drain counts the mana pushed, borrowed or not.
        self.assertEqual(statuses[drain_at]["cost"], DRAIN_PER_MANA * 2)


class TrueCastDrainEndingTest(TestCase):
    """What a deeper cast drains follows how it ended without going off:
    the injected amount for each ending, and nothing for a casting that never
    paid. A casting that went off is ``DeeperTrueCastTest``'s."""

    PUSH = 2

    def deeper_cast(self, faces, profile=TRUE_CAST_PROFILE):
        state = true_mage()
        caster = choose(state, "fire_missile", push=self.PUSH)
        return state, caster, cast(state, faces, profile)

    def test_a_casting_a_control_band_stopped(self):
        state, caster, events = self.deeper_cast([CAST_SUCCEEDS, CONTROL_STOPS])
        self.assertIs(control_payload(events)["takes_effect"], False)
        self.assertEqual(state.by_id(2).fatigue, 40)
        self.assertEqual(
            [drain["cost"] for drain in drains(events)],
            [LOST_DRAINS[CastingEnding.STOPPED] * self.PUSH],
        )
        self.assertEqual(caster.body, 27 - 4)
        self.assertEqual(caster.mana, 10 - 3)

    def test_a_casting_whose_roll_failed_and_lost_its_mana(self):
        _state, caster, events = self.deeper_cast([CAST_FAILS_MANA_LOST])
        self.assertIs(cast_payload(events)["success"], False)
        self.assertEqual(caster.mana, 10 - 3)
        self.assertEqual(
            [drain["cost"] for drain in drains(events)],
            [LOST_DRAINS[CastingEnding.FAILED] * self.PUSH],
        )
        self.assertEqual(caster.body, 27 - 8)
        self.assertEqual(caster.fatal_chain, [cast_payload(events)["casting_roll"]])

    def test_a_casting_whose_roll_failed_into_a_runaway(self):
        _state, caster, events = self.deeper_cast([CAST_FAILS_RUNAWAY])
        self.assertEqual(caster.mana, 10 - 3)
        self.assertEqual(
            [drain["cost"] for drain in drains(events)],
            [LOST_DRAINS[CastingEnding.FAILED] * self.PUSH],
        )

    def test_a_casting_whose_roll_failed_and_kept_its_mana_drains_nothing(self):
        _state, caster, events = self.deeper_cast([CAST_FAILS_MANA_KEPT])
        self.assertIs(cast_payload(events)["success"], False)
        self.assertEqual(caster.mana, 10)
        self.assertEqual(drains(events), [])
        self.assertEqual(caster.body, 27)

    def countered(self, *, push: int, profile=TRUE_CAST_PROFILE):
        state = counter_state()
        caster = state.by_id(1)
        caster.skill_levels = {KNOT_SKILL: KNOTWORK_LEVEL}
        caster.fatal_chain = [99]
        choose(state, "fire_missile", push=push)
        choose_counter(state, 3)
        turn_runner, events = runner_on(state, [CASTER_LOW, DEFENDER_HIGH], profile)
        turn_runner.declare_counter(state.by_id(2))
        turn_runner.cast_spell(caster)
        return caster, events

    def test_a_casting_that_was_countered(self):
        caster, events = self.countered(push=self.PUSH)
        countered = status_payloads(events, "spell_countered")[0]
        self.assertIs(countered["true_cast"], True)
        self.assertEqual(roll_purposes(events), ["counter", "counter"])
        self.assertEqual(caster.mana, 10 - 3)
        self.assertEqual(
            [drain["cost"] for drain in drains(events)],
            [LOST_DRAINS[CastingEnding.COUNTERED] * self.PUSH],
        )
        self.assertEqual(caster.body, 27 - 10)
        self.assertEqual(events[-1]["payload"].get("reason"), engine.DRAIN_REASON)
        # No casting roll was made, so the drain cites none: the caster's
        # fatal chain is as it was.
        self.assertEqual(caster.fatal_chain, [99])

    def test_an_unpushed_casting_that_was_countered_drains_nothing(self):
        caster, events = self.countered(push=0)
        self.assertEqual(caster.mana, 10 - 1)
        self.assertEqual(drains(events), [])
        self.assertEqual(caster.body, 27)

    def test_an_ending_the_rules_name_no_drain_for_takes_nothing(self):
        profile = TarmarProfile(
            magic=true_cast_rules(
                true_magic=example_true_magic_rules(lost_drain_per_mana={})
            )
        )
        for faces in ([CAST_SUCCEEDS, CONTROL_STOPS], [CAST_FAILS_MANA_LOST]):
            with self.subTest(faces=faces):
                _state, caster, events = self.deeper_cast(faces, profile)
                self.assertEqual(drains(events), [])
                self.assertEqual(caster.body, 27)
        caster, events = self.countered(push=self.PUSH, profile=profile)
        self.assertEqual(drains(events), [])
        self.assertEqual(caster.body, 27)


class TrueCastOwnRollTest(TestCase):
    """A True cast makes the spell's ordinary casting roll: the level bonus
    the example rules give a working's roll (2 a level) is not added."""

    def casting_roll(self, spell: str, faces) -> dict:
        state = true_mage(fatigue=30)
        choose(state, spell)
        return rolls_of(cast(state, faces), "casting")[0]["payload"]

    def test_the_target_is_the_spells_own_attribute(self):
        missile = self.casting_roll("fire_missile", [CAST_SUCCEEDS, AIM_HITS, DAMAGE])
        self.assertEqual((missile["versus"], missile["target_number"]), ("INT", 14))
        heal = self.casting_roll("heal", [CAST_SUCCEEDS, [4]])
        self.assertEqual((heal["versus"], heal["target_number"]), ("WIS", 12))

    def test_a_true_cast_and_a_recipe_cast_roll_alike(self):
        rolls = []
        for true_cast in (True, False):
            state = true_mage(spell_skill_levels={"fire_missile": 3})
            choose(state, "fire_missile", true_cast=true_cast)
            events = cast(state, [CAST_SUCCEEDS, AIM_HITS, DAMAGE])
            roll = events_of_type(events, "roll")[0]
            rolls.append((roll["message"], roll["payload"]))
        self.assertEqual(rolls[0], rolls[1])


class FamiliarityNotYetReadTest(TestCase):
    """A placeholder, which the familiarity slice of tarmar-engine #77
    rewrites. The opposed roll is told a cast is a True cast and does nothing
    with it yet: until familiarity is built, a True cast and a recipe cast
    meet a counter alike, whatever bonus is injected."""

    PROFILE = TarmarProfile(
        magic=true_cast_rules(
            counterspell=example_counterspell_rules(familiarity_per_level=3)
        )
    )

    def counter_result(self, faces, *, true_cast: bool) -> dict:
        state = counter_state()
        caster = state.by_id(1)
        caster.skill_levels = {KNOT_SKILL: KNOTWORK_LEVEL}
        caster.spell_skill_levels = {"fire_missile": 4}
        choose(state, "fire_missile", true_cast=true_cast)
        choose_counter(state, 3)
        turn_runner, events = runner_on(state, faces, self.PROFILE)
        turn_runner.declare_counter(state.by_id(2))
        turn_runner.cast_spell(caster)
        return status_payloads(events, "counter_result")[0]

    def test_a_true_cast_and_a_recipe_cast_meet_a_counter_alike(self):
        for name, faces in (
            ("through", [[8, 8], [1, 1], *MISSILE_FACES]),
            ("countered", [CASTER_LOW, DEFENDER_HIGH]),
        ):
            with self.subTest(outcome=name):
                true_result = self.counter_result(faces, true_cast=True)
                recipe_result = self.counter_result(faces, true_cast=False)
                self.assertEqual(true_result, recipe_result)
                self.assertNotIn("caster_familiarity", true_result)
                for entry in true_result["defender_totals"]:
                    self.assertNotIn("familiarity", entry)


class TrueCastChoiceTest(TestCase):
    """A candidate marked a True cast says so in its payload, and choosing it
    makes the turn's cast a True cast."""

    def test_only_a_true_cast_candidate_carries_the_key(self):
        plain = policy.Candidate("h", "CAST SPELL", 1.0, "", spell_key="fire_missile")
        self.assertFalse(plain.true_cast)
        self.assertNotIn("true_cast", plain.to_payload())
        marked = replace(plain, true_cast=True)
        self.assertIs(marked.to_payload()["true_cast"], True)
        without_the_key = {
            key: value
            for key, value in marked.to_payload().items()
            if key != "true_cast"
        }
        self.assertEqual(without_the_key, plain.to_payload())

    def run_turns(self, state: BattleState, choosers) -> list[list[dict]]:
        """Run a turn for each chooser on midline dice; each turn's events."""
        roller = ScriptedRoller()
        turns: list[list[dict]] = []
        for choose_option in choosers:
            events: list[dict] = []
            engine.run_turn(
                state, roller, events.append, choose_option, profile=TRUE_CAST_PROFILE
            )
            turns.append(events)
        return turns

    @staticmethod
    def menu_choice(state: BattleState, actor: CombatantState) -> policy.Decision:
        return policy.choose_option(state, actor, TRUE_CAST_PROFILE.magic)

    def true_casting_water_missile(
        self, state: BattleState, actor: CombatantState
    ) -> policy.Decision:
        """The menu's choice, but for the caster: its Fire Missile cast,
        turned into a True cast of Water Missile."""
        decision = self.menu_choice(state, actor)
        if actor.combatant_id == 1:
            fire_missile = next(
                candidate
                for candidate in decision.candidates
                if candidate.spell_key == "fire_missile" and not candidate.push_mana
            )
            decision.chosen = replace(
                fire_missile, spell_key="water_missile", true_cast=True
            )
        return decision

    def decisions_of(self, events, name: str) -> list[dict]:
        return [
            event["payload"]
            for event in events_of_type(events, "decision")
            if event["actor"] == name
        ]

    def test_a_turn_that_chooses_a_true_cast_makes_one(self):
        state = true_mage(spell_skill_levels={"fire_missile": 3})
        caster = state.by_id(1)
        first_turn, second_turn = self.run_turns(
            state, [self.true_casting_water_missile, self.menu_choice]
        )
        (decision,) = self.decisions_of(first_turn, caster.name)
        self.assertIs(decision["chosen"]["true_cast"], True)
        self.assertEqual(decision["chosen"]["spell_key"], "water_missile")
        for offered in decision["candidates"]:
            self.assertNotIn("true_cast", offered)
        payload = cast_payload(first_turn)
        self.assertEqual(
            (payload["spell"], payload["true_cast"]), ("water_missile", True)
        )
        self.assertEqual(
            (payload["channel"], payload["channel_kind"]), (KNOT_CHANNEL, KNOT_KIND)
        )
        # The choice lasts its turn: the next, taken off the menu, is no
        # True cast, and neither its decision nor its cast names one.
        (later_decision,) = self.decisions_of(second_turn, caster.name)
        self.assertNotIn("true_cast", later_decision["chosen"])
        self.assertFalse(caster.chosen_true_cast)
        for event in events_of_type(second_turn, "action"):
            self.assertNotIn("true_cast", event["payload"])
