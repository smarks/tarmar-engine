"""Probe tests for the magic bounds a Game Master may lift for one figure
(Spencer's ruling of 2026-10-14 on tarmar-studio #37).

Channel, Push's own cap and a counter's commitment bounds may each be lifted
for one figure, recorded on the battle by the consuming game and handed to
the engine as ``CombatantState.lifted_magic_bounds``. The tests check that
the lifted figure's menu offers the wider amounts and the engine runs them,
flagged in the payload only where a lifted bound was actually exceeded, and
that every other figure is held to the bounds as ever. Every number is the
invented one of the test modules this one borrows from.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine import engine, policy
from tarmar_engine.magic import (
    CHANNEL,
    COUNTER_BOUNDS,
    LIFTABLE_BOUNDS,
    LIFTED_BOUNDS_KEY,
    LIFTED_COUNTER_MINIMUM,
    PUSH_CAP,
    ChannelUnclassifiedSpell,
    MagicRules,
    bound_lifted,
    casting_mana,
)
from tarmar_engine.profile import TarmarProfile
from tarmar_engine.spells import get_spell
from tarmar_engine.state import BattleState, WeaponState

from .test_borrowing import (
    BOLT_FACES,
    action_payload,
    borrow_cast,
    channel_borrowing_rules,
    example_borrowing_rules,
)
from .test_channel import (
    CHANNEL_PROFILE,
    cast_payload,
    channel_caster,
    channel_magic_rules,
    example_channel_rules,
)
from .test_counterspell import (
    CASTER_LOW,
    DEFENDER_HIGH,
    MISSILE_FACES,
    choose_counter,
    counter_state,
    countered_cast,
    counters,
    decide,
    example_counterspell_rules,
    result_payload,
    runner,
)
from .test_engine import ScriptedRoller, events_of_type
from .test_magic import cast, status_payloads


def lift(state: BattleState, *bounds: str, combatant_id: int = 1) -> BattleState:
    """The state with ``bounds`` lifted for one combatant."""
    state.by_id(combatant_id).lifted_magic_bounds = list(bounds)
    return state


def capped_channel_borrowing_rules() -> MagicRules:
    """Channel and Borrowing with its debt cap of 4."""
    return replace(
        channel_magic_rules(),
        borrowing=example_borrowing_rules(),
    )


# Lightning Bolt (level 3) with a recorded level of 1: Channel 1.
def bolt_caster(**overrides) -> BattleState:
    return channel_caster(
        {"lightning_bolt": 1},
        spells=["fire_missile", "lightning_bolt"],
        intelligence=18,
        **overrides,
    )


class BoundLiftedTest(TestCase):
    def test_the_keys_are_the_three_bounds(self):
        self.assertEqual(LIFTABLE_BOUNDS, (CHANNEL, PUSH_CAP, COUNTER_BOUNDS))
        self.assertEqual(LIFTABLE_BOUNDS, ("channel", "push_cap", "counter_bounds"))

    def test_a_listed_bound_is_lifted_and_no_other(self):
        caster = lift(channel_caster({}), CHANNEL).by_id(1)
        self.assertTrue(bound_lifted(caster, CHANNEL))
        self.assertFalse(bound_lifted(caster, PUSH_CAP))

    def test_an_unknown_bound_is_refused_listed_or_asked(self):
        caster = lift(channel_caster({}), "chanel").by_id(1)
        with self.assertRaisesRegex(ValueError, "chanel"):
            bound_lifted(caster, CHANNEL)
        with self.assertRaisesRegex(ValueError, "debt_cap"):
            bound_lifted(channel_caster({}).by_id(1), "debt_cap")


class LiftedChannelCastTest(TestCase):
    def test_the_lifted_caster_casts_past_its_channel_and_says_so(self):
        state = lift(bolt_caster(), CHANNEL)
        events = cast(
            state, BOLT_FACES, profile=CHANNEL_PROFILE, spell="lightning_bolt"
        )
        payload = cast_payload(events)
        self.assertTrue(payload["success"])
        self.assertEqual((payload["channel"], payload["channel_kind"]), (1, "loom"))
        self.assertEqual(payload[LIFTED_BOUNDS_KEY], [CHANNEL])
        self.assertIn("Channel 1", events_of_type(events, "action")[0]["message"])
        self.assertEqual(state.by_id(1).mana, 10 - 3)

    def test_another_caster_is_still_refused(self):
        state = lift(bolt_caster(), CHANNEL, combatant_id=2)
        with self.assertRaisesRegex(ValueError, "Channel"):
            cast(state, BOLT_FACES, profile=CHANNEL_PROFILE, spell="lightning_bolt")
        self.assertEqual(state.by_id(1).mana, 10)

    def test_a_cast_within_a_lifted_channel_is_not_flagged(self):
        state = lift(channel_caster({"fire_missile": 3}), CHANNEL)
        events = cast(state, MISSILE_FACES, profile=CHANNEL_PROFILE)
        self.assertNotIn(LIFTED_BOUNDS_KEY, cast_payload(events))

    def test_lifting_the_push_cap_does_not_lift_channel(self):
        state = lift(bolt_caster(), PUSH_CAP)
        with self.assertRaisesRegex(ValueError, "Channel"):
            cast(state, BOLT_FACES, profile=CHANNEL_PROFILE, spell="lightning_bolt")

    def test_a_countered_cast_past_a_lifted_channel_is_flagged(self):
        state = lift(counter_state(), CHANNEL)
        rules = replace(
            channel_magic_rules(),
            counterspell=example_counterspell_rules(),
        )
        state.by_id(1).spell_skill_levels = {"fire_missile": 1}
        events = countered_cast(
            state,
            [CASTER_LOW, DEFENDER_HIGH],
            profile=TarmarProfile(magic=rules),
            push=1,
        )
        # Channel 1 for Fire Missile; 1 + 1 pushed went past it.
        countered = status_payloads(events, "spell_countered")[0]
        self.assertEqual(countered[LIFTED_BOUNDS_KEY], [CHANNEL])
        self.assertEqual(countered["channel"], 1)


class LiftedPushCapCastTest(TestCase):
    def test_the_lifted_caster_pushes_past_the_cap(self):
        # Channel 9 for Fire Missile; the cap is 3, the push 4.
        state = lift(channel_caster({"fire_missile": 5}, wisdom=30), PUSH_CAP)
        events = cast(
            state,
            [[3, 3, 3], [3, 3], [2, 2, 2], [5]],
            profile=CHANNEL_PROFILE,
            push=4,
        )
        self.assertEqual(cast_payload(events)[LIFTED_BOUNDS_KEY], [PUSH_CAP])
        self.assertEqual(state.by_id(1).mana, 10 - 5)

    def test_an_unlifted_caster_is_held_to_the_cap(self):
        state = lift(channel_caster({"fire_missile": 5}), CHANNEL)
        with self.assertRaisesRegex(ValueError, "cap"):
            cast(state, [[3, 3, 3]], profile=CHANNEL_PROFILE, push=4)

    def test_both_lifted_both_flagged(self):
        # Channel 3 for Fire Missile; 1 + 5 pushed goes past it and the cap.
        state = lift(channel_caster({"fire_missile": 2}, wisdom=30), PUSH_CAP, CHANNEL)
        events = cast(
            state,
            [[3, 3, 3], [3, 3], [2, 2, 2], [5]],
            profile=CHANNEL_PROFILE,
            push=5,
        )
        self.assertEqual(cast_payload(events)[LIFTED_BOUNDS_KEY], [PUSH_CAP, CHANNEL])


class LiftedChannelBorrowingTest(TestCase):
    """A lifted Channel and Borrowing (house reading
    ``lifted_channel_bounds_the_shortfall``)."""

    def test_with_a_debt_cap_the_lifted_caster_borrows_past_its_channel(self):
        # Channel 1, 2 mana: the Bolt (3) borrows 1, within the cap of 4.
        state = lift(bolt_caster(mana=2), CHANNEL)
        events = borrow_cast(
            state,
            [*BOLT_FACES, [1, 1]],
            profile=TarmarProfile(magic=capped_channel_borrowing_rules()),
        )
        payload = action_payload(events)
        self.assertEqual(
            (payload["borrowed"], payload[LIFTED_BOUNDS_KEY]), (1, [CHANNEL])
        )

    def test_with_no_debt_cap_channel_bounds_the_shortfall(self):
        # Channel 1, 2 mana: the Bolt (3) borrows 1, within 2 + 1.
        state = lift(bolt_caster(mana=2), CHANNEL)
        events = borrow_cast(
            state,
            [*BOLT_FACES, [1, 1]],
            profile=TarmarProfile(magic=channel_borrowing_rules()),
        )
        payload = action_payload(events)
        self.assertEqual(
            (payload["borrowed"], payload[LIFTED_BOUNDS_KEY]), (1, [CHANNEL])
        )
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (0, 1))

    def test_with_no_debt_cap_no_borrowing_past_mana_and_channel(self):
        # 3 + 1 pushed is past 2 mana + Channel 1.
        state = lift(bolt_caster(mana=2), CHANNEL)
        with self.assertRaisesRegex(ValueError, "borrow to 3"):
            borrow_cast(
                state,
                [*BOLT_FACES, [1, 1]],
                profile=TarmarProfile(magic=channel_borrowing_rules()),
                push=1,
            )
        self.assertEqual((state.by_id(1).mana, state.by_id(1).debt), (2, 0))

    def test_with_no_debt_cap_an_unlifted_caster_is_held_to_its_channel(self):
        state = bolt_caster(mana=2)
        with self.assertRaisesRegex(ValueError, "Channel for it is 1$"):
            borrow_cast(
                state,
                [*BOLT_FACES, [1, 1]],
                profile=TarmarProfile(magic=channel_borrowing_rules()),
            )

    def test_with_no_debt_cap_a_cast_from_the_purse_still_passes_it(self):
        state = lift(bolt_caster(), CHANNEL)
        state.by_id(1).chosen_borrow = True
        events = cast(
            state,
            BOLT_FACES,
            profile=TarmarProfile(magic=channel_borrowing_rules()),
            spell="lightning_bolt",
        )
        payload = action_payload(events)
        self.assertEqual(
            (payload["borrowed"], payload[LIFTED_BOUNDS_KEY]), (0, [CHANNEL])
        )


class LiftedCounterBoundsTest(TestCase):
    """The example bounds are 1 to 4; the defender holds 6 mana."""

    def declared(self, mana: int, *bounds: str) -> list[dict]:
        state = lift(counter_state(), *bounds, combatant_id=2)
        choose_counter(state, mana)
        turn_runner, events = runner(state)
        turn_runner.declare_counter(state.by_id(2))
        self.assertEqual(state.by_id(2).mana, 6 - mana)
        return status_payloads(events, "counter_declared")

    def test_a_lifted_defender_may_commit_nothing_or_past_the_most(self):
        for mana in (LIFTED_COUNTER_MINIMUM, 5, 6):
            with self.subTest(mana=mana):
                self.assertEqual(
                    self.declared(mana, COUNTER_BOUNDS)[0][LIFTED_BOUNDS_KEY],
                    [COUNTER_BOUNDS],
                )

    def test_a_commitment_within_the_bounds_is_not_flagged(self):
        self.assertNotIn(LIFTED_BOUNDS_KEY, self.declared(3, COUNTER_BOUNDS)[0])

    def test_the_purse_and_a_negative_commitment_still_hold(self):
        state = lift(counter_state(mana=2), COUNTER_BOUNDS, combatant_id=2)
        choose_counter(state, 5)
        turn_runner, _events = runner(state)
        turn_runner.declare_counter(state.by_id(2))
        self.assertIsNone(state.by_id(2).counter)
        state = lift(counter_state(), COUNTER_BOUNDS, combatant_id=2)
        choose_counter(state, -1)
        with self.assertRaisesRegex(ValueError, "allow 0 to the purse"):
            runner(state)[0].declare_counter(state.by_id(2))

    def test_an_unlifted_defender_is_held_to_the_bounds(self):
        for bounds in ((), (CHANNEL, PUSH_CAP)):
            for mana in (0, 5):
                state = lift(counter_state(), *bounds, combatant_id=2)
                choose_counter(state, mana)
                with self.subTest(bounds=bounds, mana=mana):
                    with self.assertRaisesRegex(ValueError, "allow 1 to 4"):
                        runner(state)[0].declare_counter(state.by_id(2))

    def test_a_counter_of_nothing_opposes_with_no_bonus(self):
        state = lift(counter_state(), COUNTER_BOUNDS, combatant_id=2)
        events = countered_cast(
            state, [CASTER_LOW, DEFENDER_HIGH, *MISSILE_FACES], mana=0
        )
        totals = result_payload(events)["defender_totals"]
        self.assertEqual((totals[0]["mana"], totals[0]["bonus"]), (0, 0))
        self.assertEqual(state.by_id(2).mana, 6)


class LiftedMenuTest(TestCase):
    """The lifted figure's menu offers the wider amounts; no one else's does."""

    def menu(
        self,
        state: BattleState,
        rules: MagicRules | None = None,
        actor_id: int = 1,
    ) -> policy.Decision:
        return policy.choose_option(
            state, state.by_id(actor_id), magic=rules or channel_magic_rules()
        )

    def pushes(
        self, decision: policy.Decision, spell: str = "fire_missile"
    ) -> list[int]:
        return sorted(
            candidate.push_mana
            for candidate in decision.candidates
            if candidate.spell_key == spell and candidate.push_mana
        )

    def test_a_lifted_channel_puts_the_cast_back_on_the_menu_and_the_ai_takes_it(
        self,
    ):
        # Unarmed, Channel 0 for Fire Missile: withheld unless lifted.
        held = channel_caster({}, spells=["fire_missile"], weapon=WeaponState())
        self.assertEqual(self.menu(held).chosen.spell_key, "")
        lifted = lift(
            channel_caster({}, spells=["fire_missile"], weapon=WeaponState()), CHANNEL
        )
        decision = self.menu(lifted)
        self.assertEqual(decision.withheld, [])
        self.assertEqual(decision.chosen.spell_key, "fire_missile")
        other = lift(
            channel_caster({}, spells=["fire_missile"], weapon=WeaponState()),
            CHANNEL,
            combatant_id=2,
        )
        self.assertEqual(len(self.menu(other).withheld), 1)

    def test_a_lifted_channel_still_withholds_an_unclassified_spell(self):
        rules = replace(
            channel_magic_rules(),
            channel=replace(example_channel_rules(), default_kind=None),
        )
        state = lift(channel_caster({"fire_missile": 3}), CHANNEL)
        offered = {
            candidate.spell_key for candidate in self.menu(state, rules).candidates
        }
        self.assertNotIn("fire_missile", offered)

    def test_pushes_widen_with_each_bound_lifted(self):
        # Channel 9 for Fire Missile (level 1), 10 mana, Push's cap 3.
        def pushes_with(*bounds: str) -> list[int]:
            return self.pushes(
                self.menu(lift(channel_caster({"fire_missile": 5}), *bounds))
            )

        self.assertEqual(pushes_with(), [1, 2, 3])
        self.assertEqual(pushes_with(CHANNEL), [1, 2, 3])
        self.assertEqual(pushes_with(PUSH_CAP), list(range(1, 9)))
        self.assertEqual(pushes_with(PUSH_CAP, CHANNEL), list(range(1, 10)))

    def test_a_lifted_cap_with_no_channel_rules_pushes_to_the_purse(self):
        from .test_magic import example_magic_rules

        state = lift(channel_caster({}), PUSH_CAP)
        self.assertEqual(
            self.pushes(self.menu(state, example_magic_rules())), list(range(1, 10))
        )

    def test_a_push_line_names_the_bound_it_passes_by_ruling(self):
        def rationale(push_mana: int, *bounds: str) -> str:
            # Channel 3 for Fire Missile (level 1); Push's cap 3.
            decision = self.menu(lift(channel_caster({"fire_missile": 2}), *bounds))
            return next(
                candidate.rationale
                for candidate in decision.candidates
                if candidate.spell_key == "fire_missile"
                and candidate.push_mana == push_mana
            )

        self.assertIn(", Channel 3)", rationale(2))
        self.assertIn(", past Channel 3)", rationale(3, CHANNEL))
        self.assertIn(
            ", past Channel 3, past the push cap 3)",
            rationale(4, CHANNEL, PUSH_CAP),
        )

    def test_the_ruling_is_left_to_the_caller_s_words(self):
        # The line names the bound; that a ruling allowed it rides on
        # lifted_bounds for the caller to word (tarmar-engine #55).
        self.assertFalse(hasattr(policy, "PAST_BY_RULING"))
        decision = self.menu(
            lift(channel_caster({"fire_missile": 2}), CHANNEL, PUSH_CAP)
        )
        past = [
            candidate for candidate in decision.candidates if candidate.lifted_bounds
        ]
        self.assertTrue(past)
        for candidate in past:
            with self.subTest(candidate=candidate.name):
                self.assertNotIn("ruling", candidate.rationale)
                self.assertNotIn("Game Master", candidate.rationale)

    def test_an_unpushed_cast_past_a_lifted_channel_says_so(self):
        # Channel 1 for the Bolt (cost 3).
        note = "; 3 mana, past Channel 1"

        def bolt_lines(state: BattleState, rules: MagicRules) -> list[str]:
            return [
                candidate.rationale
                for candidate in self.menu(state, rules).candidates
                if candidate.spell_key == "lightning_bolt" and not candidate.push_mana
            ]

        plain = bolt_lines(lift(bolt_caster(), CHANNEL), channel_magic_rules())
        self.assertTrue(plain)
        self.assertTrue(all(line.endswith(note) for line in plain))
        borrowing = bolt_lines(
            lift(bolt_caster(mana=2), CHANNEL), channel_borrowing_rules()
        )
        self.assertTrue(borrowing)
        self.assertTrue(all(note in line for line in borrowing))
        # Channel 5 for Fire Missile (cost 1): within it, no note.
        within = self.menu(lift(channel_caster({"fire_missile": 3}), CHANNEL))
        self.assertFalse(
            any(
                "past Channel" in candidate.rationale
                for candidate in within.candidates
                if candidate.spell_key == "fire_missile" and not candidate.push_mana
            )
        )

    def test_borrowing_widens_past_a_lifted_channel(self):
        def borrowed(rules: MagicRules, *bounds: str, mana: int = 0) -> set:
            state = lift(bolt_caster(mana=mana), *bounds)
            return {
                (candidate.spell_key, candidate.push_mana)
                for candidate in self.menu(state, rules).candidates
                if candidate.borrow
            }

        capped = capped_channel_borrowing_rules()
        # Channel 1 for the Bolt (cost 3) and 0 for Fire Missile (cost 1),
        # with an empty purse: neither may borrow unless Channel is lifted,
        # and then the debt cap of 4 bounds the whole casting.
        self.assertEqual(borrowed(capped), set())
        self.assertEqual(
            borrowed(capped, CHANNEL),
            {
                ("fire_missile", 0),
                ("fire_missile", 1),
                ("fire_missile", 2),
                ("fire_missile", 3),
                ("lightning_bolt", 0),
                ("lightning_bolt", 1),
            },
        )
        # With no debt cap, Channel bounds the shortfall: 2 mana + Channel 1
        # lets the Bolt borrow 1; unlifted, nothing.
        uncapped = channel_borrowing_rules()
        self.assertEqual(borrowed(uncapped, mana=2), set())
        self.assertEqual(borrowed(uncapped, CHANNEL, mana=2), {("lightning_bolt", 0)})

    def test_counters_widen_for_the_lifted_defender_only(self):
        state = lift(counter_state(), COUNTER_BOUNDS, combatant_id=2)
        state.by_id(2).spells = ["shield"]
        defender = [candidate.counter_mana for candidate in counters(decide(state, 2))]
        self.assertEqual(defender, list(range(0, 7)))
        caster = [candidate.counter_mana for candidate in counters(decide(state, 1))]
        self.assertEqual(caster, [1, 2, 3, 4])


class LiftedSnapshotTest(TestCase):
    def test_the_lifting_rides_the_snapshot_only_when_set(self):
        state = lift(channel_caster({}), CHANNEL, PUSH_CAP)
        data = state.to_dict()
        self.assertEqual(
            data["combatants"][0]["lifted_magic_bounds"], [CHANNEL, PUSH_CAP]
        )
        self.assertNotIn("lifted_magic_bounds", data["combatants"][1])
        restored = BattleState.from_dict(data)
        self.assertEqual(restored.by_id(1).lifted_magic_bounds, [CHANNEL, PUSH_CAP])

    def test_an_older_snapshot_loads_with_nothing_lifted(self):
        older = channel_caster({}).to_dict()
        for entry in older["combatants"]:
            self.assertNotIn("lifted_magic_bounds", entry)
        self.assertEqual(BattleState.from_dict(older).by_id(1).lifted_magic_bounds, [])


class CandidatesCarryTheEnginesAnswerTest(TestCase):
    """Every option on a lifted figure's menu carries the bounds it goes
    past, and running it gives the engine's payload the same answer, from
    the one function (:meth:`MagicRules.lifted_bounds_passed`)."""

    RULES = capped_channel_borrowing_rules()

    def caster(self, *bounds: str) -> BattleState:
        """Channel 9 for Fire Missile (so a push may pass the cap of 3 alone)
        and 1 for the Bolt (so a cast may pass Channel alone), 8 mana."""
        return lift(
            channel_caster(
                {"fire_missile": 5, "lightning_bolt": 1},
                spells=["fire_missile", "lightning_bolt"],
                mana=8,
                intelligence=30,
                wisdom=30,
            ),
            *bounds,
        )

    def ran(self, state: BattleState, candidate: policy.Candidate) -> list[str]:
        """The cast's payload ``lifted_bounds`` when the option runs."""
        trial = BattleState.from_dict(state.to_dict())
        caster = trial.by_id(1)
        caster.chosen_spell = candidate.spell_key
        caster.chosen_target = candidate.target_id
        caster.chosen_push_mana = candidate.push_mana
        caster.chosen_borrow = candidate.borrow
        events: list[dict] = []
        engine.TurnRunner(
            trial,
            ScriptedRoller(),
            events.append,
            profile=TarmarProfile(magic=self.RULES),
        ).cast_spell(caster)
        return cast_payload(events).get(LIFTED_BOUNDS_KEY, [])

    def test_each_cast_option_agrees_with_the_engine(self):
        for bounds in ((), (CHANNEL,), (PUSH_CAP,), (CHANNEL, PUSH_CAP)):
            state = self.caster(*bounds)
            casts = [
                candidate
                for candidate in policy.choose_option(
                    state, state.by_id(1), magic=self.RULES
                ).candidates
                if candidate.spell_key
            ]
            passed_somewhere: set[str] = set()
            for candidate in casts:
                with self.subTest(bounds=bounds, option=candidate.name):
                    answer = self.RULES.lifted_bounds_passed(
                        state.by_id(1),
                        get_spell(candidate.spell_key),
                        candidate.push_mana,
                    )
                    self.assertEqual(candidate.lifted_bounds, answer)
                    self.assertEqual(self.ran(state, candidate), list(answer))
                    payload = candidate.to_payload()
                    if answer:
                        self.assertEqual(payload[LIFTED_BOUNDS_KEY], list(answer))
                    else:
                        self.assertNotIn(LIFTED_BOUNDS_KEY, payload)
                passed_somewhere.update(candidate.lifted_bounds)
            # Not vacuous: each lifted bound is passed by some option, and
            # no option passes a bound that is not lifted.
            self.assertEqual(passed_somewhere, set(bounds))

    def test_each_counter_option_agrees_with_the_engine(self):
        for bounds in ((), (COUNTER_BOUNDS,)):
            state = lift(counter_state(), *bounds, combatant_id=2)
            offered = counters(decide(state, 2))
            rules = example_counterspell_rules()
            for candidate in offered:
                mana = candidate.counter_mana
                assert mana is not None
                with self.subTest(bounds=bounds, mana=mana):
                    trial = BattleState.from_dict(state.to_dict())
                    choose_counter(trial, mana)
                    turn_runner, events = runner(trial)
                    turn_runner.declare_counter(trial.by_id(2))
                    declared = status_payloads(events, "counter_declared")[0]
                    answer = rules.lifted_bounds_passed(mana)
                    self.assertEqual(candidate.lifted_bounds, answer)
                    self.assertEqual(declared.get(LIFTED_BOUNDS_KEY, []), list(answer))
            self.assertEqual(
                {bound for option in offered for bound in option.lifted_bounds},
                set(bounds),
            )


