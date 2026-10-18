"""Pins every entry of ``tarmar_engine.house_rulings`` to the code using it.

tarmar-studio #826: each number the engine uses with no page behind it gets
a ruling (marked for Spencer) or a page, "and then a drift test". A change
to the record or to the code without the other fails here.
"""

from dataclasses import replace
from unittest import TestCase

import tarmar_rules

from tarmar_engine import actions, engine, hexes, policy, spells
from tarmar_engine.house_rulings import HOUSE_RULINGS, PAGE, RULING, TACTIC, ruling
from tarmar_engine.state import BattleState


class HouseRulingsRecordTest(TestCase):
    def test_every_entry_names_its_authority_and_issue(self):
        keys = [entry.key for entry in HOUSE_RULINGS]
        self.assertEqual(len(keys), len(set(keys)))
        for entry in HOUSE_RULINGS:
            with self.subTest(key=entry.key):
                self.assertTrue(
                    any(mark in entry.authority for mark in (PAGE, RULING, TACTIC))
                )
                self.assertTrue(entry.issue.startswith("#"))

    def test_spell_magnitudes(self):
        value = ruling("spell_magnitudes").value
        for key in (
            "fire_missile",
            "fire_ball",
            "lightning_bolt",
            "heal",
            "fatigue",
            "wound",
        ):
            self.assertEqual(spells.SPELLS[key].damage, value[key], key)
        self.assertEqual(spells.SPELLS["shield"].tn_bonus, value["shield_tn_bonus"])
        self.assertEqual(
            spells.SPELLS["blur"].attacker_penalty, value["blur_attacker_penalty"]
        )

    def test_geometry_and_tactics(self):
        self.assertEqual(hexes.HEXES_PER_MEGAHEX_STEP, ruling("megahex_step").value)
        self.assertEqual(engine.WALK_SLOW_MAX, ruling("walk_slow_step").value)
        self.assertEqual(engine.PREFERRED_STANDOFF, ruling("preferred_standoff").value)

    def test_plate_cracking_rounds_down(self):
        # 5 stops halved: 2 still apply, so a 7 gets 5 through.
        self.assertEqual(
            tarmar_rules.damage_after_armour(7, 5, "Heavy Striking", "Heavy"), 5
        )
        self.assertEqual(ruling("plate_cracking_rounding").value, "stops // 2")

    def test_the_review_rulings(self):
        from tarmar_engine import combat_math
        from tarmar_engine.state import BattleState, WeaponState

        from .test_state import make_combatant

        bare = WeaponState(damage="1d6-2")
        first = make_combatant(1, q=0, r=0, facing=0, weapon=bare)
        second = make_combatant(2, q=1, r=0, facing=3, weapon=bare)
        state = BattleState(arena_radius=6, combatants=[first, second])
        self.assertIn(
            combat_math.hth_entry_reason(state, first, second),
            ruling("hth_by_agreement").value,
        )
        # The arena edge: a target at the rim facing inward.
        edge = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=5, r=0, facing=0),
                make_combatant(2, q=6, r=0, facing=3),
            ],
        )
        self.assertEqual(
            combat_math.hth_entry_reason(edge, edge.by_id(1), edge.by_id(2)),
            "their back is to the wall",
        )
        self.assertEqual(ruling("squeeze_defences").authority, RULING)
        self.assertEqual(ruling("unconscious_caster_spells_end").authority, RULING)
        self.assertEqual(ruling("dodge_closes_on_missile_threat").authority, TACTIC)

    def test_mana_on_failure(self):
        from .test_magic import CASTING_FAILURE_PROFILE, cast, wizard_state

        self.assertIs(ruling("mana_on_failure").value, True)
        # INT 10 and the invented casting failure rules (mana lost on 12 and
        # 14): both totals fail, and only the named one spends the mana.
        pays = wizard_state(intelligence=10)
        cast(pays, [[4, 4, 4]], profile=CASTING_FAILURE_PROFILE)  # 12: named
        self.assertEqual(pays.by_id(1).mana, 9)
        keeps = wizard_state(intelligence=10)
        cast(keeps, [[5, 4, 4]], profile=CASTING_FAILURE_PROFILE)  # 13: not named
        self.assertEqual(keeps.by_id(1).mana, 10)

    def test_the_pass_rulings(self):
        self.assertEqual(
            tarmar_rules.CRIT_DAMAGE_ROLLS,
            ruling("critical_modifier_once").value["critical_dice_rolls"],
        )
        self.assertEqual(
            tarmar_rules.SEVERE_CRIT_DAMAGE_ROLLS,
            ruling("critical_modifier_once").value["severe_dice_rolls"],
        )
        self.assertEqual(ruling("forced_retreat_choice").value, "policy.choose_retreat")
        self.assertTrue(callable(policy.choose_retreat))
        self.assertIn(
            "q",
            actions.legal_actions(
                engaged=False,
                prone=False,
                has_missile=False,
                has_spells=False,
                has_melee_target=False,
                can_pick_up=True,
            ),
        )


