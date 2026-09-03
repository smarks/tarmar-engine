"""Reactions-to-injury rules behind the profile seam.

``TarmarReactions`` carries the pre-seam semantics exactly (pool at or below
zero → unconscious; survival saves for pools deep below zero, thresholds per
tarmar-studio's injury_thresholds). ``HitCountReactions`` ports melee's
structural reaction mechanics — hits-per-turn thresholds for wound penalty
and knockdown, pool thresholds for unconsciousness and death, the
wounded-last-turn roll-forward — as parameterized structure: every threshold
and penalty is injected by the caller (the classic profile's actual numbers
are milestone-3 data, kept out of this package's mechanics per the plan's
copyright note). Tests adapted from melee's ``test_state.py``
(``test_knockdown_on_eight_plus_hits``, ``test_end_turn_rolls_wound_flag
_forward``) with arbitrary, non-rulebook numbers.
"""

import math
from unittest import TestCase

from tarmar_engine.reactions import (
    DEAD,
    KNOCKDOWN,
    UNCONSCIOUS,
    HitCountReactions,
    TarmarReactions,
)

from .test_state import make_combatant


class TarmarReactionsTest(TestCase):
    def setUp(self):
        self.rules = TarmarReactions()

    def test_unconscious_when_either_pool_empties(self):
        combatant = make_combatant()
        self.assertFalse(self.rules.unconscious(combatant))
        combatant.fatigue = 0
        self.assertTrue(self.rules.unconscious(combatant))
        combatant.fatigue = 5
        combatant.body = -1
        self.assertTrue(self.rules.unconscious(combatant))

    def test_no_save_above_the_deep_threshold(self):
        combatant = make_combatant(fatigue=1, body=1)
        self.assertIsNone(self.rules.survival_save_penalty(combatant))

    def test_save_forced_at_half_max_below_zero(self):
        # injury_thresholds semantics: a pool at or below -ceil(max/2)
        # forces a save every turn; the penalty starts past -max.
        combatant = make_combatant(max_fatigue=20, fatigue=-10, body=5)
        self.assertEqual(self.rules.survival_save_penalty(combatant), 0)
        combatant.fatigue = -9
        self.assertIsNone(self.rules.survival_save_penalty(combatant))

    def test_penalty_grows_past_negative_max(self):
        combatant = make_combatant(max_fatigue=20, fatigue=-23, body=5)
        self.assertEqual(self.rules.survival_save_penalty(combatant), 3)

    def test_worst_pool_governs(self):
        combatant = make_combatant(
            max_fatigue=20, fatigue=-10, max_body=14, body=-16
        )
        # fatigue penalty 0, body penalty -14 - (-16) = 2 → worst is 2.
        self.assertEqual(self.rules.survival_save_penalty(combatant), 2)

    def test_odd_maximum_rounds_the_threshold_up(self):
        combatant = make_combatant(max_body=15, body=-8, fatigue=5)
        self.assertEqual(-math.ceil(15 / 2), -8)
        self.assertEqual(self.rules.survival_save_penalty(combatant), 0)
        combatant.body = -7
        self.assertIsNone(self.rules.survival_save_penalty(combatant))

    def test_save_rolls_against_constitution(self):
        combatant = make_combatant(constitution=13)
        self.assertEqual(self.rules.survival_save_target(combatant), 13)


class HitCountReactionsTest(TestCase):
    """Melee's reaction structure with arbitrary injected thresholds."""

    def setUp(self):
        self.rules = HitCountReactions(
            knockdown_hits=6,
            wound_hits=4,
            wound_dx_penalty=2,
            low_pool_threshold=2,
            low_pool_dx_penalty=3,
        )

    def test_thresholds_are_required_not_defaulted(self):
        # The mechanics carry no rulebook numbers — a caller must inject
        # every threshold (the classic profile's data arrives in milestone 3).
        with self.assertRaises(TypeError):
            HitCountReactions()  # type: ignore[call-arg]

    def test_knockdown_on_threshold_hits_in_one_turn(self):
        combatant = make_combatant()
        combatant.hits_this_turn = 5
        self.assertIsNone(self.rules.status_after_hit(combatant))
        combatant.hits_this_turn = 6
        self.assertEqual(self.rules.status_after_hit(combatant), KNOCKDOWN)

    def test_unconscious_at_zero_dead_below(self):
        combatant = make_combatant()
        combatant.fatigue = 0
        self.assertEqual(self.rules.status_after_hit(combatant), UNCONSCIOUS)
        combatant.fatigue = -1
        self.assertEqual(self.rules.status_after_hit(combatant), DEAD)

    def test_death_outranks_knockdown(self):
        combatant = make_combatant()
        combatant.fatigue = -2
        combatant.hits_this_turn = 9
        self.assertEqual(self.rules.status_after_hit(combatant), DEAD)

    def test_wound_flag_rolls_forward_at_end_of_turn(self):
        combatant = make_combatant()
        combatant.hits_this_turn = 4
        self.rules.end_of_turn(combatant)
        self.assertTrue(combatant.wounded_last_turn)
        combatant.hits_this_turn = 3
        self.rules.end_of_turn(combatant)
        self.assertFalse(combatant.wounded_last_turn)

    def test_wound_penalty_lasts_one_turn(self):
        combatant = make_combatant()
        self.assertEqual(self.rules.dx_penalty(combatant), 0)
        combatant.wounded_last_turn = True
        self.assertEqual(self.rules.dx_penalty(combatant), -2)
        combatant.wounded_last_turn = False
        self.assertEqual(self.rules.dx_penalty(combatant), 0)

    def test_low_pool_penalty_is_cumulative_with_the_wound(self):
        combatant = make_combatant()
        combatant.fatigue = 2
        self.assertEqual(self.rules.dx_penalty(combatant), -3)
        combatant.wounded_last_turn = True
        self.assertEqual(self.rules.dx_penalty(combatant), -5)

    def test_low_pool_penalty_is_optional_structure(self):
        rules = HitCountReactions(
            knockdown_hits=6, wound_hits=4, wound_dx_penalty=2
        )
        combatant = make_combatant()
        combatant.fatigue = 1
        self.assertEqual(rules.dx_penalty(combatant), 0)
