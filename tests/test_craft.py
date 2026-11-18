"""Crafting a working (tarmar-engine #77): the craft option, the craft that
begins, the share each turn gathers, and the ways a craft is lost by its own
caster's choice or purse.

Every number and name here is invented, as in ``test_true_magic``: the "knot"
kind gives a Channel of 3 x the "knotwork" level + 1 and a tier cap of twice
the level, a craft abandoned drains 2 Body per mana gathered, one blocked
drains 4, and one its caster cannot pay drains nothing. A working takes two
craft turns a tier here, so an unpushed one gathers nothing on its odd turns;
most tests craft a tier-2 Scorch pushed by 2, whose 4 mana come in a point a
turn over its 4 turns. The example rules' own three turns a tier serve one
test of a share of nothing. Push and Borrowing are the invented ones of
``test_magic`` and ``test_borrowing``. The tests check the engine's
*structure*: what a craft is refused for, the order its events come in, and
that each injected value is the one used.

Nothing here makes a working go off, and the menu does not offer a craft yet:
each test marks the caster's choice itself, or hands a whole turn a craft
candidate.
"""

import json
from dataclasses import replace
from unittest import TestCase

from tarmar_engine import actions, engine, policy
from tarmar_engine.magic import CHANNEL, CastingEnding, MagicRules, TrueMagicRules
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.state import (
    BODY_POOL,
    BattleState,
    CombatantState,
    CraftState,
    RunawayState,
    WeaponState,
)

from .event_stream_scenarios import BATTLES, RecordedBattle, recording
from .test_borrowing import BACKLASH, SURGE, example_borrowing_rules
from .test_cast_pieces import adjacent_pair, hand_to_hand, held, runner_on
from .test_engine import events_of_type
from .test_event_stream_regression import RECORDED, first_divergence
from .test_magic import (
    MAGIC_PROFILE,
    example_push_rules,
    roll_purposes,
    status_payloads,
    wizard_state,
)
from .test_runaway import example_runaway_rules
from .test_true_cast import drains
from .test_true_magic import (
    KNOT,
    KNOT_KIND,
    KNOT_SKILL,
    example_true_magic_rules,
    true_magic_rules,
)

#: What a craft that is lost drains per mana gathered, by how it ended. One
#: its caster could not pay has no entry, and drains nothing. Not Tarmar's
#: numbers.
LOST_DRAINS = {CastingEnding.ABANDONED: 2, CastingEnding.BLOCKED: 4}
#: The caster's "knotwork" level, the Channel the "knot" kind gives it and
#: its highest tier.
KNOTWORK_LEVEL = 2
KNOT_CHANNEL = 3 * KNOTWORK_LEVEL + 1
TIER_CAP = 2 * KNOTWORK_LEVEL
#: The craft turns a working takes per tier in these tests.
TURNS_PER_TIER = 2
#: The craft most tests make: a tier-2 Scorch pushed by 2, whose 4 mana come
#: in a point a turn over its 4 turns.
STEADY = {"effect": "scorch", "tier": 2, "push": 2}
#: A debt roll (2d8) that holds whatever is owed; one that misses a target
#: of 12 - 2 x 2 by 3, the "Backlash" band; and one on the example rules'
#: automatic total, which lands in "Surge", the band that starts a Runaway.
DEBT_HOLDS = [1, 1]
DEBT_BACKLASH = [6, 5]
DEBT_SURGE = [8, 8]


def craft_true_magic(**changes) -> TrueMagicRules:
    """The example True Magic rules at the tests' pace and lost drains, some
    fields changed."""
    fields = {"turns_per_tier": TURNS_PER_TIER, "lost_drain_per_mana": LOST_DRAINS}
    fields.update(changes)
    return example_true_magic_rules(**fields)


def craft_rules(**true_magic_changes) -> MagicRules:
    """True Magic at two craft turns a tier, with Push and Borrowing beside
    it, some of the True Magic fields changed."""
    return replace(
        true_magic_rules(),
        true_magic=craft_true_magic(**true_magic_changes),
        push=example_push_rules(),
        borrowing=example_borrowing_rules(),
    )


CRAFT_PROFILE = TarmarProfile(magic=craft_rules())
#: The example rules' own pace: three craft turns a tier.
SLOW_PROFILE = TarmarProfile(magic=craft_rules(turns_per_tier=3))


def working_key(effect: str, tier: int) -> str:
    """The key of an example working."""
    return example_true_magic_rules().working_key(effect, tier)


def crafter(**caster_overrides) -> BattleState:
    """The test wizard (INT 14, WIS 12, 10 mana) at "knotwork" 2, and a
    figure well out of reach."""
    fields = {"skill_levels": {KNOT_SKILL: KNOTWORK_LEVEL}}
    fields.update(caster_overrides)
    return wizard_state(**fields)


def crafter_beside(**caster_overrides) -> BattleState:
    """The crafter with a sword in hand and a figure in the next hex."""
    fields = {"skill_levels": {KNOT_SKILL: KNOTWORK_LEVEL}}
    fields.update(caster_overrides)
    return adjacent_pair(**fields)


def choose_craft(
    state: BattleState,
    effect: str,
    tier: int,
    *,
    push: int = 0,
    borrow: bool = False,
    target: int | None = 2,
) -> CombatantState:
    """Mark the caster's choice: a craft action on the working."""
    caster = state.by_id(1)
    caster.chosen_letter = actions.CRAFT_OPTION
    caster.chosen_spell = working_key(effect, tier)
    caster.chosen_push_mana = push
    caster.chosen_borrow = borrow
    caster.chosen_target = target
    return caster


def craft(state: BattleState, profile=CRAFT_PROFILE, faces=()) -> list[dict]:
    """Take the caster's craft action on a runner of its own, as each turn
    has; the events."""
    turn_runner, events = runner_on(state, faces, profile)
    turn_runner.craft_working(state.by_id(1))
    return events


def standing_craft(caster: CombatantState) -> CraftState:
    """The craft standing on a caster, for a test that expects one."""
    craft = caster.crafting
    assert craft is not None, f"{caster.name} has no craft standing"
    return craft


