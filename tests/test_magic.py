"""Probe tests for the injected magic rules: casting success tiers
(tarmar-engine #26) and Push (tarmar-engine #27), split from tarmar-studio
#825.

Every number and name here is invented. The magic-mechanics pages are
DM-only and this repository is public, so the tests build their
:class:`MagicRules` from values that are plainly not the rulebook's — a 2d6
Control Roll against WIS, tiers on natural 6, 7 and 8 named "radiant",
"keen" and "tidy", bands named "wobble", "sputter" and "torrent" — and check
the engine's *structure*: each hook fires in the right phase and order, and
each injected value is the one the engine used. The real rules are built by
the caller that owns the canon.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine import combat_math, engine, policy
from tarmar_engine.magic import (
    CastingSuccessTier,
    ControlBand,
    MagicRules,
    PushRules,
    SpellEffectKind,
)
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.state import BattleState

from .test_engine import ScriptedRoller, events_of_type
from .test_state import make_combatant

WOBBLE = ControlBand(
    key="wobble",
    label="wobble",
    is_runaway=False,
    lowest_margin=1,
    highest_margin=2,
    spell_takes_effect=True,
    push_bonus_applies=False,
)
SPUTTER = ControlBand(
    key="sputter",
    label="sputter",
    is_runaway=False,
    lowest_margin=3,
    highest_margin=4,
    spell_takes_effect=False,
    push_bonus_applies=False,
)
TORRENT = ControlBand(
    key="torrent",
    label="torrent",
    is_runaway=True,
    lowest_margin=5,
    highest_margin=None,
    spell_takes_effect=False,
    push_bonus_applies=False,
)


def example_push_rules() -> PushRules:
    """Invented Push rules for the tests. Not Tarmar's numbers."""
    return PushRules(
        control_dice="2d6",
        control_attribute="WIS",
        penalty_per_mana=2,
        base_cost_counts_as_invested=False,
        failure_bands=(WOBBLE, SPUTTER, TORRENT),
        automatic_runaway_totals=frozenset({2}),
        effect_bonus_per_mana_by_kind={
            SpellEffectKind.DAMAGE: 4,
            SpellEffectKind.HEALING: 3,
        },
        max_push_mana=3,
        spell_effect_bonus_per_mana={"heal": 1},
    )


def example_magic_rules() -> MagicRules:
    """Invented magic rules for the tests. Not Tarmar's numbers."""
    return MagicRules(
        casting_success_tiers=(
            CastingSuccessTier(
                key="radiant",
                label="radiant cast",
                natural_totals=frozenset({6}),
                effect_bonus=5,
            ),
            CastingSuccessTier(
                key="keen",
                label="keen cast",
                natural_totals=frozenset({7}),
                effect_bonus=2,
                mana_refund=1,
            ),
            CastingSuccessTier(
                key="tidy",
                label="tidy cast",
                natural_totals=frozenset({8}),
            ),
        ),
        push=example_push_rules(),
    )


MAGIC_PROFILE = TarmarProfile(magic=example_magic_rules())


def profile_with(**push_changes) -> TarmarProfile:
    """The example profile with some Push fields changed."""
    return TarmarProfile(
        magic=replace(
            example_magic_rules(), push=replace(example_push_rules(), **push_changes)
        )
    )


def wizard_state(**caster_overrides) -> BattleState:
    """A caster (INT 14, WIS 12, 10 mana) and a target well out of reach."""
    caster_fields = {
        "q": -3,
        "r": 0,
        "facing": 0,
        "intelligence": 14,
        "wisdom": 12,
        "spells": ["fire_missile", "shield", "heal"],
        "mana": 10,
        "max_mana": 10,
    }
    caster_fields.update(caster_overrides)
    return BattleState(
        arena_radius=8,
        combatants=[
            make_combatant(1, **caster_fields),
            make_combatant(2, q=3, r=0, facing=3),
        ],
    )


def cast(state, faces, *, profile=MAGIC_PROFILE, spell="fire_missile", push=0):
    """Cast the caster's spell at the target with scripted dice; the events."""
    caster = state.by_id(1)
    caster.chosen_spell = spell
    caster.chosen_target = 2
    caster.chosen_push_mana = push
    events: list[dict] = []
    runner = engine.TurnRunner(
        state, ScriptedRoller(faces), events.append, profile=profile
    )
    runner.cast_spell(caster)
    return events