class MagicRulingsTest(TestCase):
    """The magic readings, pinned by probe (tarmar-engine #26/#27)."""

    def test_success_tier_needs_a_success(self):
        from .test_magic import cast, status_payloads, wizard_state

        self.assertEqual(ruling("success_tier_needs_a_success").value, "success first")
        events = cast(wizard_state(intelligence=5), [[2, 2, 2]])
        self.assertEqual(status_payloads(events, "success_tier"), [])

    def test_magic_bonus_on_rolled_effect(self):
        from tarmar_engine.magic import SpellEffectKind

        from .test_magic import cast, wizard_state

        self.assertEqual(
            ruling("magic_bonus_on_rolled_effect").value,
            tuple(kind.value for kind in SpellEffectKind),
        )
        state = wizard_state()
        state.by_id(2).stops = 3
        cast(state, [[2, 2, 2], [2, 2, 2], [5]])
        # 5 rolled + 5 radiant = 10, less 3 stopped.
        self.assertEqual(state.by_id(2).fatigue, state.by_id(2).max_fatigue - 7)
        with self.assertRaises(ValueError):
            cast(wizard_state(), [[3, 3, 3]], spell="shield", push=1)

    def test_push_control_after_casting(self):
        from .test_magic import cast, roll_purposes, wizard_state

        events = cast(wizard_state(), [[3, 3, 3], [3, 3], [2, 2, 2], [5]], push=1)
        self.assertEqual(
            tuple(roll_purposes(events)[:3]),
            ruling("push_control_after_casting").value,
        )
        injured = wizard_state(fatigue=1)
        events = cast(injured, [[3, 3, 3], [3, 3], [2, 2, 2], [5]], push=1)
        casting, control = [
            event["payload"]["target_number"]
            for event in events
            if event["event_type"] == "roll"
        ][:2]
        self.assertLess(casting, 14)  # the injury band reached the cast...
        self.assertEqual(control, 12 - 2 - (14 - casting))  # ...and the control

    def test_pushed_mana_paid_with_cost(self):
        from .test_magic import CASTING_FAILURE_PROFILE, cast, wizard_state

        self.assertEqual(ruling("pushed_mana_paid_with_cost").value, "with the cost")
        state = wizard_state(intelligence=18)
        # 12: the invented floor, a failure that loses its mana.
        cast(state, [[4, 4, 4]], profile=CASTING_FAILURE_PROFILE, push=2)
        self.assertEqual(state.by_id(1).mana, 10 - 1 - 2)

    def test_tier_refund_before_control(self):
        from .test_magic import (
            cast,
            control_payload,
            events_of_type,
            status_payloads,
            wizard_state,
        )

        self.assertEqual(
            ruling("tier_refund_before_control").value, "before the Control Roll"
        )
        state = wizard_state()
        # Keen (natural 7, 1 back), then an automatic Runaway on the control.
        events = cast(state, [[2, 2, 3], [1, 1]], push=2)
        refund = next(
            event
            for event in events_of_type(events, "status")
            if event["payload"].get("success_tier")
        )
        control_roll = events_of_type(events, "roll")[1]
        self.assertLess(refund["sequence"], control_roll["sequence"])
        self.assertTrue(control_payload(events)["runaway"])
        self.assertEqual(status_payloads(events, "mana_refund")[0]["mana_refund"], 1)
        self.assertEqual(state.by_id(1).mana, 10 - 1 - 2 + 1)

    def test_tier_refund_cap_includes_push(self):
        from tarmar_engine.profile import TarmarProfile

        from .test_magic import cast, example_magic_rules, wizard_state

        self.assertEqual(
            ruling("tier_refund_cap_includes_push").value, "spell cost + pushed mana"
        )
        rules = example_magic_rules()
        keen = replace(rules.casting_success_tiers[1], mana_refund=99)
        lavish = replace(
            rules,
            casting_success_tiers=(rules.casting_success_tiers[0], keen),
        )
        state = wizard_state()
        # Level 1 + 2 pushed = 3 paid; the refund of 99 is capped at 3.
        cast(
            state,
            [[2, 2, 3], [3, 3], [2, 2, 2], [5]],
            push=2,
            profile=TarmarProfile(magic=lavish),
        )
        self.assertEqual(state.by_id(1).mana, 10)


