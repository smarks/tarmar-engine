"""Per-phase engine tests with scripted and seeded stub rollers.

The engine consumes anything with the ``common.rolling.Roller`` interface;
these tests drive it with a scripted stub (exact faces per roll, for branch
coverage) and a seeded stub (realistic full turns). The end-to-end golden
battle through the real ``Roller`` lives in ``test_services``.
"""

import random
from dataclasses import dataclass
from unittest import TestCase

from tarmar_engine import engine, policy
from tarmar_engine.state import BattleState, WeaponState

from .test_state import make_combatant


@dataclass(frozen=True)
class StubRecord:
    purpose: str
    specification: str
    faces: tuple
    modifier: int
    total: int
    target_number: int | None
    outcome: str | None


class ScriptedRoller:
    """Returns scripted faces per roll, in order; then midline defaults."""

    def __init__(self, faces_queue=()):
        self.faces_queue = list(faces_queue)

    def roll(
        self, specification, *, purpose, modifier=0, target_number=None, outcome=None
    ):
        from tarmar_engine.dice import parse_dice_expression

        count, sides, spec_modifier = parse_dice_expression(specification)
        if self.faces_queue:
            faces = tuple(self.faces_queue.pop(0))
        else:
            faces = tuple((sides + 1) // 2 for _ in range(count))
        total_modifier = spec_modifier + modifier
        return StubRecord(
            purpose=purpose,
            specification=specification.strip().lower(),
            faces=faces,
            modifier=total_modifier,
            total=sum(faces) + total_modifier,
            target_number=target_number,
            outcome=outcome,
        )


class SeededStubRoller(ScriptedRoller):
    """Same interface, faces from a private seeded RNG — deterministic turns."""

    def __init__(self, seed):
        super().__init__()
        self.random = random.Random(seed)

    def roll(
        self, specification, *, purpose, modifier=0, target_number=None, outcome=None
    ):
        from tarmar_engine.dice import parse_dice_expression

        count, sides, spec_modifier = parse_dice_expression(specification)
        faces = tuple(self.random.randint(1, sides) for _ in range(count))
        total_modifier = spec_modifier + modifier
        return StubRecord(
            purpose=purpose,
            specification=specification.strip().lower(),
            faces=faces,
            modifier=total_modifier,
            total=sum(faces) + total_modifier,
            target_number=target_number,
            outcome=outcome,
        )


def duel_state(**overrides):
    """Two engaged fighters face to face."""
    first = make_combatant(1, q=0, r=0, facing=0)
    second = make_combatant(2, q=1, r=0, facing=3)
    state = BattleState(arena_radius=6, combatants=[first, second])
    for field, value in overrides.items():
        setattr(state, field, value)
    return state


def run_one_turn(state, roller):
    events = []
    engine.run_turn(state, roller, events.append, policy.choose_option)
    return events


def events_of_type(events, event_type):
    return [event for event in events if event["event_type"] == event_type]


class PhaseStructureTest(TestCase):
    def test_every_turn_emits_all_six_phases_in_order(self):
        events = run_one_turn(duel_state(), SeededStubRoller(1))
        phase_events = events_of_type(events, "phase")
        self.assertEqual(len(phase_events), 6)
        self.assertEqual(
            [event["payload"]["phase_name"] for event in phase_events],
            [name for _number, name in engine.PHASES],
        )
        self.assertEqual(
            [event["phase"] for event in phase_events],
            [number for number, _name in engine.PHASES],
        )

    def test_sequences_are_monotonic_and_unique(self):
        state = duel_state()
        events = run_one_turn(state, SeededStubRoller(2))
        sequences = [event["sequence"] for event in events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(sequences), len(set(sequences)))
        self.assertEqual(state.next_sequence, sequences[-1] + 1)

    def test_turn_number_increments(self):
        state = duel_state()
        run_one_turn(state, SeededStubRoller(3))
        self.assertEqual(state.turn, 1)
        run_one_turn(state, SeededStubRoller(4))
        self.assertEqual(state.turn, 2)


class InitiativeTest(TestCase):
    def test_each_combatant_rolls_own_d6_and_order_follows_totals(self):
        # First rolls 2, second rolls 5 (both DEX 12, +1): the second moves
        # first on the higher total.
        events = []
        state = duel_state()
        runner = engine.TurnRunner(state, ScriptedRoller([[2], [5]]), events.append)
        state.turn += 1
        order = runner.phase_initiative()
        self.assertEqual([combatant.combatant_id for combatant in order], [2, 1])
        initiative_rolls = [
            event for event in events if event["payload"].get("purpose") == "initiative"
        ]
        self.assertEqual(len(initiative_rolls), 2)
        info = events_of_type(events, "info")[0]
        self.assertIn("Movement order", info["message"])

    def test_initiative_roll_carries_the_adjdex_modifier(self):
        # DEX 16 is a +3 modifier: the roll record and event payload must
        # show faces, modifier, and total so the UI renders "d6: [2] +3 = 5".
        state = duel_state()
        state.by_id(1).dexterity = 16
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[2], [4]]), events.append)
        runner.phase_initiative()
        first_roll = events_of_type(events, "roll")[0]
        self.assertEqual(first_roll["payload"]["faces"], [2])
        self.assertEqual(first_roll["payload"]["modifier"], 3)
        self.assertEqual(first_roll["payload"]["total"], 5)
        self.assertIn("[2] +3 = 5", first_roll["message"])

    def test_higher_total_beats_higher_face(self):
        # First rolls 5 (+1 = 6); second, DEX 18 (+4), rolls 3 (= 7) and
        # still moves first: the total orders, not the bare die.
        state = duel_state()
        state.by_id(2).dexterity = 18
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[5], [3]]), events.append)
        order = runner.phase_initiative()
        self.assertEqual([combatant.combatant_id for combatant in order], [2, 1])

    def test_initiative_tie_breaks_by_higher_adjdex(self):
        # DEX 12 and DEX 13 are both +1: equal faces tie on total, and the
        # higher adjDEX (13) wins the tie.
        state = duel_state()
        state.by_id(2).dexterity = 13
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[4], [4]]), events.append)
        order = runner.phase_initiative()
        self.assertEqual(order[0].combatant_id, 2)

    def test_full_tie_keeps_stable_order(self):
        # Same face, same DEX: the tie falls through to combatant id.
        state = duel_state()
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[4], [4]]), events.append)
        order = runner.phase_initiative()
        self.assertEqual([combatant.combatant_id for combatant in order], [1, 2])


