"""Axial hex-grid geometry for the battle arena.

Implements the spatial rules from
``reference/content/public-rules/combat/action-options/movement.md``:
facing (front/side/rear hexes), engagement on adjacency, megahex range bands
for missiles (``dex-adjustments.md``), and straight-line stepping across an
open circular arena.

Coordinates are axial ``(q, r)`` on a pointy-top hex grid; the six neighbour
directions are indexed 0–5 and double as facing values.

Pure hex coordinate math (adjacency, distance, direction-finding, rings) is
the shared :mod:`hexarena.axial` package's job (issue #238) — this module
keeps battle's own ``(q, r)`` tuple surface (the type every call site and
the frontend's serialized state already use) and layers Tarmar's domain
rules on top: facing arcs, multi-hex figure footprints, and engagement. None
of that domain logic — what counts as a figure's front hexes, how many
engagers stop it, the megahex range table — belongs in hexarena, which knows
nothing about any particular game's rules.

Multi-hex figures (movement.md's engagement table, arrived with the beasts
of #181): a figure of ``size_hexes`` > 1 occupies a *footprint* of hexes
anchored on its head hex. The rules publish the 1-hex, "3–6 hex" and 7-hex
engagement rows but no cluster geometry, so the implemented footprints are
the two shapes the source game uses — a 3-hex triangle (head plus the two
hexes behind it) and the 7-hex megahex (head plus its six neighbours) — and
intermediate sizes round to the nearest implemented footprint: 2 rounds up
to the triangle (a 2-hex body cannot fit one hex), 4 rounds down to it, and
5–6 round up to the megahex. Engagement *thresholds*, by contrast, follow
the table's own size bands exactly (1 / 3–6 / 7+), drift-guarded against
``movement.md``; a 2-hex figure uses the 3–6 row it rounds into.

Deliberately out of scope for v1 (each is covered by the rules but not
modelled here):

* Terrain, obstacles and line-of-sight blockers — ``movement.md`` shadows/
  broken ground and the ``dex-adjustments.md`` shadow-hex modifiers assume
  map features the open arena does not have.
* Jousting, and HTH generally beyond the grapple sub-flow: entering HTH
  literally shares a hex with the enemy (``hand-to-hand-and-grappling.md``'s
  "Entering Hand-to-Hand"), which this module never models — two footprints
  are never allowed to overlap (``test_no_two_living_combatants_share_a_hex``
  guards this). Grappling (issue #231) instead treats an already-engaged,
  adjacent pair as close enough to attempt and resolve HTH actions in place;
  :func:`figure_locked_by_grapple` is what actually keeps a grappled pair
  from drifting apart once the attempt lands. Plain standalone unarmed
  strikes (HTH option t on its own, ``o``'s entry preconditions, and ``u``
  DRAW DAGGER) remain unimplemented.
"""

from __future__ import annotations

from hexarena.axial import (
    AxialHex,
    axial_add,
    axial_direction_to,
    axial_distance,
    axial_is_adjacent,
    axial_neighbors,
    axial_ring,
)

# movement.md: a megahex is 7 hexes (1 centre + 6 surrounding) — one megahex
# "step" of range therefore spans 3 hexes of distance (centre to centre).
HEXES_PER_MEGAHEX_STEP = 3

# dex-adjustments.md positional advantage, applied to the attacker's to-hit.
SIDE_HEX_BONUS = 2
REAR_HEX_BONUS = 4

# special-combat-situations.md: Defend/Dodge add +4 to your Target Number.
DEFEND_DODGE_TN_BONUS = 4

# hand-to-hand-and-grappling.md: "Once you're sharing a hex, both combatants
# get +4 to their to-hit rolls" — applies to a grapple attempt, a grappled
# figure's Strike Back, and a grappler's Squeeze (all HTH-range rolls).
HTH_TO_HIT_BONUS = 4

Hex = tuple[int, int]


def _to_axial(position: Hex) -> AxialHex:
    return AxialHex(position[0], position[1])


def _from_axial(hex_position: AxialHex) -> Hex:
    return (hex_position.q, hex_position.r)


def add(position: Hex, direction: int) -> Hex:
    """The hex one step from ``position`` in ``direction`` (0–5)."""
    return _from_axial(axial_add(_to_axial(position), direction))


def neighbors(position: Hex) -> list[Hex]:
    """The six adjacent hexes, in direction order."""
    return [
        _from_axial(hex_position)
        for hex_position in axial_neighbors(_to_axial(position))
    ]