class ChannelRulingsTest(TestCase):
    """The Channel readings, pinned by probe (tarmar-engine #29)."""

    def test_channel_bounds_whole_casting(self):
        from .test_channel import CHANNEL_PROFILE, channel_caster
        from .test_magic import cast

        self.assertEqual(
            ruling("channel_bounds_whole_casting").value, "spell cost + pushed mana"
        )
        # Channel 3: cost 1 + 2 pushed fits, cost 1 + 3 pushed does not.
        cast(
            channel_caster({"fire_missile": 2}, wisdom=30),
            [[3, 3, 3], [3, 3], [2, 2, 2], [5]],
            profile=CHANNEL_PROFILE,
            push=2,
        )
        with self.assertRaises(ValueError):
            cast(
                channel_caster({"fire_missile": 2}),
                [[3, 3, 3]],
                profile=CHANNEL_PROFILE,
                push=3,
            )
        # Lightning Bolt's own cost of 3 past a Channel of 1.
        with self.assertRaises(ValueError):
            cast(
                channel_caster({"lightning_bolt": 1}, spells=["lightning_bolt"]),
                [[3, 3, 3]],
                profile=CHANNEL_PROFILE,
                spell="lightning_bolt",
            )

    def test_channel_unrecorded_level(self):
        from tarmar_engine.magic import UNRECORDED_SKILL_LEVEL, ChannelDerivation
        from tarmar_engine.spells import get_spell

        from .test_channel import channel_caster

        self.assertEqual(ruling("channel_unrecorded_level").value, 0)
        self.assertEqual(UNRECORDED_SKILL_LEVEL, 0)
        caster = channel_caster({}, skill_levels={}).by_id(1)
        per_spell = ChannelDerivation(source_skill=None, multiplier=5, offset=0)
        general = ChannelDerivation(source_skill="tapestry", multiplier=5, offset=0)
        spell = get_spell("fire_missile")
        self.assertEqual(per_spell.recorded_level(caster, spell), 0)
        self.assertEqual(general.recorded_level(caster, spell), 0)

    def test_channel_kind_belongs_to_the_spell(self):
        from tarmar_engine.spells import get_spell

        from .test_channel import channel_caster, example_channel_rules

        self.assertEqual(ruling("channel_kind_belongs_to_the_spell").value, "per spell")
        rules = example_channel_rules()
        first = channel_caster({}, skill_levels={"tapestry": 4}).by_id(1)
        second = channel_caster({}, skill_levels={"tapestry": 1}).by_id(1)
        heal = get_spell("heal")
        self.assertEqual(rules.kind_for(heal), "weft")
        self.assertEqual(rules.channel_for(first, heal), 4 + 2)
        self.assertEqual(rules.channel_for(second, heal), 1 + 2)

    def test_channel_not_on_renewal(self):
        from tarmar_engine import engine

        from .test_channel import CHANNEL_PROFILE, channel_caster
        from .test_engine import ScriptedRoller

        self.assertEqual(ruling("channel_not_on_renewal").value, "casting only")
        # Shield is up with no recorded level: Channel 0, yet it renews.
        state = channel_caster({}, active_spells=["shield"])
        events: list[dict] = []
        runner = engine.TurnRunner(
            state, ScriptedRoller(), events.append, profile=CHANNEL_PROFILE
        )
        runner.phase_renew_spells()
        self.assertEqual(state.by_id(1).active_spells, ["shield"])
        self.assertEqual(state.by_id(1).mana, 10 - 1)


