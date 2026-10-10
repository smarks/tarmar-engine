"""The hand-to-hand menu (tarmar-engine #19) and placed destinations (#20).

Spencer's rulings of 2026-10-09 (tarmar-studio #867, #819, #866): once a
figure is in hand-to-hand the HTH table is its menu, and a turn choice may
carry the hex the rules let the figure pick. Each test quotes the page it
holds the engine to; the pages are the vendored ones under
``tarmar_engine/spec/``.
"""

import json
from pathlib import Path
from unittest import TestCase

import tarmar_engine
from tarmar_engine import actions, combat_math, hexes, movement, policy
from tarmar_engine.policy import Candidate, Decision
from tarmar_engine.state import BattleState, WeaponState

from .test_battle_rules_pass import DAGGER, letters, runner_for
from .test_engine import duel_state, events_of_type
from .test_state import make_combatant

COMBAT = Path(tarmar_engine.__file__).resolve().parent / "spec" / "combat"
HTH_PAGE = (COMBAT / "action-options" / "hand-to-hand-and-grappling.md").read_text()
OPTIONS_PAGE = (COMBAT / "action-options.md").read_text()
SITUATIONS_PAGE = (
    COMBAT / "action-options" / "special-combat-situations.md"
).read_text()


def choosing(letter, target_id=None, destination=None, spell_key=""):
    """A choose_option that always picks one option, with a hex (a player)."""

    def choose(_state, _actor):
        chosen = Candidate(
            letter,
            actions.option_name(letter),
            1.0,
            "test",
            target_id,
            spell_key,
            destination=destination,
        )
        return Decision(chosen=chosen, candidates=[chosen])

    return choose


def in_hand_to_hand(state, first_id=1, second_id=2):
    state.by_id(first_id).hth_with.append(second_id)
    state.by_id(second_id).hth_with.append(first_id)
    return state