def craft_actions(events) -> list[dict]:
    """The payload of each craft action an event stream logged."""
    return [
        event["payload"]
        for event in events_of_type(events, "action")
        if "craft" in event["payload"]
    ]


def losses(events) -> list[dict]:
    """The payload of each craft an event stream lost."""
    return status_payloads(events, "craft_lost")


def craft_candidate(
    effect: str,
    tier: int,
    *,
    push: int = 0,
    borrow: bool = False,
    target: int | None = 2,
) -> policy.Candidate:
    """A craft action as a turn's choice carries it."""
    return policy.Candidate(
        actions.CRAFT_OPTION,
        actions.option_name(actions.CRAFT_OPTION),
        0.0,
        "",
        target_id=target,
        spell_key=working_key(effect, tier),
        push_mana=push,
        borrow=borrow,
    )


#: Options that stand still, for a figure the test wants out of the way.
DEFEND = policy.Candidate("k", "DEFEND", 0.0, "")
MOVE = policy.Candidate("a", "MOVE", 0.0, "", target_id=2)


def choosing(caster_choice: policy.Candidate):
    """A turn's chooser: the caster takes the choice given, and every other
    figure defends where it stands."""

    def choose_option(state: BattleState, actor) -> policy.Decision:
        chosen = caster_choice if actor.combatant_id == 1 else DEFEND
        return policy.Decision(chosen=chosen, candidates=[chosen])

    return choose_option


def run_turn(state: BattleState, caster_choice, profile=CRAFT_PROFILE) -> list[dict]:
    """Run one whole turn on midline dice; its events."""
    turn_runner, events = runner_on(state, (), profile)
    turn_runner.run(choosing(caster_choice))
    return events


class CraftOptionTest(TestCase):
    def test_it_is_a_magic_action_outside_the_lettered_tables(self):
        self.assertNotIn(actions.CRAFT_OPTION, actions.ALL_OPTIONS)
        self.assertNotIn(actions.CRAFT_OPTION, actions.IMPLEMENTED)
        self.assertEqual(actions.option_name(actions.CRAFT_OPTION), "CRAFT WORKING")
        self.assertEqual(actions.MAGIC_ACTIONS[actions.COUNTER_OPTION], "COUNTER SPELL")

    def test_a_turn_that_chooses_it_stands_still_and_crafts(self):
        state = crafter()
        caster = state.by_id(1)
        before = caster.position
        events = run_turn(state, craft_candidate(**STEADY))
        self.assertEqual(caster.position, before)
        self.assertFalse(caster.moved_this_turn)
        self.assertEqual(events_of_type(events, "movement"), [])
        (action,) = craft_actions(events)
        self.assertEqual(action["craft"], engine.CRAFT_BEGUN)
        (decision,) = [
            event["payload"]
            for event in events_of_type(events, "decision")
            if event["actor"] == caster.name
        ]
        self.assertEqual(decision["chosen"]["letter"], actions.CRAFT_OPTION)
        self.assertEqual(decision["chosen"]["spell_key"], working_key("scorch", 2))
        self.assertEqual(decision["chosen"]["push_mana"], 2)


class CraftBeginsTest(TestCase):
    def test_the_first_action_sets_the_craft_and_pays_the_first_share(self):
        state = crafter()
        caster = choose_craft(state, **STEADY)
        events = craft(state)
        self.assertEqual(
            caster.crafting,
            CraftState(
                spell_key=working_key("scorch", 2),
                push_mana=2,
                borrow=False,
                turns_done=1,
                borrowed=0,
                target_id=2,
            ),
        )
        self.assertEqual(caster.mana, 9)
        (event,) = events
        self.assertEqual(event["event_type"], "action")
        self.assertEqual(
            event["payload"],
            {
                "craft": "begun",
                "spell": "working:scorch:2",
                "tier": 2,
                "turn": 1,
                "turns": 4,
                "share": 1,
                "gathered": 1,
                "mana_total": 4,
                "mana_left": 9,
                "push_mana": 2,
                "channel": KNOT_CHANNEL,
                "channel_kind": KNOT_KIND,
                "borrowed": 0,
                "debt": 0,
            },
        )
        self.assertEqual(
            event["message"],
            "Fighter 1 begins crafting Scorch (tier 2), turn 1 of 4 "
            f"(1 mana, 9 left; 1 of 4 gathered; Channel {KNOT_CHANNEL})",
        )

    def test_the_cost_keys_come_only_with_the_rules_that_bring_them(self):
        state = crafter()
        choose_craft(state, "scorch", 2)
        plain = TarmarProfile(magic=true_magic_rules(turns_per_tier=TURNS_PER_TIER))
        (action,) = craft_actions(craft(state, plain))
        for key in ("push_mana", "borrowed", "debt", "lifted_bounds"):
            self.assertNotIn(key, action)
        self.assertEqual(
            (action["channel"], action["channel_kind"]), (KNOT_CHANNEL, KNOT_KIND)
        )

    def test_the_spells_the_caster_keeps_up_end_as_the_craft_begins(self):
        state = crafter(active_spells=["shield", "fire_missile"])
        caster = choose_craft(state, **STEADY)
        caster.anchored_turns_running = {"shield": 2}
        events = craft(state)
        self.assertEqual(caster.active_spells, [])
        self.assertEqual(caster.anchored_turns_running, {})
        self.assertEqual(
            [event["event_type"] for event in events], ["status", "status", "action"]
        )
        self.assertEqual(
            [event["payload"] for event in events[:2]],
            [
                {"spell": "shield", "ended": True, "crafting": True},
                {"spell": "fire_missile", "ended": True, "crafting": True},
            ],
        )
        self.assertIn("Shield", events[0]["message"])

    def test_a_craft_the_log_refuses_ends_no_spell(self):
        state = crafter(active_spells=["shield"], mana=2)
        caster = choose_craft(state, **STEADY)
        events = craft(state)
        self.assertEqual([event["event_type"] for event in events], ["info"])
        self.assertEqual(caster.active_spells, ["shield"])

    def test_a_granted_working_is_begun_and_its_grant_left_listed(self):
        granted_key = working_key("omen", 2)
        state = crafter(granted_workings=[granted_key])
        caster = choose_craft(state, "omen", 2)
        (action,) = craft_actions(craft(state))
        self.assertEqual(action["spell"], granted_key)
        self.assertEqual(caster.granted_workings, [granted_key])

    def test_a_lifted_channel_lets_the_tier_past_the_cap_and_says_so(self):
        state = crafter(lifted_magic_bounds=[CHANNEL])
        choose_craft(state, "mend", TIER_CAP + 1)
        (action,) = craft_actions(craft(state))
        self.assertEqual(action["tier"], TIER_CAP + 1)
        self.assertEqual(action["lifted_bounds"], [CHANNEL])

    def test_a_pushed_craft_gathers_the_push_with_the_tier(self):
        state = crafter()
        caster = choose_craft(state, "scorch", 1, push=3)
        (action,) = craft_actions(craft(state))
        # 4 mana over 2 turns: 4 x 1 // 2 on the first, where the tier's one
        # point alone would bring nothing.
        self.assertEqual(
            (action["mana_total"], action["share"], action["push_mana"]), (4, 2, 3)
        )
        self.assertEqual(standing_craft(caster).push_mana, 3)
        self.assertEqual(caster.mana, 8)