class RenewSpellsTest(TestCase):
    def test_affordable_spell_is_renewed_and_mana_paid(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.active_spells = ["shield"]
        caster.mana = 3
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_renew_spells()
        self.assertEqual(caster.mana, 2)
        self.assertEqual(caster.active_spells, ["shield"])
        self.assertIn("renews Shield", events_of_type(events, "action")[0]["message"])

    def test_unaffordable_spell_ends(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.active_spells = ["blur"]  # costs 2
        caster.mana = 1
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_renew_spells()
        self.assertEqual(caster.active_spells, [])
        self.assertIn("it ends", events_of_type(events, "status")[0]["message"])


class MovementTest(TestCase):
    def test_distant_fighters_close_and_stop_when_engaged(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=-5, r=0, facing=0),
                make_combatant(2, q=5, r=0, facing=3),
            ],
        )
        events = run_one_turn(state, SeededStubRoller(5))
        movements = events_of_type(events, "movement")
        self.assertTrue(movements)
        first, second = state.by_id(1), state.by_id(2)
        # They ended adjacent (engaged) — never in the same hex.
        self.assertNotEqual(first.position, second.position)
        distance = engine.hexes.distance(first.position, second.position)
        self.assertEqual(distance, 1)

    def test_running_costs_fatigue(self):
        # A lone runner far from a single enemy: chooses MOVE (run) or
        # CHARGE (jog). Force MOVE by scripting the choice.
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=-9, r=0, facing=0),
                make_combatant(2, q=9, r=0, facing=3),
            ],
        )
        mover = state.by_id(1)
        mover.chosen_letter = "a"
        mover.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.move_towards_target(mover)
        self.assertEqual(mover.fatigue, mover.max_fatigue - engine.RUN_FATIGUE_COST)
        statuses = events_of_type(events, "status")
        self.assertIn("running", statuses[0]["message"])

    def test_kite_step_opens_distance(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=2, r=0, facing=3),
            ],
        )
        archer = state.by_id(1)
        archer.chosen_letter = "f"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.kite_step(archer)
        self.assertEqual(
            engine.hexes.distance(archer.position, state.by_id(2).position), 3
        )
        self.assertTrue(events_of_type(events, "movement"))


