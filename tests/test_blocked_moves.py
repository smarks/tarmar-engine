"""A figure blocked short of its target (tarmar-engine #37).

Seen in tarmar-studio's ``manage.py simulate --seed 5 --beasts 2`` on
v0.9.10: a Mammoth (4 hexes) on the arena rim, 3 hexes from an archer, with
downed figures at (4, -7) and (2, -8), "jogs 9 hex(es)" every turn and ends
a hex from where it began, and its charge falls short every turn. The
geometry below is that board; the archer's hex, (2, -7), is the one of the
four hexes 3 from the Mammoth's footprint where the old walk circled.
"""

from collections.abc import Iterator
from functools import cache
from unittest import TestCase

from tarmar_engine import actions, combat_math, engine, hexes, movement, policy
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

    def test_the_jog_it_names_takes_an_before_eight(self):
        state = rim_board(hexes.neighbors(ARCHER_HEX))
        state.by_id(1).move_jog = 8
        self.assertIn(
            "an 8-hex jog along a clear path ends", candidate_for(state, "b").rationale
        )

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


def walled_board(*, ring: bool) -> BattleState:
    """A swordsman 4 hexes from an archer with downed figures on every hex
    beside him that is nearer the archer, or with ``ring`` on all six. Walled,
    the way round fits his 4-hex run but not his 1-hex jog; ringed, nothing
    gets him nearer."""
    swordsman = make_combatant(
        1, name="Dodger", q=0, r=0, move_walk=1, move_jog=1, move_run=4
    )
    archer = make_combatant(2, name="Archer", q=4, r=0, weapon=SMALL_BOW)
    cells = [
        cell
        for cell in hexes.neighbors(swordsman.position)
        if ring or hexes.distance(cell, archer.position) < 4
    ]
    downed = [
        make_combatant(10 + index, q=cell[0], r=cell[1], conscious=False)
        for index, cell in enumerate(cells)
    ]
    return BattleState(arena_radius=8, combatants=[swordsman, archer, *downed])


@cache
def approach_boards() -> tuple[tuple[str, BattleState], ...]:
    """Every board the replay scenarios pass through in their first turns,
    on three seeds, and the boards above: open ground, closing figures,
    engaged figures, a blocked Mammoth, a figure barred from jogging, a way
    round that a run fits and a jog does not, and a figure nothing gets
    nearer. Built once, when a test first asks; no test changes them."""
    from .replay_scenarios import SCENARIOS

    boards: list[tuple[str, BattleState]] = []
    for scenario, build in SCENARIOS.items():
        for seed in (1, 2, 3):
            state = build()
            roller = SeededStubRoller(seed)
            for turn in range(4):
                boards.append(
                    (
                        f"{scenario} seed {seed} turn {turn}",
                        state.from_dict(state.to_dict()),
                    )
                )
                engine.run_turn(state, roller, lambda event: None, policy.choose_option)
    boards.append(("rim, issue blockers", rim_board(ISSUE_BLOCKERS)))
    boards.append(("rim, open", rim_board([])))
    walker = rim_board(ISSUE_BLOCKERS)
    walker.by_id(1).move_jog = 0
    boards.append(("rim, barred from jogging", walker))
    boards.append(
        ("walled: a run gets round, a jog does not", walled_board(ring=False))
    )
    boards.append(("ringed: nothing gets nearer", walled_board(ring=True)))
    return tuple(boards)


#: Every key that makes a target-derived move, its own and yielded.
MOVING_KEYS = (
    *movement.MOVEMENT_GAITS,
    *(actions.yield_key(key) for key in movement.MOVEMENT_GAITS),
)


