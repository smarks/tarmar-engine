"""The pieces a cast is built from (tarmar-engine #77).

``TurnRunner.cast_spell`` makes its checks and hands the casting to pieces a
working will share: the guard on where a figure may cast, the sequence from
the counter to the debt roll, and resolvers that take the casting's own push
and target instead of reading the caster's choice for the turn. The suite's
cast tests and ``test_event_stream_regression`` show a cast logs what it
always has; these tests ask each piece what only it can answer.

Every number is invented, as in ``test_magic`` and ``test_true_magic``.
"""

from dataclasses import replace
from unittest import TestCase

from tarmar_engine import combat_math, engine, policy
from tarmar_engine.magic import CastingEnding, MagicRules
from tarmar_engine.profile import TARMAR, TarmarProfile
from tarmar_engine.spells import get_spell
from tarmar_engine.state import BODY_POOL, BattleState, RunawayState, WeaponState

from .test_channel import cast_payload, channel_caster
from .test_counterspell import (
    CASTER_LOW,
    DEFENDER_HIGH,
    MISSILE_FACES,
    choose_counter,
    counter_state,
)
from .test_counterspell import runner as counter_runner
from .test_engine import ScriptedRoller, events_of_type
from .test_magic import (
    MAGIC_PROFILE,
    example_push_rules,
    roll_purposes,
    wizard_state,
)
from .test_runaway import (
    RUNAWAY_PROFILE,
    SURGE_PROFILE,
    anchor,
    anchor_payload,
    example_runaway_rules,
    feed,
    with_helper,
)
from .test_state import make_combatant
from .test_true_magic import KNOT_SKILL, example_true_magic_rules, true_magic_rules

TRUE_MAGIC_PROFILE = TarmarProfile(magic=true_magic_rules())


def runner_on(state, faces=(), profile=TARMAR):
    """A turn runner on scripted faces, and the events it logs."""
    events: list[dict] = []
    return (
        engine.TurnRunner(state, ScriptedRoller(faces), events.append, profile=profile),
        events,
    )


def adjacent_pair(**caster_overrides) -> BattleState:
    """A caster with a sword in hand (Shield and Heal known, 10 mana) and
    another figure in the next hex."""
    caster_fields = {
        "q": 0,
        "r": 0,
        "facing": 0,
        "intelligence": 14,
        "wisdom": 14,
        "spells": ["shield", "heal"],
        "mana": 10,
        "max_mana": 10,
    }
    caster_fields.update(caster_overrides)
    return BattleState(
        arena_radius=6,
        combatants=[
            make_combatant(1, **caster_fields),
            make_combatant(2, q=1, r=0, facing=3),
        ],
    )


def held(state: BattleState) -> BattleState:
    """The pair with figure 2 holding the caster in a grapple."""
    state.by_id(1).grappled_by = 2
    state.by_id(2).grappling = 1
    return state


def hand_to_hand(state: BattleState) -> BattleState:
    """The pair in hand-to-hand with each other."""
    state.by_id(1).hth_with = [2]
    state.by_id(2).hth_with = [1]
    return state


