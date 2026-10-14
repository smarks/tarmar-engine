"""A figure blocked short of its target (tarmar-engine #37).

Seen in tarmar-studio's ``manage.py simulate --seed 5 --beasts 2`` on
v0.9.10: a Mammoth (4 hexes) on the arena rim, 3 hexes from an archer, with
downed figures at (4, -7) and (2, -8), "jogs 9 hex(es)" every turn and ends
a hex from where it began, and its charge falls short every turn. The
geometry below is that board; the archer's hex, (2, -7), is the one of the
four hexes 3 from the Mammoth's footprint where the old walk circled.
"""

from unittest import TestCase

from tarmar_engine import combat_math, engine, hexes, movement, policy
from tarmar_engine.state import BattleState, CombatantState

from .test_battle_rules_pass import SMALL_BOW, runner_for
from .test_engine import SeededStubRoller, events_of_type, make_beast_combatant
from .test_state import make_combatant

ARCHER_HEX = (2, -7)
#: The downed figures the issue names.
ISSUE_BLOCKERS = [(4, -7), (2, -8)]


def rim_board(
    blockers: list[tuple[int, int]],
    mammoth_hex: tuple[int, int] = (5, -8),
    others: tuple[CombatantState, ...] = (),
) -> BattleState:
    """The Mammoth on the rim facing the archer's way, the archer, and
    unconscious figures (still on the board, taking up hexes) at
    ``blockers``; ``others`` join the fight."""
    mammoth = make_beast_combatant(
        1,
        size_hexes=4,
        name="Mammoth",
        q=mammoth_hex[0],
        r=mammoth_hex[1],
        facing=3,
        move_jog=9,
        move_run=12,
        max_body=200,
        body=200,
    )
    archer = make_combatant(
        2, name="Ulfa", q=ARCHER_HEX[0], r=ARCHER_HEX[1], weapon=SMALL_BOW
    )
    downed = [
        make_combatant(10 + index, q=cell[0], r=cell[1], conscious=False)
        for index, cell in enumerate(blockers)
    ]
    return BattleState(arena_radius=8, combatants=[mammoth, archer, *downed, *others])


def charge(state: BattleState) -> list[dict]:
    """The Mammoth's CHARGE move toward the archer; the events it logs."""
    mammoth = state.by_id(1)
    mammoth.chosen_letter = "b"
    mammoth.chosen_target = 2
    events: list[dict] = []
    runner_for(state, events=events).move_towards_target(mammoth)
    return events


class BlockedMoveTest(TestCase):
    def test_the_issue_board_is_the_issue_board(self):
        state = rim_board(ISSUE_BLOCKERS)
        mammoth, archer = state.by_id(1), state.by_id(2)
        self.assertEqual(mammoth.position, (5, -8))
        self.assertEqual(combat_math.figure_distance(mammoth, archer), 3)
        self.assertEqual(hexes.distance((0, 0), mammoth.position), state.arena_radius)

    def test_the_route_never_revisits_a_hex(self):
        state = rim_board(ISSUE_BLOCKERS)
        mammoth, archer = state.by_id(1), state.by_id(2)
        route = movement.approach_route(state, mammoth, archer, 9).route
        anchors = [mammoth.position, *(cell for cell, _facing in route)]
        self.assertEqual(len(anchors), len(set(anchors)))

    def test_the_straight_walk_alone_goes_round_without_circling(self):
        """The line never steps back onto a hex it stood on, so on the
        issue's board it finds the way round by itself, before any path
        search; stepping back, it circled and got no nearer."""
        state = rim_board(ISSUE_BLOCKERS)
        mammoth, archer = state.by_id(1), state.by_id(2)
        line, _blocked = movement._line_towards(state, mammoth, archer, 9)
        anchors = [mammoth.position, *(cell for cell, _facing in line.route)]
        self.assertEqual(len(anchors), len(set(anchors)))
        self.assertEqual(line.distance, 1)

    def test_the_mammoth_goes_round_and_closes_in(self):
        state = rim_board(ISSUE_BLOCKERS)
        charge(state)
        self.assertEqual(combat_math.figure_distance(state.by_id(1), state.by_id(2)), 1)

    def test_a_figure_cupped_by_blockers_goes_round_them(self):
        """Every hex toward the target is taken, so the straight line cannot
        start; the way round begins with a step away from the target."""
        cup = [(1, -1), (1, 0), (0, 1)]
        fighter = make_combatant(1, q=0, r=0, facing=0)
        archer = make_combatant(2, name="Ulfa", q=4, r=0, weapon=SMALL_BOW)
        downed = [
            make_combatant(10 + index, q=cell[0], r=cell[1], conscious=False)
            for index, cell in enumerate(cup)
        ]
        state = BattleState(arena_radius=8, combatants=[fighter, archer, *downed])
        decision = policy.choose_option(state, fighter)
        self.assertEqual(decision.chosen.letter, "b")
        charge(state)
        self.assertEqual(combat_math.figure_distance(fighter, archer), 1)

    def test_a_boxed_in_target_is_closed_on_once_and_then_left(self):
        """Every hex beside the archer is taken: the Mammoth gets as near as
        it can, then stands, rather than stepping aside and back."""
        state = rim_board(hexes.neighbors(ARCHER_HEX))
        mammoth, archer = state.by_id(1), state.by_id(2)
        (move,) = events_of_type(charge(state), "movement")
        first = mammoth.position
        self.assertEqual(combat_math.figure_distance(mammoth, archer), 2)
        # One step straight in, not a walk round the archer that ends no
        # nearer than that step did.
        self.assertEqual(move["payload"]["hexes"], 1)
        self.assertEqual(move["payload"]["net_hexes"], 1)
        self.assertEqual(charge(state), [])
        self.assertEqual(mammoth.position, first)

    def test_the_movement_line_says_the_ground_covered(self):
        state = rim_board(ISSUE_BLOCKERS)
        start = state.by_id(1).position
        (line,) = events_of_type(charge(state), "movement")
        net = hexes.distance(start, state.by_id(1).position)
        self.assertEqual(
            line["message"],
            f"Mammoth jogs {line['payload']['hexes']} hex(es) toward Ulfa, "
            f"ending {net} hex(es) from its start",
        )
        self.assertEqual(line["payload"]["net_hexes"], net)
        self.assertLess(net, line["payload"]["hexes"])


