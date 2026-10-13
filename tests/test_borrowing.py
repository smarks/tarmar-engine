"""Probe tests for injected Borrowing (tarmar-engine #28, split from
tarmar-studio #825).

Every number and name here is invented, as in ``test_magic`` and
``test_channel``: a 2d8 debt roll against WIS alone, a penalty of 2 per point
owed, a cap of 4, an automatic failure on a natural 16, and failures named
"Backlash" and "Surge". The tests check the engine's *structure*: a cast the
caster cannot afford is offered as a borrowing variant, borrowing empties
the pool and records the debt, the spell resolves, and the debt roll after
it is made against the injected target less what is owed.
"""

import json
from dataclasses import replace
from unittest import TestCase

from tarmar_engine import combat_math, engine, policy
from tarmar_engine.magic import BorrowingFailure, BorrowingRules, MagicRules
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.state import BattleState

from .test_channel import channel_caster, example_channel_rules
from .test_engine import events_of_type
from .test_magic import (
    MAGIC_PROFILE,
    PurposeRoller,
    cast,
    example_magic_rules,
    roll_purposes,
    status_payloads,
    wizard_state,
)

BACKLASH = BorrowingFailure(key="backlash", label="Backlash", is_runaway=False)
SURGE = BorrowingFailure(key="surge", label="Surge", is_runaway=True)


def example_borrowing_rules(**changes) -> BorrowingRules:
    """Invented Borrowing rules for the tests. Not Tarmar's numbers."""
    rules = BorrowingRules(
        debt_dice="2d8",
        debt_attributes=("WIS",),
        penalty_per_debt=2,
        failure=BACKLASH,
        automatic_failure_totals=frozenset({16}),
        max_debt=4,
    )
    return replace(rules, **changes)


def borrowing_magic_rules(**changes) -> MagicRules:
    """The example magic rules with Borrowing, some of its fields changed."""
    return replace(example_magic_rules(), borrowing=example_borrowing_rules(**changes))


def channel_borrowing_rules() -> MagicRules:
    """Borrowing with no debt cap, bounded by the example Channel alone."""
    return replace(
        example_magic_rules(),
        channel=example_channel_rules(),
        borrowing=example_borrowing_rules(max_debt=None),
    )


BORROWING_PROFILE = TarmarProfile(magic=borrowing_magic_rules())


def borrower(**overrides) -> BattleState:
    """The test wizard with 2 mana and Lightning Bolt (level 3) known."""
    fields = {
        "mana": 2,
        "spells": ["fire_missile", "lightning_bolt", "shield", "heal"],
    }
    fields.update(overrides)
    return wizard_state(**fields)


def borrow_cast(
    state, faces, *, profile=BORROWING_PROFILE, spell="lightning_bolt", push=0
):
    """Cast with the borrowing choice made; the events."""
    state.by_id(1).chosen_borrow = True
    return cast(state, faces, profile=profile, spell=spell, push=push)


def action_payload(events) -> dict:
    return events_of_type(events, "action")[0]["payload"]


def debt_payload(events) -> dict:
    return status_payloads(events, "debt_roll")[0]


def debt_roll_event(events) -> dict:
    return next(
        event
        for event in events_of_type(events, "roll")
        if event["payload"]["purpose"] == "debt"
    )


# Lightning Bolt with a natural 9 (no tier), on target, 9 damage.
BOLT_FACES = [[3, 3, 3], [2, 2, 2], [3, 3, 3]]
# The same with a keen natural 7 (1 mana back), and a held debt roll.
KEEN_BOLT_FACES = [[2, 2, 3], [2, 2, 2], [3, 3, 3], [1, 1]]
POOL_REFUND_PROFILE = TarmarProfile(
    magic=borrowing_magic_rules(refunds_pay_debt_first=False)
)