def roll_purposes(events) -> list[str]:
    return [event["payload"]["purpose"] for event in events_of_type(events, "roll")]


def status_payloads(events, key) -> list[dict]:
    return [
        event["payload"]
        for event in events_of_type(events, "status")
        if key in event["payload"]
    ]


def control_payload(events) -> dict:
    return status_payloads(events, "control_band")[0]


class NoMagicRulesTest(TestCase):
    """The default profile carries no magic numbers and logs as it always has."""

    def test_the_default_profile_injects_no_magic_rules(self):
        self.assertIsNone(TARMAR.magic)
        self.assertIsNone(TarmarProfile().magic)
        self.assertEqual(MagicRules(), MagicRules((), None))

    def test_a_cast_without_magic_rules_has_no_tier_and_the_old_payload(self):
        state = wizard_state()
        events = cast(state, [[2, 2, 2], [2, 2, 2], [5]], profile=TARMAR)
        action = events_of_type(events, "action")[0]
        self.assertEqual(
            set(action["payload"]),
            {"spell", "success", "mana_left", "casting_roll"},
        )
        self.assertEqual(status_payloads(events, "success_tier"), [])
        self.assertEqual(state.by_id(2).fatigue, state.by_id(2).max_fatigue - 5)

    def test_rules_without_push_add_no_push_key(self):
        tiers_only = TarmarProfile(magic=replace(example_magic_rules(), push=None))
        events = cast(wizard_state(), [[2, 2, 2], [2, 2, 2], [5]], profile=tiers_only)
        action = events_of_type(events, "action")[0]
        self.assertNotIn("push_mana", action["payload"])
        self.assertEqual(len(status_payloads(events, "success_tier")), 1)

    def test_a_push_with_no_push_rules_is_refused(self):
        with self.assertRaises(ValueError):
            cast(wizard_state(), [[2, 2, 2]], profile=TARMAR, push=1)


class CastingSuccessTierTest(TestCase):
    """#26: the injected tier table, keyed by the casting roll's natural total."""

    def test_an_injected_tier_is_narrated_after_the_casting_roll(self):
        events = cast(wizard_state(), [[2, 2, 2], [2, 2, 2], [5]])
        tiers = status_payloads(events, "success_tier")
        self.assertEqual(len(tiers), 1)
        self.assertEqual(tiers[0]["success_tier"], "radiant")
        self.assertEqual(tiers[0]["natural_total"], 6)
        casting = events_of_type(events, "roll")[0]
        tier_event = next(
            event
            for event in events
            if event["payload"].get("success_tier") == "radiant"
        )
        self.assertGreater(tier_event["sequence"], casting["sequence"])
        self.assertIn("radiant cast", tier_event["message"])

    def test_the_tier_effect_bonus_adds_to_the_rolled_damage(self):
        state = wizard_state()
        # Radiant (+5) on the invented table; damage 1d6 rolls 5 -> 10.
        events = cast(state, [[2, 2, 2], [2, 2, 2], [5]])
        defender = state.by_id(2)
        self.assertEqual(defender.fatigue, defender.max_fatigue - 10)
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 10)

    def test_the_tier_mana_refund_is_paid_back(self):
        state = wizard_state()
        # Keen (natural 7): +2 effect and 1 mana back on a level-1 spell.
        events = cast(state, [[2, 2, 3], [2, 2, 2], [5]])
        self.assertEqual(state.by_id(1).mana, 10)
        tier = status_payloads(events, "success_tier")[0]
        self.assertEqual((tier["success_tier"], tier["mana_refund"]), ("keen", 1))
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 7)

    def test_a_tier_with_no_bonus_is_narration_only(self):
        events = cast(wizard_state(), [[2, 3, 3], [2, 2, 2], [5]])
        tier = status_payloads(events, "success_tier")[0]
        self.assertEqual((tier["success_tier"], tier["effect_bonus"]), ("tidy", 0))
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 5)

    def test_the_tier_table_is_the_injected_one_not_a_built_in(self):
        # A natural 3 is outside the invented table, so no tier fires.
        events = cast(wizard_state(), [[1, 1, 1], [2, 2, 2], [5]])
        self.assertEqual(status_payloads(events, "success_tier"), [])
        moved = replace(
            example_magic_rules(),
            casting_success_tiers=(
                CastingSuccessTier("radiant", "radiant", frozenset({3}), 1),
            ),
        )
        events = cast(
            wizard_state(),
            [[1, 1, 1], [2, 2, 2], [5]],
            profile=TarmarProfile(magic=moved),
        )
        self.assertEqual(status_payloads(events, "success_tier")[0]["natural_total"], 3)

    def test_a_tier_never_rescues_a_failed_roll(self):
        events = cast(wizard_state(intelligence=5), [[2, 2, 2]])
        self.assertIn("failure", events_of_type(events, "action")[0]["message"])
        self.assertEqual(status_payloads(events, "success_tier"), [])

    def test_a_continuing_spell_takes_the_tier_but_reports_no_bonus(self):
        state = wizard_state()
        events = cast(state, [[2, 2, 2]], spell="shield")
        self.assertEqual(state.by_id(1).active_spells, ["shield"])
        tier = status_payloads(events, "success_tier")[0]
        self.assertEqual((tier["success_tier"], tier["effect_bonus"]), ("radiant", 0))
        self.assertFalse(
            any(
                "gains" in event["message"]
                for event in events_of_type(events, "status")
            )
        )

    def test_a_tier_bonus_adds_to_healing(self):
        state = wizard_state()
        caster = state.by_id(1)
        caster.fatigue = caster.max_fatigue - 20
        cast(state, [[2, 2, 2], [3]], spell="heal")
        self.assertEqual(caster.fatigue, caster.max_fatigue - 20 + 3 + 5)


