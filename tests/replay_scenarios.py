"""Seeded battles recorded on tarmar-engine v0.9.4, for replay guards.

Spencer's instruction for the 2026-09-29 battle-rules pass is "no
regressions": a seeded battle must replay identically before and after a
fix wherever the rule that fix changed is not exercised. The fixtures under
``tests/fixtures/replay/`` were recorded from these builders on the v0.9.4
engine (commit 36c667a), before any of the pass's changes; ``test_replay``
replays each one on the current engine and compares event for event.

One carve-out, stated rather than hidden: a decision event's ``candidates``
payload is the menu the policy scored, and the pass *adds* options to that
menu (Sprint, yielding, One Last Shot, pick-up and change-weapon), so the
fixtures are stored without it. ``test_replay`` goes one step further and
compares a decision by what was chosen, not by its forecast prose: #824
changed one forecast input on purpose (a critical's modifier counts once),
which moves the printed expectation of any weapon with a damage modifier
without moving any choice or any die.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from tarmar_engine import engine, policy
from tarmar_engine.state import BattleState, CombatantState, WeaponState

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "replay"

BROADSWORD = WeaponState(
    item_id="broadsword",
    name="Broadsword",
    weapon_class="Striking",
    damage="2d6",
    str_req=12,
)
SPEAR = WeaponState(
    item_id="spear_1h",
    name="Spear",
    weapon_class="Thrusting",
    damage="1d6",
    str_req=11,
)
SMALL_BOW = WeaponState(
    item_id="small_bow",
    name="Small Bow",
    weapon_class="Missile — Bows",
    damage="1d6-1",
    str_req=9,
    is_missile=True,
)


def _figure(combatant_id: int, **fields) -> CombatantState:
    base = {
        "combatant_id": combatant_id,
        "name": f"Figure {combatant_id}",
        "strength": 12,
        "dexterity": 12,
        "intelligence": 10,
        "wisdom": 10,
        "constitution": 12,
        "max_fatigue": 40,
        "max_body": 27,
        "fatigue": 40,
        "body": 27,
        "weapon": BROADSWORD,
    }
    base.update(fields)
    # Each figure holds its own copy of the weapon, as a game's snapshot
    # gives it; the module constants are templates, never shared in hand.
    base["weapon"] = replace(base["weapon"])
    return CombatantState(**base)


def duel() -> BattleState:
    """Two broadsword fighters, face to face, fresh."""
    return BattleState(
        arena_radius=6,
        combatants=[
            _figure(1, name="Aldo", q=0, r=0, facing=0),
            _figure(2, name="Brin", q=1, r=0, facing=3, dexterity=11),
        ],
    )


def skirmish() -> BattleState:
    """Four figures, free for all, starting apart: sword, spear, bow, caster."""
    return BattleState(
        arena_radius=7,
        combatants=[
            _figure(1, name="Sword", q=-5, r=0, facing=0),
            _figure(
                2,
                name="Spear",
                q=5,
                r=0,
                facing=3,
                weapon=SPEAR,
                armour_tier="Medium",
                stops=3,
                dexterity=10,
            ),
            _figure(3, name="Archer", q=0, r=-5, facing=1, weapon=SMALL_BOW),
            _figure(
                4,
                name="Mage",
                q=0,
                r=5,
                facing=4,
                intelligence=14,
                wisdom=13,
                max_mana=10,
                mana=10,
                spells=["fire_missile", "shield"],
            ),
        ],
    )


def teams() -> BattleState:
    """Two against two, teams tagged, closing from the rim."""
    return BattleState(
        arena_radius=6,
        combatants=[
            _figure(1, name="Red One", q=-3, r=0, facing=0, team="red"),
            _figure(2, name="Red Two", q=-3, r=1, facing=0, team="red", weapon=SPEAR),
            _figure(3, name="Blue One", q=3, r=0, facing=3, team="blue"),
            _figure(
                4,
                name="Blue Two",
                q=3,
                r=-1,
                facing=3,
                team="blue",
                armour_tier="Light",
                stops=2,
            ),
        ],
    )


def standoff() -> BattleState:
    """A caster and an archer against one sword, both kept at a distance."""
    return BattleState(
        arena_radius=8,
        combatants=[
            _figure(
                1,
                name="Caster",
                q=-6,
                r=0,
                facing=0,
                intelligence=15,
                wisdom=14,
                max_mana=12,
                mana=12,
                spells=["fire_missile", "shield", "heal"],
                team="far",
            ),
            _figure(
                2, name="Bowman", q=-6, r=3, facing=0, weapon=SMALL_BOW, team="far"
            ),
            _figure(3, name="Blade", q=6, r=0, facing=3, team="near"),
        ],
    )


MACE = WeaponState(
    item_id="mace",
    name="Mace",
    weapon_class="Striking",
    damage="2d6-1",
    str_req=11,
    is_thrown=True,
)


def armoured() -> BattleState:
    """Two armoured fighters, chainmail against leather, for a long duel.

    The review of the pass asked for one long battle: the 3-turn fixtures
    stop being a guard at their first divergence, and armour that stops
    whole blows is where #813's reading of "physical hits" shows.
    """
    return BattleState(
        arena_radius=6,
        combatants=[
            _figure(
                1,
                name="Aldo",
                q=-2,
                r=0,
                facing=0,
                armour_tier="Medium",
                stops=3,
                dexterity=11,
                weapon_skill_level=2,
            ),
            _figure(
                2,
                name="Brin",
                q=2,
                r=0,
                facing=3,
                armour_tier="Light",
                stops=2,
                weapon=MACE,
                weapon_skill_level=1,
            ),
        ],
    )


SCENARIOS: dict[str, Callable[[], BattleState]] = {
    "duel": duel,
    "skirmish": skirmish,
    "teams": teams,
    "standoff": standoff,
    "armoured": armoured,
}

#: Seeds recorded per scenario, and the turns each recording runs.
SEEDS = range(1, 7)
TURNS = 3
#: The long armoured duel: one seed, 25 turns (``RECORDED``).
LONG_TURNS = {"armoured": 25}
ARMOURED_SEED = 7

#: Every recorded fixture, as (scenario, seed).
RECORDED: list[tuple[str, int]] = [
    (scenario, seed)
    for scenario in ("duel", "skirmish", "teams", "standoff")
    for seed in SEEDS
] + [("armoured", ARMOURED_SEED)]


def comparable(event: dict) -> dict:
    """The event minus the one additive key (module docstring)."""
    if event["event_type"] != "decision":
        return event
    payload = {key: value for key, value in event["payload"].items()}
    payload.pop("candidates", None)
    return {**event, "payload": payload}


def record(scenario: str, seed: int, roller_factory) -> list[dict]:
    """Run a scenario's turns on one seed; the comparable events."""
    state = SCENARIOS[scenario]()
    roller = roller_factory(seed)
    events: list[dict] = []
    for _turn in range(LONG_TURNS.get(scenario, TURNS)):
        engine.run_turn(state, roller, events.append, policy.choose_option)
    return [comparable(event) for event in json.loads(json.dumps(events))]


def fixture_path(scenario: str, seed: int) -> Path:
    return FIXTURES / f"{scenario}-seed{seed}.json"
