"""Engine state dataclasses: serialization round-trip and derived values."""

import json
from unittest import TestCase

from tarmar_engine.state import (
    BattleState,
    CombatantState,
    WeaponState,
    bare_handed_damage,
)


def make_combatant(combatant_id=1, **overrides):
    fields = {
        "combatant_id": combatant_id,
        "name": f"Fighter {combatant_id}",
        "strength": 12,
        "dexterity": 12,
        "intelligence": 10,
        "wisdom": 10,
        "constitution": 12,
        "max_fatigue": 40,
        "max_body": 27,
        "fatigue": 40,
        "body": 27,
        "weapon": WeaponState(
            item_id="broadsword",
            name="Broadsword",
            weapon_class="Striking",
            damage="2d6",
            str_req=12,
        ),
    }
    fields.update(overrides)
    return CombatantState(**fields)


class BareHandedDamageTest(TestCase):
    def test_band_lookup(self):
        self.assertEqual(bare_handed_damage(8), "1d6-4")
        self.assertEqual(bare_handed_damage(9), "1d6-3")
        self.assertEqual(bare_handed_damage(15), "1d6")
        self.assertEqual(bare_handed_damage(30), "1d6+3")
        self.assertEqual(bare_handed_damage(50), "3d6+1")

    def test_above_table_uses_top_band(self):
        self.assertEqual(bare_handed_damage(60), "3d6+1")


class CombatantStateTest(TestCase):
    def test_active_requires_alive_and_conscious(self):
        combatant = make_combatant()
        self.assertTrue(combatant.active)
        combatant.conscious = False
        self.assertFalse(combatant.active)
        combatant.conscious = True
        combatant.alive = False
        self.assertFalse(combatant.active)

    def test_position_property_round_trips(self):
        combatant = make_combatant(q=2, r=-3)
        self.assertEqual(combatant.position, (2, -3))
        combatant.position = (0, 1)
        self.assertEqual((combatant.q, combatant.r), (0, 1))

    def test_renewal_order_key_is_dex_int_wis(self):
        combatant = make_combatant(dexterity=14, intelligence=12, wisdom=9)
        self.assertEqual(combatant.renewal_order_key, 35)

    def test_reset_for_turn_clears_turn_flags(self):
        combatant = make_combatant(
            defending=True,
            dodging=True,
            yielded=True,
            chosen_letter="j",
            chosen_target=4,
            chosen_spell="fire_missile",
            moved_this_turn=True,
            dealt_damage_this_turn=True,
            took_damage_this_turn=True,
        )
        combatant.reset_for_turn()
        self.assertFalse(combatant.defending)
        self.assertFalse(combatant.dodging)
        self.assertFalse(combatant.yielded)
        self.assertEqual(combatant.chosen_letter, "")
        self.assertIsNone(combatant.chosen_target)
        self.assertEqual(combatant.chosen_spell, "")
        self.assertFalse(combatant.moved_this_turn)
        self.assertFalse(combatant.dealt_damage_this_turn)
        self.assertFalse(combatant.took_damage_this_turn)

    def test_reset_for_turn_leaves_grapple_state_alone(self):
        # The hold persists across turns until Struggle Free or Release —
        # it is not a per-turn flag like defending/dodging.
        combatant = make_combatant(grappled_by=7, grappling=None)
        combatant.reset_for_turn()
        self.assertEqual(combatant.grappled_by, 7)
        self.assertIsNone(combatant.grappling)


class BattleStateTest(TestCase):
    def test_enemies_of_excludes_self_and_inactive(self):
        state = BattleState(
            combatants=[
                make_combatant(1),
                make_combatant(2),
                make_combatant(3, alive=False),
            ]
        )
        enemies = state.enemies_of(state.by_id(1))
        self.assertEqual([enemy.combatant_id for enemy in enemies], [2])

    def test_by_id_raises_for_unknown(self):
        state = BattleState(combatants=[make_combatant(1)])
        with self.assertRaises(KeyError):
            state.by_id(99)

    def test_occupied_hexes_counts_the_unconscious_but_not_the_dead(self):
        # A corpse no longer blocks a hex (movement.md's "standing on a body"
        # modifier is out of scope); an unconscious figure still does.
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=1, r=0, alive=False),
                make_combatant(3, q=2, r=0, conscious=False),
            ]
        )
        self.assertEqual(state.occupied_hexes(), {(0, 0), (2, 0)})

    def test_json_round_trip_preserves_everything(self):
        state = BattleState(
            arena_radius=6,
            turn=3,
            next_sequence=42,
            combatants=[
                make_combatant(
                    1,
                    q=2,
                    r=-1,
                    facing=4,
                    fatigue=-3,
                    spells=["fire_missile"],
                    active_spells=["shield"],
                    fatal_chain=[7, 8],
                    off_balance=True,
                    grappled_by=None,
                    grappling=9,
                )
            ],
        )
        # Through real JSON, as Battle.state_json stores it.
        restored = BattleState.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(restored, state)
        self.assertEqual(restored.combatants[0].weapon.name, "Broadsword")
        self.assertEqual(restored.combatants[0].grappling, 9)

    def test_from_dict_defaults_for_missing_keys(self):
        state = BattleState.from_dict({})
        self.assertEqual(state.arena_radius, 8)
        self.assertEqual(state.turn, 0)
        self.assertEqual(state.combatants, [])
