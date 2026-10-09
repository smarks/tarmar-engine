"""Probe tests for injected Channel (tarmar-engine #29, split from
tarmar-studio #825).

Every number and name here is invented, as in ``test_magic``: two kinds of
magic called "loom" and "weft", a general skill called "tapestry", and
derivation coefficients that are plainly not the rulebook's. The tests check
the engine's *structure*: Channel derives per caster and per spell from the
levels on the snapshot, bounds the whole mana of one casting, trims the
menu, and is named in the narration and the payload.
"""

from dataclasses import replace
from functools import partial
from unittest import TestCase

from tarmar_engine import engine, policy
from tarmar_engine.magic import (
    UNRECORDED_SKILL_LEVEL,
    ChannelDerivation,
    ChannelRules,
    ChannelUnclassifiedSpell,
    MagicRules,
)
from tarmar_engine.profile import TarmarProfile
from tarmar_engine.spells import get_spell
from tarmar_engine.state import BattleState, WeaponState

from .test_engine import events_of_type
from .test_magic import (
    PurposeRoller,
    cast,
    control_payload,
    example_magic_rules,
    example_push_rules,
    wizard_state,
)

LOOM = ChannelDerivation(source_skill=None, multiplier=2, offset=-1)
WEFT = ChannelDerivation(source_skill="tapestry", multiplier=1, offset=2)


def example_channel_rules() -> ChannelRules:
    """Invented Channel rules for the tests. Not Tarmar's numbers."""
    return ChannelRules(
        derivations={"loom": LOOM, "weft": WEFT},
        kind_by_spell={"heal": "weft"},
        default_kind="loom",
    )


def channel_magic_rules(**push_changes) -> MagicRules:
    """The example magic rules with Channel, and some Push fields changed."""
    return replace(
        example_magic_rules(),
        push=replace(example_push_rules(), **push_changes),
        channel=example_channel_rules(),
    )


CHANNEL_PROFILE = TarmarProfile(magic=channel_magic_rules())


def channel_caster(spell_levels: dict[str, int], **overrides) -> BattleState:
    """The test wizard, with recorded spell levels and a "tapestry" of 4."""
    fields = {"spell_skill_levels": dict(spell_levels), "skill_levels": {}}
    fields.update(overrides)
    return wizard_state(**fields)


def cast_payload(events) -> dict:
    return next(
        event["payload"]
        for event in events_of_type(events, "action")
        if "casting_roll" in event["payload"]
    )


class ChannelDerivationTest(TestCase):
    def test_a_per_spell_kind_reads_the_spell_level(self):
        caster = channel_caster({"fire_missile": 3}).by_id(1)
        rules = example_channel_rules()
        # loom: 2 x 3 - 1.
        self.assertEqual(rules.channel_for(caster, get_spell("fire_missile")), 5)
        self.assertEqual(rules.kind_for(get_spell("fire_missile")), "loom")

    def test_a_general_skill_kind_reads_that_skill_for_every_spell(self):
        caster = channel_caster({"heal": 9}, skill_levels={"tapestry": 4}).by_id(1)
        rules = replace(
            example_channel_rules(), kind_by_spell={"heal": "weft", "shield": "weft"}
        )
        # weft: 1 x 4 + 2, whatever the spell's own level.
        self.assertEqual(rules.channel_for(caster, get_spell("heal")), 6)
        self.assertEqual(rules.channel_for(caster, get_spell("shield")), 6)

    def test_an_unrecorded_level_reads_as_the_engine_default(self):
        caster = channel_caster({}).by_id(1)
        rules = example_channel_rules()
        expected = max(0, 2 * UNRECORDED_SKILL_LEVEL - 1)
        self.assertEqual(rules.channel_for(caster, get_spell("fire_missile")), expected)

    def test_channel_never_goes_below_zero(self):
        self.assertEqual(LOOM.channel_at(0), 0)

    def test_a_spell_with_no_kind_is_an_error(self):
        rules = replace(example_channel_rules(), default_kind=None)
        caster = channel_caster({"fire_missile": 3}).by_id(1)
        with self.assertRaises(ValueError):
            rules.channel_for(caster, get_spell("fire_missile"))
        self.assertEqual(rules.channel_for(caster, get_spell("heal")), 2)

    def test_malformed_rules_are_refused_when_built(self):
        with self.assertRaises(ValueError):
            ChannelRules(derivations={})
        with self.assertRaises(ValueError):
            ChannelRules(derivations={"loom": LOOM}, kind_by_spell={"heal": "warp"})
        with self.assertRaises(ValueError):
            ChannelRules(derivations={"loom": LOOM}, default_kind="warp")
        with self.assertRaises(ValueError):
            ChannelDerivation(source_skill=None, multiplier=-1, offset=0)
        with self.assertRaises(ValueError):
            ChannelDerivation(source_skill="", multiplier=1, offset=0)

    def test_magic_rules_without_channel_bound_nothing(self):
        caster = channel_caster({"fire_missile": 3}).by_id(1)
        spell = get_spell("fire_missile")
        self.assertIsNone(example_magic_rules().channel_for(caster, spell))
        self.assertEqual(channel_magic_rules().channel_for(caster, spell), 5)

    def test_the_levels_ride_the_snapshot(self):
        state = channel_caster({"fire_missile": 3}, skill_levels={"tapestry": 4})
        restored = BattleState.from_dict(state.to_dict())
        self.assertEqual(restored.by_id(1).spell_skill_levels, {"fire_missile": 3})
        self.assertEqual(restored.by_id(1).skill_levels, {"tapestry": 4})
        older = state.to_dict()
        for entry in older["combatants"]:
            del entry["spell_skill_levels"]
            del entry["skill_levels"]
        loaded = BattleState.from_dict(older).by_id(1)
        self.assertEqual((loaded.spell_skill_levels, loaded.skill_levels), ({}, {}))