def distance(a: Hex, b: Hex) -> int:
    """Hex distance between two axial coordinates."""
    return axial_distance(_to_axial(a), _to_axial(b))


def is_adjacent(a: Hex, b: Hex) -> bool:
    """Are two hexes exactly one step apart?"""
    return axial_is_adjacent(_to_axial(a), _to_axial(b))


def direction_towards(origin: Hex, target: Hex) -> int:
    """The direction index (0–5) that best points from ``origin`` at ``target``.

    Chosen as the neighbour direction whose step most reduces the distance to
    the target (ties broken by lowest index, so the choice is deterministic).
    For ``origin == target`` the answer is direction 0.
    """
    return axial_direction_to(_to_axial(origin), _to_axial(target))


def ring(radius: int) -> list[Hex]:
    """The hexes exactly ``radius`` steps from the origin, walked in order.

    Used to seed starting positions around the arena rim. ``radius`` 0 is
    just the origin.
    """
    return [
        _from_axial(hex_position) for hex_position in axial_ring(AxialHex(0, 0), radius)
    ]


def arc_of(defender: Hex, facing: int, attacker: Hex) -> str:
    """Which arc of the defender the attacker occupies: front, side, or rear.

    movement.md: each figure faces one hex side, determining front, side and
    rear hexes. A one-hex figure's three front hexes are the facing direction
    and its two flanking directions; the single rear hex is directly behind;
    the remaining two are side hexes. For a non-adjacent attacker (missiles),
    the arc is read from the straight-line direction to the attacker.
    """
    towards_attacker = direction_towards(defender, attacker)
    offset = (towards_attacker - facing) % 6
    if offset in (0, 1, 5):
        return "front"
    if offset == 3:
        return "rear"
    return "side"


def arc_to_hit_bonus(arc: str, *, defender_prone: bool = False) -> int:
    """Attacker's to-hit bonus for the defender arc (dex-adjustments.md).

    Side +2, rear +4. movement.md: prone/crawling figures count every hex as
    rear, so a prone defender always yields the rear bonus.
    """
    if defender_prone:
        return REAR_HEX_BONUS
    if arc == "rear":
        return REAR_HEX_BONUS
    if arc == "side":
        return SIDE_HEX_BONUS
    return 0