class AttackResolutionTest(TestCase):
    def _attack(self, faces_queue, attacker_overrides=None, defender_overrides=None):
        state = duel_state()
        attacker = state.by_id(1)
        defender = state.by_id(2)
        for field, value in (attacker_overrides or {}).items():
            setattr(attacker, field, value)
        for field, value in (defender_overrides or {}).items():
            setattr(defender, field, value)
        attacker.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(faces_queue), events.append)
        runner.resolve_attack(attacker, defender, ranged=False)
        return state, events

    def test_plain_hit_applies_post_armour_damage(self):
        # d20 = 18 (hit), damage 2d6 = [4, 4] = 8 minus 2 stops = 6.
        state, events = self._attack(
            [[18], [4, 4]], defender_overrides={"stops": 2, "armour_tier": "Light"}
        )
        defender = state.by_id(2)
        self.assertEqual(defender.fatigue, defender.max_fatigue - 6)
        damage_event = events_of_type(events, "damage")[0]
        self.assertEqual(damage_event["payload"]["net"], 6)
        self.assertEqual(damage_event["payload"]["raw"], 8)
        # The chain names the attack roll and the damage roll.
        roll_sequences = [event["sequence"] for event in events_of_type(events, "roll")]
        self.assertEqual(damage_event["payload"]["chain"], roll_sequences)

    def test_miss_leaves_the_defender_untouched(self):
        state, events = self._attack([[3]])
        self.assertEqual(state.by_id(2).fatigue, state.by_id(2).max_fatigue)
        self.assertEqual(events_of_type(events, "damage"), [])

    def test_natural_twenty_rolls_damage_twice(self):
        # Nat 20, confirm misses (3): critical, two damage rolls.
        state, events = self._attack([[20], [3], [4, 4], [2, 3]])
        rolls = events_of_type(events, "roll")
        purposes = [event["payload"]["purpose"] for event in rolls]
        self.assertEqual(purposes, ["attack", "confirm", "damage", "damage"])
        defender = state.by_id(2)
        self.assertEqual(defender.fatigue, defender.max_fatigue - 13)
        self.assertEqual(defender.body, defender.max_body)  # not severe

    def test_confirmed_severe_critical_reaches_body_and_bleeds(self):
        # Nat 20, confirm 19 (hits): three damage rolls, body damage, bleeding.
        state, events = self._attack([[20], [19], [4, 4], [2, 3], [6, 1]])
        defender = state.by_id(2)
        total = 8 + 5 + 7
        self.assertEqual(defender.fatigue, defender.max_fatigue - total)
        self.assertEqual(defender.body, defender.max_body - total)
        bleeding = [
            event
            for event in events_of_type(events, "status")
            if event["payload"].get("bleeding")
        ]
        self.assertEqual(len(bleeding), 1)
        self.assertIn("not ticked", bleeding[0]["message"])

    def test_fumble_drop_weapon_disarms(self):
        # Nat 1, fumble d6 = 4: drop weapon.
        state, events = self._attack([[1], [4]])
        attacker = state.by_id(1)
        self.assertEqual(attacker.weapon.item_id, "")
        self.assertEqual(attacker.weapon_skill_level, 0)
        status = events_of_type(events, "status")[0]
        self.assertIn("drops their Broadsword", status["message"])

    def test_fumble_off_balance_penalizes_next_attack_once(self):
        state, events = self._attack([[1], [2]])
        attacker = state.by_id(1)
        self.assertTrue(attacker.off_balance)
        # The next swing takes -2, and the flag clears.
        events2 = []
        runner = engine.TurnRunner(state, ScriptedRoller([[10]]), events2.append)
        runner.resolve_attack(attacker, state.by_id(2), ranged=False)
        self.assertFalse(attacker.off_balance)
        first_roll = events_of_type(events2, "roll")[0]
        clean_events = []
        runner2 = engine.TurnRunner(state, ScriptedRoller([[10]]), clean_events.append)
        runner2.resolve_attack(attacker, state.by_id(2), ranged=False)
        clean_roll = events_of_type(clean_events, "roll")[0]
        self.assertEqual(
            first_roll["payload"]["modifier"],
            clean_roll["payload"]["modifier"] - engine.combat_math.OFF_BALANCE_PENALTY,
        )

    def test_second_fumble_on_stressed_weapon_breaks_it(self):
        state, _events = self._attack([[1], [6]])
        attacker = state.by_id(1)
        self.assertTrue(attacker.weapon_stressed)
        self.assertEqual(attacker.weapon.item_id, "broadsword")
        events2 = []
        runner = engine.TurnRunner(state, ScriptedRoller([[1], [6]]), events2.append)
        runner.resolve_attack(attacker, state.by_id(2), ranged=False)
        self.assertEqual(attacker.weapon.item_id, "")
        self.assertIn("breaks", events_of_type(events2, "status")[0]["message"])

    def test_armour_can_stop_the_whole_blow(self):
        state, events = self._attack(
            [[18], [1, 1]], defender_overrides={"stops": 5, "armour_tier": "Medium"}
        )
        defender = state.by_id(2)
        self.assertEqual(defender.fatigue, defender.max_fatigue)
        damage_event = events_of_type(events, "damage")[0]
        self.assertEqual(damage_event["payload"]["net"], 0)
        self.assertIn("armour stops the blow", damage_event["message"])


class UnconsciousnessAndDeathTest(TestCase):
    def test_fatigue_at_zero_collapses(self):
        state, events = duel_state(), []
        defender = state.by_id(2)
        defender.fatigue = 5
        state.by_id(1).chosen_target = 2
        runner = engine.TurnRunner(state, ScriptedRoller([[18], [4, 4]]), events.append)
        runner.resolve_attack(state.by_id(1), defender, ranged=False)
        self.assertFalse(defender.conscious)
        self.assertTrue(defender.prone)
        self.assertFalse(defender.active)
        collapse = [
            event
            for event in events_of_type(events, "status")
            if event["payload"].get("unconscious")
        ]
        self.assertEqual(len(collapse), 1)

    def test_failed_survival_save_is_death_with_full_fatal_chain(self):
        state = duel_state()
        defender = state.by_id(2)
        defender.fatigue = 2  # max 40: survival save at -20
        events = []
        # Attack 18 hits; damage [6,6]+... 2d6 = 12+12 scripted twice? One
        # blow of 2d6 = [6, 6] = 12 brings fatigue to -10; not deep enough,
        # so script a huge second blow via low CON instead: set fatigue so
        # one blow crosses the save line.
        defender.fatigue = -8  # next 12 damage -> -20 == save threshold
        defender.conscious = False
        defender.prone = True
        state.by_id(1).chosen_target = 2
        runner = engine.TurnRunner(
            state, ScriptedRoller([[18], [6, 6], [6, 6, 6]]), events.append
        )
        runner.resolve_attack(state.by_id(1), defender, ranged=False)
        runner.survival_saves()  # 3d6 = 18 > CON 12: dies
        self.assertFalse(defender.alive)
        death = events_of_type(events, "death")[0]
        chain = death["payload"]["fatal_chain"]
        self.assertEqual(len(chain), 3)  # to-hit, damage, survival save
        rolls = {event["sequence"]: event for event in events_of_type(events, "roll")}
        self.assertEqual(
            [rolls[sequence]["payload"]["purpose"] for sequence in chain],
            ["attack", "damage", "survival"],
        )

    def test_made_survival_save_clings_to_life(self):
        state = duel_state()
        defender = state.by_id(2)
        defender.fatigue = -25
        defender.conscious = False
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[2, 2, 2]]), events.append)
        runner.survival_saves()
        self.assertTrue(defender.alive)
        self.assertIn("clings to life", events_of_type(events, "info")[0]["message"])

    def test_save_past_negative_max_is_penalized(self):
        state = duel_state()
        defender = state.by_id(2)
        defender.fatigue = -45  # max 40: 5 past -max
        defender.conscious = False
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[2, 2, 2]]), events.append)
        runner.survival_saves()
        save = events_of_type(events, "roll")[0]
        self.assertEqual(save["payload"]["modifier"], 5)
        self.assertEqual(save["payload"]["total"], 11)

    def test_dead_actors_act_no_more(self):
        state = duel_state()
        state.by_id(2).alive = False
        events = run_one_turn(state, SeededStubRoller(6))
        dead_name = state.by_id(2).name
        for event in events:
            self.assertNotEqual(event["actor"], dead_name)


