"""The spell catalog's Elemental ladders (tarmar-engine #75).

A student of Elemental magic studies one element as a track and climbs it
tier by tier: Missile (1), Ball (2), Bolt (3). The catalog says each spell's
element and holds every element's full ladder, so a caller enforcing that
ruling can ask for the rung below any Elemental spell.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine.spells import (
    ELEMENTAL_SCHOOL,
    ELEMENTS,
    SPELLS,
    Spell,
    get_spell,
    lower_tier,
)

TIER_NAMES_BY_LEVEL = {1: "missile", 2: "ball", 3: "bolt"}

# The three Elemental spells the catalog carried before the ladders, field for
# field as they were written then, plus the element each now names.
SPELLS_BEFORE_THE_LADDERS = (
    Spell(
        key="fire_missile",
        name="Fire Missile",
        school="Elemental",
        level=1,
        attribute="INT",
        targeted=True,
        damage="1d6",
        element="fire",
    ),
    Spell(
        key="fire_ball",
        name="Fire Ball",
        school="Elemental",
        level=2,
        attribute="INT",
        targeted=True,
        damage="2d6",
        element="fire",
    ),
    Spell(
        key="lightning_bolt",
        name="Lightning Bolt",
        school="Elemental",
        level=3,
        attribute="INT",
        targeted=True,
        damage="3d6",
        element="lightning",
    ),
)


class ElementalLadderTests(TestCase):
    def test_elements_are_the_five_the_spell_page_names_in_order(self) -> None:
        self.assertEqual(ELEMENTS, ("water", "fire", "lightning", "earth", "wind"))

    def test_every_element_has_missile_ball_and_bolt(self) -> None:
        for element in ELEMENTS:
            for level, tier_name in TIER_NAMES_BY_LEVEL.items():
                with self.subTest(element=element, tier=tier_name):
                    spell = get_spell(f"{element}_{tier_name}")
                    self.assertEqual(
                        spell.name, f"{element.capitalize()} {tier_name.capitalize()}"
                    )
                    self.assertEqual(spell.school, ELEMENTAL_SCHOOL)
                    self.assertEqual(spell.element, element)
                    self.assertEqual(spell.level, level)
                    self.assertEqual(spell.damage, f"{level}d6")
                    self.assertTrue(spell.targeted)
                    self.assertEqual(spell.attribute, "INT")

    def test_the_elemental_school_holds_exactly_the_fifteen_rungs(self) -> None:
        elemental_keys = {
            spell.key for spell in SPELLS.values() if spell.school == ELEMENTAL_SCHOOL
        }
        self.assertEqual(
            elemental_keys,
            {
                f"{element}_{tier_name}"
                for element in ELEMENTS
                for tier_name in TIER_NAMES_BY_LEVEL.values()
            },
        )

    def test_the_three_existing_spells_are_unchanged(self) -> None:
        for expected in SPELLS_BEFORE_THE_LADDERS:
            with self.subTest(key=expected.key):
                self.assertEqual(SPELLS[expected.key], expected)

    def test_no_spell_outside_the_elemental_school_names_an_element(self) -> None:
        others = [
            spell for spell in SPELLS.values() if spell.school != ELEMENTAL_SCHOOL
        ]
        self.assertEqual(
            {spell.key for spell in others},
            {"shield", "blur", "fatigue", "wound", "heal"},
        )
        for spell in others:
            with self.subTest(key=spell.key):
                self.assertIsNone(spell.element)


class ElementValidationTests(TestCase):
    def test_an_unknown_element_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "'ice'"):
            replace(get_spell("fire_missile"), key="ice_missile", element="ice")

    def test_an_element_outside_the_elemental_school_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "Protection"):
            replace(get_spell("shield"), element="fire")

    def test_a_spell_without_an_element_is_accepted(self) -> None:
        self.assertIsNone(replace(get_spell("heal"), key="other_heal").element)


class LowerTierTests(TestCase):
    def test_each_rung_steps_down_its_own_element(self) -> None:
        for element in ELEMENTS:
            with self.subTest(element=element):
                missile = get_spell(f"{element}_missile")
                ball = get_spell(f"{element}_ball")
                bolt = get_spell(f"{element}_bolt")
                self.assertIs(lower_tier(bolt), ball)
                self.assertIs(lower_tier(ball), missile)
                self.assertIsNone(lower_tier(missile))

    def test_lightning_bolt_steps_down_to_lightning_ball_not_fire_ball(
        self,
    ) -> None:
        self.assertIs(
            lower_tier(get_spell("lightning_bolt")), get_spell("lightning_ball")
        )

    def test_a_spell_with_no_element_has_no_lower_tier(self) -> None:
        for key in ("shield", "blur", "fatigue", "wound", "heal"):
            with self.subTest(key=key):
                self.assertIsNone(lower_tier(get_spell(key)))