class CastBlockedTest(TestCase):
    """One guard says where a figure may not cast, and logs the refusal."""

    def blocked(self, state: BattleState, spell=None) -> tuple[bool, list[dict]]:
        turn_runner, events = runner_on(state)
        spell = get_spell("shield") if spell is None else spell
        return turn_runner._cast_blocked(state.by_id(1), spell), events

    def test_a_free_caster_is_not_blocked_and_nothing_is_logged(self):
        self.assertEqual(self.blocked(adjacent_pair()), (False, []))

    def test_a_held_caster_is_blocked_below_mastery_three(self):
        for mastery in ({}, {"shield": 2}):
            with self.subTest(mastery=mastery):
                refused, events = self.blocked(
                    held(adjacent_pair(spell_mastery=mastery))
                )
                self.assertTrue(refused)
                self.assertEqual([event["event_type"] for event in events], ["info"])
                self.assertIn("locked in a grapple", events[0]["message"])
        self.assertEqual(
            self.blocked(held(adjacent_pair(spell_mastery={"shield": 3}))), (False, [])
        )

    def test_the_one_holding_is_blocked_as_the_one_held_is(self):
        state = adjacent_pair()
        state.by_id(1).grappling = 2
        state.by_id(2).grappled_by = 1
        refused, events = self.blocked(state)
        self.assertTrue(refused)
        self.assertIn("locked in a grapple", events[0]["message"])

    def test_hand_to_hand_with_a_weapon_in_hand_is_blocked(self):
        refused, events = self.blocked(hand_to_hand(adjacent_pair()))
        self.assertTrue(refused)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["payload"], {"hth_cast_refused": True, "spell": "shield"}
        )
        self.assertIn("Broadsword in hand", events[0]["message"])

    def test_hand_to_hand_with_empty_hands_or_no_gestures_is_not(self):
        empty_handed = hand_to_hand(adjacent_pair(weapon=WeaponState()))
        self.assertEqual(self.blocked(empty_handed), (False, []))
        no_gestures = hand_to_hand(
            adjacent_pair(spell_mastery={"shield": policy.HTH_NO_GESTURE_MASTERY})
        )
        self.assertEqual(self.blocked(no_gestures), (False, []))

    def test_a_spell_the_caster_has_no_mastery_of_reads_at_level_one(self):
        # A working is no catalog spell and has no mastery entry.
        working = example_true_magic_rules().working("mend", 2)
        for state in (held(adjacent_pair()), hand_to_hand(adjacent_pair())):
            refused, events = self.blocked(state, working)
            self.assertTrue(refused)
            self.assertIn(working.name, events[0]["message"])
        self.assertEqual(self.blocked(adjacent_pair(), working), (False, []))

    def test_a_blocked_cast_rolls_nothing_and_spends_nothing(self):
        state = hand_to_hand(adjacent_pair())
        caster = state.by_id(1)
        caster.chosen_spell = "shield"
        turn_runner, events = runner_on(state, [[3, 3, 3]])
        turn_runner.cast_spell(caster)
        self.assertEqual([event["event_type"] for event in events], ["info"])
        self.assertEqual(caster.mana, 10)
        self.assertEqual(caster.active_spells, [])


class LivingTargetTest(TestCase):
    """The figure an action lands on is the one it names, not whatever the
    actor chose for its turn."""

    def setUp(self):
        self.state = BattleState(
            arena_radius=8,
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=2, r=0),
                make_combatant(3, q=6, r=0),
            ],
        )
        self.actor = self.state.by_id(1)
        self.turn_runner, _events = runner_on(self.state)

    def test_the_named_figure_is_the_target_however_far(self):
        self.actor.chosen_target = 2
        target = self.turn_runner._living_target(self.actor, 3)
        self.assertIs(target, self.state.by_id(3))

    def test_no_figure_named_is_the_nearest_enemy(self):
        self.actor.chosen_target = 3
        target = self.turn_runner._living_target(self.actor, None)
        self.assertIs(target, self.state.by_id(2))

    def test_a_named_figure_that_is_down_gives_way_to_the_nearest(self):
        self.state.by_id(3).conscious = False
        target = self.turn_runner._living_target(self.actor, 3)
        self.assertIs(target, self.state.by_id(2))

    def test_with_no_enemy_standing_there_is_none(self):
        for enemy_id in (2, 3):
            self.state.by_id(enemy_id).alive = False
        self.assertIsNone(self.turn_runner._living_target(self.actor, 2))


