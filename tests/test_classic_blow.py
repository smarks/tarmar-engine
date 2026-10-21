"""Whom a classic attack strikes, and how (tarmar-engine #54).

``ai.attack_blow`` is the choice ``ai.queue_attack_for`` declares, made
without declaring it; ``ai.planned_blow`` asks it after a plan, a CHARGE
ATTACK's say, played on a copy of the game. The studio's turn menu says from
them whether a charge strikes and whom: a pole jabs from two hexes, a
multi-hex foe is reached through any hex of its footprint, a charge that ends
beside a different foe strikes that one, and bare hands grapple. The 47
boards below hold each of those and a missile shot, and five turns of a
seeded 2-on-2 skirmish on three seeds.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (figures are placed then used, as in the ported classic suites)
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic import ai, policy
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    BROADSWORD,
    DAGGER,
    HALBERD,
    LEATHER,
    NO_ARMOR,
    SMALL_BOW,
)
from tarmar_engine.classic.figure import Figure, create_human
from tarmar_engine.classic.monsters import create_monster
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.profile import CLASSIC_MELEE
from tarmar_engine.classic.state import GameState, IllegalAction


def _fighter(name: str, side: str, weapon=BROADSWORD, armor=NO_ARMOR) -> Figure:
    """A fresh human fighter (ST and DX 24 between them), ST enough for the
    weapon (a halberd needs 13); with ``weapon`` ``None`` it carries a dagger
    but has nothing ready."""
    weapons = [weapon, DAGGER] if weapon is not None else [DAGGER]
    strength = max(12, weapon.min_strength if weapon is not None else 0)
    return create_human(name, strength, 24 - strength, side, weapons=weapons,
                        ready_weapon=weapon, armor=armor)


def _slower_foe(name: str = "Blue") -> Figure:
    """A foe in leather, slower than an unarmoured figure, so bare hands may
    begin a grapple on it (p.17)."""
    return _fighter(name, "blue", armor=LEATHER)


def _line_up(gap: int, red: Figure, blue: Figure, *others: Figure,
             cols: int = 13) -> GameState:
    """Blue at the middle facing 0, Red ``gap`` hexes along direction 0 facing
    back; ``others`` keep the positions they were given."""
    arena = Arena(cols=cols, rows=cols)
    layout = arena.layout
    middle = cols // 2
    blue.position, blue.facing = Hex(middle, middle), 0
    red.position = blue.position
    for _ in range(gap):
        red.position = layout.neighbor(red.position, 0)
    red.facing = 3
    return GameState(arena, [red, blue, *others], dice=Dice(seed=1))


def _boards() -> list[tuple[str, GameState]]:
    boards: list[tuple[str, GameState]] = []
    for gap in range(1, 6):
        boards.append((f"swords {gap} apart",
                       _line_up(gap, _fighter("Red", "red"), _fighter("Blue", "blue"))))
        boards.append((f"halberd {gap} apart",
                       _line_up(gap, _fighter("Red", "red", HALBERD),
                                _fighter("Blue", "blue"))))
        boards.append((f"bare hands {gap} apart",
                       _line_up(gap, _fighter("Red", "red", None), _slower_foe())))
        boards.append((f"bow {gap} apart",
                       _line_up(gap, _fighter("Red", "red", SMALL_BOW),
                                _fighter("Blue", "blue"))))
        giant = create_monster("Giant", "Grond", "blue")
        boards.append((f"giant {gap} apart",
                       _line_up(gap, _fighter("Red", "red"), giant)))
        # A weaker foe off to one side of the line the charge runs along.
        weak = _fighter("Weak", "blue")
        weak.damage_taken = 6
        game = _line_up(gap, _fighter("Red", "red"), _fighter("Blue", "blue"), weak)
        layout = game.arena.layout
        weak.position = layout.neighbor(layout.neighbor(game.figures[1].position, 1), 0)
        weak.facing = 3
        boards.append((f"a weaker foe beside the line, {gap} apart", game))
    # Seven hexes: a charge's half MA brings a sword short and a halberd two
    # hexes off, in reach of its jab.
    boards.append(("swords 7 apart", _line_up(7, _fighter("Red", "red"),
                                              _fighter("Blue", "blue"), cols=17)))
    boards.append(("halberd 7 apart", _line_up(7, _fighter("Red", "red", HALBERD),
                                               _fighter("Blue", "blue"), cols=17)))
    for seed in (1, 2, 3):
        red_one = _fighter("Red One", "red")
        red_two = _fighter("Red Two", "red", HALBERD)
        blue_one = _fighter("Blue One", "blue", SMALL_BOW)
        blue_two = create_monster("Giant", "Grond", "blue")
        game = _line_up(4, red_one, blue_one, red_two, blue_two, cols=15)
        layout = game.arena.layout
        red_two.position, red_two.facing = layout.neighbor(red_one.position, 2), 3
        blue_two.position, blue_two.facing = (
            layout.neighbor(layout.neighbor(blue_one.position, 4), 4), 0)
        for turn in range(5):
            boards.append((f"skirmish seed {seed} turn {turn}",
                           GameState.from_dict(game.to_dict())))
            CLASSIC_MELEE.run_turn(game, Dice(seed=seed), lambda _line: None,
                                   lambda state, figure:
                                   policy.choose_option(state, figure).chosen)
    return boards


BOARDS = _boards()


def _figure(game: GameState, uid: str) -> Figure:
    return next(each for each in game.figures if each.uid == uid)


def _declared(game: GameState, figure: Figure, plan: ai.Plan) -> tuple | None:
    """What ``queue_attack_for`` declares after the plan, on a copy, its
    declaring verbs recorded rather than run: (target uid, kind) or None."""
    copy = GameState.from_dict(game.to_dict())
    mover = _figure(copy, figure.uid)
    ai.apply(copy, mover, plan)
    calls: list[tuple] = []
    copy.hth_attack = lambda attacker, defender: calls.append(("hth", defender.uid))
    copy.queue_attack = lambda attacker, target, **_: calls.append(
        ("queue", target.uid))
    copy.aim = lambda attacker, target: calls.append(("aim", target.uid))
    copy.queue_spell = lambda *_args, **_kwargs: calls.append(("spell",))
    copy.stand_down = lambda _figure: calls.append(("stand_down",))
    ai.queue_attack_for(copy, mover)
    verbs = [call[0] for call in calls]
    if verbs == ["hth"]:
        return calls[0][1], ai.BlowKind.HAND_TO_HAND
    if verbs == ["aim", "queue"]:
        return calls[1][1], ai.BlowKind.RANGED
    if verbs == ["queue"]:
        return calls[0][1], ai.BlowKind.MELEE
    assert verbs in ([], ["spell"], ["stand_down"]), verbs
    return None


def _cases():
    """Every option on every figure's menu; a CHARGE ATTACK at every foe, as
    a player may aim it, as well as at the AI's focus-fire target."""
    for label, game in BOARDS:
        for figure in game.figures:
            if not figure.can_act() or figure.position is None:
                continue
            for candidate in policy.menu(game, figure):
                yield label, game, figure, candidate
                if candidate.letter != Option.CHARGE_ATTACK.value:
                    continue
                for foe in game.figures:
                    if foe.side != figure.side and foe.uid != candidate.target_id:
                        yield label, game, figure, replace(candidate, target_id=foe.uid)


