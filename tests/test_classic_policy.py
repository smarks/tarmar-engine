"""The classic decision layer: one menu shape, and tactics that survive the split.

tarmar-engine#3. The point of :mod:`tarmar_engine.classic.policy` is that a
classic turn can be *shown* before it is *taken* — so these pin both halves:
the menu never drifts from the engine's own legality, and driving a whole turn
through the menu lands the same board as melee's fused ``take_action`` did.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (figures are placed then used, as in the ported classic suites)
from __future__ import annotations

from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic import ai, policy
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import BROADSWORD, DAGGER, NO_ARMOR
from tarmar_engine.classic.figure import Posture, create_human
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.profile import CLASSIC_MELEE
from tarmar_engine.classic.state import GameState


def _fighter(name: str, side: str, weapon=BROADSWORD, **gear):
    return create_human(name, 12, 12, side, weapons=[weapon, DAGGER],
                        ready_weapon=weapon, armor=NO_ARMOR, **gear)


def _duel(*, gap: int = 3, seed: int = 1) -> tuple[GameState, object, object]:
    """Red and Blue ``gap`` hexes apart on a small field, facing each other."""
    arena = Arena(cols=13, rows=13)
    layout = arena.layout
    red, blue = _fighter("Red", "red"), _fighter("Blue", "blue")
    blue.position, blue.facing = Hex(6, 6), 0
    red.position = blue.position
    for _ in range(gap):
        red.position = layout.neighbor(red.position, 0)
    red.facing = 3
    return GameState(arena, [red, blue], dice=Dice(seed=seed)), red, blue


class TestMenu:
    """The menu is the engine's legality, not a second opinion about it."""

    def test_menu_is_exactly_the_legal_options(self) -> None:
        game, red, _blue = _duel()
        letters = [candidate.letter for candidate in policy.menu(game, red)]
        assert letters == [option.value for option in game.legal_options(red)]

    def test_menu_names_come_from_the_shared_catalog(self) -> None:
        game, red, _blue = _duel()
        move = next(candidate for candidate in policy.menu(game, red)
                    if candidate.letter == Option.MOVE.value)
        assert move.name and move.name != Option.MOVE.value

    def test_menu_carries_the_focus_fire_target(self) -> None:
        game, red, blue = _duel()
        assert all(candidate.target_id == blue.uid
                   for candidate in policy.menu(game, red))

    def test_an_unavailable_option_is_absent_with_its_reason_left_to_the_engine(
        self,
    ) -> None:
        game, red, _blue = _duel()
        red.posture = Posture.PRONE
        letters = [candidate.letter for candidate in policy.menu(game, red)]
        assert Option.STAND_UP.value in letters
        assert Option.CHARGE_ATTACK.value not in letters


class TestChooseOption:
    """The pick is the ported tactics; the rest of the menu comes back at zero."""

    def test_chosen_is_the_plan_the_heuristics_intend(self) -> None:
        game, red, _blue = _duel()
        intent = ai.plan(game, red)
        decision = policy.choose_option(game, red)
        assert decision.chosen.letter == intent.option.value
        assert decision.chosen.score == policy.CHOSEN_SCORE

    def test_alternatives_are_listed_at_zero_rather_than_invented_scores(self) -> None:
        game, red, _blue = _duel()
        decision = policy.choose_option(game, red)
        others = [candidate for candidate in decision.candidates
                  if candidate.letter != decision.chosen.letter]
        assert others
        assert all(candidate.score == 0.0 for candidate in others)
        assert all(candidate.rationale == policy.NOT_CHOSEN for candidate in others)

    def test_the_chosen_option_appears_once_in_the_menu(self) -> None:
        game, red, _blue = _duel()
        decision = policy.choose_option(game, red)
        matching = [candidate for candidate in decision.candidates
                    if candidate.letter == decision.chosen.letter]
        assert len(matching) == 1

    def test_choosing_is_pure_so_a_replayed_menu_matches(self) -> None:
        game, red, _blue = _duel()
        first = policy.choose_option(game, red)
        second = policy.choose_option(game, red)
        assert ([candidate.to_payload() for candidate in first.candidates]
                == [candidate.to_payload() for candidate in second.candidates])
        assert red.current_option is None      # nothing was committed

    def test_a_classic_uid_survives_the_shared_candidate_payload(self) -> None:
        game, red, blue = _duel()
        payload = policy.choose_option(game, red).chosen.to_payload()
        assert payload["target_id"] == blue.uid
        assert isinstance(payload["target_id"], str)


