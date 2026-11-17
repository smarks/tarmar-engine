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
    _index_ladders,
    get_spell,
    lower_tier,
)

TIER_NAMES_BY_LEVEL = {1: "missile", 2: "ball", 3: "bolt"}

# Every catalog spell outside the Elemental school, none of which names an
# element.
NON_ELEMENTAL_KEYS = ("shield", "blur", "fatigue", "wound", "heal")

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
        self.assertEqual({spell.key for spell in others}, set(NON_ELEMENTAL_KEYS))
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

    def test_an_elemental_spell_without_an_element_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "names no element"):
            replace(get_spell("fire_bolt"), key="plain_bolt", element=None)

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

    def test_a_rung_missing_below_is_an_error_not_none(self) -> None:
        # The catalog's fire ladder stops at 3, so a level-5 fire spell has
        # no level-4 rung to step down to.
        off_the_ladder = replace(get_spell("fire_bolt"), key="fire_storm", level=5)
        with self.assertRaisesRegex(KeyError, "no level 4 fire spell"):
            lower_tier(off_the_ladder)

    def test_a_spell_with_no_element_has_no_lower_tier(self) -> None:
        for key in NON_ELEMENTAL_KEYS:
            with self.subTest(key=key):
                self.assertIsNone(lower_tier(get_spell(key)))


class LadderIndexTests(TestCase):
    def test_the_catalog_indexes_every_rung_by_element_and_level(self) -> None:
        index = _index_ladders(SPELLS.values())
        self.assertEqual(len(index), len(ELEMENTS) * len(TIER_NAMES_BY_LEVEL))
        self.assertIs(index[("wind", 2)], get_spell("wind_ball"))

    def test_spells_without_an_element_are_left_out(self) -> None:
        self.assertEqual(_index_ladders([get_spell("heal")]), {})

    def test_two_spells_on_one_rung_are_refused(self) -> None:
        missile = get_spell("earth_missile")
        twin = replace(missile, key="earth_dart", name="Earth Dart")
        with self.assertRaisesRegex(ValueError, "'earth_dart' and 'earth_missile'"):
            _index_ladders([missile, twin])

    def test_a_gap_in_a_ladder_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, r"water ladder holds levels \[1, 3\]"):
            _index_ladders([get_spell("water_missile"), get_spell("water_bolt")])

    def test_a_ladder_that_does_not_start_at_one_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, r"fire ladder holds levels \[2, 3\]"):
            _index_ladders([get_spell("fire_ball"), get_spell("fire_bolt")])