class TestPlannedBlow:
    """Pins the call site: ``queue_attack_for`` declares from ``attack_blow``,
    so this guards its use of it; the choice itself is pinned by value below.
    Of the 875 menu cases on the boards, 602 are compared; the other 273 are
    plans the engine refuses on that board (a PASS, or a figure whose side is
    not moving), and the test asserts both paths refuse them."""

    def test_it_is_what_the_attack_declaration_strikes(self) -> None:
        seen: Counter = Counter()
        for label, game, figure, candidate in _cases():
            plan = policy.plan_for(game, figure, candidate)
            try:
                expected = _declared(game, figure, plan)
            except IllegalAction:
                # A plan the engine refuses here is refused by planned_blow too.
                try:
                    ai.planned_blow(game, figure, plan)
                except IllegalAction:
                    continue
                raise AssertionError(f"{label}: {candidate.letter} ran") from None
            blow = ai.planned_blow(game, figure, plan)
            answer = None if blow is None else (blow.target.uid, blow.kind)
            assert answer == expected, (label, figure.name, candidate.letter)
            seen[None if blow is None else blow.kind] += 1
            if blow is not None and candidate.letter == Option.CHARGE_ATTACK.value:
                seen["charge strikes"] += 1
                if blow.target.uid != candidate.target_id:
                    seen["charge strikes another foe"] += 1
                if blow.target.size > 1:
                    seen["charge strikes a multi-hex foe"] += 1
                rehearsal = GameState.from_dict(game.to_dict())
                mover = _figure(rehearsal, figure.uid)
                ai.apply(rehearsal, mover, plan)
                jab = rehearsal.arena.distance(mover.position, blow.target.position)
                if blow.target.size == 1 and jab == 2:
                    seen["charge jabs from two hexes"] += 1
            if blow is None and candidate.letter == Option.CHARGE_ATTACK.value:
                seen["charge strikes nobody"] += 1
        # Not vacuous: every kind and every case the issue names occurs.
        for kind in (None, *ai.BlowKind):
            assert seen[kind], kind
        for case in ("charge strikes", "charge strikes another foe",
                     "charge strikes a multi-hex foe", "charge jabs from two hexes",
                     "charge strikes nobody"):
            assert seen[case], (case, seen)

    def test_it_leaves_the_live_game_alone_and_names_its_figures(self) -> None:
        for label, game, figure, candidate in _cases():
            plan = policy.plan_for(game, figure, candidate)
            before = game.to_dict()
            try:
                blow = ai.planned_blow(game, figure, plan)
            except IllegalAction:
                continue
            assert game.to_dict() == before, label
            if blow is not None:
                assert any(blow.target is each for each in game.figures), label


