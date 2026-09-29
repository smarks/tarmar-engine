"""The 2026-09-29 battle-rules pass: one class per tarmar-studio issue.

Each test quotes the rules page it holds the engine to. Pages live in
tarmar-studio's ``reference/content/``; the ones vendored under
``tarmar_engine/spec/`` are asserted verbatim where a table allows it.
"""

from pathlib import Path
from unittest import TestCase

import tarmar_engine
from tarmar_engine import actions, combat_math, engine, policy, weapons
from tarmar_engine.policy import Candidate, Decision
from tarmar_engine.state import BattleState, GroundWeapon, WeaponState

from .test_engine import ScriptedRoller, duel_state, events_of_type
from .test_state import make_combatant

SPEC = Path(tarmar_engine.__file__).resolve().parent / "spec"

SMALL_BOW = WeaponState(
    item_id="small_bow",
    name="Small Bow",
    weapon_class="Missile — Bows",
    damage="1d6-1",
    str_req=9,
    is_missile=True,
)
DAGGER = WeaponState(
    item_id="dagger",
    name="Dagger",
    weapon_class="Piercing",
    damage="1d6-1",
    is_thrown=True,
    hth_usable=True,
)
THROWN_ROCK = WeaponState(
    item_id="thrown_rock",
    name="Thrown Rock",
    weapon_class="Missile — Bows",
    damage="1d6-4",
    is_missile=True,
    is_thrown=True,
)
BROADSWORD = WeaponState(
    item_id="broadsword",
    name="Broadsword",
    weapon_class="Striking",
    damage="2d6",
    str_req=12,
)


def letters(state, combatant_id):
    decision = policy.choose_option(state, state.by_id(combatant_id))
    return [candidate.letter for candidate in decision.candidates]


def runner_for(state, faces=(), events=None, **kwargs):
    sink = events.append if events is not None else (lambda _event: None)
    return engine.TurnRunner(state, ScriptedRoller(list(faces)), sink, **kwargs)


def fixed_choice(letter, target_id=None, spell_key=""):
    """A choose_option that always picks one option (a player's choice)."""

    def choose(_state, _actor):
        chosen = Candidate(
            letter, actions.option_name(letter), 1.0, "test", target_id, spell_key
        )
        return Decision(chosen=chosen, candidates=[chosen])

    return choose


class OneLastShotTest(TestCase):
    """#776 — action-options.md: "l | ONE LAST SHOT | Still | Fire missile
    (if ready before engaged)"."""

    def engaged_archer(self):
        state = duel_state()
        archer = state.by_id(1)
        archer.weapon = SMALL_BOW
        return state, archer

    def test_an_archer_charged_before_loosing_takes_one_last_shot(self):
        state, archer = self.engaged_archer()
        archer.chosen_letter = "f"
        archer.chosen_target = 2
        events = []
        runner_for(state, [[15], [4]], events).missile_attack(archer)
        self.assertFalse(archer.defending)
        self.assertTrue(archer.last_shot_spent)
        self.assertIn("One Last Shot", events_of_type(events, "info")[0]["message"])
        shot = events_of_type(events, "action")[0]
        self.assertTrue(shot["payload"]["ranged"])
        self.assertEqual(shot["payload"]["outcome"], "hit")

    def test_the_menu_offers_l_until_the_shot_is_spent(self):
        state, archer = self.engaged_archer()
        self.assertIn("l", letters(state, 1))
        self.assertNotIn("j", letters(state, 1))  # a bow is no melee weapon
        archer.last_shot_spent = True
        self.assertNotIn("l", letters(state, 1))

    def test_a_turn_begun_disengaged_restores_the_shot(self):
        state, archer = self.engaged_archer()
        archer.last_shot_spent = True
        runner_for(state)._start_of_turn_missile_state()
        self.assertTrue(archer.last_shot_spent)  # still engaged
        state.by_id(2).position = (5, 0)
        runner_for(state)._start_of_turn_missile_state()
        self.assertFalse(archer.last_shot_spent)


class DisengagePenaltyTest(TestCase):
    """#777 — special-combat-situations.md: "Faster enemies (higher adjDEX)
    can still strike" / "Slower enemies attack at penalty = difference in
    adjDEX"."""

    def test_a_slower_enemy_strikes_the_disengager_at_the_adjdex_gap(self):
        quick = make_combatant(1, name="Quick", dexterity=14, q=0, r=0, facing=0)
        slow = make_combatant(2, name="Slow", dexterity=11, q=1, r=0, facing=3)
        state = BattleState(arena_radius=6, combatants=[quick, slow])
        quick.chosen_letter = "n"
        slow.chosen_letter = "j"
        slow.chosen_target = 1
        events = []
        # d20 18 - 3 = 15 meets TN 15 (Striking 13 + DEX 14 dodge 2).
        runner_for(state, [[18], [3, 3]], events).phase_actions()
        self.assertEqual(quick.position, (-1, 0))
        self.assertIn("as they disengage", events_of_type(events, "info")[0]["message"])
        attack = events_of_type(events, "roll")[0]
        # DEX 11 is +0 to hit; the gap to DEX 14 is 3.
        self.assertEqual(attack["payload"]["modifier"], -3)
        self.assertEqual(quick.fatigue, quick.max_fatigue - 6)

    def test_an_enemy_the_disengager_never_stood_beside_still_falls_short(self):
        state = duel_state()
        runner = runner_for(state)
        state.by_id(2).position = (3, 0)
        state.by_id(2).chosen_target = 1
        events = []
        runner = runner_for(state, events=events)
        runner.melee_attack(state.by_id(2))
        self.assertIn("fell short", events_of_type(events, "info")[0]["message"])