class PushTest(TestCase):
    """#27: the pushed cast, its Control Roll and its injected bands."""

    def test_a_held_push_rolls_control_after_casting_and_before_aim(self):
        # Casting 9 (no tier), control 2d6 = 6 vs WIS 12 - 2 x 2 = 8: held.
        events = cast(wizard_state(), [[3, 3, 3], [3, 3], [2, 2, 2], [5]], push=2)
        self.assertEqual(
            roll_purposes(events), ["casting", "control", "spell aim", "spell damage"]
        )
        control = events_of_type(events, "roll")[1]
        self.assertEqual(control["payload"]["target_number"], 8)
        self.assertIn("vs WIS 8", control["message"])
        self.assertIn("2d6", control["message"])
        outcome = control_payload(events)
        self.assertTrue(outcome["held"])
        self.assertIsNone(outcome["control_band"])
        self.assertEqual((outcome["push_mana"], outcome["mana_invested"]), (2, 2))

    def test_a_held_push_pays_its_mana_and_adds_its_bonus(self):
        state = wizard_state()
        events = cast(state, [[3, 3, 3], [3, 3], [2, 2, 2], [5]], push=2)
        self.assertEqual(state.by_id(1).mana, 10 - 1 - 2)
        action = events_of_type(events, "action")[0]
        self.assertEqual(action["payload"]["push_mana"], 2)
        self.assertIn("1 + 2 pushed mana", action["message"])
        # Damage kind: 4 per pushed mana x 2 = +8 on a damage roll of 5.
        self.assertEqual(control_payload(events)["push_bonus"], 8)
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 13)

    def test_the_spell_cost_counts_as_invested_when_the_rules_say_so(self):
        events = cast(
            wizard_state(),
            [[3, 3, 3], [1, 2], [2, 2, 2], [5]],
            profile=profile_with(base_cost_counts_as_invested=True),
            push=2,
        )
        # WIS 12 - 2 x (1 + 2) = 6.
        self.assertEqual(
            events_of_type(events, "roll")[1]["payload"]["target_number"], 6
        )

    def test_a_per_spell_override_sets_the_push_bonus(self):
        state = wizard_state()
        caster = state.by_id(1)
        caster.fatigue = caster.max_fatigue - 20
        cast(state, [[3, 3, 3], [3, 3], [3]], spell="heal", push=2)
        self.assertEqual(caster.fatigue, caster.max_fatigue - 20 + 3 + 2)

    def test_the_kind_bonus_applies_without_an_override(self):
        state = wizard_state()
        caster = state.by_id(1)
        caster.fatigue = caster.max_fatigue - 20
        cast(
            state,
            [[3, 3, 3], [3, 3], [3]],
            spell="heal",
            push=2,
            profile=profile_with(spell_effect_bonus_per_mana={}),
        )
        # Healing kind: 3 per pushed mana x 2.
        self.assertEqual(caster.fatigue, caster.max_fatigue - 20 + 3 + 6)

    def test_a_spell_with_no_rolled_effect_cannot_be_pushed(self):
        with self.assertRaises(ValueError):
            cast(wizard_state(), [[3, 3, 3]], spell="shield", push=1)

    def test_a_kind_the_rules_give_no_bonus_cannot_be_pushed(self):
        damage_only = profile_with(
            effect_bonus_per_mana_by_kind={SpellEffectKind.DAMAGE: 4},
            spell_effect_bonus_per_mana={},
        )
        with self.assertRaises(ValueError):
            cast(wizard_state(), [[3, 3, 3]], spell="heal", push=1, profile=damage_only)

    def test_a_band_that_takes_effect_without_the_push_bonus(self):
        # Control 10 vs 8: margin 2, the invented wobble band.
        events = cast(wizard_state(), [[3, 3, 3], [5, 5], [2, 2, 2], [5]], push=2)
        outcome = control_payload(events)
        self.assertEqual(outcome["control_band"], "wobble")
        self.assertEqual(outcome["control_margin"], 2)
        self.assertEqual(outcome["push_bonus"], 0)
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 5)

    def test_a_band_that_takes_effect_with_the_push_bonus(self):
        generous = replace(WOBBLE, push_bonus_applies=True)
        events = cast(
            wizard_state(),
            [[3, 3, 3], [5, 5], [2, 2, 2], [5]],
            push=2,
            profile=profile_with(failure_bands=(generous, SPUTTER, TORRENT)),
        )
        outcome = control_payload(events)
        self.assertEqual(
            (outcome["control_band"], outcome["push_bonus"]), ("wobble", 8)
        )
        self.assertTrue(outcome["takes_effect"])
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 13)

    def test_a_band_that_stops_the_spell_keeps_the_mana_paid(self):
        state = wizard_state()
        # Control 11 vs 8: margin 3, the invented sputter band.
        events = cast(state, [[3, 3, 3], [6, 5]], push=2)
        outcome = control_payload(events)
        self.assertEqual(outcome["control_band"], "sputter")
        self.assertFalse(outcome["takes_effect"])
        self.assertEqual(roll_purposes(events), ["casting", "control"])
        self.assertEqual(state.by_id(1).mana, 7)

    def test_a_wide_failure_lands_in_the_runaway_band(self):
        # Control 12 vs 10 - 4 = 6: margin 6, the invented open-ended band.
        events = cast(wizard_state(wisdom=10), [[3, 3, 3], [6, 6]], push=2)
        outcome = control_payload(events)
        self.assertEqual(outcome["control_band"], "torrent")
        self.assertTrue(outcome["runaway"])
        self.assertIn("runs away", events_of_type(events, "status")[-1]["message"])

    def test_an_automatic_runaway_total_ignores_the_target(self):
        # Natural 2 on the invented list, though 2 <= 8 would hold.
        events = cast(wizard_state(), [[3, 3, 3], [1, 1]], push=2)
        outcome = control_payload(events)
        self.assertEqual(
            (outcome["control_band"], outcome["runaway"]), ("torrent", True)
        )
        self.assertEqual(roll_purposes(events), ["casting", "control"])

    def test_a_failed_casting_roll_rolls_no_control(self):
        state = wizard_state(intelligence=8)
        events = cast(state, [[4, 4, 4]], push=2)
        self.assertEqual(roll_purposes(events), ["casting"])
        self.assertEqual(state.by_id(1).mana, 10)

    def test_the_pushed_mana_must_be_affordable(self):
        events = cast(wizard_state(mana=2), [], push=2)
        self.assertIn("lacks the mana", events_of_type(events, "info")[0]["message"])
        self.assertEqual(roll_purposes(events), [])

    def test_a_push_past_the_injected_cap_is_refused(self):
        with self.assertRaises(ValueError):
            cast(wizard_state(), [[3, 3, 3]], push=4)

    def test_no_cap_lets_the_purse_decide(self):
        events = cast(
            wizard_state(wisdom=30),
            [[3, 3, 3], [3, 3], [2, 2, 2], [5]],
            push=6,
            profile=profile_with(max_push_mana=None),
        )
        self.assertTrue(control_payload(events)["held"])

    def test_a_tier_and_a_push_stack(self):
        # Radiant (+5) and a held push of 1 (+4) on a damage roll of 5.
        events = cast(wizard_state(), [[2, 2, 2], [3, 3], [2, 2, 2], [5]], push=1)
        self.assertEqual(status_payloads(events, "effect_bonus")[-1]["effect_bonus"], 9)
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 14)


