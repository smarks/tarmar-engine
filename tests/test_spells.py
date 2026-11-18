"""The spell catalog's ladders: the Elemental ones (tarmar-engine #75) and
the generic counters' (tarmar-engine #77).

A student of Elemental magic studies one element as a track and climbs it
tier by tier: Missile (1), Ball (2), Bolt (3). The catalog says each spell's
element and holds every element's full ladder, so a caller enforcing that
ruling can ask for the rung below any Elemental spell.

Protection's Counter 1 to Counter 5 are generic counters: learned, never
cast, and climbed on one ladder of their own.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine.spells import (
    ELEMENTAL_SCHOOL,
    ELEMENTS,
    GENERIC_COUNTER_LADDER,
    PROTECTION_SCHOOL,
    SPELLS,
    Spell,
    _index_ladders,
    element_siblings,
    generic_counter_level,
    get_spell,
    lower_tier,
)
from tarmar_engine.state import CASTER_POOLS

TIER_NAMES_BY_LEVEL = {1: "missile", 2: "ball", 3: "bolt"}

# Every catalog spell outside the Elemental school that can be cast, none of
# which names an element.
NON_ELEMENTAL_KEYS = ("shield", "blur", "fatigue", "wound", "heal")

# The generic counters, lowest rung first: schools-of-magic.md's Protection
# list, Counter 1 to Counter 5.
GENERIC_COUNTER_KEYS = ("counter_1", "counter_2", "counter_3", "counter_4", "counter_5")

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
        self.assertEqual(
            {spell.key for spell in others},
            {*NON_ELEMENTAL_KEYS, *GENERIC_COUNTER_KEYS},
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

    def test_an_elemental_spell_without_an_element_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "names no element"):
            replace(get_spell("fire_bolt"), key="plain_bolt", element=None)

    def test_a_spell_without_an_element_is_accepted(self) -> None:
        self.assertIsNone(replace(get_spell("heal"), key="other_heal").element)


class DamagePoolValidationTests(TestCase):
    def test_an_unknown_damage_pool_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "'fire_missile''s damage_pool"):
            replace(get_spell("fire_missile"), damage_pool="mana")

    def test_every_caster_pool_is_accepted(self) -> None:
        for pool in CASTER_POOLS:
            with self.subTest(pool=pool):
                spell = replace(get_spell("wound"), damage_pool=pool)
                self.assertEqual(spell.damage_pool, pool)


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

    def test_each_counter_steps_down_to_the_counter_below(self) -> None:
        self.assertIsNone(lower_tier(get_spell("counter_1")))
        for key_below, key in zip(GENERIC_COUNTER_KEYS, GENERIC_COUNTER_KEYS[1:]):
            with self.subTest(key=key):
                self.assertIs(lower_tier(get_spell(key)), get_spell(key_below))

    def test_a_counter_steps_down_its_ladder_whatever_its_school(self) -> None:
        elsewhere = replace(get_spell("counter_4"), school="Passage")
        self.assertIs(lower_tier(elsewhere), get_spell("counter_3"))

    def test_a_counter_rung_missing_below_is_an_error_not_none(self) -> None:
        off_the_ladder = replace(get_spell("counter_5"), key="counter_7", level=7)
        with self.assertRaisesRegex(KeyError, "no level 6 generic counter spell"):
            lower_tier(off_the_ladder)


class LadderIndexTests(TestCase):
    def test_the_catalog_indexes_every_rung_by_ladder_and_level(self) -> None:
        index = _index_ladders(SPELLS.values())
        self.assertEqual(
            len(index),
            len(ELEMENTS) * len(TIER_NAMES_BY_LEVEL) + len(GENERIC_COUNTER_KEYS),
        )
        self.assertIs(index[("wind", 2)], get_spell("wind_ball"))
        self.assertIs(index[(GENERIC_COUNTER_LADDER, 4)], get_spell("counter_4"))

    def test_spells_on_no_ladder_are_left_out(self) -> None:
        self.assertEqual(_index_ladders([get_spell("heal")]), {})

    def test_two_generic_counters_on_one_rung_are_refused(self) -> None:
        counter = get_spell("counter_1")
        twin = replace(counter, key="counter_one", name="Counter One")
        with self.assertRaisesRegex(ValueError, "'counter_one' and 'counter_1'"):
            _index_ladders([counter, twin])

    def test_a_gap_in_the_counter_ladder_is_refused(self) -> None:
        with self.assertRaisesRegex(
            ValueError, r"generic counter ladder holds levels \[1, 3\]"
        ):
            _index_ladders([get_spell("counter_1"), get_spell("counter_3")])

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


class GenericCounterCatalogTests(TestCase):
    def test_the_catalog_holds_counter_one_to_five(self) -> None:
        for level, key in enumerate(GENERIC_COUNTER_KEYS, start=1):
            with self.subTest(key=key):
                # Field for field: nothing to aim, roll, keep up or climb an
                # element by.
                self.assertEqual(
                    get_spell(key),
                    Spell(
                        key=key,
                        name=f"Counter {level}",
                        school="Protection",
                        level=level,
                        attribute="WIS",
                        targeted=False,
                        generic_counter=True,
                    ),
                )

    def test_only_the_counter_rungs_are_generic_counters(self) -> None:
        self.assertEqual(
            {spell.key for spell in SPELLS.values() if spell.generic_counter},
            set(GENERIC_COUNTER_KEYS),
        )

    def test_a_spell_is_no_generic_counter_unless_it_says_so(self) -> None:
        plain = Spell(
            key="ward",
            name="Ward",
            school=PROTECTION_SCHOOL,
            level=1,
            attribute="WIS",
            targeted=False,
        )
        self.assertFalse(plain.generic_counter)


class GenericCounterValidationTests(TestCase):
    def test_a_generic_counter_sets_nothing_a_cast_would_use(self) -> None:
        for field_name, value in (
            ("targeted", True),
            ("continuing", True),
            ("heals", True),
            ("damage", "1d6"),
            ("tn_bonus", 1),
            ("tn_bonus", -1),
            ("attacker_penalty", 2),
            ("element", "fire"),
        ):
            with (
                self.subTest(field=field_name, value=value),
                self.assertRaisesRegex(ValueError, rf"never cast.*'{field_name}'"),
            ):
                replace(get_spell("counter_1"), **{field_name: value})

    def test_every_field_set_is_named(self) -> None:
        with self.assertRaisesRegex(ValueError, r"\['targeted', 'damage'\]"):
            replace(get_spell("counter_1"), targeted=True, damage="2d6")

    def test_a_castable_spell_may_not_be_marked_a_generic_counter(self) -> None:
        for key in ("fire_missile", "shield", "heal"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(get_spell(key), generic_counter=True)


class GenericCounterLevelTests(TestCase):
    def test_no_generic_counter_is_level_zero(self) -> None:
        self.assertEqual(generic_counter_level([]), 0)
        self.assertEqual(generic_counter_level(["heal", "fire_ball", "shield"]), 0)

    def test_one_generic_counter_is_its_level(self) -> None:
        for level, key in enumerate(GENERIC_COUNTER_KEYS, start=1):
            with self.subTest(key=key):
                self.assertEqual(generic_counter_level([key]), level)
                self.assertEqual(generic_counter_level(["wound", key, "blur"]), level)

    def test_several_give_the_highest_not_the_sum(self) -> None:
        self.assertEqual(
            generic_counter_level(["counter_1", "shield", "counter_4", "counter_3"]),
            4,
        )
        self.assertEqual(generic_counter_level(GENERIC_COUNTER_KEYS), 5)

    def test_it_reads_any_iterable_of_keys(self) -> None:
        self.assertEqual(generic_counter_level(key for key in ("counter_2", "heal")), 2)
        self.assertEqual(generic_counter_level({"counter_3": 1, "heal": 4}), 3)

    def test_a_key_outside_the_catalog_is_a_key_error(self) -> None:
        with self.assertRaises(KeyError):
            generic_counter_level(["counter_1", "counter_6"])


class ElementSiblingsTests(TestCase):
    def test_a_rung_has_the_same_rung_of_every_other_element(self) -> None:
        self.assertEqual(
            [spell.key for spell in element_siblings(get_spell("fire_ball"))],
            ["water_ball", "lightning_ball", "earth_ball", "wind_ball"],
        )

    def test_siblings_keep_the_level_and_leave_out_the_spell_itself(self) -> None:
        for element in ELEMENTS:
            for tier_name in TIER_NAMES_BY_LEVEL.values():
                spell = get_spell(f"{element}_{tier_name}")
                siblings = element_siblings(spell)
                with self.subTest(key=spell.key):
                    self.assertEqual(
                        [sibling.element for sibling in siblings],
                        [other for other in ELEMENTS if other != element],
                    )
                    self.assertEqual(
                        {sibling.level for sibling in siblings}, {spell.level}
                    )
                    for sibling in siblings:
                        self.assertIs(sibling, get_spell(sibling.key))

    def test_a_spell_with_no_element_has_none(self) -> None:
        for key in (*NON_ELEMENTAL_KEYS, *GENERIC_COUNTER_KEYS):
            with self.subTest(key=key):
                self.assertEqual(element_siblings(get_spell(key)), ())

    def test_a_rung_no_other_element_holds_has_none(self) -> None:
        # No element's ladder reaches level 5 in the catalog.
        off_the_ladder = replace(get_spell("fire_bolt"), key="fire_storm", level=5)
        self.assertEqual(element_siblings(off_the_ladder), ())