class CraftRefusedTest(TestCase):
    """A craft the menu never offers is a caller's error: it raises, and
    nothing is changed or logged."""

    def assert_refused(self, state: BattleState, message: str, profile=CRAFT_PROFILE):
        caster = state.by_id(1)
        before = json.dumps(state.to_dict(), sort_keys=True)
        turn_runner, events = runner_on(state, (), profile)
        with self.assertRaisesRegex(ValueError, message):
            turn_runner.craft_working(caster)
        self.assertEqual(json.dumps(state.to_dict(), sort_keys=True), before)
        self.assertEqual(events, [])
        self.assertEqual(turn_runner._crafts_ended, set())

    def test_with_no_true_magic_rules(self):
        for profile in (TARMAR, MAGIC_PROFILE):
            with self.subTest(profile=profile.magic is not None):
                state = crafter(active_spells=["shield"])
                choose_craft(state, "scorch", 2)
                self.assert_refused(state, "needs True Magic rules", profile)

    def test_with_no_level_in_the_true_skill(self):
        for skill_levels in ({}, {KNOT_SKILL: 0}):
            with self.subTest(skill_levels=skill_levels):
                state = crafter(skill_levels=skill_levels)
                choose_craft(state, "scorch", 1)
                self.assert_refused(state, "with no level in Knotwork")

    def test_a_key_that_is_no_working_these_rules_build(self):
        for key in ("fire_missile", "working:gust:1", "working:scorch:0", ""):
            with self.subTest(key=key):
                state = crafter()
                choose_craft(state, "scorch", 2).chosen_spell = key
                self.assert_refused(state, "not a working")

    def test_an_effect_that_needs_a_grant_at_a_key_not_granted(self):
        for grants in ([], [working_key("omen", 1)]):
            with self.subTest(grants=grants):
                state = crafter(granted_workings=grants)
                choose_craft(state, "omen", 2)
                self.assert_refused(state, "Game Master has named it for")

    def test_a_tier_past_the_cap(self):
        state = crafter()
        choose_craft(state, "mend", TIER_CAP + 1)
        self.assert_refused(state, f"highest tier they may craft is {TIER_CAP}")

    def test_an_error_is_raised_before_the_log_refuses_anything(self):
        # Each caster is somewhere the log would refuse a craft, and chooses
        # one past its tier cap: the error comes first, and nothing is logged.
        refused_in_the_log = {
            "held in a grapple": held(crafter_beside()),
            "in hand-to-hand, weapon in hand": hand_to_hand(crafter_beside()),
            "short of the mana": crafter(mana=1),
        }
        for situation, state in refused_in_the_log.items():
            with self.subTest(situation=situation):
                choose_craft(state, "mend", TIER_CAP + 1)
                self.assert_refused(state, f"highest tier they may craft is {TIER_CAP}")

    def test_the_debt_cap_is_held_before_the_log_refuses_anything(self):
        # The last check a craft's beginning makes still comes before either
        # thing the log refuses. Held in a grapple, 1 mana and 3 owed: a
        # tier-3 working borrows 2, past the example debt cap of 4.
        state = held(crafter_beside(mana=1, debt=3))
        choose_craft(state, "scorch", 3, borrow=True)
        self.assert_refused(state, "borrows 2 mana.*owing 3; the debt cap is 4")
        # Free of the hold and owing nothing, the same craft begins.
        state = crafter_beside(mana=1)
        choose_craft(state, "scorch", 3, borrow=True)
        (action,) = craft_actions(craft(state))
        self.assertEqual(action["craft"], engine.CRAFT_BEGUN)

    def test_a_push_the_rules_do_not_allow(self):
        no_push = TarmarProfile(magic=replace(craft_rules(), push=None))
        cases = (
            ("no Push rules", "scorch", 1, no_push),
            # A narrated working has no rolled effect for a push to add to.
            ("no push effect", "hush", 1, CRAFT_PROFILE),
            # The example Push cap is 3.
            ("the cap is 3", "scorch", 4, CRAFT_PROFILE),
            ("cannot push -1 mana", "scorch", -1, CRAFT_PROFILE),
        )
        for message, effect, push, profile in cases:
            with self.subTest(message=message):
                state = crafter()
                choose_craft(state, effect, 1, push=push)
                self.assert_refused(state, message, profile)

    def test_whole_mana_past_channel_by_the_true_kind(self):
        # "knotwork" 1 under five tiers a level: tier 5 is within the cap,
        # and its 5 mana is past a Channel of 3 x 1 + 1.
        profile = TarmarProfile(magic=craft_rules(tiers_per_level=5))
        state = crafter(skill_levels={KNOT_SKILL: 1})
        choose_craft(state, "mend", 5)
        self.assert_refused(
            state, r"crafts Mend \(tier 5\) with 5 mana.*Channel for it is 4", profile
        )
        # Tier 2 and a push of 3 make the same 5 mana.
        choose_craft(state, "mend", 2, push=3)
        self.assert_refused(state, "with 5 mana.*Channel for it is 4", profile)

    def test_a_borrowing_the_rules_do_not_allow(self):
        no_borrowing = TarmarProfile(magic=replace(craft_rules(), borrowing=None))
        state = crafter(mana=1)
        choose_craft(state, "scorch", 3, borrow=True)
        self.assert_refused(state, "no Borrowing rules", no_borrowing)
        # The example debt cap is 4: 3 owed, and 2 more to cover the working.
        state = crafter(mana=1, debt=3)
        choose_craft(state, "scorch", 3, borrow=True)
        self.assert_refused(state, "borrows 2 mana.*owing 3; the debt cap is 4")


