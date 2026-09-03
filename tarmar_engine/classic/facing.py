"""Classic facing, front/side/rear zones, and engagement (Section VI).

Ported from melee's ``engine/facing.py``. This is the Figure-typed reference
implementation of the same structural mechanics milestone 2 ported onto the
shared state types as :class:`tarmar_engine.engagement.MeleeStyleEngagement`;
both are pinned by the ported melee tests so they cannot drift apart in
meaning. Arc classification matches the shared ``hexes.arc_of`` split exactly
(front offsets 0/1/5, side 2/4, rear 3; +2 side / +4 rear).
"""

from __future__ import annotations

from collections.abc import Iterable

from hexarena.hex import Hex, HexLayout

from .figure import Figure, Posture

FRONT = "front"
SIDE = "side"
REAR = "rear"


def zone_of_direction(facing: int, direction_index: int) -> str:
    """Classify ``direction_index`` relative to a figure facing ``facing``."""
    offset = (direction_index - facing) % 6
    if offset in (0, 1, 5):
        return FRONT
    if offset in (2, 4):
        return SIDE
    return REAR  # offset == 3


def facing_toward(layout: HexLayout, from_hex: Hex, to_hex: Hex) -> int:
    """Direction index (0-5) whose front points most directly at ``to_hex``."""
    best_dir, best_dist = 0, None
    for direction in range(6):
        distance = layout.distance(layout.neighbor(from_hex, direction), to_hex)
        if best_dist is None or distance < best_dist:
            best_dir, best_dist = direction, distance
    return best_dir


def front_hexes(layout: HexLayout, figure: Figure) -> list[Hex]:
    """The hexes in front of ``figure`` (no bounds-checking).

    For a single-hex figure these are exactly the three front hexes (the faced
    hex and its two flanks). For a multi-hex figure (the giant) the front is
    the union of every footprint hex's own three front hexes, deduped and with
    the figure's own footprint removed.
    """
    footprint = figure.footprint(layout)
    footprint_set = set(footprint)
    fronts: list[Hex] = []
    for hex_position in footprint:
        for delta in (-1, 0, 1):
            candidate = layout.neighbor(hex_position, (figure.facing + delta) % 6)
            if candidate not in footprint_set and candidate not in fronts:
                fronts.append(candidate)
    return fronts


def zone_toward(layout: HexLayout, observer: Figure, point: Hex) -> str | None:
    """Which zone of ``observer`` the ``point`` lies in, or ``None``.

    Works at any range by taking the direction of the first step along the
    line from the observer to the point. A prone figure has no front and is
    struck as from the rear; a kneeling figure keeps its front (melee #354).
    """
    if observer.position is None or point == observer.position:
        return None
    footprint = observer.footprint(layout)
    if len(footprint) > 1:                       # multi-hex observer (the giant)
        return _multi_zone_toward(layout, observer, point, footprint)
    line = layout.line(observer.position, point)
    direction = layout.direction_to(observer.position, line[1])
    if direction is None:
        return None
    if observer.all_front:
        return FRONT
    if observer.posture == Posture.PRONE:
        return REAR
    return zone_of_direction(observer.facing, direction)


def _multi_zone_toward(
    layout: HexLayout, observer: Figure, point: Hex, footprint: list[Hex]
) -> str | None:
    """Zone of a multi-hex observer (the giant) toward ``point``."""
    if observer.all_front:
        return FRONT
    if observer.posture == Posture.PRONE:
        return REAR
    if point in set(front_hexes(layout, observer)):
        return FRONT
    nearest = min(
        footprint, key=lambda hex_position: layout.distance(hex_position, point))
    if point == nearest:
        return REAR
    line = layout.line(nearest, point)
    if len(line) < 2:
        return None
    direction = layout.direction_to(nearest, line[1])
    if direction is None:
        return None
    return zone_of_direction(observer.facing, direction)


def _footprints_adjacent(layout: HexLayout, figure: Figure, other: Figure) -> bool:
    """Whether any hex of one figure's footprint is adjacent to the other's."""
    figure_footprint = figure.footprint(layout)
    other_footprint = other.footprint(layout)
    return any(
        layout.distance(here, there) == 1
        for here in figure_footprint
        for there in other_footprint
    )


def is_engaged_by(layout: HexLayout, figure: Figure, enemy: Figure) -> bool:
    """True if ``figure`` stands in ``enemy``'s front hex (so enemy engages it).

    A prone or airborne enemy has no front and engages no one; nor does an
    unarmed (staffless) wizard (Wizard p.9 — inert here until milestone 5).
    """
    if enemy.posture == Posture.PRONE or enemy.collapsed or enemy.flying:
        return False
    if enemy.unarmed_wizard:
        return False
    if figure.position is None or enemy.position is None:
        return False
    if not _footprints_adjacent(layout, figure, enemy):
        return False  # engagement requires adjacency
    return any(
        zone_toward(layout, enemy, hex_position) == FRONT
        for hex_position in figure.footprint(layout)
    )


def _engages(layout: HexLayout, figure: Figure, enemy: Figure) -> bool:
    """Whether ``enemy`` engages ``figure`` — one-directional (p.9)."""
    return is_engaged_by(layout, figure, enemy)


def engagement_count(
    layout: HexLayout, figure: Figure, enemies: Iterable[Figure]
) -> int:
    """How many distinct enemies are in melee contact with ``figure``."""
    return sum(1 for enemy in enemies if _engages(layout, figure, enemy))


def is_engaged(
    layout: HexLayout, figure: Figure, enemies: Iterable[Figure]
) -> bool:
    """True if ``figure`` is in melee contact with enough standing enemies.

    One-directional: engaged only by foes whose front hex it occupies. A giant
    needs **two** distinct foes in its front (``needs_two_to_engage``); an
    airborne figure is never engaged.
    """
    if figure.flying:
        return False
    needed = 2 if figure.needs_two_to_engage else 1
    return engagement_count(layout, figure, enemies) >= needed


def attack_zone(layout: HexLayout, attacker: Figure, target: Figure) -> str | None:
    """Zone of ``target`` that ``attacker`` strikes from (for the DX bonus)."""
    if attacker.position is None or target.position is None:
        return None
    return zone_toward(layout, target, attacker.position)


def facing_bonus(zone: str | None) -> int:
    """DX bonus for striking from a given zone (Attacks, p.10)."""
    if zone == SIDE:
        return 2
    if zone == REAR:
        return 4
    return 0


def format_situational_parts(zone: str | None, *, ignore_facing: bool,
                             range_penalty: int, situational_note: str) -> list[str]:
    """The shared trailing fragments of a to-hit explanation."""
    parts: list[str] = []
    if not ignore_facing and facing_bonus(zone):
        parts.append(f"+{facing_bonus(zone)} {'rear' if zone == REAR else 'flank'}")
    if range_penalty:
        parts.append(f"{range_penalty:+d} range")
    if situational_note:
        parts.append(situational_note)
    return parts