class ResolveCastTest(TestCase):
    """A successful cast's effect says whether the spell went off, and reads
    the casting's own push and target."""

    #: A natural total the example rules give no tier.
    NO_TIER = 9

    def resolve(self, state, spell_key, faces, *, push_mana=0, target_id=2):
        turn_runner, events = runner_on(state, faces, MAGIC_PROFILE)
        spell = get_spell(spell_key)
        went_off = turn_runner._resolve_cast(
            state.by_id(1),
            spell,
            MAGIC_PROFILE.magic,
            self.NO_TIER,
            1,
            spell.level + push_mana,
            push_mana=push_mana,
            target_id=target_id,
        )
        return went_off, events

    def test_a_spell_that_lands_went_off(self):
        state = wizard_state()
        went_off, events = self.resolve(state, "fire_missile", [[2, 2, 2], [5]])
        self.assertTrue(went_off)
        self.assertEqual(roll_purposes(events), ["spell aim", "spell damage"])
        self.assertEqual(state.by_id(2).fatigue, 40 - 5)

    def test_a_missed_aim_still_went_off(self):
        state = wizard_state()
        went_off, events = self.resolve(state, "fire_missile", [[6, 6, 6]])
        self.assertTrue(went_off)
        self.assertEqual(roll_purposes(events), ["spell aim"])
        self.assertEqual(state.by_id(2).fatigue, 40)

    def test_a_spell_with_no_one_left_to_land_on_still_went_off(self):
        state = wizard_state()
        state.by_id(2).alive = False
        went_off, events = self.resolve(state, "fire_missile", [])
        self.assertTrue(went_off)
        self.assertEqual(events, [])

    def test_a_continuing_spell_and_a_healing_one_went_off(self):
        state = wizard_state(fatigue=30)
        went_off, _events = self.resolve(state, "shield", [])
        self.assertTrue(went_off)
        self.assertEqual(state.by_id(1).active_spells, ["shield"])
        went_off, _events = self.resolve(state, "heal", [[4]])
        self.assertTrue(went_off)
        self.assertEqual(state.by_id(1).fatigue, 34)

    def test_a_control_band_that_stops_the_spell_did_not(self):
        # WIS 12 less 2 x 3 pushed = 6; a 9 misses by 3, the band that stops
        # the spell.
        state = wizard_state()
        went_off, events = self.resolve(state, "fire_missile", [[5, 4]], push_mana=3)
        self.assertFalse(went_off)
        self.assertEqual(roll_purposes(events), ["control"])
        self.assertEqual(state.by_id(2).fatigue, 40)

    def test_a_control_band_that_lets_the_spell_through_went_off(self):
        # A 7 misses by 1: the spell takes effect without the push's bonus.
        state = wizard_state()
        went_off, events = self.resolve(
            state, "fire_missile", [[4, 3], [2, 2, 2], [5]], push_mana=3
        )
        self.assertTrue(went_off)
        self.assertEqual(
            roll_purposes(events), ["control", "spell aim", "spell damage"]
        )
        self.assertEqual(state.by_id(2).fatigue, 40 - 5)

    def test_the_push_and_the_target_are_the_castings_not_the_turns_choice(self):
        state = wizard_state()
        state.combatants.append(make_combatant(3, q=3, r=2, facing=3))
        caster = state.by_id(1)
        # The turn's choice says no push, at figure 2.
        caster.chosen_push_mana = 0
        caster.chosen_target = 2
        went_off, events = self.resolve(
            state, "fire_missile", [[2, 2], [2, 2, 2], [5]], push_mana=1, target_id=3
        )
        self.assertTrue(went_off)
        self.assertEqual(roll_purposes(events)[0], "control")
        self.assertEqual(state.by_id(2).fatigue, 40)
        self.assertLess(state.by_id(3).fatigue, 40)


class DebtRollCostTest(TestCase):
    def test_a_runaway_band_starts_one_of_the_cost_it_is_given(self):
        # Owing 3: WIS 12 less 2 x 3 = 6, and a 13 misses by 7, the band
        # that is a Runaway. The turn's choice of push is not read.
        state = wizard_state(debt=3)
        caster = state.by_id(1)
        caster.chosen_push_mana = 2
        magic = SURGE_PROFILE.magic
        assert magic is not None and magic.borrowing is not None
        turn_runner, _events = runner_on(state, [[7, 6]], SURGE_PROFILE)
        turn_runner._debt_roll(
            caster, get_spell("fire_missile"), magic.borrowing, cost=7
        )
        self.assertEqual(caster.runaway, RunawayState("fire_missile", 7))


class OpposeCastTest(TestCase):
    """A countered casting leaves its contest standing on the caster, unless
    it is told not to."""

    def countered(self, **oppose_arguments):
        state = counter_state()
        choose_counter(state, 3)
        turn_runner, _events = counter_runner(state, [CASTER_LOW, DEFENDER_HIGH])
        turn_runner.declare_counter(state.by_id(2))
        caster = state.by_id(1)
        contest = turn_runner._oppose_cast(
            caster, get_spell("fire_missile"), 1, **oppose_arguments
        )
        return state, caster, contest

    def test_a_countered_cast_keeps_its_contest_for_a_later_turn(self):
        _state, caster, contest = self.countered(target_id=2)
        self.assertIsNotNone(contest)
        self.assertIs(caster.contest, contest)

    def test_the_contest_keeps_the_figure_the_casting_names(self):
        # The caster's choice for the turn names figure 2; the casting, none.
        _state, caster, contest = self.countered(target_id=None)
        assert contest is not None
        self.assertEqual(caster.chosen_target, 2)
        self.assertIsNone(contest.target_id)

    def test_a_casting_that_does_not_stand_leaves_no_contest(self):
        state, caster, contest = self.countered(target_id=2, stands=False)
        assert contest is not None
        self.assertEqual(contest.defender_ids, [2])
        self.assertEqual(contest.turns, 1)
        self.assertIsNone(caster.contest)
        # The counter that met it is spent all the same.
        self.assertIsNone(state.by_id(2).counter)


