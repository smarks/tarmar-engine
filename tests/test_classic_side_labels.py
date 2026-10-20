"""What the classic narrative calls a side (tarmar-engine #25).

A consumer may key a teamless figure's side by its own uid (tarmar-studio keys
it by a row id), and the narrative printed that key as a team's name: "Yaria
Hawkridge (743) advances", "The 743 hold the field". A figure carrying a
``SideLabel`` is named by it instead; a figure without one reads exactly as it
always has, which the first tests pin.
"""
# pyright: reportOptionalMemberAccess=false
# (figures are placed on the board before the duel; positions are not None)

from __future__ import annotations

import json

import pytest
from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic import persistence
from tarmar_engine.classic.arena import DEFAULT_LAYOUT as LAYOUT
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import BROADSWORD
from tarmar_engine.classic.figure import Figure, SideLabel, create_human
from tarmar_engine.classic.narrative import _name, narrate_victory
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.state import GameState

# A triple hit (1, 1, 1) of 6 + 6 broadsword damage: 36 hits, a sure kill.
KILLING_BLOW = (1, 1, 1, 6, 6)


def _duel_to_the_death(
    winner_side: str, loser_side: str
) -> tuple[GameState, Figure, Figure]:
    """Two adjacent broadswordsmen; the first strikes the second dead."""
    winner = create_human(
        "Yaria Hawkridge",
        12,
        12,
        winner_side,
        weapons=[BROADSWORD],
        ready_weapon=BROADSWORD,
    )
    loser = create_human(
        "Thorn", 12, 12, loser_side, weapons=[BROADSWORD], ready_weapon=BROADSWORD
    )
    winner.uid, loser.uid = "743", "744"
    winner.position = Hex(5, 5)
    loser.position = LAYOUT.neighbor(Hex(5, 5), 0)
    winner.facing = LAYOUT.direction_to(winner.position, loser.position)
    loser.facing = LAYOUT.direction_to(loser.position, winner.position)
    game = GameState(
        Arena(cols=9, rows=15), [winner, loser], dice=Dice(scripted=KILLING_BLOW)
    )
    return game, winner, loser


def _fight(game: GameState, winner: Figure, loser: Figure) -> list[str]:
    """The log lines the killing blow and the win write."""
    logged_before = len(game.log)
    winner.current_option = Option.ATTACK
    game.queue_attack(winner, loser)
    game.resolve_combat()
    return game.log[logged_before:]


# ---- without a label, nothing changes ----------------------------------------


def test_an_unlabelled_figure_reads_as_it_always_has() -> None:
    _game, winner, _loser = _duel_to_the_death("red", "blue")
    assert _name(winner) == "Yaria Hawkridge (red)"
    assert narrate_victory("red") == "🏆 The red hold the field — victory!"


def test_an_unlabelled_duel_logs_the_same_lines_as_before() -> None:
    # The lines as the engine wrote them before #25, read off v0.9.18.
    game, winner, loser = _duel_to_the_death("red", "blue")
    assert _fight(game, winner, loser) == [
        "Yaria Hawkridge (red) swings a Broadsword at Thorn (blue) — a crushing blow "
        "for 36! (needed 12 or less, rolled 3 — DX 12).",
        "Thorn (blue) falls, slain!",
        "🏆 The red hold the field — victory!",
    ]


# ---- a team's label ----------------------------------------------------------


def test_a_team_member_is_named_with_the_team() -> None:
    game, winner, loser = _duel_to_the_death("team:Red", "team:Blue")
    winner.side_label = SideLabel("Red", team=True)
    loser.side_label = SideLabel("Blue", team=True)
    assert _fight(game, winner, loser) == [
        "Yaria Hawkridge (Red) swings a Broadsword at Thorn (Blue) — a crushing blow "
        "for 36! (needed 12 or less, rolled 3 — DX 12).",
        "Thorn (Blue) falls, slain!",
        "🏆 The Red hold the field — victory!",
    ]


# ---- a side of one -----------------------------------------------------------


def test_a_teamless_figure_is_named_by_its_label_never_its_uid() -> None:
    game, winner, loser = _duel_to_the_death("743", "744")
    winner.side_label = SideLabel("Yaria Hawkridge", team=False)
    loser.side_label = SideLabel("Thorn", team=False)
    lines = _fight(game, winner, loser)
    assert lines == [
        "Yaria Hawkridge swings a Broadsword at Thorn — a crushing blow "
        "for 36! (needed 12 or less, rolled 3 — DX 12).",
        "Thorn falls, slain!",
        "🏆 Yaria Hawkridge holds the field — victory!",
    ]
    assert not [line for line in game.log if "743" in line or "744" in line]


