"""The classic option catalog (Section IV) — a thin face over the shared seam.

Melee's ``engine/options.py`` and milestone 2's
:func:`tarmar_engine.options.melee_structure_catalog` describe the same
taxonomy; the shared catalog's keys are exactly melee's ``Option`` enum
values, chosen for this moment. This module therefore adds only what the
ported classic machinery needs — the :class:`Option` enum itself and melee's
``spec()``/``options_for()`` call shapes — and reads every fact (contexts,
movement caps, attack/missile/dodge/defend flags) from the ONE shared
catalog, so the classic profile and the melee-structure seam cannot drift.
"""

from __future__ import annotations

from enum import StrEnum

from ..options import ANY, DISENGAGED, ENGAGED, SPECIAL, OptionSpec
from ..options import melee_structure_catalog as _melee_structure_catalog

__all__ = [
    "ANY",
    "DISENGAGED",
    "ENGAGED",
    "SPECIAL",
    "Option",
    "OptionSpec",
    "options_for",
    "spec",
]

#: The one melee-structure catalog instance the classic machinery reads.
CATALOG = _melee_structure_catalog()


class Option(StrEnum):
    """Melee's option identities; each value is a shared-catalog key."""

    MOVE = "move"                      # (a) move up to full MA
    HALF_MOVE = "half_move"            # (a') move up to half MA, no attack
    CHARGE_ATTACK = "charge_attack"    # (b) move <= half MA, then attack
    DODGE = "dodge"                    # (c) move <= half MA while dodging
    READY_WEAPON = "ready_weapon"      # (e) move <= 2, swap ready weapon
    MISSILE_ATTACK = "missile_attack"  # (f) move <= 1, fire a missile weapon
    STAND_UP = "stand_up"              # (g) rise from prone/kneeling
    CRAWL = "crawl"                    # (g) crawl <= 2 hexes instead
    ATTACK = "attack"                  # (j) stand still, strike adjacent
    SHIFT_ATTACK = "shift_attack"      # (j) shift 1, attack
    SHIFT_DEFEND = "shift_defend"      # (k) shift 1, defend
    ONE_LAST_SHOT = "one_last_shot"    # (l) one last missile shot
    CHANGE_WEAPONS = "change_weapons"  # (m) shift 1, swap to a non-missile
    DISENGAGE = "disengage"            # (n) move away from engaging enemies
    HTH_ATTACK = "hth_attack"          # (b/o) grapple hand-to-hand
    CAST = "cast"                      # (h/r) a wizard casts a spell
    PICK_UP = "pick_up"                # (q) take a dropped weapon in reach
    GO_PRONE = "go_prone"              # (f) drop prone
    KNEEL = "kneel"                    # (f) drop to one knee
    DO_NOTHING = "do_nothing"          # hold: a real, legal no-op
    PASS = "pass"                      # defer: choose last


def spec(option: Option) -> OptionSpec:
    """The shared catalog's structural facts for ``option``."""
    return CATALOG.spec(option.value)


def options_for(*, engaged: bool) -> list[Option]:
    """Legal options for a standing figure given whether it is engaged."""
    return [Option(key) for key in CATALOG.options_for(engaged=engaged)]
