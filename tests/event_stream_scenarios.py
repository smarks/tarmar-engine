"""Seeded battles recorded on tarmar-engine v0.9.21, for the True Magic work
(tarmar-engine #77).

That work moves the whole of a cast (the counter, the casting roll, the
payment, the Control Roll, the aim, the effect, the debt roll) into pieces a
working will share. Nothing a v0.9.21 battle can do may change on the way, so
``tests/fixtures/event_streams/v0_9_21.json`` holds what each battle below
logged on the v0.9.21 engine (commit e09b045), and
``test_event_stream_regression`` runs each on the current engine and compares
event for event, and the state the battle ended in.

The battles are the replay guards' own (``replay_scenarios``), run two ways:

* as they stand, under the default profile, every figure taking the AI's
  choice;
* with every figure made a mage and every magic mechanic v0.9.21 has
  injected at once, each figure taking a seeded draw from its whole menu, as a
  person might, so pushed, borrowing, countering, pressing and anchoring
  casts are all made. The AI makes none of those.

The magic numbers are the tests' invented ones, never the rulebook's.

A fixture stores a digest of each event, with its type and message so a
divergence can be read; the digest covers the whole event, a decision's
menu included. To record again on some other engine, put that engine's
``tarmar_engine`` and its ``tests`` package on the path with this module
beside them and call :func:`write_fixture`.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from tarmar_engine import engine, policy
from tarmar_engine.magic import CHANNEL, COUNTER_BOUNDS, PUSH_CAP, MagicRules
from tarmar_engine.profile import TarmarProfile
from tarmar_engine.state import BattleState, CombatantState

from . import replay_scenarios
from .test_borrowing import example_borrowing_rules
from .test_channel import example_channel_rules
from .test_counterspell import example_counterspell_rules
from .test_engine import SeededStubRoller
from .test_magic import example_casting_failure_rules, example_magic_rules
from .test_runaway import example_runaway_rules

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "event_streams" / "v0_9_21.json"
)

#: The spells every mage in an armed battle knows: a damaging rung of two
#: elements, both direct-damage spells, the healing spell and both
#: continuing ones.
MAGE_SPELLS: tuple[str, ...] = (
    "fire_missile",
    "fire_ball",
    "lightning_bolt",
    "fatigue",
    "wound",
    "heal",
    "shield",
    "blur",
)
#: Each mage's recorded level in every spell it knows, and in the general
#: skill the example Channel rules read for one kind of magic.
MAGE_SPELL_LEVEL = 3
MAGE_GENERAL_SKILL_LEVEL = 4
MAGE_MANA = 14
#: A seeded draw from the menu differs from the dice's by this much, so the
#: two never share a sequence.
MENU_SEED_OFFSET = 1000


def every_mechanic() -> MagicRules:
    """The tests' invented magic rules with every mechanic v0.9.21 has
    injected at once. Not Tarmar's numbers."""
    return replace(
        example_magic_rules(),
        channel=example_channel_rules(),
        borrowing=example_borrowing_rules(),
        runaway=example_runaway_rules(),
        counterspell=example_counterspell_rules(),
        casting_failure=example_casting_failure_rules(),
    )


def armed(
    build: Callable[[], BattleState], lifted_for: int | None = None
) -> Callable[[], BattleState]:
    """A battle's builder with every figure made a mage.

    Args:
        build: The battle to arm.
        lifted_for: The combatant a Game Master has lifted every magic bound
            for, or ``None`` for nobody.

    Returns:
        A builder of the armed battle's starting state.
    """

    def build_armed() -> BattleState:
        state = build()
        general_skills = {
            derivation.source_skill: MAGE_GENERAL_SKILL_LEVEL
            for derivation in example_channel_rules().derivations.values()
            if derivation.source_skill is not None
        }
        for figure in state.combatants:
            figure.intelligence = 13 + figure.combatant_id % 3
            figure.wisdom = 12 + figure.combatant_id % 2
            figure.max_mana = MAGE_MANA
            figure.mana = MAGE_MANA
            figure.spells = list(MAGE_SPELLS)
            figure.spell_skill_levels = dict.fromkeys(MAGE_SPELLS, MAGE_SPELL_LEVEL)
            figure.skill_levels = dict(general_skills)
        if lifted_for is not None:
            state.by_id(lifted_for).lifted_magic_bounds = [
                CHANNEL,
                PUSH_CAP,
                COUNTER_BOUNDS,
            ]
        return state

    return build_armed


