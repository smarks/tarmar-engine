"""A working goes off (tarmar-engine #77): the end of the Actions phase
completes each craft whose last turn is taken, with the working's own roll
and all that follows a casting roll.

Every number and name here is invented, as in ``test_craft``, whose crafter
and workings these tests use. The working's roll is the casting dice (3d6)
under WIS (12 for the crafter) plus 2 per "knotwork" level, 16 at level 2.
The example casting failure rules fail a natural 12 and up, spend the mana
on a 12 or a 14, and start a Runaway on the 14; a natural 7 is the "keen
cast" tier, 2 more on a rolled effect and a point of mana back. A working
that goes off drains 3 Body per mana; one that failed on a total that loses
the mana drains 1, one a Control band stopped 5, one countered 6. Most tests
complete the steady Scorch (tier 2 pushed by 2: 4 mana, "2d4+2" to Body past
armour, and 8 more for the push) or an unpushed tier-1 working, whose one
point is gathered on its second turn. The tests check the engine's
*structure*: when a working goes off, the order its events come in, who it
lands on, and that each injected value is the one used.
"""

from dataclasses import replace
from typing import Any
from unittest import TestCase

from tarmar_engine import actions, engine, policy
from tarmar_engine.magic import (
    CHANNEL,
    CastingEnding,
    MagicRules,
    SpellEffectKind,
    WorkingEffect,
)
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.spells import get_spell
from tarmar_engine.state import (
    BODY_POOL,
    FATIGUE_POOL,
    BattleState,
    CraftState,
    RunawayState,
)

from .test_borrowing import example_borrowing_rules
from .test_cast_pieces import runner_on
from .test_counterspell import (
    CASTER_LOW,
    DEFENDER_HIGH,
    FAMILIARITY_RULES,
    choose_counter,
    example_counterspell_rules,
    opposed_result,
)
from .test_craft import (
    DEBT_HOLDS,
    DEBT_SURGE,
    KNOT_CHANNEL,
    LOST_DRAINS,
    STEADY,
    TURNS_PER_TIER,
    borrowed_craft,
    choose_craft,
    craft,
    craft_actions,
    craft_candidate,
    craft_rules,
    craft_turns,
    crafter,
    loss_events,
    losses,
    steady_craft,
    working_key,
)
from .test_craft_broken import (
    ATTACK,
    INITIATIVE,
    KEEPS,
    SNAGS,
    STRIKES,
    beside,
    choosing_for,
    interruptions,
)
from .test_engine import events_of_type, roll_purposes, rolls_of
from .test_magic import (
    example_casting_failure_rules,
    example_magic_rules,
    status_payloads,
)
from .test_runaway import example_runaway_rules
from .test_state import make_combatant
from .test_true_cast import (
    AIM_HITS,
    AIM_MISSES,
    CAST_FAILS_MANA_KEPT,
    CAST_FAILS_MANA_LOST,
    CAST_FAILS_RUNAWAY,
    CAST_SUCCEEDS,
    CONTROL_HELD,
    CONTROL_STOPS,
    drains,
)
from .test_true_magic import EFFECTS, KNOT_SKILL, example_true_magic_rules

#: What a working drains per mana when it is lost at the last, by how it
#: ended. Not Tarmar's numbers.
COMPLETION_DRAINS = {
    **LOST_DRAINS,
    CastingEnding.FAILED: 1,
    CastingEnding.STOPPED: 5,
    CastingEnding.COUNTERED: 6,
}
#: What one that goes off drains per mana, as the example rules have it.
DRAIN_PER_MANA = 3
#: The steady Scorch's whole mana, and its key.
STEADY_MANA = STEADY["tier"] + STEADY["push"]
STEADY_KEY = working_key("scorch", 2)
#: The working's roll for the crafter: WIS 12 and 2 per "knotwork" level.
ROLL_TARGET = 12 + 2 * 2
#: A casting roll on the "keen cast" total, and one only the level bonus
#: lets succeed (15, over the WIS of 12).
CAST_KEEN = [2, 2, 3]
CAST_NEEDS_THE_BONUS = [5, 5, 5]
#: The steady Scorch's damage dice (2d4+2): 7, and 15 with the push's 8.
SCORCH_DICE = [3, 2]
#: A Control Roll (2d6 under 8 for the steady Scorch) that misses by 1, the
#: band that lets the spell through without the push's bonus; and the
#: example rules' automatic total, which runs away.
CONTROL_WOBBLES = [5, 4]
CONTROL_RUNS_AWAY = [1, 1]
#: A debt roll (2d8) that misses a target of 12 - 2 x 3 - 1 by 4, the
#: "Backlash" band: 2 Fatigue for each point owed.
DEBT_BACKLASH_OF_THREE = [5, 4]
#: A survival save (3d6) no figure makes.
SAVE_FAILS = [6, 6, 6]
#: A counter of 3 mana by figure 2, as a turn's choice carries it.
COUNTER = policy.Candidate(
    actions.COUNTER_OPTION,
    actions.option_name(actions.COUNTER_OPTION),
    0.0,
    "",
    target_id=1,
    counter_mana=3,
)


def completion_rules(**true_magic_changes) -> MagicRules:
    """The crafting rules with the casting tiers, the casting failure
    totals, Runaway and Counterspell beside them, some of the True Magic
    fields changed."""
    fields = {"lost_drain_per_mana": COMPLETION_DRAINS}
    fields.update(true_magic_changes)
    return replace(
        craft_rules(**fields),
        casting_success_tiers=example_magic_rules().casting_success_tiers,
        casting_failure=example_casting_failure_rules(),
        runaway=example_runaway_rules(),
        counterspell=example_counterspell_rules(),
    )


COMPLETION_PROFILE = TarmarProfile(magic=completion_rules())
#: The same rules where the caster's attribute alone judges the roll.
NO_FLOOR_PROFILE = TarmarProfile(
    magic=replace(completion_rules(), casting_failure=None)
)


def whole_craft(
    effect: str = "scorch",
    tier: int = 1,
    *,
    target: int | None = 2,
    state: BattleState | None = None,
    **caster_overrides,
) -> BattleState:
    """A caster whose craft of an unpushed working has had every turn it
    takes: the crafter, with the overrides given, or figure 1 of the state
    given."""
    if state is None:
        state = crafter(**caster_overrides)
    elif caster_overrides:
        raise ValueError("a state given is crafted in as it stands")
    choose_craft(state, effect, tier, target=target)
    return craft_turns(state, TURNS_PER_TIER * tier)


def whole_steady(**caster_overrides) -> BattleState:
    """The crafter with the steady Scorch whole: 4 mana gathered."""
    return steady_craft(TURNS_PER_TIER * STEADY["tier"], **caster_overrides)


def whole_borrowed(**caster_overrides) -> BattleState:
    """The crafter, 1 mana to its name, with the steady Scorch whole: one
    share paid and three borrowed."""
    state = craft_turns(borrowed_craft(**caster_overrides), 2)
    craft_state = state.by_id(1).crafting
    assert craft_state is not None and craft_state.borrowed == 3
    return state


