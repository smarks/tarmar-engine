"""Pins every entry of ``tarmar_engine.house_rulings`` to the code using it.

tarmar-studio #826: each number the engine uses with no page behind it gets
a ruling (marked for Spencer) or a page, "and then a drift test". A change
to the record or to the code without the other fails here.
"""

from unittest import TestCase

import tarmar_rules

from tarmar_engine import actions, engine, hexes, policy, spells
from tarmar_engine.house_rulings import HOUSE_RULINGS, PAGE, RULING, TACTIC, ruling


class HouseRulingsRecordTest(TestCase):
    def test_every_entry_names_its_authority_and_issue(self):
        keys = [entry.key for entry in HOUSE_RULINGS]
        self.assertEqual(len(keys), len(set(keys)))
        for entry in HOUSE_RULINGS:
            with self.subTest(key=entry.key):
                self.assertTrue(
                    any(mark in entry.authority for mark in (PAGE, RULING, TACTIC))
                )
                self.assertTrue(entry.issue.startswith("#"))

    def test_spell_magnitudes(self):
        value = ruling("spell_magnitudes").value
        for key in (
            "fire_missile",
            "fire_ball",
            "lightning_bolt",
            "heal",
            "fatigue",
            "wound",
        ):
            self.assertEqual(spells.SPELLS[key].damage, value[key], key)
        self.assertEqual(spells.SPELLS["shield"].tn_bonus, value["shield_tn_bonus"])
        self.assertEqual(
            spells.SPELLS["blur"].attacker_penalty, value["blur_attacker_penalty"]
        )

    def test_geometry_and_tactics(self):
        self.assertEqual(hexes.HEXES_PER_MEGAHEX_STEP, ruling("megahex_step").value)
        self.assertEqual(engine.WALK_SLOW_MAX, ruling("walk_slow_step").value)
        self.assertEqual(engine.PREFERRED_STANDOFF, ruling("preferred_standoff").value)

    def test_plate_cracking_rounds_down(self):
        # 5 stops halved: 2 still apply, so a 7 gets 5 through.
        self.assertEqual(
            tarmar_rules.damage_after_armour(7, 5, "Heavy Striking", "Heavy"), 5
        )
        self.assertEqual(ruling("plate_cracking_rounding").value, "stops // 2")

    def test_the_review_rulings(self):
        from tarmar_engine import combat_math
        from tarmar_engine.state import BattleState, WeaponState

        from .test_state import make_combatant

        bare = WeaponState(damage="1d6-2")
        first = make_combatant(1, q=0, r=0, facing=0, weapon=bare)
        second = make_combatant(2, q=1, r=0, facing=3, weapon=bare)
        state = BattleState(arena_radius=6, combatants=[first, second])
        self.assertIn(
            combat_math.hth_entry_reason(state, first, second),
            ruling("hth_by_agreement").value,
        )
        # The arena edge: a target at the rim facing inward.
        edge = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=5, r=0, facing=0),
                make_combatant(2, q=6, r=0, facing=3),
            ],
        )
        self.assertEqual(
            combat_math.hth_entry_reason(edge, edge.by_id(1), edge.by_id(2)),
            "their back is to the wall",
        )
        self.assertEqual(ruling("squeeze_defences").authority, RULING)
        self.assertEqual(ruling("unconscious_caster_spells_end").authority, RULING)
        self.assertEqual(ruling("dodge_closes_on_missile_threat").authority, TACTIC)

    def test_the_pass_rulings(self):
        self.assertEqual(
            engine.CASTING_MANA_LOST_ROLLS, ruling("mana_on_failure").value
        )
        self.assertEqual(
            tarmar_rules.CRIT_DAMAGE_ROLLS,
            ruling("critical_modifier_once").value["critical_dice_rolls"],
        )
        self.assertEqual(
            tarmar_rules.SEVERE_CRIT_DAMAGE_ROLLS,
            ruling("critical_modifier_once").value["severe_dice_rolls"],
        )
        self.assertEqual(ruling("forced_retreat_choice").value, "policy.choose_retreat")
        self.assertTrue(callable(policy.choose_retreat))
        self.assertIn(
            "q",
            actions.legal_actions(
                engaged=False,
                prone=False,
                has_missile=False,
                has_spells=False,
                has_melee_target=False,
                can_pick_up=True,
            ),
        )