class DefaultApproachTest(TestCase):
    """The public option-to-route derivation (tarmar-engine #50): the
    derivation the engine and the policy used to restate, the walk the engine
    makes, and the menu's charge and dodge, across every board above."""

    def movers(
        self,
    ) -> Iterator[tuple[str, BattleState, CombatantState, CombatantState]]:
        """Each board, each active figure on it, and each enemy of it."""
        for label, state in approach_boards():
            for mover in state.combatants:
                if not mover.active:
                    continue
                for target in state.enemies_of(mover):
                    yield label, state, mover, target

    def test_the_gait_each_option_moves_at(self):
        self.assertEqual(
            {key: movement.option_gait(key) for key in MOVING_KEYS},
            {
                "a": "run",
                "sprint": "sprint",
                "b": "jog",
                "c": "jog",
                "a_yield": "run",
                "sprint_yield": "sprint",
                "b_yield": "jog",
                "c_yield": "jog",
            },
        )
        for key in ("f", "d", "n", ""):
            with self.subTest(key=key), self.assertRaises(ValueError):
                movement.option_gait(key)

    def test_it_is_the_route_at_the_options_gait_allowance(self):
        # The derivation as move_towards_target and the policy wrote it
        # before #50: option key to gait to allowance to route.
        for label, state, mover, target in self.movers():
            for key in MOVING_KEYS:
                gait = movement.MOVEMENT_GAITS[actions.base_option(key)]
                allowance = movement.gait_allowance(mover, gait)
                with self.subTest(board=label, mover=mover.name, key=key):
                    self.assertEqual(movement.option_allowance(mover, key), allowance)
                    self.assertEqual(
                        movement.default_approach(state, mover, key, target),
                        movement.approach_route(state, mover, target, allowance),
                    )

    def test_the_engine_walks_it(self):
        walked = 0
        for label, state, mover, target in self.movers():
            for key in movement.MOVEMENT_GAITS:
                approach = movement.default_approach(state, mover, key, target)
                trial = state.from_dict(state.to_dict())
                moving = trial.by_id(mover.combatant_id)
                moving.chosen_letter = key
                moving.chosen_target = target.combatant_id
                events: list[dict] = []
                runner_for(trial, events=events).move_towards_target(moving)
                end = approach.route[-1][0] if approach.route else mover.position
                with self.subTest(board=label, mover=mover.name, key=key):
                    self.assertEqual(moving.position, end)
                    moves = events_of_type(events, "movement")
                    self.assertEqual(len(moves), 1 if approach.route else 0)
                    if moves:
                        self.assertEqual(
                            moves[0]["payload"]["gait"], movement.option_gait(key)
                        )
                walked += bool(approach.route)
        self.assertTrue(walked)

    def test_the_menu_charges_the_first_enemy_it_reaches(self):
        reached = short = 0
        for label, state in approach_boards():
            for mover in state.combatants:
                if not mover.active:
                    continue
                charge = next(
                    (
                        candidate
                        for candidate in policy.choose_option(state, mover).candidates
                        if candidate.letter == "b"
                    ),
                    None,
                )
                if charge is None:
                    continue
                ranked = policy.enemies_by_distance(state, mover)
                nearest = ranked[0]
                if combat_math.figure_distance(mover, nearest) <= movement.CHARGE_REACH:
                    expected = nearest
                else:
                    expected = next(
                        (
                            enemy
                            for enemy in ranked
                            if movement.charge_reaches(
                                movement.default_approach(state, mover, "b", enemy)
                            )
                        ),
                        None,
                    )
                with self.subTest(board=label, mover=mover.name):
                    falls_short = "out of charge reach" in charge.rationale
                    self.assertEqual(falls_short, expected is None)
                    self.assertEqual(
                        charge.target_id,
                        (nearest if expected is None else expected).combatant_id,
                    )
                reached += expected is not None
                short += expected is None
        self.assertTrue(reached)
        self.assertTrue(short)

    def test_the_menu_says_whether_a_dodge_moves(self):
        moving = standing = 0
        for label, state in approach_boards():
            for mover in state.combatants:
                if not mover.active:
                    continue
                dodge = next(
                    (
                        candidate
                        for candidate in policy.choose_option(state, mover).candidates
                        if candidate.letter == "c" and candidate.target_id is not None
                    ),
                    None,
                )
                if dodge is None:
                    continue
                assert isinstance(dodge.target_id, int)
                threat = state.by_id(dodge.target_id)
                nearer = movement.default_approach(
                    state, mover, "c", threat
                ).distance < combat_math.figure_distance(mover, threat)
                with self.subTest(board=label, mover=mover.name):
                    self.assertEqual("jogging toward" in dodge.rationale, nearer)
                    self.assertEqual("standing" in dodge.rationale, not nearer)
                moving += nearer
                standing += not nearer
        self.assertTrue(moving)
        self.assertTrue(standing)

    def test_the_menu_moves_on_the_first_enemy_a_run_gets_nearer_to(self):
        closed = blocked = 0
        for label, state in approach_boards():
            for mover in state.combatants:
                if not mover.active:
                    continue
                move = next(
                    (
                        candidate
                        for candidate in policy.choose_option(state, mover).candidates
                        if candidate.letter == "a" and candidate.target_id is not None
                    ),
                    None,
                )
                if move is None:
                    continue
                ranked = policy.enemies_by_distance(state, mover)
                now = combat_math.figure_distance(mover, ranked[0])
                nearable = next(
                    (
                        enemy
                        for enemy in ranked
                        if movement.default_approach(state, mover, "a", enemy).distance
                        < combat_math.figure_distance(mover, enemy)
                    ),
                    None,
                )
                with self.subTest(board=label, mover=mover.name):
                    if now <= 1:
                        self.assertEqual(move.target_id, ranked[0].combatant_id)
                        continue
                    no_path = "no clear path" in move.rationale
                    self.assertEqual(no_path, nearable is None)
                    self.assertEqual(
                        move.target_id,
                        (ranked[0] if nearable is None else nearable).combatant_id,
                    )
                closed += nearable is not None
                blocked += nearable is None
        self.assertTrue(closed)
        self.assertTrue(blocked)

    def test_a_charge_strikes_exactly_when_its_approach_reaches(self):
        struck = short = 0
        for label, state, mover, target in self.movers():
            if mover.combatant_id in target.disengaged_from:
                continue
            approach = movement.default_approach(state, mover, "b", target)
            trial = state.from_dict(state.to_dict())
            charger = trial.by_id(mover.combatant_id)
            charger.chosen_letter = "b"
            charger.chosen_target = target.combatant_id
            events: list[dict] = []
            runner = runner_for(trial, events=events)
            runner.move_towards_target(charger)
            runner.melee_attack(charger)
            fell_short = any(
                "charge fell short" in event["message"]
                for event in events_of_type(events, "info")
            )
            with self.subTest(board=label, mover=mover.name, target=target.name):
                self.assertEqual(fell_short, not movement.charge_reaches(approach))
            struck += not fell_short
            short += fell_short
        self.assertTrue(struck)
        self.assertTrue(short)

    def test_a_charge_reaches_from_beside_its_target_and_no_further(self):
        self.assertEqual(movement.CHARGE_REACH, 1)
        for distance, reaches in ((0, True), (1, True), (2, False)):
            with self.subTest(distance=distance):
                self.assertEqual(
                    movement.charge_reaches(
                        movement.Approach(route=(), distance=distance)
                    ),
                    reaches,
                )