class PushMenuTest(TestCase):
    """#27: the pushed variants on the menu, for a player to choose."""

    def pushed(self, state, rules=None) -> list:
        decision = policy.choose_option(
            state, state.by_id(1), magic=rules or example_magic_rules()
        )
        return [candidate for candidate in decision.candidates if candidate.push_mana]

    def test_pushed_variants_follow_the_cap_and_the_purse(self):
        pairs = [
            (candidate.spell_key, candidate.push_mana)
            for candidate in self.pushed(wizard_state(mana=3))
        ]
        self.assertIn(("fire_missile", 1), pairs)
        self.assertIn(("fire_missile", 2), pairs)
        self.assertNotIn(("fire_missile", 3), pairs)
        rich = self.pushed(wizard_state(mana=10))
        self.assertEqual(max(candidate.push_mana for candidate in rich), 3)

    def test_no_cap_offers_everything_the_purse_holds(self):
        uncapped = replace(
            example_magic_rules(),
            push=replace(example_push_rules(), max_push_mana=None),
        )
        offered = self.pushed(wizard_state(mana=10), uncapped)
        self.assertEqual(max(candidate.push_mana for candidate in offered), 9)

    def test_a_spell_with_no_rolled_effect_gets_no_pushed_variant(self):
        offered = {candidate.spell_key for candidate in self.pushed(wizard_state())}
        self.assertNotIn("shield", offered)
        self.assertIn("fire_missile", offered)

    def test_the_ai_never_picks_a_push_and_its_choice_is_unchanged(self):
        state = wizard_state()
        plain = policy.choose_option(state, state.by_id(1))
        with_push = policy.choose_option(
            state, state.by_id(1), magic=example_magic_rules()
        )
        self.assertEqual(with_push.chosen, plain.chosen)
        self.assertEqual(
            with_push.candidates[: len(plain.candidates)], plain.candidates
        )
        for candidate in with_push.candidates[len(plain.candidates) :]:
            self.assertEqual(candidate.score, policy.PLAYER_ONLY_SCORE)
            self.assertEqual(candidate.to_payload()["push_mana"], candidate.push_mana)
            self.assertIn("2d6", candidate.rationale)
        self.assertNotIn("push_mana", plain.chosen.to_payload())

    def test_the_rationale_target_carries_injury_and_off_balance(self):
        def menu_target(state) -> int:
            first = next(
                candidate
                for candidate in self.pushed(state)
                if candidate.spell_key == "fire_missile" and candidate.push_mana == 1
            )
            return int(first.rationale.split(";")[0].rsplit("= ", 1)[1])

        # Fatigue 1 is in the injury band: the menu's target is the rolled one.
        injured = wizard_state(fatigue=1)
        events = cast(injured, [[3, 3, 3], [3, 3], [2, 2, 2], [5]], push=1)
        rolled_target = events_of_type(events, "roll")[1]["payload"]["target_number"]
        self.assertEqual(menu_target(wizard_state(fatigue=1)), rolled_target)
        self.assertLess(rolled_target, 12 - 2)
        # An owed off-balance penalty lowers it by the engine's own amount.
        off_balance = wizard_state(fatigue=1, off_balance=True)
        self.assertEqual(
            menu_target(off_balance),
            rolled_target - combat_math.OFF_BALANCE_PENALTY,
        )

    def test_no_push_rules_no_pushed_variants(self):
        self.assertEqual(self.pushed(wizard_state(), MagicRules(push=None)), [])