class TakeDrainTest(TestCase):
    """A drain is taken from the injected pool, and only when there is one
    to take. The example rules drain 3 Body per mana for a casting that went
    off, 5 for one countered, and nothing for one that failed."""

    def drained(self, drained_mana, ending, **caster_overrides):
        state = wizard_state(**caster_overrides)
        caster = state.by_id(1)
        caster.fatal_chain = [2]
        turn_runner, events = runner_on(state, profile=TRUE_MAGIC_PROFILE)
        turn_runner._take_drain(caster, drained_mana, ending, [7, 9])
        return caster, events

    def test_a_casting_that_went_off_drains_per_mana(self):
        caster, events = self.drained(2, None)
        self.assertEqual(caster.body, 27 - 6)
        self.assertEqual([event["event_type"] for event in events], ["status"])
        self.assertEqual(
            events[0]["payload"],
            {BODY_POOL: 21, "cost": 6, "reason": engine.DRAIN_REASON},
        )
        self.assertEqual(caster.fatal_chain, [7, 9])

    def test_a_lost_casting_drains_by_how_it_ended(self):
        caster, events = self.drained(2, CastingEnding.COUNTERED)
        self.assertEqual(caster.body, 27 - 10)
        self.assertEqual(events[0]["payload"]["cost"], 10)

    def test_no_drain_takes_nothing_and_logs_nothing(self):
        for drained_mana, ending in ((4, CastingEnding.FAILED), (0, None)):
            with self.subTest(drained_mana=drained_mana, ending=ending):
                caster, events = self.drained(drained_mana, ending)
                self.assertEqual(caster.body, 27)
                self.assertEqual(events, [])
                self.assertEqual(caster.fatal_chain, [2])

    def test_a_drain_that_empties_the_pool_fells_the_caster(self):
        caster, events = self.drained(9, None)
        self.assertEqual(caster.body, 0)
        self.assertFalse(caster.conscious)
        self.assertEqual(events[0]["payload"]["cost"], 27)

    def test_a_drain_with_no_true_magic_rules_is_refused(self):
        for profile in (TARMAR, MAGIC_PROFILE):
            with self.subTest(magic=profile.magic is not None):
                state = wizard_state()
                turn_runner, events = runner_on(state, profile=profile)
                with self.assertRaises(ValueError):
                    turn_runner._take_drain(state.by_id(1), 2, None, [])
                self.assertEqual(events, [])


class CastSpellRefusesAWorkingTest(TestCase):
    """A working is crafted. CAST SPELL never runs one, whatever key the
    caster's choice carries."""

    def cast_of(self, key: str, profile):
        state = wizard_state()
        caster = state.by_id(1)
        caster.chosen_spell = key
        turn_runner, events = runner_on(state, [[3, 3, 3]], profile)
        return caster, turn_runner, events

    def test_a_working_these_rules_build_is_refused(self):
        key = example_true_magic_rules().working_key("scorch", 1)
        caster, turn_runner, events = self.cast_of(key, TRUE_MAGIC_PROFILE)
        with self.assertRaises(ValueError):
            turn_runner.cast_spell(caster)
        self.assertEqual(caster.mana, 10)
        self.assertEqual(events, [])

    def test_with_no_true_magic_rules_a_working_key_is_no_spell_at_all(self):
        key = example_true_magic_rules().working_key("scorch", 1)
        for profile in (TARMAR, TarmarProfile(magic=MagicRules())):
            with self.subTest(magic=profile.magic is not None):
                caster, turn_runner, _events = self.cast_of(key, profile)
                with self.assertRaises(KeyError):
                    turn_runner.cast_spell(caster)
        caster, turn_runner, _events = self.cast_of("no_such_spell", TARMAR)
        with self.assertRaises(KeyError):
            turn_runner.cast_spell(caster)