class CraftNotBegunTest(TestCase):
    """A craft its caster may not begin where it stands, or cannot pay for,
    is refused in the log, as a cast is: no error, and no craft."""

    def assert_not_begun(self, state: BattleState, message: str) -> None:
        caster = state.by_id(1)
        mana = caster.mana
        turn_runner, events = runner_on(state, (), CRAFT_PROFILE)
        turn_runner.craft_working(caster)
        self.assertEqual([event["event_type"] for event in events], ["info"])
        self.assertIn(message, events[0]["message"])
        self.assertIsNone(caster.crafting)
        self.assertEqual(caster.mana, mana)
        # No craft was lost: none had begun.
        self.assertEqual(turn_runner._crafts_ended, set())

    def test_in_a_grapple(self):
        state = held(crafter_beside())
        choose_craft(state, **STEADY)
        self.assert_not_begun(state, "locked in a grapple and cannot cast Scorch")

    def test_in_hand_to_hand_with_a_weapon_in_hand(self):
        state = hand_to_hand(crafter_beside())
        choose_craft(state, **STEADY)
        self.assert_not_begun(state, "in hand-to-hand with the Broadsword in hand")

    def test_in_hand_to_hand_with_empty_hands_it_begins(self):
        state = hand_to_hand(crafter_beside(weapon=WeaponState()))
        choose_craft(state, **STEADY)
        (action,) = craft_actions(craft(state))
        self.assertEqual(action["craft"], engine.CRAFT_BEGUN)

    def test_a_pool_that_cannot_cover_the_whole_working(self):
        # 2 mana would pay every share of the three points but the last.
        state = crafter(mana=2)
        choose_craft(state, "scorch", 3)
        self.assert_not_begun(state, "lacks the mana for Scorch (tier 3)")
        choose_craft(state, "scorch", 1, push=2)
        self.assert_not_begun(state, "Scorch (tier 1) pushed with 2 more")

    def test_a_caster_whose_craft_was_lost_this_turn_begins_no_other(self):
        state = crafter()
        caster = choose_craft(state, **STEADY)
        turn_runner, events = runner_on(state, (), CRAFT_PROFILE)
        turn_runner._crafts_ended.add(caster.combatant_id)
        turn_runner.craft_working(caster)
        self.assertEqual([event["event_type"] for event in events], ["info"])
        self.assertIn("lost their craft this turn", events[0]["message"])
        self.assertIsNone(caster.crafting)
        self.assertEqual(caster.mana, 10)
        # The loss took the action whatever was chosen: a craft that would
        # be an error on any other turn raises nothing here.
        for effect, tier in (("mend", TIER_CAP + 1), ("omen", 1)):
            with self.subTest(effect=effect, tier=tier):
                choose_craft(state, effect, tier)
                events.clear()
                turn_runner.craft_working(caster)
                self.assertEqual([event["event_type"] for event in events], ["info"])
        caster.chosen_spell = "fire_missile"
        events.clear()
        turn_runner.craft_working(caster)
        self.assertEqual([event["event_type"] for event in events], ["info"])
        self.assertIsNone(caster.crafting)
        # The other figure lost none, and crafts.
        other = state.by_id(2)
        other.skill_levels = {KNOT_SKILL: 1}
        other.mana = 5
        other.chosen_spell = working_key("mend", 1)
        turn_runner.craft_working(other)
        self.assertIsNotNone(other.crafting)


