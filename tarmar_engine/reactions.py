"""Reactions to injury — the "what does being hurt do" area of the seam.

The two games disagree on what damage does to a figure beyond the numbers:

* :class:`TarmarReactions` — the pre-seam six-phase semantics exactly:
  either pool (Fatigue or Body) at or below zero means unconscious, and a
  pool deep below zero forces a 3d6 ≤ CON survival save every turn, with a
  penalty for how far past −max the pool sits (tarmar-studio's
  ``characters.models`` injury-thresholds semantics). Death happens only by
  failing a save.
* :class:`HitCountReactions` — melee's structural reaction mechanics
  ("Reactions to Injury"): enough hits in one turn wound you (a DX penalty
  next turn — the flag rolls forward at end of turn) or knock you down;
  the single-track pool (mapped onto ``fatigue``) reaching zero fells you
  and passing the death line kills. Every threshold and penalty is
  **injected** — this class carries the mechanism only, and the classic
  profile's actual rulebook numbers arrive in milestone 3's segregated data
  module, per the unification plan's copyright note.
"""

from __future__ import annotations

import math

from .state import CombatantState

# Reaction verdicts (names shared with melee's ruleset constants).
DEAD = "dead"
UNCONSCIOUS = "unconscious"
KNOCKDOWN = "knockdown"


class InjuryReactions:
    """Base of the reactions seam.

    The hooks below are what the six-phase TurnRunner consults after damage
    and in phase 6; a reactions variant that wants to ride that runner
    implements them.
    """

    def unconscious(self, combatant: CombatantState) -> bool:
        """Is the combatant felled by its current pools?"""
        raise NotImplementedError

    def survival_save_penalty(self, combatant: CombatantState) -> int | None:
        """This turn's survival-save penalty, or ``None`` for no save."""
        raise NotImplementedError

    def survival_save_target(self, combatant: CombatantState) -> int:
        """The attribute a survival save rolls against."""
        raise NotImplementedError


class TarmarReactions(InjuryReactions):
    """Fatigue/Body pool semantics, verbatim from the pre-seam engine."""

    def unconscious(self, combatant: CombatantState) -> bool:
        """Either pool at or below zero fells the combatant."""
        return combatant.fatigue <= 0 or combatant.body <= 0

    def survival_save_penalty(self, combatant: CombatantState) -> int | None:
        """The turn's survival-save penalty, or ``None`` when no save is due.

        A pool at or below −ceil(max/2) forces a save every turn; past −max
        the save is penalized by how far past that threshold the pool sits.
        The worst pool governs.
        """
        worst_penalty: int | None = None
        for pool_value, pool_maximum in (
            (combatant.fatigue, combatant.max_fatigue),
            (combatant.body, combatant.max_body),
        ):
            save_at = -math.ceil(pool_maximum / 2)
            if pool_value > save_at:
                continue
            penalized_at = -pool_maximum
            penalty = max(0, penalized_at - pool_value)
            if worst_penalty is None or penalty > worst_penalty:
                worst_penalty = penalty
        return worst_penalty

    def survival_save_target(self, combatant: CombatantState) -> int:
        """The save rolls 3d6 at or under CON."""
        return combatant.constitution


class HitCountReactions(InjuryReactions):
    """Melee's reaction structure, thresholds injected by the caller.

    Args:
        knockdown_hits: Hits in one turn that knock the figure down.
        wound_hits: Hits in one turn that wound it (DX penalty next turn).
        wound_dx_penalty: Magnitude of that one-turn penalty (positive).
        unconscious_at: Pool value at or below which the figure is felled.
        dead_at: Pool value at or below which it is dead.
        low_pool_threshold: Pool value at or below which a lasting DX
            penalty applies (``None`` disables the rule).
        low_pool_dx_penalty: Magnitude of the lasting penalty (positive).
    """

    def __init__(
        self,
        *,
        knockdown_hits: int,
        wound_hits: int,
        wound_dx_penalty: int,
        unconscious_at: int = 0,
        dead_at: int = -1,
        low_pool_threshold: int | None = None,
        low_pool_dx_penalty: int = 0,
    ) -> None:
        self.knockdown_hits = knockdown_hits
        self.wound_hits = wound_hits
        self.wound_dx_penalty = wound_dx_penalty
        self.unconscious_at = unconscious_at
        self.dead_at = dead_at
        self.low_pool_threshold = low_pool_threshold
        self.low_pool_dx_penalty = low_pool_dx_penalty

    def pool(self, combatant: CombatantState) -> int:
        """The single damage track. Mapped onto ``fatigue`` — the classic
        profile runs one pool, and fatigue is the package pool that drains
        first and fells at zero."""
        return combatant.fatigue

    def status_after_hit(self, combatant: CombatantState) -> str | None:
        """The verdict after a damaging hit, worst first."""
        if self.pool(combatant) <= self.dead_at:
            return DEAD
        if self.pool(combatant) <= self.unconscious_at:
            return UNCONSCIOUS
        if combatant.hits_this_turn >= self.knockdown_hits:
            return KNOCKDOWN
        return None

    def dx_penalty(self, combatant: CombatantState) -> int:
        """The figure's current injury DX penalty, as a number ≤ 0."""
        penalty = 0
        if combatant.wounded_last_turn:
            penalty -= self.wound_dx_penalty
        if (
            self.low_pool_threshold is not None
            and self.pool(combatant) <= self.low_pool_threshold
        ):
            penalty -= self.low_pool_dx_penalty
        return penalty

    def end_of_turn(self, combatant: CombatantState) -> None:
        """Roll the wound flag forward: this turn's hits set next turn's
        penalty (melee's ``end_turn`` bookkeeping)."""
        combatant.wounded_last_turn = combatant.hits_this_turn >= self.wound_hits
