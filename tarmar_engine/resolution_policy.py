"""Resolution policies — the "how an attack roll works" area of the seam.

Melee's ``Ruleset`` proved that resolution is policy, not structure: the same
turn engine can run classic 3d6 roll-under or Tarmar d20 roll-over mechanics
behind one seam. This module ports that idea onto :class:`.profile.RulesProfile`
as a third kind of seam component (beside the structural areas milestone 2
added): a :class:`ResolutionPolicy` names the to-hit roll, its direction, and
the primitives a profile's machinery reads.

:class:`TarmarResolution` is the default and changes nothing — it points at the
same drift-guarded ``tarmar_rules`` core the TurnRunner already calls
(re-exported through :mod:`.resolution`), so the seeded equivalence test stays
byte-identical. The classic 3d6 policy lives in
``tarmar_engine.classic.resolution`` with the rest of the SJG-derived
mechanics, per the unification plan's copyright note.
"""

from __future__ import annotations

from . import resolution


class ResolutionPolicy:
    """One game's attack-resolution identity, selected as a unit.

    Attributes:
        name: A short id for logs and tests.
        attack_dice: The to-hit roll, dice-notation (``"1d20"`` / ``"3d6"``).
        roll_under: ``True`` when a hit is rolling **at or under** a figure's
            own adjusted score (classic Melee's 3d6 vs adjDX); ``False`` when
            a hit is rolling **at or over** a target number (Tarmar's d20 vs
            the TN matrix). Narration and UIs read this to print the check in
            the right direction.
    """

    name: str = ""
    attack_dice: str = ""
    roll_under: bool = False

    def attack_dice_count(
        self, *, dodging: bool, defending: bool, ranged: bool
    ) -> int:
        """How many dice the to-hit roll uses against this defender.

        Only meaningful for a pool-count resolution (classic Melee's
        dodge/defend four-dice rule); a fixed-die policy returns 1.
        """
        raise NotImplementedError

    def classify_roll(
        self, rolled: int, dice_count: int, needed: int
    ) -> tuple[bool, int, bool, bool]:
        """Map a to-hit total to ``(hit, multiplier, dropped, broke)``."""
        raise NotImplementedError


class TarmarResolution(ResolutionPolicy):
    """The d20 roll-over policy — the ``tarmar_rules`` core, unchanged.

    The six-phase TurnRunner keeps calling the :mod:`.resolution` functions
    directly (the pre-seam call path, kept so the seeded equivalence test is
    trivially byte-identical); this class is the seam's *description* of that
    path and re-exposes the same primitives, so a caller holding a profile can
    discover its resolution without importing the module by name.
    """

    name = "tarmar-d20"
    attack_dice = "1d20"
    roll_under = False

    #: The drift-guarded core the TurnRunner resolves through.
    resolve_attack = staticmethod(resolution.resolve_attack)
    target_number = staticmethod(resolution.target_number)
    to_hit_bonus = staticmethod(resolution.to_hit_bonus)
    damage_after_armour = staticmethod(resolution.damage_after_armour)

    def attack_dice_count(
        self, *, dodging: bool, defending: bool, ranged: bool
    ) -> int:
        """Always one d20 — dodge/defend shift the TN, never the dice."""
        return 1