def _whole_debt_refund_profile():
    """The Borrowing profile with a keen refund of 3: enough to pay a
    borrowed Lightning Bolt's whole debt back."""
    from tarmar_engine.profile import TarmarProfile

    from .test_borrowing import borrowing_magic_rules

    rules = borrowing_magic_rules()
    radiant, keen, tidy = rules.casting_success_tiers
    generous = replace(keen, mana_refund=3)
    return TarmarProfile(
        magic=replace(rules, casting_success_tiers=(radiant, generous, tidy))
    )


class BorrowingRulingsTest(TestCase):
    """The Borrowing readings, pinned by probe (tarmar-engine #28)."""

    def test_debt_roll_after_effect(self):
        from .test_borrowing import (
            BOLT_FACES,
            KEEN_BOLT_FACES,
            borrow_cast,
            borrower,
            debt_payload,
        )
        from .test_engine import events_of_type
        from .test_magic import control_payload, roll_purposes

        events = borrow_cast(borrower(), [*BOLT_FACES, [1, 1]])
        self.assertEqual(
            tuple(roll_purposes(events)), ruling("debt_roll_after_effect").value
        )
        # An aim that misses still owes, and still rolls.
        missed = borrow_cast(borrower(), [[3, 3, 3], [6, 6, 6], [1, 1]])
        self.assertEqual(roll_purposes(missed), ["casting", "spell aim", "debt"])
        # A Control Roll that stops the spell still owes, and still rolls:
        # 11 against WIS 12 less 2 x 2 pushed lands in a non-Runaway band
        # that stops the spell.
        stopped_state = borrower(mana=2)
        stopped = borrow_cast(
            stopped_state,
            [[3, 3, 3], [6, 5], [1, 1]],
            spell="fire_missile",
            push=2,
        )
        self.assertFalse(control_payload(stopped)["takes_effect"])
        self.assertFalse(control_payload(stopped)["runaway"])
        self.assertEqual(roll_purposes(stopped), ["casting", "control", "debt"])
        self.assertEqual(stopped_state.by_id(1).debt, 1)
        # The casting roll's injury band comes off the debt roll's target too.
        injured = borrow_cast(borrower(fatigue=1), [*BOLT_FACES, [1, 1]])
        casting_target = events_of_type(injured, "roll")[0]["payload"]["target_number"]
        self.assertLess(casting_target, 14)
        self.assertEqual(debt_payload(injured)["target"], 10 - (14 - casting_target))
        # A refund that pays the whole debt back leaves the roll to be made.
        cleared_state = borrower(mana=0)
        cleared = borrow_cast(
            cleared_state, KEEN_BOLT_FACES, profile=_whole_debt_refund_profile()
        )
        self.assertEqual(cleared_state.by_id(1).debt, 0)
        self.assertEqual(debt_payload(cleared)["debt"], 0)

    def test_borrowed_when_the_cast_pays(self):
        from .test_borrowing import FAILING_BORROWING_PROFILE, borrow_cast, borrower

        self.assertEqual(
            ruling("borrowed_when_the_cast_pays").value, "when the cast pays"
        )
        # The invented casting failure rules: 13 fails and keeps its mana,
        # 12 fails and loses it.
        keeps = borrower(mana=0, intelligence=18)
        borrow_cast(keeps, [[5, 4, 4]], profile=FAILING_BORROWING_PROFILE)
        self.assertEqual(keeps.by_id(1).debt, 0)
        loses = borrower(mana=0, intelligence=18)
        borrow_cast(loses, [[4, 4, 4], [1, 1]], profile=FAILING_BORROWING_PROFILE)
        self.assertEqual(loses.by_id(1).debt, 3)

    def test_borrowing_counts_toward_channel(self):
        from tarmar_engine.magic import MagicRules
        from tarmar_engine.profile import TarmarProfile

        from .test_borrowing import (
            BOLT_FACES,
            borrow_cast,
            channel_borrowing_rules,
            example_borrowing_rules,
        )
        from .test_channel import channel_caster

        self.assertEqual(
            ruling("borrowing_counts_toward_channel").value,
            "spell cost + pushed mana, borrowed or not",
        )
        with self.assertRaisesRegex(ValueError, "Channel"):
            borrow_cast(
                channel_caster({"lightning_bolt": 1}, mana=0),
                BOLT_FACES,
                profile=TarmarProfile(magic=channel_borrowing_rules()),
            )
        with self.assertRaisesRegex(ValueError, "Channel"):
            MagicRules(borrowing=example_borrowing_rules(max_debt=None))

    def test_ai_never_borrows(self):
        from .test_borrowing import borrower, borrowing_magic_rules

        self.assertEqual(ruling("ai_never_borrows").value, "player only")
        state = borrower(mana=0)
        decision = policy.choose_option(
            state, state.by_id(1), magic=borrowing_magic_rules()
        )
        self.assertFalse(decision.chosen.borrow)
        borrowed = [candidate for candidate in decision.candidates if candidate.borrow]
        self.assertTrue(borrowed)
        for candidate in borrowed:
            self.assertEqual(candidate.score, policy.PLAYER_ONLY_SCORE)

    def test_debt_persists_on_the_combatant(self):
        from .test_borrowing import borrower

        self.assertEqual(
            ruling("debt_persists_on_the_combatant").value, "CombatantState.debt"
        )
        state = borrower(debt=3)
        state.by_id(1).reset_for_turn()
        restored = BattleState.from_dict(state.to_dict())
        self.assertEqual(restored.by_id(1).debt, 3)

    def test_refunds_pay_debt_first(self):
        from .test_borrowing import (
            KEEN_BOLT_FACES,
            POOL_REFUND_PROFILE,
            borrow_cast,
            borrower,
            debt_payload,
        )

        self.assertEqual(ruling("refunds_pay_debt_first").value, "tier refund")
        state = borrower(mana=0)
        # A keen natural 7 hands 1 back before the debt roll.
        events = borrow_cast(state, KEEN_BOLT_FACES)
        self.assertEqual(state.by_id(1).debt, 2)
        self.assertEqual(debt_payload(events)["debt"], 2)
        # Kept by the pool instead, the refund is capped at what the pool paid.
        pool_only = borrower(mana=0)
        borrow_cast(pool_only, KEEN_BOLT_FACES, profile=POOL_REFUND_PROFILE)
        self.assertEqual((pool_only.by_id(1).mana, pool_only.by_id(1).debt), (0, 3))


