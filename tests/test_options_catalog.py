"""The profile-governed option-catalog structure.

``OptionCatalog``/``OptionSpec`` are the shared machinery; the Tarmar
catalog mirrors ``actions.py``'s drift-guarded letter tables, and the
melee-structure catalog ports melee's option taxonomy (contexts, movement
caps, attack/dodge/defend/cast flags) — tests adapted from melee's
``engine/tests/test_movement.py``/``test_half_move.py`` and the shape of its
``options.py``. Structure only: no weapon tables or other SJG rules data.
"""

from unittest import TestCase

from tarmar_engine import actions
from tarmar_engine.options import (
    ANY,
    DISENGAGED,
    ENGAGED,
    HTH,
    SPECIAL,
    OptionCatalog,
    OptionSpec,
    melee_structure_catalog,
    movement_budget,
    tarmar_catalog,
)


class MovementBudgetTest(TestCase):
    """Adapted from melee's movement_budget coverage."""

    def test_caps_translate_to_hex_budgets(self):
        self.assertEqual(movement_budget(10, "full"), 10)
        self.assertEqual(movement_budget(10, "half"), 5)
        self.assertEqual(movement_budget(9, "half"), 4)  # halves round down
        self.assertEqual(movement_budget(10, "two"), 2)
        self.assertEqual(movement_budget(10, "one"), 1)
        self.assertEqual(movement_budget(10, "none"), 0)

    def test_unknown_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            movement_budget(10, "sideways")


class OptionCatalogTest(TestCase):
    def test_duplicate_keys_are_rejected(self):
        spec = OptionSpec(key="move", name="MOVE", context=DISENGAGED)
        with self.assertRaises(ValueError):
            OptionCatalog((spec, spec))

    def test_options_for_filters_by_context(self):
        catalog = OptionCatalog(
            (
                OptionSpec(key="a", name="A", context=DISENGAGED),
                OptionSpec(key="j", name="J", context=ENGAGED),
                OptionSpec(key="g", name="G", context=ANY),
                OptionSpec(key="t", name="T", context=HTH),
                OptionSpec(key="pass", name="PASS", context=SPECIAL),
            )
        )
        self.assertEqual(catalog.options_for(engaged=False), ["a", "g"])
        self.assertEqual(catalog.options_for(engaged=True), ["j", "g"])
        # HTH and SPECIAL contexts never leak into the normal menus.
        self.assertNotIn("t", catalog.options_for(engaged=True))
        self.assertNotIn("pass", catalog.options_for(engaged=False))

    def test_spec_lookup_and_membership(self):
        catalog = tarmar_catalog()
        self.assertIn("a", catalog)
        self.assertEqual(catalog.spec("a").name, "MOVE")
        with self.assertRaises(KeyError):
            catalog.spec("zz")


class TarmarCatalogTest(TestCase):
    def test_covers_every_lettered_option(self):
        catalog = tarmar_catalog()
        self.assertEqual(set(catalog.keys()), set(actions.ALL_OPTIONS))
        for letter, name in actions.ALL_OPTIONS.items():
            self.assertEqual(catalog.spec(letter).name, name)

    def test_contexts_mirror_the_three_tables(self):
        catalog = tarmar_catalog()
        for letter in actions.DISENGAGED_OPTIONS:
            self.assertEqual(catalog.spec(letter).context, DISENGAGED)
        for letter in actions.ENGAGED_OPTIONS:
            self.assertEqual(catalog.spec(letter).context, ENGAGED)
        for letter in actions.HTH_OPTIONS:
            self.assertEqual(catalog.spec(letter).context, HTH)

    def test_flags_match_engine_semantics(self):
        catalog = tarmar_catalog()
        self.assertTrue(catalog.spec("c").sets_dodge)
        self.assertTrue(catalog.spec("k").sets_defend)
        for letter in ("h", "r"):
            self.assertTrue(catalog.spec(letter).casts_spell)
        for letter in ("b", "f", "j", "l", "t"):
            self.assertTrue(catalog.spec(letter).is_attack)
        for letter in ("f", "l"):
            self.assertTrue(catalog.spec(letter).is_missile)
        self.assertFalse(catalog.spec("j").is_missile)

    def test_movement_caps_use_tarmar_gait_vocabulary(self):
        # Tarmar's movement economy is gait-based (movement.md), not the
        # fraction-of-MA vocabulary — the cap token names the gait the
        # engine's phase 3/4 give the option.
        catalog = tarmar_catalog()
        self.assertEqual(catalog.spec("a").movement_cap, "run")
        self.assertEqual(catalog.spec("b").movement_cap, "jog")
        for letter in ("f", "h", "r"):
            self.assertEqual(catalog.spec(letter).movement_cap, "adjust")
        self.assertEqual(catalog.spec("j").movement_cap, "none")