class OffBalanceCastAgainstACounterTest(TestCase):
    """A cast called on its own, outside any action, spends an off-balance
    caster's penalty on the first roll that asks for it. Against a declared
    counter that is the opposed roll, made before the casting roll, so the
    casting roll's target takes the penalties off only once the counter has
    been met. The caster has WIS 12 and INT 14, and 1 mana buys no bonus."""

    def cast_against_a_counter(self, faces, *, off_balance: bool):
        state = counter_state()
        caster = state.by_id(1)
        caster.off_balance = off_balance
        choose_counter(state, 3)
        turn_runner, events = counter_runner(state, faces)
        turn_runner.declare_counter(state.by_id(2))
        turn_runner.cast_spell(caster)
        rolls = {
            purpose: [
                event["payload"]
                for event in events_of_type(events, "roll")
                if event["actor"] == caster.name
                and event["payload"]["purpose"] == purpose
            ]
            for purpose in ("counter", "casting")
        }
        return caster, rolls

    def test_a_cast_that_gets_through_owes_it_on_the_opposed_roll_alone(self):
        faces = [[8, 8], [1, 1], *MISSILE_FACES]
        caster, rolls = self.cast_against_a_counter(faces, off_balance=True)
        self.assertEqual(
            rolls["counter"][0]["modifier"], 12 - combat_math.OFF_BALANCE_PENALTY
        )
        self.assertEqual(rolls["casting"][0]["target_number"], 14)
        self.assertFalse(caster.off_balance)
        # Steady on its feet, the same cast owes it nowhere.
        _caster, steady = self.cast_against_a_counter(faces, off_balance=False)
        self.assertEqual(steady["counter"][0]["modifier"], 12)
        self.assertEqual(steady["casting"][0]["target_number"], 14)

    def test_a_countered_cast_owes_it_on_the_opposed_roll_and_makes_no_other(self):
        caster, rolls = self.cast_against_a_counter(
            [CASTER_LOW, DEFENDER_HIGH], off_balance=True
        )
        self.assertEqual(
            rolls["counter"][0]["modifier"], 12 - combat_math.OFF_BALANCE_PENALTY
        )
        self.assertEqual(rolls["casting"], [])
        self.assertFalse(caster.off_balance)


class TrueCastChannelTest(TestCase):
    """A cast marked a True cast derives its Channel by the True kind, and is
    then stopped: a True cast is not run until its own slice of
    tarmar-engine #77. The "knot" kind gives 3 x the "knotwork" level + 1;
    a spell's own kind, 2 x its recorded level - 1."""

    PROFILE = TarmarProfile(
        magic=replace(true_magic_rules(), push=example_push_rules())
    )

    def caster_choosing(self, state, spell: str, *, true_cast: bool, push: int = 0):
        caster = state.by_id(1)
        caster.chosen_spell = spell
        caster.chosen_target = 2
        caster.chosen_push_mana = push
        caster.chosen_true_cast = true_cast
        return caster

    def test_a_true_cast_within_the_true_kinds_channel_is_not_yet_run(self):
        # "knotwork" 2: a Channel of 7 by the True kind. No level is recorded
        # in the spell, so its own kind gives a Channel of 0.
        state = channel_caster({}, skill_levels={KNOT_SKILL: 2})
        caster = self.caster_choosing(state, "fire_missile", true_cast=True)
        turn_runner, events = runner_on(state, [[3, 3, 3]], self.PROFILE)
        with self.assertRaises(NotImplementedError):
            turn_runner.cast_spell(caster)
        self.assertEqual(events, [])
        self.assertEqual(turn_runner.roller.faces_queue, [[3, 3, 3]])
        self.assertEqual(caster.mana, 10)
        # The same cast as a recipe cast is past the spell's own Channel.
        caster.chosen_true_cast = False
        with self.assertRaisesRegex(ValueError, "Channel for it is 0"):
            turn_runner.cast_spell(caster)

    def test_a_true_cast_past_the_true_kinds_channel_is_refused_by_it(self):
        # Lightning Bolt (3) pushed by 2 takes 5 mana, within the push cap of
        # 3. "knotwork" 1 gives a Channel of 4 by the True kind; a level of 3
        # in the spell gives 5 by its own.
        state = channel_caster(
            {"lightning_bolt": 3},
            skill_levels={KNOT_SKILL: 1},
            spells=["lightning_bolt"],
        )
        caster = self.caster_choosing(state, "lightning_bolt", true_cast=True, push=2)
        turn_runner, events = runner_on(state, [[3, 3, 3]], self.PROFILE)
        with self.assertRaisesRegex(ValueError, "with 5 mana.*Channel for it is 4"):
            turn_runner.cast_spell(caster)
        self.assertEqual(events, [])
        self.assertEqual(caster.mana, 10)
        # The same cast as a recipe cast is within the spell's own Channel.
        caster.chosen_true_cast = False
        turn_runner.cast_spell(caster)
        payload = cast_payload(events)
        self.assertEqual((payload["channel"], payload["channel_kind"]), (5, "loom"))
        self.assertEqual(payload["push_mana"], 2)