def with_teammate(state: BattleState, **teammate_overrides) -> BattleState:
    """The state with figure 3 beside the caster and on the caster's team,
    and figure 2 on another."""
    state.by_id(1).team = "red"
    state.by_id(2).team = "blue"
    fields = {"q": -3, "r": 2, "team": "red"}
    fields.update(teammate_overrides)
    state.combatants.append(make_combatant(3, **fields))
    return state


def complete(
    state: BattleState, faces=(), profile=COMPLETION_PROFILE
) -> tuple[list[dict], engine.TurnRunner]:
    """End the Actions phase's workings on a runner of its own; the events
    and the runner."""
    turn_runner, events = runner_on(state, faces, profile)
    turn_runner._complete_workings()
    return events, turn_runner


def completions(events) -> list[dict]:
    """Each working's own action event in an event stream."""
    return [
        event
        for event in events_of_type(events, "action")
        if event["payload"].get("working")
    ]


def steps(events) -> list[str]:
    """What each event of a working that goes off is, in the order logged:
    a roll by its purpose, a status event by the first of these names its
    payload carries, and any other by its type."""
    names = (
        "success_tier",
        "control_band",
        "effect_bonus",
        "healed",
        "working_effect",
        "runaway_begins",
        "debt_roll",
        "debt_failure",
        "counter_result",
        "craft_lost",
        "unconscious",
    )
    order = []
    for event in events:
        payload = event["payload"]
        if event["event_type"] == "roll":
            order.append(payload["purpose"])
        elif event["event_type"] != "status":
            order.append(event["event_type"])
        elif payload.get("reason") == engine.DRAIN_REASON:
            order.append("drain")
        elif "reason" in payload:
            order.append("pool cost")
        else:
            order.extend([name for name in names if name in payload][:1])
    return order


class GoesOffTest(TestCase):
    """The unpushed tier-1 Scorch, its one point gathered, goes off."""

    def test_the_roll_the_action_the_effect_and_the_drain_in_order(self):
        state = whole_craft()
        caster, target = state.by_id(1), state.by_id(2)
        self.assertEqual(caster.mana, 9)
        events, turn_runner = complete(state, [CAST_SUCCEEDS, AIM_HITS, [3]])
        self.assertEqual(
            steps(events),
            ["casting", "action", "spell aim", "spell damage", "damage", "drain"],
        )
        roll = events[0]
        self.assertEqual(roll["actor"], caster.name)
        self.assertEqual(
            roll["payload"],
            {
                "purpose": "casting",
                "specification": "3d6",
                "faces": [3, 3, 3],
                "modifier": 0,
                "total": 9,
                "target_number": ROLL_TARGET,
                "versus": "WIS+Knotwork",
                "roll_under": True,
                "outcome": "success",
            },
        )
        (action,) = completions(events)
        self.assertEqual(
            action["payload"],
            {
                "spell": working_key("scorch", 1),
                "success": True,
                "mana_left": 9,
                "casting_roll": roll["sequence"],
                "push_mana": 0,
                "channel": KNOT_CHANNEL,
                "channel_kind": "knot",
                "borrowed": 0,
                "debt": 0,
                "true_cast": True,
                "working": True,
                "tier": 1,
            },
        )
        self.assertEqual(
            action["message"],
            "Fighter 1 completes Scorch (tier 1) (3d6 ≤ WIS+Knotwork 16): "
            "success (1 mana gathered, 9 left; Channel 7)",
        )
        # The shares paid for it: nothing more leaves the pool.
        self.assertEqual(caster.mana, 9)
        # 1d4+1 to Body, and the drain of its one point.
        self.assertEqual(target.body, 27 - 4)
        self.assertEqual(
            drains(events),
            [
                {
                    BODY_POOL: 27 - 3,
                    "cost": DRAIN_PER_MANA,
                    "reason": engine.DRAIN_REASON,
                }
            ],
        )
        # The craft is done with, and was not lost.
        self.assertIsNone(caster.crafting)
        self.assertEqual(losses(events), [])
        self.assertEqual(turn_runner._crafts_ended, set())

    def test_the_roll_adds_the_injected_bonus_for_each_level(self):
        for level, bonus_per_level, target in ((2, 2, 16), (1, 2, 14), (2, 3, 18)):
            with self.subTest(level=level, bonus_per_level=bonus_per_level):
                state = whole_craft(skill_levels={KNOT_SKILL: level})
                profile = TarmarProfile(
                    magic=completion_rules(roll_bonus_per_level=bonus_per_level)
                )
                events, _turn_runner = complete(state, [CAST_SUCCEEDS], profile)
                self.assertEqual(events[0]["payload"]["target_number"], target)

    def test_a_total_over_the_attribute_succeeds_by_the_bonus_alone(self):
        # 15 is over the WIS of 12: two levels' bonus admits it, one does
        # not.
        for level, succeeds in ((2, True), (1, False)):
            with self.subTest(level=level):
                state = whole_craft(skill_levels={KNOT_SKILL: level})
                events, _turn_runner = complete(
                    state, [CAST_NEEDS_THE_BONUS, AIM_HITS], NO_FLOOR_PROFILE
                )
                (action,) = completions(events)
                self.assertIs(action["payload"]["success"], succeeds)

    def test_the_roll_is_under_the_injected_attribute_and_names_the_skill(self):
        by_wits = TarmarProfile(
            magic=completion_rules(roll_attribute="INT", skill_label="Weave")
        )
        events, _turn_runner = complete(whole_craft(), [CAST_SUCCEEDS], by_wits)
        self.assertEqual(
            (events[0]["payload"]["target_number"], events[0]["payload"]["versus"]),
            (14 + 2 * 2, "INT+Weave"),
        )
        self.assertIn("(3d6 ≤ INT+Weave 18)", completions(events)[0]["message"])

    def test_the_casters_injury_band_comes_off_the_roll(self):
        state = whole_craft()
        caster = state.by_id(1)
        caster.fatigue = 20
        band = COMPLETION_PROFILE.reactions.injury_penalty(caster)
        self.assertGreater(band, 0)
        events, _turn_runner = complete(state, [CAST_SUCCEEDS])
        self.assertEqual(events[0]["payload"]["target_number"], ROLL_TARGET - band)

    def test_it_lands_on_the_figure_the_craft_named_not_the_turns_choice(self):
        state = crafter()
        state.combatants.append(make_combatant(3, q=3, r=2, facing=3))
        whole_craft(target=3, state=state)
        caster = state.by_id(1)
        # The turn's choice has since moved on, and names a push as well.
        caster.chosen_target = 2
        caster.chosen_push_mana = 3
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, AIM_HITS, [3]])
        self.assertEqual(state.by_id(3).body, 27 - 4)
        self.assertEqual(state.by_id(2).body, 27)
        self.assertNotIn("control", roll_purposes(events))
        self.assertEqual(completions(events)[0]["payload"]["push_mana"], 0)

    def test_a_working_past_a_lifted_bound_says_so_as_it_goes_off(self):
        # A tier-2 working pushed by 2 has 4 mana: "knotwork" 1 gives a
        # Channel of 4 by the True kind and a tier cap of 2, so it is within
        # both; at 5 mana it is past Channel, which a Game Master lifted.
        state = crafter(lifted_magic_bounds=[CHANNEL], skill_levels={KNOT_SKILL: 1})
        choose_craft(state, "scorch", 2, push=3)
        craft_turns(state, TURNS_PER_TIER * 2)
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, CONTROL_STOPS])
        payload = completions(events)[0]["payload"]
        self.assertEqual((payload["channel"], payload["channel_kind"]), (4, "knot"))
        self.assertEqual(payload["lifted_bounds"], [CHANNEL])

    def test_a_caster_with_no_craft_is_refused(self):
        state = whole_craft()
        turn_runner, events = runner_on(state, [CAST_SUCCEEDS], COMPLETION_PROFILE)
        with self.assertRaisesRegex(ValueError, "no craft to complete"):
            turn_runner._complete_working(state.by_id(2))
        self.assertEqual(events, [])


