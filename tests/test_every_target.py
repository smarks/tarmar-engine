"""The menu a person aims: every option at every legal target (tarmar-engine #21).

The policy aims each attack and each damaging cast where the AI would.
``choose_option(..., every_target=True)`` also offers it at every other enemy
it may be aimed at, priced by the code that prices the AI's own (score,
rationale and forecast), right after the AI's own target, while the AI's
choice stays what it was. tarmar-studio's turn menu built this itself from
the policy's private helpers; these tests pin the behaviour it relied on.
"""

from copy import deepcopy
from dataclasses import replace
from unittest import TestCase

from tarmar_engine import engine, policy, weapons
from tarmar_engine.policy import Candidate, Decision
from tarmar_engine.state import BattleState, WeaponState

from .replay_scenarios import LONG_TURNS, RECORDED, SCENARIOS, TURNS
from .test_battle_rules_pass import DAGGER, SMALL_BOW
from .test_borrowing import borrowing_magic_rules
from .test_engine import SeededStubRoller, events_of_type
from .test_magic import example_magic_rules
from .test_policy import bow
from .test_state import make_combatant

FIRE_MISSILE = "fire_missile"
SPEAR = WeaponState(
    item_id="spear",
    name="Spear",
    weapon_class="Piercing",
    damage="1d6+1",
    is_thrown=True,
)


def wide(state: BattleState, actor_id: int = 1, **keywords) -> Decision:
    return policy.choose_option(
        state, state.by_id(actor_id), every_target=True, **keywords
    )


def plain(state: BattleState, actor_id: int = 1, **keywords) -> Decision:
    return policy.choose_option(state, state.by_id(actor_id), **keywords)


def aimed(decision: Decision, letter: str, spell_key: str = "") -> list[Candidate]:
    """The plain candidates for one option (no push, borrow or escalation)."""
    return [
        candidate
        for candidate in decision.candidates
        if candidate.letter == letter
        and candidate.spell_key == spell_key
        and not candidate.push_mana
        and not candidate.borrow
    ]


def target_id(candidate: Candidate) -> int:
    """The Tarmar combatant a candidate is aimed at."""
    assert isinstance(candidate.target_id, int), candidate
    return candidate.target_id


def targets(candidates: list[Candidate]) -> list[int]:
    return [target_id(candidate) for candidate in candidates]


def two_beside() -> BattleState:
    """Fighter 1 engaged by 2 (armoured) and 3 (bare), both adjacent."""
    return BattleState(
        arena_radius=8,
        combatants=[
            make_combatant(1, q=0, r=0, facing=0),
            make_combatant(2, q=1, r=0, facing=3, stops=4),
            make_combatant(3, q=0, r=1, facing=4),
        ],
    )


def one_held_one_free() -> BattleState:
    """Fighter 1 (dagger in hand) beside 2 and 3, who agree to close (#867);
    1's ally 4 holds 2 in a grapple, and 3 is free (tarmar-engine #60)."""
    state = BattleState(
        arena_radius=8,
        combatants=[
            make_combatant(1, q=0, r=0, facing=0, team="near", weapon=DAGGER),
            make_combatant(2, q=1, r=0, facing=3, team="far"),
            make_combatant(3, q=0, r=1, facing=4, team="far"),
            make_combatant(4, q=2, r=-1, facing=4, team="near"),
        ],
    )
    state.by_id(1).hth_agreed_with = [2, 3]
    state.by_id(4).grappling, state.by_id(2).grappled_by = 2, 4
    return state


def three_at_range(weapon: WeaponState) -> BattleState:
    """Figure 1 with ``weapon``; 2 near and armoured, 3 and 4 farther. With
    a bow the AI shoots 2, though a shot at 4 scores higher."""
    return BattleState(
        arena_radius=10,
        combatants=[
            make_combatant(1, q=0, r=0, weapon=weapon),
            make_combatant(2, q=3, r=0, stops=3),
            make_combatant(3, q=-5, r=0),
            make_combatant(4, q=0, r=6),
        ],
    )