class HandToHandAndDestinationRulingsTest(TestCase):
    """The hand-to-hand menu (tarmar-engine #19) and the AI's hexes (#20)."""

    def test_hth_table_is_the_menu(self):
        self.assertEqual(
            tuple(
                actions.legal_actions(
                    engaged=True,
                    prone=True,
                    has_missile=True,
                    has_spells=True,
                    has_melee_target=True,
                    can_grapple=True,
                    in_hth=True,
                    can_draw_dagger=True,
                )
            ),
            ruling("hth_table_is_the_menu").value,
        )

    def test_hth_casting_hands_free(self):
        self.assertEqual(
            policy.HTH_NO_GESTURE_MASTERY, ruling("hth_casting_hands_free").value
        )

    def test_hth_disengage_leaves_every_partner(self):
        from .test_battle_rules_pass import runner_for
        from .test_state import make_combatant

        self.assertIs(ruling("hth_disengage_leaves_every_partner").value, True)
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0, hth_with=[2, 3]),
                make_combatant(2, q=1, r=0, facing=3, hth_with=[1]),
                make_combatant(3, q=0, r=1, facing=5, hth_with=[1]),
            ],
        )
        figure = state.by_id(1)
        figure.chosen_letter = "v"
        runner_for(state, [[2, 2, 2, 2]]).execute_action(figure)
        self.assertEqual([state.by_id(n).hth_with for n in (1, 2, 3)], [[], [], []])

    def test_ai_destinations(self):
        from tarmar_engine import movement

        from .test_engine import duel_state

        self.assertEqual(ruling("ai_destinations").value, "stand still")
        state = duel_state()
        decision = policy.choose_option(state, state.by_id(1))
        by_letter = {c.letter: c for c in decision.candidates}
        for letter in ("j", "k", "n"):
            self.assertIsNone(by_letter[letter].destination)
        step = movement.step_away_hex(state, state.by_id(1), state.by_id(2))
        self.assertIn(f"toward {step}", by_letter["n"].rationale)

    def test_shift_keeps_engagement(self):
        from tarmar_engine import movement

        from .test_engine import duel_state

        self.assertIs(ruling("shift_keeps_engagement").value, True)
        state = duel_state()
        figure = state.by_id(1)
        self.assertFalse(movement.keeps_engagement(state, figure, (-1, 0)))
        self.assertTrue(movement.keeps_engagement(state, figure, (0, 1)))

    def test_hth_agreement_by_game_master(self):
        from tarmar_engine import combat_math

        self.assertEqual(
            combat_math.GAME_MASTER_AGREEMENT,
            ruling("hth_agreement_by_game_master").value,
        )