class WhichWorkingsTest(TestCase):
    """The end of the Actions phase completes the crafts that are whole, in
    the phase's own order."""

    def crafting(self, state: BattleState, combatant_id: int, target: int) -> None:
        """Stand a whole tier-1 Scorch on a figure, aimed at another."""
        figure = state.by_id(combatant_id)
        figure.skill_levels = {KNOT_SKILL: 2}
        figure.crafting = CraftState(
            working_key("scorch", 1), 0, False, TURNS_PER_TIER, 0, target
        )

    def pair(self, **dexterities: int) -> BattleState:
        """Figures 1 and 2 each with a whole Scorch aimed at the other."""
        state = crafter()
        self.crafting(state, 1, 2)
        self.crafting(state, 2, 1)
        for name, dexterity in dexterities.items():
            state.by_id({"first": 1, "second": 2}[name]).dexterity = dexterity
        return state

    def test_a_craft_with_a_turn_still_to_take_does_not_go_off(self):
        for turns in (1, 3):
            with self.subTest(turns=turns):
                state = steady_craft(turns)
                events, _turn_runner = complete(state, [CAST_SUCCEEDS])
                self.assertEqual(events, [])
                craft_state = state.by_id(1).crafting
                assert craft_state is not None
                self.assertEqual(craft_state.turns_done, turns)

    def test_workings_go_off_in_dexterity_order_and_then_by_id(self):
        for dexterities, order in (
            ({"first": 9, "second": 13}, ["Fighter 2", "Fighter 1"]),
            ({"first": 13, "second": 9}, ["Fighter 1", "Fighter 2"]),
            ({"first": 11, "second": 11}, ["Fighter 1", "Fighter 2"]),
        ):
            with self.subTest(dexterities=dexterities):
                events, _turn_runner = complete(
                    self.pair(**dexterities),
                    [CAST_SUCCEEDS, AIM_MISSES, CAST_SUCCEEDS, AIM_MISSES],
                )
                self.assertEqual(
                    [event["actor"] for event in completions(events)], order
                )

    def test_a_working_an_earlier_one_breaks_does_not_go_off(self):
        # Figure 1's Scorch lands on figure 2, whose roll to keep its own
        # working then fails.
        state = self.pair(first=13, second=9)
        events, turn_runner = complete(state, [CAST_SUCCEEDS, AIM_HITS, [3], SNAGS])
        self.assertEqual(
            [event["actor"] for event in completions(events)], ["Fighter 1"]
        )
        self.assertEqual([lost["ending"] for lost in losses(events)], ["interrupted"])
        self.assertEqual(roll_purposes(events).count("casting"), 1)
        self.assertIn(2, turn_runner._crafts_ended)
        # Had the roll held, the second working would have gone off after.
        state = self.pair(first=13, second=9)
        events, _turn_runner = complete(
            state, [CAST_SUCCEEDS, AIM_HITS, [3], KEEPS, CAST_SUCCEEDS, AIM_MISSES]
        )
        self.assertEqual(
            [event["actor"] for event in completions(events)],
            ["Fighter 1", "Fighter 2"],
        )

    def test_a_working_whose_caster_an_earlier_one_fells_does_not_go_off(self):
        state = self.pair(first=13, second=9)
        state.by_id(2).body = 4
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, AIM_HITS, [3]])
        self.assertFalse(state.by_id(2).conscious)
        self.assertEqual(
            [event["actor"] for event in completions(events)], ["Fighter 1"]
        )
        self.assertEqual([lost["ending"] for lost in losses(events)], ["unconscious"])

    def test_a_craft_standing_with_no_true_magic_rules_is_refused(self):
        state = whole_craft()
        with self.assertRaises(ValueError):
            complete(state, [CAST_SUCCEEDS], TARMAR)
        # With no craft standing the default profile does nothing at all.
        state.by_id(1).crafting = None
        events, _turn_runner = complete(state, [CAST_SUCCEEDS], TARMAR)
        self.assertEqual(events, [])