class RunawayOfAWorkingTest(TestCase):
    """A Runaway's key is read through the one lookup that knows a working,
    so one that names a working feeds, is anchored and is kept as a catalog
    spell's is."""

    PROFILE = TarmarProfile(
        magic=replace(true_magic_rules(), runaway=example_runaway_rules())
    )
    WORKING = example_true_magic_rules().working("scorch", 2)

    def running(self) -> BattleState:
        state = wizard_state()
        state.by_id(1).runaway = RunawayState(self.WORKING.key, 2)
        return state

    def test_it_feeds_on_its_caster_under_its_own_name(self):
        state = self.running()
        events = feed(state, self.PROFILE)
        drain = next(event for event in events if event["payload"].get("runaway_drain"))
        self.assertEqual(drain["payload"]["spell"], self.WORKING.key)
        self.assertTrue(drain["message"].startswith("Scorch (tier 2) feeds on"))
        # The example rules drain 2 x the original cost + 1 a turn.
        self.assertEqual(state.by_id(1).mana, 10 - 5)
        self.assertEqual(state.by_id(1).runaway, RunawayState(self.WORKING.key, 2, 1))

    def test_anchored_it_stops_and_is_no_active_spell(self):
        state = self.running()
        events = anchor(state, 1, [[2, 2]], self.PROFILE)
        caster = state.by_id(1)
        self.assertIsNone(caster.runaway)
        # A working never continues, so there is nothing to renew.
        self.assertEqual(caster.active_spells, [])
        payload = anchor_payload(events)
        self.assertEqual((payload["spell"], payload["held"]), (self.WORKING.key, True))
        self.assertEqual(caster.anchored_turns_running, {self.WORKING.key: 0})
        self.assertIn("anchors Scorch (tier 2)", events[-1]["message"])
        self.assertTrue(events[-1]["message"].endswith("it stops"))

    def test_a_helper_who_fails_is_caught_by_the_working(self):
        state = with_helper(self.running())
        events = anchor(state, 2, [[8, 8]], self.PROFILE)
        self.assertTrue(anchor_payload(events)["caught"])
        self.assertEqual(
            state.by_id(2).runaway,
            RunawayState(self.WORKING.key, 2, 0, caught_from=1),
        )
        self.assertEqual(state.by_id(1).runaway, RunawayState(self.WORKING.key, 2))

    def test_a_second_runaway_is_refused_naming_the_working_kept(self):
        state = self.running()
        turn_runner, events = runner_on(state, profile=self.PROFILE)
        turn_runner._begin_runaway(state.by_id(1), get_spell("fire_missile"), 1)
        self.assertEqual(
            events[-1]["payload"],
            {"spell": "fire_missile", "runaway_kept": self.WORKING.key},
        )
        self.assertIn("runaway Scorch (tier 2)", events[-1]["message"])
        self.assertEqual(state.by_id(1).runaway, RunawayState(self.WORKING.key, 2))

    def test_with_no_true_magic_rules_the_key_names_no_spell(self):
        with self.assertRaises(KeyError):
            feed(self.running(), RUNAWAY_PROFILE)
        with self.assertRaises(KeyError):
            anchor(self.running(), 1, [[2, 2]], RUNAWAY_PROFILE)
