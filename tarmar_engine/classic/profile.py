"""The runnable CLASSIC MELEE profile — milestone 3 of the unification plan.

:class:`ClassicMeleeProfile` completes what milestone 2 left deliberately
abstract: it is the :class:`~tarmar_engine.profile.MeleeStructureProfile`
(melee-style engagement, the shared option taxonomy, per-target forced-retreat
entitlements, hit-count injury reactions) wired with the CLASSIC RULES DATA
from the segregated :mod:`.data` module — the rulebook's actual thresholds —
plus the classic 3d6 roll-under :class:`.resolution.ClassicResolution` and a
real turn runner.

The runner drives the classic :class:`.state.GameState` through melee's turn
shape (the ``MELEE_STRUCTURE_PHASES`` table): initiative-ordered option
selection and movement, attacks resolved in adjDX order, forced retreats, and
the end-of-turn settling. Its acceptance test is the rulebook's nine-turn
Combat Example (``tests/test_combat_example.py``), imported verbatim from
melee.

Importing this module registers the profile under
:data:`~tarmar_engine.profile.CLASSIC_MELEE_NAME`;
``tarmar_engine.profile.get_profile("classic-melee")`` does so lazily, so
Tarmar-canon code never touches the SJG-derived subpackage unasked.
"""

from __future__ import annotations

from collections.abc import Callable

from hexarena.dice import Dice

from ..profile import CLASSIC_MELEE_NAME, PROFILES, MeleeStructureProfile
from . import policy
from .data import classic_reactions
from .resolution import ClassicResolution
from .state import GameState, IllegalAction


class ClassicMeleeProfile(MeleeStructureProfile):
    """Classic *The Fantasy Trip: Melee* (3rd ed.) as a rules profile."""

    def __init__(self) -> None:
        super().__init__(
            reactions=classic_reactions(),
            resolution=ClassicResolution(),
        )
        self.name = CLASSIC_MELEE_NAME

    def run_turn(self, state, roller, sink, choose_option) -> None:
        """Run one full classic turn over a :class:`.state.GameState`.

        Args:
            state: The classic :class:`.state.GameState` (turn structure is
                profile identity, so each profile runs over its own state
                type — see :meth:`RulesProfile.run_turn`).
            roller: A ``hexarena.dice.Dice`` to install as the game's dice
                source, or ``None`` to keep the state's own.
            sink: Called once per new log line the turn produced.
            choose_option: ``choose_option(game, figure)``, in either of two
                shapes. **Menu-driven** (tarmar-engine#3, what an app with a
                UI wants): return a
                :class:`~tarmar_engine.policy.Candidate` off
                :func:`.policy.menu` and this runner enacts it — the same
                path whether the AI or a player picked it. **Direct**
                (melee's own board, and mechanics tests): drive the game's
                verbs yourself (``game.move(...)``, direct option assignment
                plus ``game.queue_attack(...)``, ``game.pass_action(...)``)
                and return ``None``. A figure left with no option either way
                is set to DO NOTHING so the turn always completes.

        The phase walk is the profile's own ``phases`` table: (1) Movement —
        initiative-ordered selection via :meth:`GameState.begin_selection` /
        ``active_character``; (2) Attacks — :meth:`GameState.resolve_combat`
        (adjDX order); (3) Forced Retreat — every armed, eligible push is
        taken (no advance; a driver wanting the optional follow-up calls
        ``force_retreat`` itself before ``run_turn``'s phase 3 would); (4)
        End of Turn — :meth:`GameState.end_turn`.
        """
        game: GameState = state
        if isinstance(roller, Dice):
            game.dice = roller
        log_mark = len(game.log)

        # Phase 1 — Movement: initiative-ordered option selection.
        game.begin_selection()
        while (figure := game.active_character()) is not None:
            picked = choose_option(game, figure)
            if picked is not None:
                policy.enact(game, figure, picked)
            if figure.current_option is None and game.active_character() is figure:
                game.set_do_nothing(figure)

        # Phase 2 — Attacks. Declared only now, once every figure has moved, so
        # a blow is aimed at where its target actually stands (melee declares
        # attacks in its own combat phase for exactly this reason); a figure
        # whose driver queued its attack directly is left alone. Then they
        # resolve in adjDX order.
        policy.declare_attacks(game)
        game.resolve_combat()

        # Phase 3 — Forced Retreat: spend every armed, still-legal push.
        for figure in game.living():
            for target_uid in list(figure.force_retreat_targets_this_turn):
                target = game._figure_by_uid(target_uid)
                if target is None or not game.can_force_retreat(figure, target):
                    continue
                try:
                    game.force_retreat(figure, target)
                except IllegalAction:
                    continue  # boxed in: no hex to retreat into means no push

        # Phase 4 — End of Turn: injury flags settle, per-turn state resets.
        game.end_turn()

        for line in game.log[log_mark:]:
            sink(line)


#: The one shared instance, registered for ``get_profile("classic-melee")``.
CLASSIC_MELEE = ClassicMeleeProfile()
PROFILES.setdefault(CLASSIC_MELEE.name, CLASSIC_MELEE)

ChooseOption = Callable[[GameState, object], None]