class ChannelCastTest(TestCase):
    """The casting phase: the whole casting within the Channel, named."""

    def test_a_cast_names_the_channel_it_used(self):
        events = cast(
            channel_caster({"fire_missile": 3}),
            [[3, 3, 3], [2, 2, 2], [5]],
            profile=CHANNEL_PROFILE,
        )
        payload = cast_payload(events)
        self.assertEqual((payload["channel"], payload["channel_kind"]), (5, "loom"))
        message = events_of_type(events, "action")[0]["message"]
        self.assertIn("Channel 5", message)

    def test_a_pushed_cast_up_to_the_channel_runs(self):
        # Level 2 recorded: Channel 3, so 1 + 2 pushed fills it exactly.
        state = channel_caster({"fire_missile": 2}, wisdom=30)
        events = cast(
            state,
            [[3, 3, 3], [3, 3], [2, 2, 2], [5]],
            profile=CHANNEL_PROFILE,
            push=2,
        )
        self.assertTrue(control_payload(events)["held"])
        self.assertEqual(state.by_id(1).mana, 10 - 3)

    def test_a_push_past_the_channel_is_refused_under_the_push_cap(self):
        # Channel 3; Push's own cap (3) would allow this push of 3.
        state = channel_caster({"fire_missile": 2})
        with self.assertRaises(ValueError) as raised:
            cast(state, [[3, 3, 3]], profile=CHANNEL_PROFILE, push=3)
        self.assertIn("Channel", str(raised.exception))
        self.assertEqual(state.by_id(1).mana, 10)

    def test_the_push_cap_still_holds_beneath_a_wider_channel(self):
        # Channel 9 would allow it; Push's own cap of 3 does not.
        state = channel_caster({"fire_missile": 5})
        with self.assertRaises(ValueError) as raised:
            cast(state, [[3, 3, 3]], profile=CHANNEL_PROFILE, push=4)
        self.assertIn("cap", str(raised.exception))

    def test_a_spell_costing_more_than_the_channel_cannot_be_cast(self):
        # Lightning Bolt costs 3; recorded level 1 gives Channel 1.
        state = channel_caster(
            {"lightning_bolt": 1}, spells=["lightning_bolt"], intelligence=18
        )
        with self.assertRaises(ValueError):
            cast(state, [[3, 3, 3]], profile=CHANNEL_PROFILE, spell="lightning_bolt")

    def test_rules_without_channel_add_no_channel_key(self):
        events = cast(
            channel_caster({"fire_missile": 3}),
            [[3, 3, 3], [2, 2, 2], [5]],
        )
        self.assertNotIn("channel", cast_payload(events))
        self.assertNotIn("Channel", events_of_type(events, "action")[0]["message"])