class UnreachableChargeTest(TestCase):
    """The policy prices a charge's reach by the engine's own route."""

    def test_a_charge_that_cannot_reach_scores_nothing(self):
        state = rim_board(hexes.neighbors(ARCHER_HEX))
        decision = policy.choose_option(state, state.by_id(1))
        (charge_candidate,) = [
            candidate for candidate in decision.candidates if candidate.letter == "b"
        ]
        self.assertEqual(charge_candidate.score, 0.0)
        self.assertIn("out of charge reach", charge_candidate.rationale)

    def test_a_charge_that_can_go_round_is_still_priced(self):
        state = rim_board(ISSUE_BLOCKERS)
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "b")
        self.assertGreater(decision.chosen.score, 0.0)

    def test_an_unreachable_charge_is_not_chosen_turn_after_turn(self):
        state = rim_board(hexes.neighbors(ARCHER_HEX))
        events: list[dict] = []
        roller = SeededStubRoller(5)
        for _turn in range(6):
            engine.run_turn(state, roller, events.append, policy.choose_option)
        mammoth_events = [event for event in events if event["actor"] == "Mammoth"]
        chosen = [
            event["payload"]["chosen"]["letter"]
            for event in mammoth_events
            if event["event_type"] == "decision"
        ]
        self.assertEqual(len(chosen), 6)
        self.assertNotIn("b", chosen)
        # Once as near as it can get, moving gains nothing: it dodges.
        self.assertEqual(chosen[1:], ["c"] * 5)
        self.assertFalse(
            any("fell short" in event["message"] for event in mammoth_events)
        )


#: Where the Mammoth stands once it has got as near the boxed-in archer as
#: any path allows: 2 hexes off.
PARKED = (4, -8)


def parked_beside_a_boxed_archer(fighter_hex: tuple[int, int]) -> BattleState:
    """The boxed-in archer's board with the Mammoth parked, and a second
    enemy, Brin, on clear ground at ``fighter_hex``."""
    brin = make_combatant(3, name="Brin", q=fighter_hex[0], r=fighter_hex[1], facing=1)
    return rim_board(hexes.neighbors(ARCHER_HEX), mammoth_hex=PARKED, others=(brin,))


class AnotherEnemyWithinReachTest(TestCase):
    """A figure blocked from its nearest enemy goes for one a clear path gets
    it nearer to, rather than standing because the nearest is unreachable."""

    def test_the_board(self):
        state = parked_beside_a_boxed_archer((0, 3))
        mammoth = state.by_id(1)
        self.assertEqual(combat_math.figure_distance(mammoth, state.by_id(2)), 2)
        self.assertEqual(combat_math.figure_distance(mammoth, state.by_id(3)), 10)
        self.assertEqual(policy.nearest_enemy(state, mammoth), state.by_id(2))

    def test_it_closes_on_an_enemy_it_can_get_nearer_to(self):
        state = parked_beside_a_boxed_archer((0, 3))
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "a")
        self.assertEqual(decision.chosen.target_id, 3)
        self.assertGreater(decision.chosen.score, 0.0)
        self.assertIn("Ulfa, 2 hexes off, cannot be neared", decision.chosen.rationale)

    def test_it_charges_an_enemy_it_can_reach(self):
        state = parked_beside_a_boxed_archer((2, 2))
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "b")
        self.assertEqual(decision.chosen.target_id, 3)
        self.assertIn(
            "Ulfa is nearer but out of charge reach", decision.chosen.rationale
        )

    def test_the_engine_takes_it_toward_that_enemy(self):
        state = parked_beside_a_boxed_archer((0, 3))
        mammoth, brin = state.by_id(1), state.by_id(3)
        decision = policy.choose_option(state, mammoth)
        mammoth.chosen_letter = decision.chosen.letter
        mammoth.chosen_target = decision.chosen.target_id
        runner_for(state).move_towards_target(mammoth)
        self.assertLess(combat_math.figure_distance(mammoth, brin), 10)

    def test_a_dodge_that_cannot_move_says_it_stands(self):
        state = rim_board(hexes.neighbors(ARCHER_HEX), mammoth_hex=PARKED)
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "c")
        self.assertIn("standing", decision.chosen.rationale)
        self.assertNotIn("jogging toward", decision.chosen.rationale)