def ringed_sprinter_board() -> BattleState:
    """The tarmar-studio #57 board: a sprinter ringed by six downed figures,
    an archer three hexes off."""
    sprinter = make_combatant(1, name="Sprinter", q=0, r=0, move_sprint=18)
    archer = make_combatant(2, name="Ulfa", q=3, r=0, weapon=SMALL_BOW)
    ring = [
        make_combatant(10 + index, q=cell[0], r=cell[1], conscious=False)
        for index, cell in enumerate(hexes.neighbors((0, 0)))
    ]
    return BattleState(arena_radius=8, combatants=[sprinter, archer, *ring])


def sprinter_beside_a_ringed_archer() -> BattleState:
    """A sprinter two hexes from an archer ringed by six downed figures, so
    no path gets it nearer, and a second enemy, Brin, on clear ground."""
    sprinter = make_combatant(1, name="Sprinter", q=-2, r=0, move_sprint=18)
    archer = make_combatant(2, name="Ulfa", q=0, r=0, weapon=SMALL_BOW)
    brin = make_combatant(3, name="Brin", q=-2, r=6)
    ring = [
        make_combatant(10 + index, q=cell[0], r=cell[1], conscious=False)
        for index, cell in enumerate(hexes.neighbors((0, 0)))
    ]
    return BattleState(arena_radius=8, combatants=[sprinter, archer, brin, *ring])


