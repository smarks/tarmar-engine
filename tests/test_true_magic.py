"""Probe tests for the injected True Magic rules (tarmar-engine #77): their
validation, the workings they build, and the small helpers the casting code
will ask.

Every number and name here is invented, as in ``test_magic`` and
``test_channel``: a kind of magic called "knot" deriving from a general skill
called "knotwork", effects called "scorch", "salve", "omen" and "hush", a 2d6
interruption roll against WIS with bands named "snag" and "unravel", and
coefficients that are plainly not the rulebook's. The tests check the
engine's *structure*: what the rules refuse, the spell a working is built as,
and that each helper answers from the injected values. The real rules are
built by the caller that owns the canon.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine.dice import MAX_DICE_COUNT, parse_dice_expression
from tarmar_engine.magic import (
    BODY_POOL,
    CHANNEL,
    FATIGUE_POOL,
    PUSH_CAP,
    UNRECORDED_SKILL_LEVEL,
    WORKING_KEY_PREFIX,
    WORKING_KEY_SEPARATOR,
    CastingEnding,
    ChannelDerivation,
    ChannelRules,
    ChannelUnclassifiedSpell,
    InterruptionRules,
    MagicRules,
    MarginBand,
    RollUnderRule,
    SpellEffectKind,
    TrueMagicRules,
    WorkingEffect,
    casting_mana,
    failed_band,
    is_working_key,
    runaway_band,
    spell_effect_kind,
    spell_for,
)
from tarmar_engine.spells import ELEMENTAL_SCHOOL, get_spell
from tarmar_engine.state import CombatantState, CraftState

from .test_borrowing import DEBT_BANDS, FLICKER, SURGE, example_borrowing_rules
from .test_channel import LOOM, channel_caster, example_channel_rules
from .test_counterspell import FAMILIARITY_RULES, example_counterspell_rules
from .test_magic import SPUTTER, TORRENT, WOBBLE, example_push_rules

KNOT_KIND = "knot"
KNOT_SKILL = "knotwork"
#: Channel by the "knot" kind is 3 x the "knotwork" level + 1.
KNOT = ChannelDerivation(source_skill=KNOT_SKILL, multiplier=3, offset=1)

SNAG = MarginBand(
    key="snag", label="Snag", is_runaway=False, lowest_margin=1, highest_margin=3
)
UNRAVEL = MarginBand(
    key="unravel",
    label="Unravel",
    is_runaway=True,
    lowest_margin=4,
    highest_margin=None,
)
INTERRUPTION_BANDS = (SNAG, UNRAVEL)

SCORCH = WorkingEffect(
    key="scorch",
    label="Scorch",
    kind=SpellEffectKind.DAMAGE,
    dice_per_tier="1d4+1",
    pool=BODY_POOL,
    targeted=True,
    ignores_armour=True,
)
SALVE = WorkingEffect(
    key="salve", label="Salve", kind=SpellEffectKind.HEALING, dice_per_tier="1d8"
)
OMEN = WorkingEffect(
    key="omen", label="Omen", kind=None, dice_per_tier="3d4-1", needs_grant=True
)
HUSH = WorkingEffect(key="hush", label="Hush", kind=None, dice_per_tier=None)
EFFECTS = (SCORCH, SALVE, OMEN, HUSH)

SCHOOL = "Loomcraft"


def example_interruption_rules(**changes) -> InterruptionRules:
    """Invented interruption rules for the tests. Not Tarmar's numbers."""
    rules = InterruptionRules(
        roll=RollUnderRule(dice="2d6", attribute="WIS"),
        penalty_per_mana=2,
        failure_bands=INTERRUPTION_BANDS,
        automatic_runaway_totals=frozenset({11}),
    )
    return replace(rules, **changes)


def example_true_magic_rules(**changes) -> TrueMagicRules:
    """Invented True Magic rules for the tests. Not Tarmar's numbers."""
    rules = TrueMagicRules(
        channel_kind=KNOT_KIND,
        skill_label="Knotwork",
        school=SCHOOL,
        effects=EFFECTS,
        tiers_per_level=2,
        turns_per_tier=3,
        roll_attribute="WIS",
        roll_bonus_per_level=2,
        interruption=example_interruption_rules(),
        drain_pool=BODY_POOL,
        drain_per_mana=3,
        lost_drain_per_mana={
            CastingEnding.INTERRUPTED: 2,
            CastingEnding.COUNTERED: 5,
        },
    )
    return replace(rules, **changes)


def knot_channel_rules() -> ChannelRules:
    """The example Channel rules with the "knot" kind beside their own."""
    return replace(
        example_channel_rules(),
        derivations={**example_channel_rules().derivations, KNOT_KIND: KNOT},
    )


def true_magic_rules(**true_magic_changes) -> MagicRules:
    """Magic rules with Channel and the example True Magic rules, some of
    the True Magic fields changed."""
    return MagicRules(
        channel=knot_channel_rules(),
        true_magic=example_true_magic_rules(**true_magic_changes),
    )


def knot_caster(knotwork_level: int | None, **overrides) -> CombatantState:
    """The test wizard with a recorded "knotwork" level, or none recorded."""
    skill_levels = {} if knotwork_level is None else {KNOT_SKILL: knotwork_level}
    return channel_caster({}, skill_levels=skill_levels, **overrides).by_id(1)


class WorkingEffectValidationTest(TestCase):
    def test_the_key_is_not_empty_and_holds_no_separator(self):
        for key in ("", f"sc{WORKING_KEY_SEPARATOR}orch", WORKING_KEY_SEPARATOR):
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(SCORCH, key=key)

    def test_a_rolled_effect_names_its_dice(self):
        for effect in (SCORCH, SALVE):
            with self.subTest(effect=effect.key), self.assertRaises(ValueError):
                replace(effect, dice_per_tier=None)

    def test_a_narrated_effect_may_name_dice_or_none(self):
        for dice_per_tier in ("2d10+3", None):
            with self.subTest(dice_per_tier=dice_per_tier):
                narrated = WorkingEffect(
                    key="murmur",
                    label="Murmur",
                    kind=None,
                    dice_per_tier=dice_per_tier,
                )
                self.assertEqual(narrated.dice_per_tier, dice_per_tier)

    def test_a_narrated_effect_is_neither_targeted_nor_armour_piercing(self):
        for flag in ("targeted", "ignores_armour"):
            for effect in (OMEN, HUSH):
                with (
                    self.subTest(flag=flag, effect=effect.key),
                    self.assertRaises(ValueError),
                ):
                    replace(effect, **{flag: True})

    def test_the_dice_and_the_pool_are_checked(self):
        with self.assertRaises(ValueError):
            replace(SCORCH, dice_per_tier="a handful")
        with self.assertRaises(ValueError):
            replace(OMEN, dice_per_tier="d")
        with self.assertRaises(ValueError):
            replace(SCORCH, pool="mana")

    def test_the_defaults_are_an_untargeted_fatigue_effect_open_to_all(self):
        plain = WorkingEffect(
            key="soothe",
            label="Soothe",
            kind=SpellEffectKind.HEALING,
            dice_per_tier="1d10",
        )
        self.assertEqual(plain.pool, FATIGUE_POOL)
        self.assertFalse(plain.targeted)
        self.assertFalse(plain.ignores_armour)
        self.assertFalse(plain.needs_grant)