class HthTableIsTheMenuTest(TestCase):
    """#19 — action-options.md's Hand-to-Hand Combat table is the whole menu
    once a figure is in hand-to-hand."""

    def test_the_page_lists_t_u_v_and_the_cast(self):
        for row in (
            "| t      | HTH ATTACK  | Bare hands or dagger. Both get +4 to hit |",
            "| u      | DRAW DAGGER | Roll 3d6 ≤ DEX to ready dagger           |",
            "| v      | DISENGAGE   | Roll 4d6 ≤ DEX → stand, move to adjacent |",
            "|        | CAST SPELL  | If hands free or no-gesture spell        |",
        ):
            self.assertIn(row, OPTIONS_PAGE)

    def test_legal_actions_returns_the_table(self):
        self.assertEqual(
            actions.legal_actions(
                engaged=True,
                prone=False,
                has_missile=False,
                has_spells=True,
                has_melee_target=True,
                in_hth=True,
                can_draw_dagger=True,
            ),
            ["t", "u", "v", "r"],
        )
        self.assertEqual(
            actions.legal_actions(
                engaged=True,
                prone=True,
                has_missile=False,
                has_spells=False,
                has_melee_target=True,
                in_hth=True,
            ),
            ["t", "v"],
        )

    def test_an_armed_figure_strikes_with_bare_hands_not_its_sword(self):
        state = in_hand_to_hand(duel_state())
        self.assertEqual(letters(state, 1), ["t", "v"])
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "t")
        self.assertIn("bare hands", decision.chosen.rationale)

    def test_a_dagger_in_hand_is_the_strike(self):
        state = in_hand_to_hand(duel_state())
        state.by_id(1).weapon = DAGGER
        decision = policy.choose_option(state, state.by_id(1))
        self.assertIn("the Dagger", decision.chosen.rationale)

    def test_a_carried_dagger_may_be_drawn(self):
        state = in_hand_to_hand(duel_state())
        figure = state.by_id(1)
        figure.spare_weapons = [DAGGER]
        self.assertEqual(letters(state, 1), ["t", "u", "v"])
        figure.chosen_letter = "u"
        events = []
        runner_for(state, [[3, 3, 3]], events).execute_action(figure)
        self.assertTrue(figure.weapon.hth_usable)
        self.assertIn(
            "draws the Dagger", events_of_type(events, "action")[0]["message"]
        )

    def test_the_struck_figure_strikes_back_at_plus_four_bare_handed(self):
        """'Once you're sharing a hex, **both combatants get +4**'."""
        self.assertIn("**both combatants get +4**", HTH_PAGE)
        state = duel_state()
        first, second = state.by_id(1), state.by_id(2)
        first.weapon = WeaponState(damage="1d6-2")
        first.prone = False
        second.chosen_letter, second.chosen_target = "o", 1  # it closes in too
        first.chosen_target = 2
        runner_for(state, [[2]]).hth_strike(first)
        self.assertEqual(second.hth_with, [1])
        self.assertEqual(letters(state, 2), ["t", "v"])  # its broadsword is out
        second.chosen_target = 1
        events = []
        runner_for(state, [[10], [3]], events).hth_strike(second)
        plain = combat_math.attack_numbers(
            second,
            first,
            ranged=False,
            weapon_class="Striking",
            ignore_attacker_skill=True,
        )
        self.assertEqual(
            events_of_type(events, "roll")[0]["payload"]["modifier"],
            plain.bonus + 4,
        )

    def test_casting_needs_free_hands_or_a_no_gesture_spell(self):
        state = in_hand_to_hand(duel_state())
        caster = state.by_id(1)
        caster.intelligence = caster.wisdom = 14
        caster.mana = caster.max_mana = 10
        caster.spells = ["shield", "blur"]
        caster.spell_mastery = {"blur": 2}

        def casts():
            decision = policy.choose_option(state, caster)
            return [c.spell_key for c in decision.candidates if c.letter == "r"]

        self.assertEqual(casts(), ["blur"])  # the broadsword fills a hand
        caster.weapon = WeaponState(damage="1d6-2")
        self.assertEqual(casts(), ["shield", "blur"])  # hands free

    def test_no_weapon_attack_at_a_third_enemy_beside_it(self):
        """The open question on #19: a figure in hand-to-hand with one enemy
        is offered no j at a third. Coordinator's ruling under the standing
        rule (the page as written governs: the HTH table is the menu);
        Spencer may overrule."""
        state = in_hand_to_hand(duel_state())
        state.combatants.append(make_combatant(3, q=0, r=1, facing=5))
        self.assertTrue(combat_math.figures_adjacent(state.by_id(1), state.by_id(3)))
        self.assertNotIn("j", letters(state, 1))

    def test_down_in_hand_to_hand_the_table_still_rules(self):
        state = in_hand_to_hand(duel_state())
        state.by_id(1).prone = True
        self.assertEqual(letters(state, 1), ["t", "v"])

    def test_a_cast_that_bypasses_the_menu_is_refused_when_cast(self):
        """'CAST SPELL — If hands free or no-gesture spell', held by the
        engine as well as the menu: a supplied choice cannot get round it."""
        state = in_hand_to_hand(duel_state())
        caster = state.by_id(1)
        caster.intelligence = caster.wisdom = 14
        caster.mana = caster.max_mana = 10
        caster.spells = ["fire_missile"]
        caster.chosen_letter, caster.chosen_spell, caster.chosen_target = (
            "r",
            "fire_missile",
            2,
        )
        events = []
        runner_for(state, events=events).execute_action(caster)
        self.assertEqual(caster.mana, 10)
        self.assertEqual(events_of_type(events, "roll"), [])
        self.assertIn(
            "cannot cast Fire Missile", events_of_type(events, "info")[0]["message"]
        )
        caster.spell_mastery = {"fire_missile": 2}
        events = []
        runner_for(state, [[3, 3, 3]], events).execute_action(caster)
        self.assertEqual(
            events_of_type(events, "roll")[0]["payload"]["purpose"], "casting"
        )

    def test_a_beast_keeps_its_own_menu(self):
        state = in_hand_to_hand(duel_state())
        state.by_id(2).is_beast = True
        self.assertIn("j", letters(state, 2))

    def test_a_pair_that_has_parted_is_out(self):
        state = in_hand_to_hand(duel_state())
        state.by_id(2).position = (3, 0)
        self.assertFalse(movement.in_hand_to_hand(state, state.by_id(1)))

    def test_a_caster_in_hand_to_hand_takes_no_walk_slow_step(self):
        state = in_hand_to_hand(duel_state())
        caster = state.by_id(1)
        caster.chosen_letter, caster.yielded = "r", True
        runner_for(state).phase_final_movement([caster])
        self.assertEqual(caster.position, (0, 0))