class SharesTest(TestCase):
    """Each craft action gathers one share of the working's whole mana."""

    def turns(self, state: BattleState, count: int, profile=CRAFT_PROFILE):
        """Take that many craft actions, a turn each; every action logged."""
        logged: list[dict] = []
        for _turn in range(count):
            events = craft(state, profile)
            self.assertEqual(len(events), 1)
            logged += craft_actions(events)
        return logged

    def test_each_turn_pays_its_share(self):
        state = crafter()
        caster = choose_craft(state, **STEADY)
        logged = self.turns(state, 4)
        self.assertEqual(
            [
                (turn["craft"], turn["turn"], turn["share"], turn["gathered"])
                for turn in logged
            ],
            [
                ("begun", 1, 1, 1),
                ("continued", 2, 1, 2),
                ("continued", 3, 1, 3),
                ("continued", 4, 1, 4),
            ],
        )
        self.assertEqual([turn["mana_left"] for turn in logged], [9, 8, 7, 6])
        self.assertEqual(caster.mana, 6)
        for turn in logged:
            self.assertEqual((turn["turns"], turn["mana_total"]), (4, 4))

    def test_uneven_shares_round_each_turn_down(self):
        # Tier 2 pushed by 3: 5 mana over 4 turns is 1, 1, 1, then 2.
        state = crafter()
        caster = choose_craft(state, "scorch", 2, push=3)
        logged = self.turns(state, 4)
        self.assertEqual([turn["share"] for turn in logged], [1, 1, 1, 2])
        self.assertEqual(caster.mana, 5)

    def test_an_unpushed_working_gathers_on_every_other_turn(self):
        # Tier 3: 3 mana over 6 turns.
        state = crafter()
        caster = choose_craft(state, "scorch", 3)
        logged = self.turns(state, 6)
        self.assertEqual([turn["share"] for turn in logged], [0, 1, 0, 1, 0, 1])
        self.assertEqual([turn["mana_left"] for turn in logged], [10, 9, 9, 8, 8, 7])
        self.assertEqual(caster.mana, 7)

    def test_the_last_turn_leaves_the_craft_standing_whole(self):
        state = crafter()
        caster = choose_craft(state, "scorch", 1, push=1)
        self.turns(state, 2)
        self.assertEqual(standing_craft(caster).turns_done, 2)
        self.assertEqual(craft_true_magic().gathered(standing_craft(caster)), 2)
        # No share is left to gather: a further craft action is refused.
        before = json.dumps(state.to_dict(), sort_keys=True)
        with self.assertRaisesRegex(ValueError, "2 turns cannot have taken 3"):
            craft(state)
        self.assertEqual(json.dumps(state.to_dict(), sort_keys=True), before)

    def test_a_turn_with_nothing_due_is_logged_and_takes_nothing(self):
        # Three turns a tier: tier 2's 2 mana comes in on turns 3 and 6.
        state = crafter()
        caster = choose_craft(state, "mend", 2)
        logged: list[dict] = []
        messages: list[str] = []
        for _turn in range(6):
            events = craft(state, SLOW_PROFILE)
            # One event a turn: a share of nothing logs no payment of its own.
            self.assertEqual([event["event_type"] for event in events], ["action"])
            logged.append(events[0]["payload"])
            messages.append(events[0]["message"])
        self.assertEqual([turn["share"] for turn in logged], [0, 0, 1, 0, 0, 1])
        self.assertEqual([turn["gathered"] for turn in logged], [0, 0, 1, 1, 1, 2])
        self.assertEqual([turn["mana_left"] for turn in logged], [10, 10, 9, 9, 9, 8])
        self.assertEqual([turn["turn"] for turn in logged], [1, 2, 3, 4, 5, 6])
        self.assertEqual(logged[0]["craft"], engine.CRAFT_BEGUN)
        for turn, message in enumerate(messages):
            with self.subTest(turn=turn + 1):
                self.assertNotIn("(0 mana", message)
                self.assertEqual("no mana due" in message, logged[turn]["share"] == 0)
        self.assertEqual(standing_craft(caster).turns_done, 6)

    def test_a_share_the_pool_cannot_pay_is_borrowed_by_a_craft_that_may(self):
        # Tier 1 pushed by 3: 4 mana over 2 turns from a purse of 3. The
        # first share of 2 is paid; the second takes the 1 left and borrows 1.
        state = crafter(mana=3)
        caster = choose_craft(state, "scorch", 1, push=3, borrow=True)
        first_events = craft(state)
        second_events = craft(state)
        (first,) = craft_actions(first_events)
        (second,) = craft_actions(second_events)
        self.assertEqual(
            (first["share"], first["mana_left"], first["borrowed"], first["debt"]),
            (2, 1, 0, 0),
        )
        self.assertEqual(
            (second["share"], second["mana_left"], second["borrowed"], second["debt"]),
            (2, 0, 1, 1),
        )
        self.assertIn("(2 mana, 1 left;", first_events[0]["message"])
        self.assertIn(
            "(2 mana, 0 left, borrows 1, debt 1;", second_events[0]["message"]
        )
        self.assertEqual((caster.mana, caster.debt), (0, 1))
        self.assertEqual(standing_craft(caster).borrowed, 1)
        self.assertTrue(standing_craft(caster).borrow)

    def test_a_craft_that_may_borrow_pays_from_the_pool_while_it_can(self):
        state = crafter()
        caster = choose_craft(state, **STEADY, borrow=True)
        logged = self.turns(state, 4)
        self.assertEqual([turn["borrowed"] for turn in logged], [0, 0, 0, 0])
        self.assertEqual(
            (caster.mana, caster.debt, standing_craft(caster).borrowed), (6, 0, 0)
        )

    def test_a_share_the_pool_cannot_pay_loses_a_craft_that_may_not_borrow(self):
        state = crafter()
        caster = choose_craft(state, **STEADY)
        self.turns(state, 2)
        standing = standing_craft(caster)
        # The purse is emptied between turns, by whatever else spends mana.
        caster.mana = 0
        turn_runner, events = runner_on(state, (), CRAFT_PROFILE)
        turn_runner.craft_working(caster)
        self.assertEqual(craft_actions(events), [])
        (lost,) = losses(events)
        self.assertEqual(
            lost,
            {
                "craft_lost": True,
                "ending": "unpaid",
                "spell": working_key("scorch", 2),
                # What the two earlier turns gathered.
                "mana_spent": 2,
                "drain": 0,
            },
        )
        self.assertEqual((caster.mana, caster.debt), (0, 0))
        self.assertEqual((standing.turns_done, standing.borrowed), (2, 0))
        self.assertIsNone(caster.crafting)
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})
        # The rules name no drain for this ending: one event, and no cost.
        self.assertEqual([event["event_type"] for event in events], ["status"])
        self.assertEqual(drains(events), [])

    def test_a_share_partly_covered_takes_nothing_from_a_craft_that_may_not_borrow(
        self,
    ):
        # Tier 1 pushed by 3 gathers 2 a turn; 1 point is left for the second.
        state = crafter()
        caster = choose_craft(state, "scorch", 1, push=3)
        self.turns(state, 1)
        caster.mana = 1
        events = craft(state)
        (lost,) = losses(events)
        self.assertEqual((lost["ending"], lost["mana_spent"]), ("unpaid", 2))
        self.assertEqual((caster.mana, caster.debt), (1, 0))


