"""The classic profile's decision layer — its menu, in the shared shape.

The two profiles disagreed about what a turn *decision* is, and that
disagreement is what kept a classic battle off tarmar-studio's UI even after
its state became storable (v0.6.0):

* the Tarmar profile's :func:`tarmar_engine.policy.choose_option` is **pure**
  and returns a :class:`~tarmar_engine.policy.Decision` — the scored candidate
  menu plus the pick. The studio draws the command bar from those candidates,
  logs every one with its rationale, and (since its remote-play work) *replays*
  the menu to validate a seated player's submitted choice;
* the classic profile's turn runner instead called a callable for its **side
  effect** — set the figure's option through the game's verbs, return nothing.
  No menu, no scores, nothing to show a player or replay a choice against.

This module supplies the missing half, in the *shared* ``Candidate``/
``Decision`` types so one payload shape serves both profiles and the app above
needs no per-profile branch. It splits what melee's ``take_action`` fused:

* :func:`menu` — what this figure may legally do, from the engine's own
  :meth:`~tarmar_engine.classic.state.GameState.option_availability` (already
  the single source of truth for legality);
* :func:`choose_option` — the menu scored and picked, via the ported
  heuristics in :mod:`.ai`;
* :func:`enact` — a chosen candidate carried out through the game's verbs,
  whoever chose it;
* :func:`declare_attacks` — the combat-phase declaration melee kept separate
  from selection, so an attack is still aimed *after* every figure has moved.

**Scores are thin here, and deliberately so.** melee's AI is a decision tree,
not a scorer: it has a reason for the branch it takes and no opinion at all
about the alternatives. Inventing numbers for the rest would be worse than
admitting that, so the chosen candidate carries the tactic that produced it
and the others are listed at zero with :data:`NOT_CHOSEN` — exactly what the
Tarmar policy already does for its fixed grapple defaults, so the decision log
still shows what else was on the table.

**A behavioural difference worth naming.** Enacting an option means the
*engine* derives the movement — where it goes and which way it ends up facing
— as the Tarmar engine already does. melee's own board instead lets a human
click the destination hex. Under the unification plan tarmar's UI is
canonical, so this is the intended direction, but a classic player will notice
it: choosing "charge attack" commits to the engine's closing path, not one
they picked.
"""

from __future__ import annotations

from ..policy import Candidate, Decision
from . import ai
from .figure import Figure
from .options import Option, spec
from .state import GameState

#: Rationale for a legal option the classic heuristics did not take. They are
#: listed so the decision log shows the whole menu, not just the pick.
NOT_CHOSEN = "not chosen by the classic policy"

#: The score every chosen candidate carries. The classic AI ranks nothing —
#: it commits to one branch — so this is a marker, not a measurement.
CHOSEN_SCORE = 1.0

#: Why the heuristics took each option, in the AI's own terms. Missing keys
#: fall back to the option's catalog name, which is never wrong, only terse.
TACTICS: dict[str, str] = {
    Option.STAND_UP: "down: get up before anything else",
    Option.CRAWL: "down and boxed in: crawl clear",
    Option.CHARGE_ATTACK: "close into contact and strike this turn",
    Option.ATTACK: "already in reach: stand and strike",
    Option.SHIFT_ATTACK: "a hex short: step in and strike",
    Option.SHIFT_DEFEND: "parry rather than trade blows",
    Option.MISSILE_ATTACK: "shoot, closing a hex while it looses",
    Option.ONE_LAST_SHOT: "engaged with a loaded bow: loose it",
    Option.MOVE: "close the distance at a run",
    Option.HALF_MOVE: "advance without committing to a blow",
    Option.DODGE: "close while dodging",
    Option.READY_WEAPON: "take up the best weapon carried",
    Option.CHANGE_WEAPONS: "swap to a weapon it can actually fight with",
    Option.PICK_UP: "a weapon lies in reach: take it up",
    Option.DISENGAGE: "break contact to re-arm next turn",
    Option.CAST: "cast rather than march",
    Option.HTH_ATTACK: "grapple",
    Option.DO_NOTHING: "nothing useful on offer: hold",
}


def _label(option: Option) -> str:
    """The option's human name from the shared catalog."""
    return spec(option).name


def _target_uid(game: GameState, figure: Figure) -> str:
    """The foe this figure's turn is about, or ``""`` when it has no enemy left."""
    target = ai._pick_target(game, figure)
    return target.uid if target is not None else ""


def menu(game: GameState, figure: Figure) -> list[Candidate]:
    """Every option ``figure`` may legally take now, unscored.

    Read straight off :meth:`GameState.option_availability` — the options it
    leaves untagged — so the menu and the engine's legality cannot drift. The
    order is the catalog's, which is stable, so a replayed menu matches the one
    a player was shown.
    """
    target_uid = _target_uid(game, figure)
    return [
        Candidate(
            letter=option.value,
            name=_label(option),
            score=0.0,
            rationale=NOT_CHOSEN,
            target_id=target_uid or None,
        )
        for option, reason in game.option_availability(figure)
        if reason is None
    ]