class BorrowingRulesValidationTest(TestCase):
    """Malformed Borrowing rules are refused when built, not mid-battle."""

    def test_the_dice_attributes_penalty_and_cap_are_checked(self):
        for changes in (
            {"debt_dice": "lots"},
            {"debt_attributes": ("STR",)},
            {"debt_attributes": ()},
            {"penalty_per_debt": -1},
            {"max_debt": -1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                example_borrowing_rules(**changes)

    def test_an_uncapped_debt_needs_channel_rules(self):
        with self.assertRaisesRegex(ValueError, "Channel"):
            borrowing_magic_rules(max_debt=None)
        uncapped = channel_borrowing_rules().borrowing
        assert uncapped is not None
        self.assertIsNone(uncapped.max_debt)

    def test_the_target_sums_the_attributes_less_the_debt(self):
        caster = wizard_state().by_id(1)
        both = example_borrowing_rules(debt_attributes=("INT", "WIS"))
        self.assertEqual(both.debt_target(caster, 3), 14 + 12 - 2 * 3)
        self.assertEqual(both.versus, "INT+WIS")

    def test_the_default_profile_injects_no_borrowing(self):
        self.assertIsNone(example_magic_rules().borrowing)


class BorrowMenuTest(TestCase):
    """The borrowing variants on the menu, for a player to choose."""

    def borrowed(self, state, rules=None) -> list:
        decision = policy.choose_option(
            state, state.by_id(1), magic=rules or borrowing_magic_rules()
        )
        return [candidate for candidate in decision.candidates if candidate.borrow]

    def pairs(self, state, rules=None) -> set:
        return {
            (candidate.spell_key, candidate.push_mana)
            for candidate in self.borrowed(state, rules)
        }

    def test_an_unaffordable_cast_gains_a_borrowing_variant(self):
        offered = self.pairs(borrower(mana=0))
        self.assertIn(("lightning_bolt", 0), offered)
        self.assertIn(("fire_missile", 0), offered)

    def test_no_borrowing_rules_no_variants_and_no_unaffordable_cast(self):
        state = borrower(mana=0)
        decision = policy.choose_option(
            state, state.by_id(1), magic=example_magic_rules()
        )
        self.assertFalse(any(candidate.borrow for candidate in decision.candidates))
        self.assertFalse(any(candidate.spell_key for candidate in decision.candidates))

    def test_a_pushed_cast_short_of_mana_gains_a_borrowing_variant(self):
        state = borrower(mana=2)
        offered = self.pairs(state)
        # Fire Missile (1) is affordable plain and pushed by 1; pushed by 2 or
        # 3 (the push cap) it needs 1 or 2 more than the purse holds.
        self.assertNotIn(("fire_missile", 0), offered)
        self.assertNotIn(("fire_missile", 1), offered)
        self.assertIn(("fire_missile", 2), offered)
        self.assertIn(("fire_missile", 3), offered)
        self.assertIn(("lightning_bolt", 0), offered)
        names = [candidate.name for candidate in self.borrowed(state)]
        self.assertIn("CAST SPELL Fire Missile (PUSH 2 MANA, BORROW 1 MANA)", names)

    def test_no_variant_past_the_debt_cap(self):
        # Owing 3 of a cap of 4: only a cast short by 1 may still borrow.
        offered = self.pairs(borrower(mana=0, debt=3))
        self.assertIn(("fire_missile", 0), offered)
        self.assertNotIn(("lightning_bolt", 0), offered)
        self.assertNotIn(("fire_missile", 1), offered)
        self.assertEqual(self.pairs(borrower(mana=0, debt=4)), set())

    def test_no_variant_past_the_channel(self):
        state = channel_caster(
            {"fire_missile": 2, "lightning_bolt": 1},
            mana=0,
            spells=["fire_missile", "lightning_bolt"],
        )
        offered = self.pairs(state, channel_borrowing_rules())
        # Channel 3 for Fire Missile (cost 1 + up to 2 pushed), 1 for the Bolt.
        self.assertEqual(
            offered, {("fire_missile", 0), ("fire_missile", 1), ("fire_missile", 2)}
        )

    def test_the_ai_never_borrows_and_its_choice_is_unchanged(self):
        for mana in (0, 2):
            with self.subTest(mana=mana):
                state = borrower(mana=mana)
                plain = policy.choose_option(
                    state, state.by_id(1), magic=example_magic_rules()
                )
                with_borrowing = policy.choose_option(
                    state, state.by_id(1), magic=borrowing_magic_rules()
                )
                self.assertEqual(with_borrowing.chosen, plain.chosen)
                self.assertEqual(
                    with_borrowing.candidates[: len(plain.candidates)],
                    plain.candidates,
                )
                extra = with_borrowing.candidates[len(plain.candidates) :]
                self.assertTrue(extra)
                for candidate in extra:
                    self.assertTrue(candidate.borrow)
                    self.assertEqual(candidate.score, policy.PLAYER_ONLY_SCORE)
                    self.assertIs(candidate.to_payload()["borrow"], True)
                for candidate in plain.candidates:
                    self.assertNotIn("borrow", candidate.to_payload())

    def test_the_rationale_names_the_shortfall_and_the_rolled_target(self):
        bolt = next(
            candidate
            for candidate in self.borrowed(borrower(mana=0))
            if candidate.spell_key == "lightning_bolt" and not candidate.push_mana
        )
        self.assertIn("Borrow 3 mana", bolt.rationale)
        self.assertIn(
            "debt roll 2d8 ≤ WIS 12 less 2 x 3 owed, 0 injury and 0 off-balance = 6",
            bolt.rationale,
        )
        events = borrow_cast(borrower(mana=0), [*BOLT_FACES, [1, 1]])
        self.assertEqual(debt_payload(events)["target"], 6)

    def test_the_rationale_target_carries_injury_and_off_balance(self):
        def menu_target(state) -> int:
            bolt = next(
                candidate
                for candidate in self.borrowed(state)
                if candidate.spell_key == "lightning_bolt" and not candidate.push_mana
            )
            return int(bolt.rationale.split(";")[0].rsplit("= ", 1)[1])

        events = borrow_cast(borrower(mana=0, fatigue=1), [*BOLT_FACES, [1, 1]])
        rolled = debt_payload(events)["target"]
        self.assertLess(rolled, 6)
        self.assertEqual(menu_target(borrower(mana=0, fatigue=1)), rolled)
        self.assertEqual(
            menu_target(borrower(mana=0, fatigue=1, off_balance=True)),
            rolled - combat_math.OFF_BALANCE_PENALTY,
        )

    def test_hand_to_hand_with_a_weapon_in_hand_withholds_gestured_casts(self):
        def hand_to_hand(**overrides) -> BattleState:
            state = borrower(mana=0, hth_with=[2], **overrides)
            partner = state.by_id(2)
            partner.q, partner.hth_with = -2, [1]
            return state

        self.assertNotIn(("lightning_bolt", 0), self.pairs(hand_to_hand()))
        offered = self.borrowed(hand_to_hand(spell_mastery={"lightning_bolt": 2}))
        bolts = [
            candidate
            for candidate in offered
            if candidate.spell_key == "lightning_bolt" and not candidate.push_mana
        ]
        self.assertEqual([candidate.letter for candidate in bolts], ["r"])

    def test_a_grapple_withholds_casts_below_spell_mastery_3(self):
        def grappled(**overrides) -> BattleState:
            state = borrower(mana=0, grappled_by=2, **overrides)
            captor = state.by_id(2)
            captor.q, captor.grappling = -2, 1
            return state

        self.assertEqual(self.pairs(grappled()), set())
        self.assertIn(
            ("lightning_bolt", 0),
            self.pairs(grappled(spell_mastery={"lightning_bolt": 3})),
        )


class BorrowingCastTest(TestCase):
    """The cast: the pool emptied, the debt recorded, the spell, the roll."""

    def test_borrowing_empties_the_pool_and_records_the_debt(self):
        state = borrower(mana=2)
        events = borrow_cast(state, [*BOLT_FACES, [1, 1]])
        caster = state.by_id(1)
        self.assertEqual((caster.mana, caster.debt), (0, 1))
        payload = action_payload(events)
        self.assertEqual((payload["borrowed"], payload["debt"]), (1, 1))
        self.assertIn(
            "borrows 1, debt 1", events_of_type(events, "action")[0]["message"]
        )
        # The spell resolved: 9 damage on the target.
        self.assertEqual(state.by_id(2).fatigue, state.by_id(2).max_fatigue - 9)

    def test_the_debt_roll_comes_after_the_spell(self):
        events = borrow_cast(borrower(), [*BOLT_FACES, [1, 1]])
        self.assertEqual(
            roll_purposes(events), ["casting", "spell aim", "spell damage", "debt"]
        )

    def test_the_debt_roll_target_subtracts_the_debt(self):
        # Owing 2 already, borrowing 1 more: WIS 12 less 2 x 3.
        events = borrow_cast(borrower(debt=2), [*BOLT_FACES, [3, 3]])
        payload = debt_payload(events)
        self.assertEqual((payload["target"], payload["debt"]), (6, 3))
        self.assertTrue(payload["held"])
        roll = debt_roll_event(events)["payload"]
        self.assertEqual(
            (roll["target_number"], roll["versus"], roll["outcome"]), (6, "WIS", "held")
        )
        self.assertEqual(payload["debt_roll"], debt_roll_event(events)["sequence"])

    def test_a_held_roll_narrates_held_and_no_failure(self):
        events = borrow_cast(borrower(), [*BOLT_FACES, [1, 1]])
        message = next(
            event["message"]
            for event in events_of_type(events, "status")
            if "debt_roll" in event["payload"]
        )
        self.assertIn("holds the debt roll", message)
        self.assertEqual(status_payloads(events, "debt_failure"), [])

    def test_a_failure_narrates_the_injected_label(self):
        # 15 against WIS 12 less 2 x 1 = 10.
        state = borrower()
        events = borrow_cast(state, [*BOLT_FACES, [8, 7]])
        self.assertFalse(debt_payload(events)["held"])
        self.assertEqual(debt_roll_event(events)["payload"]["outcome"], "failed")
        failure = next(
            event
            for event in events_of_type(events, "status")
            if "debt_failure" in event["payload"]
        )
        self.assertEqual(
            failure["payload"],
            {
                "spell": "lightning_bolt",
                "debt_failure": "backlash",
                "label": "Backlash",
                "runaway": False,
            },
        )
        self.assertIn("Backlash", failure["message"])
        # The spell already resolved, and stands.
        self.assertEqual(state.by_id(2).fatigue, state.by_id(2).max_fatigue - 9)

    def test_a_failure_sets_the_runaway_flag_when_injected_so(self):
        surge = TarmarProfile(magic=borrowing_magic_rules(failure=SURGE))
        events = borrow_cast(borrower(), [*BOLT_FACES, [8, 7]], profile=surge)
        failure = status_payloads(events, "debt_failure")[0]
        self.assertEqual((failure["label"], failure["runaway"]), ("Surge", True))

    def test_an_automatic_failure_total_fails_whatever_the_target(self):
        events = borrow_cast(borrower(wisdom=40), [*BOLT_FACES, [8, 8]])
        payload = debt_payload(events)
        self.assertEqual((payload["natural"], payload["target"]), (16, 38))
        self.assertTrue(payload["automatic"])
        self.assertFalse(payload["held"])
        self.assertEqual(len(status_payloads(events, "debt_failure")), 1)

    def test_a_tier_refund_pays_the_debt_first_when_injected_so(self):
        # A keen natural 7 hands 1 back; the Bolt borrowed all 3 of its cost.
        state = borrower(mana=0)
        events = borrow_cast(state, KEEN_BOLT_FACES)
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (0, 2))
        tier = status_payloads(events, "success_tier")[0]
        self.assertEqual((tier["debt_paid"], tier["debt"]), (1, 2))
        self.assertEqual(debt_payload(events)["target"], 12 - 2 * 2)

    def test_a_tier_refund_refills_the_pool_otherwise(self):
        # 2 from the pool and 1 borrowed: the keen refund of 1 refills it.
        state = borrower(mana=2)
        events = borrow_cast(state, KEEN_BOLT_FACES, profile=POOL_REFUND_PROFILE)
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (1, 1))
        tier = status_payloads(events, "success_tier")[0]
        self.assertEqual((tier["mana_refund"], tier["debt_paid"]), (1, 0))
        self.assertEqual(debt_payload(events)["target"], 12 - 2 * 1)

    def test_a_refund_the_pool_keeps_is_capped_at_what_the_pool_paid(self):
        # All 3 borrowed, nothing from the pool: no refund lifts it above 0.
        state = borrower(mana=0)
        events = borrow_cast(state, KEEN_BOLT_FACES, profile=POOL_REFUND_PROFILE)
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (0, 3))
        self.assertEqual(status_payloads(events, "success_tier")[0]["mana_refund"], 0)

    def test_an_injured_caster_s_debt_target_drops_by_the_band(self):
        healthy = borrow_cast(borrower(), [*BOLT_FACES, [1, 1]])
        injured = borrow_cast(borrower(fatigue=1), [*BOLT_FACES, [1, 1]])
        casting = [
            events_of_type(events, "roll")[0]["payload"]["target_number"]
            for events in (healthy, injured)
        ]
        band = casting[0] - casting[1]
        self.assertGreater(band, 0)
        self.assertEqual(
            debt_payload(injured)["target"], debt_payload(healthy)["target"] - band
        )

    def test_a_pushed_borrowing_cast_rolls_control_before_the_debt(self):
        state = borrower(mana=2)
        events = borrow_cast(
            state,
            [[3, 3, 3], [3, 3], [2, 2, 2], [3], [1, 1]],
            spell="fire_missile",
            push=2,
        )
        self.assertEqual(
            roll_purposes(events),
            ["casting", "control", "spell aim", "spell damage", "debt"],
        )
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (0, 1))
        # 3 rolled + a held push of 2 x 4.
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 11)

    def test_a_failure_that_keeps_its_mana_borrows_nothing(self):
        state = borrower(mana=0)
        events = borrow_cast(state, [[5, 5, 5]])  # 15: fails, no mana lost
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (0, 0))
        self.assertEqual(action_payload(events)["borrowed"], 0)
        self.assertEqual(roll_purposes(events), ["casting"])

    def test_a_failure_that_loses_its_mana_still_borrows_and_rolls(self):
        state = borrower(mana=0, intelligence=18)
        events = borrow_cast(state, [[6, 6, 5], [1, 1]])  # 17: fails, mana lost
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (0, 3))
        self.assertEqual(roll_purposes(events), ["casting", "debt"])

    def test_an_affordable_cast_borrows_nothing(self):
        state = borrower(mana=10)
        events = borrow_cast(state, BOLT_FACES)
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (7, 0))
        self.assertEqual(action_payload(events)["borrowed"], 0)
        self.assertNotIn("debt", roll_purposes(events))

    def test_without_the_choice_the_caster_still_lacks_the_mana(self):
        state = borrower(mana=0)
        events = cast(
            state, BOLT_FACES, profile=BORROWING_PROFILE, spell="lightning_bolt"
        )
        self.assertIn("lacks the mana", events_of_type(events, "info")[0]["message"])
        self.assertEqual(state.by_id(1).debt, 0)

    def test_borrowing_past_the_cap_is_refused(self):
        with self.assertRaisesRegex(ValueError, "debt cap"):
            borrow_cast(borrower(mana=0, debt=2), BOLT_FACES)

    def test_borrowing_past_the_channel_is_refused(self):
        state = channel_caster({"lightning_bolt": 1}, mana=0, spells=["lightning_bolt"])
        with self.assertRaisesRegex(ValueError, "Channel"):
            borrow_cast(
                state,
                BOLT_FACES,
                profile=TarmarProfile(magic=channel_borrowing_rules()),
            )

    def test_borrowing_with_no_borrowing_rules_is_refused(self):
        for profile in (TARMAR, MAGIC_PROFILE):
            with (
                self.subTest(profile=profile.name),
                self.assertRaisesRegex(ValueError, "no Borrowing rules"),
            ):
                borrow_cast(borrower(mana=0), BOLT_FACES, profile=profile)

    def test_rules_without_borrowing_add_no_borrowing_keys(self):
        events = cast(borrower(mana=10), BOLT_FACES, spell="lightning_bolt")
        self.assertNotIn("borrowed", action_payload(events))
        self.assertNotIn("debt", action_payload(events))