class TestEnact:
    """A chosen candidate becomes a moved figure — whoever chose it."""

    def test_enacting_the_ai_pick_matches_take_action(self) -> None:
        driven, driven_red, _ = _duel()
        ai.take_action(driven, driven_red)

        chosen, chosen_red, _ = _duel()
        policy.enact(chosen, chosen_red,
                     policy.choose_option(chosen, chosen_red).chosen)

        assert chosen_red.current_option == driven_red.current_option
        assert chosen_red.position == driven_red.position
        assert chosen_red.facing == driven_red.facing

    def test_a_player_may_take_an_option_the_ai_did_not(self) -> None:
        game, red, _blue = _duel()
        decision = policy.choose_option(game, red)
        other = next(candidate for candidate in decision.candidates
                     if candidate.letter not in (decision.chosen.letter,
                                                 Option.PASS.value))
        policy.enact(game, red, other)
        assert red.current_option.value == other.letter

    def test_a_movement_option_closes_toward_the_named_foe(self) -> None:
        game, red, blue = _duel(gap=4)
        before = game.arena.distance(red.position, blue.position)
        half = next(candidate for candidate in policy.menu(game, red)
                    if candidate.letter == Option.HALF_MOVE.value)
        policy.enact(game, red, half)
        assert game.arena.distance(red.position, blue.position) < before

    def test_pass_defers_rather_than_committing_an_option(self) -> None:
        game, red, _blue = _duel()
        deferral = next(candidate for candidate in policy.menu(game, red)
                        if candidate.letter == Option.PASS.value)
        game.begin_selection()
        active = game.active_character()
        policy.enact(game, active, deferral)
        assert active.uid in game.passed
        assert active.current_option is None

    def test_a_weapon_option_takes_up_the_best_spare(self) -> None:
        game, red, _blue = _duel()
        ready = next(candidate for candidate in policy.menu(game, red)
                     if candidate.letter == Option.READY_WEAPON.value)
        policy.enact(game, red, ready)
        assert red.ready_weapon is DAGGER      # the only spare carried


class TestDeclareAttacks:
    """Attacks are declared after everyone has moved, and only once."""

    def test_a_figure_that_chose_a_strike_gets_its_attack_queued(self) -> None:
        game, red, blue = _duel(gap=1)
        game.begin_selection()
        while (figure := game.active_character()) is not None:
            policy.enact(game, figure, policy.choose_option(game, figure).chosen)
        assert not game._pending
        policy.declare_attacks(game)
        assert any(pending.attacker is red or pending.attacker is blue
                   for pending in game._pending)

    def test_an_already_queued_attack_is_left_alone(self) -> None:
        game, red, blue = _duel(gap=1)
        game.begin_selection()
        active = game.active_character()
        foe = blue if active is red else red
        active.current_option = Option.SHIFT_ATTACK
        game.queue_attack(active, foe)
        queued = len(game._pending)
        policy.declare_attacks(game)           # would raise if it re-declared
        assert len([pending for pending in game._pending
                    if pending.attacker is active]) == queued


class TestRunnerDrivenByTheMenu:
    """The whole point: a turn driven off the menu is the turn melee played."""

    def _turn(self, *, menu_driven: bool) -> GameState:
        game, _red, _blue = _duel(gap=1, seed=7)

        def by_menu(state, figure):
            return policy.choose_option(state, figure).chosen

        def by_verbs(state, figure):
            ai.take_action(state, figure)
            ai.queue_attack_for(state, figure)
            return None

        CLASSIC_MELEE.run_turn(
            game, Dice(seed=7), lambda _line: None,
            by_menu if menu_driven else by_verbs)
        return game

    def test_menu_driven_turn_matches_the_direct_ai_turn(self) -> None:
        by_menu = self._turn(menu_driven=True)
        by_verbs = self._turn(menu_driven=False)
        assert ([(figure.name, figure.position, figure.facing, figure.damage_taken)
                 for figure in by_menu.figures]
                == [(figure.name, figure.position, figure.facing,
                     figure.damage_taken)
                    for figure in by_verbs.figures])

    def test_the_turn_logs_and_settles(self) -> None:
        lines: list[str] = []
        game, _red, _blue = _duel(gap=1, seed=7)
        CLASSIC_MELEE.run_turn(
            game, Dice(seed=7), lines.append,
            lambda state, figure: policy.choose_option(state, figure).chosen)
        assert lines
        assert game.turn_number == 2