class HthDisengageTest(TestCase):
    """#19 — special-combat-situations.md, "From HTH (option v)"."""

    def test_the_page_says_so(self):
        for line in (
            "- Roll 4d6 ≤ DEX",
            "- Success: stand up and move to adjacent empty hex",
            "- Failure: remain in HTH",
        ):
            self.assertIn(line, SITUATIONS_PAGE)

    def test_success_stands_steps_and_leaves_hand_to_hand(self):
        state = in_hand_to_hand(duel_state())
        figure = state.by_id(1)
        figure.prone = True
        figure.chosen_letter = "v"
        events = []
        runner_for(state, [[2, 2, 2, 2]], events).execute_action(figure)
        self.assertFalse(figure.prone)
        self.assertEqual(figure.position, (-1, 0))  # straight back
        self.assertEqual(figure.hth_with, [])
        self.assertEqual(state.by_id(2).hth_with, [])
        self.assertIn(
            "disengages from hand-to-hand",
            events_of_type(events, "movement")[0]["message"],
        )

    def test_failure_stays_in(self):
        state = in_hand_to_hand(duel_state())
        figure = state.by_id(1)
        figure.chosen_letter = "v"
        runner_for(state, [[6, 6, 6, 6]]).execute_action(figure)
        self.assertEqual(figure.position, (0, 0))
        self.assertEqual(figure.hth_with, [2])

    def test_the_chosen_hex_is_where_it_goes(self):
        state = in_hand_to_hand(duel_state())
        figure = state.by_id(1)
        figure.chosen_letter, figure.chosen_destination = "v", (0, 1)
        runner_for(state, [[2, 2, 2, 2]]).execute_action(figure)
        self.assertEqual(figure.position, (0, 1))

    def test_the_ai_leaves_the_step_to_the_engine_and_scores_the_roll(self):
        state = in_hand_to_hand(duel_state())
        decision = policy.choose_option(state, state.by_id(1))
        disengage = next(c for c in decision.candidates if c.letter == "v")
        self.assertIsNone(disengage.destination)
        self.assertIn("toward (-1, 0)", disengage.rationale)
        self.assertAlmostEqual(
            disengage.score,
            policy.round_score(
                policy.DISENGAGE_BASE_SCORE * policy.four_d6_at_most(12)
            ),
            places=3,
        )

    def test_a_struggle_free_with_nowhere_to_step_leaves_hand_to_hand(self):
        """Struggle Free is "the same roll as a plain HTH Disengage", so it
        ends hand-to-hand as v does, stepped clear or not."""
        self.assertIn("the same roll as a plain HTH [Disengage]", HTH_PAGE)
        state = BattleState(
            arena_radius=1,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
                *(
                    make_combatant(3 + index, q=q, r=r, facing=0, team="held")
                    for index, (q, r) in enumerate(
                        [(1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
                    )
                ),
            ],
        )
        held, grappler = state.by_id(1), state.by_id(2)
        held.team = "held"
        held.grappled_by, grappler.grappling = 2, 1
        in_hand_to_hand(state)
        held.chosen_letter = "v"
        events = []
        runner_for(state, [[2, 2, 2, 2]], events).execute_action(held)
        self.assertEqual(held.position, (0, 0))
        self.assertIsNone(held.grappled_by)
        self.assertEqual((held.hth_with, grappler.hth_with), ([], []))
        self.assertIn(
            "nowhere to step to", events_of_type(events, "status")[0]["message"]
        )

    def test_four_d6_at_most_is_exact(self):
        self.assertEqual(policy.four_d6_at_most(4), 1 / 1296)
        self.assertEqual(policy.four_d6_at_most(24), 1.0)
        self.assertEqual(policy.four_d6_at_most(14), 721 / 1296)


class CandidateDestinationTest(TestCase):
    """#20 — a turn choice carries a hex: action-options.md's Move column
    ("Shift/still", "Jog or less") and special-combat-situations.md's
    "Shift 1 hex or stand still during movement"."""

    def test_the_pages_say_so(self):
        self.assertIn("- Shift 1 hex or stand still during movement", SITUATIONS_PAGE)
        self.assertIn(
            "| d      | DROP           | Jog or less  | Go prone or kneeling",
            OPTIONS_PAGE,
        )
        self.assertIn(
            "| n      | DISENGAGE      | Shift/still | Move 1 hex any direction",
            OPTIONS_PAGE,
        )

    def test_the_payload_carries_a_hex_only_when_one_is_named(self):
        plain = Candidate("j", "ATTACK", 1.0, "r", 2)
        self.assertNotIn("destination", plain.to_payload())
        placed = Candidate("j", "ATTACK", 1.0, "r", 2, destination=(0, 1))
        self.assertEqual(placed.to_payload()["destination"], [0, 1])

    def test_an_engaged_option_shifts_one_hex(self):
        state = duel_state()
        legal = movement.legal_destinations(state, state.by_id(1), "j")
        # The two clear neighbours still in the enemy's front hexes.
        self.assertEqual(legal, {(0, 1), (1, -1)})
        events = []
        runner_for(state, events=events).phase_initial_movement(
            [state.by_id(1)], choosing("j", 2, (0, 1))
        )
        self.assertEqual(state.by_id(1).position, (0, 1))
        self.assertIn(
            "shifts one hex", events_of_type(events, "movement")[0]["message"]
        )

    def test_a_shift_may_not_leave_the_engagement(self):
        """Coordinator's ruling under the standing rule (Spencer may
        overrule): leaving an engagement is DISENGAGE's (n), so a DEFEND
        shift out of the enemy's reach is refused."""
        state = duel_state()
        figure = state.by_id(1)
        self.assertNotIn((-1, 0), movement.legal_destinations(state, figure, "k"))
        events = []
        runner_for(state, events=events).phase_initial_movement(
            [figure], choosing("k", destination=(-1, 0))
        )
        self.assertEqual(figure.position, (0, 0))
        self.assertIn(
            "out of an engagement", events_of_type(events, "info")[0]["message"]
        )
        self.assertTrue(combat_math.is_engaged(state, figure))

    def test_a_hex_survives_a_snapshot_round_trip(self):
        state = duel_state()
        figure = state.by_id(1)
        figure.chosen_letter, figure.chosen_destination = "j", (0, 1)
        restored = BattleState.from_dict(json.loads(json.dumps(state.to_dict())))
        again = restored.by_id(1)
        destination = again.chosen_destination
        self.assertEqual(destination, (0, 1))
        self.assertIsInstance(destination, tuple)  # JSON gave a list
        assert destination is not None
        self.assertIsNone(movement.refusal(restored, again, "j", destination))

    def test_a_hex_the_option_may_not_take_is_refused_in_the_log(self):
        state = duel_state()
        events = []
        runner_for(state, events=events).phase_initial_movement(
            [state.by_id(1)], choosing("j", 2, (-2, 0))
        )
        self.assertEqual(state.by_id(1).position, (0, 0))
        self.assertIsNone(state.by_id(1).chosen_destination)
        refusal = events_of_type(events, "info")[0]
        self.assertIn("cannot move to (-2, 0)", refusal["message"])
        self.assertEqual(refusal["payload"]["destination_refused"], [-2, 0])

    def test_a_charge_takes_no_hex(self):
        state = duel_state()
        self.assertEqual(movement.legal_destinations(state, state.by_id(1), "b"), set())
        self.assertIn(
            "takes no placed move",
            movement.refusal(state, state.by_id(1), "b", (0, 1)) or "",
        )

    def test_drop_jogs_to_its_hex_then_goes_prone(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=7, r=0, facing=3),
            ],
        )
        figure = state.by_id(1)
        self.assertIsNone(movement.refusal(state, figure, "d", (-4, 0)))
        self.assertIsNotNone(movement.refusal(state, figure, "d", (-8, 0)))  # past 7
        runner = runner_for(state)
        runner.phase_initial_movement([figure], choosing("d", destination=(-4, 0)))
        self.assertEqual(figure.position, (-4, 0))
        runner.execute_action(figure)
        self.assertTrue(figure.prone)

    def test_a_barred_jog_drops_within_the_walk(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0, move_jog=0, move_walk=3),
                make_combatant(2, q=7, r=0, facing=3),
            ],
        )
        figure = state.by_id(1)
        self.assertIsNone(movement.refusal(state, figure, "d", (-3, 0)))
        self.assertIsNotNone(movement.refusal(state, figure, "d", (-4, 0)))

    def test_disengage_steps_to_its_hex(self):
        state = duel_state()
        figure = state.by_id(1)
        decision = policy.choose_option(state, figure)
        disengage = next(c for c in decision.candidates if c.letter == "n")
        self.assertIsNone(disengage.destination)  # the engine steps when it acts
        self.assertIn("toward (-1, 0)", disengage.rationale)  # straight back
        figure.chosen_letter, figure.chosen_destination = "n", (0, -1)
        runner_for(state).execute_action(figure)
        self.assertEqual(figure.position, (0, -1))

    def test_a_hex_filled_since_it_was_chosen_falls_back_to_the_step(self):
        state = duel_state()
        figure = state.by_id(1)
        figure.chosen_letter, figure.chosen_destination = "n", (0, -1)
        state.combatants.append(make_combatant(3, q=0, r=-1, facing=0))
        events = []
        runner_for(state, events=events).execute_action(figure)
        self.assertEqual(figure.position, (-1, 0))
        self.assertIn("is not clear", events_of_type(events, "info")[0]["message"])

    def test_a_yielded_move_runs_to_its_hex_in_final_movement(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=9, r=0, facing=3),
            ],
        )
        figure = state.by_id(1)
        runner = runner_for(state)
        runner.phase_initial_movement([figure], choosing("a_yield", 2, (0, -5)))
        self.assertEqual(figure.position, (0, 0))
        events = []
        runner_for(state, events=events).phase_final_movement([figure])
        self.assertEqual(figure.position, (0, -5))
        moved = events_of_type(events, "movement")[0]["payload"]
        self.assertEqual((moved["gait"], moved["hexes"]), ("run", 5))
        self.assertEqual(moved["destination"], [0, -5])

    def test_a_path_stops_where_the_mover_is_engaged(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=4, r=0, facing=3),
            ],
        )
        figure, enemy = state.by_id(1), state.by_id(2)
        self.assertIn((3, 0), movement.legal_destinations(state, figure, "a"))
        # Behind the enemy is reached around its front, never through it.
        path = movement.path_to(state, figure, "a", (5, 0))
        self.assertIsNotNone(path)
        for cell in (path or [])[:-1]:
            self.assertNotIn(cell, enemy.front_hexes)

    def test_a_grappled_figure_names_its_escape_hex(self):
        state = duel_state()
        held = state.by_id(1)
        held.grappled_by, state.by_id(2).grappling = 2, 1
        decision = policy.choose_option(state, held)
        self.assertEqual(decision.chosen.letter, "v")
        self.assertIsNone(decision.chosen.destination)
        self.assertIn("toward (-1, 0)", decision.chosen.rationale)
        self.assertEqual(movement.placement(state, held, "j"), "")