class ForcedRetreatTest(TestCase):
    def test_push_back_and_advance(self):
        state = duel_state()
        pusher, victim = state.by_id(1), state.by_id(2)
        pusher.dealt_damage_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_forced_retreat()
        self.assertEqual(victim.position, (2, 0))
        self.assertEqual(pusher.position, (1, 0))
        self.assertIn("forces", events_of_type(events, "movement")[0]["message"])

    def test_no_retreat_hex_forces_dex_roll_and_fall(self):
        state = duel_state()
        pusher, victim = state.by_id(1), state.by_id(2)
        victim.position = (6, 0)  # on the arena rim
        pusher.position = (5, 0)
        pusher.dealt_damage_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[6, 6, 6]]), events.append)
        runner.phase_forced_retreat()
        self.assertTrue(victim.prone)
        self.assertIn("falls", events_of_type(events, "status")[0]["message"])

    def test_no_retreat_hex_kept_feet_on_made_roll(self):
        state = duel_state()
        pusher, victim = state.by_id(1), state.by_id(2)
        victim.position = (6, 0)
        pusher.position = (5, 0)
        pusher.dealt_damage_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[2, 2, 2]]), events.append)
        runner.phase_forced_retreat()
        self.assertFalse(victim.prone)
        self.assertIn("keeps their feet", events_of_type(events, "info")[0]["message"])

    def test_taking_damage_forfeits_the_push(self):
        state = duel_state()
        pusher = state.by_id(1)
        pusher.dealt_damage_this_turn = True
        pusher.took_damage_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_forced_retreat()
        self.assertEqual(events_of_type(events, "movement"), [])


class GrappleAttemptTest(TestCase):
    """Option o (attempt_grapple): Flexible/Snare TN, HTH +4, no weapon skill.

    duel_state()'s default combatants (DEX 12, no armour, no shield) give a
    Flexible/Snare-vs-None TN of 13 + 1 dodge = 14, and a bonus of DEX +1
    plus the HTH +4 = 5 — die 9 is exactly a hit, die 8 exactly a miss.
    """

    def _attempt(self, faces_queue):
        state = duel_state()
        attacker = state.by_id(1)
        attacker.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(faces_queue), events.append)
        runner.attempt_grapple(attacker)
        return state, events

    def test_hit_establishes_the_grapple_both_ways(self):
        state, events = self._attempt([[9]])
        attacker, defender = state.by_id(1), state.by_id(2)
        self.assertEqual(attacker.grappling, 2)
        self.assertEqual(defender.grappled_by, 1)
        self.assertIn(
            "grapples and holds", events_of_type(events, "action")[0]["message"]
        )
        roll = events_of_type(events, "roll")[0]
        self.assertEqual(roll["payload"]["purpose"], "grapple attempt")
        self.assertEqual(roll["payload"]["target_number"], 14)

    def test_miss_leaves_both_sides_free(self):
        state, events = self._attempt([[8]])
        attacker, defender = state.by_id(1), state.by_id(2)
        self.assertIsNone(attacker.grappling)
        self.assertIsNone(defender.grappled_by)
        self.assertIn(
            "fails to grapple", events_of_type(events, "action")[0]["message"]
        )

    def test_natural_twenty_grapples_and_stuns_no_confirm_roll(self):
        state, events = self._attempt([[20]])
        attacker, defender = state.by_id(1), state.by_id(2)
        self.assertEqual(attacker.grappling, 2)
        self.assertTrue(defender.off_balance)
        purposes = [
            event["payload"]["purpose"] for event in events_of_type(events, "roll")
        ]
        self.assertEqual(purposes, ["grapple attempt"])  # no confirm, no damage
        self.assertIn("briefly stunned", events_of_type(events, "action")[0]["message"])

    def test_natural_one_off_balance_variant(self):
        state, events = self._attempt([[1], [2]])  # fumble d6=2 -> off_balance
        attacker = state.by_id(1)
        self.assertTrue(attacker.off_balance)
        self.assertFalse(attacker.prone)
        self.assertIn(
            "overextends and is off-balance",
            events_of_type(events, "status")[0]["message"],
        )

    def test_natural_one_drop_weapon_becomes_prone(self):
        state, events = self._attempt([[1], [4]])  # fumble d6=4 -> drop_weapon
        attacker = state.by_id(1)
        self.assertTrue(attacker.prone)
        self.assertFalse(attacker.off_balance)
        self.assertIn(
            "overextends and ends up prone",
            events_of_type(events, "status")[0]["message"],
        )

    def test_natural_one_weapon_stress_becomes_off_balance(self):
        state, events = self._attempt([[1], [6]])  # fumble d6=6 -> weapon_stress
        attacker = state.by_id(1)
        self.assertTrue(attacker.off_balance)
        self.assertFalse(attacker.weapon_stressed)  # no weapon to stress
        self.assertIn(
            "overextends and is off-balance",
            events_of_type(events, "status")[0]["message"],
        )

    def test_cannot_grapple_an_already_held_target(self):
        state = duel_state()
        attacker, defender = state.by_id(1), state.by_id(2)
        attacker.chosen_target = 2
        defender.grappled_by = 99
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[9]]), events.append)
        runner.attempt_grapple(attacker)
        self.assertEqual(events, [])
        self.assertIsNone(attacker.grappling)

    def test_cannot_attempt_while_already_grappling(self):
        state = duel_state()
        attacker, defender = state.by_id(1), state.by_id(2)
        attacker.chosen_target = 2
        attacker.grappling = 77
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[9]]), events.append)
        runner.attempt_grapple(attacker)
        self.assertEqual(events, [])
        self.assertIsNone(defender.grappled_by)