def megahex_range(distance_hexes: int) -> int:
    """Distance expressed in megahexes for the missile-range table.

    A megahex spans :data:`HEXES_PER_MEGAHEX_STEP` hexes of centre-to-centre
    distance; the band is the number of megahex steps, rounded up.
    """
    return -(-distance_hexes // HEXES_PER_MEGAHEX_STEP)


def missile_range_penalty(distance_hexes: int) -> int:
    """Missile to-hit penalty from range (dex-adjustments.md).

    0–2 MH → 0, 3–4 → −1, 5–6 → −2, 7–8 → −3, then −1 per further 2 MH —
    a single formula: ``−max(0, (MH − 1) // 2)``.
    """
    bands = megahex_range(distance_hexes)
    return -max(0, (bands - 1) // 2)


def thrown_range_penalty(distance_hexes: int) -> int:
    """Thrown-weapon to-hit penalty: −1 per hex to target (dex-adjustments.md)."""
    return -distance_hexes


def in_arena(position: Hex, radius: int) -> bool:
    """Is the hex inside the open circular arena of the given radius?"""
    return distance((0, 0), position) <= radius


def step_towards(
    origin: Hex,
    target: Hex,
    occupied: set[Hex],
    radius: int,
    size_hexes: int = 1,
) -> Hex:
    """One straight-line step of a figure's anchor toward ``target``.

    Picks the in-arena neighbour that most reduces the distance to the target
    (ties broken by direction order) among those where the figure's whole
    footprint — oriented toward the target from the candidate anchor, the
    facing the mover will adopt — lands clear of ``occupied``. When the
    straight line is blocked and no neighbour is strictly closer, an
    equal-distance sidestep is taken instead (again first by direction order)
    so a single blocker does not freeze the mover; steps per turn are bounded
    by gait, so this cannot loop. Returns ``origin`` unchanged only when boxed
    in outright. ``occupied`` must not include the mover's own footprint.
    """
    origin_distance = distance(origin, target)
    sidestep = None
    best = origin
    best_distance = origin_distance
    for direction in range(6):
        candidate = add(origin, direction)
        landing = footprint(candidate, direction_towards(candidate, target), size_hexes)
        if any(cell in occupied or not in_arena(cell, radius) for cell in landing):
            continue
        candidate_distance = distance(candidate, target)
        if candidate_distance < best_distance:
            best = candidate
            best_distance = candidate_distance
        elif candidate_distance == origin_distance and sidestep is None:
            sidestep = candidate
    if best != origin:
        return best
    if sidestep is not None and origin_distance > 1:
        return sidestep
    return origin


def figure_locked_by_grapple(grappled_by: int | None, grappling: int | None) -> bool:
    """Is a figure locked in place by a grapple (hand-to-hand-and-grappling.md)?

    The turn-sequence table's Phases 3-4 row: "Neither combatant moves —
    both are locked to the shared hex until the grapple ends." True for
    either side of the hold — the captive (``grappled_by`` set) or the
    captor (``grappling`` set).
    """
    return grappled_by is not None or grappling is not None


def footprint_size_class(size_hexes: int) -> int:
    """The implemented footprint (1, 3 or 7 hexes) a figure size rounds to.

    See the module docstring: 1 stays 1; 2 rounds up to the 3-hex triangle
    (a 2-hex body cannot fit one hex); 4 rounds down to it; 5–6 round up to
    the 7-hex megahex.
    """
    if size_hexes <= 1:
        return 1
    if size_hexes <= 4:
        return 3
    return 7


def footprint(anchor: Hex, facing: int, size_hexes: int) -> tuple[Hex, ...]:
    """The hex cluster a figure occupies, head at ``anchor``, oriented by facing.

    1-hex: the anchor alone. 3-hex triangle: the anchor plus its neighbours
    in directions ``facing+2`` and ``facing+3`` — two mutually adjacent hexes
    behind the head (the grid offers no symmetric pair straight behind, so
    the trailing pair is taken rear-left of the axis, deterministically).
    7-hex megahex: the anchor plus all six neighbours.
    """
    size_class = footprint_size_class(size_hexes)
    if size_class == 1:
        return (anchor,)
    if size_class == 3:
        return (anchor, add(anchor, facing + 2), add(anchor, facing + 3))
    return (anchor, *neighbors(anchor))


def front_hexes(anchor: Hex, facing: int, size_hexes: int) -> frozenset[Hex]:
    """The hexes a figure attacks into and engages through (movement.md).

    A figure's front hexes are the hexes adjacent to its footprint, outside
    it, that lie in its front arc as read from the head hex. For a one-hex
    figure this is exactly the classic three front hexes; for multi-hex
    figures the same rule extends the arc around the footprint boundary.
    """
    body = set(footprint(anchor, facing, size_hexes))
    result = set()
    for cell in body:
        for neighbor in neighbors(cell):
            if neighbor in body:
                continue
            if arc_of(anchor, facing, neighbor) == "front":
                result.add(neighbor)
    return frozenset(result)


def figure_distance(a: tuple[Hex, ...], b: tuple[Hex, ...]) -> int:
    """Hex distance between two figures: the closest pair of footprint hexes."""
    return min(distance(hex_a, hex_b) for hex_a in a for hex_b in b)


def figures_adjacent(a: tuple[Hex, ...], b: tuple[Hex, ...]) -> bool:
    """Are two figures in reach of each other's edges (footprints one apart)?"""
    return figure_distance(a, b) == 1


def engagement_threshold(size_hexes: int) -> int:
    """How many engaging one-hex enemies it takes to stop this figure.

    movement.md's engagement table, by the figure's *actual* size band:
    one-hex figures are engaged by 1, 3–6 hex figures by 2+, 7-hex figures
    by 3+. A 2-hex figure uses the 3–6 row it rounds into (module docstring).
    """
    if size_hexes >= 7:
        return 3
    if size_hexes >= 2:
        return 2
    return 1


def figure_engaged(
    body: tuple[Hex, ...],
    size_hexes: int,
    enemies: list[tuple[frozenset[Hex], int]],
) -> bool:
    """Is the figure occupying ``body`` engaged (movement.md's table)?

    ``enemies`` is ``(front hexes, size_hexes)`` per living, armed enemy. An
    enemy engages when any of its front hexes holds part of this figure's
    footprint. One-hex engagers must number at least
    :func:`engagement_threshold`; a single engaging multi-hex enemy always
    suffices ("or 1 multi-hex", every row).
    """
    threshold = engagement_threshold(size_hexes)
    body_hexes = set(body)
    engaging_one_hex = 0
    for enemy_front, enemy_size in enemies:
        if not (enemy_front & body_hexes):
            continue
        if enemy_size > 1:
            return True
        engaging_one_hex += 1
    return engaging_one_hex >= threshold