class RunBarsTest(TestCase):
    """#778 — armor-and-shields.md: "Chainmail | 3 | −3 | 200 | 30.0 | Cannot
    Run or Sprint"; movement.md: "Medium | STR×1.5+1 to STR×2 | −2 | Cannot
    Run or Sprint"."""

    def test_the_armour_row_is_as_quoted(self):
        text = (SPEC / "equipment" / "armor-and-shields.md").read_text()
        self.assertIn(
            "| Chainmail     | 3     | −3  | 200   | 30.0     | Cannot Run or Sprint |",
            text,
        )

    def test_a_figure_that_cannot_run_is_not_offered_move(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=-6, r=0, move_run=0),
                make_combatant(2, q=6, r=0),
            ],
        )
        menu = letters(state, 1)
        self.assertNotIn("a", menu)
        self.assertNotIn("a_yield", menu)
        self.assertIn("b", menu)

    def test_a_charge_that_cannot_jog_moves_at_a_walk_with_no_fatigue(self):
        mover = make_combatant(1, q=-8, r=0, move_jog=0, move_walk=3)
        state = BattleState(
            arena_radius=10, combatants=[mover, make_combatant(2, q=8, r=0)]
        )
        mover.chosen_letter = "b"
        mover.chosen_target = 2
        events = []
        runner_for(state, events=events).move_towards_target(mover, gait="jog")
        self.assertEqual(mover.position, (-5, 0))
        self.assertEqual(mover.fatigue, mover.max_fatigue)


class SprintTest(TestCase):
    """#818 — movement.md: "Sprint | 18 + modifier | 6 per turn | None"."""

    def test_sprint_is_on_the_menu_and_costs_six_fatigue(self):
        mover = make_combatant(1, q=-10, r=0, move_sprint=18)
        state = BattleState(
            arena_radius=12, combatants=[mover, make_combatant(2, q=10, r=0)]
        )
        decision = policy.choose_option(state, mover)
        sprint = next(c for c in decision.candidates if c.letter == "sprint")
        self.assertEqual(sprint.score, 0.0)  # the AI keeps to the run
        self.assertNotEqual(decision.chosen.letter, "sprint")
        mover.chosen_letter = "sprint"
        mover.chosen_target = 2
        events = []
        runner_for(state, events=events).move_towards_target(mover, gait="sprint")
        self.assertEqual(mover.position, (8, 0))  # 18 hexes, stopped adjacent
        self.assertEqual(mover.fatigue, mover.max_fatigue - engine.SPRINT_FATIGUE_COST)
        self.assertIn("sprints", events_of_type(events, "movement")[0]["message"])

    def test_no_sprint_distance_means_no_sprint(self):
        state = BattleState(
            arena_radius=10,
            combatants=[make_combatant(1, q=-6, r=0), make_combatant(2, q=6, r=0)],
        )
        self.assertNotIn("sprint", letters(state, 1))


class ForcedRetreatDirectionTest(TestCase):
    """#779 — special-combat-situations.md: "Push enemy back 1 hex in any
    direction" / "Choose to advance into vacated hex or stand still" / "No
    retreat hex → enemy rolls 3d6 ≤ DEX or falls"."""

    def rim_push(self, **runner_kwargs):
        state = duel_state()
        pusher, victim = state.by_id(1), state.by_id(2)
        victim.position = (6, 0)
        pusher.position = (5, 0)
        pusher.dealt_physical_hit_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner_for(state, [[6, 6, 6]], events, **runner_kwargs).phase_forced_retreat()
        return pusher, victim, events

    def test_a_blocked_straight_back_pushes_to_a_clear_side_hex(self):
        pusher, victim, events = self.rim_push()
        self.assertIn(victim.position, [(6, -1), (5, 1)])
        self.assertFalse(victim.prone)
        self.assertEqual(events_of_type(events, "roll"), [])
        self.assertEqual(pusher.position, (6, 0))

    def test_the_pusher_may_stand_still(self):
        def stand(state, pusher, victim, hexes, may_advance):
            return policy.RetreatChoice(destination=hexes[0], advance=False)

        pusher, victim, events = self.rim_push(choose_retreat=stand)
        self.assertEqual(pusher.position, (5, 0))
        self.assertNotIn("advances", events_of_type(events, "movement")[0]["message"])