class PurposeRoller(ScriptedRoller):
    """Scripted faces per roll purpose, so a full turn's other dice cannot
    shift which faces the casting and control rolls get."""

    def __init__(self, faces_by_purpose: dict[str, list[list[int]]]):
        super().__init__()
        self.faces_by_purpose = {
            purpose: list(queue) for purpose, queue in faces_by_purpose.items()
        }

    def roll(
        self, specification, *, purpose, modifier=0, target_number=None, outcome=None
    ):
        queue = self.faces_by_purpose.get(purpose, [])
        self.faces_queue = [queue.pop(0)] if queue else []
        return super().roll(
            specification,
            purpose=purpose,
            modifier=modifier,
            target_number=target_number,
            outcome=outcome,
        )


class MagicTurnPhaseTest(TestCase):
    """The hooks fire in the right phases of a full turn."""

    def run_pushed_turn(self, state):
        rules = example_magic_rules()

        def choose(battle, actor):
            decision = policy.choose_option(battle, actor, magic=rules)
            if actor.combatant_id != 1:
                return decision
            pushed = next(
                candidate
                for candidate in decision.candidates
                if candidate.spell_key == "fire_missile" and candidate.push_mana == 1
            )
            return policy.Decision(chosen=pushed, candidates=decision.candidates)

        events: list[dict] = []
        roller = PurposeRoller(
            {
                "casting": [[2, 2, 2]],
                "control": [[3, 3]],
                "spell aim": [[2, 2, 2]],
                "spell damage": [[5]],
            }
        )
        engine.run_turn(
            state, roller, events.append, choose, profile=TarmarProfile(magic=rules)
        )
        return events

    def test_the_push_is_chosen_in_phase_3_and_resolved_in_phase_5(self):
        state = wizard_state()
        events = self.run_pushed_turn(state)
        decision = next(
            event
            for event in events_of_type(events, "decision")
            if event["actor"] == "Fighter 1"
        )
        self.assertEqual(decision["phase"], 3)
        self.assertEqual(decision["payload"]["chosen"]["push_mana"], 1)
        tier = next(event for event in events if event["payload"].get("success_tier"))
        control = next(event for event in events if "control_band" in event["payload"])
        self.assertEqual((tier["phase"], control["phase"]), (5, 5))
        self.assertLess(tier["sequence"], control["sequence"])
        self.assertEqual(state.by_id(1).mana, 10 - 1 - 1)
        # Radiant +5, held push of 1 x 4 = +9 on a damage roll of 5.
        self.assertEqual(events_of_type(events, "damage")[0]["payload"]["raw"], 14)

    def test_the_push_choice_lasts_one_turn(self):
        state = wizard_state()
        self.run_pushed_turn(state)
        self.assertEqual(state.by_id(1).chosen_push_mana, 1)
        state.by_id(1).reset_for_turn()
        self.assertEqual(state.by_id(1).chosen_push_mana, 0)