class PaymentTest(TestCase):
    """The shares are spent when the working pays, and handed back when it
    does not."""

    def test_a_failure_that_keeps_the_mana_hands_every_share_back(self):
        state = whole_steady()
        caster = state.by_id(1)
        self.assertEqual(caster.mana, 10 - STEADY_MANA)
        events, turn_runner = complete(state, [CAST_FAILS_MANA_KEPT])
        self.assertEqual(steps(events), ["casting", "action"])
        (action,) = completions(events)
        payload = action["payload"]
        self.assertIs(payload["success"], False)
        self.assertEqual(
            (payload["mana_returned"], payload["debt_returned"]), (STEADY_MANA, 0)
        )
        self.assertEqual((payload["mana_left"], payload["borrowed"]), (10, 0))
        self.assertEqual(
            action["message"],
            "Fighter 1 completes Scorch (tier 2) (3d6 ≤ WIS+Knotwork 16): "
            "failure (4 mana back, 10 left; Channel 7)",
        )
        self.assertEqual(caster.mana, 10)
        # It owes no drain, and the craft is done with all the same.
        self.assertEqual(caster.body, 27)
        self.assertIsNone(caster.crafting)
        self.assertEqual(turn_runner._crafts_ended, set())

    def test_the_hand_back_takes_what_the_craft_borrowed_off_the_debt(self):
        # The caster owed 1 before the craft borrowed its 3.
        state = whole_borrowed(debt=1)
        caster = state.by_id(1)
        self.assertEqual((caster.mana, caster.debt), (0, 4))
        events, _turn_runner = complete(state, [CAST_FAILS_MANA_KEPT])
        # No debt roll: the borrowed shares went back.
        self.assertEqual(steps(events), ["casting", "action"])
        (action,) = completions(events)
        payload = action["payload"]
        self.assertEqual((payload["mana_returned"], payload["debt_returned"]), (1, 3))
        self.assertEqual((payload["borrowed"], payload["debt"]), (0, 1))
        self.assertIn(
            "failure (1 mana back, 1 left, 3 off the debt, debt 1; Channel 7)",
            action["message"],
        )
        self.assertEqual((caster.mana, caster.debt), (1, 1))

    def test_the_debt_handed_back_is_named_only_with_borrowing_rules(self):
        no_borrowing = TarmarProfile(magic=replace(completion_rules(), borrowing=None))
        events, _turn_runner = complete(
            whole_steady(), [CAST_FAILS_MANA_KEPT], no_borrowing
        )
        payload = completions(events)[0]["payload"]
        self.assertEqual(payload["mana_returned"], STEADY_MANA)
        self.assertNotIn("debt_returned", payload)

    def test_a_working_that_pays_names_no_hand_back(self):
        for faces in ([CAST_SUCCEEDS, CONTROL_STOPS], [CAST_FAILS_MANA_LOST]):
            with self.subTest(faces=faces):
                events, _turn_runner = complete(whole_steady(), faces)
                payload = completions(events)[0]["payload"]
                self.assertNotIn("mana_returned", payload)
                self.assertNotIn("debt_returned", payload)

    def test_a_failure_that_loses_the_mana_keeps_the_shares_spent(self):
        state = whole_steady()
        caster = state.by_id(1)
        events, _turn_runner = complete(state, [CAST_FAILS_MANA_LOST])
        self.assertEqual(steps(events), ["casting", "action", "drain"])
        (action,) = completions(events)
        self.assertIs(action["payload"]["success"], False)
        self.assertIn("failure (4 mana gathered, 6 left", action["message"])
        self.assertEqual(caster.mana, 10 - STEADY_MANA)
        # Drained as a working that failed, for its whole mana.
        self.assertEqual(
            [drain["cost"] for drain in drains(events)],
            [STEADY_MANA * COMPLETION_DRAINS[CastingEnding.FAILED]],
        )
        self.assertIsNone(caster.runaway)

    def test_the_runaway_total_starts_a_runaway_of_the_whole_mana(self):
        state = whole_steady()
        caster = state.by_id(1)
        events, _turn_runner = complete(state, [CAST_FAILS_RUNAWAY])
        self.assertEqual(
            steps(events), ["casting", "action", "runaway_begins", "drain"]
        )
        self.assertEqual(
            caster.runaway,
            RunawayState(spell_key=STEADY_KEY, original_cost=STEADY_MANA),
        )
        self.assertEqual(caster.mana, 10 - STEADY_MANA)
        self.assertEqual(
            [drain["cost"] for drain in drains(events)],
            [STEADY_MANA * COMPLETION_DRAINS[CastingEnding.FAILED]],
        )

    def test_a_success_tier_pays_its_refund_and_its_bonus(self):
        state = whole_steady()
        caster, target = state.by_id(1), state.by_id(2)
        events, _turn_runner = complete(
            state, [CAST_KEEN, CONTROL_HELD, AIM_HITS, SCORCH_DICE]
        )
        self.assertEqual(
            steps(events),
            [
                "casting",
                "action",
                "success_tier",
                "control",
                "control_band",
                "effect_bonus",
                "spell aim",
                "spell damage",
                "damage",
                "drain",
            ],
        )
        (tier,) = status_payloads(events, "success_tier")
        self.assertEqual(
            (tier["success_tier"], tier["mana_refund"], tier["effect_bonus"]),
            ("keen", 1, 2),
        )
        self.assertEqual(caster.mana, 10 - STEADY_MANA + 1)
        # 7 rolled, 2 for the tier and 8 for the push.
        self.assertEqual(target.body, 27 - 17)
        # The drain counts the whole mana, whatever the tier handed back.
        self.assertEqual(
            [drain["cost"] for drain in drains(events)], [STEADY_MANA * DRAIN_PER_MANA]
        )


class DamageTest(TestCase):
    def test_a_damaging_working_rolls_its_dice_at_its_tier(self):
        # An unpushed tier-2 Scorch: twice the 1d4+1 a tier.
        state = whole_craft("scorch", 2)
        target = state.by_id(2)
        target.stops = 3
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, AIM_HITS, SCORCH_DICE])
        (damage_roll,) = rolls_of(events, "spell damage")
        self.assertEqual(
            (damage_roll["payload"]["specification"], damage_roll["payload"]["total"]),
            ("2d4+2", 7),
        )
        # To Body alone, past the armour, as the effect says.
        self.assertEqual((target.body, target.fatigue), (27 - 7, 40))

    def test_a_working_that_misses_its_aim_still_owes_its_drain(self):
        state = whole_craft()
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, AIM_MISSES])
        self.assertEqual(
            steps(events), ["casting", "action", "spell aim", "info", "drain"]
        )
        self.assertEqual(state.by_id(2).body, 27)
        self.assertEqual([drain["cost"] for drain in drains(events)], [DRAIN_PER_MANA])

    def test_a_working_with_no_one_to_land_on_still_owes_its_drain(self):
        state = whole_craft()
        state.by_id(2).alive = False
        events, _turn_runner = complete(state, [CAST_SUCCEEDS])
        self.assertEqual(steps(events), ["casting", "action", "drain"])
        self.assertEqual([drain["cost"] for drain in drains(events)], [DRAIN_PER_MANA])


#: A healing effect on Body, beside the example rules' own on Fatigue.
KNIT = WorkingEffect(
    key="knit",
    label="Knit",
    kind=SpellEffectKind.HEALING,
    dice_per_tier="1d6",
    pool=BODY_POOL,
)


def healing(target: int | None, **teammate_overrides) -> BattleState:
    """The crafter down 10 Fatigue with the tier-1 Salve whole, a teammate
    down 30 and an enemy down 20."""
    teammate = {"fatigue": 10}
    teammate.update(teammate_overrides)
    state = with_teammate(crafter(fatigue=30), **teammate)
    state.by_id(2).fatigue = 20
    return whole_craft("salve", target=target, state=state)


