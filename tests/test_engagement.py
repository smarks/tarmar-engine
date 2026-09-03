"""Engagement rules behind the profile seam.

``TarmarEngagement`` must agree exactly with the pre-seam implementation
(``combat_math.is_engaged`` over ``hexes.figure_engaged``) — that equivalence
is the zero-behavior-change half. ``MeleeStyleEngagement`` ports melee's
structural engagement mechanics (one-directional front-hex engagement,
downed figures engage no one, a needs-two threshold for large figures);
its tests are adapted from melee's ``engine/tests/test_facing.py``.
"""

from unittest import TestCase

from tarmar_engine import combat_math, hexes
from tarmar_engine.engagement import (
    EngagementRules,
    MeleeStyleEngagement,
    TarmarEngagement,
)
from tarmar_engine.state import BattleState

from .test_state import make_combatant


def duel(first_overrides=None, second_overrides=None):
    first_fields = {"q": 0, "r": 0, "facing": 0, **(first_overrides or {})}
    second_fields = {"q": 1, "r": 0, "facing": 3, **(second_overrides or {})}
    first = make_combatant(1, **first_fields)
    second = make_combatant(2, **second_fields)
    return BattleState(arena_radius=6, combatants=[first, second]), first, second


class TarmarEngagementTest(TestCase):
    def setUp(self):
        self.rules = TarmarEngagement()

    def test_matches_combat_math_for_adjacent_pair(self):
        state, first, second = duel()
        self.assertEqual(
            self.rules.is_engaged(state, first),
            combat_math.is_engaged(state, first),
        )
        self.assertTrue(self.rules.is_engaged(state, first))

    def test_matches_combat_math_when_apart(self):
        state, first, second = duel(second_overrides={"q": 4})
        self.assertEqual(
            self.rules.is_engaged(state, first),
            combat_math.is_engaged(state, first),
        )
        self.assertFalse(self.rules.is_engaged(state, first))

    def test_prone_enemy_still_engages_in_tarmar(self):
        """Tarmar's table has no prone-engager exemption — the pre-seam
        behavior (movement.md's engagement table reads only size) must
        survive the refactor exactly."""
        state, first, second = duel(second_overrides={"prone": True})
        self.assertEqual(
            self.rules.is_engaged(state, first),
            combat_math.is_engaged(state, first),
        )

    def test_multihex_actor_needs_more_engagers(self):
        giant = make_combatant(1, q=0, r=0, facing=0, size_hexes=3)
        lone = make_combatant(2, q=2, r=0, facing=3)
        state = BattleState(arena_radius=6, combatants=[giant, lone])
        self.assertEqual(
            self.rules.is_engaged(state, giant),
            combat_math.is_engaged(state, giant),
        )

    def test_threshold_delegates_to_hexes_table(self):
        for size in (1, 2, 3, 7):
            self.assertEqual(
                self.rules.threshold(make_combatant(1, size_hexes=size)),
                hexes.engagement_threshold(size),
            )


class MeleeStyleEngagementTest(TestCase):
    """Adapted from melee's test_facing.py engagement cases."""

    def setUp(self):
        self.rules = MeleeStyleEngagement()

    def test_engagement_requires_adjacency_and_front(self):
        # Standing in the enemy's front hex while adjacent -> engaged.
        state, first, second = duel()
        self.assertTrue(self.rules.is_engaged(state, first))
        # Two hexes away in the same direction is NOT engagement.
        state, first, second = duel(second_overrides={"q": 2})
        self.assertFalse(self.rules.is_engaged(state, first))

    def test_prone_figure_engages_no_one(self):
        state, first, second = duel(second_overrides={"prone": True})
        self.assertFalse(self.rules.is_engaged(state, first))

    def test_downed_figure_engages_no_one(self):
        state, first, second = duel(second_overrides={"conscious": False})
        self.assertFalse(self.rules.is_engaged(state, first))

    def test_engagement_is_one_directional_behind_a_foe_is_free(self):
        # Second stands directly behind first (first faces 0, second in
        # direction 3), turned to face first's back.
        behind = hexes.add((0, 0), 3)
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=behind[0], r=behind[1], facing=0),
            ],
        )
        me = state.by_id(2)
        # I'm in the enemy's rear, so it does not engage me — I stay free to
        # move and strike its rear (engagement is one-directional).
        self.assertFalse(self.rules.is_engaged(state, me))
        # ...but the enemy IS in my front, so IT is engaged by me.
        self.assertTrue(self.rules.is_engaged(state, state.by_id(1)))

    def test_face_to_face_figures_are_both_engaged(self):
        state, first, second = duel()
        self.assertTrue(self.rules.is_engaged(state, first))
        self.assertTrue(self.rules.is_engaged(state, second))

    def test_not_engaged_when_neither_faces_the_other(self):
        # Second stands in first's side hex (direction 2 from first), both
        # facing away from each other.
        side = hexes.add((0, 0), 2)
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=side[0], r=side[1], facing=2),
            ],
        )
        self.assertFalse(self.rules.is_engaged(state, state.by_id(2)))

    def test_large_figure_needs_two_engagers(self):
        # A multi-hex figure is engaged only by two distinct foes in its
        # front (melee p.20: one lone figure cannot pin a giant).
        giant = make_combatant(1, q=0, r=0, facing=0, size_hexes=3)
        lone = make_combatant(2, q=1, r=0, facing=3)
        state = BattleState(arena_radius=6, combatants=[giant, lone])
        self.assertFalse(self.rules.is_engaged(state, giant))
        second = make_combatant(3, q=1, r=-1, facing=3)
        state = BattleState(arena_radius=6, combatants=[giant, lone, second])
        self.assertTrue(self.rules.is_engaged(state, giant))

    def test_multi_hex_engager_counts_as_one(self):
        # Unlike Tarmar's table, a single multi-hex enemy does not
        # auto-engage; it is one engager like any other.
        giant = make_combatant(1, q=2, r=0, facing=3, size_hexes=3)
        actor = make_combatant(2, q=1, r=0, facing=0, size_hexes=3)
        state = BattleState(arena_radius=8, combatants=[giant, actor])
        self.assertFalse(self.rules.is_engaged(state, actor))

    def test_exempt_predicate_hook(self):
        # The classic profile can exempt figures outright (melee's airborne
        # figures are never engaged); the hook is injectable structure.
        rules = MeleeStyleEngagement(exempt=lambda actor: actor.combatant_id == 1)
        state, first, second = duel()
        self.assertFalse(rules.is_engaged(state, first))
        self.assertTrue(rules.is_engaged(state, second))


class SharedArcTest(TestCase):
    def test_arc_classification_is_shared_between_profiles(self):
        """Both games split the six directions front/side/rear identically
        (offsets 0,1,5 / 2,4 / 3) and award +2 side, +4 rear — so arc math is
        one shared implementation (``hexes.arc_of``), not a profile hook."""
        zones = [hexes.arc_of((0, 0), 0, hexes.add((0, 0), d)) for d in range(6)]
        self.assertEqual(zones.count("front"), 3)
        self.assertEqual(zones.count("side"), 2)
        self.assertEqual(zones.count("rear"), 1)
        self.assertEqual(hexes.arc_to_hit_bonus("side"), 2)
        self.assertEqual(hexes.arc_to_hit_bonus("rear"), 4)
        self.assertEqual(hexes.arc_to_hit_bonus("front"), 0)

    def test_base_class_is_abstract(self):
        state, first, _ = duel()
        with self.assertRaises(NotImplementedError):
            EngagementRules().is_engaged(state, first)
