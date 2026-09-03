"""Forced-retreat rules behind the profile seam.

``TarmarForcedRetreat`` carries the pre-seam phase-6 semantics (dealt damage
and took none → push the chosen target; a blocked victim saves 3d6 ≤ DEX or
falls). ``MeleeStyleForcedRetreat`` ports melee's structural mechanics —
per-target push entitlements armed only by melee damage, spent one push per
hit, no save for a blocked victim, an optional advance — with tests adapted
from melee's ``engine/tests/test_state.py`` force-retreat suite.
"""

from unittest import TestCase

from tarmar_engine.retreat import (
    IllegalAction,
    MeleeStyleForcedRetreat,
    TarmarForcedRetreat,
)
from tarmar_engine.state import BattleState

from .test_state import make_combatant


def duel(first_overrides=None, second_overrides=None):
    first = make_combatant(1, q=0, r=0, facing=0, **(first_overrides or {}))
    second = make_combatant(2, q=1, r=0, facing=3, **(second_overrides or {}))
    return BattleState(arena_radius=6, combatants=[first, second]), first, second


class TarmarForcedRetreatTest(TestCase):
    def setUp(self):
        self.rules = TarmarForcedRetreat()

    def test_pusher_eligible_requires_dealt_and_untouched(self):
        _, first, _ = duel()
        self.assertFalse(self.rules.pusher_eligible(first))
        first.dealt_damage_this_turn = True
        self.assertTrue(self.rules.pusher_eligible(first))
        first.took_damage_this_turn = True
        self.assertFalse(self.rules.pusher_eligible(first))

    def test_grappled_pusher_is_exempt(self):
        _, first, _ = duel()
        first.dealt_damage_this_turn = True
        first.grappling = 2
        self.assertFalse(self.rules.pusher_eligible(first))

    def test_victim_is_the_chosen_adjacent_living_target(self):
        state, first, second = duel()
        first.chosen_target = 2
        self.assertIs(self.rules.victim_of(state, first), second)

    def test_no_victim_when_target_unset_dead_apart_or_grappled(self):
        state, first, second = duel()
        self.assertIsNone(self.rules.victim_of(state, first))
        first.chosen_target = 2
        second.alive = False
        self.assertIsNone(self.rules.victim_of(state, first))
        second.alive = True
        second.position = (3, 0)
        self.assertIsNone(self.rules.victim_of(state, first))
        second.position = (1, 0)
        second.grappled_by = 3
        self.assertIsNone(self.rules.victim_of(state, first))

    def test_blocked_victim_saves_against_dexterity(self):
        _, first, second = duel()
        self.assertEqual(self.rules.blocked_save_dice, "3d6")
        self.assertEqual(self.rules.blocked_save_target(second), second.dexterity)