class GrappleTurnChoicesTest(TestCase):
    def test_struggle_free_success_ends_the_grapple_and_moves(self):
        state = duel_state()
        grapplee, grappler = state.by_id(1), state.by_id(2)
        grapplee.grappled_by = 2
        grappler.grappling = 1
        events = []
        # 4d6 = [1,1,1,1] = 4 <= DEX 12: success.
        runner = engine.TurnRunner(state, ScriptedRoller([[1, 1, 1, 1]]), events.append)
        runner.grapple_struggle_free(grapplee)
        self.assertIsNone(grapplee.grappled_by)
        self.assertIsNone(grappler.grappling)
        self.assertNotEqual(grapplee.position, (0, 0))
        status = events_of_type(events, "status")[0]
        self.assertIn("struggles free", status["message"])

    def test_struggle_free_failure_leaves_the_hold(self):
        state = duel_state()
        grapplee, grappler = state.by_id(1), state.by_id(2)
        grapplee.grappled_by = 2
        grappler.grappling = 1
        events = []
        # 4d6 = [6,6,6,6] = 24 > DEX 12: failure.
        runner = engine.TurnRunner(state, ScriptedRoller([[6, 6, 6, 6]]), events.append)
        runner.grapple_struggle_free(grapplee)
        self.assertEqual(grapplee.grappled_by, 2)
        self.assertEqual(grappler.grappling, 1)
        self.assertEqual(grapplee.position, (0, 0))
        self.assertIn(
            "fails to struggle free", events_of_type(events, "info")[0]["message"]
        )

    def test_strike_back_uses_bare_hands_with_the_hth_bonus(self):
        state = duel_state()
        grapplee, grappler = state.by_id(1), state.by_id(2)
        grapplee.grappled_by = 2
        grappler.grappling = 1
        events = []
        # Bonus 5 vs TN 14: die 18 hits; bare-handed damage 1d6-2 = [4] = 2.
        runner = engine.TurnRunner(state, ScriptedRoller([[18], [4]]), events.append)
        runner.grapple_strike_back(grapplee)
        self.assertEqual(grappler.fatigue, grappler.max_fatigue - 2)
        action = events_of_type(events, "action")[0]
        self.assertIn("strikes back at", action["message"])
        self.assertIn("bare hands", action["message"])
        # The grapplee's own real weapon is untouched by a bare-handed swing.
        self.assertEqual(grapplee.weapon.item_id, "broadsword")

    def test_squeeze_ignores_the_targets_shield_dodge_and_defend(self):
        state = duel_state()
        grappler, held = state.by_id(1), state.by_id(2)
        grappler.grappling = 2
        held.grappled_by = 1
        held.shield_bonus = 3
        held.defending = True
        held.dexterity = 16  # would normally add a +3 dodge modifier
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[13], [3]]), events.append)
        runner.grapple_squeeze(grappler)
        attack_roll = events_of_type(events, "roll")[0]
        # Striking/None base 13, no shield/dodge/defend added at all.
        self.assertEqual(attack_roll["payload"]["target_number"], 13)
        self.assertEqual(held.fatigue, held.max_fatigue - 1)  # 1d6-2 = [3] = 1
        self.assertIn("squeezes", events_of_type(events, "action")[0]["message"])

    def test_release_ends_the_grapple(self):
        state = duel_state()
        grappler, held = state.by_id(1), state.by_id(2)
        grappler.grappling = 2
        held.grappled_by = 1
        grappler.chosen_letter = "release"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.execute_action(grappler)
        self.assertIsNone(grappler.grappling)
        self.assertIsNone(held.grappled_by)
        self.assertIn("releases", events_of_type(events, "status")[0]["message"])

    def test_maintain_changes_nothing(self):
        state = duel_state()
        grappler, held = state.by_id(1), state.by_id(2)
        grappler.grappling = 2
        held.grappled_by = 1
        grappler.chosen_letter = "maintain"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.execute_action(grappler)
        self.assertEqual(grappler.grappling, 2)
        self.assertEqual(held.grappled_by, 1)
        self.assertIn("maintains", events_of_type(events, "action")[0]["message"])

    def test_hold_still_changes_nothing(self):
        state = duel_state()
        held = state.by_id(1)
        held.grappled_by = 2
        held.chosen_letter = "hold_still"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.execute_action(held)
        self.assertEqual(held.grappled_by, 2)
        self.assertIn("holds still", events_of_type(events, "action")[0]["message"])


