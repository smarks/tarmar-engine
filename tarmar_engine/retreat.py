"""Forced-retreat rules — the phase-6 area of the profile seam.

Both games end a turn by letting a figure that hurt an enemy and came
through unhurt shove that enemy back one hex — but the eligibility and the
blocked-hex outcome differ, so this is a profile hook rather than shared
code:

* :class:`TarmarForcedRetreat` — special-combat-situations.md, unchanged
  from the pre-seam engine: eligibility is dealt-any-damage-and-took-none,
  the victim is the turn's chosen target, and a victim with no retreat hex
  rolls 3d6 ≤ DEX or falls prone. The TurnRunner keeps doing the pushing
  and event emission; this class supplies the decisions.
* :class:`MeleeStyleForcedRetreat` — melee's structural mechanics
  (``engine/state.py`` ``_ForceRetreatMixin``): only *melee* damage arms a
  push, per specific target hit (a missile or thrown hit never does), each
  push is spent once, the pusher may optionally advance into the vacated
  hex, and a victim with nowhere to go simply is not pushed — no saving
  roll. Ported operating on the package's own state types; melee's
  side-vs-side condition has no counterpart in the free-for-all package
  state and returns with team state (milestone 4).
"""

from __future__ import annotations

from . import hexes
from .state import BattleState, CombatantState


class IllegalAction(ValueError):
    """A rules-forbidden retreat action (name matches melee's exception)."""


class ForcedRetreatRules:
    """Base of the forced-retreat seam.

    The hooks below are what the six-phase TurnRunner consults each phase 6;
    a retreat variant that wants to ride that runner implements them (the
    melee-style variant instead exposes melee's own verb set for the classic
    runner arriving in milestone 3).
    """

    #: Roll specification for a blocked victim's save ("" = no save exists).
    blocked_save_dice: str = ""

    def blocked_save_target(self, victim: CombatantState) -> int:
        """The number a blocked victim must roll at or under."""
        raise NotImplementedError

    def pusher_eligible(self, combatant: CombatantState) -> bool:
        """May this combatant force a retreat at the turn's end?"""
        raise NotImplementedError

    def victim_of(
        self, state: BattleState, combatant: CombatantState
    ) -> CombatantState | None:
        """Whom the eligible pusher shoves, or ``None``."""
        raise NotImplementedError


class TarmarForcedRetreat(ForcedRetreatRules):
    """The six-phase engine's phase-6 semantics, verbatim."""

    #: A victim with no clear retreat hex rolls this to keep its feet…
    blocked_save_dice: str = "3d6"

    def blocked_save_target(self, victim: CombatantState) -> int:
        """…at or under this number (DEX), or falls prone."""
        return victim.dexterity

    def pusher_eligible(self, combatant: CombatantState) -> bool:
        """Dealt damage this turn, took none, and is not in a grapple.

        hand-to-hand-and-grappling.md exempts both sides of a hold: there is
        no hex to push someone into while you're holding them.
        """
        if hexes.figure_locked_by_grapple(combatant.grappled_by, combatant.grappling):
            return False
        return combatant.dealt_damage_this_turn and not combatant.took_damage_this_turn

    def victim_of(
        self, state: BattleState, combatant: CombatantState
    ) -> CombatantState | None:
        """The turn's chosen target, if it is still a pushable mark.

        Alive, adjacent, and not grapple-locked — the grapple exemption
        holds even when a third party dealt the damage ("against — or to —
        a grappled figure").
        """
        target_id = combatant.chosen_target
        if target_id is None:
            return None
        victim = state.by_id(target_id)
        if not victim.alive or not hexes.figures_adjacent(
            combatant.footprint, victim.footprint
        ):
            return None
        if hexes.figure_locked_by_grapple(victim.grappled_by, victim.grappling):
            return None
        return victim


class MeleeStyleForcedRetreat(ForcedRetreatRules):
    """Melee's "Forcing Retreat" structure (p.20 mechanics, no rules data).

    The three verbs mirror melee's mixin: :meth:`record_hit` arms a push
    when a damaging melee blow lands, :meth:`can_force_retreat` is the one
    gate both a menu and an execution path share, and :meth:`force_retreat`
    performs the shove (raising :class:`IllegalAction` when the gate or the
    geometry refuses).
    """

    def record_hit(
        self,
        attacker: CombatantState,
        target: CombatantState,
        *,
        damage: int,
        ranged: bool,
    ) -> None:
        """Arm a push against ``target`` for a damaging melee hit.

        Missile/thrown hits deal their damage but never arm a force retreat,
        and a blow armour stopped entirely arms nothing. Each struck foe is
        recorded once; :meth:`force_retreat` spends the entry.
        """
        if damage <= 0 or ranged:
            return
        if target.combatant_id not in attacker.retreat_push_targets_this_turn:
            attacker.retreat_push_targets_this_turn.append(target.combatant_id)

    def can_force_retreat(
        self,
        state: BattleState,
        attacker: CombatantState,
        target: CombatantState,
    ) -> bool:
        """Whether ``attacker`` may still shove ``target`` back one hex.

        The armed entry encodes "dealt qualifying melee damage to THIS foe,
        push not yet spent"; the rest is checked here: the attacker took no
        hits this turn (from any source), the target is adjacent, still up,
        and not locked in a grapple.
        """
        return (
            target.combatant_id in attacker.retreat_push_targets_this_turn
            and attacker.hits_this_turn == 0
            and target.active
            and not hexes.figure_locked_by_grapple(
                target.grappled_by, target.grappling
            )
            and hexes.figures_adjacent(attacker.footprint, target.footprint)
        )

    def force_retreat(
        self,
        state: BattleState,
        attacker: CombatantState,
        target: CombatantState,
        *,
        advance: bool = False,
    ) -> tuple[int, int]:
        """Push ``target`` one hex farther from ``attacker``; maybe follow.

        The destination must be a neighbour strictly farther from the
        attacker whose whole footprint lands in-arena and unoccupied (a
        shove can never overlap a figure or slide part of a multi-hex body
        off the map). Ties settle on the farthest hex, then the hex's own
        ``(q, r)`` — deterministic, never iteration order. No destination
        means no push (and no saving roll — that is Tarmar's mechanic).
        """
        if not self.can_force_retreat(state, attacker, target):
            raise IllegalAction("force retreat not allowed")
        occupied = state.occupied_hexes() - set(target.footprint)
        start_distance = hexes.distance(attacker.position, target.position)

        def footprint_fits(anchor: tuple[int, int]) -> bool:
            return all(
                cell not in occupied and hexes.in_arena(cell, state.arena_radius)
                for cell in hexes.footprint(anchor, target.facing, target.size_hexes)
            )

        destinations = [
            candidate
            for candidate in hexes.neighbors(target.position)
            if hexes.distance(attacker.position, candidate) > start_distance
            and footprint_fits(candidate)
        ]
        if not destinations:
            raise IllegalAction("no hex to retreat into")
        chosen = max(
            destinations,
            key=lambda candidate: (
                hexes.distance(attacker.position, candidate),
                candidate[0],
                candidate[1],
            ),
        )
        vacated = target.position
        target.position = chosen
        if advance:
            attacker.position = vacated
        # Spend the push: one shove per qualifying hit, never a chain.
        if target.combatant_id in attacker.retreat_push_targets_this_turn:
            attacker.retreat_push_targets_this_turn.remove(target.combatant_id)
        return target.position
