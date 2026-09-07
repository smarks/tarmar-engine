"""AI policy: probabilities, targeting, and decision candidates."""

from unittest import TestCase

from tarmar_engine import policy
from tarmar_engine.state import BattleState, WeaponState

from .test_state import make_combatant


def bow(name="Longbow"):
    return WeaponState(
        item_id="longbow",
        name=name,
        weapon_class="Missile — Bows",
        damage="1d6+2",
        str_req=11,
        is_missile=True,
    )


class ThreeD6Test(TestCase):
    def test_exact_probabilities(self):
        self.assertEqual(policy.three_d6_at_most(2), 0.0)
        self.assertEqual(policy.three_d6_at_most(3), 1 / 216)
        self.assertEqual(policy.three_d6_at_most(10), 0.5)
        self.assertEqual(policy.three_d6_at_most(18), 1.0)


class NearestEnemyTest(TestCase):
    def test_prefers_closest_then_lowest_fatigue(self):
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0),
                make_combatant(2, q=3, r=0),
                make_combatant(3, q=-3, r=0, fatigue=5),
            ]
        )
        chosen = policy.nearest_enemy(state, state.by_id(1))
        assert chosen is not None  # only None when the actor has no enemies
        self.assertEqual(chosen.combatant_id, 3)  # same distance, lower fatigue

    def test_none_when_no_enemies(self):
        state = BattleState(combatants=[make_combatant(1)])
        self.assertIsNone(policy.nearest_enemy(state, state.by_id(1)))


class ChooseOptionTest(TestCase):
    def test_prone_actor_must_stand(self):
        state = BattleState(
            combatants=[
                make_combatant(1, prone=True),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        self.assertIn(decision.chosen.letter, ("g", "p"))
        self.assertEqual(len(decision.candidates), 1)

    def test_engaged_fighter_attacks_adjacent_enemy(self):
        # Enemy at (1,0) facing 3 puts us in its front hex: engaged.
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "j")
        self.assertEqual(decision.chosen.target_id, 2)
        letters = {candidate.letter for candidate in decision.candidates}
        self.assertIn("k", letters)
        self.assertIn("n", letters)

    def test_every_candidate_has_score_and_rationale(self):
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        for candidate in decision.candidates:
            payload = candidate.to_payload()
            self.assertIsInstance(payload["score"], float)
            self.assertTrue(payload["rationale"])

    def test_distant_fighter_closes_or_charges(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=-6, r=0),
                make_combatant(2, q=6, r=0),
            ],
        )
        decision = policy.choose_option(state, state.by_id(1))
        self.assertIn(decision.chosen.letter, ("a", "b"))
        self.assertEqual(decision.chosen.target_id, 2)

    def test_archer_shoots_at_range(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, weapon=bow(), q=-4, r=0),
                make_combatant(2, q=4, r=0),
            ],
        )
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "f")
        self.assertIn("P(hit)", decision.chosen.rationale)

    def test_wizard_with_mana_casts(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(
                    1,
                    weapon=WeaponState(
                        item_id="dagger",
                        name="Dagger",
                        weapon_class="Piercing",
                        damage="1d6-1",
                    ),
                    intelligence=16,
                    spells=["fire_missile", "shield"],
                    mana=10,
                    max_mana=10,
                    q=-4,
                    r=0,
                ),
                make_combatant(2, q=4, r=0),
            ],
        )
        decision = policy.choose_option(state, state.by_id(1))
        cast_candidates = [
            candidate for candidate in decision.candidates if candidate.letter == "h"
        ]
        self.assertEqual(len(cast_candidates), 2)  # one per affordable spell
        keys = {candidate.spell_key for candidate in cast_candidates}
        self.assertEqual(keys, {"fire_missile", "shield"})

    def test_caster_skips_unaffordable_and_already_active_spells(self):
        state = BattleState(
            combatants=[
                make_combatant(
                    1,
                    spells=["lightning_bolt", "shield"],
                    active_spells=["shield"],
                    mana=1,
                    q=-4,
                    r=0,
                ),
                make_combatant(2, q=4, r=0),
            ]
        )
        candidates = policy._cast_candidates(state, state.by_id(1), "h")
        self.assertEqual(candidates, [])  # bolt too dear, shield already up

    def test_hurt_wizard_values_heal(self):
        state = BattleState(
            combatants=[
                make_combatant(
                    1, spells=["heal"], mana=5, fatigue=8, wisdom=14, q=-4, r=0
                ),
                make_combatant(2, q=4, r=0),
            ]
        )
        candidates = policy._cast_candidates(state, state.by_id(1), "h")
        self.assertEqual(len(candidates), 1)
        self.assertIn("fatigue missing", candidates[0].rationale)
        self.assertGreater(candidates[0].score, 0)

    def test_dodge_scores_with_missile_threats(self):
        state = BattleState(
            arena_radius=10,
            combatants=[
                make_combatant(1, q=-6, r=0),
                make_combatant(2, q=6, r=0, weapon=bow()),
            ],
        )
        decision = policy.choose_option(state, state.by_id(1))
        dodge = next(
            candidate for candidate in decision.candidates if candidate.letter == "c"
        )
        self.assertGreater(dodge.score, 0)
        self.assertIn("missile threat", dodge.rationale)

    def test_lone_survivor_still_gets_a_candidate(self):
        state = BattleState(combatants=[make_combatant(1)])
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "a")
        self.assertEqual(decision.chosen.score, 0.0)