class GrappleMovementLockTest(TestCase):
    def test_grappled_pair_does_not_move_in_phases_3_or_4(self):
        state = duel_state()
        grapplee, grappler = state.by_id(1), state.by_id(2)
        grapplee.grappled_by = 2
        grappler.grappling = 1
        grapplee_start, grappler_start = grapplee.position, grappler.position
        events = []
        runner = engine.TurnRunner(state, SeededStubRoller(11), events.append)
        order = [grappler, grapplee]  # grappler acts first, still doesn't move
        runner.phase_initial_movement(order, policy.choose_option)
        runner.phase_final_movement(order)
        self.assertEqual(grapplee.position, grapplee_start)
        self.assertEqual(grappler.position, grappler_start)
        self.assertFalse(grapplee.yielded)
        self.assertFalse(grappler.yielded)


class GrappleForcedRetreatExemptionTest(TestCase):
    def test_no_push_when_the_attacker_is_the_grappler(self):
        state = duel_state()
        grappler, held = state.by_id(1), state.by_id(2)
        grappler.grappling = 2
        held.grappled_by = 1
        grappler.dealt_damage_this_turn = True
        grappler.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_forced_retreat()
        self.assertEqual(events_of_type(events, "movement"), [])
        self.assertEqual(held.position, (1, 0))

    def test_no_push_when_the_victim_is_held_by_a_third_party(self):
        # A third combatant lands a hit on someone who happens to be
        # grappled by a different figure — still exempt ("against — or to
        # — a grappled figure").
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3, grappled_by=3),
                make_combatant(3, q=2, r=0, facing=3, grappling=2),
            ],
        )
        attacker = state.by_id(1)
        attacker.dealt_damage_this_turn = True
        attacker.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_forced_retreat()
        self.assertEqual(events_of_type(events, "movement"), [])
        self.assertEqual(state.by_id(2).position, (1, 0))


class GrappleRenewSpellsTest(TestCase):
    def test_grappled_casters_active_spells_lapse(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.active_spells = ["shield"]
        caster.mana = 5
        caster.grappled_by = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_renew_spells()
        self.assertEqual(caster.active_spells, [])
        self.assertEqual(caster.mana, 5)  # never paid for
        status = events_of_type(events, "status")[0]
        self.assertIn("grappled and cannot sustain", status["message"])

    def test_ungrappled_caster_still_renews_normally(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.active_spells = ["shield"]
        caster.mana = 5
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_renew_spells()
        self.assertEqual(caster.active_spells, ["shield"])
        self.assertEqual(caster.mana, 4)


class CastingTest(TestCase):
    def _wizard_state(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(
                    1,
                    q=-3,
                    r=0,
                    facing=0,
                    intelligence=14,
                    wisdom=12,
                    spells=["fire_missile", "shield", "heal", "wound", "fatigue"],
                    mana=10,
                    max_mana=10,
                ),
                make_combatant(2, q=3, r=0, facing=3),
            ],
        )
        return state

    def test_successful_targeted_spell_rolls_cast_aim_and_damage(self):
        state = self._wizard_state()
        caster = state.by_id(1)
        caster.chosen_spell = "fire_missile"
        caster.chosen_target = 2
        events = []
        runner = engine.TurnRunner(
            state, ScriptedRoller([[3, 3, 3], [2, 2, 2], [5]]), events.append
        )
        runner.cast_spell(caster)
        purposes = [
            event["payload"]["purpose"] for event in events_of_type(events, "roll")
        ]
        self.assertEqual(purposes, ["casting", "spell aim", "spell damage"])
        self.assertEqual(caster.mana, 9)  # level 1 paid
        defender = state.by_id(2)
        self.assertEqual(defender.fatigue, defender.max_fatigue - 5)

    def test_failed_casting_roll_still_costs_mana(self):
        state = self._wizard_state()
        caster = state.by_id(1)
        caster.chosen_spell = "fire_missile"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[6, 6, 6]]), events.append)
        runner.cast_spell(caster)
        self.assertEqual(caster.mana, 9)
        self.assertIn("failure", events_of_type(events, "action")[0]["message"])
        self.assertEqual(events_of_type(events, "damage"), [])

    def test_dodging_target_penalizes_the_aim_roll(self):
        state = self._wizard_state()
        caster = state.by_id(1)
        caster.chosen_spell = "fire_missile"
        caster.chosen_target = 2
        state.by_id(2).dodging = True
        events = []
        # Aim 3d6 = [3, 3, 3] = 9 vs DEX 12 - 4 = 8: misses.
        runner = engine.TurnRunner(
            state, ScriptedRoller([[3, 3, 3], [3, 3, 3]]), events.append
        )
        runner.cast_spell(caster)
        self.assertIn("misses", events_of_type(events, "info")[0]["message"])
        aim = events_of_type(events, "roll")[1]
        self.assertEqual(aim["payload"]["target_number"], 8)

    def test_continuing_spell_becomes_active(self):
        state = self._wizard_state()
        caster = state.by_id(1)
        caster.chosen_spell = "shield"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[2, 2, 2]]), events.append)
        runner.cast_spell(caster)
        self.assertEqual(caster.active_spells, ["shield"])
        self.assertIn("shimmers", events_of_type(events, "status")[0]["message"])

    def test_heal_restores_fatigue_capped_at_max(self):
        state = self._wizard_state()
        caster = state.by_id(1)
        caster.fatigue = caster.max_fatigue - 2
        caster.chosen_spell = "heal"
        events = []
        runner = engine.TurnRunner(
            state, ScriptedRoller([[2, 2, 2], [6]]), events.append
        )
        runner.cast_spell(caster)
        self.assertEqual(caster.fatigue, caster.max_fatigue)
        self.assertIn("heals 2 fatigue", events_of_type(events, "status")[0]["message"])

    def test_wound_damages_body_ignoring_armour(self):
        state = self._wizard_state()
        caster = state.by_id(1)
        caster.chosen_spell = "wound"
        caster.chosen_target = 2
        defender = state.by_id(2)
        defender.stops = 5
        events = []
        runner = engine.TurnRunner(
            state, ScriptedRoller([[2, 2, 2], [4]]), events.append
        )
        runner.cast_spell(caster)
        self.assertEqual(defender.body, defender.max_body - 4)
        self.assertEqual(defender.fatigue, defender.max_fatigue)

    def test_insufficient_mana_refuses_the_cast(self):
        state = self._wizard_state()
        caster = state.by_id(1)
        caster.mana = 0
        caster.chosen_spell = "fire_missile"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.cast_spell(caster)
        self.assertIn("lacks the mana", events_of_type(events, "info")[0]["message"])


