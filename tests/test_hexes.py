"""Unit tests for the axial hex spatial layer (DB-free)."""

from unittest import TestCase

from tarmar_engine import hexes


class DistanceAndNeighborsTest(TestCase):
    def test_distance_zero_for_same_hex(self):
        self.assertEqual(hexes.distance((2, -1), (2, -1)), 0)

    def test_distance_of_neighbours_is_one(self):
        for neighbour in hexes.neighbors((0, 0)):
            self.assertEqual(hexes.distance((0, 0), neighbour), 1)

    def test_distance_along_axis(self):
        self.assertEqual(hexes.distance((0, 0), (3, 0)), 3)
        self.assertEqual(hexes.distance((0, 0), (0, -4)), 4)
        self.assertEqual(hexes.distance((0, 0), (2, -5)), 5)

    def test_neighbors_are_distinct(self):
        self.assertEqual(len(set(hexes.neighbors((1, 1)))), 6)

    def test_add_wraps_direction(self):
        self.assertEqual(hexes.add((0, 0), 6), hexes.add((0, 0), 0))

    def test_is_adjacent(self):
        self.assertTrue(hexes.is_adjacent((0, 0), (1, 0)))
        self.assertFalse(hexes.is_adjacent((0, 0), (2, 0)))
        self.assertFalse(hexes.is_adjacent((0, 0), (0, 0)))


class FacingArcTest(TestCase):
    def test_directly_ahead_is_front(self):
        # Facing 0 = towards (+1, 0).
        self.assertEqual(hexes.arc_of((0, 0), 0, (1, 0)), "front")

    def test_flanking_front_hexes(self):
        self.assertEqual(hexes.arc_of((0, 0), 0, (1, -1)), "front")
        self.assertEqual(hexes.arc_of((0, 0), 0, (0, 1)), "front")

    def test_side_hexes(self):
        self.assertEqual(hexes.arc_of((0, 0), 0, (0, -1)), "side")
        self.assertEqual(hexes.arc_of((0, 0), 0, (-1, 1)), "side")

    def test_rear_hex(self):
        self.assertEqual(hexes.arc_of((0, 0), 0, (-1, 0)), "rear")

    def test_distant_attacker_uses_line_direction(self):
        # Attacker far away directly behind a figure facing direction 0.
        self.assertEqual(hexes.arc_of((0, 0), 0, (-5, 0)), "rear")
        self.assertEqual(hexes.arc_of((0, 0), 0, (6, 0)), "front")

    def test_arc_bonus_values(self):
        self.assertEqual(hexes.arc_to_hit_bonus("front"), 0)
        self.assertEqual(hexes.arc_to_hit_bonus("side"), hexes.SIDE_HEX_BONUS)
        self.assertEqual(hexes.arc_to_hit_bonus("rear"), hexes.REAR_HEX_BONUS)

    def test_prone_defender_counts_as_rear_from_any_arc(self):
        # movement.md: prone/crawling — all hexes count as rear.
        self.assertEqual(
            hexes.arc_to_hit_bonus("front", defender_prone=True),
            hexes.REAR_HEX_BONUS,
        )

    def test_direction_towards_same_hex(self):
        self.assertEqual(hexes.direction_towards((3, 3), (3, 3)), 0)


class RangeBandTest(TestCase):
    def test_megahex_range_rounds_up(self):
        self.assertEqual(hexes.megahex_range(0), 0)
        self.assertEqual(hexes.megahex_range(1), 1)
        self.assertEqual(hexes.megahex_range(3), 1)
        self.assertEqual(hexes.megahex_range(4), 2)
        self.assertEqual(hexes.megahex_range(6), 2)
        self.assertEqual(hexes.megahex_range(7), 3)

    def test_missile_penalty_bands_match_dex_adjustments_table(self):
        # dex-adjustments.md: 0-2 MH -> 0, 3-4 -> -1, 5-6 -> -2, 7-8 -> -3.
        expectations = {1: 0, 2: 0, 3: -1, 4: -1, 5: -2, 6: -2, 7: -3, 8: -3}
        for megahexes, penalty in expectations.items():
            distance_hexes = megahexes * hexes.HEXES_PER_MEGAHEX_STEP
            self.assertEqual(
                hexes.missile_range_penalty(distance_hexes),
                penalty,
                f"{megahexes} MH should be {penalty}",
            )

    def test_missile_penalty_continues_minus_one_per_two_megahexes(self):
        self.assertEqual(
            hexes.missile_range_penalty(10 * hexes.HEXES_PER_MEGAHEX_STEP), -4
        )

    def test_thrown_penalty_per_hex(self):
        self.assertEqual(hexes.thrown_range_penalty(0), 0)
        self.assertEqual(hexes.thrown_range_penalty(5), -5)


