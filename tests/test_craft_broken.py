"""A craft broken from outside (tarmar-engine #77): the roll a crafting
caster makes when damage lands on them, and the three things that end a
craft with no roll: unconsciousness, a grapple that takes hold, and an enemy
in hand-to-hand with a caster who has a weapon in hand.

Every number and name here is invented, as in ``test_craft``, whose crafter
and steady craft these tests use: a tier-2 Scorch pushed by 2, a point
gathered on each of its 4 turns. The roll to keep a working is the example
rules' 2d6 under WIS (12 for the crafter), less 2 per point gathered: a miss
by 1 to 3 is the "Snag" band, by 4 or more "Unravel", which runs away, and a
natural 11 runs away whatever the target. A craft lost drains Body per point
gathered by how it ended: 6 interrupted, 1 unconscious, 5 grappled and 7
countered, each unlike the 3 a working that goes off drains. The tests check
the engine's *structure*: what forces the roll, what its target counts, the
order a lost working's events come in, and that each injected value is the
one used.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine import engine, policy
from tarmar_engine.magic import CastingEnding, RollUnderRule
from tarmar_engine.profile import TarmarProfile
from tarmar_engine.state import (
    BODY_POOL,
    FATIGUE_POOL,
    BattleState,
    CombatantState,
    RunawayState,
    WeaponState,
)

from .test_cast_pieces import runner_on
from .test_craft import (
    DEBT_HOLDS,
    DEBT_SURGE,
    DEFEND,
    LOST_DRAINS,
    STEADY,
    borrowed_craft,
    choose_craft,
    craft,
    craft_actions,
    craft_candidate,
    craft_rules,
    crafter_beside,
    lose,
    losses,
    standing_craft,
    steady_craft,
    working_key,
)
from .test_engine import _box_in, events_of_type, roll_purposes, rolls_of
from .test_magic import status_payloads
from .test_runaway import example_runaway_rules
from .test_state import make_combatant
from .test_true_cast import drains
from .test_true_magic import SNAG, UNRAVEL, example_interruption_rules

#: What a craft that is lost drains per mana gathered, by how it ended. Not
#: Tarmar's numbers.
BROKEN_DRAINS = {
    **LOST_DRAINS,
    CastingEnding.INTERRUPTED: 6,
    CastingEnding.UNCONSCIOUS: 1,
    CastingEnding.GRAPPLED: 5,
    CastingEnding.COUNTERED: 7,
}
BROKEN_RULES = craft_rules(lost_drain_per_mana=BROKEN_DRAINS)
BROKEN_PROFILE = TarmarProfile(magic=BROKEN_RULES)
#: The same rules with Runaway rules beside them, so a band that runs away
#: starts one.
RUNAWAY_PROFILE = TarmarProfile(
    magic=replace(BROKEN_RULES, runaway=example_runaway_rules())
)
#: The steady Scorch's key.
STEADY_KEY = working_key("scorch", 2)
#: The roll to keep a working (2d6): one that holds against any target these
#: tests reach; a miss by 1 of the target of 8 two turns in, in "Snag"; a
#: miss by 4 of it, in "Unravel"; and the example rules' automatic total.
KEEPS = [1, 1]
SNAGS = [4, 5]
UNRAVELS = [6, 6]
AUTOMATIC = [5, 6]
#: The sequence numbers of a hit's rolls, as its damage hands them on.
HIT_CHAIN = [41, 42]
#: How the action of a grapple that takes hold names its outcome: on its
#: roll, and on a natural 20.
HOLD_OUTCOMES = ("hit", "critical grapple")


def hit(
    state: BattleState,
    faces=(),
    profile=BROKEN_PROFILE,
    *,
    net: int = 3,
    chain=HIT_CHAIN,
) -> tuple[list[dict], engine.TurnRunner]:
    """Land damage from figure 2 on the caster; the events and the runner."""
    turn_runner, events = runner_on(state, faces, profile)
    turn_runner.apply_damage(
        state.by_id(2),
        state.by_id(1),
        net,
        raw=net,
        reaches_body=False,
        chain=list(chain),
    )
    return events, turn_runner


def interruptions(events) -> list[dict]:
    """The payload of each roll to keep a working an event stream names."""
    return status_payloads(events, "craft_interrupted")


def concentration_rolls(events) -> list[dict]:
    """Each roll to keep a working an event stream logged."""
    return rolls_of(events, "concentration")


def status_order(events) -> list[str]:
    """What each step of a lost working is, in the order logged: its status
    events, and before them the action of a grapple that took hold."""
    names = (
        "craft_interrupted",
        "craft_lost",
        "runaway_begins",
        "debt_roll",
        "debt_failure",
        "unconscious",
        "grapple_ended",
    )
    order = []
    for event in events:
        payload = event["payload"]
        if (
            event["event_type"] == "action"
            and payload.get("outcome") in HOLD_OUTCOMES
            # An attack that hits names its weapon; a grapple has none.
            and "weapon" not in payload
        ):
            order.append("hold")
        if event["event_type"] != "status":
            continue
        if payload.get("reason") == engine.DRAIN_REASON:
            order.append("drain")
            continue
        order.extend(name for name in names if name in payload)
    return order


class HitForcesTheRollTest(TestCase):
    def test_damage_that_lands_forces_the_roll_and_a_roll_that_holds_keeps_it(self):
        state = steady_craft()
        caster = state.by_id(1)
        standing = replace(standing_craft(caster))
        events, turn_runner = hit(state)
        self.assertEqual(
            [event["event_type"] for event in events], ["damage", "roll", "status"]
        )
        (roll,) = concentration_rolls(events)
        self.assertEqual(roll["actor"], caster.name)
        self.assertEqual(
            roll["payload"],
            {
                "purpose": "concentration",
                "specification": "2d6",
                "faces": [3, 3],
                "modifier": 0,
                "total": 6,
                "target_number": 8,
                "versus": "WIS",
                "roll_under": True,
                "outcome": "held",
            },
        )
        self.assertEqual(
            events[2]["payload"],
            {
                "craft_interrupted": True,
                "spell": STEADY_KEY,
                "gathered": 2,
                "target": 8,
                "natural": 6,
                "margin": -2,
                "held": True,
                "band": None,
                "runaway": False,
            },
        )
        self.assertIn("keeps their craft of Scorch (tier 2)", events[2]["message"])
        self.assertEqual(caster.crafting, standing)
        self.assertEqual(turn_runner._crafts_ended, set())

    def test_a_blow_the_armour_stops_forces_none(self):
        state = steady_craft()
        events, _turn_runner = hit(state, net=0)
        self.assertEqual([event["event_type"] for event in events], ["damage"])
        self.assertEqual(events[0]["payload"]["net"], 0)
        self.assertIsNotNone(state.by_id(1).crafting)

    def test_a_cost_the_caster_pays_from_a_pool_forces_none(self):
        state = steady_craft()
        turn_runner, events = runner_on(state, (), BROKEN_PROFILE)
        turn_runner.apply_pool_cost(state.by_id(1), FATIGUE_POOL, 3, "running")
        self.assertEqual([event["event_type"] for event in events], ["status"])
        self.assertIsNotNone(state.by_id(1).crafting)

    def test_a_figure_with_no_craft_makes_none(self):
        state = steady_craft()
        turn_runner, events = runner_on(state, (), BROKEN_PROFILE)
        turn_runner.apply_damage(
            state.by_id(1), state.by_id(2), 3, raw=3, reaches_body=False, chain=[]
        )
        self.assertEqual([event["event_type"] for event in events], ["damage"])

    def test_each_hit_forces_its_own_roll(self):
        state = steady_craft()
        turn_runner, events = runner_on(state, (), BROKEN_PROFILE)
        for _blow in range(2):
            turn_runner.apply_damage(
                state.by_id(2),
                state.by_id(1),
                1,
                raw=1,
                reaches_body=False,
                chain=HIT_CHAIN,
            )
        self.assertEqual(roll_purposes(events), ["concentration", "concentration"])
        self.assertEqual([roll["held"] for roll in interruptions(events)], [True, True])

    def test_a_caster_already_down_makes_none(self):
        # No fall leaves a craft standing; a figure that is down rolls
        # nothing all the same.
        state = steady_craft()
        caster = state.by_id(1)
        caster.conscious = False
        events, _turn_runner = hit(state)
        self.assertEqual([event["event_type"] for event in events], ["damage"])
        self.assertIsNotNone(caster.crafting)

    def test_a_roll_asked_of_a_figure_with_no_craft_is_refused(self):
        state = steady_craft()
        turn_runner, events = runner_on(state, (), BROKEN_PROFILE)
        with self.assertRaisesRegex(ValueError, "no craft to be interrupted"):
            turn_runner._interruption_roll(state.by_id(2), HIT_CHAIN)
        self.assertEqual(events, [])


class RollTargetTest(TestCase):
    def target(self, state: BattleState, profile=BROKEN_PROFILE) -> int:
        events, _turn_runner = hit(state, [KEEPS], profile)
        (roll,) = concentration_rolls(events)
        (named,) = interruptions(events)
        self.assertEqual(named["target"], roll["payload"]["target_number"])
        return named["target"]

    def test_it_takes_the_injected_penalty_for_each_point_gathered(self):
        for turns in (1, 3):
            with self.subTest(turns=turns):
                self.assertEqual(self.target(steady_craft(turns)), 12 - 2 * turns)
        steeper = TarmarProfile(
            magic=craft_rules(
                interruption=example_interruption_rules(penalty_per_mana=3)
            )
        )
        self.assertEqual(self.target(steady_craft(3), steeper), 12 - 3 * 3)

    def test_the_points_gathered_count_the_pushed_shares_and_not_the_tier(self):
        # An unpushed tier-1 working gathers nothing on its first turn and
        # its one point on its second.
        for turns, gathered in ((1, 0), (2, 1)):
            with self.subTest(turns=turns):
                state = crafter_beside(wisdom=12)
                choose_craft(state, "scorch", 1)
                for _turn in range(turns):
                    craft(state)
                self.assertEqual(self.target(state), 12 - 2 * gathered)

    def test_it_is_under_the_injected_attribute_with_the_injected_dice(self):
        by_wits = TarmarProfile(
            magic=craft_rules(
                interruption=example_interruption_rules(
                    roll=RollUnderRule(dice="2d8", attribute="INT")
                )
            )
        )
        events, _turn_runner = hit(steady_craft(), [KEEPS], by_wits)
        (roll,) = concentration_rolls(events)
        self.assertEqual(
            (
                roll["payload"]["specification"],
                roll["payload"]["versus"],
                roll["payload"]["target_number"],
            ),
            ("2d8", "INT", 14 - 2 * 2),
        )

    def test_it_takes_the_injury_band_as_the_hit_leaves_it(self):
        # 22 Fatigue of 40: unhurt until the 3 that land take it to 19.
        state = steady_craft(fatigue=22)
        caster = state.by_id(1)
        reactions = BROKEN_PROFILE.reactions
        self.assertEqual(reactions.injury_penalty(caster), 0)
        target = self.target(state)
        band = reactions.injury_penalty(caster)
        self.assertGreater(band, 0)
        self.assertEqual(target, 12 - 2 * 2 - band)


class RollFailsTest(TestCase):
    def test_a_band_loses_the_working_and_its_drain_is_the_interrupted_one(self):
        state = steady_craft()
        caster = state.by_id(1)
        events, turn_runner = hit(state, [SNAGS], RUNAWAY_PROFILE)
        (named,) = interruptions(events)
        self.assertEqual(
            (named["natural"], named["margin"], named["held"]), (9, 1, False)
        )
        self.assertEqual((named["band"], named["runaway"]), (SNAG.key, False))
        (roll,) = concentration_rolls(events)
        self.assertEqual(roll["payload"]["outcome"], SNAG.label)
        self.assertEqual(
            losses(events),
            [
                {
                    "craft_lost": True,
                    "ending": "interrupted",
                    "spell": STEADY_KEY,
                    "mana_spent": 2,
                    "drain": 2 * BROKEN_DRAINS[CastingEnding.INTERRUPTED],
                }
            ],
        )
        self.assertEqual(
            status_order(events), ["craft_interrupted", "craft_lost", "drain"]
        )
        self.assertEqual(
            drains(events),
            [{BODY_POOL: 27 - 12, "cost": 12, "reason": engine.DRAIN_REASON}],
        )
        self.assertIsNone(caster.crafting)
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})
        # "Snag" is no Runaway: with Runaway rules injected, none starts.
        self.assertIsNone(caster.runaway)
        self.assertIn("broken by the hit: Snag", events[2]["message"])
        self.assertIn("a hit broke their concentration", events[3]["message"])

    def test_a_runaway_band_starts_a_runaway_at_the_mana_gathered(self):
        # 2 of the working's 4 mana gathered: the Runaway feeds on the 2.
        state = steady_craft()
        caster = state.by_id(1)
        events, _turn_runner = hit(state, [UNRAVELS], RUNAWAY_PROFILE)
        (named,) = interruptions(events)
        self.assertEqual(
            (named["margin"], named["band"], named["runaway"]),
            (4, UNRAVEL.key, True),
        )
        (begun,) = status_payloads(events, "runaway_begins")
        self.assertEqual((begun["spell"], begun["original_cost"]), (STEADY_KEY, 2))
        self.assertEqual(
            caster.runaway, RunawayState(spell_key=STEADY_KEY, original_cost=2)
        )
        self.assertEqual(
            status_order(events),
            ["craft_interrupted", "craft_lost", "runaway_begins", "drain"],
        )
        self.assertEqual(losses(events)[0]["ending"], "interrupted")

    def test_a_runaway_band_with_no_runaway_rules_is_flagged_and_starts_nothing(self):
        state = steady_craft()
        events, _turn_runner = hit(state, [UNRAVELS])
        self.assertTrue(interruptions(events)[0]["runaway"])
        self.assertEqual(status_payloads(events, "runaway_begins"), [])
        self.assertIsNone(state.by_id(1).runaway)
        self.assertEqual(len(losses(events)), 1)

    def test_an_automatic_total_runs_away_whatever_its_margin(self):
        # One turn in the target is 10: an 11 misses by 1, "Snag" by its
        # margin, and is on the automatic list.
        state = steady_craft(1)
        events, _turn_runner = hit(state, [AUTOMATIC], RUNAWAY_PROFILE)
        (named,) = interruptions(events)
        self.assertEqual(
            (named["natural"], named["margin"], named["band"], named["runaway"]),
            (11, 1, UNRAVEL.key, True),
        )
        (begun,) = status_payloads(events, "runaway_begins")
        self.assertEqual(begun["original_cost"], 1)

    def test_a_working_with_nothing_gathered_is_lost_and_starts_no_runaway(self):
        # An unpushed tier-1 working gathers nothing on its first turn. The
        # automatic total runs away whatever the target, and a 12 misses a
        # target of 6 by more than "Snag" covers: either way the band is
        # "Unravel", and the working has nothing to run away with.
        for wisdom, faces in ((12, AUTOMATIC), (6, UNRAVELS)):
            with self.subTest(wisdom=wisdom):
                state = crafter_beside(wisdom=wisdom)
                caster = choose_craft(state, "scorch", 1)
                craft(state)
                events, _turn_runner = hit(state, [faces], RUNAWAY_PROFILE)
                (named,) = interruptions(events)
                self.assertEqual(
                    (named["gathered"], named["held"], named["band"]),
                    (0, False, UNRAVEL.key),
                )
                self.assertFalse(named["runaway"])
                (lost,) = losses(events)
                self.assertEqual(
                    (lost["ending"], lost["mana_spent"], lost["drain"]),
                    ("interrupted", 0, 0),
                )
                self.assertEqual(status_payloads(events, "runaway_begins"), [])
                self.assertIsNone(caster.runaway)
                self.assertIsNone(caster.crafting)
        # The same working a turn later has its one point gathered, and
        # runs away with it.
        state = crafter_beside(wisdom=6)
        caster = choose_craft(state, "scorch", 1)
        craft(state)
        craft(state)
        events, _turn_runner = hit(state, [UNRAVELS], RUNAWAY_PROFILE)
        (named,) = interruptions(events)
        self.assertEqual((named["gathered"], named["runaway"]), (1, True))
        self.assertEqual(
            caster.runaway,
            RunawayState(spell_key=working_key("scorch", 1), original_cost=1),
        )

    def test_an_ending_the_rules_name_no_drain_for_logs_no_cost(self):
        # The crafting rules of ``test_craft`` drain nothing for a working
        # interrupted.
        state = steady_craft()
        events, _turn_runner = hit(state, [SNAGS], TarmarProfile(magic=craft_rules()))
        self.assertEqual(losses(events)[0]["drain"], 0)
        self.assertEqual(drains(events), [])
        for event in events:
            self.assertNotIn("spends 0", event["message"])

    def test_a_craft_that_borrowed_rolls_its_debt_after_the_runaway_starts(self):
        state = borrowed_craft()
        caster = state.by_id(1)
        events, _turn_runner = hit(state, [UNRAVELS, DEBT_HOLDS], RUNAWAY_PROFILE)
        self.assertEqual(roll_purposes(events), ["concentration", "debt"])
        self.assertEqual(
            status_order(events),
            ["craft_interrupted", "craft_lost", "runaway_begins", "debt_roll", "drain"],
        )
        self.assertEqual(caster.debt, 1)

    def test_a_drain_that_fells_the_caster_cites_the_hit_and_the_roll(self):
        # 12 Body, and the interrupted working drains 6 for each of its 2.
        state = steady_craft(body=12)
        caster = state.by_id(1)
        events, _turn_runner = hit(state, [SNAGS])
        (roll,) = concentration_rolls(events)
        cited = [*HIT_CHAIN, roll["sequence"]]
        self.assertFalse(caster.conscious)
        (collapse,) = status_payloads(events, "unconscious")
        self.assertEqual(collapse["chain"], cited)
        self.assertEqual(caster.fatal_chain, cited)
        # The fall finds no craft standing: nothing is lost or drained twice.
        self.assertEqual([lost["ending"] for lost in losses(events)], ["interrupted"])
        self.assertEqual(len(drains(events)), 1)


class LoseWorkingContractTest(TestCase):
    """What :meth:`_lose_working` is told beyond the ending, asked directly,
    under the rules that let a Runaway start."""

    def test_every_ending_says_why_the_craft_was_lost(self):
        reasons = {
            CastingEnding.ABANDONED: "abandoned for another action",
            CastingEnding.BLOCKED: "they may not craft where they are",
            CastingEnding.UNPAID: "their mana cannot pay its share",
            CastingEnding.INTERRUPTED: "a hit broke their concentration",
            CastingEnding.UNCONSCIOUS: "they fell unconscious",
            CastingEnding.GRAPPLED: "they are held in a grapple",
            CastingEnding.COUNTERED: "countered by Fighter 2",
        }
        self.assertEqual(set(reasons), set(engine.CRAFT_LOSS_REASONS))
        for ending, reason in reasons.items():
            with self.subTest(ending=ending):
                defenders = (2,) if ending is CastingEnding.COUNTERED else ()
                events, _crafts_ended = lose(
                    steady_craft(3), ending, (), RUNAWAY_PROFILE, defenders=defenders
                )
                self.assertEqual(
                    events[0]["message"],
                    f"Fighter 1's craft of Scorch (tier 2) is lost, {reason}: "
                    "3 mana spent",
                )

    def test_each_ending_drains_its_own_amount(self):
        for ending in (
            CastingEnding.INTERRUPTED,
            CastingEnding.UNCONSCIOUS,
            CastingEnding.GRAPPLED,
        ):
            with self.subTest(ending=ending):
                state = steady_craft(3)
                events, _crafts_ended = lose(state, ending, (), RUNAWAY_PROFILE)
                (lost,) = losses(events)
                drained = 3 * BROKEN_DRAINS[ending]
                self.assertEqual(
                    lost,
                    {
                        "craft_lost": True,
                        "ending": ending.value,
                        "spell": STEADY_KEY,
                        "mana_spent": 3,
                        "drain": drained,
                    },
                )
                self.assertEqual([drain["cost"] for drain in drains(events)], [drained])

    def test_no_runaway_starts_unless_the_working_runs_away(self):
        state = steady_craft()
        events, _crafts_ended = lose(
            state, CastingEnding.INTERRUPTED, (), RUNAWAY_PROFILE
        )
        self.assertEqual(status_payloads(events, "runaway_begins"), [])
        self.assertIsNone(state.by_id(1).runaway)

    def test_the_runaway_starts_before_the_debt_roll_and_the_drain(self):
        # The debt roll lands in the band that starts a Runaway too: the
        # working's own is already feeding, so the band's starts nothing.
        state = borrowed_craft()
        caster = state.by_id(1)
        events, _crafts_ended = lose(
            state,
            CastingEnding.INTERRUPTED,
            [DEBT_SURGE],
            RUNAWAY_PROFILE,
            runaway=True,
        )
        self.assertEqual(
            status_order(events),
            ["craft_lost", "runaway_begins", "debt_roll", "debt_failure", "drain"],
        )
        (begun,) = status_payloads(events, "runaway_begins")
        self.assertEqual(begun["original_cost"], 2)
        (kept,) = [
            event["payload"]
            for event in events_of_type(events, "info")
            if "runaway_kept" in event["payload"]
        ]
        self.assertEqual(kept["runaway_kept"], STEADY_KEY)
        self.assertEqual(
            caster.runaway, RunawayState(spell_key=STEADY_KEY, original_cost=2)
        )

    def test_a_chain_given_is_what_a_fall_from_the_drain_cites(self):
        state = steady_craft(body=12, fatal_chain=[3])
        caster = state.by_id(1)
        events, _crafts_ended = lose(
            state, CastingEnding.INTERRUPTED, (), RUNAWAY_PROFILE, chain=[17, 23]
        )
        self.assertFalse(caster.conscious)
        self.assertEqual(status_payloads(events, "unconscious")[0]["chain"], [17, 23])
        self.assertEqual(caster.fatal_chain, [17, 23])

    def test_with_no_chain_the_fatal_chain_stands_as_it_was(self):
        state = steady_craft(body=12, fatal_chain=[3])
        caster = state.by_id(1)
        events, _crafts_ended = lose(
            state, CastingEnding.INTERRUPTED, (), RUNAWAY_PROFILE
        )
        self.assertFalse(caster.conscious)
        self.assertEqual(status_payloads(events, "unconscious")[0]["chain"], [])
        self.assertEqual(caster.fatal_chain, [3])

    def test_a_countered_working_names_who_countered_it(self):
        state = steady_craft()
        state.combatants.append(make_combatant(3, q=3, r=2))
        caster = state.by_id(1)
        taken = caster.crafting
        caster.crafting = None
        # Named in the order given, which is not the order of their ids.
        events, crafts_ended = lose(
            state,
            CastingEnding.COUNTERED,
            (),
            RUNAWAY_PROFILE,
            craft=taken,
            defenders=(3, 2),
        )
        self.assertEqual(
            losses(events),
            [
                {
                    "craft_lost": True,
                    "ending": "countered",
                    "spell": STEADY_KEY,
                    "mana_spent": 2,
                    "drain": 2 * BROKEN_DRAINS[CastingEnding.COUNTERED],
                    "defenders": [3, 2],
                }
            ],
        )
        self.assertEqual(
            events[0]["message"],
            "Fighter 1's craft of Scorch (tier 2) is lost, countered by "
            "Fighter 3, Fighter 2: 2 mana spent",
        )
        self.assertEqual([drain["cost"] for drain in drains(events)], [14])
        self.assertEqual(crafts_ended, {caster.combatant_id})

    def test_defenders_are_named_for_a_countered_working_and_no_other(self):
        for ending, defenders in (
            (CastingEnding.INTERRUPTED, (2,)),
            (CastingEnding.COUNTERED, ()),
        ):
            with self.subTest(ending=ending):
                state = steady_craft()
                caster = state.by_id(1)
                standing = replace(standing_craft(caster))
                turn_runner, events = runner_on(state, (), RUNAWAY_PROFILE)
                with self.assertRaisesRegex(ValueError, "who countered it"):
                    turn_runner._lose_working(caster, ending, defenders=defenders)
                self.assertEqual(events, [])
                self.assertEqual(caster.crafting, standing)
                self.assertEqual(turn_runner._crafts_ended, set())


class UnconsciousTest(TestCase):
    def test_a_hit_that_fells_the_caster_ends_the_craft_with_no_roll(self):
        state = borrowed_craft(fatigue=3)
        caster = state.by_id(1)
        events, turn_runner = hit(state, net=5)
        self.assertFalse(caster.conscious)
        # Neither the roll to keep it nor the debt roll: a figure that is
        # down rolls nothing, and what it borrowed stays owed.
        self.assertEqual(roll_purposes(events), [])
        self.assertEqual(caster.debt, 1)
        self.assertEqual(status_order(events), ["unconscious", "craft_lost", "drain"])
        (lost,) = losses(events)
        self.assertEqual(
            (lost["ending"], lost["mana_spent"], lost["drain"]),
            ("unconscious", 2, 2 * BROKEN_DRAINS[CastingEnding.UNCONSCIOUS]),
        )
        self.assertEqual(caster.body, 27 - 2)
        self.assertIsNone(caster.crafting)
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})
        # What the lost working drains is cited against the hit that felled
        # them.
        self.assertEqual(caster.fatal_chain, HIT_CHAIN)

    def test_a_pool_cost_that_fells_the_caster_ends_it(self):
        state = steady_craft(fatal_chain=[3])
        caster = state.by_id(1)
        turn_runner, events = runner_on(state, (), BROKEN_PROFILE)
        turn_runner.apply_pool_cost(caster, FATIGUE_POOL, 40, "running")
        self.assertFalse(caster.conscious)
        self.assertEqual([lost["ending"] for lost in losses(events)], ["unconscious"])
        self.assertEqual(roll_purposes(events), [])
        # The cost named no rolls, and neither does the drain: the older
        # chain stands.
        self.assertEqual(caster.fatal_chain, [3])

    def test_a_runaway_that_feeds_the_caster_down_ends_it_in_phase_two(self):
        # 8 mana after two shares; the example Runaway drains 2 x 4 + 1.
        state = steady_craft()
        caster = state.by_id(1)
        caster.runaway = RunawayState(spell_key="fire_missile", original_cost=4)
        turn_runner, events = runner_on(state, (), RUNAWAY_PROFILE)
        turn_runner.phase_renew_spells()
        self.assertFalse(caster.conscious)
        (lost,) = [event for event in events if "craft_lost" in event["payload"]]
        self.assertEqual((lost["phase"], lost["payload"]["ending"]), (2, "unconscious"))
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})

    def test_a_figure_with_no_craft_falls_as_it_always_did(self):
        state = steady_craft()
        turn_runner, events = runner_on(state, (), BROKEN_PROFILE)
        turn_runner.apply_pool_cost(state.by_id(2), FATIGUE_POOL, 40, "running")
        self.assertEqual(
            [event["event_type"] for event in events], ["status", "status"]
        )
        self.assertEqual(losses(events), [])
        self.assertEqual(turn_runner._crafts_ended, set())


#: A grapple attempt (1d20) that takes hold, one that takes hold on a
#: natural 20, and one that slips.
HOLDS = [15]
HOLDS_OUTRIGHT = [20]
SLIPS = [2]


def beside(turns: int = 2, **caster_overrides) -> BattleState:
    """The crafter (WIS 12) some turns into the steady Scorch, a figure in
    the next hex that a Game Master lets close with them."""
    fields = {"wisdom": 12}
    fields.update(caster_overrides)
    state = steady_craft(turns, state=crafter_beside(**fields))
    state.by_id(2).hth_agreed_with = [1]
    return state


def grapple(state: BattleState, faces, profile=BROKEN_PROFILE) -> list[dict]:
    """Figure 2 tries to grapple the caster; the events."""
    grappler = state.by_id(2)
    grappler.chosen_target = 1
    turn_runner, events = runner_on(state, faces, profile)
    turn_runner.attempt_grapple(grappler)
    return events


class GrappleTest(TestCase):
    def test_a_grapple_that_takes_hold_ends_the_craft_with_no_roll(self):
        for faces in (HOLDS, HOLDS_OUTRIGHT):
            with self.subTest(faces=faces):
                state = beside()
                caster = state.by_id(1)
                events = grapple(state, [faces])
                self.assertEqual(caster.grappled_by, 2)
                self.assertEqual(roll_purposes(events), ["grapple attempt"])
                # The hold is reported, and then what it cost the caster.
                self.assertEqual(status_order(events), ["hold", "craft_lost", "drain"])
                (lost,) = losses(events)
                self.assertEqual(
                    (lost["ending"], lost["mana_spent"], lost["drain"]),
                    ("grappled", 2, 2 * BROKEN_DRAINS[CastingEnding.GRAPPLED]),
                )
                self.assertEqual(caster.body, 27 - 10)
                self.assertIsNone(caster.crafting)

    def test_it_is_the_figure_held_whose_craft_ends(self):
        # The crafter takes the hold: nothing in the engine lets a crafting
        # figure grapple, and a hold it took would end no craft of its own.
        state = beside()
        caster, other = state.by_id(1), state.by_id(2)
        turn_runner, events = runner_on(state, (), BROKEN_PROFILE)
        turn_runner._establish_grapple(caster, other)
        self.assertEqual((caster.grappling, other.grappled_by), (2, 1))
        self.assertEqual(events, [])
        self.assertIsNotNone(caster.crafting)

    def test_a_grapple_that_slips_ends_nothing(self):
        state = beside(weapon=WeaponState())
        caster = state.by_id(1)
        events = grapple(state, [SLIPS])
        self.assertIsNone(caster.grappled_by)
        self.assertEqual(losses(events), [])
        self.assertEqual(standing_craft(caster).turns_done, 2)

    def test_a_drain_that_fells_the_held_caster_leaves_no_hold_and_one_loss(self):
        # 10 Body, and the working lost to a grapple drains 5 for each of
        # its 2.
        for faces in (HOLDS, HOLDS_OUTRIGHT):
            with self.subTest(faces=faces):
                state = beside(body=10)
                caster, grappler = state.by_id(1), state.by_id(2)
                events = grapple(state, [faces])
                self.assertFalse(caster.conscious)
                self.assertEqual((caster.grappled_by, grappler.grappling), (None, None))
                self.assertEqual(
                    [lost["ending"] for lost in losses(events)], ["grappled"]
                )
                # The hold is reported before the fall that ends it.
                self.assertEqual(
                    status_order(events),
                    ["hold", "craft_lost", "drain", "unconscious", "grapple_ended"],
                )


class HandToHandTest(TestCase):
    def test_with_a_weapon_in_hand_the_next_craft_action_loses_the_working(self):
        state = beside()
        caster = state.by_id(1)
        events = grapple(state, [SLIPS])
        # The attempt that slipped still closed to hand-to-hand, and ended
        # nothing by itself.
        self.assertEqual(caster.hth_with, [2])
        self.assertEqual(losses(events), [])
        self.assertIsNotNone(caster.crafting)
        events = craft(state, BROKEN_PROFILE)
        self.assertEqual(roll_purposes(events), [])
        self.assertEqual([lost["ending"] for lost in losses(events)], ["blocked"])
        self.assertIsNone(caster.crafting)

    def test_with_empty_hands_the_craft_goes_on(self):
        state = beside(weapon=WeaponState())
        caster = state.by_id(1)
        grapple(state, [SLIPS])
        self.assertEqual(caster.hth_with, [2])
        (action,) = craft_actions(craft(state, BROKEN_PROFILE))
        self.assertEqual(action["craft"], engine.CRAFT_CONTINUED)
        self.assertEqual(standing_craft(caster).turns_done, 3)

    def test_the_blow_that_opens_it_forces_the_usual_roll_if_it_lands(self):
        state = beside(weapon=WeaponState())
        caster, striker = state.by_id(1), state.by_id(2)
        striker.chosen_target = 1
        turn_runner, events = runner_on(state, [[19], [6], KEEPS], BROKEN_PROFILE)
        turn_runner.hth_strike(striker)
        self.assertEqual(caster.hth_with, [2])
        self.assertGreater(events_of_type(events, "damage")[0]["payload"]["net"], 0)
        self.assertEqual([roll["held"] for roll in interruptions(events)], [True])
        self.assertIsNotNone(caster.crafting)


def knock_prone(state: BattleState) -> list[dict]:
    """Figure 2 forces the caster back with no hex to retreat to, and the
    caster fails the roll to keep their feet; the events."""
    caster, pusher = state.by_id(1), state.by_id(2)
    # On the rim, with the hexes beside them filled.
    caster.position = (6, 0)
    pusher.position = (5, 0)
    _box_in(state, (6, -1), (5, 1))
    turn_runner, events = runner_on(state, [[6, 6, 6]], BROKEN_PROFILE)
    turn_runner.push_back(pusher, caster)
    assert caster.prone
    return events


class FallTest(TestCase):
    def test_a_caster_knocked_prone_keeps_the_craft_and_may_carry_it_on(self):
        state = beside()
        caster = state.by_id(1)
        events = knock_prone(state)
        self.assertEqual(losses(events), [])
        self.assertEqual(roll_purposes(events), ["retreat save"])
        (action,) = craft_actions(craft(state, BROKEN_PROFILE))
        self.assertEqual(action["craft"], engine.CRAFT_CONTINUED)
        self.assertEqual(standing_craft(caster).turns_done, 3)


ATTACK = policy.Candidate("j", "ATTACK", 0.0, "", target_id=1)
GRAPPLE = policy.Candidate("o", "ATTEMPT HTH", 0.0, "", target_id=1)
CONTINUE = craft_candidate(**STEADY)
#: A turn's dice up to the first action: each figure's initiative.
INITIATIVE = [[3], [3]]
#: An attack (1d20) that hits, and its damage (2d6).
STRIKES = [[16], [2, 2]]


def choosing_for(choices: dict[int, policy.Candidate]):
    """A turn's chooser: each figure takes the choice given for it."""

    def choose_option(state: BattleState, actor: CombatantState) -> policy.Decision:
        chosen = choices.get(actor.combatant_id, DEFEND)
        return policy.Decision(chosen=chosen, candidates=[chosen])

    return choose_option