class RunawayRulingsTest(TestCase):
    """tarmar-engine #30's readings, each pinned to the code that makes it."""

    def test_one_runaway_at_a_time(self):
        from .test_magic import cast
        from .test_runaway import (
            FAILING_RUNAWAY_PROFILE,
            RUNAWAY_TOTAL_FACES,
            running_state,
        )

        self.assertEqual(ruling("one_runaway_at_a_time").value, 1)
        state = running_state()
        first = state.by_id(1).runaway
        cast(
            state, [RUNAWAY_TOTAL_FACES], profile=FAILING_RUNAWAY_PROFILE, spell="heal"
        )
        self.assertIs(state.by_id(1).runaway, first)

    def test_runaway_drain_in_renew_phase(self):
        from .test_engine import ScriptedRoller
        from .test_runaway import RUNAWAY_PROFILE, running_state

        phase = ruling("runaway_drain_in_renew_phase").value
        self.assertEqual(dict(engine.PHASES)[phase], "Renew Spells")
        # Unconscious, the caster is still fed on.
        state = running_state(conscious=False)
        events: list[dict] = []
        runner = engine.TurnRunner(
            state, ScriptedRoller(), events.append, profile=RUNAWAY_PROFILE
        )
        runner.run(policy.choose_option)
        drains = [event for event in events if event["payload"].get("runaway_drain")]
        self.assertEqual([event["phase"] for event in drains], [phase])
        self.assertEqual(state.by_id(1).mana, 10 - 5)

    def test_runaway_effect_is_narrated_not_reapplied(self):
        from .test_magic import status_payloads
        from .test_runaway import feed, running_state

        keys = ruling("runaway_effect_is_narrated_not_reapplied").value
        state = running_state()
        events = feed(state)
        drain = status_payloads(events, "runaway_drain")[0]
        self.assertEqual((drain[keys[0]], drain[keys[1]]), (1, 2))
        # No effect is rolled or applied: the target is untouched.
        self.assertEqual(
            [event for event in events if event["event_type"] in ("roll", "damage")],
            [],
        )
        self.assertEqual(state.by_id(2).fatigue, 40)

    def test_anchor_takes_casting_penalties(self):
        from tarmar_engine import combat_math
        from tarmar_engine.profile import TARMAR

        from .test_runaway import anchor, anchor_roll, running_state, with_helper

        self.assertEqual(
            ruling("anchor_takes_casting_penalties").value,
            "injury band and off-balance",
        )
        injured = running_state(fatigue=1)
        injury = TARMAR.reactions.injury_penalty(injured.by_id(1))
        self.assertGreater(injury, 0)
        self.assertEqual(
            anchor_roll(anchor(injured, 1, [[8, 8]]))["target_number"], 12 - injury
        )
        unsteady = with_helper(running_state(), off_balance=True)
        self.assertEqual(
            anchor_roll(anchor(unsteady, 2, [[8, 8]]))["target_number"],
            13 - combat_math.OFF_BALANCE_PENALTY,
        )

    def test_ai_anchors_only_itself(self):
        from .test_runaway import runaway_magic_rules, running_state, with_helper

        self.assertEqual(ruling("ai_anchors_only_itself").value, "its own only")
        state = with_helper(running_state())
        rules = runaway_magic_rules()
        own = policy.choose_option(state, state.by_id(1), magic=rules)
        self.assertEqual(own.chosen.anchor_target, 1)
        other = policy.choose_option(state, state.by_id(2), magic=rules)
        self.assertIsNone(other.chosen.anchor_target)
        self.assertTrue(
            any(candidate.anchor_target == 1 for candidate in other.candidates)
        )

    def test_anchored_spell_becomes_the_anchorers(self):
        from tarmar_engine.state import RunawayState

        from .test_runaway import anchor, running_state, with_helper

        self.assertEqual(
            ruling("anchored_spell_becomes_the_anchorers").value,
            "CombatantState.active_spells",
        )
        state = with_helper(running_state(active_spells=["shield"]))
        state.by_id(1).runaway = RunawayState("shield", 1)
        anchor(state, 2, [[1, 1]])
        self.assertEqual(state.by_id(1).active_spells, [])
        self.assertEqual(state.by_id(2).active_spells, ["shield"])


