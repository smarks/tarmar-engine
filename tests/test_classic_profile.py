"""The classic Melee profile: registry, wiring, the resolution seam, the
turn runner, and the SJG-data segregation guard."""
# pyright: reportArgumentType=false
# (the runner test places figures then reads positions, melee-style)

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import TestCase

from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic import CLASSIC_MELEE, ClassicMeleeProfile
from tarmar_engine.classic.data import (
    BROADSWORD,
    KNOCKDOWN_HITS,
    LOW_ST_DX_PENALTY,
    LOW_ST_THRESHOLD,
    WOUND_DX_PENALTY,
    WOUND_HITS_THRESHOLD,
)
from tarmar_engine.classic.figure import Posture, create_human
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.resolution import ClassicResolution
from tarmar_engine.classic.state import GameState
from tarmar_engine.options import melee_structure_catalog
from tarmar_engine.profile import (
    CLASSIC_MELEE_NAME,
    MELEE_STRUCTURE_PHASES,
    TARMAR,
    get_profile,
)
from tarmar_engine.reactions import HitCountReactions
from tarmar_engine.resolution import resolve_attack as tarmar_resolve_attack
from tarmar_engine.resolution_policy import ResolutionPolicy, TarmarResolution

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "tarmar_engine"


class RegistryTest(TestCase):
    def test_classic_profile_is_registered_and_lazily_loadable(self):
        profile = get_profile(CLASSIC_MELEE_NAME)
        self.assertIs(profile, CLASSIC_MELEE)
        self.assertIsInstance(profile, ClassicMeleeProfile)
        # The default is untouched.
        self.assertIs(get_profile("tarmar"), TARMAR)


class WiringTest(TestCase):
    """The classic profile = melee-structure mechanisms + rulebook numbers."""

    def test_reactions_carry_the_rulebook_thresholds(self):
        reactions = CLASSIC_MELEE.reactions
        self.assertIsInstance(reactions, HitCountReactions)
        self.assertEqual(reactions.knockdown_hits, KNOCKDOWN_HITS)
        self.assertEqual(reactions.wound_hits, WOUND_HITS_THRESHOLD)
        self.assertEqual(reactions.wound_dx_penalty, -WOUND_DX_PENALTY)
        self.assertEqual(reactions.unconscious_at, 0)
        self.assertEqual(reactions.dead_at, -1)
        self.assertEqual(reactions.low_pool_threshold, LOW_ST_THRESHOLD)
        self.assertEqual(reactions.low_pool_dx_penalty, -LOW_ST_DX_PENALTY)

    def test_structure_is_the_melee_structure_seam(self):
        self.assertEqual(CLASSIC_MELEE.phases, MELEE_STRUCTURE_PHASES)
        self.assertEqual(
            set(CLASSIC_MELEE.catalog.keys()),
            set(melee_structure_catalog().keys()),
        )
        # The classic Option enum's values ARE the shared catalog's keys.
        self.assertEqual(
            {option.value for option in Option},
            set(melee_structure_catalog().keys()),
        )


class ResolutionSeamTest(TestCase):
    """The new resolution area of the profile seam."""

    def test_tarmar_default_is_the_d20_core(self):
        self.assertIsInstance(TARMAR.resolution, TarmarResolution)
        self.assertEqual(TARMAR.resolution.attack_dice, "1d20")
        self.assertFalse(TARMAR.resolution.roll_under)
        # The policy names the exact tarmar-rules core the runner calls.
        self.assertIs(TarmarResolution.resolve_attack, tarmar_resolve_attack)
        self.assertEqual(
            TARMAR.resolution.attack_dice_count(
                dodging=True, defending=True, ranged=True
            ),
            1,
        )

    def test_classic_profile_resolves_by_3d6_roll_under(self):
        policy = CLASSIC_MELEE.resolution
        self.assertIsInstance(policy, ClassicResolution)
        self.assertEqual(policy.attack_dice, "3d6")
        self.assertTrue(policy.roll_under)

    def test_classic_dodge_defend_dice_matrix(self):
        policy = ClassicResolution()
        cases = [
            # (dodging, defending, ranged) -> dice
            ((False, False, False), 3),
            ((False, False, True), 3),
            ((True, False, True), 4),   # dodge counters missiles
            ((True, False, False), 3),  # ...but not melee
            ((False, True, False), 4),  # defend counters melee
            ((False, True, True), 3),   # ...but not missiles
        ]
        for (dodging, defending, ranged), expected in cases:
            self.assertEqual(
                policy.attack_dice_count(
                    dodging=dodging, defending=defending, ranged=ranged
                ),
                expected,
                (dodging, defending, ranged),
            )

    def test_classic_specials_via_the_policy(self):
        policy = ClassicResolution()
        self.assertEqual(policy.classify_roll(3, 3, 4), (True, 3, False, False))
        self.assertEqual(policy.classify_roll(17, 3, 18), (False, 0, True, False))
        self.assertEqual(policy.classify_roll(18, 3, 18), (False, 0, False, True))
        self.assertEqual(policy.classify_roll(5, 4, 3), (True, 2, False, False))
        self.assertEqual(policy.classify_roll(23, 4, 25), (False, 0, False, True))
        # Plain totals fall back to roll-under.
        self.assertEqual(policy.classify_roll(10, 3, 10), (True, 1, False, False))
        self.assertEqual(policy.classify_roll(11, 3, 10), (False, 1, False, False))

    def test_base_policy_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            ResolutionPolicy().attack_dice_count(
                dodging=False, defending=False, ranged=False
            )