class MenuDraw:
    """Chooses as a person at the menu might: any option on it, by a seeded
    draw. The menu is the one a person is shown, every target included."""

    def __init__(self, magic: MagicRules | None, seed: int) -> None:
        self.magic = magic
        self.random = random.Random(seed)

    def __call__(self, state: BattleState, actor: CombatantState) -> policy.Decision:
        decision = policy.choose_option(state, actor, self.magic, every_target=True)
        drawn = self.random.randrange(len(decision.candidates))
        decision.chosen = decision.candidates[drawn]
        return decision


@dataclass(frozen=True)
class RecordedBattle:
    """One battle of the fixture.

    Attributes:
        name: The battle's name in the fixture, its seed aside.
        build: Builds its starting state.
        magic: The magic rules its profile injects, or ``None`` for the
            default profile.
        menu_draw: Does every figure take a seeded draw from its whole menu?
            Otherwise each takes the AI's own choice.
        seed: Seeds the dice, and the menu draws with them.
        turns: How many turns are run.
    """

    name: str
    build: Callable[[], BattleState]
    magic: MagicRules | None
    menu_draw: bool
    seed: int
    turns: int

    @property
    def key(self) -> str:
        """The battle's key in the fixture."""
        return f"{self.name}-seed{self.seed}"


def _battles() -> list[RecordedBattle]:
    scenarios = replay_scenarios.SCENARIOS
    # The two replay battles with a caster in them; the others cast nothing.
    plain = [
        RecordedBattle(name, scenarios[name], None, False, seed, 8)
        for name in ("skirmish", "standoff")
        for seed in (1, 2)
    ]
    magic = every_mechanic()
    mages = [
        RecordedBattle(f"{name}-mages", armed(scenarios[name]), magic, True, seed, 8)
        for name in ("duel", "skirmish", "teams", "standoff")
        for seed in (1, 2, 3, 4)
    ]
    lifted = [
        RecordedBattle(
            "teams-mages-lifted",
            armed(scenarios["teams"], lifted_for=1),
            magic,
            True,
            seed,
            8,
        )
        for seed in (1, 2)
    ]
    return plain + mages + lifted


#: Every battle of the fixture.
BATTLES: tuple[RecordedBattle, ...] = tuple(_battles())


def run(battle: RecordedBattle) -> tuple[list[dict], dict]:
    """Run a battle on the engine in hand.

    Returns:
        Every event it logged, as JSON holds it, and the state it ended in.
    """
    state = battle.build()
    roller = SeededStubRoller(battle.seed)
    choose = (
        MenuDraw(battle.magic, battle.seed + MENU_SEED_OFFSET)
        if battle.menu_draw
        else policy.choose_option
    )
    profile = None if battle.magic is None else TarmarProfile(magic=battle.magic)
    events: list[dict] = []
    for _turn in range(battle.turns):
        engine.run_turn(state, roller, events.append, choose, profile=profile)
    return json.loads(json.dumps(events)), json.loads(json.dumps(state.to_dict()))


def digest(value: object) -> str:
    """A short digest of a JSON value, whatever order its keys were written
    in."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def recording(battle: RecordedBattle) -> dict:
    """A battle as the fixture stores it: each event's type, message and
    digest, and the digest of the state it ended in."""
    events, final_state = run(battle)
    return {
        "events": [
            [event["event_type"], event["message"], digest(event)] for event in events
        ],
        "final_state": digest(final_state),
    }


def write_fixture() -> None:
    """Record every battle on the engine in hand and store the fixture, an
    event to a line."""
    battles = []
    for battle in BATTLES:
        recorded = recording(battle)
        events = ",\n".join(json.dumps(event) for event in recorded["events"])
        battles.append(
            f"{json.dumps(battle.key)}: "
            f'{{"final_state": {json.dumps(recorded["final_state"])}, '
            f'"events": [\n{events}\n]}}'
        )
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text("{\n" + ",\n".join(battles) + "\n}\n")
