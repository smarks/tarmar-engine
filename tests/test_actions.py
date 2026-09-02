"""The action-options catalog: letters, IMPLEMENTED, and legal_actions()."""

from unittest import TestCase

from tarmar_engine import actions


class ImplementedLettersTest(TestCase):
    def test_grapple_letters_are_implemented_and_documented(self):
        self.assertLessEqual({"o", "t", "v"}, actions.IMPLEMENTED)
        self.assertLessEqual(actions.IMPLEMENTED, set(actions.ALL_OPTIONS))

    def test_draw_dagger_remains_unimplemented(self):
        # Equipment-ready state the simulator does not model, same as e/m/q.
        self.assertNotIn("u", actions.IMPLEMENTED)


class GrappleActionDictsTest(TestCase):
    def test_grappled_actions_reuse_the_real_letters_where_the_page_says_to(self):
        self.assertEqual(actions.GRAPPLED_ACTIONS["v"], "STRUGGLE FREE")
        self.assertEqual(actions.GRAPPLED_ACTIONS["t"], "STRIKE BACK")
        self.assertIn("hold_still", actions.GRAPPLED_ACTIONS)

    def test_grappler_actions_have_no_lettered_equivalent(self):
        self.assertEqual(
            set(actions.GRAPPLER_ACTIONS), {"maintain", "squeeze", "release"}
        )
        # None of these tokens collide with a real action-options.md letter.
        self.assertFalse(set(actions.GRAPPLER_ACTIONS) & set(actions.ALL_OPTIONS))


class LegalActionsGrappleTest(TestCase):
    def test_can_grapple_appends_o_last(self):
        letters = actions.legal_actions(
            engaged=True,
            prone=False,
            has_missile=False,
            has_spells=False,
            has_melee_target=True,
            can_grapple=True,
        )
        self.assertEqual(letters, ["j", "k", "n", "o"])

    def test_can_grapple_false_omits_o(self):
        letters = actions.legal_actions(
            engaged=True,
            prone=False,
            has_missile=False,
            has_spells=False,
            has_melee_target=True,
        )
        self.assertNotIn("o", letters)

    def test_o_never_offered_while_disengaged(self):
        letters = actions.legal_actions(
            engaged=False,
            prone=False,
            has_missile=False,
            has_spells=False,
            has_melee_target=True,
            can_grapple=True,
        )
        self.assertNotIn("o", letters)