class DroppedWeaponTest(TestCase):
    """#780 — attack-rolls.md "4–5 drop weapon"; action-options.md: "q |
    PICK UP WEAPON | Still | Drop yours, grab from hex/adjacent", "m | CHANGE
    WEAPON | Shift/still | Drop current, ready new non-missile", "e | READY
    WEAPON | Walk | Re-sling current, ready new weapon/shield"."""

    def test_a_dropped_weapon_lies_in_the_hex_and_can_be_picked_up(self):
        state = duel_state()
        fighter = state.by_id(1)
        fighter.weapon_skill_level = 2
        runner_for(state, [[1], [4]]).resolve_attack(
            fighter, state.by_id(2), ranged=False
        )
        self.assertEqual(fighter.weapon.item_id, "")
        self.assertEqual(state.ground_weapons[0].position, fighter.position)
        decision = policy.choose_option(state, fighter)
        self.assertEqual(decision.chosen.letter, "q")
        self.assertNotIn("j", [c.letter for c in decision.candidates])
        runner_for(state).pick_up_weapon(fighter)
        self.assertEqual(fighter.weapon.item_id, "broadsword")
        self.assertEqual(fighter.weapon_skill_level, 2)
        self.assertEqual(state.ground_weapons, [])

    def test_change_weapon_drops_the_bow_and_readies_a_hand_weapon(self):
        state = duel_state()
        archer = state.by_id(1)
        archer.weapon = SMALL_BOW
        archer.spare_weapons = [DAGGER]
        archer.weapon_skills = {"dagger": 1}
        self.assertIn("m", letters(state, 1))
        runner_for(state).change_weapon(archer)
        self.assertEqual(archer.weapon.item_id, "dagger")
        self.assertEqual(archer.weapon_skill_level, 1)
        self.assertEqual(state.ground_weapons[0].weapon.item_id, "small_bow")

    def test_ready_weapon_slings_the_current_one(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=-5, r=0, spare_weapons=[DAGGER]),
                make_combatant(2, q=5, r=0),
            ],
        )
        fighter = state.by_id(1)
        self.assertIn("e", letters(state, 1))
        runner_for(state).ready_weapon(fighter)
        self.assertEqual(fighter.weapon.item_id, "dagger")
        self.assertEqual([w.item_id for w in fighter.spare_weapons], ["broadsword"])

    def test_ground_weapons_survive_the_snapshot(self):
        state = duel_state()
        state.ground_weapons.append(GroundWeapon(q=2, r=0, weapon=DAGGER))
        state.by_id(1).spare_weapons = [DAGGER]
        restored = BattleState.from_dict(state.to_dict())
        self.assertEqual(restored.ground_weapons[0].weapon, DAGGER)
        self.assertEqual(restored.by_id(1).spare_weapons, [DAGGER])


class RateOfFireTest(TestCase):
    """#781 — weapons.md: "2 shots/turn if adjDEX 15+" (small bow), 16+
    (horse bow), 18+ (longbow); light crossbow "Every other turn; every if
    14+"; heavy crossbow "Every 3rd turn; every other 16+"."""

    def test_the_weapons_table_notes_parse_to_the_rates(self):
        text = (SPEC / "equipment" / "weapons.md").read_text()
        notes = {}
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 6 and cells[0].rstrip("†") in (
                "Small Bow",
                "Horse Bow",
                "Longbow",
                "Light Crossbow",
                "Heavy Crossbow",
            ):
                notes[cells[0].rstrip("†")] = weapons.missile_rate_from_note(cells[5])
        self.assertEqual(notes["Small Bow"].double_shot_dex, 15)
        self.assertEqual(notes["Horse Bow"].double_shot_dex, 16)
        self.assertEqual(notes["Longbow"].double_shot_dex, 18)
        self.assertEqual(
            notes["Light Crossbow"],
            weapons.MissileRate(
                reload_turns=2, quick_reload_dex=14, quick_reload_turns=1
            ),
        )
        self.assertEqual(
            notes["Heavy Crossbow"],
            weapons.MissileRate(
                reload_turns=3, quick_reload_dex=16, quick_reload_turns=2
            ),
        )

    def test_a_quick_longbowman_looses_twice(self):
        longbow = WeaponState(
            item_id="longbow",
            name="Longbow",
            weapon_class="Missile — Bows",
            damage="1d6+2",
            is_missile=True,
            double_shot_dex=18,
        )
        archer = make_combatant(1, q=-4, r=0, dexterity=18, weapon=longbow)
        state = BattleState(
            arena_radius=8, combatants=[archer, make_combatant(2, q=0, r=0)]
        )
        archer.chosen_target = 2
        events = []
        runner_for(state, [[2], [2]], events).missile_attack(archer)
        self.assertEqual(
            len(
                [
                    e
                    for e in events_of_type(events, "roll")
                    if e["payload"]["purpose"] == "attack"
                ]
            ),
            2,
        )

    def test_a_light_crossbow_shoots_every_other_turn(self):
        crossbow = WeaponState(
            item_id="light_crossbow",
            name="Light Crossbow",
            weapon_class="Missile — Crossbows",
            damage="2d6",
            is_missile=True,
            reload_turns=2,
            quick_reload_dex=14,
        )
        archer = make_combatant(1, q=-4, r=0, weapon=crossbow)
        state = BattleState(
            arena_radius=8, combatants=[archer, make_combatant(2, q=0, r=0)]
        )
        archer.chosen_target = 2
        runner_for(state, [[2]]).missile_attack(archer)
        runner = runner_for(state)
        runner._start_of_turn_missile_state()  # turn 2: still reloading
        self.assertNotIn("f", letters(state, 1))
        runner._start_of_turn_missile_state()  # turn 3: loaded
        self.assertIn("f", letters(state, 1))
        archer.dexterity = 14  # quick enough to shoot every turn
        runner_for(state, [[2]]).missile_attack(archer)
        self.assertEqual(archer.weapon.reload_turns_left, 0)

    def test_a_fired_crossbow_dropped_and_picked_up_is_still_unloaded(self):
        crossbow = WeaponState(
            item_id="light_crossbow",
            name="Light Crossbow",
            weapon_class="Missile — Crossbows",
            damage="2d6",
            is_missile=True,
            reload_turns=2,
        )
        archer = make_combatant(1, q=-4, r=0, weapon=crossbow)
        state = BattleState(
            arena_radius=8, combatants=[archer, make_combatant(2, q=0, r=0)]
        )
        archer.chosen_target = 2
        runner = runner_for(state, [[2]])
        runner.missile_attack(archer)
        runner._weapon_leaves_hand(archer, lands_at=archer.position)
        runner.pick_up_weapon(archer)
        self.assertEqual(archer.weapon.item_id, "light_crossbow")
        self.assertEqual(archer.weapon.reload_turns_left, 2)
        self.assertNotIn("f", letters(state, 1))