class EngagedCastStandsStillTest(TestCase):
    """#34 — action-options.md: "| r | CAST SPELL | Shift/still | Attempt any
    spell |", and movement.md: figures "stop immediately when engaged". Only
    the disengaged f and h move at "Walk (slow)"."""

    def test_the_page_says_so(self):
        self.assertIn(
            "| r      | CAST SPELL     | Shift/still | Attempt any spell", OPTIONS_PAGE
        )
        self.assertIn(
            "| h      | CAST SPELL     | Walk (slow)  | Attempt any spell", OPTIONS_PAGE
        )

    def test_an_engaged_cast_takes_no_walk_slow_step(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.chosen_letter, caster.yielded = "r", True
        events = []
        runner_for(state, events=events).phase_final_movement([caster])
        self.assertEqual(caster.position, (0, 0))
        self.assertEqual(events_of_type(events, "movement"), [])

    def test_a_disengaged_cast_still_steps_back(self):
        state = BattleState(
            arena_radius=6,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=2, r=0, facing=3),
            ],
        )
        caster = state.by_id(1)
        caster.chosen_letter, caster.yielded = "h", True
        runner_for(state).phase_final_movement([caster])
        self.assertEqual(hexes.distance(caster.position, (2, 0)), 3)

    def test_an_engaged_cast_may_shift_to_a_chosen_hex(self):
        state = duel_state()
        caster = state.by_id(1)
        runner_for(state).phase_initial_movement(
            [caster], choosing("r", 2, (0, 1), spell_key="shield")
        )
        self.assertEqual(caster.position, (0, 1))