class ContinuationTest(TestCase):
    def test_it_carries_the_craft_on_as_it_began_and_names_the_target_anew(self):
        state = crafter()
        caster = choose_craft(state, **STEADY, target=2)
        craft(state)
        # The choice a craft action reads is its target; the working, the
        # push and the borrowing are the craft's own.
        choose_craft(state, "mend", 1, push=1, borrow=True, target=1)
        (action,) = craft_actions(craft(state))
        self.assertEqual(
            (action["craft"], action["spell"], action["push_mana"], action["turn"]),
            ("continued", working_key("scorch", 2), 2, 2),
        )
        self.assertEqual(
            caster.crafting,
            CraftState(
                spell_key=working_key("scorch", 2),
                push_mana=2,
                borrow=False,
                turns_done=2,
                borrowed=0,
                target_id=1,
            ),
        )
        choose_craft(state, **STEADY, target=None)
        craft(state)
        self.assertIsNone(standing_craft(caster).target_id)

    def test_it_ends_no_spell_and_asks_for_no_grant_again(self):
        granted_key = working_key("omen", 2)
        state = crafter(granted_workings=[granted_key])
        caster = choose_craft(state, "omen", 2)
        craft(state)
        # The grant is withdrawn mid-craft, and a spell is somehow up: only
        # a craft's beginning asks after either.
        caster.granted_workings = []
        caster.active_spells = ["shield"]
        events = craft(state)
        self.assertEqual([event["event_type"] for event in events], ["action"])
        self.assertEqual(caster.active_spells, ["shield"])
        self.assertEqual(standing_craft(caster).turns_done, 2)

    def test_where_no_cast_may_be_made_the_working_is_lost(self):
        state = crafter_beside()
        caster = choose_craft(state, **STEADY)
        craft(state)
        hand_to_hand(state)
        turn_runner, events = runner_on(state, (), CRAFT_PROFILE)
        turn_runner.craft_working(caster)
        self.assertEqual(
            [event["event_type"] for event in events], ["info", "status", "status"]
        )
        self.assertIn("in hand-to-hand with the Broadsword", events[0]["message"])
        (lost,) = losses(events)
        self.assertEqual(
            (lost["ending"], lost["mana_spent"], lost["drain"]),
            ("blocked", 1, LOST_DRAINS[CastingEnding.BLOCKED]),
        )
        self.assertEqual(
            drains(events),
            [{BODY_POOL: 27 - 4, "cost": 4, "reason": engine.DRAIN_REASON}],
        )
        self.assertIsNone(caster.crafting)
        self.assertEqual(caster.mana, 9)
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})

    def test_a_grapple_blocks_it_as_hand_to_hand_does(self):
        state = crafter_beside(weapon=WeaponState())
        caster = choose_craft(state, **STEADY)
        craft(state)
        events = craft(held(state))
        self.assertIn("locked in a grapple", events[0]["message"])
        self.assertEqual([lost["ending"] for lost in losses(events)], ["blocked"])
        self.assertIsNone(caster.crafting)

    def test_with_empty_hands_hand_to_hand_blocks_nothing(self):
        state = crafter_beside(weapon=WeaponState())
        caster = choose_craft(state, **STEADY)
        craft(state)
        (action,) = craft_actions(craft(hand_to_hand(state)))
        self.assertEqual(action["craft"], engine.CRAFT_CONTINUED)
        self.assertEqual(standing_craft(caster).turns_done, 2)