class WorkingDiceTest(TestCase):
    def test_the_count_and_the_modifier_are_multiplied_by_the_tier(self):
        self.assertEqual(SCORCH.dice_at(1), "1d4+1")
        self.assertEqual(SCORCH.dice_at(3), "3d4+3")
        self.assertEqual(OMEN.dice_at(2), "6d4-2")
        self.assertEqual(SALVE.dice_at(4), "4d8")

    def test_an_effect_with_no_dice_rolls_none_at_any_tier(self):
        self.assertIsNone(HUSH.dice_at(1))
        self.assertIsNone(HUSH.dice_at(5))

    def test_a_tier_below_one_is_refused(self):
        for effect in (SCORCH, HUSH):
            for tier in (0, -2):
                with (
                    self.subTest(effect=effect.key, tier=tier),
                    self.assertRaises(ValueError),
                ):
                    effect.dice_at(tier)

    def test_a_tier_that_takes_the_dice_out_of_range_is_refused(self):
        heavy = replace(SCORCH, dice_per_tier="60d4")
        self.assertEqual(heavy.dice_at(1), "60d4")
        with self.assertRaises(ValueError):
            heavy.dice_at(2)


class HighestBuildableTierTest(TestCase):
    """An effect's dice bound the tiers a working of it can be built at."""

    HEAVY = replace(SCORCH, dice_per_tier="60d4")
    DICED_EFFECTS = (SCORCH, SALVE, OMEN, HEAVY)

    def test_it_is_the_last_tier_whose_dice_count_is_in_range(self):
        self.assertEqual(SCORCH.highest_buildable_tier(), MAX_DICE_COUNT)
        self.assertEqual(SALVE.highest_buildable_tier(), MAX_DICE_COUNT)
        self.assertEqual(OMEN.highest_buildable_tier(), MAX_DICE_COUNT // 3)
        self.assertEqual(self.HEAVY.highest_buildable_tier(), 1)

    def test_an_effect_with_no_dice_builds_at_any_tier(self):
        self.assertIsNone(HUSH.highest_buildable_tier())
        HUSH.check_tier(5000)
        self.assertIsNone(HUSH.dice_at(5000))

    def test_the_dice_at_the_highest_tier_parse_and_one_tier_more_would_not(self):
        for effect in self.DICED_EFFECTS:
            with self.subTest(effect=effect.key, dice=effect.dice_per_tier):
                highest = effect.highest_buildable_tier()
                assert highest is not None and effect.dice_per_tier is not None
                dice = effect.dice_at(highest)
                assert dice is not None
                count, sides, _modifier = parse_dice_expression(effect.dice_per_tier)
                self.assertEqual(parse_dice_expression(dice)[0], count * highest)
                with self.assertRaises(ValueError):
                    parse_dice_expression(f"{count * (highest + 1)}d{sides}")

    def test_check_tier_holds_a_tier_between_one_and_the_highest(self):
        for effect in self.DICED_EFFECTS:
            highest = effect.highest_buildable_tier()
            assert highest is not None
            with self.subTest(effect=effect.key, dice=effect.dice_per_tier):
                effect.check_tier(1)
                effect.check_tier(highest)
                for tier in (0, -1, highest + 1):
                    with self.assertRaises(ValueError):
                        effect.check_tier(tier)
                    with self.assertRaises(ValueError):
                        effect.dice_at(tier)


class InterruptionRulesValidationTest(TestCase):
    def test_a_negative_penalty_is_refused(self):
        with self.assertRaises(ValueError):
            example_interruption_rules(penalty_per_mana=-1)
        example_interruption_rules(penalty_per_mana=0)

    def test_the_bands_are_held_to_the_shared_shape(self):
        gap = replace(UNRAVEL, lowest_margin=5)
        closed = replace(UNRAVEL, highest_margin=9)
        for bands in ((SNAG, gap), (SNAG, closed), (SNAG,), ()):
            with (
                self.subTest(bands=[band.key for band in bands]),
                self.assertRaises(ValueError),
            ):
                example_interruption_rules(failure_bands=bands)

    def test_automatic_totals_need_a_runaway_band(self):
        tame = (SNAG, replace(UNRAVEL, is_runaway=False))
        with self.assertRaises(ValueError):
            example_interruption_rules(failure_bands=tame)
        example_interruption_rules(
            failure_bands=tame, automatic_runaway_totals=frozenset()
        )


class TrueMagicRulesValidationTest(TestCase):
    def test_at_least_one_effect(self):
        with self.assertRaises(ValueError):
            example_true_magic_rules(effects=())

    def test_effect_keys_are_unique(self):
        twin = replace(SALVE, key=SCORCH.key)
        with self.assertRaises(ValueError):
            example_true_magic_rules(effects=(SCORCH, twin))

    def test_tiers_per_level_and_turns_per_tier_are_at_least_one(self):
        for field_name in ("tiers_per_level", "turns_per_tier"):
            for value in (0, -1):
                with (
                    self.subTest(field=field_name, value=value),
                    self.assertRaises(ValueError),
                ):
                    example_true_magic_rules(**{field_name: value})

    def test_negative_bonus_and_drains_are_refused(self):
        for changes in (
            {"roll_bonus_per_level": -1},
            {"drain_per_mana": -1},
            {"lost_drain_per_mana": {CastingEnding.FAILED: -1}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                example_true_magic_rules(**changes)
        example_true_magic_rules(
            roll_bonus_per_level=0, drain_per_mana=0, lost_drain_per_mana={}
        )

    def test_the_roll_attribute_and_the_drain_pool_are_checked(self):
        with self.assertRaises(ValueError):
            example_true_magic_rules(roll_attribute="DEX")
        with self.assertRaises(ValueError):
            example_true_magic_rules(drain_pool="mana")

    def test_an_unknown_ending_is_refused(self):
        with self.assertRaises(ValueError):
            example_true_magic_rules(lost_drain_per_mana={"fizzled": 1})
        # An ending may be named by its value.
        named_by_value = example_true_magic_rules(
            lost_drain_per_mana={CastingEnding.STOPPED.value: 4}
        )
        self.assertEqual(named_by_value.drain(2, CastingEnding.STOPPED), 8)

    def test_a_school_no_working_can_carry_is_refused(self):
        # The catalog's Elemental school needs an element, and no working
        # names one.
        with self.assertRaises(ValueError):
            example_true_magic_rules(school=ELEMENTAL_SCHOOL)


class MagicRulesTrueMagicValidationTest(TestCase):
    def test_no_true_magic_by_default(self):
        self.assertIsNone(MagicRules().true_magic)

    def test_true_magic_needs_channel_rules(self):
        with self.assertRaises(ValueError):
            MagicRules(true_magic=example_true_magic_rules())

    def test_the_channel_kind_needs_a_derivation(self):
        with self.assertRaises(ValueError):
            true_magic_rules(channel_kind="braid")

    def test_the_channel_kind_names_a_general_skill(self):
        # "loom" derives per spell: it names no skill to be the True skill.
        with self.assertRaises(ValueError):
            true_magic_rules(channel_kind="loom")
        self.assertIsNotNone(true_magic_rules(channel_kind="weft").true_magic)


class CounterspellFamiliarityFieldTest(TestCase):
    def test_familiarity_counts_for_nothing_by_default(self):
        self.assertEqual(example_counterspell_rules().familiarity_per_level, 0)

    def test_a_negative_familiarity_bonus_is_refused(self):
        with self.assertRaises(ValueError):
            example_counterspell_rules(familiarity_per_level=-1)
        # Nothing and more than nothing are both allowed.
        example_counterspell_rules(familiarity_per_level=0)
        example_counterspell_rules(familiarity_per_level=3)

    def test_the_bonus_is_the_injected_amount_for_each_level(self):
        self.assertEqual(
            [FAMILIARITY_RULES.familiarity_bonus(level) for level in (0, 1, 4)],
            [0, 3, 12],
        )
        self.assertTrue(FAMILIARITY_RULES.counts_familiarity)
        self.assertFalse(example_counterspell_rules().counts_familiarity)


def versed(levels: dict[str, int], *known_counters: str) -> CombatantState:
    """The test wizard with these levels recorded in spells, knowing these
    generic counters beside its own spells."""
    figure = knot_caster(None, spell_skill_levels=dict(levels))
    figure.spells = [*figure.spells, *known_counters]
    return figure


class CasterFamiliarityTest(TestCase):
    """The caster's familiarity: its level in the exact spell, and for a
    working or a True cast it holds no level in, the True level it is handed."""

    def familiarity(
        self, levels: dict[str, int], spell: str, true_level: int, *, true_cast: bool
    ) -> int:
        return FAMILIARITY_RULES.caster_familiarity(
            versed(levels), get_spell(spell), true_level, true_cast=true_cast
        )

    def test_a_recipe_cast_adds_its_level_in_the_exact_spell(self):
        levels = {"fire_missile": 4, "fire_ball": 6}
        self.assertEqual(
            self.familiarity(levels, "fire_missile", 5, true_cast=False), 4
        )
        # No other spell's level stands in, and neither does the True level.
        self.assertEqual(
            self.familiarity(levels, "water_missile", 5, true_cast=False), 0
        )

    def test_a_true_cast_of_a_spell_it_holds_a_level_in_adds_that_level(self):
        # Whichever is the higher: it is not the better of the two.
        for true_level in (2, 5):
            with self.subTest(true_level=true_level):
                self.assertEqual(
                    self.familiarity(
                        {"fire_missile": 4}, "fire_missile", true_level, true_cast=True
                    ),
                    4,
                )

    def test_a_true_cast_of_a_spell_it_holds_no_level_in_adds_the_true_level(self):
        # None recorded, as for an element never studied, and a recorded 0.
        for levels in ({"fire_missile": 4}, {"water_missile": 0}):
            with self.subTest(levels=levels):
                self.assertEqual(
                    self.familiarity(levels, "water_missile", 5, true_cast=True), 5
                )

    def test_a_working_adds_the_true_level(self):
        working = example_true_magic_rules().working("scorch", 3)
        caster = versed({"fire_missile": 4})
        # A working is told by its key, whatever its caller says of the cast.
        for true_cast in (True, False):
            with self.subTest(true_cast=true_cast):
                self.assertEqual(
                    FAMILIARITY_RULES.caster_familiarity(
                        caster, working, 5, true_cast=true_cast
                    ),
                    5,
                )


class DefenderFamiliarityTest(TestCase):
    """The defender's familiarity: the highest of its level in the exact
    spell, its True level and the generic counter it knows, with the source
    of that level. Fire Ball is a level-2 spell and Fire Bolt a level-3 one."""

    def familiarity(
        self,
        spell_level: int,
        true_level: int,
        *known_counters: str,
        spell: str = "fire_ball",
    ) -> tuple[int, str | None]:
        defender = versed({spell: spell_level}, *known_counters)
        return FAMILIARITY_RULES.defender_familiarity(
            defender, get_spell(spell), true_level
        )

    def test_it_is_the_highest_of_the_three_and_not_their_sum(self):
        self.assertEqual(self.familiarity(5, 3, "counter_4"), (5, "spell"))
        self.assertEqual(self.familiarity(1, 5, "counter_3"), (5, "true"))
        self.assertEqual(self.familiarity(1, 2, "counter_4"), (4, "generic"))

    def test_only_the_exact_spell_counts_as_the_spell(self):
        defender = versed({"fire_missile": 6})
        self.assertEqual(
            FAMILIARITY_RULES.defender_familiarity(defender, get_spell("fire_ball"), 0),
            (0, None),
        )

    def test_a_generic_counter_counts_at_or_below_its_level_only(self):
        # Against the level-3 spell: Counter 2 is too low, Counter 3 reaches.
        self.assertEqual(
            self.familiarity(0, 0, "counter_2", spell="fire_bolt"), (0, None)
        )
        self.assertEqual(
            self.familiarity(0, 0, "counter_3", spell="fire_bolt"), (3, "generic")
        )
        # A counter above the spell's level counts at its own level.
        self.assertEqual(
            self.familiarity(0, 0, "counter_5", spell="fire_bolt"), (5, "generic")
        )

    def test_the_generic_level_is_the_highest_counter_known(self):
        self.assertEqual(
            self.familiarity(0, 0, "counter_1", "counter_4"), (4, "generic")
        )

    def test_a_tie_names_the_first_of_spell_true_and_generic(self):
        # Each pair of sources equal highest, then all three.
        self.assertEqual(self.familiarity(3, 3, "counter_2"), (3, "spell"))
        self.assertEqual(self.familiarity(3, 1, "counter_3"), (3, "spell"))
        self.assertEqual(self.familiarity(1, 3, "counter_3"), (3, "true"))
        self.assertEqual(self.familiarity(3, 3, "counter_3"), (3, "spell"))

    def test_no_familiarity_names_no_source(self):
        self.assertEqual(self.familiarity(0, 0), (0, None))

    def test_a_generic_counter_reads_a_workings_tier(self):
        rules = example_true_magic_rules()
        defender = versed({}, "counter_3")
        self.assertEqual(
            FAMILIARITY_RULES.defender_familiarity(
                defender, rules.working("scorch", 3), 0
            ),
            (3, "generic"),
        )
        self.assertEqual(
            FAMILIARITY_RULES.defender_familiarity(
                defender, rules.working("scorch", 4), 0
            ),
            (0, None),
        )
        # No level is recorded in a working: the True level is what is left.
        self.assertEqual(
            FAMILIARITY_RULES.defender_familiarity(
                defender, rules.working("scorch", 4), 2
            ),
            (2, "true"),
        )


class WorkingKeyTest(TestCase):
    def test_a_working_key_is_told_from_a_catalog_key(self):
        rules = example_true_magic_rules()
        self.assertTrue(is_working_key(rules.working_key("scorch", 1)))
        self.assertTrue(is_working_key("working:anything:at-all"))
        for key in ("fire_missile", "heal", "working", "workings:scorch:1", ""):
            with self.subTest(key=key):
                self.assertFalse(is_working_key(key))

    def test_the_key_is_prefix_effect_and_tier(self):
        rules = example_true_magic_rules()
        self.assertEqual(rules.working_key("scorch", 3), "working:scorch:3")
        self.assertEqual(
            rules.working_key("salve", 12),
            WORKING_KEY_SEPARATOR.join((WORKING_KEY_PREFIX, "salve", "12")),
        )

    def test_a_key_for_an_unknown_effect_or_a_tier_below_one_is_refused(self):
        rules = example_true_magic_rules()
        with self.assertRaises(KeyError):
            rules.working_key("drench", 1)
        with self.assertRaises(ValueError):
            rules.working_key("scorch", 0)

    def test_the_key_gives_back_its_effect_and_tier(self):
        rules = example_true_magic_rules()
        for effect in EFFECTS:
            for tier in (1, 2, 7, 10, 23):
                with self.subTest(effect=effect.key, tier=tier):
                    key = rules.working_key(effect.key, tier)
                    self.assertEqual(rules.effect_and_tier(key), (effect, tier))

    def test_a_key_these_rules_do_not_build_is_a_key_error(self):
        rules = example_true_magic_rules()
        for key in (
            "fire_missile",
            "working",
            "working:scorch",
            "working::1",
            "working:drench:1",
            "working:scorch:0",
            "working:scorch:-1",
            "working:scorch:02",
            "working:scorch:two",
            "working:scorch:1:1",
            "working:scorch:1 ",
            "workings:scorch:1",
        ):
            with self.subTest(key=key), self.assertRaises(KeyError):
                rules.effect_and_tier(key)

    def test_a_tier_whose_dice_cannot_be_built_is_a_key_error(self):
        rules = example_true_magic_rules()
        highest_scorch = MAX_DICE_COUNT
        highest_omen = MAX_DICE_COUNT // 3
        self.assertEqual(
            rules.effect_and_tier(f"working:scorch:{highest_scorch}"),
            (SCORCH, highest_scorch),
        )
        self.assertEqual(
            rules.effect_and_tier(f"working:omen:{highest_omen}"), (OMEN, highest_omen)
        )
        for key in (
            f"working:scorch:{highest_scorch + 1}",
            f"working:omen:{highest_omen + 1}",
            # More digits than a whole number may be read from.
            "working:hush:" + "9" * 5000,
        ):
            with self.subTest(key=key[:24]):
                with self.assertRaises(KeyError) as refusal:
                    rules.effect_and_tier(key)
                self.assertIsInstance(refusal.exception.__cause__, ValueError)

    def test_an_effect_with_no_dice_is_built_at_any_tier(self):
        rules = example_true_magic_rules()
        self.assertEqual(rules.effect_and_tier("working:hush:5000"), (HUSH, 5000))

    def test_working_key_refuses_a_tier_whose_dice_cannot_be_built(self):
        rules = example_true_magic_rules()
        self.assertEqual(
            rules.working_key("omen", MAX_DICE_COUNT // 3),
            f"working:omen:{MAX_DICE_COUNT // 3}",
        )
        with self.assertRaises(ValueError):
            rules.working_key("omen", MAX_DICE_COUNT // 3 + 1)
        with self.assertRaises(ValueError):
            rules.working("scorch", MAX_DICE_COUNT + 1)


class BuiltWorkingTest(TestCase):
    def test_a_damaging_working(self):
        working = example_true_magic_rules().working("scorch", 3)
        self.assertEqual(working.key, "working:scorch:3")
        self.assertEqual(working.name, "Scorch (tier 3)")
        self.assertEqual(working.school, SCHOOL)
        self.assertEqual(working.level, 3)
        self.assertEqual(working.attribute, "WIS")
        self.assertEqual(working.damage, "3d4+3")
        self.assertFalse(working.heals)
        self.assertEqual(working.damage_pool, BODY_POOL)
        self.assertTrue(working.targeted)
        self.assertTrue(working.ignores_armour)
        self.assertFalse(working.continuing)
        self.assertIsNone(working.element)
        self.assertEqual(spell_effect_kind(working), SpellEffectKind.DAMAGE)

    def test_a_healing_working(self):
        working = example_true_magic_rules().working("salve", 2)
        self.assertEqual(working.name, "Salve (tier 2)")
        self.assertEqual(working.damage, "2d8")
        self.assertTrue(working.heals)
        self.assertEqual(working.damage_pool, FATIGUE_POOL)
        self.assertFalse(working.targeted)
        self.assertFalse(working.ignores_armour)
        self.assertFalse(working.continuing)
        self.assertEqual(spell_effect_kind(working), SpellEffectKind.HEALING)

    def test_a_narrated_working_carries_no_rolled_effect(self):
        rules = example_true_magic_rules()
        for effect_key in ("omen", "hush"):
            with self.subTest(effect=effect_key):
                working = rules.working(effect_key, 2)
                self.assertIsNone(working.damage)
                self.assertFalse(working.heals)
                self.assertFalse(working.continuing)
                self.assertIsNone(spell_effect_kind(working))

    def test_its_mana_is_its_tier_and_any_push(self):
        working = example_true_magic_rules().working("scorch", 4)
        self.assertEqual(casting_mana(working, 0), 4)
        self.assertEqual(casting_mana(working, 3), 7)

    def test_a_working_follows_the_injected_roll_attribute(self):
        working = example_true_magic_rules(roll_attribute="INT").working("salve", 1)
        self.assertEqual(working.attribute, "INT")

    def test_an_unknown_effect_or_a_tier_below_one_is_refused(self):
        rules = example_true_magic_rules()
        with self.assertRaises(KeyError):
            rules.working("drench", 1)
        with self.assertRaises(ValueError):
            rules.working("scorch", 0)


class WorkingFromKeyTest(TestCase):
    """One method turns a working's key into its spell."""

    def test_it_builds_the_working_the_key_names(self):
        rules = example_true_magic_rules()
        for effect in EFFECTS:
            for tier in (1, 3, 8):
                with self.subTest(effect=effect.key, tier=tier):
                    key = rules.working_key(effect.key, tier)
                    self.assertEqual(
                        rules.working_from_key(key), rules.working(effect.key, tier)
                    )

    def test_a_key_these_rules_do_not_build_is_a_key_error(self):
        rules = example_true_magic_rules()
        for key in (
            "fire_missile",
            "working:drench:1",
            "working:scorch:0",
            f"working:scorch:{MAX_DICE_COUNT + 1}",
        ):
            with self.subTest(key=key), self.assertRaises(KeyError):
                rules.working_from_key(key)

    def test_spell_for_answers_with_it(self):
        rules = true_magic_rules()
        key = example_true_magic_rules().working_key("omen", 4)
        self.assertEqual(
            spell_for(key, rules), example_true_magic_rules().working_from_key(key)
        )


class SpellForTest(TestCase):
    def test_a_working_key_round_trips(self):
        rules = true_magic_rules()
        true_magic = example_true_magic_rules()
        for effect in EFFECTS:
            for tier in (1, 4, 11):
                with self.subTest(effect=effect.key, tier=tier):
                    working = true_magic.working(effect.key, tier)
                    self.assertEqual(spell_for(working.key, rules), working)
                    self.assertEqual(spell_for(working.key, rules).key, working.key)

    def test_a_catalog_key_goes_to_the_catalog(self):
        for rules in (None, MagicRules(), true_magic_rules()):
            with self.subTest(rules=rules is not None):
                self.assertIs(
                    spell_for("fire_missile", rules), get_spell("fire_missile")
                )

    def test_an_unknown_catalog_key_is_a_key_error(self):
        with self.assertRaises(KeyError):
            spell_for("no_such_spell", true_magic_rules())

    def test_a_working_key_with_no_rules_is_a_key_error(self):
        key = example_true_magic_rules().working_key("scorch", 2)
        for rules in (None, MagicRules(), MagicRules(channel=knot_channel_rules())):
            with self.subTest(rules=rules), self.assertRaises(KeyError):
                spell_for(key, rules)

    def test_a_working_these_rules_do_not_build_is_a_key_error(self):
        rules = true_magic_rules()
        for key in (
            "working:drench:1",
            "working:scorch:0",
            "working:scorch",
            # Tiers whose dice cannot be built: unknown keys, not bad values.
            f"working:scorch:{MAX_DICE_COUNT + 1}",
            f"working:omen:{MAX_DICE_COUNT // 3 + 1}",
        ):
            with self.subTest(key=key), self.assertRaises(KeyError):
                spell_for(key, rules)

    def test_every_key_effect_and_tier_admits_builds(self):
        rules = true_magic_rules()
        true_magic = example_true_magic_rules()
        for effect in EFFECTS:
            highest = effect.highest_buildable_tier() or 5000
            key = true_magic.working_key(effect.key, highest)
            with self.subTest(key=key):
                self.assertEqual(true_magic.effect_and_tier(key), (effect, highest))
                self.assertEqual(spell_for(key, rules).level, highest)


class FailedBandTest(TestCase):
    """One pick for the Control Roll's bands, the debt roll's and the
    interruption roll's."""

    CONTROL_BANDS = (WOBBLE, SPUTTER, TORRENT)

    def band_sets(self):
        return {
            "control": (self.CONTROL_BANDS, WOBBLE, TORRENT),
            "debt": (DEBT_BANDS, FLICKER, SURGE),
            "interruption": (INTERRUPTION_BANDS, SNAG, UNRAVEL),
        }

    def test_a_roll_that_held_has_no_band(self):
        for name, (bands, _first, last) in self.band_sets().items():
            for margin in (0, -1, -9):
                with self.subTest(roll=name, margin=margin):
                    self.assertIsNone(
                        failed_band(bands, margin, automatic=False, automatic_band=last)
                    )

    def test_a_failure_lands_in_the_band_its_margin_picks(self):
        for name, (bands, first, last) in self.band_sets().items():
            with self.subTest(roll=name):
                self.assertIs(
                    failed_band(bands, 1, automatic=False, automatic_band=last),
                    first,
                )
                self.assertIs(
                    failed_band(bands, 40, automatic=False, automatic_band=first),
                    last,
                )

    def test_an_automatic_total_lands_in_its_band_whatever_the_margin(self):
        for name, (bands, first, last) in self.band_sets().items():
            for margin in (-5, 0, 1):
                with self.subTest(roll=name, margin=margin):
                    self.assertIs(
                        failed_band(bands, margin, automatic=True, automatic_band=last),
                        last,
                    )
        # The band is the one the roll names, not one the margin would pick.
        self.assertIs(
            failed_band(DEBT_BANDS, 40, automatic=True, automatic_band=FLICKER),
            FLICKER,
        )

    def test_an_automatic_total_with_no_band_is_refused(self):
        with self.assertRaises(ValueError):
            failed_band(INTERRUPTION_BANDS, 2, automatic=True, automatic_band=None)


class RunawayBandTest(TestCase):
    def test_the_first_runaway_band(self):
        early = replace(SNAG, is_runaway=True)
        self.assertIs(runaway_band(INTERRUPTION_BANDS), UNRAVEL)
        self.assertIs(runaway_band((early, UNRAVEL)), early)
        self.assertIs(runaway_band(DEBT_BANDS), SURGE)

    def test_none_with_no_runaway_band(self):
        self.assertIsNone(runaway_band((SNAG, replace(UNRAVEL, is_runaway=False))))
        self.assertIsNone(runaway_band(()))

    def test_push_rules_answer_with_it(self):
        push = example_push_rules()
        self.assertIs(push.runaway_band(), runaway_band(push.failure_bands))
        self.assertIs(push.runaway_band(), TORRENT)


class SkillLevelTest(TestCase):
    def test_a_general_skill_derivation_reads_its_skill(self):
        self.assertEqual(KNOT.skill_level(knot_caster(4)), 4)

    def test_an_unrecorded_skill_reads_as_the_engine_default(self):
        self.assertEqual(KNOT.skill_level(knot_caster(None)), UNRECORDED_SKILL_LEVEL)

    def test_a_per_spell_derivation_has_no_skill_level(self):
        with self.assertRaises(ValueError):
            LOOM.skill_level(knot_caster(4))

    def test_the_recorded_level_is_the_skill_level_whatever_the_spell(self):
        caster = knot_caster(4)
        for spell_key in ("fire_missile", "heal"):
            with self.subTest(spell=spell_key):
                self.assertEqual(
                    KNOT.recorded_level(caster, get_spell(spell_key)),
                    KNOT.skill_level(caster),
                )


class ChannelKindTest(TestCase):
    """A cast may name the kind of magic its Channel derives by."""

    def test_kind_for_without_a_kind_is_the_spells_own(self):
        rules = knot_channel_rules()
        self.assertEqual(rules.kind_for(get_spell("fire_missile")), "loom")
        self.assertEqual(rules.kind_for(get_spell("heal")), "weft")
        self.assertEqual(rules.kind_for(get_spell("heal"), None), "weft")

    def test_kind_for_with_a_kind_is_that_kind(self):
        rules = knot_channel_rules()
        self.assertEqual(
            rules.kind_for(get_spell("fire_missile"), KNOT_KIND), KNOT_KIND
        )
        self.assertEqual(rules.kind_for(get_spell("heal"), "loom"), "loom")

    def test_a_named_kind_needs_a_derivation(self):
        with self.assertRaises(ValueError) as refusal:
            knot_channel_rules().kind_for(get_spell("heal"), "braid")
        self.assertNotIsInstance(refusal.exception, ChannelUnclassifiedSpell)

    def test_a_named_kind_classifies_a_spell_the_rules_do_not(self):
        rules = replace(knot_channel_rules(), kind_by_spell={}, default_kind=None)
        missile = get_spell("fire_missile")
        with self.assertRaises(ChannelUnclassifiedSpell):
            rules.kind_for(missile)
        self.assertEqual(rules.kind_for(missile, KNOT_KIND), KNOT_KIND)

    def test_channel_derives_by_the_named_kind(self):
        channel = knot_channel_rules()
        magic = MagicRules(channel=channel)
        caster = channel_caster(
            {"fire_missile": 2}, skill_levels={KNOT_SKILL: 3}
        ).by_id(1)
        missile = get_spell("fire_missile")
        own_channel = 2 * 2 - 1
        knot_channel = 3 * 3 + 1
        self.assertEqual(channel.channel_for(caster, missile), own_channel)
        self.assertEqual(channel.channel_for(caster, missile, KNOT_KIND), knot_channel)
        self.assertEqual(channel.bound_for(caster, missile, KNOT_KIND), knot_channel)
        self.assertEqual(magic.channel_for(caster, missile), own_channel)
        self.assertEqual(magic.channel_for(caster, missile, KNOT_KIND), knot_channel)
        self.assertEqual(magic.channel_bound(caster, missile), own_channel)
        self.assertEqual(
            magic.channel_bound(caster, missile, kind=KNOT_KIND), knot_channel
        )

    def test_a_cast_is_within_channel_by_the_named_kind(self):
        magic = MagicRules(channel=knot_channel_rules())
        caster = channel_caster(
            {"fire_missile": 2}, skill_levels={KNOT_SKILL: 3}
        ).by_id(1)
        missile = get_spell("fire_missile")
        # The missile costs 1; pushed by 5 it takes 6, past its own Channel
        # of 3 and within the "knot" Channel of 10.
        self.assertFalse(magic.cast_within_channel(caster, missile, 5))
        self.assertTrue(magic.cast_within_channel(caster, missile, 5, kind=KNOT_KIND))
        self.assertFalse(magic.cast_within_channel(caster, missile, 10, kind=KNOT_KIND))
        self.assertEqual(magic.lifted_bounds_passed(caster, missile, 5), (CHANNEL,))
        self.assertEqual(
            magic.lifted_bounds_passed(caster, missile, 5, kind=KNOT_KIND), ()
        )

    def test_a_lifted_channel_lifts_the_named_kinds_bound_too(self):
        channel = knot_channel_rules()
        caster = channel_caster(
            {}, skill_levels={KNOT_SKILL: 3}, lifted_magic_bounds=[CHANNEL]
        ).by_id(1)
        missile = get_spell("fire_missile")
        self.assertIsNone(channel.bound_for(caster, missile, KNOT_KIND))
        self.assertEqual(channel.channel_for(caster, missile, KNOT_KIND), 10)

    def test_borrowing_with_channel_lifted_counts_the_named_kinds_channel(self):
        magic = MagicRules(
            channel=knot_channel_rules(),
            borrowing=example_borrowing_rules(max_debt=None),
        )
        caster = channel_caster(
            {"fire_missile": 2},
            skill_levels={KNOT_SKILL: 3},
            lifted_magic_bounds=[CHANNEL],
        ).by_id(1)
        missile = get_spell("fire_missile")
        self.assertEqual(
            magic.channel_bound(caster, missile, borrowing=True),
            caster.mana + 3,
        )
        self.assertEqual(
            magic.channel_bound(caster, missile, borrowing=True, kind=KNOT_KIND),
            caster.mana + 10,
        )


class SharesTest(TestCase):
    def test_an_even_split(self):
        rules = example_true_magic_rules()
        self.assertEqual(
            [rules.gathered_after(6, 3, turns_done) for turns_done in range(4)],
            [0, 2, 4, 6],
        )

    def test_an_uneven_split_rounds_each_turn_down(self):
        rules = example_true_magic_rules()
        gathered = [rules.gathered_after(5, 3, turns_done) for turns_done in range(4)]
        self.assertEqual(gathered, [0, 1, 3, 5])
        shares = [later - earlier for earlier, later in zip(gathered, gathered[1:])]
        self.assertEqual(shares, [1, 2, 2])

    def test_more_turns_than_mana_leaves_turns_that_gather_nothing(self):
        rules = example_true_magic_rules()
        self.assertEqual(
            [rules.gathered_after(2, 6, turns_done) for turns_done in range(7)],
            [0, 0, 0, 1, 1, 1, 2],
        )

    def test_the_shares_sum_to_the_whole_mana(self):
        rules = example_true_magic_rules()
        for mana in range(1, 14):
            for turns in range(1, 10):
                with self.subTest(mana=mana, turns=turns):
                    shares = [
                        rules.gathered_after(mana, turns, turns_done)
                        - rules.gathered_after(mana, turns, turns_done - 1)
                        for turns_done in range(1, turns + 1)
                    ]
                    self.assertEqual(sum(shares), mana)
                    self.assertLessEqual(max(shares) - min(shares), 1)
                    self.assertEqual(rules.gathered_after(mana, turns, turns), mana)

    def test_turns_taken_outside_the_crafts_turns_are_refused(self):
        rules = example_true_magic_rules()
        self.assertEqual(rules.gathered_after(10, 6, 0), 0)
        self.assertEqual(rules.gathered_after(10, 6, 6), 10)
        for turns, turns_done in ((6, -1), (6, 7), (6, 12), (0, 0), (-2, -1)):
            with (
                self.subTest(turns=turns, turns_done=turns_done),
                self.assertRaises(ValueError),
            ):
                rules.gathered_after(10, turns, turns_done)

    def test_the_turns_a_tier_takes(self):
        self.assertEqual(example_true_magic_rules().turns_for(1), 3)
        self.assertEqual(example_true_magic_rules().turns_for(4), 12)
        self.assertEqual(example_true_magic_rules(turns_per_tier=2).turns_for(4), 8)

    def test_a_tier_below_one_takes_no_turns_and_is_refused(self):
        # No turns would leave gathered_after nothing to divide the mana by.
        for tier in (0, -1):
            with self.subTest(tier=tier), self.assertRaises(ValueError):
                example_true_magic_rules().turns_for(tier)

    def test_the_tier_cap_a_level_gives(self):
        self.assertEqual(example_true_magic_rules().tier_cap(0), 0)
        self.assertEqual(example_true_magic_rules().tier_cap(3), 6)
        self.assertEqual(example_true_magic_rules(tiers_per_level=5).tier_cap(3), 15)


class DrainTest(TestCase):
    def test_a_casting_that_went_off_drains_per_mana(self):
        self.assertEqual(example_true_magic_rules().drain(4, None), 12)
        self.assertEqual(example_true_magic_rules().drain(0, None), 0)

    def test_a_lost_casting_drains_by_how_it_ended(self):
        rules = example_true_magic_rules()
        self.assertEqual(rules.drain(4, CastingEnding.INTERRUPTED), 8)
        self.assertEqual(rules.drain(4, CastingEnding.COUNTERED), 20)

    def test_an_ending_with_no_entry_drains_nothing(self):
        rules = example_true_magic_rules()
        named = set(rules.lost_drain_per_mana)
        for ending in CastingEnding:
            if ending in named:
                continue
            with self.subTest(ending=ending.value):
                self.assertEqual(rules.drain(4, ending), 0)


class TrueLevelTest(TestCase):
    def test_the_true_level_is_read_off_the_named_derivations_skill(self):
        rules = true_magic_rules()
        self.assertEqual(rules.true_level(knot_caster(3)), 3)
        self.assertEqual(rules.true_level(knot_caster(None)), 0)

    def test_it_follows_the_channel_kind(self):
        rules = true_magic_rules(channel_kind="weft")
        caster = channel_caster({}, skill_levels={"tapestry": 5, KNOT_SKILL: 2}).by_id(
            1
        )
        self.assertEqual(rules.true_level(caster), 5)

    def test_no_true_magic_rules_is_level_zero(self):
        caster = knot_caster(3)
        self.assertEqual(MagicRules().true_level(caster), 0)
        self.assertEqual(MagicRules(channel=knot_channel_rules()).true_level(caster), 0)

    def test_the_tier_cap_is_the_rules_cap_at_the_true_level(self):
        rules = true_magic_rules()
        self.assertEqual(rules.tier_cap(knot_caster(3)), 6)
        self.assertEqual(rules.tier_cap(knot_caster(None)), 0)

    def test_a_lifted_channel_lifts_the_tier_cap(self):
        rules = true_magic_rules()
        self.assertIsNone(rules.tier_cap(knot_caster(3, lifted_magic_bounds=[CHANNEL])))
        # Another lifted bound leaves the cap where it was.
        self.assertEqual(
            rules.tier_cap(knot_caster(3, lifted_magic_bounds=[PUSH_CAP])), 6
        )

    def test_no_true_magic_rules_is_a_cap_of_zero_lifted_or_not(self):
        rules = MagicRules(channel=knot_channel_rules())
        self.assertEqual(rules.tier_cap(knot_caster(3)), 0)
        self.assertEqual(
            rules.tier_cap(knot_caster(3, lifted_magic_bounds=[CHANNEL])), 0
        )

    def test_a_misspelt_lifted_bound_is_refused_as_ever(self):
        with self.assertRaises(ValueError):
            true_magic_rules().tier_cap(knot_caster(3, lifted_magic_bounds=["chanel"]))


class TrueCastableTest(TestCase):
    """What a True mage may True cast: the spells it knows, and the same
    rung of every other element's ladder."""

    def castable_keys(self, rules: MagicRules, caster: CombatantState) -> list[str]:
        return [spell.key for spell in rules.true_castable(caster)]

    def test_each_known_spell_is_followed_by_its_element_siblings(self):
        caster = knot_caster(1, spells=["fire_ball", "heal", "earth_missile"])
        self.assertEqual(
            self.castable_keys(true_magic_rules(), caster),
            [
                "fire_ball",
                "water_ball",
                "lightning_ball",
                "earth_ball",
                "wind_ball",
                "heal",
                "earth_missile",
                "water_missile",
                "fire_missile",
                "lightning_missile",
                "wind_missile",
            ],
        )

    def test_a_spell_reached_twice_is_listed_once_where_it_first_comes(self):
        # Water Ball is Fire Ball's sibling and is known itself.
        caster = knot_caster(1, spells=["fire_ball", "water_ball"])
        self.assertEqual(
            self.castable_keys(true_magic_rules(), caster),
            ["fire_ball", "water_ball", "lightning_ball", "earth_ball", "wind_ball"],
        )

    def test_another_rung_of_a_known_ladder_is_not_castable(self):
        castable = self.castable_keys(
            true_magic_rules(), knot_caster(3, spells=["fire_ball"])
        )
        for key in ("fire_missile", "fire_bolt", "water_missile", "wind_bolt"):
            with self.subTest(key=key):
                self.assertNotIn(key, castable)

    def test_a_generic_counter_is_left_out(self):
        caster = knot_caster(1, spells=["counter_2", "shield", "counter_1"])
        self.assertEqual(self.castable_keys(true_magic_rules(), caster), ["shield"])
        only_counters = knot_caster(1, spells=["counter_3"])
        self.assertEqual(true_magic_rules().true_castable(only_counters), ())

    def test_nothing_is_castable_with_no_level_in_the_true_skill(self):
        for knotwork_level in (None, 0):
            with self.subTest(knotwork_level=knotwork_level):
                caster = knot_caster(knotwork_level, spells=["fire_ball", "heal"])
                self.assertEqual(true_magic_rules().true_castable(caster), ())

    def test_nothing_is_castable_with_no_true_magic_rules(self):
        caster = knot_caster(3, spells=["fire_ball", "heal"])
        for rules in (MagicRules(), MagicRules(channel=knot_channel_rules())):
            with self.subTest(channel=rules.channel is not None):
                self.assertEqual(rules.true_castable(caster), ())

    def test_a_known_spell_the_catalog_does_not_hold_is_a_key_error(self):
        caster = knot_caster(1, spells=["fire_ball", "no_such_spell"])
        with self.assertRaises(KeyError):
            true_magic_rules().true_castable(caster)


class CastKindTest(TestCase):
    def test_a_recipe_cast_names_no_kind(self):
        for rules in (MagicRules(), true_magic_rules()):
            with self.subTest(true_magic=rules.true_magic is not None):
                self.assertIsNone(rules.cast_kind(get_spell("fire_missile")))
                self.assertIsNone(rules.cast_kind(get_spell("heal"), true_cast=False))

    def test_a_true_cast_names_the_true_kind(self):
        rules = true_magic_rules()
        self.assertEqual(
            rules.cast_kind(get_spell("fire_missile"), true_cast=True), KNOT_KIND
        )

    def test_a_working_names_the_true_kind_with_or_without_the_flag(self):
        rules = true_magic_rules()
        working = example_true_magic_rules().working("salve", 2)
        self.assertEqual(rules.cast_kind(working), KNOT_KIND)
        self.assertEqual(rules.cast_kind(working, true_cast=True), KNOT_KIND)

    def test_a_true_cast_with_no_true_magic_rules_is_refused(self):
        rules = MagicRules(channel=knot_channel_rules())
        working = example_true_magic_rules().working("salve", 2)
        with self.assertRaises(ValueError):
            rules.cast_kind(get_spell("fire_missile"), true_cast=True)
        with self.assertRaises(ValueError):
            rules.cast_kind(working)

    def test_the_kind_it_names_gives_a_working_its_channel(self):
        rules = true_magic_rules()
        caster = knot_caster(2)
        working = example_true_magic_rules().working("scorch", 4)
        kind = rules.cast_kind(working)
        self.assertEqual(rules.channel_for(caster, working, kind), 3 * 2 + 1)
        self.assertTrue(rules.cast_within_channel(caster, working, 3, kind=kind))
        self.assertFalse(rules.cast_within_channel(caster, working, 4, kind=kind))

    def test_a_working_derives_by_the_true_kind_when_no_kind_is_named(self):
        rules = true_magic_rules()
        # "tapestry" 9 and a level of 6 in the working's own key: either
        # would give a Channel other than the "knot" kind's 3 x 2 + 1.
        working = example_true_magic_rules().working("scorch", 4)
        caster = channel_caster(
            {working.key: 6}, skill_levels={KNOT_SKILL: 2, "tapestry": 9}
        ).by_id(1)
        knot_channel = 3 * 2 + 1
        self.assertEqual(rules.channel_for(caster, working), knot_channel)
        self.assertEqual(rules.channel_bound(caster, working), knot_channel)
        self.assertTrue(rules.cast_within_channel(caster, working, 3))
        self.assertFalse(rules.cast_within_channel(caster, working, 4))
        self.assertEqual(rules.lifted_bounds_passed(caster, working, 3), ())
        self.assertEqual(rules.lifted_bounds_passed(caster, working, 4), (CHANNEL,))

    def test_a_working_with_no_true_magic_rules_has_no_channel_to_ask(self):
        working = example_true_magic_rules().working("scorch", 4)
        caster = knot_caster(2)
        for rules in (MagicRules(), MagicRules(channel=knot_channel_rules())):
            with self.subTest(channel=rules.channel is not None):
                with self.assertRaises(ValueError):
                    rules.channel_for(caster, working)
                with self.assertRaises(ValueError):
                    rules.channel_bound(caster, working)
                with self.assertRaises(ValueError):
                    rules.lifted_bounds_passed(caster, working)

    def test_a_true_cast_of_a_catalog_spell_must_name_its_kind(self):
        # Nothing about a catalog spell says a cast of it is a True cast:
        # with no kind named it derives as a recipe cast does.
        rules = true_magic_rules()
        caster = channel_caster(
            {"fire_missile": 2}, skill_levels={KNOT_SKILL: 3}
        ).by_id(1)
        missile = get_spell("fire_missile")
        self.assertEqual(rules.channel_for(caster, missile), 2 * 2 - 1)
        self.assertEqual(
            rules.channel_for(
                caster, missile, rules.cast_kind(missile, true_cast=True)
            ),
            3 * 3 + 1,
        )


class WorkingLiftedBoundsTest(TestCase):
    """No bound of its own names the tier cap: a working past it reports
    Channel, as one whose mana is past Channel does."""

    def setUp(self):
        self.rules = true_magic_rules()
        # "knotwork" 2: a tier cap of 4 and a Channel of 7.
        self.caster = knot_caster(2)

    def passed(self, tier: int, push_mana: int = 0, **caster_changes):
        working = example_true_magic_rules().working("scorch", tier)
        caster = replace(self.caster, **caster_changes)
        return self.rules.lifted_bounds_passed(
            caster, working, push_mana, kind=self.rules.cast_kind(working)
        )

    def test_a_working_within_both_passes_nothing(self):
        self.assertEqual(self.passed(4), ())
        self.assertEqual(self.passed(4, 3), ())
        self.assertEqual(self.passed(1), ())

    def test_a_tier_past_the_cap_reports_channel(self):
        # Tier 5 is past the cap of 4 and its mana of 5 within the Channel of 7.
        self.assertEqual(self.passed(5), (CHANNEL,))

    def test_mana_past_channel_reports_channel(self):
        # Tier 4 is within the cap; pushed by 4 its mana of 8 is past Channel.
        self.assertEqual(self.passed(4, 4), (CHANNEL,))

    def test_past_both_reports_channel_once(self):
        self.assertEqual(self.passed(9), (CHANNEL,))

    def test_it_reports_the_bound_whether_or_not_it_is_lifted(self):
        self.assertEqual(self.passed(5, lifted_magic_bounds=[CHANNEL]), (CHANNEL,))

    def test_a_catalog_spell_is_held_to_no_tier_cap(self):
        # A level-3 spell cast by a caster whose tier cap is 0: only its
        # Channel is asked.
        caster = channel_caster({"lightning_bolt": 4}, skill_levels={}).by_id(1)
        bolt = get_spell("lightning_bolt")
        self.assertEqual(self.rules.tier_cap(caster), 0)
        self.assertEqual(self.rules.lifted_bounds_passed(caster, bolt), ())


def craft_of(spell_key: str, *, push_mana: int = 0, turns_done: int = 0) -> CraftState:
    """A craft of a working, some of its turns taken; nothing borrowed and
    no figure named."""
    return CraftState(
        spell_key=spell_key,
        push_mana=push_mana,
        borrow=False,
        turns_done=turns_done,
        borrowed=0,
        target_id=None,
    )


class GatheredTest(TestCase):
    """The mana a craft has gathered is worked out from the craft, never
    stored on it."""

    def gathered_each_turn(self, rules: TrueMagicRules, tier: int, push_mana: int):
        key = rules.working_key("scorch", tier)
        return [
            rules.gathered(craft_of(key, push_mana=push_mana, turns_done=turns_done))
            for turns_done in range(rules.turns_for(tier) + 1)
        ]

    def test_it_rises_turn_by_turn_to_the_tier_and_the_push(self):
        # Tier 2 takes 2 x 3 turns; pushed by 3 its whole mana is 5.
        self.assertEqual(
            self.gathered_each_turn(example_true_magic_rules(), 2, 3),
            [0, 0, 1, 2, 3, 4, 5],
        )

    def test_an_unpushed_craft_gathers_its_tier(self):
        # Tier 3 at 2 turns a tier: 3 mana over 6 turns.
        self.assertEqual(
            self.gathered_each_turn(example_true_magic_rules(turns_per_tier=2), 3, 0),
            [0, 0, 1, 1, 2, 2, 3],
        )

    def test_it_is_gathered_after_for_the_crafts_own_working(self):
        rules = example_true_magic_rules()
        for tier in (1, 2, 5):
            for push_mana in (0, 1, 4):
                turns = rules.turns_for(tier)
                for turns_done in range(turns + 1):
                    with self.subTest(
                        tier=tier, push_mana=push_mana, turns_done=turns_done
                    ):
                        craft = craft_of(
                            rules.working_key("salve", tier),
                            push_mana=push_mana,
                            turns_done=turns_done,
                        )
                        self.assertEqual(
                            rules.gathered(craft),
                            rules.gathered_after(tier + push_mana, turns, turns_done),
                        )

    def test_what_was_borrowed_and_who_is_named_change_nothing(self):
        rules = example_true_magic_rules()
        key = rules.working_key("scorch", 2)
        plain = craft_of(key, push_mana=1, turns_done=4)
        borrowing = CraftState(
            spell_key=key,
            push_mana=1,
            borrow=True,
            turns_done=4,
            borrowed=2,
            target_id=7,
        )
        self.assertEqual(rules.gathered(borrowing), rules.gathered(plain))

    def test_a_craft_of_no_working_these_rules_build_is_a_key_error(self):
        rules = example_true_magic_rules()
        for key in ("fire_missile", "working:drench:1", "working:scorch:0"):
            with self.subTest(key=key), self.assertRaises(KeyError):
                rules.gathered(craft_of(key, turns_done=1))

    def test_a_craft_past_its_last_turn_or_before_its_first_is_refused(self):
        rules = example_true_magic_rules()
        key = rules.working_key("scorch", 2)
        last_turn = rules.turns_for(2)
        self.assertEqual(
            rules.gathered(craft_of(key, push_mana=3, turns_done=last_turn)), 5
        )
        for turns_done in (-1, last_turn + 1, 2 * last_turn):
            with self.subTest(turns_done=turns_done), self.assertRaises(ValueError):
                rules.gathered(craft_of(key, push_mana=3, turns_done=turns_done))

    def test_a_craft_that_pushes_less_than_nothing_is_refused(self):
        rules = example_true_magic_rules()
        key = rules.working_key("scorch", 2)
        self.assertEqual(rules.gathered(craft_of(key, push_mana=0, turns_done=6)), 2)
        for push_mana in (-1, -2, -5):
            with self.subTest(push_mana=push_mana), self.assertRaises(ValueError):
                rules.gathered(craft_of(key, push_mana=push_mana, turns_done=3))


class GrantedTest(TestCase):
    """A Game Master's grants are read through the rules, which refuse a
    grant of a working they do not build."""

    def test_a_listed_working_is_granted_and_another_is_not(self):
        rules = true_magic_rules()
        caster = knot_caster(2, granted_workings=["working:omen:2", "working:hush:1"])
        self.assertTrue(rules.granted(caster, "working:omen:2"))
        self.assertTrue(rules.granted(caster, "working:hush:1"))
        # The grant is of a tier, not of the effect.
        self.assertFalse(rules.granted(caster, "working:omen:3"))
        self.assertFalse(rules.granted(caster, "working:scorch:2"))

    def test_nothing_is_granted_to_a_figure_with_no_grants(self):
        self.assertFalse(true_magic_rules().granted(knot_caster(2), "working:omen:2"))

    def test_a_grant_these_rules_do_not_build_is_refused(self):
        rules = true_magic_rules()
        for bad_grant in (
            "working:omn:2",
            "working:omen:0",
            "working:omen",
            "fire_missile",
            f"working:omen:{MAX_DICE_COUNT // 3 + 1}",
        ):
            caster = knot_caster(2, granted_workings=["working:omen:2", bad_grant])
            with self.subTest(bad_grant=bad_grant):
                # Whatever is asked, a listed key that is granted included.
                with self.assertRaises(ValueError):
                    rules.granted(caster, "working:omen:2")
                with self.assertRaises(ValueError):
                    rules.granted(caster, "working:scorch:1")

    def test_asking_after_a_key_these_rules_do_not_build_is_refused(self):
        rules = true_magic_rules()
        caster = knot_caster(2, granted_workings=["working:omen:2"])
        for asked in ("working:omn:2", "fire_missile", "working:omen:0"):
            with self.subTest(asked=asked), self.assertRaises(ValueError):
                rules.granted(caster, asked)

    def test_with_no_true_magic_rules_every_grant_is_refused(self):
        caster = knot_caster(2, granted_workings=["working:omen:2"])
        for rules in (MagicRules(), MagicRules(channel=knot_channel_rules())):
            with self.subTest(channel=rules.channel is not None):
                with self.assertRaises(ValueError):
                    rules.granted(caster, "working:omen:2")

    def test_reading_a_grant_leaves_the_list_as_it_was(self):
        rules = true_magic_rules()
        grants = ["working:omen:2", "working:hush:1"]
        caster = knot_caster(2, granted_workings=list(grants))
        rules.granted(caster, "working:omen:2")
        rules.granted(caster, "working:omen:4")
        self.assertEqual(caster.granted_workings, grants)