class ArenaAndSteppingTest(TestCase):
    def test_in_arena(self):
        self.assertTrue(hexes.in_arena((0, 0), 3))
        self.assertTrue(hexes.in_arena((3, 0), 3))
        self.assertFalse(hexes.in_arena((4, 0), 3))

    def test_step_towards_reduces_distance(self):
        stepped = hexes.step_towards((0, 0), (4, 0), set(), 8)
        self.assertLess(hexes.distance(stepped, (4, 0)), 4)

    def test_step_towards_avoids_occupied(self):
        # The straight-line hex is taken; the step sidesteps around it
        # (deterministically, first unoccupied equal-distance direction).
        stepped = hexes.step_towards((0, 0), (2, 0), {(1, 0)}, 8)
        self.assertEqual(stepped, (1, -1))
        # From the sidestep hex the next step is strictly closer.
        self.assertLess(
            hexes.distance(hexes.step_towards(stepped, (2, 0), {(1, 0)}, 8), (2, 0)),
            2,
        )

    def test_step_towards_boxed_in_stays_put(self):
        occupied = set(hexes.neighbors((0, 0)))
        self.assertEqual(hexes.step_towards((0, 0), (4, 0), occupied, 8), (0, 0))

    def test_step_towards_respects_arena_edge(self):
        # Standing on the rim, target outside: no in-arena step improves.
        self.assertEqual(hexes.step_towards((2, 0), (4, 0), set(), 2), (2, 0))

    def test_step_towards_adjacent_target_hex_is_never_entered(self):
        # The target's own hex is occupied by the target.
        stepped = hexes.step_towards((1, 0), (2, 0), {(2, 0)}, 8)
        self.assertEqual(stepped, (1, 0))


def one_hex_enemy(
    position: tuple[int, int], facing: int
) -> tuple[frozenset[hexes.Hex], int]:
    """(front hexes, size) for a one-hex enemy, as figure_engaged consumes.

    The size is annotated ``int``, not left to inference: a bare ``1`` infers
    as ``Literal[1]``, and a list of those is not assignable to the
    ``list[tuple[frozenset[Hex], int]]`` ``figure_engaged`` takes.
    """
    return (hexes.front_hexes(position, facing, 1), 1)


class FootprintTest(TestCase):
    def test_size_class_rounds_to_implemented_footprints(self):
        # 1 stays 1; 2 rounds up to the triangle; 4 rounds down to it;
        # 5-6 round up to the megahex (module docstring).
        expected = {1: 1, 2: 3, 3: 3, 4: 3, 5: 7, 6: 7, 7: 7, 9: 7}
        for size, footprint_class in expected.items():
            self.assertEqual(hexes.footprint_size_class(size), footprint_class)

    def test_one_hex_footprint_is_the_anchor(self):
        self.assertEqual(hexes.footprint((2, -1), 4, 1), ((2, -1),))

    def test_three_hex_footprint_is_a_triangle_behind_the_head(self):
        # Facing 0: body hexes in directions 2 and 3.
        self.assertEqual(hexes.footprint((0, 0), 0, 3), ((0, 0), (0, -1), (-1, 0)))

    def test_three_hex_footprint_hexes_are_mutually_adjacent(self):
        for facing in range(6):
            cluster = hexes.footprint((0, 0), facing, 3)
            for first in cluster:
                for second in cluster:
                    if first != second:
                        self.assertTrue(hexes.is_adjacent(first, second))

    def test_seven_hex_footprint_is_the_megahex(self):
        cluster = hexes.footprint((1, 1), 2, 7)
        self.assertEqual(len(cluster), 7)
        self.assertEqual(set(cluster), {(1, 1)} | set(hexes.neighbors((1, 1))))

    def test_front_hexes_of_one_hex_figure_are_the_classic_three(self):
        # Facing 0: the faced hex and its two flanking directions.
        self.assertEqual(
            hexes.front_hexes((0, 0), 0, 1), frozenset({(1, 0), (1, -1), (0, 1)})
        )

    def test_front_hexes_exclude_the_body(self):
        for size in (3, 7):
            body = set(hexes.footprint((0, 0), 0, size))
            self.assertFalse(hexes.front_hexes((0, 0), 0, size) & body)

    def test_figure_distance_uses_closest_footprint_hexes(self):
        # A megahex at the origin reaches one hex further than its anchor.
        big = hexes.footprint((0, 0), 0, 7)
        small = hexes.footprint((3, 0), 0, 1)
        self.assertEqual(hexes.figure_distance(big, small), 2)
        self.assertTrue(hexes.figures_adjacent(big, hexes.footprint((2, 0), 0, 1)))