class GrappleChoiceTest(TestCase):
    def test_engaged_fighter_considers_but_declines_grapple(self):
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "j")  # never o: valued 0
        grapple = next(
            candidate for candidate in decision.candidates if candidate.letter == "o"
        )
        self.assertEqual(grapple.score, 0.0)
        self.assertEqual(grapple.target_id, 2)

    def test_beast_never_offered_a_grapple_candidate(self):
        state = BattleState(
            combatants=[
                make_beast_actor(1, q=0, r=0, facing=0),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        letters = {candidate.letter for candidate in decision.candidates}
        self.assertNotIn("o", letters)

    def test_grappled_figure_always_struggles_free(self):
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, grappled_by=2),
                make_combatant(2, q=1, r=0, grappling=1),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "v")
        self.assertEqual(decision.chosen.target_id, 2)
        letters = {candidate.letter for candidate in decision.candidates}
        self.assertEqual(letters, {"v", "t", "hold_still"})

    def test_grappler_always_squeezes(self):
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, grappling=2),
                make_combatant(2, q=1, r=0, grappled_by=1),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "squeeze")
        self.assertEqual(decision.chosen.target_id, 2)
        letters = {candidate.letter for candidate in decision.candidates}
        self.assertEqual(letters, {"squeeze", "maintain", "release"})


def make_beast_actor(combatant_id=1, **overrides):
    fields = {
        "archetype": "beast",
        "is_beast": True,
        "weapon": WeaponState(name="Bite", weapon_class="Piercing", damage="1d6+1"),
    }
    fields.update(overrides)
    return make_combatant(combatant_id, **fields)


class BeastPolicyTest(TestCase):
    def engaged_pair(self, **beast_overrides):
        """A beast at the origin, engaged by a fighter at (1,0)."""
        return BattleState(
            arena_radius=6,
            combatants=[
                make_beast_actor(1, q=0, r=0, facing=0, **beast_overrides),
                make_combatant(2, q=1, r=0, facing=3),
            ],
        )

    def test_beast_candidates_are_the_melee_only_subset(self):
        # Engaged: j/k/n only — never missiles (f) or spells (h/r).
        state = self.engaged_pair()
        decision = policy.choose_option(state, state.by_id(1))
        letters = {candidate.letter for candidate in decision.candidates}
        self.assertTrue(letters <= {"j", "k", "n"})
        # Disengaged: a/b/c only.
        state = BattleState(
            arena_radius=8,
            combatants=[
                make_beast_actor(1, q=-4, r=0, facing=0),
                make_combatant(2, q=4, r=0, facing=3),
            ],
        )
        decision = policy.choose_option(state, state.by_id(1))
        letters = {candidate.letter for candidate in decision.candidates}
        self.assertTrue(letters <= {"a", "b", "c"})

    def test_healthy_beast_attacks(self):
        state = self.engaged_pair()
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "j")

    def test_beast_hurt_gauge_reads_body_not_fatigue(self):
        # Fatigue nearly gone but body full: the beast presses the attack.
        state = self.engaged_pair(fatigue=3)
        decision = policy.choose_option(state, state.by_id(1))
        self.assertEqual(decision.chosen.letter, "j")

    def test_wounded_beast_guards_or_flees(self):
        # Body below the threshold: the wounded-beast Defend bonus and the
        # hurt disengage multiplier both kick in — the beast stops attacking.
        state = self.engaged_pair(body=5)
        decision = policy.choose_option(state, state.by_id(1))
        self.assertIn(decision.chosen.letter, ("k", "n"))
        defend = next(c for c in decision.candidates if c.letter == "k")
        self.assertIn("wounded beast", defend.rationale)