def _board(label: str) -> GameState:
    return next(GameState.from_dict(game.to_dict()) for name, game in BOARDS
                if name == label)


def _charge_at(game: GameState, figure: Figure, foe: Figure) -> ai.Plan:
    """The plan for ``figure``'s CHARGE ATTACK aimed at ``foe``."""
    charge = next(candidate for candidate in policy.menu(game, figure)
                  if candidate.letter == Option.CHARGE_ATTACK.value)
    return policy.plan_for(game, figure, replace(charge, target_id=foe.uid))


class TestPlannedBlowValues:
    """The cases the issue names, by expected value."""

    def test_a_charge_strikes_the_weaker_foe_it_ends_beside(self) -> None:
        game = _board("a weaker foe beside the line, 3 apart")
        red, blue, weak = game.figures
        blow = ai.planned_blow(game, red, _charge_at(game, red, blue))
        assert blow == ai.Blow(weak, ai.BlowKind.MELEE)

    def test_a_charge_that_falls_short_strikes_nobody(self) -> None:
        game = _board("swords 7 apart")
        red, blue = game.figures
        plan = _charge_at(game, red, blue)
        assert game.arena.distance(plan.path[-1], blue.position) == 2
        assert ai.planned_blow(game, red, plan) is None

    def test_a_halberd_charge_jabs_from_two_hexes(self) -> None:
        game = _board("halberd 7 apart")
        red, blue = game.figures
        plan = _charge_at(game, red, blue)
        assert game.arena.distance(plan.path[-1], blue.position) == 2
        assert ai.planned_blow(game, red, plan) == ai.Blow(blue, ai.BlowKind.MELEE)

    def test_a_bare_handed_charge_grapples(self) -> None:
        game = _board("bare hands 2 apart")
        red, blue = game.figures
        blow = ai.planned_blow(game, red, _charge_at(game, red, blue))
        assert blow == ai.Blow(blue, ai.BlowKind.HAND_TO_HAND)