def caster_and_three() -> BattleState:
    """A bare-handed wizard (10 mana) and three enemies at rising distances.
    The AI casts Fire Missile at 2, though a cast at 3 scores higher."""
    return BattleState(
        arena_radius=10,
        combatants=[
            make_combatant(
                1,
                q=0,
                r=0,
                intelligence=14,
                spells=[FIRE_MISSILE, "shield"],
                mana=10,
                max_mana=10,
                weapon=WeaponState(),
            ),
            make_combatant(2, q=3, r=0, stops=2),
            make_combatant(3, q=-5, r=0),
            make_combatant(4, q=0, r=7),
        ],
    )


class TheAIsChoiceStandsTest(TestCase):
    def assert_widens(self, state: BattleState, **keywords) -> Decision:
        before = plain(state, **keywords)
        widened = wide(state, **keywords)
        self.assertEqual(widened.chosen, before.chosen)
        self.assertTrue(
            any(candidate is widened.chosen for candidate in widened.candidates)
        )
        payloads = [candidate.to_payload() for candidate in widened.candidates]
        for candidate in before.candidates:
            self.assertIn(candidate.to_payload(), payloads)
        self.assertEqual(widened.withheld, before.withheld)
        return widened

    def test_every_board_keeps_the_ais_choice_and_its_menu(self):
        for state in (
            two_beside(),
            three_at_range(bow()),
            three_at_range(SPEAR),
            caster_and_three(),
        ):
            self.assert_widens(state)
        self.assert_widens(caster_and_three(), magic=example_magic_rules())

    def test_a_better_shot_elsewhere_does_not_displace_the_ais(self):
        # The AI shoots the nearest enemy, armoured as it is; a shot at an
        # unarmoured one farther off scores higher but is a player's choice.
        widened = self.assert_widens(three_at_range(bow()))
        shots = aimed(widened, "f")
        self.assertEqual((widened.chosen.letter, widened.chosen.target_id), ("f", 2))
        self.assertIs(widened.chosen, shots[0])
        self.assertGreater(max(shot.score for shot in shots[1:]), shots[0].score)

    def test_a_better_cast_elsewhere_does_not_displace_the_ais(self):
        widened = self.assert_widens(caster_and_three())
        casts = aimed(widened, "h", FIRE_MISSILE)
        self.assertEqual(
            (widened.chosen.spell_key, widened.chosen.target_id), (FIRE_MISSILE, 2)
        )
        self.assertIs(widened.chosen, casts[0])
        self.assertGreater(casts[1].score, casts[0].score)


class TheReplayBattlesKeepTheInvariantTest(TestCase):
    """Every decision of every recorded replay battle, re-run with each
    actor's menu built both ways: the wide menu keeps the AI's choice, as a
    member, and holds the plain menu in its order."""

    def test_every_decision_of_every_replay_battle(self):
        checked = 0

        def choose(state: BattleState, actor) -> Decision:
            nonlocal checked
            before = policy.choose_option(state, actor)
            widened = policy.choose_option(state, actor, every_target=True)
            self.assertEqual(widened.chosen, before.chosen)
            self.assertTrue(
                any(candidate is widened.chosen for candidate in widened.candidates)
            )
            remaining = iter(widened.candidates)
            for candidate in before.candidates:
                self.assertIn(candidate, remaining)  # in order: consumes the rest
            checked += 1
            return before

        for scenario, seed in RECORDED:
            with self.subTest(scenario=scenario, seed=seed):
                state = SCENARIOS[scenario]()
                roller = SeededStubRoller(seed)
                for _turn in range(LONG_TURNS.get(scenario, TURNS)):
                    engine.run_turn(state, roller, lambda _event: None, choose)
        self.assertGreater(checked, 100)