class RunnerTest(TestCase):
    """The classic turn runner drives a full melee-structure turn."""

    def run_one_turn(self):
        from tarmar_engine.classic.arena import Arena

        fighter_a = create_human(
            "A", 12, 12, "a", weapons=[BROADSWORD], ready_weapon=BROADSWORD)
        fighter_b = create_human(
            "B", 12, 12, "b", weapons=[BROADSWORD], ready_weapon=BROADSWORD)
        arena = Arena(cols=9, rows=15)
        fighter_a.position = Hex(5, 5)
        fighter_b.position = arena.layout.neighbor(Hex(5, 5), 0)
        fighter_a.facing = arena.layout.direction_to(
            fighter_a.position, fighter_b.position)
        fighter_b.facing = arena.layout.direction_to(
            fighter_b.position, fighter_a.position)
        game = GameState(arena, [fighter_a, fighter_b])

        def choose_option(state: GameState, figure) -> None:
            enemy = state.enemies_of(figure)[0]
            figure.current_option = Option.SHIFT_ATTACK
            try:
                state.queue_attack(figure, enemy)
            except Exception:
                figure.current_option = Option.DO_NOTHING

        events: list[str] = []
        # A hits for 9 (knockdown at 8+); B never gets to roll (floored prone).
        CLASSIC_MELEE.run_turn(
            game, Dice(scripted=[2, 3, 3, 5, 4]), events.append, choose_option)
        return game, fighter_a, fighter_b, events

    def test_a_full_turn_resolves_pushes_and_settles(self):
        game, fighter_a, fighter_b, events = self.run_one_turn()
        self.assertEqual(fighter_b.damage_taken, 9)
        self.assertEqual(fighter_b.posture, Posture.PRONE)   # 9 hits >= 8: felled
        # Phase 3 spent A's armed push: B was shoved a hex back.
        self.assertEqual(
            game.arena.distance(fighter_a.position, fighter_b.position), 2)
        # Phase 4 settled the turn: flags rolled forward and reset.
        self.assertEqual(game.turn_number, 2)
        self.assertTrue(fighter_b.wounded_last_turn)          # 9 >= 5
        self.assertEqual(fighter_b.hits_this_turn, 0)
        self.assertIsNone(fighter_a.current_option)
        self.assertTrue(events)                               # the sink saw the log


class SegregationGuardTest(TestCase):
    """The copyright note, enforced: SJG-derived classic data never leaks."""

    def test_no_canon_module_imports_the_classic_subpackage(self):
        import_markers = (
            "from .classic",
            "from tarmar_engine.classic",
            "import tarmar_engine.classic",
            "from . import classic",
        )
        offenders = []
        for module_path in sorted(PACKAGE_ROOT.glob("*.py")):
            if module_path.name == "profile.py":
                # The one sanctioned reference: get_profile's lazy loader.
                continue
            source = module_path.read_text()
            if any(marker in source for marker in import_markers):
                offenders.append(module_path.name)
        self.assertEqual(offenders, [])

    def test_importing_canon_modules_never_loads_classic(self):
        code = (
            "import sys\n"
            "import tarmar_engine\n"
            "import tarmar_engine.engine\n"
            "import tarmar_engine.profile\n"
            "import tarmar_engine.policy\n"
            "import tarmar_engine.resolution_policy\n"
            "leaks = [m for m in sys.modules if 'classic' in m]\n"
            "assert not leaks, leaks\n"
        )
        subprocess.run([sys.executable, "-c", code], check=True)