class TestAttackBlow:
    """The declaration's choice, branch by branch, by expected value."""

    def test_it_is_pure(self) -> None:
        for label, game in BOARDS:
            before = game.to_dict()
            for figure in game.figures:
                ai.attack_blow(game, figure)
            assert game.to_dict() == before, label

    def test_a_bare_handed_charger_grapples(self) -> None:
        game = _line_up(1, _fighter("Red", "red", None), _slower_foe())
        red, blue = game.figures
        red.current_option = Option.CHARGE_ATTACK
        assert ai.attack_blow(game, red) == ai.Blow(blue, ai.BlowKind.HAND_TO_HAND)

    def test_a_halberd_jabs_from_two_hexes(self) -> None:
        game = _line_up(2, _fighter("Red", "red", HALBERD), _fighter("Blue", "blue"))
        red, blue = game.figures
        red.current_option = Option.CHARGE_ATTACK
        assert ai.attack_blow(game, red) == ai.Blow(blue, ai.BlowKind.MELEE)
        sword = _line_up(2, _fighter("Red", "red"), _fighter("Blue", "blue"))
        sword.figures[0].current_option = Option.CHARGE_ATTACK
        assert ai.attack_blow(sword, sword.figures[0]) is None

    def test_bare_hands_grapple_the_weaker_of_two_foes(self) -> None:
        weak = _slower_foe("Weak")
        game = _line_up(1, _fighter("Red", "red", None), _slower_foe(), weak)
        red, blue, weak = game.figures
        weak.damage_taken = 6
        weak.position = game.arena.layout.neighbor(red.position, 2)
        red.current_option = Option.CHARGE_ATTACK
        assert {foe.uid for foe in game.attack_candidates(red).hth} == {
            blue.uid, weak.uid}
        assert ai.attack_blow(game, red) == ai.Blow(weak, ai.BlowKind.HAND_TO_HAND)

    def test_a_grappler_passes_over_a_foe_that_cannot_act(self) -> None:
        game = _line_up(1, _fighter("Red", "red"), _fighter("Blue", "blue"),
                        _fighter("Weak", "blue"))
        red, blue, weak = game.figures
        weak.position = blue.position
        weak.damage_taken = weak.strength      # weakest, and down
        red.hth_opponents = [weak.uid, blue.uid]
        assert not weak.can_act()
        assert ai.attack_blow(game, red) == ai.Blow(blue, ai.BlowKind.HAND_TO_HAND)

    def test_a_dodging_bare_handed_figure_declares_nothing(self) -> None:
        game = _line_up(1, _fighter("Red", "red", None), _slower_foe())
        red = game.figures[0]
        red.current_option = Option.DODGE
        assert ai.attack_blow(game, red) is None

    def test_a_blade_strikes_the_weakest_foe_in_reach(self) -> None:
        game = _line_up(1, _fighter("Red", "red"), _fighter("Blue", "blue"),
                        _fighter("Weak", "blue"))
        red, blue, weak = game.figures
        weak.damage_taken = 6
        weak.position = game.arena.layout.neighbor(red.position, 2)  # front arc
        red.current_option = Option.ATTACK
        assert {foe.uid for foe in game.attack_candidates(red).melee} == {
            blue.uid, weak.uid}
        assert ai.attack_blow(game, red) == ai.Blow(weak, ai.BlowKind.MELEE)

    def test_a_multi_hex_foe_is_struck_through_its_footprint(self) -> None:
        game = _board("giant 1 apart")
        red, giant = game.figures
        red.current_option = Option.ATTACK
        assert ai.attack_blow(game, red) == ai.Blow(giant, ai.BlowKind.MELEE)

    def test_a_loaded_bow_shoots_and_a_reloading_one_does_not(self) -> None:
        game = _board("bow 4 apart")
        red, blue = game.figures
        red.current_option = Option.MISSILE_ATTACK
        assert ai.attack_blow(game, red) == ai.Blow(blue, ai.BlowKind.RANGED)
        red.missile_cooldown = 1
        assert ai.attack_blow(game, red) is None

    def test_a_bow_never_shoots_into_a_pile_holding_a_friend(self) -> None:
        friend = _fighter("Friend", "red")
        game = _line_up(4, _fighter("Red", "red", SMALL_BOW), _fighter("Blue", "blue"),
                        friend)
        red, blue, friend = game.figures
        friend.position = blue.position
        friend.hth_opponents, blue.hth_opponents = [blue.uid], [friend.uid]
        red.current_option = Option.MISSILE_ATTACK
        assert ai.attack_blow(game, red) is None

    def test_a_grappler_strikes_the_weakest_foe_it_holds(self) -> None:
        game = _line_up(1, _fighter("Red", "red"), _fighter("Blue", "blue"),
                        _fighter("Weak", "blue"))
        red, blue, weak = game.figures
        weak.damage_taken = 6
        weak.position = blue.position
        red.hth_opponents = [blue.uid, weak.uid]
        red.current_option = Option.CAST       # a grapple outranks a declared cast
        assert ai.attack_blow(game, red) == ai.Blow(weak, ai.BlowKind.HAND_TO_HAND)

    def test_nothing_for_a_figure_that_will_not_attack(self) -> None:
        # Bare hands grapple whatever the option but a defence or a breakaway
        # (melee #275), so MOVE is asked of the sword alone.
        for option, boards in (
            (Option.DISENGAGE, ("swords 1 apart", "bare hands 1 apart")),
            (Option.CAST, ("swords 1 apart", "bare hands 1 apart")),
            (Option.MOVE, ("swords 1 apart",)),
        ):
            for board in boards:
                game = _board(board)
                red = game.figures[0]
                red.current_option = option
                assert ai.attack_blow(game, red) is None, (option, board)
        game = _board("bare hands 1 apart")
        red, blue = game.figures
        red.current_option = Option.MOVE
        assert ai.attack_blow(game, red) == ai.Blow(blue, ai.BlowKind.HAND_TO_HAND)
        game = _board("swords 1 apart")
        red = game.figures[0]
        red.current_option = Option.ATTACK
        red.attacked_this_turn = True
        assert ai.attack_blow(game, red) is None