class MagicRulesValidationTest(TestCase):
    """A malformed injected table is refused when it is built, not mid-battle."""

    def test_tiers_may_not_share_a_natural_total(self):
        with self.assertRaises(ValueError):
            MagicRules(
                casting_success_tiers=(
                    CastingSuccessTier("first", "first", frozenset({6})),
                    CastingSuccessTier("second", "second", frozenset({6, 7})),
                )
            )

    def test_bands_must_run_contiguously_from_one(self):
        with self.assertRaises(ValueError):
            replace(example_push_rules(), failure_bands=(SPUTTER, TORRENT))
        with self.assertRaises(ValueError):
            replace(example_push_rules(), failure_bands=(WOBBLE, SPUTTER))

    def test_only_the_last_band_is_open_ended(self):
        open_wobble = replace(WOBBLE, highest_margin=None)
        with self.assertRaises(ValueError):
            replace(
                example_push_rules(),
                failure_bands=(open_wobble, replace(TORRENT, lowest_margin=2)),
            )

    def test_an_automatic_runaway_needs_a_runaway_band(self):
        open_wobble = replace(WOBBLE, highest_margin=None)
        with self.assertRaises(ValueError):
            replace(example_push_rules(), failure_bands=(open_wobble,))
        replace(
            example_push_rules(),
            failure_bands=(open_wobble,),
            automatic_runaway_totals=frozenset(),
        )

    def test_the_control_attribute_dice_and_cap_are_checked(self):
        with self.assertRaises(ValueError):
            replace(example_push_rules(), control_attribute="STR")
        with self.assertRaises(ValueError):
            replace(example_push_rules(), control_dice="lots")
        with self.assertRaises(ValueError):
            replace(example_push_rules(), max_push_mana=-1)
