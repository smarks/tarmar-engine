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

    def test_the_pass_rulings(self):
        self.assertEqual(
            engine.CASTING_MANA_LOST_ROLLS, ruling("mana_on_failure").value
        )
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
        from .test_magic import cast, wizard_state

        self.assertEqual(ruling("pushed_mana_paid_with_cost").value, "with the cost")
        state = wizard_state(intelligence=18)
        cast(state, [[6, 6, 5]], push=2)  # 17: fails and loses its mana
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
