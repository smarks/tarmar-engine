"""Rules profiles — the single seam for swapping structural mechanics.

Melee proved the pattern one level down (its ``engine/ruleset.py`` swaps
*resolution* mechanics behind a ``Ruleset`` of hooks); this module ports the
idea up to turn *structure*. A :class:`RulesProfile` bundles everything the
engine treats as game-identity rather than machinery:

* **turn structure** — the phase table and the turn runner itself,
* **the option catalog** — what a figure may do each turn,
* **facing/engagement** — who is pinned by whom (arc math is shared, see
  :mod:`.engagement`),
* **forced retreat** — who pushes whom at the turn's end, and what happens
  when there is nowhere to go,
* **reactions to injury** — what being hurt does beyond the subtraction,
* **grapple/HTH** — the hold's movement lock, its turn-choice vocabularies,
  and the shared-hex to-hit bonus.

:data:`TARMAR` (a :class:`TarmarProfile`) is the default everywhere and
reproduces the pre-seam six-phase engine exactly — the milestone-1 test
suite passing unchanged is the acceptance bar. :class:`MeleeStructureProfile`
wires the melee-style structural variants together; it deliberately has no
turn runner yet (and none of the classic rulebook's numbers — both arrive
with milestone 3's classic profile and its segregated data module).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from . import actions, hexes
from .engagement import EngagementRules, MeleeStyleEngagement, TarmarEngagement
from .options import OptionCatalog, melee_structure_catalog, tarmar_catalog
from .reactions import HitCountReactions, InjuryReactions, TarmarReactions
from .retreat import ForcedRetreatRules, MeleeStyleForcedRetreat, TarmarForcedRetreat
from .state import BattleState


class GrappleRules:
    """The grapple/HTH area of the seam.

    Carries the hold's structural facts: whether a held pair may move, the
    turn-choice vocabularies that replace the normal menus once a grapple is
    live, and the to-hit bonus both sides get at HTH range.
    """

    grappled_actions: Mapping[str, str] = {}
    grappler_actions: Mapping[str, str] = {}
    to_hit_bonus: int = 0

    def locks_movement(
        self, grappled_by: int | None, grappling: int | None
    ) -> bool:
        """Is a figure held in place by a grapple (either side of the hold)?"""
        return grappled_by is not None or grappling is not None


class TarmarGrapple(GrappleRules):
    """hand-to-hand-and-grappling.md's facts, from the pre-seam constants."""

    grappled_actions = actions.GRAPPLED_ACTIONS
    grappler_actions = actions.GRAPPLER_ACTIONS
    to_hit_bonus = hexes.HTH_TO_HIT_BONUS

    def locks_movement(
        self, grappled_by: int | None, grappling: int | None
    ) -> bool:
        return hexes.figure_locked_by_grapple(grappled_by, grappling)


# The melee-structure turn shape (melee's engine/state.py turn verbs):
# figures select options and move in initiative order, attacks resolve in
# adjDX order, retreats are forced, and the turn's flags settle at the end.
MELEE_STRUCTURE_PHASES: tuple[tuple[int, str], ...] = (
    (1, "Movement"),
    (2, "Attacks"),
    (3, "Forced Retreat"),
    (4, "End of Turn"),
)


class RulesProfile:
    """One game's structural mechanics, selected as a unit.

    Subclasses (or callers constructing the base directly) supply the five
    seam components; :meth:`run_turn` is the turn-structure entry and must
    be provided by a runnable profile.
    """

    def __init__(
        self,
        *,
        name: str = "",
        phases: tuple[tuple[int, str], ...] = (),
        engagement: EngagementRules | None = None,
        catalog: OptionCatalog | None = None,
        retreat: ForcedRetreatRules | None = None,
        reactions: InjuryReactions | None = None,
        grapple: GrappleRules | None = None,
    ) -> None:
        self.name = name
        self._phases = phases
        self.engagement: EngagementRules = engagement or EngagementRules()
        self.catalog: OptionCatalog = catalog or OptionCatalog(())
        self.retreat: ForcedRetreatRules = retreat or ForcedRetreatRules()
        self.reactions: InjuryReactions = reactions or InjuryReactions()
        self.grapple: GrappleRules = grapple or GrappleRules()

    @property
    def phases(self) -> tuple[tuple[int, str], ...]:
        return self._phases

    def run_turn(
        self, state: BattleState, roller, sink, choose_option
    ) -> None:
        """Run one full turn under this profile's structure."""
        raise NotImplementedError(f"profile {self.name!r} has no turn runner")


class TarmarProfile(RulesProfile):
    """The six-phase Tarmar engine, unchanged — the package default."""

    def __init__(self) -> None:
        super().__init__(
            name="tarmar",
            engagement=TarmarEngagement(),
            catalog=tarmar_catalog(),
            retreat=TarmarForcedRetreat(),
            reactions=TarmarReactions(),
            grapple=TarmarGrapple(),
        )

    @property
    def phases(self) -> tuple[tuple[int, str], ...]:
        from . import engine  # runner import deferred: engine imports this module

        return engine.PHASES

    def legal_actions(self, **situation) -> list[str]:
        """The lettered legality filter (``actions.legal_actions``)."""
        return actions.legal_actions(**situation)

    def run_turn(
        self, state: BattleState, roller, sink, choose_option
    ) -> None:
        from . import engine  # deferred for the same one-way-import reason

        engine.TurnRunner(state, roller, sink, profile=self).run(choose_option)


class MeleeStructureProfile(RulesProfile):
    """Melee's structural mechanics wired together, data-free.

    ``reactions`` must be supplied because :class:`HitCountReactions` takes
    injected thresholds — this package ships the mechanism, milestone 3's
    classic data module ships the numbers. The turn runner also arrives with
    milestone 3 (its acceptance test is the rulebook's nine-turn Combat
    Example), so :meth:`run_turn` raises until then.
    """

    def __init__(
        self,
        *,
        reactions: HitCountReactions,
        needs_two: Callable | None = None,
        exempt: Callable | None = None,
        grapple: GrappleRules | None = None,
    ) -> None:
        super().__init__(
            name="melee-structure",
            phases=MELEE_STRUCTURE_PHASES,
            engagement=MeleeStyleEngagement(needs_two=needs_two, exempt=exempt),
            catalog=melee_structure_catalog(),
            retreat=MeleeStyleForcedRetreat(),
            reactions=reactions,
            grapple=grapple or GrappleRules(),
        )

    def run_turn(
        self, state: BattleState, roller, sink, choose_option
    ) -> None:
        raise NotImplementedError(
            "the classic profile's turn runner arrives in milestone 3"
        )


#: The default profile — every engine entrypoint that takes no profile uses it.
TARMAR = TarmarProfile()

#: Registered, runnable profiles by name. The classic Melee profile joins in
#: milestone 3 once its data module and turn runner exist.
PROFILES: dict[str, RulesProfile] = {TARMAR.name: TARMAR}


def get_profile(name: str) -> RulesProfile:
    """Look up a registered profile; raises ``KeyError`` for an unknown name."""
    return PROFILES[name]