class ChannelMenuTest(TestCase):
    """The menu offers no cast and no push past the Channel."""

    def menu(self, state, rules=None) -> policy.Decision:
        return policy.choose_option(
            state, state.by_id(1), magic=rules or channel_magic_rules()
        )

    def test_pushed_variants_stop_at_the_channel(self):
        # Channel 3 for Fire Missile (level 1): pushes of 1 and 2, though the
        # purse holds 10 and Push's own cap is 3.
        decision = self.menu(channel_caster({"fire_missile": 2}))
        pushes = sorted(
            candidate.push_mana
            for candidate in decision.candidates
            if candidate.spell_key == "fire_missile" and candidate.push_mana
        )
        self.assertEqual(pushes, [1, 2])
        rationale = next(
            candidate.rationale
            for candidate in decision.candidates
            if candidate.push_mana == 1 and candidate.spell_key == "fire_missile"
        )
        self.assertIn("Channel 3", rationale)

    def test_the_push_cap_still_trims_beneath_a_wider_channel(self):
        decision = self.menu(channel_caster({"fire_missile": 5}))
        self.assertEqual(
            max(
                candidate.push_mana
                for candidate in decision.candidates
                if candidate.spell_key == "fire_missile"
            ),
            3,
        )

    def test_a_cast_past_the_channel_leaves_the_menu_and_the_ai_choice(self):
        # Unarmed, so with no Channel rules the AI's best option is the cast.
        state = channel_caster({}, spells=["fire_missile"], weapon=WeaponState())
        plain = policy.choose_option(state, state.by_id(1))
        self.assertEqual(plain.chosen.spell_key, "fire_missile")
        bounded = self.menu(state)
        self.assertFalse(
            [candidate for candidate in bounded.candidates if candidate.spell_key]
        )
        self.assertEqual(bounded.chosen.spell_key, "")
        expected = max(
            (candidate for candidate in plain.candidates if not candidate.spell_key),
            key=lambda candidate: candidate.score,
        )
        self.assertEqual(bounded.chosen, expected)

    def test_a_cast_within_the_channel_keeps_the_ai_choice(self):
        state = channel_caster({"fire_missile": 3, "shield": 3, "heal": 3})
        plain = policy.choose_option(state, state.by_id(1))
        bounded = self.menu(state)
        self.assertEqual(bounded.chosen, plain.chosen)
        self.assertEqual(bounded.candidates[: len(plain.candidates)], plain.candidates)

    def test_a_turn_with_the_channel_on_the_menu_never_casts_past_it(self):
        def turn_purposes(rules: MagicRules) -> list[str]:
            state = channel_caster({}, spells=["fire_missile"], weapon=WeaponState())
            events: list[dict] = []
            engine.run_turn(
                state,
                PurposeRoller({}),
                events.append,
                partial(policy.choose_option, magic=rules),
                profile=TarmarProfile(magic=rules),
            )
            return [
                event["payload"]["purpose"] for event in events_of_type(events, "roll")
            ]

        self.assertIn("casting", turn_purposes(example_magic_rules()))
        self.assertNotIn("casting", turn_purposes(channel_magic_rules()))

    def test_a_cast_past_the_channel_is_withheld_with_its_reason(self):
        state = channel_caster({}, spells=["fire_missile"], weapon=WeaponState())
        withheld = self.menu(state).withheld
        self.assertEqual(len(withheld), 1)
        self.assertIn("Fire Missile", withheld[0])
        self.assertIn("Channel 0", withheld[0])

    def test_the_move_fallback_when_every_option_is_withheld(self):
        # choose_option never builds an all-cast menu (every menu holds DODGE,
        # an engaged option, standing up or Struggle Free), so the fallback
        # is probed on a hand-built decision.
        state = channel_caster({}, spells=["fire_missile"], weapon=WeaponState())
        only_cast = next(
            candidate
            for candidate in policy.choose_option(state, state.by_id(1)).candidates
            if candidate.spell_key
        )
        bounded = policy._within_channel(
            state.by_id(1),
            policy.Decision(chosen=only_cast, candidates=[only_cast]),
            example_channel_rules(),
        )
        self.assertEqual((bounded.chosen.letter, bounded.chosen.name), ("a", "MOVE"))
        self.assertEqual(bounded.candidates, [bounded.chosen])


class UnclassifiedSpellTest(TestCase):
    """A spell the Channel rules give no kind: the menu withholds it, the
    casting phase refuses it."""

    def rules(self) -> MagicRules:
        return replace(
            channel_magic_rules(),
            channel=replace(example_channel_rules(), default_kind=None),
        )

    def test_the_menu_withholds_it_and_names_it(self):
        state = channel_caster(
            {"fire_missile": 3}, skill_levels={"tapestry": 4}, weapon=WeaponState()
        )
        decision = policy.choose_option(state, state.by_id(1), magic=self.rules())
        offered = {candidate.spell_key for candidate in decision.candidates}
        self.assertNotIn("fire_missile", offered)
        self.assertNotIn("shield", offered)
        self.assertIn("heal", offered)
        self.assertEqual(len(decision.withheld), 2)
        self.assertTrue(any("Fire Missile" in line for line in decision.withheld))
        self.assertTrue(all("no kind of magic" in line for line in decision.withheld))

    def test_a_turn_runs_and_logs_what_was_withheld(self):
        rules = self.rules()
        state = channel_caster(
            {"fire_missile": 3}, spells=["fire_missile"], weapon=WeaponState()
        )
        events: list[dict] = []
        engine.run_turn(
            state,
            PurposeRoller({}),
            events.append,
            partial(policy.choose_option, magic=rules),
            profile=TarmarProfile(magic=rules),
        )
        caster_decision = next(
            event
            for event in events_of_type(events, "decision")
            if event["actor"] == "Fighter 1"
        )
        self.assertIn("Fire Missile", caster_decision["payload"]["withheld"][0])
        other = next(
            event
            for event in events_of_type(events, "decision")
            if event["actor"] == "Fighter 2"
        )
        self.assertNotIn("withheld", other["payload"])

    def test_the_casting_phase_still_refuses_it(self):
        state = channel_caster({"fire_missile": 3})
        with self.assertRaises(ChannelUnclassifiedSpell) as raised:
            cast(state, [[3, 3, 3]], profile=TarmarProfile(magic=self.rules()))
        self.assertEqual(raised.exception.spell.key, "fire_missile")
        self.assertEqual(state.by_id(1).mana, 10)