class HealingTest(TestCase):
    """The tier-1 Salve restores 1d8 Fatigue to the figure it heals."""

    def test_a_teammate_it_names_is_healed_and_the_event_says_who(self):
        state = healing(3)
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, [6]])
        self.assertEqual(
            steps(events), ["casting", "action", "healing", "healed", "drain"]
        )
        (healed,) = events_of_type(events, "status")[:1]
        self.assertEqual(
            healed["payload"], {"healed": 6, FATIGUE_POOL: 16, "target": 3}
        )
        self.assertEqual(healed["actor"], "Fighter 1")
        self.assertEqual(
            healed["message"], "Fighter 1 heals 6 fatigue for Fighter 3 (16/40)"
        )
        self.assertEqual(
            [state.by_id(figure).fatigue for figure in (1, 2, 3)], [30, 20, 16]
        )

    def test_any_other_figure_named_leaves_the_caster_healed(self):
        for case, target, teammate in (
            ("the caster", 1, {}),
            ("an enemy", 2, {}),
            ("no one", None, {}),
            ("a dead teammate", 3, {"alive": False}),
        ):
            with self.subTest(case=case):
                state = healing(target, **teammate)
                events, _turn_runner = complete(state, [CAST_SUCCEEDS, [6]])
                (healed,) = status_payloads(events, "healed")
                self.assertEqual(healed, {"healed": 6, FATIGUE_POOL: 36})
                self.assertEqual(
                    [state.by_id(figure).fatigue for figure in (1, 2, 3)],
                    [36, 20, 10],
                )
                self.assertIn(
                    "Fighter 1 heals 6 fatigue (36/40)",
                    [event["message"] for event in events],
                )

    def test_a_teammate_who_is_down_is_healed_and_not_woken(self):
        state = healing(3, conscious=False, fatigue=-4)
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, [6]])
        teammate = state.by_id(3)
        self.assertEqual(teammate.fatigue, 2)
        self.assertFalse(teammate.conscious)
        self.assertEqual(status_payloads(events, "healed")[0]["target"], 3)

    def test_the_healing_stops_at_the_healed_figures_own_maximum(self):
        # The teammate's Fatigue is 28 of 30; the caster's maximum is 40.
        state = healing(3, max_fatigue=30, fatigue=28)
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, [6]])
        (healed,) = [event for event in events if "healed" in event["payload"]]
        self.assertEqual(
            healed["payload"], {"healed": 2, FATIGUE_POOL: 30, "target": 3}
        )
        self.assertEqual(
            healed["message"], "Fighter 1 heals 2 fatigue for Fighter 3 (30/30)"
        )
        self.assertEqual(state.by_id(3).fatigue, 30)

    def test_it_restores_the_pool_its_effect_names(self):
        # The teammate's Body is 17 of 20; the caster's maximum is 27.
        profile = TarmarProfile(magic=completion_rules(effects=(*EFFECTS, KNIT)))
        state = with_teammate(crafter(), body=17, max_body=20)
        true_magic = profile.magic.true_magic if profile.magic else None
        assert true_magic is not None
        state.by_id(1).crafting = CraftState(
            true_magic.working_key("knit", 1), 0, False, TURNS_PER_TIER, 0, 3
        )
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, [5]], profile)
        (healed,) = status_payloads(events, "healed")
        self.assertEqual(healed, {"healed": 3, BODY_POOL: 20, "target": 3})
        teammate = state.by_id(3)
        self.assertEqual((teammate.body, teammate.fatigue), (20, 40))
        self.assertIn(
            "Fighter 1 heals 3 body for Fighter 3 (20/20)",
            [event["message"] for event in events],
        )

    def test_a_catalog_healing_spell_heals_its_caster_whoever_it_names(self):
        state = with_teammate(crafter(fatigue=30), fatigue=10)
        caster = state.by_id(1)
        caster.chosen_spell = "heal"
        caster.chosen_target = 3
        turn_runner, events = runner_on(state, [CAST_SUCCEEDS, [4]], COMPLETION_PROFILE)
        turn_runner.cast_spell(caster)
        self.assertEqual(
            status_payloads(events, "healed"), [{"healed": 4, FATIGUE_POOL: 34}]
        )
        self.assertEqual((caster.fatigue, state.by_id(3).fatigue), (34, 10))


def omen(target: int | None = 2, tier: int = 1) -> BattleState:
    """The crafter with an Omen whole, a Game Master having named it for
    them."""
    granted = working_key("omen", tier)
    return whole_craft("omen", tier, target=target, granted_workings=[granted])


class NarratedTest(TestCase):
    """A working the engine does not apply: the tier-1 Omen rolls 3d4-1 and
    the Hush rolls nothing."""

    def test_it_reports_its_magnitude_and_applies_nothing(self):
        state = omen()
        target = state.by_id(2)
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, [4, 3, 2]])
        self.assertEqual(
            steps(events),
            ["casting", "action", "working magnitude", "working_effect", "drain"],
        )
        (magnitude_roll,) = rolls_of(events, "working magnitude")
        self.assertEqual(
            (
                magnitude_roll["payload"]["specification"],
                magnitude_roll["payload"]["total"],
            ),
            ("3d4-1", 8),
        )
        (narrated,) = [
            event for event in events if "working_effect" in event["payload"]
        ]
        self.assertEqual(
            narrated["payload"],
            {
                "working_effect": "omen",
                "tier": 1,
                "narrated": True,
                "target": 2,
                "magnitude": 8,
            },
        )
        self.assertEqual(
            narrated["message"],
            "Fighter 1's Omen (tier 1) goes off at Fighter 2, magnitude 8: "
            "its effect is the Game Master's to describe",
        )
        # No aim roll, and the figure named is untouched.
        self.assertEqual((target.fatigue, target.body), (40, 27))
        self.assertEqual([drain["cost"] for drain in drains(events)], [DRAIN_PER_MANA])

    def test_its_dice_are_the_effects_at_its_tier(self):
        events, _turn_runner = complete(omen(tier=2), [CAST_SUCCEEDS])
        self.assertIn(
            "6d4-2",
            [
                event["payload"]["specification"]
                for event in events_of_type(events, "roll")
            ],
        )
        self.assertEqual(status_payloads(events, "working_effect")[0]["tier"], 2)

    def test_an_effect_with_no_dice_rolls_nothing_and_reports_no_magnitude(self):
        state = whole_craft("hush")
        events, _turn_runner = complete(state, [CAST_SUCCEEDS])
        self.assertEqual(
            steps(events), ["casting", "action", "working_effect", "drain"]
        )
        (narrated,) = [
            event for event in events if "working_effect" in event["payload"]
        ]
        self.assertEqual(
            narrated["payload"],
            {
                "working_effect": "hush",
                "tier": 1,
                "narrated": True,
                "target": 2,
                "magnitude": None,
            },
        )
        self.assertEqual(
            narrated["message"],
            "Fighter 1's Hush (tier 1) goes off at Fighter 2: its effect is "
            "the Game Master's to describe",
        )

    def test_the_figure_named_is_passed_through_as_given(self):
        for case, target in (("none", None), ("the caster", 1), ("a dead figure", 2)):
            with self.subTest(case=case):
                state = omen(target)
                state.by_id(2).alive = case != "a dead figure"
                events, _turn_runner = complete(state, [CAST_SUCCEEDS])
                (narrated,) = status_payloads(events, "working_effect")
                self.assertEqual(narrated["target"], target)
        events, _turn_runner = complete(omen(None), [CAST_SUCCEEDS, [4, 3, 2]])
        self.assertIn(
            "Fighter 1's Omen (tier 1) goes off, magnitude 8: its effect",
            events[3]["message"],
        )

    def test_a_tiers_bonus_has_nothing_to_add_to(self):
        events, _turn_runner = complete(omen(), [CAST_KEEN, [4, 3, 2]])
        (tier,) = status_payloads(events, "success_tier")
        self.assertEqual((tier["success_tier"], tier["effect_bonus"]), ("keen", 0))
        self.assertNotIn("effect_bonus", steps(events))
        self.assertEqual(status_payloads(events, "working_effect")[0]["magnitude"], 8)

    def test_a_working_asked_after_with_no_true_magic_rules_is_refused(self):
        state = crafter()
        caster = state.by_id(1)
        working = example_true_magic_rules().working("omen", 1)
        for profile in (TARMAR, TarmarProfile(magic=example_magic_rules())):
            with self.subTest(magic=profile.magic is not None):
                turn_runner, events = runner_on(state, (), profile)
                with self.assertRaisesRegex(ValueError, "needs True Magic rules"):
                    turn_runner._narrated_effect(caster, working)
                # A catalog spell has no narrated effect, whatever the rules.
                self.assertIsNone(
                    turn_runner._narrated_effect(caster, get_spell("heal"))
                )
                self.assertEqual(events, [])


