"""Engagement rules — the facing/engagement area of the profile seam.

Both games classify arcs identically (front is offsets 0/1/5 from the
facing, rear is 3, side is 2/4) and award the same +2 side / +4 rear to-hit
bonuses, so arc math is ONE shared implementation (:func:`.hexes.arc_of` /
:func:`.hexes.arc_to_hit_bonus`) and is deliberately not a profile hook.
Engagement is where the games genuinely differ, and both variants share one
mechanism — a figure is engaged by enemies whose front hexes overlap its
footprint — with the differences isolated to three hooks:

* which enemies count as engagers (:meth:`EngagementRules.counts_as_engager`),
* how many engagers it takes (:meth:`EngagementRules.threshold`), and
* whether a single multi-hex engager suffices regardless of the count
  (:attr:`EngagementRules.multi_hex_always_engages`).

:class:`TarmarEngagement` is the pre-seam behavior exactly — it delegates to
``combat_math.is_engaged`` / ``hexes.figure_engaged`` so the refactor cannot
drift. :class:`MeleeStyleEngagement` ports melee's structural engagement
(``engine/facing.py``): strictly one engager per enemy, downed (prone or
felled) figures engage no one, and large figures need two distinct engagers
to be pinned. Melee conditions with no counterpart in the package state
(an airborne figure is never engaged; a staffless wizard engages no one)
arrive with the state that carries them — the injectable ``exempt`` and
``counts_as_engager`` hooks are their seam.
"""

from __future__ import annotations

from collections.abc import Callable

from . import hexes
from .state import BattleState, CombatantState


class EngagementRules:
    """Base of the engagement seam: the shared front-hex-overlap mechanism.

    Subclasses configure the hooks; :meth:`is_engaged` is the one entry the
    engine and policies consult.
    """

    #: Does a single engaging multi-hex enemy pin the figure regardless of
    #: the engager count? (Tarmar's table says yes on every row; melee has
    #: no such rule.)
    multi_hex_always_engages: bool = False

    def is_engaged(self, state: BattleState, actor: CombatantState) -> bool:
        """Is ``actor`` engaged? Subclasses must implement."""
        raise NotImplementedError

    def counts_as_engager(self, enemy: CombatantState) -> bool:
        """May ``enemy`` engage anyone at all?"""
        return enemy.active

    def threshold(self, actor: CombatantState) -> int:
        """How many qualifying engagers it takes to engage ``actor``."""
        return 1

    def _count_based_engagement(
        self, state: BattleState, actor: CombatantState
    ) -> bool:
        """The shared mechanism: engagers' front hexes overlap the footprint."""
        body = set(actor.footprint)
        engagers = 0
        for enemy in state.enemies_of(actor):
            if not self.counts_as_engager(enemy):
                continue
            if not (enemy.front_hexes & body):
                continue
            if self.multi_hex_always_engages and enemy.size_hexes > 1:
                return True
            engagers += 1
        return engagers >= self.threshold(actor)


class TarmarEngagement(EngagementRules):
    """movement.md's engagement table, unchanged from the pre-seam engine.

    Thresholds come from the figure's size band (1 / 3–6 / 7+ hexes), a
    single multi-hex engager always suffices, and any active enemy engages
    (Tarmar's table publishes no prone/unarmed exemption for engagers).
    Delegates to the drift-guarded ``hexes.figure_engaged`` so this class and
    the geometry module cannot disagree.
    """

    multi_hex_always_engages = True

    def is_engaged(self, state: BattleState, actor: CombatantState) -> bool:
        enemies = [
            (enemy.front_hexes, enemy.size_hexes) for enemy in state.enemies_of(actor)
        ]
        return hexes.figure_engaged(actor.footprint, actor.size_hexes, enemies)

    def threshold(self, actor: CombatantState) -> int:
        return hexes.engagement_threshold(actor.size_hexes)


class MeleeStyleEngagement(EngagementRules):
    """Melee's structural engagement (``engine/facing.py``, Section VI).

    One-directional: you are engaged only by a foe whose front hex you
    occupy — a figure behind or beside an enemy stays free to move and
    strike, while two figures face to face are each engaged. Downed enemies
    (prone or felled) engage no one; a large figure needs two distinct
    engagers to be pinned (one lone figure cannot stop it); a multi-hex
    engager counts as one engager like any other.

    ``needs_two`` decides which figures take two engagers (default: any
    multi-hex footprint). ``exempt`` marks figures that are never engaged at
    all (melee's airborne figures); the default exempts no one.
    """

    def __init__(
        self,
        *,
        needs_two: Callable[[CombatantState], bool] | None = None,
        exempt: Callable[[CombatantState], bool] | None = None,
    ) -> None:
        self._needs_two = needs_two or (lambda actor: actor.size_hexes > 1)
        self._exempt = exempt or (lambda actor: False)

    def counts_as_engager(self, enemy: CombatantState) -> bool:
        return enemy.active and not enemy.prone

    def threshold(self, actor: CombatantState) -> int:
        return 2 if self._needs_two(actor) else 1

    def is_engaged(self, state: BattleState, actor: CombatantState) -> bool:
        if self._exempt(actor):
            return False
        return self._count_based_engagement(state, actor)
