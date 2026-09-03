"""The classic 3d6 roll-under resolution policy — the seam's second member.

Fills the :class:`~tarmar_engine.resolution_policy.ResolutionPolicy` area for
the classic profile: three dice totalled at or under the attacker's adjusted
DX, four against a dodging (missile/thrown) or defending (melee) target, with
the p.10 special totals (3/4/5 auto-hit with triple/double/plain damage; 16
auto-miss; 17 drops the weapon; 18 breaks it — shifted on four dice). Lives in
the segregated classic subpackage because the special totals are SJG rules
data (:mod:`.data`).
"""

from __future__ import annotations

from ..resolution_policy import ResolutionPolicy
from .combat import classify_roll
from .data import THREE_DICE


class ClassicResolution(ResolutionPolicy):
    """Classic *Melee* 3d6 roll-under-adjDX resolution."""

    name = "classic-3d6"
    attack_dice = "3d6"
    roll_under = True

    def attack_dice_count(
        self, *, dodging: bool, defending: bool, ranged: bool
    ) -> int:
        """Dice rolled to hit, by attack type (Melee p.20).

        A *dodging* figure is hard to hit only with a missile or thrown
        weapon; a *defending* figure only with a melee attack. Either forces
        four dice for the matching attack type, three otherwise.
        """
        if ranged and dodging:
            return 4
        if not ranged and defending:
            return 4
        return THREE_DICE

    def classify_roll(
        self, rolled: int, dice_count: int, needed: int
    ) -> tuple[bool, int, bool, bool]:
        """The p.10 special-total table over the plain roll-under check."""
        return classify_roll(rolled, dice_count, needed)