def candidate_for(state: BattleState, letter: str) -> policy.Candidate:
    """Figure 1's menu entry for ``letter``."""
    decision = policy.choose_option(state, state.by_id(1))
    return next(
        candidate for candidate in decision.candidates if candidate.letter == letter
    )


def sprint_candidate(state: BattleState) -> policy.Candidate:
    """Figure 1's SPRINT on the menu."""
    return candidate_for(state, "sprint")


def sprinter_with_one_gap_away_from_the_archer() -> BattleState:
    """A sprinter (run 2, sprint 18) ringed by five downed figures, the one gap
    at (0, -1) on the side away from Ulfa at (4, 0): too short a run to get
    round the ring nearer to her, a sprint long enough to."""
    sprinter = make_combatant(1, name="Sprinter", q=0, r=0, move_run=2, move_sprint=18)
    archer = make_combatant(2, name="Ulfa", q=4, r=0, weapon=SMALL_BOW)
    ring = [
        make_combatant(10 + index, q=cell[0], r=cell[1], conscious=False)
        for index, cell in enumerate(
            cell for cell in hexes.neighbors((0, 0)) if cell != (0, -1)
        )
    ]
    return BattleState(arena_radius=8, combatants=[sprinter, archer, *ring])


class BlockedSprintTest(TestCase):
    """SPRINT says what the engine will do and goes where MOVE goes
    (tarmar-engine #51)."""

    def test_the_boards(self):
        ringed = ringed_sprinter_board()
        self.assertEqual(
            combat_math.figure_distance(ringed.by_id(1), ringed.by_id(2)), 3
        )
        beside = sprinter_beside_a_ringed_archer()
        sprinter = beside.by_id(1)
        self.assertEqual(combat_math.figure_distance(sprinter, beside.by_id(2)), 2)
        self.assertEqual(combat_math.figure_distance(sprinter, beside.by_id(3)), 6)
        self.assertEqual(policy.nearest_enemy(beside, sprinter), beside.by_id(2))

    def test_a_ringed_sprinter_says_it_stands(self):
        sprint = sprint_candidate(ringed_sprinter_board())
        self.assertTrue(sprint.rationale.startswith("Stand where you are"))
        self.assertIn("no clear path within an 18-hex sprint", sprint.rationale)
        self.assertIn("no Fatigue is paid", sprint.rationale)
        self.assertNotIn("Sprint up to", sprint.rationale)
        self.assertEqual(sprint.score, 0.0)

    def test_and_the_engine_does_stand_and_pays_nothing(self):
        state = ringed_sprinter_board()
        sprinter = state.by_id(1)
        sprint = sprint_candidate(state)
        sprinter.chosen_letter = sprint.letter
        sprinter.chosen_target = sprint.target_id
        events: list[dict] = []
        runner_for(state, events=events).move_towards_target(sprinter)
        self.assertEqual(sprinter.position, (0, 0))
        self.assertEqual(sprinter.fatigue, sprinter.max_fatigue)
        self.assertEqual(events_of_type(events, "movement"), [])

    def test_it_sprints_at_the_enemy_move_closes_on(self):
        state = sprinter_beside_a_ringed_archer()
        sprint = sprint_candidate(state)
        move = next(
            candidate
            for candidate in policy.choose_option(state, state.by_id(1)).candidates
            if candidate.letter == "a"
        )
        self.assertEqual(move.target_id, 3)
        self.assertEqual(sprint.target_id, move.target_id)
        self.assertIn(
            "Sprint up to 18 hexes toward Brin (Ulfa, 2 hexes off, cannot be "
            f"neared) for {movement.SPRINT_FATIGUE_COST} Fatigue",
            sprint.rationale,
        )

    def test_and_the_engine_sprints_toward_that_enemy(self):
        state = sprinter_beside_a_ringed_archer()
        sprinter, brin = state.by_id(1), state.by_id(3)
        sprint = sprint_candidate(state)
        sprinter.chosen_letter = sprint.letter
        sprinter.chosen_target = sprint.target_id
        runner_for(state).move_towards_target(sprinter)
        self.assertEqual(combat_math.figure_distance(sprinter, brin), 1)
        self.assertEqual(
            sprinter.fatigue, sprinter.max_fatigue - engine.SPRINT_FATIGUE_COST
        )

    def test_an_adjacent_sprinter_says_it_stands_and_does(self):
        # Ulfa faces away, so the sprinter beside her is not engaged and may
        # still choose to sprint.
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, name="Sprinter", q=0, r=0, move_sprint=18),
                make_combatant(2, name="Ulfa", q=1, r=0, facing=0),
            ],
        )
        sprinter = state.by_id(1)
        sprint = sprint_candidate(state)
        self.assertIn("Stand where you are, already adjacent to Ulfa", sprint.rationale)
        sprinter.chosen_letter = sprint.letter
        sprinter.chosen_target = sprint.target_id
        runner_for(state).move_towards_target(sprinter)
        self.assertEqual(sprinter.position, (0, 0))
        self.assertEqual(sprinter.fatigue, sprinter.max_fatigue)

    def test_the_sprint_searches_at_its_own_gait(self):
        # A run is too short to get round the ring nearer to Ulfa; a sprint
        # is not, so SPRINT does not stand where MOVE would (the search is at
        # the sprint gait, not the run's).
        state = sprinter_with_one_gap_away_from_the_archer()
        self.assertIn(
            "no clear path within a 2-hex run gets nearer",
            candidate_for(state, "a").rationale,
        )
        sprint = sprint_candidate(state)
        self.assertIn(
            f"Sprint up to 18 hexes toward Ulfa for {movement.SPRINT_FATIGUE_COST} "
            "Fatigue",
            sprint.rationale,
        )
        sprinter = state.by_id(1)
        sprinter.chosen_letter = sprint.letter
        sprinter.chosen_target = sprint.target_id
        runner_for(state).move_towards_target(sprinter)
        self.assertEqual(sprinter.position, (4, -1))
        self.assertEqual(
            sprinter.fatigue, sprinter.max_fatigue - movement.SPRINT_FATIGUE_COST
        )

    def test_a_blocked_run_takes_an_before_eight(self):
        state = ringed_sprinter_board()
        state.by_id(1).move_run = 8
        self.assertIn(
            "no clear path within an 8-hex run gets nearer",
            candidate_for(state, "a").rationale,
        )

    def test_in_the_open_the_sprint_is_unchanged(self):
        state = BattleState(
            arena_radius=12,
            combatants=[
                make_combatant(1, name="Sprinter", q=-10, r=0, move_sprint=18),
                make_combatant(2, name="Ulfa", q=10, r=0),
            ],
        )
        self.assertEqual(
            sprint_candidate(state).rationale,
            "Sprint up to 18 hexes toward Ulfa for "
            f"{movement.SPRINT_FATIGUE_COST} Fatigue; the AI keeps to the run "
            "= 0.000",
        )