class DrainFellsTheCasterTest(TestCase):
    def test_the_drain_takes_the_pool_below_nothing_and_cites_the_roll(self):
        # 5 Body, and the steady Scorch drains 12.
        state = whole_steady(body=5, fatal_chain=[2])
        caster = state.by_id(1)
        events, turn_runner = complete(
            state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE]
        )
        self.assertEqual(steps(events)[-3:], ["damage", "drain", "unconscious"])
        self.assertEqual(caster.body, 5 - 12)
        self.assertFalse(caster.conscious)
        cast_sequence = events[0]["sequence"]
        (collapse,) = status_payloads(events, "unconscious")
        self.assertEqual(collapse["chain"], [cast_sequence])
        self.assertEqual(caster.fatal_chain, [cast_sequence])
        # The fall finds no craft standing: nothing is lost, nothing is
        # drained twice, and the caster's craft did not end early.
        self.assertEqual(losses(events), [])
        self.assertEqual(len(drains(events)), 1)
        self.assertEqual(turn_runner._crafts_ended, set())

    def test_a_debt_band_that_fells_the_caster_loses_nothing_either(self):
        # 6 Fatigue, in the injury band: "Backlash" costs 2 for each of the
        # 3 owed.
        state = whole_borrowed()
        caster = state.by_id(1)
        caster.fatigue = 6
        events, turn_runner = complete(
            state,
            [
                CAST_SUCCEEDS,
                CONTROL_HELD,
                AIM_HITS,
                SCORCH_DICE,
                DEBT_BACKLASH_OF_THREE,
            ],
        )
        self.assertEqual(
            steps(events)[-6:],
            ["debt", "debt_roll", "debt_failure", "pool cost", "unconscious", "drain"],
        )
        self.assertEqual(caster.fatigue, 0)
        self.assertFalse(caster.conscious)
        self.assertEqual(losses(events), [])
        self.assertEqual(
            [drain["cost"] for drain in drains(events)], [STEADY_MANA * DRAIN_PER_MANA]
        )
        self.assertEqual(turn_runner._crafts_ended, set())


class PushAndBorrowTest(TestCase):
    def test_a_pushed_working_makes_its_one_control_roll_as_it_goes_off(self):
        state = crafter()
        choose_craft(state, **STEADY)
        crafting_rolls = [
            roll_purposes(craft(state)) for _turn in range(TURNS_PER_TIER * 2)
        ]
        self.assertEqual(crafting_rolls, [[], [], [], []])
        events, _turn_runner = complete(
            state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE]
        )
        self.assertEqual(
            roll_purposes(events), ["casting", "control", "spell aim", "spell damage"]
        )
        (control,) = status_payloads(events, "control_band")
        self.assertEqual(
            (control["push_mana"], control["held"], control["push_bonus"]),
            (STEADY["push"], True, 8),
        )
        self.assertEqual(state.by_id(2).body, 27 - 15)

    def test_a_band_that_lets_it_through_drops_the_bonus_and_it_went_off(self):
        state = whole_steady()
        events, _turn_runner = complete(
            state, [CAST_SUCCEEDS, CONTROL_WOBBLES, AIM_HITS, SCORCH_DICE]
        )
        self.assertEqual(state.by_id(2).body, 27 - 7)
        self.assertEqual(
            [drain["cost"] for drain in drains(events)], [STEADY_MANA * DRAIN_PER_MANA]
        )

    def test_a_working_a_control_band_stops_takes_the_stopped_drain(self):
        state = whole_steady()
        events, _turn_runner = complete(state, [CAST_SUCCEEDS, CONTROL_STOPS])
        self.assertEqual(
            steps(events), ["casting", "action", "control", "control_band", "drain"]
        )
        self.assertEqual(state.by_id(2).body, 27)
        self.assertEqual(
            [drain["cost"] for drain in drains(events)],
            [STEADY_MANA * COMPLETION_DRAINS[CastingEnding.STOPPED]],
        )

    def test_a_control_roll_that_runs_away_starts_a_runaway_of_the_whole_mana(self):
        state = whole_steady()
        complete(state, [CAST_SUCCEEDS, CONTROL_RUNS_AWAY])
        self.assertEqual(
            state.by_id(1).runaway,
            RunawayState(spell_key=STEADY_KEY, original_cost=STEADY_MANA),
        )

    def test_the_drain_counts_the_mana_the_craft_borrowed(self):
        # One point of the 4 came from the pool.
        state = whole_borrowed()
        events, _turn_runner = complete(
            state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE, DEBT_HOLDS]
        )
        self.assertEqual(
            [drain["cost"] for drain in drains(events)], [STEADY_MANA * DRAIN_PER_MANA]
        )

    def test_a_craft_that_borrowed_makes_one_debt_roll_as_it_goes_off(self):
        state = crafter(mana=1)
        choose_craft(state, **STEADY, borrow=True)
        crafting = [craft(state) for _turn in range(TURNS_PER_TIER * 2)]
        self.assertEqual([roll_purposes(events) for events in crafting], [[]] * 4)
        caster = state.by_id(1)
        self.assertEqual(caster.debt, 3)
        events, _turn_runner = complete(
            state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE, DEBT_HOLDS]
        )
        self.assertEqual(roll_purposes(events).count("debt"), 1)
        # After the effect, and before the drain.
        self.assertEqual(steps(events)[-4:], ["damage", "debt", "debt_roll", "drain"])
        # Each craft action reported the share it borrowed. The working's
        # own event borrows nothing more, so the events sum to the debt.
        payload = completions(events)[0]["payload"]
        self.assertEqual((payload["borrowed"], payload["debt"]), (0, 3))
        shares = [craft_actions(turn_events)[0]["borrowed"] for turn_events in crafting]
        self.assertEqual(shares, [0, 1, 1, 1])
        self.assertEqual(sum(shares) + payload["borrowed"], caster.debt)

    def test_the_debt_roll_follows_a_working_that_lost_its_mana_too(self):
        for faces, ending in (
            ([CAST_FAILS_MANA_LOST, DEBT_HOLDS], CastingEnding.FAILED),
            ([CAST_SUCCEEDS, CONTROL_STOPS, DEBT_HOLDS], CastingEnding.STOPPED),
        ):
            with self.subTest(ending=ending):
                events, _turn_runner = complete(whole_borrowed(), faces)
                self.assertEqual(steps(events)[-3:], ["debt", "debt_roll", "drain"])
                self.assertEqual(
                    [drain["cost"] for drain in drains(events)],
                    [STEADY_MANA * COMPLETION_DRAINS[ending]],
                )

    def test_a_runaway_band_on_the_debt_roll_starts_one_of_the_whole_mana(self):
        state = whole_borrowed()
        complete(
            state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE, DEBT_SURGE]
        )
        self.assertEqual(
            state.by_id(1).runaway,
            RunawayState(spell_key=STEADY_KEY, original_cost=STEADY_MANA),
        )

    def test_a_tiers_refund_is_capped_at_what_the_pool_paid(self):
        # Where a refund refills the pool and leaves the debt, it hands
        # back no more than the pool paid: nothing, for a craft whose every
        # share was borrowed, and the tier's point for one that paid a share.
        pool_refund = TarmarProfile(
            magic=replace(
                completion_rules(),
                borrowing=example_borrowing_rules(refunds_pay_debt_first=False),
            )
        )
        faces = [CAST_KEEN, CONTROL_HELD, AIM_HITS, SCORCH_DICE, DEBT_HOLDS]
        for purse, refund, mana_after in ((0, 0, 0), (1, 1, 1)):
            with self.subTest(purse=purse):
                state = steady_craft(TURNS_PER_TIER * 2, borrow=True, mana=purse)
                caster = state.by_id(1)
                debt = STEADY_MANA - purse
                self.assertEqual((caster.mana, caster.debt), (0, debt))
                events, _turn_runner = complete(state, faces, pool_refund)
                (tier,) = status_payloads(events, "success_tier")
                self.assertEqual(
                    (tier["success_tier"], tier["mana_refund"]), ("keen", refund)
                )
                self.assertEqual((caster.mana, caster.debt), (mana_after, debt))

    def test_a_craft_that_borrowed_nothing_makes_no_debt_roll(self):
        # It may borrow, and its caster owes from before.
        state = steady_craft(TURNS_PER_TIER * 2, borrow=True, debt=2)
        events, _turn_runner = complete(
            state, [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE]
        )
        self.assertNotIn("debt", roll_purposes(events))