class EngagementTest(TestCase):
    def test_thresholds_follow_the_movement_md_size_bands(self):
        expected = {1: 1, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 3, 9: 3}
        for size, threshold in expected.items():
            self.assertEqual(hexes.engagement_threshold(size), threshold)

    def test_one_hex_figure_engaged_in_enemy_front_hex(self):
        # Enemy at (1,0) facing 3 (towards (-1,0)): our hex (0,0) is its front.
        body = hexes.footprint((0, 0), 0, 1)
        self.assertTrue(hexes.figure_engaged(body, 1, [one_hex_enemy((1, 0), 3)]))

    def test_one_hex_figure_not_engaged_in_enemy_rear_hex(self):
        body = hexes.footprint((0, 0), 0, 1)
        self.assertFalse(hexes.figure_engaged(body, 1, [one_hex_enemy((1, 0), 0)]))

    def test_one_hex_figure_not_engaged_when_not_adjacent(self):
        body = hexes.footprint((0, 0), 0, 1)
        self.assertFalse(hexes.figure_engaged(body, 1, [one_hex_enemy((3, 0), 3)]))

    def test_not_engaged_with_no_enemies(self):
        self.assertFalse(hexes.figure_engaged(hexes.footprint((0, 0), 0, 1), 1, []))

    def test_three_hex_figure_needs_two_one_hex_engagers(self):
        # movement.md: 3-6 hex figure engaged in front hexes of 2+ one-hex
        # figures. Two enemies whose front arcs cover the triangle's hexes.
        body = hexes.footprint((0, 0), 0, 3)  # (0,0), (0,-1), (-1,0)
        first = one_hex_enemy((1, 0), 3)  # front covers (0,0)
        second = one_hex_enemy((-2, 0), 0)  # front covers (-1,0)
        self.assertFalse(hexes.figure_engaged(body, 3, [first]))
        self.assertTrue(hexes.figure_engaged(body, 3, [first, second]))

    def test_three_hex_figure_engaged_by_one_multi_hex_enemy(self):
        body = hexes.footprint((0, 0), 0, 3)
        # A 3-hex enemy headed at (1,0) facing 3: its front covers (0,0).
        enemy = (hexes.front_hexes((1, 0), 3, 3), 3)
        self.assertTrue(hexes.figure_engaged(body, 3, [enemy]))

    def test_seven_hex_figure_needs_three_one_hex_engagers(self):
        body = hexes.footprint((0, 0), 0, 7)
        engagers = [
            one_hex_enemy((2, 0), 3),  # front covers (1,0)
            one_hex_enemy((-2, 0), 0),  # front covers (-1,0)
            one_hex_enemy((0, 2), 2),  # front covers (0,1)
        ]
        self.assertFalse(hexes.figure_engaged(body, 7, engagers[:2]))
        self.assertTrue(hexes.figure_engaged(body, 7, engagers))

    def test_seven_hex_figure_engaged_by_one_multi_hex_enemy(self):
        body = hexes.footprint((0, 0), 0, 7)
        enemy = (hexes.front_hexes((3, 0), 3, 7), 7)
        self.assertTrue(hexes.figure_engaged(body, 7, [enemy]))


class MultiHexSteppingTest(TestCase):
    def test_three_hex_figure_steps_where_its_whole_body_fits(self):
        stepped = hexes.step_towards((0, 0), (4, 0), set(), 8, size_hexes=3)
        self.assertEqual(stepped, (1, 0))

    def test_three_hex_figure_sidesteps_a_blocked_landing(self):
        # (1,0) is occupied, so the straight-line landing is invalid; the
        # first equal-distance sidestep in direction order is taken.
        stepped = hexes.step_towards((0, 0), (4, 0), {(1, 0)}, 8, size_hexes=3)
        self.assertEqual(stepped, (1, -1))

    def test_seven_hex_figure_respects_the_arena_edge(self):
        # A megahex anchored on the rim cannot step outward.
        self.assertEqual(
            hexes.step_towards((2, 0), (5, 0), set(), 3, size_hexes=7), (2, 0)
        )


class GrappleLockTest(TestCase):
    def test_hth_to_hit_bonus_is_four(self):
        self.assertEqual(hexes.HTH_TO_HIT_BONUS, 4)

    def test_neither_side_locked_when_no_grapple(self):
        self.assertFalse(hexes.figure_locked_by_grapple(None, None))

    def test_the_held_figure_is_locked(self):
        self.assertTrue(hexes.figure_locked_by_grapple(grappled_by=3, grappling=None))

    def test_the_grappler_is_locked(self):
        self.assertTrue(hexes.figure_locked_by_grapple(grappled_by=None, grappling=5))