class CounterspellRulingsTest(TestCase):
    """tarmar-engine #31's readings, each pinned to the code that makes it."""

    def test_counter_resolves_at_the_cast(self):
        from .test_counterspell import (
            CASTER_LOW,
            DEFENDER_HIGH,
            choose_counter,
            counter_rolls,
            counter_state,
            runner,
        )

        self.assertEqual(ruling("counter_resolves_at_the_cast").value, "at the cast")
        # The slower defender's counter is met at the faster caster's cast.
        state = counter_state(dexterity=8)
        choose_counter(state, 3)
        turn_runner, events = runner(state, [CASTER_LOW, DEFENDER_HIGH])
        turn_runner.phase_actions()
        caster_casts = next(
            event for event in events if "spell_countered" in event["payload"]
        )
        self.assertEqual(caster_casts["actor"], "Fighter 1")
        self.assertTrue(
            all(
                roll["sequence"] < caster_casts["sequence"]
                for roll in counter_rolls(events)
            )
        )

    def test_counter_names_a_caster(self):
        from .test_counterspell import counter_state, counters, decide

        self.assertEqual(
            ruling("counter_names_a_caster").value, "any mage against an enemy mage"
        )
        # Offered before the caster has chosen anything, and against a caster
        # aiming elsewhere: the counter names the caster.
        undecided = counter_state()
        undecided.by_id(1).chosen_letter = ""
        undecided.by_id(1).chosen_target = None
        self.assertEqual({c.target_id for c in counters(decide(undecided, 2))}, {1})
        self.assertEqual(counters(decide(counter_state(spells=[]), 2)), [])

    def test_ai_neither_counters_nor_escalates(self):
        from tarmar_engine.state import ContestState

        from .test_counterspell import counter_state, decide

        self.assertEqual(
            ruling("ai_neither_counters_nor_escalates").value, "player only"
        )
        defender = decide(counter_state(), 2)
        self.assertIsNone(defender.chosen.counter_mana)
        contested = counter_state()
        contested.by_id(1).contest = ContestState("fire_missile", 2, [2], 1)
        caster = decide(contested, 1)
        self.assertFalse(caster.chosen.escalate)
        self.assertTrue(any(candidate.escalate for candidate in caster.candidates))

    def test_committed_mana_is_spent(self):
        from .test_counterspell import choose_counter, counter_state, runner

        self.assertEqual(ruling("committed_mana_is_spent").value, "spent")
        state = counter_state()
        state.by_id(1).chosen_letter = "k"
        choose_counter(state, 2)
        runner(state)[0].phase_actions()
        self.assertEqual(state.by_id(2).mana, 6 - 2)
        self.assertIsNone(state.by_id(2).counter)

    def test_countered_cast_makes_no_casting_roll(self):
        from .test_counterspell import (
            CASTER_LOW,
            DEFENDER_HIGH,
            counter_state,
            countered_cast,
        )
        from .test_magic import roll_purposes

        self.assertEqual(
            ruling("countered_cast_makes_no_casting_roll").value,
            "no casting roll, mana paid",
        )
        state = counter_state()
        events = countered_cast(state, [CASTER_LOW, DEFENDER_HIGH], push=2)
        self.assertNotIn("casting", roll_purposes(events))
        self.assertEqual(state.by_id(1).mana, 10 - 1 - 2)

    def test_contest_ends_on_any_other_action(self):
        from tarmar_engine.state import ContestState

        from .test_counterspell import COUNTER_PROFILE, counter_state, runner

        reason = ruling("contest_ends_on_any_other_action").value
        state = counter_state()
        caster = state.by_id(1)
        caster.contest = ContestState("fire_missile", 2, [2], 1)
        turn_runner, events = runner(state)

        def dodge(battle, actor):
            return policy.Decision(
                chosen=policy.Candidate("c", "DODGE", 0.0, "test"), candidates=[]
            )

        self.assertIs(turn_runner.profile, COUNTER_PROFILE)
        turn_runner.phase_initial_movement([caster], dodge)
        self.assertIsNone(caster.contest)
        ended = next(event for event in events if "contest_ended" in event["payload"])
        self.assertEqual(ended["payload"]["reason"], reason)

    def test_defender_off_balance_not_on_counter(self):
        from .test_counterspell import (
            CASTER_LOW,
            DEFENDER_HIGH,
            choose_counter,
            counter_rolls,
            counter_state,
            runner,
        )

        self.assertEqual(
            ruling("defender_off_balance_not_on_counter").value, "injury band only"
        )
        # Faster and slower than the caster, an off-balance defender's side of
        # the opposed roll is its WIS 11 + 1 for 3 mana, with no penalty.
        for dexterity in (16, 8):
            state = counter_state(dexterity=dexterity, off_balance=True)
            choose_counter(state, 3)
            turn_runner, events = runner(state, [CASTER_LOW, DEFENDER_HIGH])
            turn_runner.phase_actions()
            with self.subTest(dexterity=dexterity):
                self.assertEqual(
                    counter_rolls(events)[1]["payload"]["modifier"], 11 + 1
                )
                self.assertFalse(state.by_id(2).off_balance)


