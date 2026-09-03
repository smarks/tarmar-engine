"""Megahex tiling and megahex-distance for missile range (Melee p.16).

Ported from melee's ``engine/megahex.py``. A *megahex* (MH) is the printed
map's larger cell: a centre hex plus its six neighbours — a 7-hex flower.
Missile fire on p.16 penalises DX by the number of *megahexes* between firer
and target, not by raw hex count.

The centres of the 7-hex flowers tile the plane on a sqrt(7) sublattice of
the hex lattice, generated in axial ``(q, r)`` by ``u = (2, 1)`` and
``v = (-1, 3)`` (each of squared length 7, determinant 7 — one full flower
per fundamental cell). Megahex coordinates form an ordinary hex grid, so
megahex distance is the ordinary axial hex distance in lattice space.
"""

from __future__ import annotations

from hexarena.hex import Hex, HexLayout

# Generators of the megahex-centre sublattice, in axial (q, r) coordinates.
_U = (2, 1)
_V = (-1, 3)


def _axial(layout: HexLayout, hex_position: Hex) -> tuple[int, int]:
    """Axial ``(q, r)`` of a hex (``q == cube_x``, ``r == cube_z``)."""
    cube_x, _cube_y, cube_z = layout.to_cube(hex_position)
    return cube_x, cube_z


def _center_axial(coord_a: int, coord_b: int) -> tuple[int, int]:
    """Axial ``(q, r)`` of the flower centre for lattice coordinates ``(a, b)``."""
    return (
        coord_a * _U[0] + coord_b * _V[0],
        coord_a * _U[1] + coord_b * _V[1],
    )


def _axial_distance(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Hex distance between two axial ``(q, r)`` points."""
    diff_q = start[0] - end[0]
    diff_r = start[1] - end[1]
    return (abs(diff_q) + abs(diff_r) + abs(diff_q + diff_r)) // 2


def megahex_coord(layout: HexLayout, hex_position: Hex) -> tuple[int, int]:
    """Lattice coordinates ``(a, b)`` of the megahex containing ``hex_position``.

    Rounding the exact solution lands within one lattice step; a 3x3 search
    around the rounded pair selects the flower whose centre is nearest (the
    flowers partition the plane, so exactly one is within hex-distance 1).
    """
    axial_q, axial_r = _axial(layout, hex_position)
    guess_a = round((3 * axial_q + axial_r) / 7)
    guess_b = round((-axial_q + 2 * axial_r) / 7)

    best_coord = (guess_a, guess_b)
    best_distance = None
    for delta_a in (-1, 0, 1):
        for delta_b in (-1, 0, 1):
            candidate_a = guess_a + delta_a
            candidate_b = guess_b + delta_b
            center = _center_axial(candidate_a, candidate_b)
            distance = _axial_distance((axial_q, axial_r), center)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_coord = (candidate_a, candidate_b)
    return best_coord


def megahex_distance(layout: HexLayout, start: Hex, end: Hex) -> int:
    """Number of megahex steps between the flowers holding ``start`` and ``end``."""
    start_coord = megahex_coord(layout, start)
    end_coord = megahex_coord(layout, end)
    return _axial_distance(start_coord, end_coord)
