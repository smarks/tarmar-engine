"""SJG-DERIVED CLASSIC *MELEE* — the segregated classic profile subpackage.

Everything derived from Steve Jackson Games' *The Fantasy Trip: Melee*
(3rd ed.) lives under this subpackage and nowhere else: the rules data
(:mod:`.data` — the one segregated data module), the classic combat machinery
ported from the melee project's engine (figure, arena, facing, combat,
ruleset, game state), the 3d6 roll-under resolution policy, and the runnable
:data:`~.profile.CLASSIC_MELEE` rules profile.

Per the unification plan's copyright note, no Tarmar-canon module may import
from here (guarded by ``tests/test_classic_profile.py``); the one sanctioned
entry is ``tarmar_engine.profile.get_profile("classic-melee")``, which loads
this subpackage lazily at the caller's explicit request.

Importing the subpackage registers the classic profile.
"""

from .profile import CLASSIC_MELEE, ClassicMeleeProfile
from .resolution import ClassicResolution

__all__ = ["CLASSIC_MELEE", "ClassicMeleeProfile", "ClassicResolution"]