class CastWithinChannelValueTest(TestCase):
    """The public Channel check (tarmar-engine #41), by expected value, one
    test per branch. Fire Missile costs 1 and Lightning Bolt 3; a recorded
    level L gives Channel 2L - 1, never below 0."""

    def within(
        self,
        rules: MagicRules,
        state: BattleState,
        spell_key: str,
        push_mana: int = 0,
        *,
        borrowing: bool = False,
    ) -> bool:
        return rules.cast_within_channel(
            state.by_id(1), get_spell(spell_key), push_mana, borrowing=borrowing
        )

    def test_unlifted_a_cast_at_the_channel_passes_and_one_past_does_not(self):
        rules = channel_magic_rules()
        # Level 2: Channel 3, the Bolt's cost exactly.
        self.assertTrue(
            self.within(rules, channel_caster({"lightning_bolt": 2}), "lightning_bolt")
        )
        # Level 0: Channel 0, one short of Fire Missile's cost.
        self.assertFalse(
            self.within(rules, channel_caster({"fire_missile": 0}), "fire_missile")
        )

    def test_pushed_mana_counts_toward_the_channel(self):
        rules = channel_magic_rules()
        # Level 2: Channel 3; Fire Missile costs 1.
        state = channel_caster({"fire_missile": 2})
        self.assertTrue(self.within(rules, state, "fire_missile", 2))
        self.assertFalse(self.within(rules, state, "fire_missile", 3))

    def test_lifted_and_not_borrowing_past_the_channel_passes(self):
        # Channel 1 for the Bolt (cost 3), lifted.
        state = lift(bolt_caster(), CHANNEL)
        rules = channel_magic_rules()
        self.assertTrue(self.within(rules, state, "lightning_bolt"))
        self.assertTrue(self.within(rules, state, "lightning_bolt", 6))

    def test_lifted_and_borrowing_past_the_debt_cap_passes(self):
        # Debt cap 4, 2 mana: 9 mana borrows 7, past the cap; the cap is the
        # caller's check, not Channel's.
        state = lift(bolt_caster(mana=2), CHANNEL)
        rules = capped_channel_borrowing_rules()
        self.assertTrue(self.within(rules, state, "lightning_bolt", 6, borrowing=True))

    def test_lifted_and_borrowing_with_no_debt_cap_holds_to_mana_plus_channel(self):
        # 2 mana plus Channel 1: 3, the Bolt's cost.
        state = lift(bolt_caster(mana=2), CHANNEL)
        rules = channel_borrowing_rules()
        self.assertTrue(self.within(rules, state, "lightning_bolt", borrowing=True))
        self.assertFalse(self.within(rules, state, "lightning_bolt", 1, borrowing=True))
        # The same casting from the purse is not bounded at all.
        self.assertTrue(self.within(rules, state, "lightning_bolt", 1))

    def test_no_channel_rules_bound_nothing(self):
        from .test_magic import example_magic_rules

        self.assertTrue(
            self.within(example_magic_rules(), channel_caster({}), "lightning_bolt", 6)
        )


