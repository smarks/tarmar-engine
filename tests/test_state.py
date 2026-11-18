"""Engine state dataclasses: serialization round-trip and derived values."""

import json
from unittest import TestCase

from tarmar_engine.state import (
    BattleState,
    CombatantState,
    CraftState,
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

    def test_enemies_of_excludes_living_teammates(self):
        state = BattleState(
            combatants=[
                make_combatant(1, team="Red"),
                make_combatant(2, team="Red"),
                make_combatant(3, team="Blue"),
                make_combatant(4),  # free-for-all: everyone's enemy
            ]
        )
        red_enemies = state.enemies_of(state.by_id(1))
        self.assertEqual([enemy.combatant_id for enemy in red_enemies], [3, 4])
        # A teamless combatant fights everyone, teamed included.
        solo_enemies = state.enemies_of(state.by_id(4))
        self.assertEqual([enemy.combatant_id for enemy in solo_enemies], [1, 2, 3])

    def test_two_teamless_combatants_are_enemies(self):
        # Empty team is "own team of one", never a shared team.
        state = BattleState(combatants=[make_combatant(1), make_combatant(2)])
        self.assertEqual(
            [enemy.combatant_id for enemy in state.enemies_of(state.by_id(1))], [2]
        )

    def test_teammates_of_are_the_others_sharing_the_team_tag(self):
        state = BattleState(
            combatants=[
                make_combatant(1, team="Red"),
                make_combatant(2, team="Blue"),
                make_combatant(3, team="Red"),
                make_combatant(4),
                make_combatant(5, team="Red"),
            ]
        )
        teammates = state.teammates_of(state.by_id(1))
        self.assertEqual([mate.combatant_id for mate in teammates], [3, 5])
        self.assertEqual(
            [mate.combatant_id for mate in state.teammates_of(state.by_id(5))], [1, 3]
        )
        self.assertEqual(state.teammates_of(state.by_id(2)), [])

    def test_teammates_of_counts_the_unconscious_but_not_the_dead(self):
        state = BattleState(
            combatants=[
                make_combatant(1, team="Red"),
                make_combatant(2, team="Red", conscious=False),
                make_combatant(3, team="Red", alive=False),
            ]
        )
        teammates = state.teammates_of(state.by_id(1))
        self.assertEqual([mate.combatant_id for mate in teammates], [2])
        # A figure that is down still has its living teammates.
        self.assertEqual(
            [mate.combatant_id for mate in state.teammates_of(state.by_id(2))], [1]
        )

    def test_a_teamless_figure_has_no_teammates(self):
        # An empty tag is a team of one: two teamless figures share nothing.
        state = BattleState(
            combatants=[
                make_combatant(1),
                make_combatant(2),
                make_combatant(3, team="Red"),
            ]
        )
        self.assertEqual(state.teammates_of(state.by_id(1)), [])
        self.assertEqual(state.teammates_of(state.by_id(3)), [])

    def test_team_round_trips_and_defaults_empty(self):
        state = BattleState(combatants=[make_combatant(1, team="Red")])
        restored = BattleState.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(restored.combatants[0].team, "Red")
        # A snapshot written before the team field existed loads teamless.
        legacy = state.to_dict()
        del legacy["combatants"][0]["team"]
        self.assertEqual(BattleState.from_dict(legacy).combatants[0].team, "")

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


#: The combatant fields this slice of True Magic adds (tarmar-engine #77).
TRUE_MAGIC_FIELDS = ("crafting", "granted_workings", "chosen_true_cast")


class TrueMagicSnapshotTest(TestCase):
    """A craft, a Game Master's granted workings and a True cast choice
    (tarmar-engine #77) are written to a snapshot only when set. The keys and
    numbers are invented."""

    def test_a_craft_the_grants_and_the_choice_round_trip(self):
        state = BattleState(
            combatants=[make_combatant(1), make_combatant(2), make_combatant(3)]
        )
        state.by_id(1).crafting = CraftState(
            spell_key="working:scorch:4",
            push_mana=2,
            borrow=True,
            turns_done=3,
            borrowed=1,
            target_id=2,
        )
        state.by_id(2).granted_workings = ["working:omen:2", "working:omen:5"]
        state.by_id(3).chosen_true_cast = True
        data = json.loads(json.dumps(state.to_dict()))
        restored = BattleState.from_dict(data)
        self.assertEqual(restored, state)
        self.assertIsInstance(restored.by_id(1).crafting, CraftState)
        self.assertEqual(
            data["combatants"][0]["crafting"],
            {
                "spell_key": "working:scorch:4",
                "push_mana": 2,
                "borrow": True,
                "turns_done": 3,
                "borrowed": 1,
                "target_id": 2,
            },
        )
        self.assertEqual(
            data["combatants"][1]["granted_workings"],
            ["working:omen:2", "working:omen:5"],
        )
        self.assertIs(data["combatants"][2]["chosen_true_cast"], True)
        # Each is written only on the combatant that holds it.
        written = [
            [name for name in TRUE_MAGIC_FIELDS if name in combatant]
            for combatant in data["combatants"]
        ]
        self.assertEqual(
            written, [["crafting"], ["granted_workings"], ["chosen_true_cast"]]
        )

    def test_a_craft_that_names_no_figure_round_trips(self):
        state = BattleState(combatants=[make_combatant(1)])
        state.by_id(1).crafting = CraftState("working:hush:1", 0, False, 0, 0, None)
        data = json.loads(json.dumps(state.to_dict()))
        self.assertIsNone(data["combatants"][0]["crafting"]["target_id"])
        self.assertEqual(BattleState.from_dict(data), state)

    def test_the_choice_lasts_one_turn_and_the_craft_and_grants_persist(self):
        combatant = make_combatant(1)
        craft = CraftState("working:mend:2", 1, False, 1, 0, 1)
        combatant.crafting = craft
        combatant.granted_workings = ["working:omen:3"]
        combatant.chosen_true_cast = True
        combatant.reset_for_turn()
        self.assertFalse(combatant.chosen_true_cast)
        self.assertIs(combatant.crafting, craft)
        self.assertEqual(combatant.granted_workings, ["working:omen:3"])

    def test_unset_none_of_them_is_written(self):
        state = BattleState(combatants=[make_combatant(1), make_combatant(2)])
        for combatant in state.to_dict()["combatants"]:
            for name in TRUE_MAGIC_FIELDS:
                self.assertNotIn(name, combatant)

    def test_set_and_then_unset_they_are_written_and_then_not(self):
        # An emptied grant list, a finished craft and a turn's reset each
        # leave the snapshot as it was before the field was set.
        state = BattleState(combatants=[make_combatant(1)])
        before = state.to_dict()
        holder = state.by_id(1)
        holder.granted_workings = ["working:omen:3"]
        holder.crafting = CraftState("working:mend:2", 1, False, 1, 0, 1)
        holder.chosen_true_cast = True
        while_set = state.to_dict()["combatants"][0]
        for name in TRUE_MAGIC_FIELDS:
            self.assertIn(name, while_set)
        holder.granted_workings = []
        holder.crafting = None
        holder.reset_for_turn()
        self.assertEqual(state.to_dict(), before)

    def test_a_snapshot_missing_the_keys_loads_them_as_their_defaults(self):
        state = BattleState(
            turn=4,
            combatants=[
                make_combatant(1, spells=["fire_missile"], team="Red"),
                make_combatant(2, q=3, r=-1),
            ],
        )
        older_text = json.dumps(state.to_dict())
        for name in TRUE_MAGIC_FIELDS:
            self.assertNotIn(name, older_text)
        loaded = BattleState.from_dict(json.loads(older_text))
        for combatant in loaded.combatants:
            self.assertIsNone(combatant.crafting)
            self.assertEqual(combatant.granted_workings, [])
            self.assertFalse(combatant.chosen_true_cast)
        self.assertEqual(json.dumps(loaded.to_dict()), older_text)