class LoseWorkingTest(TestCase):
    """The one way a craft ends early, asked what only it can answer."""

    def begun(
        self, turns: int = 2, *, borrow: bool = False, **caster_overrides
    ) -> BattleState:
        """The crafter some turns into the steady Scorch, a point gathered
        each turn, which its pool can pay for."""
        state = crafter(**caster_overrides)
        choose_craft(state, **STEADY, borrow=borrow)
        for _turn in range(turns):
            craft(state)
        return state

    def lose(
        self, state, ending, faces=(), profile=CRAFT_PROFILE, **keywords
    ) -> tuple[list[dict], set[int]]:
        turn_runner, events = runner_on(state, faces, profile)
        turn_runner._lose_working(state.by_id(1), ending, **keywords)
        return events, turn_runner._crafts_ended

    def test_the_craft_is_gone_the_loss_is_logged_and_the_drain_is_taken(self):
        state = self.begun()
        caster = state.by_id(1)
        events, crafts_ended = self.lose(state, CastingEnding.ABANDONED)
        self.assertIsNone(caster.crafting)
        self.assertEqual(crafts_ended, {caster.combatant_id})
        self.assertEqual(
            [event["event_type"] for event in events], ["status", "status"]
        )
        self.assertEqual(
            events[0]["payload"],
            {
                "craft_lost": True,
                "ending": "abandoned",
                "spell": working_key("scorch", 2),
                "mana_spent": 2,
                "drain": 2 * LOST_DRAINS[CastingEnding.ABANDONED],
            },
        )
        self.assertIn("Scorch (tier 2) is lost", events[0]["message"])
        self.assertEqual(
            drains(events),
            [{BODY_POOL: 27 - 4, "cost": 4, "reason": engine.DRAIN_REASON}],
        )
        self.assertEqual(caster.body, 23)
        # The mana gathered is spent, not handed back.
        self.assertEqual(caster.mana, 8)

    def test_the_drain_is_the_endings_own_and_counts_the_mana_gathered(self):
        for turns, ending, drained in (
            (1, CastingEnding.ABANDONED, 2),
            (3, CastingEnding.ABANDONED, 6),
            (3, CastingEnding.BLOCKED, 12),
        ):
            with self.subTest(turns=turns, ending=ending):
                state = self.begun(turns)
                events, _crafts_ended = self.lose(state, ending)
                self.assertEqual(losses(events)[0]["drain"], drained)
                self.assertEqual([drain["cost"] for drain in drains(events)], [drained])

    def test_an_ending_the_rules_name_no_drain_for_logs_no_cost(self):
        state = self.begun()
        events, _crafts_ended = self.lose(state, CastingEnding.UNPAID)
        self.assertEqual([event["event_type"] for event in events], ["status"])
        self.assertEqual(losses(events)[0]["drain"], 0)
        self.assertEqual(state.by_id(1).body, 27)
        for event in events:
            self.assertNotIn("spends 0", event["message"])

    def test_a_craft_that_gathered_nothing_spends_and_drains_nothing(self):
        # Tier 1's one point comes in on its second turn.
        state = crafter()
        choose_craft(state, "scorch", 1)
        craft(state)
        events, _crafts_ended = self.lose(state, CastingEnding.ABANDONED)
        self.assertEqual([event["event_type"] for event in events], ["status"])
        self.assertEqual(
            (losses(events)[0]["mana_spent"], losses(events)[0]["drain"]), (0, 0)
        )

    def borrowed(self, **caster_overrides) -> BattleState:
        """The crafter, 1 mana to its name, two turns into the steady
        Scorch: one share paid and one borrowed."""
        state = crafter(mana=1, **caster_overrides)
        choose_craft(state, **STEADY, borrow=True)
        craft(state)
        craft(state)
        self.assertEqual(standing_craft(state.by_id(1)).borrowed, 1)
        return state

    def test_a_craft_that_borrowed_makes_its_debt_roll_before_the_drain(self):
        state = self.borrowed()
        caster = state.by_id(1)
        events, _crafts_ended = self.lose(state, CastingEnding.ABANDONED, [DEBT_HOLDS])
        self.assertEqual(roll_purposes(events), ["debt"])
        (debt_roll,) = status_payloads(events, "debt_roll")
        self.assertEqual(
            (debt_roll["spell"], debt_roll["debt"], debt_roll["held"]),
            (working_key("scorch", 2), 1, True),
        )
        order = [
            "craft_lost"
            if "craft_lost" in event["payload"]
            else "debt_roll"
            if "debt_roll" in event["payload"]
            else "drain"
            for event in events_of_type(events, "status")
        ]
        self.assertEqual(order, ["craft_lost", "debt_roll", "drain"])
        # The debt stands: the roll is made on it, not paid by it.
        self.assertEqual(caster.debt, 1)

    def test_a_failed_debt_roll_costs_its_band_and_the_drain_still_follows(self):
        state = self.borrowed(debt=1)
        caster = state.by_id(1)
        self.assertEqual(caster.debt, 2)
        events, _crafts_ended = self.lose(
            state, CastingEnding.ABANDONED, [DEBT_BACKLASH]
        )
        (failure,) = status_payloads(events, "debt_failure")
        self.assertEqual(failure["debt_failure"], BACKLASH.key)
        # "Backlash" costs 2 Fatigue per point owed; the drain, 2 Body per
        # mana gathered.
        self.assertEqual(caster.fatigue, 40 - 2 * 2)
        self.assertEqual(caster.body, 27 - 2 * 2)

    def test_a_debt_band_that_starts_a_runaway_starts_it_at_the_mana_gathered(self):
        # 2 of the working's 4 mana gathered, 1 of them borrowed: the Runaway
        # the band starts feeds on the 2, not the 4 and not the 1 owed.
        with_runaway = TarmarProfile(
            magic=replace(craft_rules(), runaway=example_runaway_rules())
        )
        state = self.borrowed()
        caster = state.by_id(1)
        events, _crafts_ended = self.lose(
            state, CastingEnding.ABANDONED, [DEBT_SURGE], with_runaway
        )
        (failure,) = status_payloads(events, "debt_failure")
        self.assertEqual(
            (failure["debt_failure"], failure["runaway"]), (SURGE.key, True)
        )
        (begun,) = status_payloads(events, "runaway_begins")
        self.assertEqual(
            (begun["spell"], begun["original_cost"]), (working_key("scorch", 2), 2)
        )
        self.assertEqual(
            caster.runaway,
            RunawayState(spell_key=working_key("scorch", 2), original_cost=2),
        )
        self.assertIsNone(caster.crafting)
        # The same band with no Runaway rules injected starts nothing.
        state = self.borrowed()
        events, _crafts_ended = self.lose(state, CastingEnding.ABANDONED, [DEBT_SURGE])
        self.assertEqual(status_payloads(events, "runaway_begins"), [])
        self.assertIsNone(state.by_id(1).runaway)

    def test_a_craft_that_borrowed_nothing_makes_no_debt_roll(self):
        # It may borrow, and its caster owes from before: no share of its
        # own was borrowed.
        state = self.begun(borrow=True, debt=3)
        self.assertTrue(standing_craft(state.by_id(1)).borrow)
        events, _crafts_ended = self.lose(state, CastingEnding.ABANDONED)
        self.assertEqual(roll_purposes(events), [])

    def test_a_caster_no_longer_active_rolls_no_debt_and_is_still_drained(self):
        state = self.borrowed()
        caster = state.by_id(1)
        caster.conscious = False
        events, _crafts_ended = self.lose(state, CastingEnding.ABANDONED)
        self.assertEqual(roll_purposes(events), [])
        self.assertEqual(caster.debt, 1)
        self.assertEqual([drain["cost"] for drain in drains(events)], [4])

    def test_a_drain_that_fells_the_caster_loses_nothing_twice(self):
        state = self.begun(body=3)
        caster = state.by_id(1)
        events, crafts_ended = self.lose(state, CastingEnding.ABANDONED)
        self.assertFalse(caster.conscious)
        self.assertEqual(len(losses(events)), 1)
        self.assertEqual(len(drains(events)), 1)
        self.assertEqual(crafts_ended, {caster.combatant_id})
        self.assertEqual(
            [event["payload"].get("unconscious") for event in events][-1], True
        )

    def test_a_craft_already_taken_off_the_caster_is_lost_as_given(self):
        state = self.begun()
        caster = state.by_id(1)
        taken = caster.crafting
        caster.crafting = None
        events, crafts_ended = self.lose(state, CastingEnding.BLOCKED, craft=taken)
        self.assertEqual(
            (losses(events)[0]["mana_spent"], losses(events)[0]["drain"]), (2, 8)
        )
        self.assertIsNone(caster.crafting)
        self.assertEqual(crafts_ended, {caster.combatant_id})

    def test_a_caster_with_no_craft_has_none_to_lose(self):
        state = crafter()
        turn_runner, events = runner_on(state, (), CRAFT_PROFILE)
        with self.assertRaisesRegex(ValueError, "no craft to lose"):
            turn_runner._lose_working(state.by_id(1), CastingEnding.ABANDONED)
        self.assertEqual(events, [])
        self.assertEqual(turn_runner._crafts_ended, set())