class MeleeStyleForcedRetreatTest(TestCase):
    """Adapted from melee's force-retreat tests (#229A/#271/#311 lineage)."""

    def setUp(self):
        self.rules = MeleeStyleForcedRetreat()

    def arm(self, attacker, target):
        self.rules.record_hit(attacker, target, damage=3, ranged=False)

    def test_melee_damage_arms_a_push_against_that_target_only(self):
        state, first, second = duel()
        self.arm(first, second)
        self.assertTrue(self.rules.can_force_retreat(state, first, second))
        self.assertEqual(first.retreat_push_targets_this_turn, [2])

    def test_a_missile_hit_does_not_arm_a_force_retreat(self):
        state, first, second = duel()
        self.rules.record_hit(first, second, damage=3, ranged=True)
        self.assertFalse(self.rules.can_force_retreat(state, first, second))

    def test_a_zero_damage_hit_does_not_arm(self):
        state, first, second = duel()
        self.rules.record_hit(first, second, damage=0, ranged=False)
        self.assertFalse(self.rules.can_force_retreat(state, first, second))

    def test_attacker_who_took_hits_cannot_push(self):
        state, first, second = duel()
        self.arm(first, second)
        first.hits_this_turn = 2
        self.assertFalse(self.rules.can_force_retreat(state, first, second))

    def test_force_retreat_pushes_enemy_and_can_advance(self):
        state, first, second = duel()
        self.arm(first, second)
        destination = self.rules.force_retreat(state, first, second, advance=True)
        self.assertEqual(second.position, destination)
        self.assertEqual(destination, (2, 0))
        self.assertEqual(first.position, (1, 0))  # advanced into the vacated hex

    def test_force_retreat_without_advance_leaves_the_pusher(self):
        state, first, second = duel()
        self.arm(first, second)
        self.rules.force_retreat(state, first, second)
        self.assertEqual(first.position, (0, 0))

    def test_force_retreat_breaks_ties_deterministically(self):
        # Three of (1,0)'s neighbours are strictly farther from (0,0):
        # (2,0), (2,-1) and (1,1). The rule picks the farthest, settling
        # remaining ties on the hex's own (q, r) — always (2, 0) here.
        state, first, second = duel()
        self.arm(first, second)
        destination = self.rules.force_retreat(state, first, second)
        self.assertEqual(destination, (2, 0))

    def test_force_retreat_is_spent_and_cannot_chain(self):
        state, first, second = duel()
        self.arm(first, second)
        self.rules.force_retreat(state, first, second, advance=True)
        # The push is spent: the same hit cannot shove the foe across the map.
        self.assertFalse(self.rules.can_force_retreat(state, first, second))
        with self.assertRaises(IllegalAction):
            self.rules.force_retreat(state, first, second)

    def test_rejects_targets_the_menu_never_offers(self):
        state, first, second = duel()
        self.arm(first, second)
        # Non-adjacent (the victim was relocated between declaration and push).
        second.position = (3, 0)
        self.assertFalse(self.rules.can_force_retreat(state, first, second))
        second.position = (1, 0)
        # A fallen body is not pushed.
        second.conscious = False
        self.assertFalse(self.rules.can_force_retreat(state, first, second))
        second.conscious = True
        second.alive = False
        self.assertFalse(self.rules.can_force_retreat(state, first, second))

    def test_cannot_relocate_a_grappler(self):
        state, first, second = duel()
        self.arm(first, second)
        second.grappling = 3
        self.assertFalse(self.rules.can_force_retreat(state, first, second))
        second.grappling = None
        second.grappled_by = 3
        self.assertFalse(self.rules.can_force_retreat(state, first, second))

    def test_no_hex_to_retreat_into_is_illegal_not_a_save(self):
        # Box the victim in: every neighbour farther from the attacker is
        # occupied. Unlike Tarmar's phase 6 there is no save-or-fall — the
        # push simply cannot happen.
        state, first, second = duel()
        self.arm(first, second)
        blockers = []
        for index, position in enumerate(((2, 0), (2, -1), (1, 1)), start=3):
            blockers.append(
                make_combatant(index, q=position[0], r=position[1], facing=3)
            )
        state.combatants.extend(blockers)
        self.assertTrue(self.rules.can_force_retreat(state, first, second))
        with self.assertRaises(IllegalAction):
            self.rules.force_retreat(state, first, second)

    def test_multi_hex_victim_needs_its_whole_footprint_clear(self):
        # A shove can never overlap another figure or slide part of a
        # multi-hex body off the arena (melee #311).
        from tarmar_engine import hexes

        first = make_combatant(1, q=0, r=0, facing=0)
        giant = make_combatant(2, q=1, r=0, facing=3, size_hexes=3)
        state = BattleState(arena_radius=4, combatants=[first, giant])
        self.arm(first, giant)
        destination = self.rules.force_retreat(state, first, giant)
        occupied_by_first = set(first.footprint)
        for cell in giant.footprint:
            self.assertNotIn(cell, occupied_by_first)
            self.assertTrue(hexes.in_arena(cell, state.arena_radius))
        self.assertEqual(giant.position, destination)

    def test_a_boxed_in_multi_hex_victim_cannot_be_pushed(self):
        # The whole-footprint fit can leave NO legal destination even with
        # empty neighbouring hexes — part of the body would leave the arena.
        first = make_combatant(1, q=0, r=0, facing=0)
        giant = make_combatant(2, q=1, r=0, facing=3, size_hexes=3)
        state = BattleState(arena_radius=2, combatants=[first, giant])
        self.arm(first, giant)
        with self.assertRaises(IllegalAction):
            self.rules.force_retreat(state, first, giant)
