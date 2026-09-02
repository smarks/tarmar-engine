"""Golden equivalence: hexarena-backed geometry vs the original bespoke math.

Issue #238 replaced battle/engine/hexes.py's hand-rolled axial coordinate
math (add/neighbors/distance/direction_towards/ring) with the shared
hexarena package. Because battle/engine/hexes.py itself now delegates to
hexarena, comparing hexes.distance() against hexarena.axial_distance()
directly would be circular — they're the same call. Instead this module
carries independent, hand-typed reference implementations of the *original*
formulas (the exact math battle/engine/hexes.py used before this issue, and
the standard redblobgames pointy-top/cube-round algorithms for the
capabilities battle never had), and checks that both battle's hexes module
and hexarena.axial agree with them across a representative arena
(``ARENA_RADIUS`` below, matching ``services.MIN_ARENA_RADIUS``).

If this test ever fails, the adaptation changed behaviour — it is not the
reference that should be "fixed" to match.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Hashable
from typing import cast
from unittest import TestCase

from hexarena.axial import AxialHex, axial_distance, axial_line, axial_neighbors
from hexarena.layout import axial_hex_center
from hexarena.pathfinding import reachable

from tarmar_engine import hexes

# Matches battle.services.MIN_ARENA_RADIUS — a real battle never uses a
# smaller arena, so this is a representative size, not a toy one.
ARENA_RADIUS = 5

_REFERENCE_DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)


def _reference_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    delta_q = a[0] - b[0]
    delta_r = a[1] - b[1]
    return (abs(delta_q) + abs(delta_r) + abs(delta_q + delta_r)) // 2


def _reference_neighbors(position: tuple[int, int]) -> list[tuple[int, int]]:
    return [(position[0] + dq, position[1] + dr) for dq, dr in _REFERENCE_DIRECTIONS]


def _reference_arena_hexes(radius: int) -> list[tuple[int, int]]:
    """Every axial hex within ``radius`` of the origin, row by row."""
    result = []
    for q in range(-radius, radius + 1):
        for r in range(-radius, radius + 1):
            if _reference_distance((0, 0), (q, r)) <= radius:
                result.append((q, r))
    return result


def _reference_line(
    start: tuple[int, int], end: tuple[int, int]
) -> list[tuple[int, int]]:
    """Independent cube-round straight line (redblobgames), typed from scratch."""
    steps = _reference_distance(start, end)
    if steps == 0:
        return [start]

    def to_cube(position: tuple[int, int]) -> tuple[int, int, int]:
        q, r = position
        return q, -q - r, r

    start_cube = to_cube(start)
    end_cube = to_cube(end)
    result = []
    epsilon = 1e-6
    for step in range(steps + 1):
        fraction = step / steps
        x = start_cube[0] + (end_cube[0] - start_cube[0]) * fraction + epsilon
        y = start_cube[1] + (end_cube[1] - start_cube[1]) * fraction + 2 * epsilon
        z = start_cube[2] + (end_cube[2] - start_cube[2]) * fraction - 3 * epsilon
        rx, ry, rz = round(x), round(y), round(z)
        dx, dy, dz = abs(rx - x), abs(ry - y), abs(rz - z)
        if dx > dy and dx > dz:
            rx = -ry - rz
        elif dy > dz:
            ry = -rx - rz
        else:
            rz = -rx - ry
        result.append((rx, rz))
    return result


def _reference_reachable(
    start: tuple[int, int],
    blocked: set[tuple[int, int]],
    budget: int,
    radius: int,
) -> set[tuple[int, int]]:
    """Independent uniform-cost BFS (not hexarena's Dijkstra implementation)."""
    cost = {start: 0}
    frontier: deque[tuple[int, int]] = deque([start])
    while frontier:
        current = frontier.popleft()
        if cost[current] == budget:
            continue
        for neighbor in _reference_neighbors(current):
            if neighbor in blocked:
                continue
            if _reference_distance((0, 0), neighbor) > radius:
                continue
            new_cost = cost[current] + 1
            if new_cost > budget:
                continue
            if neighbor not in cost or new_cost < cost[neighbor]:
                cost[neighbor] = new_cost
                frontier.append(neighbor)
    cost.pop(start, None)
    return set(cost)


def _reference_axial_pixel(q: int, r: int, size: float) -> tuple[float, float]:
    """Independent pointy-top axial-to-pixel formula, matching
    static/js/battle.js's axialToPixel() (the frontend's own independent
    implementation, never touched by this issue)."""
    sqrt3 = math.sqrt(3.0)
    return size * (sqrt3 * q + (sqrt3 / 2) * r), size * 1.5 * r


class DistanceAndNeighborEquivalenceTest(TestCase):
    def test_distance_matches_reference_across_the_arena(self):
        arena = _reference_arena_hexes(ARENA_RADIUS)
        for origin in arena:
            for target in arena:
                expected = _reference_distance(origin, target)
                self.assertEqual(hexes.distance(origin, target), expected)
                self.assertEqual(
                    axial_distance(AxialHex(*origin), AxialHex(*target)), expected
                )

    def test_neighbors_match_reference_across_the_arena(self):
        arena = _reference_arena_hexes(ARENA_RADIUS)
        for position in arena:
            expected = _reference_neighbors(position)
            self.assertEqual(hexes.neighbors(position), expected)
            self.assertEqual(
                [(h.q, h.r) for h in axial_neighbors(AxialHex(*position))],
                expected,
            )

    def test_pinned_distance_values(self):
        # Literal golden values, independent of any formula in this file.
        cases = {
            ((0, 0), (5, -5)): 5,
            ((0, 0), (5, 0)): 5,
            ((0, 0), (0, 5)): 5,
            ((-5, 5), (5, -5)): 10,
            ((-3, 2), (4, -1)): 7,
        }
        for (a, b), expected in cases.items():
            self.assertEqual(hexes.distance(a, b), expected)


class RingEquivalenceTest(TestCase):
    def test_ring_matches_reference_for_every_radius(self):
        for radius in range(ARENA_RADIUS + 1):
            self.assertEqual(hexes.ring(radius), _reference_arena_ring(radius))


def _reference_arena_ring(radius: int) -> list[tuple[int, int]]:
    if radius == 0:
        return [(0, 0)]
    hexes_on_ring = []
    dq, dr = _REFERENCE_DIRECTIONS[4]
    position = (dq * radius, dr * radius)
    for direction in range(6):
        step_dq, step_dr = _REFERENCE_DIRECTIONS[direction]
        for _step in range(radius):
            hexes_on_ring.append(position)
            position = (position[0] + step_dq, position[1] + step_dr)
    return hexes_on_ring


class LineEquivalenceTest(TestCase):
    def test_axial_line_matches_reference_across_the_arena(self):
        arena = _reference_arena_hexes(ARENA_RADIUS)
        # Full O(n^2) pairing over the whole arena is slow; sample the rim
        # and a handful of interior hexes against the origin and each other.
        rim = _reference_arena_ring(ARENA_RADIUS)
        sample_points = rim[::3] + [(0, 0), (2, -1), (-2, 3)]
        for start in sample_points:
            for end in sample_points:
                expected = _reference_line(start, end)
                actual = [
                    (h.q, h.r) for h in axial_line(AxialHex(*start), AxialHex(*end))
                ]
                self.assertEqual(actual, expected, f"{start} -> {end}")
                for point in expected:
                    self.assertIn(point, arena)


class ReachabilityEquivalenceTest(TestCase):
    def test_reachability_with_blockers_matches_reference(self):
        start = (0, 0)
        # A ring of blockers two hexes out, with one gap, forces the search
        # to route around rather than just thresholding on raw distance.
        blocked = set(_reference_arena_ring(2)) - {(2, 0)}

        # hexarena.pathfinding.reachable() is generic over any Hashable node
        # (typing.Hashable, not AxialHex specifically), so the callbacks it
        # takes are typed against that broader contract rather than AxialHex.
        def neighbors_fn(hex_position: Hashable) -> list[Hashable]:
            candidate_hexes = axial_neighbors(cast(AxialHex, hex_position))
            return [
                candidate
                for candidate in candidate_hexes
                if axial_distance(AxialHex(0, 0), candidate) <= ARENA_RADIUS
            ]

        def uniform_cost(_from: Hashable, _to: Hashable) -> int:
            return 1

        budget = 4
        result = reachable(
            AxialHex(*start),
            neighbors_fn,
            uniform_cost,
            budget,
            blocked={AxialHex(*hex_position) for hex_position in blocked},
        )
        actual = {(h.q, h.r) for h in result.reachable_hexes()}
        expected = _reference_reachable(start, blocked, budget, ARENA_RADIUS)
        self.assertEqual(actual, expected)
        self.assertTrue(expected)  # sanity: the gap does let some hexes through


class PixelCenterEquivalenceTest(TestCase):
    def test_axial_hex_center_matches_reference_across_the_arena(self):
        size = 10.0
        arena = _reference_arena_hexes(ARENA_RADIUS)
        for q, r in arena:
            expected_x, expected_y = _reference_axial_pixel(q, r, size)
            actual_x, actual_y = axial_hex_center(q, r, size=size)
            self.assertAlmostEqual(actual_x, expected_x, places=9)
            self.assertAlmostEqual(actual_y, expected_y, places=9)

    def test_pinned_pixel_center_values(self):
        # Literal golden values (HEX_SIZE = 10, as static/js/battle.js uses).
        size = 10.0
        sqrt3 = math.sqrt(3.0)
        cases = {
            (0, 0): (0.0, 0.0),
            (1, 0): (sqrt3 * size, 0.0),
            (0, 1): ((sqrt3 / 2) * size, 1.5 * size),
            (2, -1): (sqrt3 * size * 1.5, -1.5 * size),
        }
        for (q, r), (expected_x, expected_y) in cases.items():
            actual_x, actual_y = axial_hex_center(q, r, size=size)
            self.assertAlmostEqual(actual_x, expected_x, places=9)
            self.assertAlmostEqual(actual_y, expected_y, places=9)
