"""The classic arena map (Section II / V) — ported from melee's ``engine/arena.py``.

A bounded rectangular field of clear hexes with entrance hexes at each end,
owning the flat-top odd-q :class:`~hexarena.hex.HexLayout` the printed Melee
map uses. Occupancy is tracked by :mod:`.state`, so the arena stays pure
geometry plus terrain.
"""

from __future__ import annotations

from collections.abc import Iterator

from hexarena.hex import FLAT, Hex, HexLayout

# One movement point per clear hex (5.01); a fallen body is a 3-MA obstacle (p.8).
CLEAR_COST = 1
BODY_COST = 3

# The one canonical arena geometry: flat-top, odd-q — the same orientation the
# printed Melee map uses. HexLayout is immutable read-only geometry, so sharing
# the one instance is safe.
DEFAULT_LAYOUT = HexLayout(orientation=FLAT, odd=True)


class Arena:
    """A bounded, flat-top hex field with entrance hexes at each end."""

    DEFAULT_LAYOUT = DEFAULT_LAYOUT

    def __init__(
        self,
        cols: int = 9,
        rows: int = 15,
        *,
        layout: HexLayout | None = None,
        name: str = "arena",
    ) -> None:
        self.cols = cols
        self.rows = rows
        self.layout = layout or DEFAULT_LAYOUT
        self.name = name
        self.walls: set[Hex] = set()

    # ---- membership / geometry ----
    def contains(self, hex_position: Hex) -> bool:
        return (
            1 <= hex_position.col <= self.cols
            and 1 <= hex_position.row <= self.rows
            and hex_position not in self.walls
        )

    def all_hexes(self) -> Iterator[Hex]:
        for col in range(1, self.cols + 1):
            for row in range(1, self.rows + 1):
                here = Hex(col, row)
                if here not in self.walls:
                    yield here

    def neighbors(self, hex_position: Hex) -> list[Hex]:
        return [n for n in self.layout.neighbors(hex_position) if self.contains(n)]

    def distance(self, start: Hex, end: Hex) -> int:
        return self.layout.distance(start, end)

    def ray_past(self, start: Hex, target: Hex) -> list[Hex]:
        """The hexes a straight flight from ``start`` through ``target`` enters
        BEYOND the target, in order, extended well past the field edge.

        The flight is extended far past the field in CUBE space and walked with
        the standard hex lerp, so the continuation agrees exactly with the lane
        already walked (stepping a neighbor direction index would bend at the
        target on this offset grid). Off-field hexes are included; callers stop
        at the first hex the field does not contain.
        """
        span = self.layout.distance(start, target)
        scale = (self.cols + self.rows) // span + 2
        cube_start = self.layout.to_cube(start)
        cube_target = self.layout.to_cube(target)
        far = self.layout.from_cube(
            *(start_component + (target_component - start_component) * scale
              for start_component, target_component
              in zip(cube_start, cube_target)))
        return self.layout.line(start, far)[span + 1:]

    # ---- entrance hexes (Section V) ----
    @property
    def north_entrances(self) -> list[Hex]:
        middle = (self.cols + 1) // 2
        return [Hex(middle, 1), Hex(min(middle + 1, self.cols), 1)]

    @property
    def south_entrances(self) -> list[Hex]:
        middle = (self.cols + 1) // 2
        return [Hex(middle, self.rows), Hex(min(middle + 1, self.cols), self.rows)]