class OffBalanceNextActionTest(TestCase):
    """#811 — attack-rolls.md: "**1–3** off-balance (−2 to your next
    action)"."""

    def test_the_penalty_lands_on_a_casting_roll_and_is_spent(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.intelligence = 12
        caster.mana = caster.max_mana = 5
        caster.spells = ["shield"]
        caster.wisdom = 12
        caster.chosen_letter = "r"
        caster.chosen_spell = "shield"
        caster.off_balance = True
        events = []
        runner_for(state, [[4, 4, 4]], events).execute_action(caster)
        casting = events_of_type(events, "roll")[0]
        self.assertEqual(casting["payload"]["target_number"], 10)
        self.assertFalse(caster.off_balance)

    def test_defending_spends_it_too(self):
        state = duel_state()
        fighter = state.by_id(1)
        fighter.chosen_letter = "k"
        fighter.off_balance = True
        runner_for(state).execute_action(fighter)
        self.assertFalse(fighter.off_balance)

    def test_a_fumble_during_the_action_carries_to_the_next(self):
        state = duel_state()
        fighter = state.by_id(1)
        fighter.chosen_letter = "j"
        fighter.chosen_target = 2
        runner_for(state, [[1], [2]]).execute_action(fighter)
        self.assertTrue(fighter.off_balance)


class ThrownWeaponTest(TestCase):
    """#812 — dex-adjustments.md: "## Range (Thrown Weapons)" / "−1 to hit
    per hex to target"; action-options.md f: "Fire bow, crossbow, or thrown
    weapon"."""

    def test_the_page_says_so(self):
        text = (SPEC / "combat" / "action-options" / "dex-adjustments.md").read_text()
        self.assertIn("## Range (Thrown Weapons)\n\n−1 to hit per hex to target.", text)

    def test_a_thrown_rock_takes_the_thrown_range_table(self):
        thrower = make_combatant(1, q=0, r=0, weapon=THROWN_ROCK)
        archer = make_combatant(3, q=0, r=1, weapon=SMALL_BOW)
        target = make_combatant(2, q=3, r=0)
        self.assertEqual(
            combat_math.attack_numbers(thrower, target, ranged=True).range_penalty, -3
        )
        target.position = (6, 0)
        self.assertEqual(
            combat_math.attack_numbers(thrower, target, ranged=True).range_penalty, -6
        )
        self.assertEqual(
            combat_math.attack_numbers(archer, target, ranged=True).range_penalty, 0
        )

    def test_the_ai_keeps_its_only_hand_weapon(self):
        state = BattleState(
            arena_radius=12,
            combatants=[
                make_combatant(1, q=-9, r=0, weapon=DAGGER),
                make_combatant(2, q=9, r=0),
            ],
        )
        decision = policy.choose_option(state, state.by_id(1))
        throw = next(c for c in decision.candidates if c.letter == "f")
        self.assertEqual(throw.score, 0.0)
        self.assertNotEqual(decision.chosen.letter, "f")

    def test_a_dagger_can_be_thrown_and_lands_by_its_target(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=-4, r=0, weapon=DAGGER, spare_weapons=[BROADSWORD]),
                make_combatant(2, q=0, r=0),
            ],
        )
        self.assertIn("f", letters(state, 1))
        thrower = state.by_id(1)
        thrower.chosen_target = 2
        runner_for(state, [[18], [3]]).missile_attack(thrower)
        self.assertEqual(thrower.weapon.item_id, "")
        self.assertEqual(state.ground_weapons[0].position, (0, 0))