class MeleeStructureCatalogTest(TestCase):
    """The ported melee option taxonomy (adapted from melee's options.py)."""

    def setUp(self):
        self.catalog = melee_structure_catalog()

    def test_disengaged_menu(self):
        keys = self.catalog.options_for(engaged=False)
        for expected in (
            "move",
            "half_move",
            "charge_attack",
            "dodge",
            "ready_weapon",
            "missile_attack",
            "stand_up",
            "crawl",
        ):
            self.assertIn(expected, keys)
        self.assertNotIn("shift_attack", keys)
        self.assertNotIn("disengage", keys)

    def test_engaged_menu(self):
        keys = self.catalog.options_for(engaged=True)
        for expected in (
            "attack",
            "shift_attack",
            "shift_defend",
            "one_last_shot",
            "change_weapons",
            "disengage",
            "hth_attack",
            "cast",
            "pick_up",
        ):
            self.assertIn(expected, keys)
        self.assertNotIn("move", keys)
        self.assertNotIn("charge_attack", keys)

    def test_movement_caps(self):
        # Melee's caps are fractions of MA — full move, half-move options,
        # the 2-hex ready-weapon crawl band, and 1-hex shifts.
        self.assertEqual(self.catalog.spec("move").movement_cap, "full")
        for key in ("half_move", "charge_attack", "dodge"):
            self.assertEqual(self.catalog.spec(key).movement_cap, "half")
        for key in ("ready_weapon", "crawl"):
            self.assertEqual(self.catalog.spec(key).movement_cap, "two")
        for key in (
            "missile_attack",
            "shift_attack",
            "shift_defend",
            "change_weapons",
            "disengage",
            "hth_attack",
            "cast",
        ):
            self.assertEqual(self.catalog.spec(key).movement_cap, "one")
        for key in ("attack", "one_last_shot", "stand_up", "pick_up"):
            self.assertEqual(self.catalog.spec(key).movement_cap, "none")

    def test_flags(self):
        self.assertTrue(self.catalog.spec("dodge").sets_dodge)
        self.assertTrue(self.catalog.spec("shift_defend").sets_defend)
        self.assertTrue(self.catalog.spec("cast").casts_spell)
        for key in (
            "charge_attack",
            "missile_attack",
            "attack",
            "shift_attack",
            "one_last_shot",
            "hth_attack",
        ):
            self.assertTrue(self.catalog.spec(key).is_attack)
        for key in ("missile_attack", "one_last_shot"):
            self.assertTrue(self.catalog.spec(key).is_missile)
        self.assertFalse(self.catalog.spec("shift_attack").is_missile)

    def test_turn_flow_specials_stay_out_of_the_menus(self):
        # DO_NOTHING/PASS are turn-flow options injected by the selection
        # pass, never part of the engaged/disengaged menus (melee options.py).
        for key in ("do_nothing", "pass"):
            self.assertEqual(self.catalog.spec(key).context, SPECIAL)
            self.assertNotIn(key, self.catalog.options_for(engaged=False))
            self.assertNotIn(key, self.catalog.options_for(engaged=True))
