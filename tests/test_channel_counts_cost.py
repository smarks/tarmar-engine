"""Probe tests for a kind of magic that leaves the cost out of Channel
(tarmar-engine #83, following smarks/tarmar-studio#196).

Every number here is invented, as in ``test_channel``: the "loom" kind's
derivation (2 x the level - 1) gives a caster at level 1 in Lightning Bolt
(cost 3 in the engine's catalog) a Channel of 1. Counting the cost, that
caster cannot cast it at all; leaving the cost out, the cost always fits and
Channel bounds the mana past it, pushed or borrowed, so the bolt casts for 3
and may take 1 more.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine import policy
from tarmar_engine.magic import CHANNEL, MagicRules
from tarmar_engine.profile import TarmarProfile
from tarmar_engine.spells import get_spell
from tarmar_engine.state import BattleState

from .test_borrowing import borrow_cast, example_borrowing_rules
from .test_channel import LOOM, channel_caster, example_channel_rules
from .test_magic import cast, example_magic_rules
from .test_true_magic import (
    KNOT_KIND,
    KNOT_SKILL,
    example_true_magic_rules,
    knot_channel_rules,
)

BOLT = "lightning_bolt"
#: The "loom" derivation with the cost left out of Channel.
LOOM_WITHOUT_COST = replace(LOOM, counts_cost=False)


def magic_rules(*, counts_cost: bool, **borrowing_changes) -> MagicRules:
    """The example magic rules with Channel and Borrowing (no debt cap
    unless changed), "loom" counting the cost or leaving it out."""
    channel = example_channel_rules()
    loom = LOOM if counts_cost else LOOM_WITHOUT_COST
    return replace(
        example_magic_rules(),
        channel=replace(channel, derivations={**channel.derivations, "loom": loom}),
        borrowing=example_borrowing_rules(**{"max_debt": None, **borrowing_changes}),
    )


def bolt_caster(mana: int, **overrides) -> BattleState:
    """The test wizard at level 1 in Lightning Bolt: Channel 1 by "loom"."""
    return channel_caster({BOLT: 1}, spells=[BOLT], mana=mana, **overrides)


def bolt_candidates(state: BattleState, rules: MagicRules) -> list[policy.Candidate]:
    decision = policy.choose_option(state, state.by_id(1), magic=rules)
    return [candidate for candidate in decision.candidates if candidate.spell_key]


def offered(state: BattleState, rules: MagicRules) -> set[tuple[int, bool]]:
    """The (push, borrow) pairs the menu offers a Lightning Bolt with."""
    return {
        (candidate.push_mana, candidate.borrow)
        for candidate in bolt_candidates(state, rules)
    }


def engine_accepts(
    state: BattleState, rules: MagicRules, push: int, *, borrow: bool
) -> bool:
    """Does the casting phase take this Lightning Bolt up, or refuse it?"""
    profile = TarmarProfile(magic=rules)
    try:
        if borrow:
            borrow_cast(state, [], profile=profile, spell=BOLT, push=push)
        else:
            cast(state, [], profile=profile, spell=BOLT, push=push)
    except ValueError:
        return False
    return True


def refusal(
    state: BattleState, rules: MagicRules, push: int, *, borrow: bool = False
) -> str:
    profile = TarmarProfile(magic=rules)
    with TestCase().assertRaises(ValueError) as raised:
        if borrow:
            borrow_cast(state, [], profile=profile, spell=BOLT, push=push)
        else:
            cast(state, [], profile=profile, spell=BOLT, push=push)
    return str(raised.exception)


class CountedManaTest(TestCase):
    def test_the_flag_says_what_a_casting_puts_against_channel(self):
        bolt = get_spell(BOLT)
        counting = magic_rules(counts_cost=True).channel
        leaving = magic_rules(counts_cost=False).channel
        assert counting is not None and leaving is not None
        self.assertEqual(counting.counted_mana(bolt, 2), 3 + 2)
        self.assertEqual(leaving.counted_mana(bolt, 2), 2)
        # Per kind: "weft" still counts the cost beside a "loom" that does not.
        self.assertEqual(leaving.counted_mana(bolt, 2, "weft"), 3 + 2)

    def test_the_default_counts_the_cost(self):
        self.assertTrue(LOOM.counts_cost)


class CastWithinChannelTest(TestCase):
    """Level 1, cost 3, Channel 1: no push, a push at the bound, one past."""

    def test_counting_the_cost_the_bolt_is_past_channel_unpushed(self):
        caster = bolt_caster(10).by_id(1)
        rules = magic_rules(counts_cost=True)
        bolt = get_spell(BOLT)
        self.assertFalse(rules.cast_within_channel(caster, bolt, 0))
        self.assertEqual(rules.channel_bound(caster, bolt), 1)

    def test_leaving_it_out_the_cost_fits_and_channel_bounds_the_push(self):
        caster = bolt_caster(10).by_id(1)
        rules = magic_rules(counts_cost=False)
        bolt = get_spell(BOLT)
        self.assertEqual(
            [rules.cast_within_channel(caster, bolt, push) for push in (0, 1, 2)],
            [True, True, False],
        )
        self.assertEqual(rules.channel_bound(caster, bolt), 3 + 1)
        self.assertEqual(
            [rules.past_channel(caster, bolt, push) for push in (0, 1, 2)],
            [False, False, True],
        )

    def test_the_push_room_is_the_purse_past_the_cost_within_channel(self):
        rules = magic_rules(counts_cost=False)
        bolt = get_spell(BOLT)
        assert rules.push is not None
        for mana, room in ((3, 0), (4, 1), (10, 1)):
            with self.subTest(mana=mana):
                caster = bolt_caster(mana).by_id(1)
                bound = rules.channel_bound(caster, bolt)
                self.assertEqual(rules.push.push_room(bolt, mana, bound), room)


class EngineCastTest(TestCase):
    def test_leaving_the_cost_out_the_bolt_casts_and_pushes_to_the_bound(self):
        rules = magic_rules(counts_cost=False)
        for push in (0, 1):
            with self.subTest(push=push):
                state = bolt_caster(10)
                self.assertTrue(engine_accepts(state, rules, push, borrow=False))
                self.assertEqual(state.by_id(1).mana, 10 - 3 - push)

    def test_a_push_past_the_bound_is_refused_naming_the_mana_past_the_cost(self):
        state = bolt_caster(10)
        message = refusal(state, magic_rules(counts_cost=False), 2)
        self.assertEqual(
            message,
            "Fighter 1 casts Lightning Bolt with 2 mana past its cost; "
            "their Channel for it is 1",
        )
        self.assertEqual(state.by_id(1).mana, 10)

    def test_counting_the_cost_the_refusal_names_the_whole_mana(self):
        message = refusal(bolt_caster(10), magic_rules(counts_cost=True), 0)
        self.assertEqual(
            message,
            "Fighter 1 casts Lightning Bolt with 3 mana; their Channel for it is 1",
        )

    def test_counting_the_cost_a_lifted_borrowing_at_no_mana_names_the_channel(
        self,
    ):
        # Lifted with no mana, the bound is 0 + Channel: the Channel alone,
        # worded as before v0.9.24.
        state = bolt_caster(0, lifted_magic_bounds=[CHANNEL])
        message = refusal(state, magic_rules(counts_cost=True), 0, borrow=True)
        self.assertEqual(
            message,
            "Fighter 1 casts Lightning Bolt with 3 mana; their Channel for it is 1",
        )

    def test_counting_the_cost_a_lifted_borrowing_names_its_bound(self):
        state = bolt_caster(1, lifted_magic_bounds=[CHANNEL])
        message = refusal(state, magic_rules(counts_cost=True), 0, borrow=True)
        self.assertEqual(
            message,
            "Fighter 1 casts Lightning Bolt with 3 mana; their Channel for it "
            "is 1, which lets them borrow to 2",
        )

    def test_a_lifted_borrowing_refusal_names_the_whole_mana_and_its_bound(self):
        state = bolt_caster(0, lifted_magic_bounds=[CHANNEL])
        message = refusal(state, magic_rules(counts_cost=False), 2, borrow=True)
        self.assertEqual(
            message,
            "Fighter 1 casts Lightning Bolt with 5 mana; their Channel for it "
            "is 1, which lets them borrow to 4",
        )


class MenuTest(TestCase):
    def test_leaving_the_cost_out_the_menu_offers_the_cast_and_one_push(self):
        state = bolt_caster(10)
        decision = policy.choose_option(
            state, state.by_id(1), magic=magic_rules(counts_cost=False)
        )
        self.assertEqual(
            offered(state, magic_rules(counts_cost=False)), {(0, False), (1, False)}
        )
        self.assertEqual(decision.withheld, [])
        pushed = next(
            candidate
            for candidate in decision.candidates
            if candidate.spell_key == BOLT and candidate.push_mana == 1
        )
        self.assertIn("(4 in all, Channel 1)", pushed.rationale)
        self.assertEqual(pushed.lifted_bounds, ())

    def test_counting_the_cost_the_menu_withholds_the_bolt(self):
        state = bolt_caster(10)
        decision = policy.choose_option(
            state, state.by_id(1), magic=magic_rules(counts_cost=True)
        )
        self.assertFalse(
            [candidate for candidate in decision.candidates if candidate.spell_key]
        )
        self.assertEqual(
            decision.withheld,
            [
                "Lightning Bolt withheld: it costs 3 mana, past Fighter 1's "
                "Channel 1 for it"
            ],
        )

    def test_leaving_the_cost_out_a_channel_of_0_withholds_nothing(self):
        # Level 0 by "loom": Channel max(0, -1) = 0, and the cost still fits.
        state = channel_caster({BOLT: 0}, spells=[BOLT])
        decision = policy.choose_option(
            state, state.by_id(1), magic=magic_rules(counts_cost=False)
        )
        self.assertEqual(decision.withheld, [])
        self.assertEqual(offered(state, magic_rules(counts_cost=False)), {(0, False)})

    def test_a_lifted_channel_names_the_push_past_it(self):
        state = bolt_caster(10, lifted_magic_bounds=[CHANNEL])
        candidates = {
            candidate.push_mana: candidate
            for candidate in bolt_candidates(state, magic_rules(counts_cost=False))
        }
        # Lifted, Push's own cap of 3 bounds the push.
        self.assertEqual(sorted(candidates), [0, 1, 2, 3])
        self.assertEqual(candidates[1].lifted_bounds, ())
        self.assertIn("(4 in all, Channel 1)", candidates[1].rationale)
        self.assertEqual(candidates[2].lifted_bounds, (CHANNEL,))
        self.assertIn("(5 in all, past Channel 1)", candidates[2].rationale)


class BorrowingTest(TestCase):
    """Leaving the cost out, a borrowed cost fits too: Channel bounds the
    mana past it."""

    def test_unlifted_a_borrowing_may_take_the_cost_plus_channel(self):
        rules = magic_rules(counts_cost=False)
        caster = bolt_caster(0).by_id(1)
        bolt = get_spell(BOLT)
        self.assertEqual(rules.channel_bound(caster, bolt, borrowing=True), 3 + 1)
        self.assertEqual(offered(bolt_caster(0), rules), {(0, True), (1, True)})

    def test_a_debt_cap_still_bounds_the_borrowing(self):
        rules = magic_rules(counts_cost=False, max_debt=3)
        self.assertEqual(offered(bolt_caster(0), rules), {(0, True)})
        self.assertEqual(
            offered(bolt_caster(0), magic_rules(counts_cost=False, max_debt=2)), set()
        )

    def test_lifted_with_no_debt_cap_the_bound_is_the_greater_of_mana_and_cost(self):
        bolt = get_spell(BOLT)
        leaving = magic_rules(counts_cost=False)
        counting = magic_rules(counts_cost=True)
        for mana, leaving_bound, counting_bound in (
            (0, 3 + 1, 0 + 1),
            (2, 3 + 1, 2 + 1),
            (5, 5 + 1, 5 + 1),
        ):
            with self.subTest(mana=mana):
                caster = bolt_caster(mana, lifted_magic_bounds=[CHANNEL]).by_id(1)
                self.assertEqual(
                    leaving.channel_bound(caster, bolt, borrowing=True), leaving_bound
                )
                self.assertEqual(
                    counting.channel_bound(caster, bolt, borrowing=True),
                    counting_bound,
                )
                self.assertIsNone(leaving.channel_bound(caster, bolt))

    def test_a_lift_never_binds_tighter_than_the_unlifted_bound(self):
        rules = magic_rules(counts_cost=False)
        bolt = get_spell(BOLT)
        for mana in range(8):
            with self.subTest(mana=mana):
                unlifted = bolt_caster(mana).by_id(1)
                lifted = bolt_caster(mana, lifted_magic_bounds=[CHANNEL]).by_id(1)
                unlifted_bound = rules.channel_bound(unlifted, bolt, borrowing=True)
                lifted_bound = rules.channel_bound(lifted, bolt, borrowing=True)
                assert unlifted_bound is not None and lifted_bound is not None
                self.assertGreaterEqual(lifted_bound, unlifted_bound)


class MenuAndEngineAgreeTest(TestCase):
    """Every Lightning Bolt the menu offers the engine takes up, and every
    one it leaves off (up to past Push's own cap) the engine refuses."""

    def test_they_agree_leaving_the_cost_out_and_counting_it(self):
        situations = {
            "from the purse": ({"mana": 10}, {}),
            "short of the cost": ({"mana": 0}, {}),
            "short, part paid": ({"mana": 2}, {}),
            "a debt cap": ({"mana": 0}, {"max_debt": 3}),
            "lifted, short": ({"mana": 0, "lifted_magic_bounds": [CHANNEL]}, {}),
            "lifted, part paid": ({"mana": 2, "lifted_magic_bounds": [CHANNEL]}, {}),
        }
        for counts_cost in (False, True):
            for situation, (caster_fields, borrowing_changes) in situations.items():
                rules = magic_rules(counts_cost=counts_cost, **borrowing_changes)
                menu = offered(bolt_caster(**caster_fields), rules)
                for push in range(5):
                    for borrow in (False, True):
                        with self.subTest(
                            counts_cost=counts_cost,
                            situation=situation,
                            push=push,
                            borrow=borrow,
                        ):
                            state = bolt_caster(**caster_fields)
                            short = state.by_id(1).mana < 3 + push
                            # A borrowing is offered only where the purse is short.
                            if borrow != short:
                                continue
                            self.assertEqual(
                                (push, borrow) in menu,
                                engine_accepts(state, rules, push, borrow=borrow),
                            )

    def test_the_leaving_out_is_offered_where_the_counting_is_not(self):
        # The agreement above would pass with both menus empty; this pins
        # what each offers a caster short of the cost.
        self.assertEqual(
            offered(bolt_caster(0), magic_rules(counts_cost=False)),
            {(0, True), (1, True)},
        )
        self.assertEqual(offered(bolt_caster(0), magic_rules(counts_cost=True)), set())


class TrueMagicUnchangedTest(TestCase):
    def rules(self, *, recipe_counts_cost: bool) -> MagicRules:
        channel = knot_channel_rules()
        loom = LOOM if recipe_counts_cost else LOOM_WITHOUT_COST
        return MagicRules(
            channel=replace(channel, derivations={**channel.derivations, "loom": loom}),
            true_magic=example_true_magic_rules(),
        )

    def test_a_true_cast_still_counts_the_cost(self):
        # "knot": 3 x 1 + 1 = 4 for a Knotwork of 1: the bolt and 1 pushed.
        caster = bolt_caster(10, skill_levels={KNOT_SKILL: 1}).by_id(1)
        bolt = get_spell(BOLT)
        for recipe_counts_cost in (True, False):
            with self.subTest(recipe_counts_cost=recipe_counts_cost):
                rules = self.rules(recipe_counts_cost=recipe_counts_cost)
                self.assertEqual(
                    [
                        rules.cast_within_channel(caster, bolt, push, kind=KNOT_KIND)
                        for push in (1, 2)
                    ],
                    [True, False],
                )
                self.assertEqual(rules.channel_bound(caster, bolt, kind=KNOT_KIND), 4)

    def test_rules_whose_true_kind_leaves_the_cost_out_are_refused(self):
        channel = knot_channel_rules()
        knot = replace(channel.derivations[KNOT_KIND], counts_cost=False)
        with self.assertRaisesRegex(ValueError, "leaves the cost out"):
            MagicRules(
                channel=replace(
                    channel, derivations={**channel.derivations, KNOT_KIND: knot}
                ),
                true_magic=example_true_magic_rules(),
            )
