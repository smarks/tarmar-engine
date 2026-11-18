"""Regression guard: battles recorded on v0.9.21 must log what they logged.

The True Magic work (tarmar-engine #77) rebuilds a cast out of pieces a
working shares. ``tests/event_stream_scenarios.py`` holds the battles and
says how the fixture was recorded; each is run here on the current engine and
compared event for event, a decision's whole menu included, and by the state
it ends in.
"""

import json
from unittest import TestCase

from .event_stream_scenarios import BATTLES, FIXTURE, digest, recording, run

RECORDED: dict[str, dict] = json.loads(FIXTURE.read_text())


def first_divergence(recorded: dict, replayed: dict) -> str | None:
    """``None`` when a battle logs what it logged on v0.9.21, else the first
    event that differs, or the difference in what it ended as."""
    before_events = recorded["events"]
    after_events = replayed["events"]
    for index, (before, after) in enumerate(
        zip(before_events, after_events, strict=False)
    ):
        if before != after:
            return f"event {index}: {before[:2]} became {after[:2]}"
    if len(before_events) != len(after_events):
        return f"{len(before_events)} events recorded, {len(after_events)} replayed"
    if recorded["final_state"] != replayed["final_state"]:
        return "every event matches, and the battle ends in a different state"
    return None


class EventStreamRegressionTest(TestCase):
    def test_every_recorded_battle_logs_what_it_did_on_v0_9_21(self):
        self.assertEqual(set(RECORDED), {battle.key for battle in BATTLES})
        for battle in BATTLES:
            with self.subTest(battle=battle.key):
                self.assertIsNone(
                    first_divergence(RECORDED[battle.key], recording(battle))
                )

    def test_a_changed_event_is_caught(self):
        # The comparison is by digest of the whole event: one payload value
        # changed, with the message as it was, is a divergence.
        battle = BATTLES[0]
        events, _final_state = run(battle)
        changed = json.loads(json.dumps(events[5]))
        changed["payload"] = {**changed.get("payload", {}), "changed": True}
        replayed = recording(battle)
        self.assertEqual(replayed["events"][5][2], digest(events[5]))
        replayed["events"][5][2] = digest(changed)
        self.assertEqual(
            first_divergence(RECORDED[battle.key], replayed),
            f"event 5: {replayed['events'][5][:2]} became {replayed['events'][5][:2]}",
        )

    def test_the_battles_make_every_kind_of_cast(self):
        """What the guard covers: the armed battles between them push,
        borrow, counter, press again, lose control and anchor."""
        payloads = [
            event.get("payload") or {} for battle in BATTLES for event in run(battle)[0]
        ]
        casts = [payload for payload in payloads if "casting_roll" in payload]

        def events_with(key: str) -> int:
            return sum(1 for payload in payloads if payload.get(key))

        self.assertGreater(len(casts), 200)
        for outcome in (True, False):
            self.assertTrue(any(cast["success"] is outcome for cast in casts))
        for key in ("push_mana", "borrowed", "lifted_bounds"):
            with self.subTest(cast_with=key):
                self.assertTrue(any(cast.get(key) for cast in casts))
        for key in (
            "spell_countered",
            "counter_result",
            "counter_taken_at_cast",
            "contest_ended",
            "debt_roll",
            "debt_failure",
            "control_band",
            "runaway_begins",
            "effect_bonus",
            "healed",
            "active",
        ):
            with self.subTest(event_with=key):
                self.assertGreater(events_with(key), 0)
        purposes = {payload.get("purpose") for payload in payloads}
        self.assertLessEqual(
            {"casting", "control", "counter", "debt", "spell aim", "anchor"}, purposes
        )