class ActionPhaseRemapTest(TestCase):
    def test_engaged_archer_defends_instead_of_shooting(self):
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(
                    1,
                    q=0,
                    r=0,
                    facing=0,
                    weapon=WeaponState(
                        item_id="longbow",
                        name="Longbow",
                        weapon_class="Missile — Bows",
                        damage="1d6+2",
                        is_missile=True,
                    ),
                ),
                make_combatant(2, q=1, r=0, facing=3),
            ],
        )
        archer = state.by_id(1)
        archer.chosen_letter = "f"
        archer.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.missile_attack(archer)
        self.assertTrue(archer.defending)
        self.assertIn(
            "engaged before loosing", events_of_type(events, "status")[0]["message"]
        )

    def test_charge_that_fell_short_swings_at_nothing(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=-4, r=0, facing=0),
                make_combatant(2, q=4, r=0, facing=3),
            ],
        )
        charger = state.by_id(1)
        charger.chosen_letter = "b"
        charger.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.melee_attack(charger)
        self.assertIn("fell short", events_of_type(events, "info")[0]["message"])

    def test_disengage_steps_away(self):
        state = duel_state()
        mover = state.by_id(1)
        mover.chosen_letter = "n"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.disengage_step(mover)
        self.assertEqual(
            engine.hexes.distance(mover.position, state.by_id(2).position), 2
        )
        self.assertIn("disengages", events_of_type(events, "movement")[0]["message"])

    def test_execute_action_dispatches_dodge_defend_and_cast(self):
        state = duel_state()
        actor = state.by_id(1)
        actor.spells = ["shield"]
        actor.mana = 5
        for letter, expectation in (
            ("c", lambda: actor.dodging),
            ("k", lambda: actor.defending),
        ):
            actor.chosen_letter = letter
            events = []
            runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
            runner.execute_action(actor)
            self.assertTrue(expectation(), letter)
            self.assertTrue(events_of_type(events, "status"), letter)
        actor.chosen_letter = "h"
        actor.chosen_spell = "shield"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller([[2, 2, 2]]), events.append)
        runner.execute_action(actor)
        self.assertEqual(actor.active_spells, ["shield"])

    def test_stand_up_consumes_the_turn(self):
        state = duel_state()
        faller = state.by_id(1)
        faller.prone = True
        faller.chosen_letter = "p"
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.execute_action(faller)
        self.assertFalse(faller.prone)
        self.assertIn("stands up", events_of_type(events, "action")[0]["message"])


class InvariantsTest(TestCase):
    """Seeded multi-turn runs uphold the battle invariants."""

    def _run_battle(self, seed, turns=12):
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=-4, r=0, facing=0),
                make_combatant(2, q=4, r=0, facing=3),
                make_combatant(3, q=0, r=-4, facing=5, dexterity=14),
            ],
        )
        all_events = []
        roller = SeededStubRoller(seed)
        for _turn in range(turns):
            if len(state.active_combatants()) <= 1:
                break
            engine.run_turn(state, roller, all_events.append, policy.choose_option)
        return state, all_events

    def test_roll_faces_sum_to_total_minus_modifier(self):
        _state, events = self._run_battle(seed=11)
        rolls = events_of_type(events, "roll")
        self.assertTrue(rolls)
        for event in rolls:
            payload = event["payload"]
            self.assertEqual(
                sum(payload["faces"]),
                payload["total"] - payload["modifier"],
                payload,
            )

    def test_dead_and_unconscious_actors_stop_acting(self):
        state, events = self._run_battle(seed=12, turns=30)
        downed_at = {}
        for index, event in enumerate(events):
            if event["event_type"] == "death" or event["payload"].get("unconscious"):
                downed_at.setdefault(event["actor"], index)
        self.assertTrue(downed_at, "no one went down in 30 turns")
        for name, downed_index in downed_at.items():
            for event in events[downed_index + 1 :]:
                if event["actor"] == name and event["event_type"] in (
                    "decision",
                    "action",
                ):
                    self.fail(f"{name} acted after going down: {event}")

    def test_every_turn_has_all_six_phase_events(self):
        state, events = self._run_battle(seed=13)
        for turn in range(1, state.turn + 1):
            phases = [
                event["phase"]
                for event in events
                if event["turn"] == turn and event["event_type"] == "phase"
            ]
            self.assertEqual(phases, [1, 2, 3, 4, 5, 6], f"turn {turn}")

    def test_seeded_runs_are_identical(self):
        _state_a, events_a = self._run_battle(seed=14)
        _state_b, events_b = self._run_battle(seed=14)
        self.assertEqual(events_a, events_b)

    def test_no_two_living_combatants_share_a_hex(self):
        state, _events = self._run_battle(seed=15, turns=30)
        positions = [
            combatant.position for combatant in state.combatants if combatant.alive
        ]
        self.assertEqual(len(positions), len(set(positions)))


