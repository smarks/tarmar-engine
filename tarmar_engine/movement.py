"""Where a figure may move: gait allowances and placed destinations.

movement.md's Speed table gives each gait its hexes, and action-options.md's
Move column says which gait (or which one-hex shift) each option allows. Most
of the engine's movement is derived from a target: MOVE and CHARGE close on
the chosen enemy, DODGE jogs toward the missile threat, all along
:func:`approach_route`, which the AI also prices a charge's reach by. A few
moves are placed instead, at a hex the figure picks (tarmar-engine #20, from
tarmar-studio #819 and #866):

* **Shift** — the engaged options whose Move column reads "Shift/still"
  (j, k, m, o, r; s DISBELIEVE is not implemented) may move one hex during
  movement: special-combat-situations.md, "Shift 1 hex or stand still during
  movement". The figure stays engaged with every enemy it was engaged with:
  leaving an engagement is DISENGAGE's (n), with its strike from a slower
  enemy (coordinator's ruling under the standing rule; Spencer may
  overrule). A figure in hand-to-hand or in a grapple has no such move.
* **Step** — DISENGAGE (n), "Move 1 hex any direction instead of attack",
  and the hand-to-hand DISENGAGE (v), "stand, move to adjacent", take their
  hex in the Actions phase. A held figure's Struggle Free is v too.
* **Gait** — MOVE (a, at the Run; "sprint" at the Sprint), DODGE (c) and
  DROP (d) at "Jog or less", and the yielded MOVE and DODGE, may end at any
  hex the gait reaches along a clear path. A CHARGE goes at its target and
  takes no hex.

``Candidate.destination`` carries the hex; ``None`` keeps the derived move.
The engine checks a destination with :func:`refusal` when the option is
chosen and again when the move is made, and moves the figure only to a hex
this module allows. :func:`legal_destinations` is the same rule as a set, for
a game that lets a person pick the hex.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from . import actions, combat_math, hexes
from .state import BattleState, CombatantState

Hex = tuple[int, int]

# Gait by movement option: the gait each phase-3/4 mover moves at toward its
# target (movement.md's Speed table; action-options.md's Move column). DODGE
# is "Jog or less" and moves toward the missile threat it dodges (#819).
MOVEMENT_GAITS: dict[str, str] = {
    "a": "run",
    "sprint": "sprint",
    "b": "jog",
    "c": "jog",
}

#: Options whose move may end at a placed hex, and the gait that bounds it:
#: the target-derived movers less the CHARGE, plus DROP ("Jog or less").
PLACED_GAITS: dict[str, str] = {
    **{key: gait for key, gait in MOVEMENT_GAITS.items() if key != "b"},
    "d": "jog",
}

#: The engaged options whose Move column is "Shift/still" and that the engine
#: implements (action-options.md, Engaged Figures). n is a step, below.
SHIFT_OPTIONS: frozenset[str] = frozenset({"j", "k", "m", "o", "r"})

#: Options that move one hex in the Actions phase.
STEP_OPTIONS: frozenset[str] = frozenset({"n", "v"})


def gait_allowance(combatant: CombatantState, gait: str) -> int:
    """Hexes the figure may cover at ``gait`` (0 = a gait it may not use).

    movement.md: CHARGE ATTACK, DODGE and DROP move at "Jog or less", so a
    figure barred from jogging (a Heavy load) moves at its walk. The engine
    moves at this allowance and the policy prices a charge's reach by it
    (tarmar-engine #17), so the two read one rule.
    """
    if gait == "sprint":
        return combatant.move_sprint
    if gait == "run":
        return combatant.move_run
    if gait == "jog":
        return combatant.move_jog if combatant.move_jog > 0 else combatant.move_walk
    return combatant.move_walk


def footprint_clear(
    state: BattleState, combatant: CombatantState, anchor: Hex, facing: int
) -> bool:
    """Would the combatant's footprint fit at ``anchor`` facing ``facing``?"""
    occupied = state.occupied_hexes() - set(combatant.footprint)
    return all(
        cell not in occupied and hexes.in_arena(cell, state.arena_radius)
        for cell in hexes.footprint(anchor, facing, combatant.size_hexes)
    )


def step_away_hex(
    state: BattleState, combatant: CombatantState, threat: CombatantState
) -> Hex | None:
    """The nearest clear hex stepping ``combatant`` away from ``threat``.

    Straight back first, then either flank. This is the engine's own choice
    for a DISENGAGE or a Struggle Free that names no hex, and the hex the AI
    names for one.
    """
    away = hexes.direction_towards(threat.position, combatant.position)
    for direction in (away, (away + 1) % 6, (away - 1) % 6):
        destination = hexes.add(combatant.position, direction)
        if footprint_clear(state, combatant, destination, combatant.facing):
            return destination
    return None


def in_hand_to_hand(state: BattleState, combatant: CombatantState) -> bool:
    """Is the figure in hand-to-hand with an active enemy beside it?

    ``CombatantState.hth_with`` is pruned at the start of each turn; this
    reads it against the board as it stands, so a partner felled or moved
    since does not hold the figure in. A beast has no hands and keeps its
    own menu (hand-to-hand-and-grappling.md's table is "Bare hands or
    dagger").
    """
    if combatant.is_beast:
        return False
    for other_id in combatant.hth_with:
        other = state.by_id(other_id)
        if other.active and combat_math.figures_adjacent(combatant, other):
            return True
    return False


def _paths(
    state: BattleState, combatant: CombatantState, allowance: int
) -> dict[Hex, list[Hex]]:
    """Every hex the figure can reach within ``allowance`` steps, with a path.

    Breadth first over clear footprints, facing the way each step goes, as
    the engine moves a figure. A figure stops the moment it is engaged
    (movement.md), so a hex where it would be engaged ends a path: it can be
    reached but not passed through, and a figure engaged where it stands
    reaches nothing.
    """
    engagers = combat_math.engaging_fronts(state, combatant)
    occupied = state.occupied_hexes() - set(combatant.footprint)

    def fits(anchor: Hex, facing: int) -> tuple[Hex, ...] | None:
        body = hexes.footprint(anchor, facing, combatant.size_hexes)
        if any(
            cell in occupied or not hexes.in_arena(cell, state.arena_radius)
            for cell in body
        ):
            return None
        return body

    start = combatant.position
    if hexes.figure_engaged(combatant.footprint, combatant.size_hexes, engagers):
        return {}
    paths: dict[Hex, list[Hex]] = {start: []}
    frontier: deque[Hex] = deque([start])
    while frontier:
        here = frontier.popleft()
        if len(paths[here]) >= allowance:
            continue
        for direction in range(6):
            there = hexes.add(here, direction)
            if there in paths:
                continue
            body = fits(there, direction)
            if body is None:
                continue
            paths[there] = [*paths[here], there]
            if not hexes.figure_engaged(body, combatant.size_hexes, engagers):
                frontier.append(there)
    del paths[start]
    return paths


@dataclass(frozen=True)
class Approach:
    """Where a figure closing on a target goes this move.

    ``route`` is each anchor it steps onto with the facing it takes there,
    in order; ``distance`` is the figure distance to the target at the end,
    once the mover has turned to face it.
    """

    route: tuple[tuple[Hex, int], ...]
    distance: int


def _line_towards(
    state: BattleState,
    combatant: CombatantState,
    target: CombatantState,
    allowance: int,
) -> tuple[Approach, bool]:
    """The straight-line walk (:func:`hexes.step_towards`), cut at its closest.

    One step at a time, facing the target, stopping when adjacent, when
    engaged (movement.md) or when boxed in, and never stepping back onto a
    hex stood on this move. Sidesteps that end the walk no nearer than it
    already got are dropped: a figure that cannot gain ground stands rather
    than shuffling a hex aside and back again on the next turn (#37).

    Returns the walk and whether the board blocked it: a sidestep taken, or
    no step to take. A walk that engagement stopped is not blocked, since
    movement.md stops an engaged figure where it is.
    """
    occupied = state.occupied_hexes() - set(combatant.footprint)
    engagers = combat_math.engaging_fronts(state, combatant)
    anchor = combatant.position
    body = combatant.footprint
    stood_on = {anchor}
    route: list[tuple[Hex, int]] = []
    distances: list[int] = []
    blocked = False
    for _step in range(allowance):
        if hexes.figure_engaged(body, combatant.size_hexes, engagers):
            return _closest_of(combatant, target, route, distances), False
        if hexes.figures_adjacent(body, target.footprint):
            break
        stepped = hexes.step_towards(
            anchor,
            target.position,
            occupied,
            state.arena_radius,
            combatant.size_hexes,
            avoid=stood_on,
        )
        if hexes.distance(stepped, target.position) >= hexes.distance(
            anchor, target.position
        ):
            blocked = True
        if stepped == anchor:
            break
        anchor = stepped
        facing = hexes.direction_towards(anchor, target.position)
        body = hexes.footprint(anchor, facing, combatant.size_hexes)
        stood_on.add(anchor)
        route.append((anchor, facing))
        distances.append(hexes.figure_distance(body, target.footprint))
    return _closest_of(combatant, target, route, distances), blocked


def _closest_of(
    combatant: CombatantState,
    target: CombatantState,
    route: list[tuple[Hex, int]],
    distances: list[int],
) -> Approach:
    """A walk cut at the first step of its closest approach, or no walk at
    all when no step got nearer than the start."""
    start_distance = combat_math.figure_distance(combatant, target)
    closest = min(distances, default=start_distance)
    if closest >= start_distance:
        return Approach(route=(), distance=start_distance)
    return Approach(
        route=tuple(route[: distances.index(closest) + 1]), distance=closest
    )


def _arrival_distance(
    state: BattleState,
    combatant: CombatantState,
    target: CombatantState,
    anchor: Hex,
    facing: int,
) -> int:
    """Figure distance to ``target`` from ``anchor`` once turned to face it.

    The engine turns a mover to its target after the move, unless a
    multi-hex body has no room to swing (``TurnRunner.face_towards``).
    """
    turned = hexes.direction_towards(anchor, target.position)
    if footprint_clear(state, combatant, anchor, turned):
        facing = turned
    body = hexes.footprint(anchor, facing, combatant.size_hexes)
    return hexes.figure_distance(body, target.footprint)


def approach_route(
    state: BattleState,
    combatant: CombatantState,
    target: CombatantState,
    allowance: int,
) -> Approach:
    """How a MOVE, CHARGE or DODGE closes on ``target`` within ``allowance``.

    The straight line toward the target first (:func:`_line_towards`), as the
    engine has always walked it. When that is blocked short of the target, a
    clear path round (:func:`_paths`, the search a placed move walks) is
    taken instead if it ends strictly nearer, the shortest such path first.
    A figure no path brings nearer stays where it is (tarmar-engine #37). An
    unblocked line is never searched past, so a move in the open walks the
    straight line it always has; and a line that stops because the figure is
    engaged stays stopped: movement.md stops a figure the moment it is
    engaged, and the search is for a way round what blocks the line, not
    round an enemy that holds the figure.

    The engine walks this route and the AI prices a charge's reach by it, so
    the two read one rule.
    """
    line, blocked = _line_towards(state, combatant, target, allowance)
    if not blocked or line.distance <= 1:
        return line
    best = line
    # _paths is breadth first, so the first path to a distance is a shortest.
    for anchor, path in _paths(state, combatant, allowance).items():
        before = path[-2] if len(path) > 1 else combatant.position
        arrival = _arrival_distance(
            state, combatant, target, anchor, hexes.direction_towards(before, anchor)
        )
        if arrival < best.distance:
            route = tuple(
                (cell, hexes.direction_towards(previous, cell))
                for previous, cell in zip([combatant.position, *path], path)
            )
            best = Approach(route=route, distance=arrival)
    return best


def path_to(
    state: BattleState, combatant: CombatantState, key: str, destination: Hex
) -> list[Hex] | None:
    """The hexes a gait option's figure walks to ``destination``, or ``None``."""
    gait = PLACED_GAITS.get(actions.base_option(key))
    if gait is None:
        return None
    return _paths(state, combatant, gait_allowance(combatant, gait)).get(destination)


def _adjacent_clear(state: BattleState, combatant: CombatantState) -> list[Hex]:
    return [
        cell
        for cell in hexes.neighbors(combatant.position)
        if footprint_clear(state, combatant, cell, combatant.facing)
    ]


def _engagers(
    state: BattleState, combatant: CombatantState, body: tuple[Hex, ...]
) -> set[int]:
    """The enemies whose front hexes would hold part of ``body``."""
    cells = set(body)
    return {
        enemy.combatant_id
        for enemy in state.enemies_of(combatant)
        if combat_math.engages(enemy) and enemy.front_hexes & cells
    }


def keeps_engagement(
    state: BattleState, combatant: CombatantState, anchor: Hex
) -> bool:
    """Would a Shift to ``anchor`` leave the figure engaged with every enemy
    it is engaged with now? A Shift that leaves one is a DISENGAGE (n)."""
    after = hexes.footprint(anchor, combatant.facing, combatant.size_hexes)
    return _engagers(state, combatant, combatant.footprint) <= _engagers(
        state, combatant, after
    )


def placement(state: BattleState, combatant: CombatantState, key: str) -> str:
    """Which kind of placed move ``key`` allows this figure, or "".

    "shift", "step" or "gait" (module docstring). A grappled or grappling
    figure, or one in hand-to-hand, has only the DISENGAGE step (v): the
    hand-to-hand table has no Move column, and a hold locks both figures in
    place.
    """
    base = actions.base_option(key)
    held = hexes.figure_locked_by_grapple(combatant.grappled_by, combatant.grappling)
    if held or in_hand_to_hand(state, combatant):
        return "step" if base == "v" else ""
    if base in STEP_OPTIONS:
        return "step"
    if base in SHIFT_OPTIONS and not actions.is_yielded(key):
        return "shift"
    if base in PLACED_GAITS:
        return "gait"
    return ""


def legal_destinations(
    state: BattleState, combatant: CombatantState, key: str
) -> frozenset[Hex]:
    """Every hex ``key`` may place this figure at, as the board stands."""
    kind = placement(state, combatant, key)
    if kind == "shift":
        return frozenset(
            cell
            for cell in _adjacent_clear(state, combatant)
            if keeps_engagement(state, combatant, cell)
        )
    if kind == "step":
        return frozenset(_adjacent_clear(state, combatant))
    if kind == "gait":
        gait = PLACED_GAITS[actions.base_option(key)]
        return frozenset(_paths(state, combatant, gait_allowance(combatant, gait)))
    return frozenset()


def refusal(
    state: BattleState, combatant: CombatantState, key: str, destination: Hex
) -> str | None:
    """Why ``destination`` is not a hex ``key`` may place the figure at, or None."""
    kind = placement(state, combatant, key)
    if not kind:
        return f"{actions.option_name(key)} takes no placed move"
    if kind in ("shift", "step"):
        if not hexes.is_adjacent(combatant.position, destination):
            return f"{destination} is not next to {combatant.name}"
        if not footprint_clear(state, combatant, destination, combatant.facing):
            return f"{destination} is not clear"
        if kind == "shift" and not keeps_engagement(state, combatant, destination):
            return (
                f"a Shift to {destination} would take {combatant.name} out of "
                "an engagement, which only DISENGAGE (n) does"
            )
        return None
    gait = PLACED_GAITS[actions.base_option(key)]
    allowance = gait_allowance(combatant, gait)
    if destination not in _paths(state, combatant, allowance):
        return (
            f"{destination} is not within {combatant.name}'s {allowance}-hex "
            f"{gait} along a clear path"
        )
    return None