class CastWithinChannelTest(TestCase):
    """Pins the call sites: the casting phase refuses, and the menu withholds,
    exactly the casts the public Channel check (tarmar-engine #41) says no
    to, for every caster, spell, push and borrowing the test boards hold,
    lifted or not. Both call it, so this guards their use of it; the method's
    own answers are pinned by :class:`CastWithinChannelValueTest`."""

    RULE_SETS = {
        "channel": channel_magic_rules(),
        "debt cap": capped_channel_borrowing_rules(),
        "no debt cap": channel_borrowing_rules(),
    }
    LIFTINGS = ((), (CHANNEL,), (PUSH_CAP,), (CHANNEL, PUSH_CAP))
    SPELLS = ("fire_missile", "lightning_bolt", "heal")

    def caster(self, fire_level: int, mana: int, *bounds: str) -> BattleState:
        return lift(
            channel_caster(
                {"fire_missile": fire_level, "lightning_bolt": 1},
                skill_levels={"tapestry": 0},
                spells=list(self.SPELLS),
                mana=mana,
                intelligence=30,
                wisdom=30,
            ),
            *bounds,
        )

    def engine_allows(
        self,
        state: BattleState,
        rules: MagicRules,
        spell_key: str,
        push_mana: int,
        borrow: bool,
    ) -> bool:
        """Does the casting phase let the cast past its Channel check? Only
        its Channel refusal says no; any later outcome says yes."""
        trial = BattleState.from_dict(state.to_dict())
        caster = trial.by_id(1)
        caster.chosen_spell = spell_key
        caster.chosen_target = 2
        caster.chosen_push_mana = push_mana
        caster.chosen_borrow = borrow
        try:
            engine.TurnRunner(
                trial,
                ScriptedRoller(),
                lambda event: None,
                profile=TarmarProfile(magic=rules),
            ).cast_spell(caster)
        except ValueError as refusal:
            if "their Channel for it" in str(refusal):
                return False
        return True

    def cases(self):
        """Every case the Channel check decides: the engine refuses a push
        the rules give no effect or past an unlifted cap before it."""
        for rules_name, rules in self.RULE_SETS.items():
            assert rules.push is not None
            for bounds in self.LIFTINGS:
                for fire_level in (0, 1, 2, 3, 5):
                    for mana in (2, 8):
                        state = self.caster(fire_level, mana, *bounds)
                        cap_lifted = PUSH_CAP in bounds
                        for spell_key in self.SPELLS:
                            spell = get_spell(spell_key)
                            for push_mana in range(0, 7):
                                if push_mana and not (
                                    rules.push.can_push(spell)
                                    and rules.push.within_cap(
                                        push_mana, cap_lifted=cap_lifted
                                    )
                                ):
                                    continue
                                for borrow in (False, True):
                                    if borrow and rules.borrowing is None:
                                        continue
                                    yield (
                                        rules_name,
                                        rules,
                                        bounds,
                                        state,
                                        spell_key,
                                        push_mana,
                                        borrow,
                                    )

    def test_the_casting_phase_refuses_exactly_what_it_says_no_to(self):
        answers: dict[tuple[tuple[str, ...], bool], int] = {}
        for rules_name, rules, bounds, state, spell_key, push, borrow in self.cases():
            spell = get_spell(spell_key)
            caster = state.by_id(1)
            borrows = borrow and casting_mana(spell, push) > caster.mana
            with self.subTest(
                rules=rules_name,
                bounds=bounds,
                fire=caster.spell_skill_levels,
                mana=caster.mana,
                spell=spell_key,
                push=push,
                borrow=borrow,
            ):
                answer = rules.cast_within_channel(
                    caster, spell, push, borrowing=borrows
                )
                self.assertEqual(
                    answer,
                    self.engine_allows(state, rules, spell_key, push, borrow),
                )
                key = (bounds, answer)
                answers[key] = answers.get(key, 0) + 1
        # Not vacuous: each lifting has casts allowed, and the unlifted and
        # push-cap-only boards have casts refused.
        for bounds in self.LIFTINGS:
            self.assertTrue(answers.get((bounds, True)))
        self.assertTrue(answers.get(((), False)))
        self.assertTrue(answers.get(((PUSH_CAP,), False)))

    def test_the_menu_withholds_exactly_what_it_says_no_to(self):
        refused = allowed_past_channel = 0
        for rules_name, rules, bounds, state, spell_key, push, borrow in self.cases():
            spell = get_spell(spell_key)
            caster = state.by_id(1)
            # The menu tests the Channel of the plain casts it offers: those
            # the caster can pay for from the purse.
            if push or borrow or spell.level > caster.mana:
                continue
            with self.subTest(
                rules=rules_name,
                bounds=bounds,
                fire=caster.spell_skill_levels,
                mana=caster.mana,
                spell=spell_key,
            ):
                withheld = policy.choose_option(state, caster, magic=rules).withheld
                answer = rules.cast_within_channel(caster, spell)
                self.assertEqual(
                    not answer,
                    any(line.startswith(f"{spell.name} withheld") for line in withheld),
                )
                refused += not answer
                channel = rules.channel_for(caster, spell)
                assert channel is not None
                allowed_past_channel += answer and spell.level > channel
        self.assertTrue(refused)
        self.assertTrue(allowed_past_channel)

    def test_an_unclassified_spell_raises_lifted_or_not(self):
        rules = replace(
            channel_magic_rules(),
            channel=replace(example_channel_rules(), default_kind=None),
        )
        for bounds in ((), (CHANNEL,)):
            state = lift(channel_caster({"fire_missile": 3}), *bounds)
            with self.subTest(bounds=bounds):
                with self.assertRaises(ChannelUnclassifiedSpell):
                    rules.cast_within_channel(state.by_id(1), get_spell("fire_missile"))
                with self.assertRaises(ChannelUnclassifiedSpell):
                    cast(state, [[3, 3, 3]], profile=TarmarProfile(magic=rules))