class DebtSnapshotTest(TestCase):
    """The debt persists on the combatant and rides the snapshot."""

    def test_the_debt_round_trips_through_a_snapshot(self):
        state = borrower(debt=3)
        state.by_id(1).chosen_borrow = True
        data = json.loads(json.dumps(state.to_dict()))
        self.assertEqual(data["combatants"][0]["debt"], 3)
        restored = BattleState.from_dict(data)
        self.assertEqual(restored.by_id(1).debt, 3)
        self.assertTrue(restored.by_id(1).chosen_borrow)

    def test_a_battle_with_no_debt_snapshots_as_it_always_has(self):
        combatant = borrower().to_dict()["combatants"][0]
        self.assertNotIn("debt", combatant)
        self.assertNotIn("chosen_borrow", combatant)
        restored = BattleState.from_dict(borrower().to_dict())
        self.assertEqual(restored.by_id(1).debt, 0)

    def test_the_debt_outlasts_the_turn_and_the_choice_does_not(self):
        state = borrower(debt=3)
        state.by_id(1).chosen_borrow = True
        state.by_id(1).reset_for_turn()
        self.assertEqual(state.by_id(1).debt, 3)
        self.assertFalse(state.by_id(1).chosen_borrow)


class BorrowingTurnPhaseTest(TestCase):
    """A player's borrowing choice, made in phase 3 and paid in phase 5."""

    def test_the_borrowing_is_chosen_in_phase_3_and_rolled_in_phase_5(self):
        rules = borrowing_magic_rules()

        def choose(battle, actor):
            decision = policy.choose_option(battle, actor, magic=rules)
            if actor.combatant_id != 1:
                return decision
            bolt = next(
                candidate
                for candidate in decision.candidates
                if candidate.spell_key == "lightning_bolt"
                and candidate.borrow
                and not candidate.push_mana
            )
            return policy.Decision(chosen=bolt, candidates=decision.candidates)

        state = borrower(mana=0)
        events: list[dict] = []
        roller = PurposeRoller(
            {
                "casting": [[3, 3, 3]],
                "spell aim": [[2, 2, 2]],
                "spell damage": [[3, 3, 3]],
                "debt": [[1, 1]],
            }
        )
        engine.run_turn(
            state, roller, events.append, choose, profile=TarmarProfile(magic=rules)
        )
        decision = next(
            event
            for event in events_of_type(events, "decision")
            if event["actor"] == "Fighter 1"
        )
        self.assertEqual(decision["phase"], 3)
        self.assertIs(decision["payload"]["chosen"]["borrow"], True)
        debt = next(event for event in events if "debt_roll" in event["payload"])
        self.assertEqual(debt["phase"], 5)
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (0, 3))