def test_a_nameless_teamless_figure_reads_as_the_label_it_is_given() -> None:
    game, winner, loser = _duel_to_the_death("743", "744")
    winner.name = ""
    winner.side_label = SideLabel("Side 1", team=False)
    loser.side_label = SideLabel("Thorn", team=False)
    lines = _fight(game, winner, loser)
    assert lines[0].startswith("Side 1 swings a Broadsword at Thorn — ")
    assert lines[-1] == "🏆 Side 1 holds the field — victory!"


def test_a_side_label_needs_a_name() -> None:
    with pytest.raises(ValueError, match="needs a name"):
        SideLabel("  ", team=False)


def test_a_side_label_name_has_no_spaces_at_its_ends() -> None:
    with pytest.raises(ValueError, match="spaces at an end"):
        SideLabel(" Ada ", team=False)


# ---- every figure on a side carries the same label ---------------------------


def _team_of_two_against_one() -> tuple[GameState, Figure, Figure, Figure]:
    """Ada and Bo on team:Red; Bo kills Cy, the only figure of team:Blue."""
    game, bo, cy = _duel_to_the_death("team:Red", "team:Blue")
    ada = create_human("Ada", 12, 12, "team:Red")
    ada.uid = "745"
    ada.position = Hex(1, 1)
    game.figures.append(ada)
    cy.side_label = SideLabel("Blue", team=True)
    return game, ada, bo, cy


def test_a_side_labelled_on_only_some_figures_is_refused() -> None:
    # Only the second figure labelled: the label must not be taken from
    # whichever figure comes first (here, unlabelled Bo) or last (Ada).
    game, ada, bo, cy = _team_of_two_against_one()
    ada.side_label = SideLabel("Red", team=True)
    with pytest.raises(ValueError, match="'team:Red' carry different side labels"):
        _fight(game, bo, cy)


def test_a_side_with_two_different_labels_is_refused() -> None:
    game, ada, bo, cy = _team_of_two_against_one()
    ada.side_label = SideLabel("Red", team=True)
    bo.side_label = SideLabel("Crimson", team=True)
    with pytest.raises(ValueError, match="'team:Red' carry different side labels"):
        _fight(game, bo, cy)


def test_a_side_labelled_on_only_some_figures_is_refused_on_turn_one() -> None:
    game, ada, _bo, _cy = _team_of_two_against_one()
    ada.side_label = SideLabel("Red", team=True)
    with pytest.raises(ValueError, match="'team:Red' carry different side labels"):
        game.begin_selection()


def test_labelled_and_unlabelled_games_still_begin() -> None:
    unlabelled, _winner, _loser = _duel_to_the_death("red", "blue")
    unlabelled.begin_selection()
    labelled, ada, bo, _cy = _team_of_two_against_one()
    ada.side_label = bo.side_label = SideLabel("Red", team=True)
    labelled.begin_selection()
    assert unlabelled.initiative_order and labelled.initiative_order


def test_a_side_labelled_alike_wins_under_its_label() -> None:
    game, ada, bo, cy = _team_of_two_against_one()
    ada.side_label = bo.side_label = SideLabel("Red", team=True)
    assert game.side_label("team:Red") == SideLabel("Red", team=True)
    assert _fight(game, bo, cy)[-1] == "🏆 The Red hold the field — victory!"


# ---- a label survives a snapshot ---------------------------------------------


def test_a_side_label_survives_a_round_trip() -> None:
    game, winner, loser = _duel_to_the_death("743", "team:Blue")
    winner.side_label = SideLabel("Yaria Hawkridge", team=False)
    loser.side_label = SideLabel("Blue", team=True)
    restored = GameState.from_dict(json.loads(json.dumps(game.to_dict())))
    assert [figure.side_label for figure in restored.figures] == [
        SideLabel("Yaria Hawkridge", team=False),
        SideLabel("Blue", team=True),
    ]


def test_a_snapshot_from_before_side_labels_loads_unlabelled() -> None:
    game, winner, _loser = _duel_to_the_death("red", "blue")
    winner.side_label = SideLabel("Red", team=True)
    payload = persistence.figure_to_json(winner)
    del payload["side_label"]
    assert persistence.figure_from_json(payload).side_label is None