class AttacksAtEveryTargetTest(TestCase):
    def test_attack_at_each_adjacent_enemy_the_ais_best_first(self):
        state = two_beside()
        strikes = aimed(wide(state), "j")
        self.assertEqual(targets(strikes), [3, 2])  # 3 has no armour
        self.assertEqual(targets(aimed(plain(state), "j")), [3])
        for strike in strikes:
            defender = state.by_id(target_id(strike))
            forecast, _rationale = policy._attack_forecast(
                state.by_id(1), defender, ranged=False
            )
            self.assertEqual(strike.forecast, forecast)
            self.assertIn(f"Strike {defender.name}:", strike.rationale)

    def test_missile_at_every_enemy_nearest_first(self):
        shots = aimed(wide(three_at_range(bow())), "f")
        self.assertEqual(targets(shots), [2, 3, 4])
        for shot in shots:
            self.assertIsNotNone(shot.forecast)
            self.assertIn("range", shot.rationale)

    def test_the_only_hand_weapon_is_kept_at_every_target(self):
        state = three_at_range(SPEAR)
        self.assertTrue(weapons.throw_leaves_no_hand_weapon(state.by_id(1)))
        throws = aimed(wide(state), "f")
        self.assertEqual(targets(throws), [2, 3, 4])
        for throw in throws:
            self.assertEqual(throw.score, policy.PLAYER_ONLY_SCORE)
            self.assertIsNotNone(throw.forecast)
            self.assertIn("so the AI keeps it", throw.rationale)

    def test_one_last_shot_at_each_adjacent_enemy_lowest_id_first(self):
        state = two_beside()
        state.by_id(1).weapon = SMALL_BOW
        shots = aimed(wide(state), "l")
        self.assertEqual(targets(shots), [2, 3])
        self.assertTrue(all(shot.forecast is not None for shot in shots))

    def test_charge_at_each_enemy_its_jog_ends_beside(self):
        state = BattleState(
            arena_radius=12,
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=4, r=0, facing=3),
                make_combatant(3, q=-5, r=0, facing=0),
                make_combatant(4, q=0, r=11, facing=1),  # beyond a jog
            ],
        )
        widened = wide(state)
        charges = aimed(widened, "b")
        self.assertEqual(targets(charges), [2, 3])
        self.assertTrue(all(charge.forecast is not None for charge in charges))
        # The yielded charge is offered at the same targets.
        self.assertEqual(targets(aimed(widened, "b_yield")), [2, 3])
        self.assertEqual(
            [charge.forecast for charge in aimed(widened, "b_yield")],
            [charge.forecast for charge in charges],
        )

    def test_a_charge_reaching_no_one_is_offered_once(self):
        state = BattleState(
            arena_radius=20,
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=15, r=0, facing=3),
                make_combatant(3, q=-16, r=0, facing=0),
            ],
        )
        charges = aimed(wide(state), "b")
        self.assertEqual(len(charges), 1)
        self.assertEqual(charges[0].score, 0.0)
        self.assertIsNone(charges[0].forecast)

    def test_strike_in_close_and_grapple_at_each_enemy_an_entry_admits(self):
        state = two_beside()
        state.by_id(1).weapon = DAGGER
        # Both agree to close, on a Game Master's ruling (#867).
        state.by_id(1).hth_agreed_with = [2, 3]
        widened = wide(state)
        strikes = aimed(widened, "t")
        self.assertEqual(sorted(targets(strikes)), [2, 3])
        self.assertEqual(strikes[0].score, max(strike.score for strike in strikes))
        self.assertTrue(all(strike.forecast is not None for strike in strikes))
        self.assertEqual(targets(aimed(widened, "o")), [2, 3])

    def test_no_grapple_attempt_at_an_enemy_already_held(self):
        # The engine refuses a grip on a held enemy (target_held), so neither
        # the AI's own ATTEMPT HTH nor the every-target menu offers one; a
        # strike in close at it stays, since the engine allows that (#60).
        state = one_held_one_free()
        self.assertEqual(targets(aimed(plain(state), "o")), [3])
        widened = wide(state)
        self.assertEqual(targets(aimed(widened, "o")), [3])
        self.assertEqual(sorted(targets(aimed(widened, "t"))), [2, 3])

    def test_every_grapple_attempt_offered_is_one_the_engine_takes_up(self):
        state = one_held_one_free()
        for attempt in aimed(wide(state), "o"):
            board = deepcopy(state)
            attacker = board.by_id(1)
            attacker.chosen_letter, attacker.chosen_target = "o", attempt.target_id
            events: list[dict] = []
            engine.TurnRunner(
                board, SeededStubRoller(3), events.append
            ).attempt_grapple(attacker)
            refusals = [
                event
                for event in events
                if "grapple_refused" in (event.get("payload") or {})
            ]
            self.assertEqual(refusals, [], attempt)

    def test_no_grapple_attempt_when_the_only_admitted_enemy_is_held(self):
        state = one_held_one_free()
        state.by_id(3).position = (-5, 0)
        widened = wide(state)
        self.assertEqual(aimed(widened, "o"), [])
        self.assertEqual(targets(aimed(widened, "t")), [2])