def make_beast_combatant(combatant_id, size_hexes=1, **overrides):
    """A beast combatant as battle.adaptation would snapshot one."""
    fields = {
        "name": f"Beast {combatant_id}",
        "archetype": "beast",
        "is_beast": True,
        "size_hexes": size_hexes,
        "weapon": WeaponState(name="Bite", weapon_class="Piercing", damage="1d6+1"),
    }
    fields.update(overrides)
    return make_combatant(combatant_id, **fields)


class MultiHexFigureTest(TestCase):
    """movement.md multi-hex rules as the engine runs them."""

    def test_three_hex_beast_walks_through_a_single_enemys_front_hex(self):
        # One one-hex bystander cannot engage a 3-6 hex figure (threshold
        # 2+), so the beast jogs straight past its front hex to the target.
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_beast_combatant(1, size_hexes=3, q=-4, r=0, facing=0),
                make_combatant(2, q=4, r=0, facing=3),
                make_combatant(3, q=0, r=-2, facing=5),  # front covers (0,-1)
            ],
        )
        beast = state.by_id(1)
        beast.chosen_letter = "b"
        beast.chosen_target = 2
        runner = engine.TurnRunner(state, ScriptedRoller(), [].append)
        runner.move_towards_target(beast)
        self.assertEqual(beast.position, (3, 0))
        self.assertEqual(engine.combat_math.figure_distance(beast, state.by_id(2)), 1)

    def test_two_one_hex_enemies_stop_a_three_hex_beast(self):
        # movement.md: figures stop immediately when engaged — for a 3-6 hex
        # figure that takes 2+ one-hex figures' front hexes on its body.
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_beast_combatant(1, size_hexes=3, q=-4, r=0, facing=0),
                make_combatant(2, q=4, r=0, facing=3),
                make_combatant(3, q=0, r=-2, facing=5),  # front covers (0,-1)
                make_combatant(4, q=0, r=1, facing=2),  # front covers (0,0)
            ],
        )
        beast = state.by_id(1)
        beast.chosen_letter = "b"
        beast.chosen_target = 2
        runner = engine.TurnRunner(state, ScriptedRoller(), [].append)
        runner.move_towards_target(beast)
        self.assertEqual(beast.position, (0, 0))

    def test_push_back_of_a_three_hex_victim_moves_its_whole_body(self):
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=1, r=0, facing=3),
                make_beast_combatant(2, size_hexes=3, q=0, r=0, facing=0),
            ],
        )
        pusher = state.by_id(1)
        pusher.dealt_damage_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_forced_retreat()
        victim = state.by_id(2)
        self.assertEqual(victim.position, (-1, 0))
        self.assertEqual(pusher.position, (0, 0))
        self.assertIn("and advances", events_of_type(events, "movement")[0]["message"])

    def test_pusher_stays_when_the_vacated_hex_is_still_covered(self):
        # A megahex victim shifted one hex still covers its old anchor.
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=2, r=0, facing=3),
                make_beast_combatant(2, size_hexes=7, q=0, r=0, facing=0),
            ],
        )
        pusher = state.by_id(1)
        pusher.dealt_damage_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.phase_forced_retreat()
        victim = state.by_id(2)
        self.assertEqual(victim.position, (-1, 0))
        self.assertEqual(pusher.position, (2, 0))
        message = events_of_type(events, "movement")[0]["message"]
        self.assertNotIn("and advances", message)

    def test_rotation_is_refused_when_the_body_cannot_swing(self):
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_beast_combatant(1, size_hexes=3, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
            ],
        )
        beast = state.by_id(1)
        runner = engine.TurnRunner(state, ScriptedRoller(), [].append)
        # Facing 4 would put the beast's body on (1,0) — the enemy's hex.
        runner.face_towards(beast, (-2, 2))
        self.assertEqual(beast.facing, 0)

    def test_beast_fumbles_degrade_to_off_balance(self):
        # Natural weapons can neither drop nor break (§7 adaptation).
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_beast_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
            ],
        )
        beast = state.by_id(1)
        events = []
        runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
        runner.apply_fumble(beast, {"key": "drop_weapon", "effect": "drops weapon"})
        self.assertTrue(beast.off_balance)
        self.assertEqual(beast.weapon.name, "Bite")
        self.assertIn("stumbles", events_of_type(events, "status")[0]["message"])

    def test_seeded_mixed_battle_keeps_footprints_disjoint(self):
        state = BattleState(
            arena_radius=7,
            combatants=[
                make_combatant(1, q=-5, r=0, facing=0),
                make_combatant(2, q=5, r=0, facing=3),
                make_beast_combatant(3, size_hexes=3, q=0, r=-5, facing=5),
            ],
        )
        roller = SeededStubRoller(21)
        for _turn in range(12):
            if len(state.active_combatants()) <= 1:
                break
            engine.run_turn(state, roller, (lambda event: None), policy.choose_option)
            covered = []
            for combatant in state.combatants:
                if combatant.alive:
                    covered.extend(combatant.footprint)
            self.assertEqual(len(covered), len(set(covered)))