class CheckableRationaleTest(TestCase):
    """tarmar-studio #301: a reader must be able to verify every score.

    The old log printed ``P(hit) 60% x 4.7 expected damage`` beside a score
    of ``2.835`` — three numbers that do not multiply out, because the
    factors were rounded for display while the score was not. These tests
    pin the two guarantees that fix it: the printed factors reproduce the
    printed score, and the inputs behind those factors are on the line.
    """

    def melee_pair(self, **overrides):
        return BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, facing=0, **overrides),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )

    def test_printed_factors_reproduce_the_printed_score(self):
        state = self.melee_pair()
        decision = policy.choose_option(state, state.by_id(1))
        for candidate in decision.candidates:
            if "=" not in candidate.rationale:
                continue
            printed = candidate.rationale.rsplit("=", 1)[1].strip()
            self.assertEqual(
                printed,
                policy.score_text(candidate.score),
                f"({candidate.letter}) rationale ends in {printed} "
                f"but scores {candidate.score}",
            )

    def test_product_of_multiplies_the_rounded_factors(self):
        # 0.6 x 4.725 is 2.835 exactly, but 0.600 x 4.725 as printed is
        # 2.835 too — the discipline only bites when a factor is truncated.
        self.assertEqual(policy.product_of(0.6, 4.7254), 2.835)
        self.assertEqual(policy.product_of(1 / 3, 3.0), 0.999)

    def test_melee_rationale_names_every_input(self):
        state = self.melee_pair()
        decision = policy.choose_option(state, state.by_id(1))
        strike = next(c for c in decision.candidates if c.letter == "j")
        self.assertIn("Broadsword", strike.rationale)  # weapon in hand
        self.assertIn("1 hex", strike.rationale)  # distance, as missile shows
        self.assertIn("d20 +", strike.rationale)  # the attack bonus
        self.assertIn("vs TN", strike.rationale)  # what it is tested against
        self.assertIn("of 20 faces", strike.rationale)  # where P(hit) came from
        self.assertIn("armour stops", strike.rationale)  # what damage loses

    def test_missile_rationale_keeps_its_range_band(self):
        state = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, weapon=bow()),
                make_combatant(2, q=6, r=0),
            ]
        )
        decision = policy.choose_option(state, state.by_id(1))
        shot = next(c for c in decision.candidates if c.letter == "f")
        self.assertIn("6 hexes", shot.rationale)
        self.assertIn("range ", shot.rationale)
        self.assertIn("Longbow", shot.rationale)

    def test_move_says_which_side_of_the_jog_line_it_fell(self):
        near = BattleState(
            combatants=[
                make_combatant(1, q=0, r=0, weapon=bow()),
                make_combatant(2, q=3, r=0),
            ]
        )
        move = next(
            c for c in policy.choose_option(near, near.by_id(1)).candidates
            if c.letter == "a"
        )
        self.assertIn("within a 7-hex jog, so half of 1.000", move.rationale)
        self.assertEqual(move.score, 0.5)

        far = BattleState(
            arena_radius=20,
            combatants=[
                make_combatant(1, q=0, r=0, weapon=bow()),
                make_combatant(2, q=12, r=0),
            ],
        )
        move = next(
            c for c in policy.choose_option(far, far.by_id(1)).candidates
            if c.letter == "a"
        )
        self.assertIn("beyond a 7-hex jog, so the full 1.000", move.rationale)

    def test_payload_carries_a_score_string_matching_the_rationale(self):
        state = self.melee_pair()
        strike = next(
            c for c in policy.choose_option(state, state.by_id(1)).candidates
            if c.letter == "j"
        )
        payload = strike.to_payload()
        self.assertEqual(payload["score_text"], policy.score_text(strike.score))
        self.assertTrue(strike.rationale.endswith(payload["score_text"]))

    def test_wounded_beast_caution_stays_in_the_printed_chain(self):
        state = BattleState(
            combatants=[
                make_beast_actor(1, q=0, r=0, facing=0, body=5),
                make_combatant(2, q=1, r=0, facing=3),
            ]
        )
        strike = next(
            c for c in policy.choose_option(state, state.by_id(1)).candidates
            if c.letter == "j"
        )
        self.assertIn("body left", strike.rationale)
        self.assertTrue(strike.rationale.endswith(policy.score_text(strike.score)))