class ChargeReachTest(TestCase):
    """#17 — movement.md: CHARGE ATTACK moves at "Jog or less"; a figure
    barred from jogging charges at its walk, and the policy prices the
    reach the engine moves."""

    def test_a_barred_jog_charges_within_the_walk(self):
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=0, r=0, facing=0, move_jog=0, move_walk=4),
                make_combatant(2, q=5, r=0, facing=3),
            ],
        )
        figure = state.by_id(1)
        self.assertEqual(movement.gait_allowance(figure, "jog"), 4)
        decision = policy.choose_option(state, figure)
        charge = next(c for c in decision.candidates if c.letter == "b")
        self.assertGreater(charge.score, 0)
        self.assertNotIn("beyond", charge.rationale)
        figure.chosen_letter, figure.chosen_target = "b", 2
        runner_for(state).move_towards_target(figure)
        self.assertTrue(combat_math.figures_adjacent(figure, state.by_id(2)))


class HthAgreementByGameMasterTest(TestCase):
    """tarmar-studio #867 — hand-to-hand-and-grappling.md, "Entering
    Hand-to-Hand": "... or they simply agree". Spencer, 2026-10-09: "a Game
    Master marks a battle, or a pair, as 'HTH by agreement'; players alone
    still need the engine's entry condition"."""

    def test_the_page_says_so(self):
        self.assertIn("or they\nsimply agree", HTH_PAGE)

    def test_a_listed_pair_agrees_both_ways_and_no_one_else(self):
        state = duel_state()
        # Beside the first and facing it: no entry condition between them.
        state.combatants.append(make_combatant(3, q=0, r=1, facing=2))
        first, second, third = state.by_id(1), state.by_id(2), state.by_id(3)
        self.assertIsNone(combat_math.hth_entry_reason(state, first, second))
        first.hth_agreed_with = [2]  # one list filled: the pair still agrees
        for actor, target in ((first, second), (second, first)):
            self.assertEqual(
                combat_math.hth_entry_reason(state, actor, target),
                combat_math.GAME_MASTER_AGREEMENT,
            )
        self.assertEqual(
            combat_math.GAME_MASTER_AGREEMENT, "a Game Master rules they agree"
        )
        # The third figure, beside the first, is listed by no one.
        self.assertTrue(combat_math.figures_adjacent(first, third))
        self.assertIsNone(combat_math.hth_entry_reason(state, first, third))
        self.assertIsNone(combat_math.hth_entry_reason(state, third, first))

    def test_it_is_checked_after_the_agreements_read_off_the_board(self):
        state = duel_state()
        first, second = state.by_id(1), state.by_id(2)
        first.weapon = second.weapon = WeaponState(damage="1d6-2")
        first.hth_agreed_with = [2]
        self.assertEqual(
            combat_math.hth_entry_reason(state, first, second), "both bare-handed"
        )

    def test_the_menu_and_the_engine_take_it(self):
        state = duel_state()
        first, second = state.by_id(1), state.by_id(2)
        self.assertNotIn("o", letters(state, 1))
        first.hth_agreed_with, second.hth_agreed_with = [2], [1]
        self.assertIn("o", letters(state, 1))
        first.weapon = WeaponState(damage="1d6-2")
        first.chosen_target = 2
        events = []
        runner_for(state, [[10], [3]], events).hth_strike(first)
        self.assertEqual(first.hth_with, [2])
        self.assertIn(
            "closes and strikes", events_of_type(events, "action")[0]["message"]
        )

    def test_a_snapshot_writes_it_only_when_it_names_someone(self):
        state = duel_state()
        plain = state.to_dict()
        self.assertNotIn("hth_agreed_with", plain["combatants"][0])
        self.assertEqual(BattleState.from_dict(plain).by_id(1).hth_agreed_with, [])
        state.by_id(1).hth_agreed_with = [2]
        restored = BattleState.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(restored.by_id(1).hth_agreed_with, [2])
        self.assertNotIn("hth_agreed_with", state.to_dict()["combatants"][1])

    def test_a_battle_with_none_replays_unchanged(self):
        """Every turn played from a JSON snapshot, which drops the empty
        list: the recorded v0.9.4 duels replay event for event."""
        from tarmar_engine import engine

        from .replay_scenarios import SCENARIOS, comparable, fixture_path
        from .test_engine import SeededStubRoller
        from .test_replay import _comparable

        for seed in (1, 2, 4):
            with self.subTest(seed=seed):
                state = SCENARIOS["duel"]()
                roller = SeededStubRoller(seed)
                events: list[dict] = []
                recorded = json.loads(fixture_path("duel", seed).read_text())
                while len(events) < len(recorded):
                    state = BattleState.from_dict(
                        json.loads(json.dumps(state.to_dict()))
                    )
                    engine.run_turn(state, roller, events.append, policy.choose_option)
                replayed = [comparable(event) for event in events][: len(recorded)]
                self.assertEqual(
                    [_comparable(event) for event in replayed],
                    [_comparable(event) for event in recorded],
                )