def defended(
    state: BattleState, defender_id: int = 2, **defender_overrides: Any
) -> BattleState:
    """The state with a figure, figure 2 unless another is named, a mage
    who may counter (WIS 11, 6 mana, Shield known), with the overrides
    given."""
    defender = state.by_id(defender_id)
    fields: dict[str, Any] = {
        "spells": ["shield"],
        "wisdom": 11,
        "mana": 6,
        "max_mana": 6,
    }
    fields.update(defender_overrides)
    for name, value in fields.items():
        setattr(defender, name, value)
    return state


def countered(
    state: BattleState,
    faces,
    defenders: tuple[int, ...] = (2,),
    *,
    profile: TarmarProfile = COMPLETION_PROFILE,
    **defender_overrides: Any,
) -> tuple[list[dict], engine.TurnRunner]:
    """Each defender, figure 2 unless others are named, declares a counter
    of 3 mana in the order given, and the working goes off against them;
    the events from the opposed roll on, and the runner."""
    turn_runner, events = runner_on(state, faces, profile)
    for defender_id in defenders:
        defended(state, defender_id, **defender_overrides)
        choose_counter(state, 3, defender_id=defender_id)
        turn_runner.declare_counter(state.by_id(defender_id))
    events.clear()
    turn_runner._complete_workings()
    return events, turn_runner


class CounteredTest(TestCase):
    def test_a_countered_working_is_lost_and_leaves_no_contest(self):
        state = whole_steady()
        caster = state.by_id(1)
        events, turn_runner = countered(state, [CASTER_LOW, DEFENDER_HIGH])
        self.assertEqual(
            steps(events),
            ["counter", "counter", "counter_result", "craft_lost", "drain"],
        )
        drained = STEADY_MANA * COMPLETION_DRAINS[CastingEnding.COUNTERED]
        self.assertEqual(
            losses(events),
            [
                {
                    "craft_lost": True,
                    "ending": "countered",
                    "spell": STEADY_KEY,
                    "mana_spent": STEADY_MANA,
                    "drain": drained,
                    "defenders": [2],
                }
            ],
        )
        self.assertEqual([drain["cost"] for drain in drains(events)], [drained])
        # Nothing stands to be pressed again, and the event that says a
        # cast may be is not logged.
        self.assertIsNone(caster.contest)
        self.assertEqual(status_payloads(events, "spell_countered"), [])
        self.assertEqual(completions(events), [])
        self.assertIsNone(caster.crafting)
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})
        # The mana gathered is spent, and the counter that met it too.
        self.assertEqual(caster.mana, 10 - STEADY_MANA)
        self.assertIsNone(state.by_id(2).counter)

    def test_the_opposed_roll_counts_the_workings_whole_mana(self):
        # The example rules buy 1 for each 2 mana: 2 for the working's 4.
        events, _turn_runner = countered(whole_steady(), [CASTER_LOW, DEFENDER_HIGH])
        (result,) = status_payloads(events, "counter_result")
        self.assertEqual(result["caster_bonus"], 2)
        self.assertEqual(result["spell"], STEADY_KEY)

    def test_a_countered_craft_that_borrowed_makes_its_debt_roll(self):
        events, _turn_runner = countered(
            whole_borrowed(), [CASTER_LOW, DEFENDER_HIGH, DEBT_HOLDS]
        )
        self.assertEqual(
            steps(events)[-4:], ["craft_lost", "debt", "debt_roll", "drain"]
        )

    def test_the_defenders_are_named_in_the_contests_order(self):
        # Figure 3 declares before figure 2; the contest holds them by id.
        state = whole_steady()
        state.combatants.append(make_combatant(3, q=3, r=2, facing=3))
        events, _turn_runner = countered(
            state, [CASTER_LOW, DEFENDER_HIGH, DEFENDER_HIGH], defenders=(3, 2)
        )
        (lost,) = loss_events(events)
        self.assertEqual(lost["payload"]["defenders"], [2, 3])
        self.assertEqual(
            lost["message"],
            "Fighter 1's craft of Scorch (tier 2) is lost, countered by "
            "Fighter 2, Fighter 3: 4 mana spent",
        )
        self.assertEqual(
            status_payloads(events, "counter_result")[0]["countered"], True
        )

    def test_a_working_that_gets_through_goes_off(self):
        state = whole_steady()
        events, turn_runner = countered(
            state,
            [
                DEFENDER_HIGH,
                CASTER_LOW,
                CAST_SUCCEEDS,
                CONTROL_HELD,
                AIM_HITS,
                SCORCH_DICE,
            ],
        )
        self.assertEqual(losses(events), [])
        self.assertEqual(len(completions(events)), 1)
        self.assertEqual(state.by_id(2).body, 27 - 15)
        self.assertEqual(turn_runner._crafts_ended, set())


#: The completion rules where each level of familiarity adds 3 to a
#: counter's side.
COMPLETION_FAMILIARITY_PROFILE = TarmarProfile(
    magic=replace(completion_rules(), counterspell=FAMILIARITY_RULES)
)


def working_opposed(
    state: BattleState | None = None, **defender_overrides: Any
) -> tuple[dict, list[int]]:
    """The steady Scorch, the crafter's unless a state is given, goes off
    against a counter of 3 mana where familiarity counts; the opposed roll's
    result and each side's modifier, the caster's first."""
    events, _turn_runner = countered(
        whole_steady() if state is None else state,
        [CASTER_LOW, DEFENDER_HIGH],
        profile=COMPLETION_FAMILIARITY_PROFILE,
        **defender_overrides,
    )
    return opposed_result(events)