def choose_option(game: GameState, figure: Figure) -> Decision:
    """Score ``figure``'s legal options and choose one — the classic pick.

    Mirrors :func:`tarmar_engine.policy.choose_option`'s contract: pure,
    dice-free, and returning every candidate so the caller can log the whole
    deliberation. The chosen one is whatever :func:`tarmar_engine.classic.ai.plan`
    intends; the rest come back at zero with :data:`NOT_CHOSEN`.

    A figure with no turn to take (it cannot act, or the plan is ``None``)
    still gets a decision: the held no-op, so a selection pass always advances.
    """
    candidates = menu(game, figure)
    intent = ai.plan(game, figure)
    option = Option.DO_NOTHING if intent is None else intent.option
    target_uid = intent.target_uid if intent is not None else ""
    rationale = TACTICS.get(option, _label(option))
    chosen = Candidate(
        letter=option.value,
        name=_label(option),
        score=CHOSEN_SCORE,
        rationale=rationale,
        target_id=target_uid or None,
    )
    # The plan's option is legal by construction, so it is already in the menu:
    # replace that entry rather than appending a duplicate.
    candidates = [
        chosen if candidate.letter == chosen.letter else candidate
        for candidate in candidates
    ]
    if all(candidate.letter != chosen.letter for candidate in candidates):
        # DO NOTHING for a figure that cannot act at all: the menu is empty
        # because the engine offers it nothing, but the turn still has to move on.
        candidates.append(chosen)
    return Decision(chosen=chosen, candidates=candidates)


def _figure_by_uid(game: GameState, uid) -> Figure | None:
    """The figure with this uid, or ``None`` — including for ``None`` itself."""
    if not uid:
        return None
    return next((figure for figure in game.figures if figure.uid == uid), None)


def plan_for(game: GameState, figure: Figure, candidate: Candidate) -> ai.Plan:
    """The concrete move ``candidate`` means for ``figure``.

    The AI's own pick re-derives exactly (:func:`ai.plan` is deterministic), so
    the common path is the tactics verbatim. A *different* option — a human's
    pick off the same menu — is derived here instead: the engine closes toward
    the named foe under that option where the option moves, faces it where it
    does not, and takes up the best weapon on offer where the option is about
    weapons.

    That last derivation is a deliberate simplification: a ``Candidate`` names
    an option and a foe, never which sword to draw or which hex to end on. The
    studio's command bar offers options, so the engine settles the rest — see
    the module docstring's note on the difference from melee's board.
    """
    intent = ai.plan(game, figure)
    if intent is not None and intent.option.value == candidate.letter:
        return intent

    option = Option(candidate.letter)
    target = _figure_by_uid(game, candidate.target_id) or ai._pick_target(game, figure)
    if target is None:
        return ai.Plan(option)

    facing = ai._turn_in_place_facing(game, figure, target)
    option_spec = spec(option)
    if option in (Option.READY_WEAPON, Option.CHANGE_WEAPONS):
        spares = [weapon for weapon in figure.weapons
                  if weapon is not figure.ready_weapon]
        best = max(spares or figure.weapons, key=ai._weapon_power, default=None)
        return ai.Plan(option, facing=facing, target_uid=target.uid,
                       ready=best.name if best is not None else "")
    if option == Option.PICK_UP:
        dropped = game.dropped_in_reach(figure)
        best = max(dropped, key=ai._weapon_power, default=None)
        return ai.Plan(option, target_uid=target.uid,
                       ready=best.name if best is not None else "")
    if option_spec.movement_cap != "none":
        closing = ai._closing_move(game, figure, target, option)
        if closing is not None:
            destination, path = closing
            travel_facing = ai._travel_facing(
                game.arena.layout, figure, destination, target)
            return ai.Plan(option, path=list(path or []), target_uid=target.uid,
                           facing=travel_facing)
    return ai.Plan(option, facing=facing, target_uid=target.uid)


def enact(game: GameState, figure: Figure, candidate: Candidate) -> None:
    """Carry out ``candidate`` for ``figure`` through the game's own verbs.

    The one path from a chosen menu entry to a moved figure, whether the AI
    chose it or a player did. ``PASS`` defers instead of committing an option,
    so it goes through :meth:`GameState.pass_action` rather than ``move``.
    """
    if candidate.letter == Option.PASS.value:
        game.pass_action(figure)
        return
    ai.apply(game, figure, plan_for(game, figure, candidate))


def declare_attacks(game: GameState) -> None:
    """Queue the combat-phase attack of every figure that chose one.

    Kept apart from selection on purpose: melee declares attacks only once
    every figure has moved, so a blow is aimed at where its target actually
    stands. Figures that already have an attack pending (a driver that used the
    game's verbs directly) are left alone.
    """
    for figure in list(game.figures):
        if figure.can_act():
            ai.queue_attack_for(game, figure)