class LiftedBoundsRulingsTest(TestCase):
    """The readings on a Game Master's lifted magic bounds (tarmar-studio
    #37), each pinned to the code that makes it."""

    def test_lifted_counter_minimum_is_zero(self):
        from tarmar_engine.magic import COUNTER_BOUNDS, LIFTED_COUNTER_MINIMUM

        from .test_counterspell import counter_state, counters, decide

        self.assertEqual(
            ruling("lifted_counter_minimum_is_zero").value, LIFTED_COUNTER_MINIMUM
        )
        state = counter_state()
        state.by_id(2).lifted_magic_bounds = [COUNTER_BOUNDS]
        offered = [candidate.counter_mana for candidate in counters(decide(state, 2))]
        self.assertEqual(offered, list(range(LIFTED_COUNTER_MINIMUM, 6 + 1)))

    def test_lifted_cast_names_its_channel(self):
        from tarmar_engine.magic import CHANNEL, LIFTED_BOUNDS_KEY

        from .test_channel import CHANNEL_PROFILE, cast_payload, channel_caster
        from .test_magic import cast

        self.assertEqual(
            ruling("lifted_cast_names_its_channel").value, LIFTED_BOUNDS_KEY
        )
        state = channel_caster(
            {"lightning_bolt": 1}, spells=["lightning_bolt"], intelligence=18
        )
        state.by_id(1).lifted_magic_bounds = [CHANNEL]
        payload = cast_payload(
            cast(
                state,
                [[3, 3, 3], [2, 2, 2], [3, 3, 3]],
                profile=CHANNEL_PROFILE,
                spell="lightning_bolt",
            )
        )
        self.assertEqual(payload["channel"], 1)
        self.assertEqual(payload[LIFTED_BOUNDS_KEY], [CHANNEL])

    def test_lifted_channel_bounds_the_shortfall(self):
        from tarmar_engine.magic import CHANNEL

        from .test_borrowing import channel_borrowing_rules, example_borrowing_rules
        from .test_channel import channel_caster

        self.assertEqual(
            ruling("lifted_channel_bounds_the_shortfall").value,
            "mana + Channel, when Borrowing has no debt cap",
        )
        rules = channel_borrowing_rules()
        # Channel 1 for the Bolt, 2 mana.
        caster = channel_caster({"lightning_bolt": 1}, mana=2).by_id(1)
        bolt = spells.get_spell("lightning_bolt")
        self.assertEqual(rules.channel_bound(caster, bolt, borrowing=True), 1)
        caster.lifted_magic_bounds = [CHANNEL]
        self.assertIsNone(rules.channel_bound(caster, bolt))
        self.assertEqual(rules.channel_bound(caster, bolt, borrowing=True), 2 + 1)
        capped = replace(rules, borrowing=example_borrowing_rules(max_debt=4))
        self.assertIsNone(capped.channel_bound(caster, bolt, borrowing=True))
