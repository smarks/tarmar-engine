"""The RulesProfile seam: registry, Tarmar defaults, zero behavior change.

The acceptance bar for the seam refactor is that the default profile changes
nothing: a seeded battle run through ``engine.run_turn`` with no profile
argument and one run through ``TarmarProfile.run_turn`` must emit identical
event streams, roll for roll and message for message.
"""

import copy
from unittest import TestCase

from tarmar_engine import actions, engine, hexes, policy
from tarmar_engine.engagement import MeleeStyleEngagement, TarmarEngagement
from tarmar_engine.options import melee_structure_catalog, tarmar_catalog
from tarmar_engine.profile import (
    MELEE_STRUCTURE_PHASES,
    PROFILES,
    TARMAR,
    MeleeStructureProfile,
    RulesProfile,
    TarmarGrapple,
    TarmarProfile,
    get_profile,
)
from tarmar_engine.reactions import HitCountReactions, TarmarReactions
from tarmar_engine.retreat import MeleeStyleForcedRetreat, TarmarForcedRetreat
from tarmar_engine.state import BattleState

from .test_engine import SeededStubRoller
from .test_state import make_combatant


class RegistryTest(TestCase):
    def test_tarmar_is_the_registered_default(self):
        self.assertIs(get_profile("tarmar"), TARMAR)
        self.assertIsInstance(TARMAR, TarmarProfile)
        self.assertIn("tarmar", PROFILES)

    def test_unknown_profile_raises(self):
        with self.assertRaises(KeyError):
            get_profile("chess")


class TarmarProfileTest(TestCase):
    def test_seam_areas_are_tarmar_components(self):
        self.assertIsInstance(TARMAR.engagement, TarmarEngagement)
        self.assertIsInstance(TARMAR.retreat, TarmarForcedRetreat)
        self.assertIsInstance(TARMAR.reactions, TarmarReactions)
        self.assertIsInstance(TARMAR.grapple, TarmarGrapple)
        self.assertEqual(
            set(TARMAR.catalog.keys()), set(tarmar_catalog().keys())
        )

    def test_phases_are_the_six_phase_table(self):
        self.assertEqual(TARMAR.phases, engine.PHASES)

    def test_legal_actions_delegates_to_the_lettered_tables(self):
        kwargs = dict(
            engaged=True,
            prone=False,
            has_missile=False,
            has_spells=True,
            has_melee_target=True,
            can_grapple=True,
        )
        self.assertEqual(
            TARMAR.legal_actions(**kwargs), actions.legal_actions(**kwargs)
        )

    def test_grapple_seam_matches_the_pre_seam_constants(self):
        grapple = TARMAR.grapple
        self.assertEqual(grapple.to_hit_bonus, hexes.HTH_TO_HIT_BONUS)
        self.assertEqual(dict(grapple.grappled_actions), actions.GRAPPLED_ACTIONS)
        self.assertEqual(dict(grapple.grappler_actions), actions.GRAPPLER_ACTIONS)
        for grappled_by, grappling in ((None, None), (1, None), (None, 2), (1, 2)):
            self.assertEqual(
                grapple.locks_movement(grappled_by, grappling),
                hexes.figure_locked_by_grapple(grappled_by, grappling),
            )


def seeded_battle():
    first = make_combatant(1, q=-3, r=0, facing=0)
    second = make_combatant(2, q=3, r=0, facing=3)
    third = make_combatant(
        3,
        q=0,
        r=3,
        facing=1,
        weapon_skill_level=2,
        spells=["fire_missile", "heal"],
        max_mana=6,
        mana=6,
        intelligence=13,
    )
    return BattleState(arena_radius=6, combatants=[first, second, third])


def run_turns(runner, turns=6, seed=99):
    state = seeded_battle()
    roller = SeededStubRoller(seed)
    events = []
    for _turn in range(turns):
        runner(state, roller, events.append)
    return state, events


class ZeroBehaviorChangeTest(TestCase):
    """The default path and the explicit Tarmar profile are one behavior."""

    def test_profile_run_turn_matches_the_default_entrypoint(self):
        default_state, default_events = run_turns(
            lambda state, roller, sink: engine.run_turn(
                state, roller, sink, policy.choose_option
            )
        )
        profile_state, profile_events = run_turns(
            lambda state, roller, sink: TarmarProfile().run_turn(
                state, roller, sink, policy.choose_option
            )
        )
        self.assertEqual(default_events, profile_events)
        self.assertEqual(default_state.to_dict(), profile_state.to_dict())
        # The seeded battle actually exercised the loop.
        self.assertGreater(len(default_events), 50)

    def test_explicit_profile_argument_matches_too(self):
        default_state, default_events = run_turns(
            lambda state, roller, sink: engine.run_turn(
                state, roller, sink, policy.choose_option
            )
        )
        explicit_state, explicit_events = run_turns(
            lambda state, roller, sink: engine.run_turn(
                state, roller, sink, policy.choose_option, profile=TARMAR
            )
        )
        self.assertEqual(default_events, explicit_events)
        self.assertEqual(default_state.to_dict(), explicit_state.to_dict())

    def test_deep_copied_state_replays_identically(self):
        state = seeded_battle()
        replay = copy.deepcopy(state)
        first_events, second_events = [], []
        engine.run_turn(
            state, SeededStubRoller(7), first_events.append, policy.choose_option
        )
        engine.run_turn(
            replay, SeededStubRoller(7), second_events.append, policy.choose_option
        )
        self.assertEqual(first_events, second_events)


class MeleeStructureProfileTest(TestCase):
    def make_profile(self):
        return MeleeStructureProfile(
            reactions=HitCountReactions(
                knockdown_hits=6, wound_hits=4, wound_dx_penalty=2
            )
        )

    def test_wires_the_melee_style_components(self):
        profile = self.make_profile()
        self.assertIsInstance(profile.engagement, MeleeStyleEngagement)
        self.assertIsInstance(profile.retreat, MeleeStyleForcedRetreat)
        self.assertIsInstance(profile.reactions, HitCountReactions)
        self.assertEqual(
            set(profile.catalog.keys()), set(melee_structure_catalog().keys())
        )
        self.assertEqual(profile.phases, MELEE_STRUCTURE_PHASES)

    def test_turn_runner_arrives_in_milestone_3(self):
        profile = self.make_profile()
        with self.assertRaises(NotImplementedError):
            profile.run_turn(seeded_battle(), SeededStubRoller(1), print, None)

    def test_base_profile_is_abstract_about_running(self):
        with self.assertRaises(NotImplementedError):
            RulesProfile().run_turn(seeded_battle(), SeededStubRoller(1), print, None)