class ChoiceWhileCraftingTest(TestCase):
    """Initial Movement holds a crafting caster's choice to its craft."""

    def crafting(self, **caster_overrides) -> BattleState:
        """The crafter after the turn that begins the steady Scorch."""
        state = crafter(**caster_overrides)
        run_turn(state, craft_candidate(**STEADY))
        self.assertEqual(standing_craft(state.by_id(1)).turns_done, 1)
        return state

    def test_the_craft_option_again_carries_the_craft_on(self):
        state = self.crafting()
        caster = state.by_id(1)
        events = run_turn(state, craft_candidate(**STEADY, target=None))
        self.assertEqual(losses(events), [])
        (action,) = craft_actions(events)
        self.assertEqual((action["craft"], action["turn"]), ("continued", 2))
        self.assertEqual(standing_craft(caster).turns_done, 2)
        self.assertIsNone(standing_craft(caster).target_id)

    def test_any_other_option_abandons_it_as_the_choice_is_recorded(self):
        state = self.crafting()
        caster = state.by_id(1)
        events = run_turn(state, DEFEND)
        (lost_event,) = [
            event
            for event in events_of_type(events, "status")
            if "craft_lost" in event["payload"]
        ]
        self.assertEqual(lost_event["phase"], 3)
        self.assertEqual(
            (lost_event["payload"]["ending"], lost_event["payload"]["mana_spent"]),
            ("abandoned", 1),
        )
        # It follows the caster's own decision, and its drain is taken there.
        (decision,) = [
            event
            for event in events_of_type(events, "decision")
            if event["actor"] == caster.name
        ]
        self.assertEqual(lost_event["sequence"], decision["sequence"] + 1)
        (drain_event,) = [
            event
            for event in events_of_type(events, "status")
            if event["payload"].get("reason") == engine.DRAIN_REASON
        ]
        self.assertEqual((drain_event["phase"], drain_event["payload"]["cost"]), (3, 2))
        self.assertIsNone(caster.crafting)
        self.assertEqual(craft_actions(events), [])
        # The action chosen instead is taken.
        self.assertTrue(caster.defending)
        self.assertEqual(caster.mana, 9)

    def test_a_craft_option_that_differs_from_the_craft_is_refused(self):
        differing = {
            "another working": craft_candidate("mend", 2, push=2),
            "another tier": craft_candidate("scorch", 3, push=2),
            "another push": craft_candidate("scorch", 2, push=1),
            "another borrowing": craft_candidate(**STEADY, borrow=True),
        }
        for difference, candidate in differing.items():
            with self.subTest(difference=difference):
                state = self.crafting()
                caster = state.by_id(1)
                standing = replace(standing_craft(caster))
                with self.assertRaisesRegex(ValueError, "carried on as it began"):
                    run_turn(state, candidate)
                self.assertEqual(caster.crafting, standing)
                self.assertEqual(caster.mana, 9)

    def test_a_caster_felled_by_what_the_abandoned_craft_costs_does_nothing_more(
        self,
    ):
        # 2 Body left, and the craft abandoned drains 2.
        state = self.crafting(body=2)
        caster = state.by_id(1)
        before = caster.position
        events = run_turn(state, MOVE)
        self.assertFalse(caster.conscious)
        self.assertEqual(caster.position, before)
        self.assertEqual(events_of_type(events, "movement"), [])
        self.assertEqual(len(losses(events)), 1)
        # A caster who stays up makes the move it chose.
        state = self.crafting()
        events = run_turn(state, MOVE)
        self.assertNotEqual(state.by_id(1).position, before)

    def test_a_caster_with_no_craft_standing_is_held_to_nothing(self):
        state = crafter()
        events = run_turn(state, DEFEND)
        self.assertEqual(losses(events), [])
        self.assertTrue(state.by_id(1).defending)


class CraftsEndedResetTest(TestCase):
    """The record of who lost a craft is the whole turn's: it is cleared when
    a turn starts, and by nothing later in it."""

    def test_a_runner_run_again_starts_the_turn_with_no_craft_lost(self):
        state = crafter()
        caster = state.by_id(1)
        turn_runner, events = runner_on(state, (), CRAFT_PROFILE)
        turn_runner.run(choosing(craft_candidate(**STEADY)))
        self.assertEqual(turn_runner._crafts_ended, set())
        turn_runner.run(choosing(DEFEND))
        # Lost in Initial Movement, and still on record after the Actions
        # phase and the turn's end.
        self.assertEqual(turn_runner._crafts_ended, {caster.combatant_id})
        events.clear()
        turn_runner.run(choosing(craft_candidate("mend", 2)))
        # The new turn begins a new craft: the last turn's loss is no bar.
        (action,) = craft_actions(events)
        self.assertEqual(
            (action["craft"], action["spell"]), ("begun", working_key("mend", 2))
        )
        self.assertEqual(
            [
                event
                for event in events_of_type(events, "info")
                if event["actor"] == caster.name
            ],
            [],
        )
        self.assertEqual(turn_runner._crafts_ended, set())


class NoCraftNoChangeTest(TestCase):
    """A battle in which nobody crafts logs what it logged on v0.9.21, event
    for event. ``test_event_stream_regression`` holds every recorded battle
    to that with no True Magic rules; this holds the armed ones to it with
    True Magic rules injected that no figure has a level to use."""

    @staticmethod
    def with_true_magic(battle: RecordedBattle) -> RecordedBattle:
        """The battle under its own rules and the example True Magic rules,
        the "knot" kind added to its Channel derivations."""
        magic = battle.magic
        assert magic is not None and magic.channel is not None
        channel = replace(
            magic.channel, derivations={**magic.channel.derivations, KNOT_KIND: KNOT}
        )
        return replace(
            battle,
            magic=replace(
                magic, channel=channel, true_magic=example_true_magic_rules()
            ),
        )

    def test_true_magic_rules_nobody_can_use_change_no_recorded_battle(self):
        armed = [battle for battle in BATTLES if battle.magic is not None]
        self.assertGreater(len(armed), 10)
        for battle in armed:
            with self.subTest(battle=battle.key):
                self.assertNotEqual(self.with_true_magic(battle).magic, battle.magic)
                self.assertIsNone(
                    first_divergence(
                        RECORDED[battle.key], recording(self.with_true_magic(battle))
                    )
                )

    def test_the_same_rules_let_a_figure_with_a_level_craft_in_such_a_battle(self):
        # The rules injected above are live: nothing but the level is wanting.
        battle = self.with_true_magic(
            next(battle for battle in BATTLES if battle.magic is not None)
        )
        state = battle.build()
        state.by_id(1).skill_levels[KNOT_SKILL] = KNOTWORK_LEVEL
        events = run_turn(
            state, craft_candidate("mend", 1), TarmarProfile(magic=battle.magic)
        )
        (action,) = craft_actions(events)
        self.assertEqual(action["craft"], engine.CRAFT_BEGUN)
        self.assertIsNotNone(state.by_id(1).crafting)