class WholeTurnTest(TestCase):
    """A craft broken inside a turn: before its caster's action, and after."""

    def turn(
        self,
        state: BattleState,
        enemy_choice: policy.Candidate,
        faces,
        *,
        enemy_dexterity: int,
        caster_choice: policy.Candidate = CONTINUE,
    ) -> tuple[list[dict], engine.TurnRunner]:
        state.by_id(2).dexterity = enemy_dexterity
        turn_runner, events = runner_on(state, [*INITIATIVE, *faces], BROKEN_PROFILE)
        turn_runner.run(choosing_for({1: caster_choice, 2: enemy_choice}))
        return events, turn_runner

    def lost_action(self, events, caster: CombatantState) -> list[dict]:
        """The logged no-op of a caster whose craft was lost this turn."""
        return [
            event
            for event in events_of_type(events, "info")
            if event["actor"] == caster.name and "lost their craft" in event["message"]
        ]

    def test_a_faster_enemys_hit_takes_the_turns_craft_action(self):
        state = beside()
        caster = state.by_id(1)
        events, turn_runner = self.turn(
            state, ATTACK, [*STRIKES, SNAGS], enemy_dexterity=14
        )
        (lost,) = [event for event in events if "craft_lost" in event["payload"]]
        self.assertEqual(
            (lost["phase"], lost["payload"]["ending"], lost["payload"]["mana_spent"]),
            (5, "interrupted", 2),
        )
        (no_action,) = self.lost_action(events, caster)
        self.assertGreater(no_action["sequence"], lost["sequence"])
        self.assertEqual(craft_actions(events), [])
        # No third share was taken.
        self.assertEqual(caster.mana, 8)
        # The next turn is free to begin a new craft.
        events.clear()
        turn_runner.run(choosing_for({1: craft_candidate("salve", 1, target=1)}))
        (action,) = craft_actions(events)
        self.assertEqual(
            (action["craft"], action["spell"]), ("begun", working_key("salve", 1))
        )
        self.assertEqual(self.lost_action(events, caster), [])

    def test_a_roll_that_holds_leaves_the_turns_craft_action_to_be_taken(self):
        state = beside()
        caster = state.by_id(1)
        events, _turn_runner = self.turn(
            state, ATTACK, [*STRIKES, KEEPS], enemy_dexterity=14
        )
        self.assertEqual([roll["held"] for roll in interruptions(events)], [True])
        (action,) = craft_actions(events)
        self.assertEqual((action["craft"], action["turn"]), ("continued", 3))
        self.assertEqual(self.lost_action(events, caster), [])
        self.assertEqual(standing_craft(caster).turns_done, 3)

    def test_a_slower_enemys_hit_breaks_the_craft_after_its_action(self):
        state = beside()
        caster = state.by_id(1)
        # The share this turn gathered counts against the roll: a 7 would
        # have held the target of 8, and misses the 6 by 1.
        events, turn_runner = self.turn(
            state, ATTACK, [*STRIKES, [3, 4]], enemy_dexterity=10
        )
        (action,) = craft_actions(events)
        (named,) = interruptions(events)
        self.assertEqual(
            (action["turn"], named["gathered"], named["target"]), (3, 3, 6)
        )
        (lost,) = losses(events)
        self.assertEqual((lost["ending"], lost["mana_spent"]), ("interrupted", 3))
        self.assertEqual(self.lost_action(events, caster), [])
        self.assertIsNone(caster.crafting)
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})

    def test_a_faster_enemys_grapple_takes_the_turns_craft_action(self):
        state = beside()
        caster = state.by_id(1)
        events, _turn_runner = self.turn(state, GRAPPLE, [HOLDS], enemy_dexterity=14)
        self.assertEqual([lost["ending"] for lost in losses(events)], ["grappled"])
        self.assertEqual(status_order(events), ["hold", "craft_lost", "drain"])
        self.assertEqual(len(self.lost_action(events, caster)), 1)
        self.assertEqual(craft_actions(events), [])
        self.assertNotIn("concentration", roll_purposes(events))

    def test_a_faster_enemy_closing_to_hand_to_hand_blocks_the_turns_craft_action(
        self,
    ):
        for hands, ending in ((None, ["blocked"]), (WeaponState(), [])):
            with self.subTest(empty_hands=hands is not None):
                state = beside() if hands is None else beside(weapon=hands)
                caster = state.by_id(1)
                events, _turn_runner = self.turn(
                    state, GRAPPLE, [SLIPS], enemy_dexterity=14
                )
                self.assertEqual([lost["ending"] for lost in losses(events)], ending)
                self.assertNotIn("concentration", roll_purposes(events))
                self.assertEqual(caster.crafting is None, hands is None)
                self.assertEqual(len(craft_actions(events)), 0 if hands is None else 1)