class CounteredWorkingFamiliarityTest(TestCase):
    """A working meets a counter with familiarity as a cast does: its caster
    adds its "knotwork" level, having no level in a working, and a defender
    reads a generic counter against the working's tier. Each level adds 3
    here. The steady Scorch is tier 2 and 4 mana, which buys its caster 2."""

    def test_the_caster_adds_its_true_level(self):
        # "Knotwork" 3: not the tier, the mana, or a level in another spell.
        state = whole_steady(
            skill_levels={KNOT_SKILL: 3}, spell_skill_levels={"fire_missile": 5}
        )
        result, modifiers = working_opposed(state)
        self.assertEqual(result["spell"], STEADY_KEY)
        self.assertEqual(result["caster_familiarity"], 3)
        # WIS 12, 2 for the 4 mana and 3 x 3.
        self.assertEqual(modifiers[0], 12 + 2 + 9)
        self.assertTrue(result["countered"])

    def test_a_generic_counter_reads_the_tier_and_not_the_mana(self):
        # Counter 2 reaches the tier-2 working, pushed to 4 mana as it is;
        # Counter 1 does not.
        for known, familiarity, source in (
            ("counter_2", 2, "generic"),
            ("counter_1", 0, None),
        ):
            with self.subTest(known=known):
                result, modifiers = working_opposed(spells=["shield", known])
                (entry,) = result["defender_totals"]
                self.assertEqual(
                    (entry["familiarity"], entry["familiarity_source"]),
                    (familiarity, source),
                )
                self.assertEqual(modifiers[1], 11 + 1 + 3 * familiarity)

    def test_a_defender_adds_its_true_level(self):
        result, modifiers = working_opposed(skill_levels={KNOT_SKILL: 4})
        (entry,) = result["defender_totals"]
        self.assertEqual(
            (entry["familiarity"], entry["familiarity_source"]), (4, "true")
        )
        self.assertEqual(modifiers[1], 11 + 1 + 12)

    def test_with_no_bonus_injected_a_working_names_no_familiarity(self):
        events, _turn_runner = countered(
            whole_steady(), [CASTER_LOW, DEFENDER_HIGH], spells=["shield", "counter_2"]
        )
        result, modifiers = opposed_result(events)
        self.assertNotIn("caster_familiarity", result)
        self.assertEqual(
            [list(entry) for entry in result["defender_totals"]],
            [["defender", "total", "mana", "bonus", "roll"]],
        )
        self.assertEqual(modifiers, [12 + 2, 11 + 1])


def turn(
    state: BattleState,
    faces,
    *,
    caster_choice: policy.Candidate = craft_candidate(**STEADY),
    enemy_choice: policy.Candidate | None = None,
    enemy_dexterity: int = 10,
    profile=COMPLETION_PROFILE,
) -> list[dict]:
    """A whole turn in which the caster (DEX 12) takes a craft action, the
    steady Scorch's unless another is given, and figure 2 does as given or
    defends; the events."""
    state.by_id(2).dexterity = enemy_dexterity
    choices = {1: caster_choice}
    if enemy_choice is not None:
        choices[2] = enemy_choice
    turn_runner, events = runner_on(state, [*INITIATIVE, *faces], profile)
    turn_runner.run(choosing_for(choices))
    return events


class WholeTurnTest(TestCase):
    """A working going off inside a turn."""

    def test_it_goes_off_in_the_actions_phase_after_the_last_action(self):
        state = steady_craft(3)
        events = turn(
            state,
            [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE],
            enemy_dexterity=8,
        )
        (crafted,) = [
            event
            for event in events_of_type(events, "action")
            if "craft" in event["payload"]
        ]
        (completed,) = completions(events)
        last_action = [
            event
            for event in events
            if event["phase"] == 5 and event["actor"] == "Fighter 2"
        ][-1]
        self.assertEqual(crafted["payload"]["turn"], 4)
        self.assertLess(crafted["sequence"], last_action["sequence"])
        self.assertLess(last_action["sequence"], completed["sequence"])
        self.assertEqual(completed["phase"], 5)
        self.assertEqual(state.by_id(2).body, 27 - 15)
        self.assertIsNone(state.by_id(1).crafting)

    def test_a_turn_that_does_not_finish_the_craft_completes_nothing(self):
        state = steady_craft(2)
        events = turn(state, [CAST_SUCCEEDS])
        self.assertEqual(completions(events), [])
        self.assertNotIn("casting", roll_purposes(events))
        self.assertEqual(len(craft_actions(events)), 1)

    def test_a_slower_enemys_hit_on_the_last_craft_turn_can_still_break_it(self):
        # Four points gathered: the roll to keep it is under 12 - 2 x 4.
        state = beside(3)
        events = turn(state, [*STRIKES, SNAGS], enemy_choice=ATTACK, enemy_dexterity=10)
        self.assertEqual(craft_actions(events)[0]["turn"], 4)
        (named,) = interruptions(events)
        self.assertEqual((named["gathered"], named["held"]), (STEADY_MANA, False))
        self.assertEqual([lost["ending"] for lost in losses(events)], ["interrupted"])
        self.assertEqual(completions(events), [])
        self.assertNotIn("casting", roll_purposes(events))

    def test_a_working_kept_through_the_hit_goes_off_after_it(self):
        state = beside(3)
        events = turn(
            state,
            [*STRIKES, KEEPS, CAST_SUCCEEDS, CONTROL_STOPS],
            enemy_choice=ATTACK,
            enemy_dexterity=10,
        )
        (named,) = [
            event for event in events if "craft_interrupted" in event["payload"]
        ]
        (completed,) = completions(events)
        self.assertTrue(named["payload"]["held"])
        self.assertLess(named["sequence"], completed["sequence"])

    def test_a_counter_declared_this_turn_meets_it_before_the_counters_lapse(self):
        for enemy_dexterity in (8, 14):
            with self.subTest(enemy_dexterity=enemy_dexterity):
                state = defended(steady_craft(3))
                events = turn(
                    state,
                    [CASTER_LOW, DEFENDER_HIGH],
                    enemy_choice=COUNTER,
                    enemy_dexterity=enemy_dexterity,
                )
                self.assertEqual(
                    [lost["ending"] for lost in losses(events)], ["countered"]
                )
                self.assertEqual(status_payloads(events, "counter_lapsed"), [])
                self.assertEqual(completions(events), [])

    def test_a_counter_declared_on_an_earlier_craft_turn_lapses(self):
        state = defended(steady_craft(2))
        events = turn(state, [], enemy_choice=COUNTER)
        (lapsed,) = status_payloads(events, "counter_lapsed")
        self.assertEqual((lapsed["caster"], lapsed["mana"]), (1, 3))
        self.assertEqual(losses(events), [])
        craft_state = state.by_id(1).crafting
        assert craft_state is not None
        self.assertEqual(craft_state.turns_done, 3)

    def test_a_caster_its_drain_fells_makes_its_survival_save_that_turn(self):
        # The working drains 9 Body a point here: 36, from 9.
        steep = TarmarProfile(magic=completion_rules(drain_per_mana=9))
        state = steady_craft(3, body=9)
        caster = state.by_id(1)
        events = turn(
            state,
            [CAST_SUCCEEDS, CONTROL_HELD, AIM_HITS, SCORCH_DICE, SAVE_FAILS],
            profile=steep,
        )
        self.assertEqual(caster.body, 9 - 36)
        self.assertFalse(caster.alive)
        (casting_roll,) = rolls_of(events, "casting")
        (save,) = rolls_of(events, "survival")
        (death,) = events_of_type(events, "death")
        self.assertEqual(
            death["payload"]["fatal_chain"],
            [casting_roll["sequence"], save["sequence"]],
        )
        self.assertEqual(death["turn"], casting_roll["turn"])