class CastsAtEveryTargetTest(TestCase):
    def test_a_damaging_spell_at_every_enemy_nearest_first(self):
        state = caster_and_three()
        widened = wide(state)
        casts = aimed(widened, "h", FIRE_MISSILE)
        self.assertEqual(targets(casts), [2, 3, 4])
        for cast in casts:
            name = state.by_id(target_id(cast)).name
            self.assertIn(f"on {name}", cast.rationale)
            assert cast.forecast is not None
            self.assertEqual(
                cast.forecast.armour_stops, state.by_id(target_id(cast)).stops
            )
        # A buff has no target, and is offered once.
        self.assertEqual(len(aimed(widened, "h", "shield")), 1)

    def test_pushed_and_borrowing_casts_at_every_enemy(self):
        state = caster_and_three()
        pushed = {
            candidate.target_id
            for candidate in wide(state, magic=example_magic_rules()).candidates
            if candidate.spell_key == FIRE_MISSILE and candidate.push_mana
        }
        self.assertEqual(pushed, {2, 3, 4})
        state.by_id(1).spells = ["lightning_bolt"]
        state.by_id(1).mana = 2
        borrowing = replace(borrowing_magic_rules(), push=None)
        borrowed = [
            candidate
            for candidate in wide(state, magic=borrowing).candidates
            if candidate.borrow
        ]
        self.assertEqual(targets(borrowed), [2, 3, 4])
        self.assertTrue(all(candidate.forecast is not None for candidate in borrowed))

    def test_a_held_mage_casts_at_every_enemy(self):
        state = caster_and_three()
        held, captor = state.by_id(1), state.by_id(2)
        captor.position = (1, 0)
        held.grappled_by, captor.grappling = 2, 1
        held.spell_mastery = {FIRE_MISSILE: 3}
        widened = wide(state)
        self.assertEqual(widened.chosen.letter, "v")  # Struggle Free stands
        casts = aimed(widened, "r", FIRE_MISSILE)
        self.assertEqual(targets(casts), [2, 3, 4])


class TheEngineResolvesTheTargetChosenTest(TestCase):
    def test_a_shot_aimed_past_the_nearest_lands_where_aimed(self):
        state = three_at_range(bow())

        def choose(battle: BattleState, actor) -> Decision:
            menu = policy.choose_option(battle, actor, every_target=True)
            # The others drop where they stand, so the board holds still.
            letter, target = ("f", 4) if actor.combatant_id == 1 else ("d", None)
            picked = next(
                candidate
                for candidate in menu.candidates
                if candidate.letter == letter and candidate.target_id == target
            )
            return Decision(chosen=picked, candidates=menu.candidates)

        events: list[dict] = []
        engine.run_turn(state, SeededStubRoller(3), events.append, choose)
        shots = [
            event
            for event in events_of_type(events, "action")
            if event["actor"] == state.by_id(1).name and event["payload"].get("ranged")
        ]
        self.assertTrue(shots)
        self.assertEqual({shot["payload"]["target"] for shot in shots}, {4})


class ThrowLeavesNoHandWeaponTest(TestCase):
    def test_only_a_thrown_hand_weapon_with_no_spare(self):
        throws = make_combatant(1, weapon=SPEAR)
        self.assertTrue(weapons.throw_leaves_no_hand_weapon(throws))
        throws.spare_weapons = [make_combatant(2).weapon]
        self.assertFalse(weapons.throw_leaves_no_hand_weapon(throws))
        throws.spare_weapons = [SMALL_BOW]  # a bow is no hand weapon
        self.assertTrue(weapons.throw_leaves_no_hand_weapon(throws))
        self.assertFalse(weapons.throw_leaves_no_hand_weapon(make_combatant(3)))
        self.assertFalse(
            weapons.throw_leaves_no_hand_weapon(make_combatant(4, weapon=bow()))
        )
