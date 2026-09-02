"""Attack arithmetic: arcs, ranges, defend/dodge, spell effects, expectations."""

from unittest import TestCase

from tarmar_engine import combat_math
from tarmar_engine.state import BattleState, WeaponState

from .test_state import make_combatant


class AttackNumbersTest(TestCase):
    def setUp(self):
        # Attacker west of the defender; defender faces east (away).
        self.attacker = make_combatant(1, q=-1, r=0, facing=0)
        self.defender = make_combatant(2, q=0, r=0, facing=0)

    def test_rear_attack_gets_plus_four(self):
        numbers = combat_math.attack_numbers(self.attacker, self.defender)
        self.assertEqual(numbers.arc, "rear")
        self.assertEqual(numbers.situational, 4)

    def test_front_attack_gets_no_positional_bonus(self):
        self.defender.facing = 3  # now faces the attacker
        numbers = combat_math.attack_numbers(self.attacker, self.defender)
        self.assertEqual(numbers.arc, "front")
        self.assertEqual(numbers.situational, 0)

    def test_prone_defender_counts_as_rear(self):
        self.defender.facing = 3
        self.defender.prone = True
        numbers = combat_math.attack_numbers(self.attacker, self.defender)
        self.assertEqual(numbers.situational, 4)

    def test_defend_raises_tn_against_melee_only(self):
        self.defender.defending = True
        melee = combat_math.attack_numbers(self.attacker, self.defender)
        base = combat_math.attack_numbers(
            self.attacker, make_combatant(2, q=0, r=0, facing=0)
        )
        self.assertEqual(melee.target_number, base.target_number + 4)

    def test_dodge_raises_tn_against_missiles_only(self):
        archer = make_combatant(
            1,
            q=-6,
            r=0,
            weapon=WeaponState(
                item_id="longbow",
                name="Longbow",
                weapon_class="Missile — Bows",
                damage="1d6+2",
                is_missile=True,
            ),
        )
        self.defender.dodging = True
        dodged = combat_math.attack_numbers(archer, self.defender, ranged=True)
        self.defender.dodging = False
        undodged = combat_math.attack_numbers(archer, self.defender, ranged=True)
        self.assertEqual(dodged.target_number, undodged.target_number + 4)
        # Dodge does nothing against the melee swing.
        self.defender.dodging = True
        melee_dodging = combat_math.attack_numbers(self.attacker, self.defender)
        self.defender.dodging = False
        melee_plain = combat_math.attack_numbers(self.attacker, self.defender)
        self.assertEqual(melee_dodging.target_number, melee_plain.target_number)

    def test_missile_range_penalty_applies(self):
        archer = make_combatant(
            1,
            q=-9,
            r=0,
            weapon=WeaponState(
                item_id="longbow",
                name="Longbow",
                weapon_class="Missile — Bows",
                damage="1d6+2",
                is_missile=True,
            ),
        )
        numbers = combat_math.attack_numbers(archer, self.defender, ranged=True)
        self.assertEqual(numbers.distance, 9)  # 3 MH band: -1
        self.assertEqual(numbers.range_penalty, -1)

    def test_thrown_range_penalty_is_per_hex(self):
        thrower = make_combatant(
            1,
            q=-4,
            r=0,
            weapon=WeaponState(
                item_id="hatchet",
                name="Hatchet",
                weapon_class="Striking",
                damage="1d6",
                is_thrown=True,
            ),
        )
        numbers = combat_math.attack_numbers(thrower, self.defender, ranged=True)
        self.assertEqual(numbers.range_penalty, -4)

    def test_active_shield_spell_raises_tn(self):
        base = combat_math.attack_numbers(self.attacker, self.defender)
        self.defender.active_spells = ["shield"]
        shielded = combat_math.attack_numbers(self.attacker, self.defender)
        self.assertEqual(shielded.target_number, base.target_number + 1)

    def test_active_blur_penalizes_the_attacker(self):
        base = combat_math.attack_numbers(self.attacker, self.defender)
        self.defender.active_spells = ["blur"]
        blurred = combat_math.attack_numbers(self.attacker, self.defender)
        self.assertEqual(blurred.bonus, base.bonus - 2)

    def test_under_strength_weapon_penalizes(self):
        self.attacker.strength = 9  # broadsword needs 12
        numbers = combat_math.attack_numbers(self.attacker, self.defender)
        strong = combat_math.attack_numbers(make_combatant(1, q=-1, r=0), self.defender)
        self.assertEqual(numbers.bonus, strong.bonus - 3)


