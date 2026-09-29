"""Replay guards: seeded battles recorded on v0.9.4 must replay unchanged.

Spencer's "no regressions" instruction for the 2026-09-29 battle-rules pass:
a seeded battle must replay identically before and after a fix wherever the
rule the fix changed is not exercised. ``tests/replay_scenarios.py`` holds
the builders and the one stated carve-out (the decision menu may gain
options). Each fixture here was recorded before the pass's first change.
"""

import json
from unittest import TestCase

from .replay_scenarios import RECORDED, SEEDS, fixture_path, record
from .test_engine import SeededStubRoller


def _comparable(event: dict) -> dict:
    """What happened: every event, decision events down to what was chosen.

    A decision's prose is the AI's forecast of a candidate, and the pass
    changed one forecast input on purpose — a critical's expected damage now
    counts the modifier once (#824) — so weapons with a damage modifier
    forecast slightly differently. The choice itself, and everything the
    dice then did, must not move.
    """
    if event["event_type"] != "decision":
        return event
    chosen = event["payload"]["chosen"]
    return {
        key: value for key, value in event.items() if key not in ("message", "payload")
    } | {
        "chosen": (chosen["letter"], chosen["target_id"], chosen["spell_key"]),
    }


def first_divergence(scenario: str, seed: int) -> tuple[int, str] | None:
    """``None`` when the replay matches its fixture, else where and how not."""
    recorded = json.loads(fixture_path(scenario, seed).read_text())
    replayed = record(scenario, seed, SeededStubRoller)
    for index, (before, after) in enumerate(zip(recorded, replayed, strict=False)):
        if _comparable(before) != _comparable(after):
            return index, (
                f"event {index}: {before['message']!r} became {after['message']!r}"
            )
    if len(recorded) != len(replayed):
        return min(len(recorded), len(replayed)), (
            f"{len(recorded)} events recorded, {len(replayed)} replayed"
        )
    return None


def replay_diverges(scenario: str, seed: int) -> str | None:
    """``None`` when the replay matches its fixture, else the first difference."""
    found = first_divergence(scenario, seed)
    return None if found is None else found[1]


#: Fixtures the pass changes, each because it exercises a changed rule; the
#: first differing event must say which. Every other fixture replays exactly.
EXERCISED: dict[tuple[str, int], tuple[str, str]] = {
    ("duel", 3): ("#817", "initiative 1d6: [3] -1"),
    ("teams", 3): ("#817", "initiative 1d6: [6] = 6"),
    **{("skirmish", seed): ("#776/#815", "ONE LAST SHOT") for seed in SEEDS},
    ("teams", 1): ("#779", "forces Blue Two back a hex"),
    ("teams", 4): ("#779", "forces Red Two back a hex"),
    ("armoured", 7): ("#817", "initiative 1d6: [3] = 3"),
    ("teams", 5): ("#779", "forces Red One back a hex"),
    ("teams", 6): ("#779", "forces Blue One back a hex"),
    ("standoff", 1): ("#814", "breaks (second fumble)"),
    ("standoff", 3): ("#824", "dice only: the modifier counts once"),
}

#: For each issue, the recorded seeds that replay unchanged, and the part of
#: the battle each shows the fix left alone.
GUARDS: dict[str, list[tuple[str, int, str]]] = {
    "#776": [("standoff", 2, "an archer shooting disengaged")],
    "#777": [("duel", 1, "engaged fighters trading blows")],
    "#778": [("standoff", 4, "a sword running 11 hexes, paying 1 Fatigue")],
    "#779": [("duel", 1, "a push straight back, the pusher advancing")],
    "#780": [("duel", 6, "an off-balance fumble, no weapon dropped")],
    "#781": [("standoff", 6, "a bow with no rate note shooting once a turn")],
    "#811": [("duel", 6, "an off-balance fumble")],
    "#812": [("standoff", 4, "a bow's megahex range penalty")],
    "#813": [("duel", 2, "a push earned by damaging hits")],
    "#814": [("standoff", 4, "a weapon taking its first stress")],
    "#815": [("duel", 4, "armed fighters in melee")],
    "#816": [("standoff", 2, "a successful cast paying its mana")],
    "#817": [("duel", 1, "uninjured initiative rolls")],
    "#818": [("standoff", 5, "a figure with no sprint distance running")],
    "#819": [("teams", 2, "every figure moving in Initial Movement")],
    "#820": [("teams", 2, "armed, standing engagers")],
    "#821": [("standoff", 6, "a free caster casting")],
    "#822": [("duel", 5, "no grapple")],
    "#823": [("duel", 5, "no HTH")],
    "#824": [("duel", 2, "a critical with a +0 weapon")],
    "#826": [("standoff", 2, "the spell and missile numbers as they were")],
}


#: Long fixtures that guard a fix over a prefix: the replay must match for at
#: least this many events. Fixtures are checked only to their first
#: divergence, so everything after it is unguarded.
PREFIX_GUARDS: dict[tuple[str, int], tuple[int, str, str]] = {
    # 12 turns of chainmail against leather, whole blows stopped and pushes
    # earned, before #817's first injured initiative roll. Before the
    # review's #813 ruling this fixture diverged at event 48, a push after a
    # blow the armour had stopped entirely.
    ("armoured", 7): (194, "#813", "armour stopping whole blows; pushes on damage"),
}


class ReplayTest(TestCase):
    def test_every_fixture_replays_or_names_the_rule_it_exercises(self):
        for scenario, seed in RECORDED:
            with self.subTest(scenario=scenario, seed=seed):
                divergence = replay_diverges(scenario, seed)
                expected = EXERCISED.get((scenario, seed))
                if expected is None:
                    self.assertIsNone(divergence)
                else:
                    self.assertIsNotNone(divergence)
                    self.assertIn(expected[1], divergence or "")

    def test_the_long_armoured_duel_holds_until_its_named_divergence(self):
        for (scenario, seed), (least, _issue, _shows) in PREFIX_GUARDS.items():
            with self.subTest(scenario=scenario, seed=seed):
                found = first_divergence(scenario, seed)
                self.assertIsNotNone(found)
                self.assertGreaterEqual(found[0] if found else 0, least)
        prefix = json.loads(fixture_path("armoured", 7).read_text())[:194]
        messages = " ".join(event["message"] for event in prefix)
        self.assertIn("all of it stopped", messages)
        self.assertIn("forces", messages)

    def test_each_fix_has_a_recorded_seed_that_replays_unchanged(self):
        for issue, guards in GUARDS.items():
            for scenario, seed, _shows in guards:
                with self.subTest(issue=issue, scenario=scenario, seed=seed):
                    self.assertNotIn((scenario, seed), EXERCISED)
                    self.assertIsNone(replay_diverges(scenario, seed))

    def test_the_guards_show_what_they_claim(self):
        """Spot-check that a guard seed holds the situation it is cited for."""

        def purposes(scenario, seed):
            return {
                event["payload"].get("purpose")
                for event in json.loads(fixture_path(scenario, seed).read_text())
                if event["event_type"] == "roll"
            }

        def messages(scenario, seed):
            return " ".join(
                event["message"]
                for event in json.loads(fixture_path(scenario, seed).read_text())
            )

        self.assertIn("casting", purposes("standoff", 2))
        self.assertIn("confirm", purposes("duel", 2))
        self.assertIn("off-balance", messages("duel", 6))
        self.assertIn("takes stress", messages("standoff", 4))
        self.assertIn("forces Brin back a hex and advances", messages("duel", 1))