class ForcedRetreatHitsTest(TestCase):
    """#813 — special-combat-situations.md: "If you dealt physical hits and
    took none"."""

    def test_spell_damage_does_not_arm_a_push(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.intelligence = 14
        caster.mana = caster.max_mana = 5
        caster.chosen_spell = "fire_missile"
        caster.chosen_target = 2
        runner_for(state, [[3, 3, 3], [3, 3, 3], [5]]).cast_spell(caster)
        self.assertTrue(caster.dealt_damage_this_turn)
        self.assertFalse(caster.dealt_physical_hit_this_turn)
        self.assertFalse(engine.TARMAR.retreat.pusher_eligible(caster))

    def test_a_blow_the_armour_stopped_is_not_a_physical_hit(self):
        # Review ruling: a physical hit is post-armour damage above 0.
        state = duel_state()
        state.by_id(2).stops = 12
        attacker = state.by_id(1)
        runner_for(state, [[18], [1, 1]]).resolve_attack(
            attacker, state.by_id(2), ranged=False
        )
        self.assertFalse(attacker.dealt_physical_hit_this_turn)
        self.assertFalse(engine.TARMAR.retreat.pusher_eligible(attacker))
        self.assertFalse(state.by_id(2).took_physical_hit_this_turn)

    def test_a_blow_that_gets_through_is(self):
        state = duel_state()
        attacker = state.by_id(1)
        runner_for(state, [[18], [3, 3]]).resolve_attack(
            attacker, state.by_id(2), ranged=False
        )
        self.assertTrue(engine.TARMAR.retreat.pusher_eligible(attacker))


class StressedWeaponTest(TestCase):
    """#814 — attack-rolls.md: "**6** weapon takes stress (breaks on a
    second fumble)"."""

    def test_any_second_fumble_breaks_a_stressed_weapon(self):
        for fumble_face, off_balance in (([2], True), ([4], False), ([6], False)):
            state = duel_state()
            fighter = state.by_id(1)
            fighter.weapon = WeaponState(**{**BROADSWORD.__dict__, "stressed": True})
            events = []
            runner_for(state, [[1], fumble_face], events).resolve_attack(
                fighter, state.by_id(2), ranged=False
            )
            self.assertEqual(fighter.weapon.item_id, "", fumble_face)
            self.assertEqual(
                state.ground_weapons, [], fumble_face
            )  # broken, not dropped
            self.assertEqual(fighter.off_balance, off_balance, fumble_face)
            self.assertIn("breaks", events_of_type(events, "status")[0]["message"])


class BareHandedIsHthTest(TestCase):
    """#815 — hand-to-hand-and-grappling.md: bare-handed fighting happens
    "only ... once you and an enemy share the tight, in-close range HTH
    requires"; "No Weapon skill applies; there is no bare-hands entry in the
    skill catalog"."""

    def test_bare_hands_get_no_plain_attack(self):
        state = duel_state()
        state.by_id(1).weapon = WeaponState()
        menu = letters(state, 1)
        self.assertNotIn("j", menu)
        self.assertNotIn("t", menu)  # no way in: front, equally quick

    def test_a_bare_handed_strike_takes_the_hth_four_and_no_skill(self):
        state = duel_state()
        brawler = state.by_id(1)
        brawler.weapon = WeaponState(damage="1d6-2")
        brawler.weapon_skill_level = 3  # a "brawling" level: the rules have none
        state.by_id(2).movement_modifier = -1
        decision = policy.choose_option(state, brawler)
        self.assertIn("t", [c.letter for c in decision.candidates])
        brawler.chosen_target = 2
        events = []
        runner_for(state, [[10], [4]], events).hth_strike(brawler)
        roll = events_of_type(events, "roll")[0]
        self.assertEqual(roll["payload"]["modifier"], 1 + 4)  # DEX +1, HTH +4
        self.assertIn("bare hands", events_of_type(events, "action")[0]["message"])

    def test_a_beast_keeps_its_natural_attack(self):
        state = duel_state()
        state.by_id(1).weapon = WeaponState(name="bite", damage="1d6")
        state.by_id(1).is_beast = True
        self.assertIn("j", letters(state, 1))


class ManaOnFailureTest(TestCase):
    """#816 — mana-pool.md: "17 | Bad fumble—spell fails, mana lost, negative
    effect"; runaway.md (on 18): "Spell drains mana equal to original casting
    cost"."""

    def cast(self, faces):
        state = duel_state()
        caster = state.by_id(1)
        caster.intelligence = 12
        caster.mana = caster.max_mana = 10
        caster.chosen_spell = "fire_missile"
        events = []
        runner_for(state, [faces], events).cast_spell(caster)
        return caster, events

    def test_an_ordinary_failure_keeps_the_mana(self):
        for faces in ([5, 5, 5], [6, 5, 5]):  # 15 over INT 12; 16 the fumble
            caster, events = self.cast(faces)
            self.assertEqual(caster.mana, 10, faces)
            self.assertIn(
                "no mana spent", events_of_type(events, "action")[0]["message"]
            )

    def test_a_bad_fumble_loses_it(self):
        caster, _events = self.cast([6, 6, 5])
        self.assertEqual(caster.mana, 9)

    def test_success_pays(self):
        caster, _events = self.cast([2, 2, 2])
        self.assertEqual(caster.mana, 9)


class InjuryPenaltyEverywhereTest(TestCase):
    """#817 — injury-thresholds-death.md: "Between 1 and 5 | −2 to all
    rolls"."""

    def test_the_page_says_so(self):
        text = (SPEC / "characters" / "injury-thresholds-death.md").read_text()
        self.assertIn("| Between 1 and 5              | −2 to all rolls ", text)

    def test_initiative_carries_the_band(self):
        state = duel_state()
        state.by_id(1).fatigue = 3
        events = []
        runner_for(state, [[4], [4]], events).phase_initiative()
        rolls = {
            e["actor"]: e["payload"]["modifier"] for e in events_of_type(events, "roll")
        }
        self.assertEqual(rolls["Fighter 1"], state.by_id(1).dex_bonus - 2)
        self.assertEqual(rolls["Fighter 2"], state.by_id(2).dex_bonus)

    def test_the_retreat_save_carries_the_band(self):
        state = duel_state()
        pusher, victim = state.by_id(1), state.by_id(2)
        victim.position, pusher.position = (6, 0), (5, 0)
        for offset, cell in enumerate(((6, -1), (5, 1))):
            state.combatants.append(
                make_combatant(90 + offset, q=cell[0], r=cell[1], team="walls")
            )
        victim.fatigue = 3
        pusher.dealt_physical_hit_this_turn = True
        pusher.chosen_target = 2
        events = []
        runner_for(state, [[4, 4, 3]], events).phase_forced_retreat()
        save = events_of_type(events, "roll")[0]
        self.assertEqual(save["payload"]["target_number"], victim.dexterity - 2)
        self.assertTrue(victim.prone)  # 11 over 10


class YieldingTest(TestCase):
    """#819 — turn-sequence.md: "any combatant may hold their Initial
    Movement and move in Final Movement instead"; movement.md's Jog row
    lists "Charge Attack, Dodge, Drop"."""

    def test_the_menu_offers_yielded_movement(self):
        state = BattleState(
            arena_radius=10,
            combatants=[make_combatant(1, q=-6, r=0), make_combatant(2, q=6, r=0)],
        )
        menu = letters(state, 1)
        self.assertIn("a_yield", menu)
        self.assertIn("b_yield", menu)
        self.assertNotEqual(
            policy.choose_option(state, state.by_id(1)).chosen.letter, "a_yield"
        )

    def test_a_yielded_move_waits_for_final_movement(self):
        mover = make_combatant(1, q=-6, r=0)
        state = BattleState(
            arena_radius=10, combatants=[mover, make_combatant(2, q=6, r=0)]
        )
        runner = runner_for(state)
        runner.phase_initial_movement([mover], fixed_choice("a_yield", target_id=2))
        self.assertEqual(mover.position, (-6, 0))
        runner.phase_final_movement([mover])
        self.assertNotEqual(mover.position, (-6, 0))

    def test_dodge_jogs_toward_the_missile_threat(self):
        dodger = make_combatant(1, q=-6, r=0)
        archer = make_combatant(2, q=6, r=0, weapon=SMALL_BOW)
        state = BattleState(arena_radius=10, combatants=[dodger, archer])
        dodge = next(
            c for c in policy.choose_option(state, dodger).candidates if c.letter == "c"
        )
        self.assertEqual(dodge.target_id, 2)
        runner_for(state).phase_initial_movement(
            [dodger], fixed_choice("c", target_id=2)
        )
        self.assertEqual(dodger.position, (1, 0))  # a 7-hex jog


class EngagersAreArmedAndStandingTest(TestCase):
    """#820 — movement.md: "One-hex figure | In an armed enemy's front hex";
    "Prone/crawling: All hexes count as rear"."""

    def test_prone_and_unarmed_enemies_do_not_engage(self):
        state = duel_state()
        self.assertTrue(combat_math.is_engaged(state, state.by_id(1)))
        state.by_id(2).prone = True
        self.assertFalse(combat_math.is_engaged(state, state.by_id(1)))
        state.by_id(2).prone = False
        state.by_id(2).weapon = WeaponState()
        self.assertFalse(combat_math.is_engaged(state, state.by_id(1)))
        self.assertFalse(engine.TARMAR.engagement.is_engaged(state, state.by_id(1)))
        state.by_id(2).is_beast = True  # natural weapons are arms
        self.assertTrue(combat_math.is_engaged(state, state.by_id(1)))


class GrappledCasterTest(TestCase):
    """#821 — hand-to-hand-and-grappling.md: "A grappled caster can only
    renew a spell that needs no gestures (Spell Mastery 2+); anything else
    lapses." and "Spellcasting is limited to a spell you can cast with no
    hand gestures and no verbal component — Spell Mastery level 2 and 3
    respectively"."""

    def held_caster(self):
        state = duel_state()
        caster, captor = state.by_id(1), state.by_id(2)
        caster.grappled_by, captor.grappling = 2, 1
        caster.intelligence = caster.wisdom = 14
        caster.mana = caster.max_mana = 10
        return state, caster

    def test_mastery_two_spells_are_renewed_and_the_rest_lapse(self):
        state, caster = self.held_caster()
        caster.active_spells = ["shield", "blur"]
        caster.spell_mastery = {"shield": 2}
        runner_for(state).phase_renew_spells()
        self.assertEqual(caster.active_spells, ["shield"])
        self.assertEqual(caster.mana, 9)

    def test_only_a_mastery_three_spell_is_cast(self):
        state, caster = self.held_caster()
        caster.chosen_spell = "shield"
        caster.spell_mastery = {"shield": 2}
        events = []
        runner_for(state, [[3, 3, 3]], events).cast_spell(caster)
        self.assertEqual(caster.active_spells, [])
        self.assertIn("cannot cast", events_of_type(events, "info")[0]["message"])
        caster.spell_mastery = {"shield": 3}
        caster.spells = ["shield"]
        runner_for(state, [[3, 3, 3]]).cast_spell(caster)
        self.assertEqual(caster.active_spells, ["shield"])
        held_menu = [c.letter for c in policy.choose_option(state, caster).candidates]
        self.assertNotIn("r", held_menu)  # shield is already up
        caster.active_spells = []
        caster.spells = ["shield"]
        held_menu = [c.letter for c in policy.choose_option(state, caster).candidates]
        self.assertIn("r", held_menu)


class StrikeBackWithADaggerTest(TestCase):
    """#822 — hand-to-hand-and-grappling.md: "**Strike Back** — a normal
    unarmed strike (or dagger, if already drawn)"; action-options.md "u |
    DRAW DAGGER | Roll 3d6 ≤ DEX to ready dagger"."""

    def held(self):
        state = duel_state()
        held, captor = state.by_id(1), state.by_id(2)
        held.grappled_by, captor.grappling = 2, 1
        return state, held

    def test_strike_back_uses_a_drawn_dagger_and_its_skill(self):
        state, held = self.held()
        held.weapon = DAGGER
        held.weapon_skill_level = 2
        events = []
        runner_for(state, [[10], [4]], events).grapple_strike_back(held)
        self.assertIn("with Dagger", events_of_type(events, "action")[0]["message"])
        self.assertEqual(
            events_of_type(events, "roll")[0]["payload"]["modifier"], 1 + 2 + 4
        )

    def test_draw_dagger_is_offered_and_readies_it(self):
        state, held = self.held()
        held.spare_weapons = [DAGGER]
        self.assertIn("u", letters(state, 1))
        runner_for(state, [[2, 2, 2]]).draw_dagger(held)
        self.assertEqual(held.weapon.item_id, "dagger")
        self.assertEqual(state.ground_weapons[0].weapon.item_id, "broadsword")


class HthEntryConditionsTest(TestCase):
    """#823 — hand-to-hand-and-grappling.md: "only if one of these holds: the
    enemy has their back to a wall, is down/prone/kneeling, has a lower
    movement modifier than you, you're attacking from their rear, or they
    simply agree"."""

    def test_no_condition_no_grapple(self):
        state = duel_state()
        self.assertNotIn("o", letters(state, 1))
        attacker = state.by_id(1)
        attacker.chosen_target = 2
        events = []
        runner_for(state, [[20]], events).attempt_grapple(attacker)
        self.assertIsNone(attacker.grappling)
        self.assertEqual(
            events_of_type(events, "info")[0]["payload"]["grapple_refused"], "no_entry"
        )

    def test_the_grapple_candidate_names_an_admitted_target(self):
        # Two adjacent enemies: the lower id is face on and equally quick,
        # the other is slower — only the second may be grappled.
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
                make_combatant(3, q=0, r=1, facing=4, movement_modifier=-1),
            ],
        )
        grapple = next(
            c
            for c in policy.choose_option(state, state.by_id(1)).candidates
            if c.letter == "o"
        )
        self.assertEqual(grapple.target_id, 3)

    def test_each_condition_admits_it(self):
        cases = {
            "from their rear": {"facing": 0},
            "their back is to the wall": {"q": 6, "r": 0, "facing": 3},
            "they are slower": {"movement_modifier": -1},
        }
        for reason, overrides in cases.items():
            state = duel_state()
            defender = state.by_id(2)
            if "q" in overrides:
                state.by_id(1).position = (5, 0)
            for field, value in overrides.items():
                setattr(defender, field, value)
            self.assertEqual(
                combat_math.hth_entry_reason(state, state.by_id(1), defender), reason
            )
        state = duel_state()
        state.by_id(2).prone = True
        self.assertEqual(
            combat_math.hth_entry_reason(state, state.by_id(1), state.by_id(2)),
            "they are down",
        )