class HthOverrideTest(TestCase):
    """The grapple sub-flow's attack_numbers hooks (issue #231)."""

    def setUp(self):
        self.attacker = make_combatant(1, q=-1, r=0, facing=0)
        self.defender = make_combatant(2, q=0, r=0, facing=3)  # faces attacker

    def test_weapon_class_override_replaces_the_readied_weapon(self):
        # Striking/Medium is 16; Flexible/Snare/Medium is 19 — a flat +3
        # over the same shield/dodge bonuses either way.
        self.defender.armour_tier = "Medium"
        plain = combat_math.attack_numbers(self.attacker, self.defender)
        snared = combat_math.attack_numbers(
            self.attacker, self.defender, weapon_class="Flexible / Snare"
        )
        self.assertEqual(snared.target_number, plain.target_number + 3)

    def test_extra_situational_adds_a_flat_bonus(self):
        plain = combat_math.attack_numbers(self.attacker, self.defender)
        boosted = combat_math.attack_numbers(
            self.attacker, self.defender, extra_situational=4
        )
        self.assertEqual(boosted.bonus, plain.bonus + 4)
        self.assertEqual(boosted.situational, plain.situational + 4)

    def test_ignore_attacker_skill_drops_skill_and_str_fit(self):
        self.attacker.weapon_skill_level = 3  # +3 to hit normally
        self.attacker.strength = 9  # broadsword needs 12: -3 normally
        skilled = combat_math.attack_numbers(self.attacker, self.defender)
        unskilled = combat_math.attack_numbers(
            self.attacker, self.defender, ignore_attacker_skill=True
        )
        self.assertEqual(unskilled.bonus, skilled.bonus - 3 + 3)

    def test_ignore_defender_bonuses_drops_shield_dodge_and_defend(self):
        self.defender.shield_bonus = 2
        self.defender.defending = True
        self.defender.dexterity = 16  # +1 dodge modifier
        self.defender.active_spells = ["shield"]  # +1 TN
        normal = combat_math.attack_numbers(self.attacker, self.defender)
        held = combat_math.attack_numbers(
            self.attacker, self.defender, ignore_defender_bonuses=True
        )
        self.assertLess(held.target_number, normal.target_number)
        # Only the matrix base (Striking/None = 13) is left.
        self.assertEqual(held.target_number, 13)


class ExpectedDamageTest(TestCase):
    def test_no_stops_is_the_plain_mean(self):
        self.assertAlmostEqual(combat_math.expected_damage("2d6", 0), 7.0)

    def test_truncation_at_zero_is_exact_not_clamped_mean(self):
        # E[max(2d6 - 3, 0)] = 145/36, a touch above the clamped mean of 4.
        self.assertAlmostEqual(combat_math.expected_damage("2d6", 3), 145 / 36)

    def test_damage_that_can_never_penetrate_is_zero(self):
        self.assertAlmostEqual(combat_math.expected_damage("1d6-4", 3), 0.0)

    def test_high_rolls_still_penetrate_heavy_stops(self):
        # A saber's 2d6-2 mean (5) is at the stops, but high rolls get
        # through — the reason the AI must not score armour as unbeatable.
        self.assertGreater(combat_math.expected_damage("2d6-2", 5), 0.5)

    def test_crit_repetitions_roll_the_dice_twice_against_one_armour(self):
        self.assertAlmostEqual(
            combat_math.expected_damage("2d6+2", 2, repetitions=2), 16.0
        )

    def test_expected_attack_damage_uses_hybrid_stop_rule(self):
        smasher = make_combatant(
            1,
            weapon=WeaponState(
                item_id="great_hammer",
                name="Great Hammer",
                weapon_class="Heavy Striking",
                damage="2d6+2",
            ),
        )
        plated = make_combatant(2, armour_tier="Heavy", stops=5)
        # Hybrid rule: Heavy Striking vs Heavy armour applies stops // 2 = 2;
        # one roll expects 7, the 1-in-20 crit path adds (16 - 7) / 20.
        self.assertAlmostEqual(
            combat_math.expected_attack_damage(smasher, plated),
            7.0 + 9.0 / 20,
        )


class SpellStateHelpersTest(TestCase):
    def test_bonuses_sum_across_active_spells(self):
        defender = make_combatant(1, active_spells=["shield", "blur"])
        self.assertEqual(combat_math.spell_tn_bonus(defender), 1)
        self.assertEqual(combat_math.spell_attacker_penalty(defender), 2)


class StateImportSmokeTest(TestCase):
    def test_battle_state_is_importable_from_combat_math_module(self):
        # Guards the module wiring the engine relies on.
        self.assertIsInstance(BattleState(), BattleState)
