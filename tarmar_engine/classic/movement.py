"""Classic movement allowance and reachability (Section V) — ported from melee.

Disengaged figures may move their full MA; most attacking/defending options cap
movement at half MA or a single hex ("shifting"). A figure may not move through a
standing figure, and must stop the instant it enters an enemy's front hex
(becoming engaged).

Reachability uses the shared :func:`hexarena.pathfinding.reachable`; this module
supplies the Melee-specific blocked set (standing figures) and stop set (enemy
front hexes).
"""
# pyright: reportArgumentType=false
# (hexarena's pathfinding Node is an untyped Hashable alias; the same
# relaxation tarmar-studio applies to the identical hexarena-facing code)
from __future__ import annotations

from hexarena.hex import Hex
from hexarena.pathfinding import Reach, reachable

from ..options import movement_budget
from .arena import BODY_COST, CLEAR_COST, Arena


def reachable_moves(
    arena: Arena,
    start: Hex,
    budget: int,
    *,
    blocked: set[Hex] | None = None,
    stop_hexes: set[Hex] | None = None,
    body_hexes: set[Hex] | None = None,
) -> Reach:
    """Hexes a figure can finish movement on within ``budget`` hexes.

    Args:
        arena: the map (for bounds-checked adjacency).
        start: the figure's hex.
        budget: hexes of movement available (the option's cap).
        blocked: hexes that may not be entered (standing figures).
        stop_hexes: hexes that may be entered but not moved past (enemy fronts).
        body_hexes: hexes holding a fallen body; entering one costs
            :data:`~.arena.BODY_COST` MA instead of ``CLEAR_COST`` (p.8).
    """
    blocked = blocked or set()
    stop_hexes = stop_hexes or set()
    body_hexes = body_hexes or set()
    return reachable(
        start,
        arena.neighbors,
        lambda _from, to_hex: BODY_COST if to_hex in body_hexes else CLEAR_COST,
        budget,
        must_stop_fn=lambda hex_position: hex_position in stop_hexes,
        blocked=blocked,
    )


__all__ = ["Reach", "movement_budget", "reachable_moves"]