class CriticalModifierOnceTest(TestCase):
    """#824 — attack-rolls.md: "**Natural 20** → auto-hit regardless of TN,
    and a **critical**: roll the weapon's damage dice **twice**"."""

    def test_the_page_says_so(self):
        text = (SPEC / "combat" / "action-options" / "attack-rolls.md").read_text()
        self.assertIn("roll the weapon's\n  damage dice **twice**", text)

    def test_the_dice_roll_twice_and_the_modifier_counts_once(self):
        state = duel_state()
        attacker = state.by_id(1)
        attacker.weapon = WeaponState(
            item_id="great_hammer",
            name="Great Hammer",
            weapon_class="Heavy Striking",
            damage="2d6+2",
        )
        events = []
        runner_for(state, [[20], [1], [3, 3], [4, 4]], events).resolve_attack(
            attacker, state.by_id(2), ranged=False
        )
        damage_rolls = [
            e
            for e in events_of_type(events, "roll")
            if e["payload"]["purpose"] == "damage"
        ]
        self.assertEqual(
            [r["payload"]["specification"] for r in damage_rolls], ["2d6+2", "2d6"]
        )
        defender = state.by_id(2)
        self.assertEqual(defender.fatigue, defender.max_fatigue - (6 + 2 + 8))


class UnconsciousCasterTest(TestCase):
    """#826 — casting-spells.md: "Continuing spells require mana payment each
    turn during Phase 2 of combat. Unrenewed spells end immediately."."""

    def test_an_unconscious_casters_spells_end(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.active_spells = ["shield"]
        caster.mana = 5
        caster.conscious = False
        events = []
        runner_for(state, events=events).phase_renew_spells()
        self.assertEqual(caster.active_spells, [])
        self.assertEqual(caster.mana, 5)
        self.assertIn("unconscious", events_of_type(events, "status")[0]["message"])


class HthByAgreementTest(TestCase):
    """#867 (landed in this pass on review) — hand-to-hand-and-grappling.md:
    "or they simply agree. Once you're sharing a hex, **both combatants get
    +4** to their to-hit rolls"."""

    def bare_pair(self, **second):
        first = make_combatant(
            1, q=0, r=0, facing=0, weapon=WeaponState(damage="1d6-2")
        )
        other = make_combatant(
            2, q=1, r=0, facing=3, weapon=WeaponState(damage="1d6-2"), **second
        )
        return BattleState(arena_radius=6, combatants=[first, other])

    def test_two_bare_handed_figures_fight_hand_to_hand(self):
        state = self.bare_pair()
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "t")
        self.assertEqual(
            combat_math.hth_entry_reason(state, state.by_id(1), state.by_id(2)),
            "both bare-handed",
        )

    def test_the_struck_figure_strikes_back_at_plus_four(self):
        state = self.bare_pair()
        first, second = state.by_id(1), state.by_id(2)
        first.chosen_target, second.chosen_target = 2, 1
        runner_for(state, [[2]]).hth_strike(first)
        self.assertEqual(second.hth_with, [1])
        # Armed now: no bare-handed agreement, but the pair is in HTH.
        second.weapon = DAGGER
        self.assertEqual(
            combat_math.hth_entry_reason(state, second, first),
            "already in hand-to-hand",
        )
        events = []
        runner_for(state, [[10], [3]], events).hth_strike(second)
        self.assertEqual(
            events_of_type(events, "roll")[0]["payload"]["modifier"], 1 + 4
        )

    def test_bare_hands_against_a_weapon_keep_the_conditions(self):
        state = self.bare_pair()
        state.by_id(2).weapon = BROADSWORD
        self.assertIsNone(
            combat_math.hth_entry_reason(state, state.by_id(1), state.by_id(2))
        )

    def test_an_enemy_closing_on_you_agrees(self):
        state = duel_state()
        first, second = state.by_id(1), state.by_id(2)
        second.chosen_letter, second.chosen_target = "o", 1
        self.assertEqual(
            combat_math.hth_entry_reason(state, first, second), "they close in too"
        )

    def test_hand_to_hand_ends_when_the_pair_parts(self):
        state = self.bare_pair()
        first, second = state.by_id(1), state.by_id(2)
        first.chosen_target = 2
        runner = runner_for(state, [[2]])
        runner.hth_strike(first)
        second.position = (3, 0)
        runner._start_of_turn_missile_state()
        self.assertEqual(first.hth_with, [])
        self.assertEqual(second.hth_with, [])


class GrapplerCastsTest(TestCase):
    """#821 on review — both sides of a hold have their hands full:
    "one or both hands are occupied holding on or being held"."""

    def holder(self):
        state = duel_state()
        holder, held = state.by_id(1), state.by_id(2)
        holder.grappling, held.grappled_by = 2, 1
        holder.intelligence = holder.wisdom = 14
        holder.mana = holder.max_mana = 10
        return state, holder

    def test_the_grappler_renews_only_mastery_two_spells(self):
        state, holder = self.holder()
        holder.active_spells = ["shield", "blur"]
        holder.spell_mastery = {"shield": 2}
        events = []
        runner_for(state, events=events).phase_renew_spells()
        self.assertEqual(holder.active_spells, ["shield"])
        self.assertIn(
            "is holding a grapple", events_of_type(events, "status")[0]["message"]
        )

    def test_the_grappler_is_offered_a_mastery_three_cast(self):
        state, holder = self.holder()
        holder.spells = ["shield"]
        self.assertNotIn("r", letters(state, 1))
        holder.spell_mastery = {"shield": 3}
        self.assertIn("r", letters(state, 1))
