"""Probe tests for the menu of a True mage, and for the AI's crafting
(tarmar-engine #77): the True casts and the workings ``policy`` offers, the
craft that stands and its continuation, and how each is scored.

Every number and name is invented, as in ``test_true_magic``: a True skill
called "knotwork", a kind of magic called "knot", and the effects "scorch",
"salve", "omen" and "hush". The crafter is the test wizard (INT 14, WIS 12,
DEX 12, 10 mana; Fire Missile, Shield and Heal known) at "knotwork" 2: its
Channel by the "knot" kind is 3 x 2 + 1, and its highest tier is 2 x 2. A
working takes two craft turns a tier and drains 3 Body a point when it goes
off.
"""

import random
from dataclasses import replace
from functools import partial
from unittest import TestCase

from tarmar_engine import actions, engine, policy
from tarmar_engine.magic import (
    CHANNEL,
    WORKING_KEY_PREFIX,
    WORKING_KEY_SEPARATOR,
    ChannelDerivation,
    MagicRules,
    SpellEffectKind,
    WorkingEffect,
)
from tarmar_engine.profile import TarmarProfile
from tarmar_engine.spells import get_spell
from tarmar_engine.state import (
    BattleState,
    CombatantState,
    RunawayState,
    WeaponState,
)

from .event_stream_scenarios import BATTLES, MENU_SEED_OFFSET
from .test_borrowing import example_borrowing_rules
from .test_cast_pieces import hand_to_hand, held, runner_on
from .test_counterspell import (
    FAMILIARITY_RULES,
    counters,
    decide,
    example_counterspell_rules,
)
from .test_craft import (
    DEFEND,
    KNOT_CHANNEL,
    choose_craft,
    craft_actions,
    craft_rules,
    craft_true_magic,
    craft_turns,
    crafter,
    crafter_beside,
    losses,
    run_turn,
    standing_craft,
    steady_craft,
    working_key,
)
from .test_craft_completion import completions
from .test_engine import ScriptedRoller, SeededStubRoller
from .test_magic import example_casting_failure_rules, example_push_rules
from .test_runaway import example_runaway_rules
from .test_state import make_combatant
from .test_true_magic import (
    EFFECTS,
    KNOT,
    KNOT_KIND,
    KNOT_SKILL,
    SCORCH,
    knot_channel_rules,
)

RULES = craft_rules()
#: The same rules with no True Magic in them.
NO_TRUE_MAGIC = replace(RULES, true_magic=None)
#: What the crafter's first working forecasts at figure 2: its roll under
#: WIS 12 + 2 x 2, the aim under DEX 12, and tier 1 of Scorch's 1d4+1.
SCORCH_ONE_FORECAST = 2.544
#: The sibling of Fire Missile in each other element, in the catalog's order.
MISSILE_SIBLINGS = [
    "water_missile",
    "lightning_missile",
    "earth_missile",
    "wind_missile",
]


def menu(state: BattleState, rules: MagicRules = RULES, **keywords) -> policy.Decision:
    """Figure 1's menu under the rules."""
    return policy.choose_option(state, state.by_id(1), rules, **keywords)


def casts(state: BattleState, rules: MagicRules | None = RULES, **keywords):
    """What the disengaged cast letter offers figure 1."""
    return policy._cast_candidates(state, state.by_id(1), "h", magic=rules, **keywords)


def crafts(candidates) -> list[policy.Candidate]:
    """The craft actions among some candidates."""
    return [
        candidate
        for candidate in candidates
        if candidate.letter == actions.CRAFT_OPTION
    ]


def anchors(candidates) -> list[policy.Candidate]:
    """The anchor options among some candidates."""
    return [
        candidate
        for candidate in candidates
        if candidate.letter == policy.ANCHOR_OPTION
    ]


def true_casts(candidates) -> list[policy.Candidate]:
    """The True casts among some candidates."""
    return [candidate for candidate in candidates if candidate.true_cast]


def plain(candidates) -> list[policy.Candidate]:
    """The candidates that neither push nor borrow."""
    return [
        candidate
        for candidate in candidates
        if not candidate.push_mana and not candidate.borrow
    ]


def forecast_of(candidate: policy.Candidate) -> policy.Forecast:
    """A candidate's forecast, for a test that expects one."""
    forecast = candidate.forecast
    assert forecast is not None, f"{candidate.name} carries no forecast"
    return forecast


def tiers_of(candidates, effect: str = "scorch") -> list[int]:
    """The tiers at which an effect's working is among some candidates, in
    the order they are listed, each once."""
    tiers: list[int] = []
    for candidate in candidates:
        prefix, _, tier = candidate.spell_key.rpartition(WORKING_KEY_SEPARATOR)
        named = WORKING_KEY_SEPARATOR.join((WORKING_KEY_PREFIX, effect))
        if prefix == named and int(tier) not in tiers:
            tiers.append(int(tier))
    return tiers


def empty_handed(**caster_overrides) -> BattleState:
    """The crafter with nothing in hand, so it has no charge to weigh."""
    return crafter(weapon=WeaponState(), **caster_overrides)


def crafting(
    effect: str, tier: int, turns: int, *, push: int = 0, borrow: bool = False
) -> BattleState:
    """The crafter some turns into a working: one it neither pushes nor
    borrows for, unless told to."""
    state = crafter()
    choose_craft(state, effect, tier, push=push, borrow=borrow)
    return craft_turns(state, turns)


def three_sided() -> BattleState:
    """The crafter and a teammate, who lies unconscious, against two
    enemies, one nearer; a fourth figure of the other side lies dead."""
    state = crafter(team="red")
    state.by_id(2).team = "blue"
    state.combatants += [
        make_combatant(3, q=-3, r=3, team="red", conscious=False),
        make_combatant(4, q=0, r=0, team="blue"),
        make_combatant(5, q=3, r=3, team="blue", alive=False),
    ]
    return state


class NoTrueMagicNoChangeTest(TestCase):
    """With no rules passed, none for True Magic, or no level in the True
    skill, a menu is the one it was."""

    def test_the_cast_letter_keeps_its_positional_signature_and_its_list(self):
        state = crafter()
        caster = state.by_id(1)
        today = policy._cast_candidates(state, caster, "h")
        self.assertEqual(
            [candidate.spell_key for candidate in today],
            ["fire_missile", "shield", "heal"],
        )
        self.assertEqual(casts(state, None), today)
        self.assertEqual(casts(state, MagicRules()), today)
        self.assertEqual(casts(state, NO_TRUE_MAGIC), today)
        # Positional, casting failure rules included, as it always was.
        floor = example_casting_failure_rules()
        self.assertEqual(
            policy._cast_candidates(state, caster, "h", False, floor, True),
            policy._cast_candidates(
                state, caster, "h", casting_failure=floor, every_target=True
            ),
        )

    def test_no_level_in_the_true_skill_offers_nothing_new(self):
        for lifted in ([], [CHANNEL]):
            for skill_levels in ({}, {KNOT_SKILL: 0}):
                with self.subTest(lifted=lifted, skill_levels=skill_levels):
                    # With Channel lifted the tier cap is no bound, so only
                    # the level itself keeps the crafts off the menu.
                    state = crafter(
                        skill_levels=skill_levels, lifted_magic_bounds=lifted
                    )
                    self.assertEqual(
                        casts(state),
                        policy._cast_candidates(state, state.by_id(1), "h"),
                    )
                    decision = menu(state, every_target=True)
                    self.assertEqual(crafts(decision.candidates), [])
                    self.assertEqual(true_casts(decision.candidates), [])

    def test_a_level_with_no_true_magic_rules_offers_nothing_new(self):
        decision = menu(crafter(), NO_TRUE_MAGIC, every_target=True)
        self.assertEqual(crafts(decision.candidates), [])
        self.assertEqual(true_casts(decision.candidates), [])

    def test_the_recorded_battles_menus_are_theirs_with_true_magic_injected(self):
        # The battles of the event-stream fixture that inject magic, run as
        # the fixture runs them (seeded dice, a seeded draw from each menu).
        # True Magic is injected beside every mechanic they have, and no
        # figure of theirs has a level in the True skill: each figure's
        # menu, and the AI's choice from it, is the one it has without, at
        # every decision of every battle.
        compared = 0
        for battle in BATTLES:
            without = battle.magic
            if without is None or without.channel is None:
                continue
            with_true_magic = replace(
                without,
                channel=replace(
                    without.channel,
                    derivations={**without.channel.derivations, KNOT_KIND: KNOT},
                ),
                true_magic=craft_true_magic(),
            )
            state = battle.build()
            roller = SeededStubRoller(battle.seed)
            draws = random.Random(battle.seed + MENU_SEED_OFFSET)

            def choose(
                state: BattleState,
                actor: CombatantState,
                without: MagicRules = without,
                with_true_magic: MagicRules = with_true_magic,
                draws: random.Random = draws,
            ) -> policy.Decision:
                nonlocal compared
                decision = policy.choose_option(
                    state, actor, without, every_target=True
                )
                injected = policy.choose_option(
                    state, actor, with_true_magic, every_target=True
                )
                self.assertEqual(injected, decision)
                compared += 1
                drawn = draws.randrange(len(decision.candidates))
                decision.chosen = decision.candidates[drawn]
                return decision

            for _turn in range(battle.turns):
                engine.run_turn(
                    state,
                    roller,
                    lambda event: None,
                    choose,
                    profile=TarmarProfile(magic=without),
                )
        self.assertGreater(compared, 300)


class OneCastingFailureTest(TestCase):
    """The casting failure rules come from one place: the magic rules, when
    they are passed."""

    FLOOR_RULES = replace(RULES, casting_failure=example_casting_failure_rules())

    def callers(self) -> dict:
        """Each function that takes both, as a call on a state it suits."""
        free = crafter()
        close = hand_to_hand(crafter_beside(weapon=WeaponState()))
        gripped = held(crafter_beside(spell_mastery={"shield": 3}))
        holding = crafter_beside(spell_mastery={"shield": 3})
        holding.by_id(1).grappling = 2
        holding.by_id(2).grappled_by = 1
        return {
            "_score_options": partial(policy._score_options, free, free.by_id(1)),
            "_cast_candidates": partial(
                policy._cast_candidates, free, free.by_id(1), "h"
            ),
            "_hth_casts": partial(policy._hth_casts, close, close.by_id(1)),
            "_mastery_three_casts": partial(
                policy._mastery_three_casts, gripped, gripped.by_id(1)
            ),
            "_grappled_decision": partial(
                policy._grappled_decision, gripped, gripped.by_id(1)
            ),
            "_grappler_decision": partial(
                policy._grappler_decision, holding, holding.by_id(1)
            ),
        }

    @staticmethod
    def rationales(result) -> list[str]:
        candidates = (
            result.candidates if isinstance(result, policy.Decision) else result
        )
        return [candidate.rationale for candidate in candidates]

    def test_a_casting_failure_that_is_not_the_magic_rules_own_is_refused(self):
        for name, call in self.callers().items():
            with self.subTest(function=name):
                with self.assertRaisesRegex(ValueError, "pass the magic rules alone"):
                    call(
                        casting_failure=example_casting_failure_rules(),
                        magic=self.FLOOR_RULES,
                    )
                # Rules beside magic rules that carry none are refused too.
                with self.assertRaisesRegex(ValueError, "pass the magic rules alone"):
                    call(casting_failure=example_casting_failure_rules(), magic=RULES)

    def test_the_magic_rules_own_are_the_ones_used(self):
        for name, call in self.callers().items():
            with self.subTest(function=name):
                alone = call(magic=self.FLOOR_RULES)
                self.assertTrue(
                    any("fumble floor 12" in text for text in self.rationales(alone))
                )
                same_object = call(
                    casting_failure=self.FLOOR_RULES.casting_failure,
                    magic=self.FLOOR_RULES,
                )
                self.assertEqual(same_object, alone)
                # Magic rules that carry none price a cast with none.
                self.assertFalse(
                    any(
                        "fumble floor" in text
                        for text in self.rationales(call(magic=RULES))
                    )
                )

    def test_with_no_magic_rules_the_argument_is_used_as_it_was(self):
        for name, call in self.callers().items():
            with self.subTest(function=name):
                floored = call(casting_failure=example_casting_failure_rules())
                self.assertTrue(
                    any("fumble floor 12" in text for text in self.rationales(floored))
                )


class TrueCastMenuTest(TestCase):
    """A True mage is offered a True cast of each spell it may True cast."""

    def test_the_recipe_casts_come_first_then_the_true_casts_then_the_crafts(self):
        offered = casts(crafter())
        kinds = [
            "craft"
            if candidate.letter == actions.CRAFT_OPTION
            else "true cast"
            if candidate.true_cast
            else "cast"
            for candidate in offered
        ]
        self.assertEqual(kinds, ["cast"] * 3 + ["true cast"] * 7 + ["craft"] * 12)
        self.assertEqual(
            [candidate.spell_key for candidate in offered[:3]],
            ["fire_missile", "shield", "heal"],
        )

    def test_one_is_offered_for_each_spell_known_and_each_element_sibling(self):
        offered = true_casts(casts(crafter()))
        self.assertEqual(
            [candidate.spell_key for candidate in offered],
            ["fire_missile", *MISSILE_SIBLINGS, "shield", "heal"],
        )
        # An unrelated spell the caster never learned is not among them.
        self.assertNotIn("wound", [candidate.spell_key for candidate in offered])

    def test_it_takes_the_casts_letter_and_is_left_to_a_player(self):
        state = crafter()
        recipe = casts(state, None)[0]
        self.assertEqual(recipe.spell_key, "fire_missile")
        for letter in ("h", "r"):
            with self.subTest(letter=letter):
                offered = true_casts(
                    policy._cast_candidates(state, state.by_id(1), letter, magic=RULES)
                )
                self.assertEqual({candidate.letter for candidate in offered}, {letter})
                self.assertEqual(
                    {candidate.score for candidate in offered},
                    {policy.PLAYER_ONLY_SCORE},
                )
        water = true_casts(casts(state))[1]
        self.assertEqual(
            (water.name, water.target_id, water.push_mana, water.borrow),
            ("CAST SPELL Water Missile (TRUE CAST)", 2, 0, False),
        )
        # It makes the spell's own roll, so the cast's forecast stands.
        self.assertEqual(water.forecast, recipe.forecast)
        self.assertEqual(
            water.rationale,
            "True cast, its Channel by Knotwork: 3d6 ≤ INT 14 — P(cast) 0.907 "
            "x P(aim) 0.741 (3d6 ≤ DEX 12) — P(land) 0.672 x 3.500 expected "
            "damage on Fighter 2 (1d6 less 0 armour stops) = 2.352; the AI "
            "does not True cast = 0.000",
        )
        self.assertIs(water.to_payload()["true_cast"], True)

    def test_none_past_the_purse_or_of_a_spell_already_kept_up(self):
        self.assertEqual(true_casts(casts(crafter(mana=0))), [])
        self.assertEqual(len(true_casts(casts(crafter(mana=0), beyond_purse=True))), 7)
        kept_up = true_casts(casts(crafter(active_spells=["shield"])))
        self.assertNotIn("shield", [candidate.spell_key for candidate in kept_up])
        self.assertEqual(len(kept_up), 6)

    def test_the_ai_never_picks_one_whatever_it_forecasts(self):
        # Bare-handed and out of reach, with no working that damages on the
        # menu: nothing the AI scores comes near what a True cast forecasts.
        rules = craft_rules(effects=EFFECTS[1:])
        decision = menu(empty_handed(), rules)
        best = max(
            (candidate for candidate in decision.candidates if candidate.forecast),
            key=lambda candidate: forecast_of(candidate).score,
        )
        self.assertTrue(best.true_cast)
        self.assertGreater(forecast_of(best).score, decision.chosen.score)
        self.assertFalse(decision.chosen.true_cast)
        self.assertEqual(decision.chosen.letter, "a")


class WithheldByKindTest(TestCase):
    """Channel trims each casting by the kind of magic it names."""

    #: The example rules with the "knot" Channel equal to the "knotwork"
    #: level, so a level-3 spell is past it at level 2.
    TIGHT = replace(
        RULES,
        channel=replace(
            knot_channel_rules(),
            derivations={
                **knot_channel_rules().derivations,
                KNOT_KIND: ChannelDerivation(
                    source_skill=KNOT_SKILL, multiplier=1, offset=0
                ),
            },
        ),
    )

    def test_a_recipe_cast_and_a_true_cast_of_one_spell_each_keep_their_line(self):
        # No level recorded in the Bolt: its own Channel is 0.
        state = crafter(spells=["lightning_bolt"])
        decision = menu(state, self.TIGHT)
        self.assertEqual(
            decision.withheld[:2],
            [
                "Lightning Bolt withheld: it costs 3 mana, past Fighter 1's "
                "Channel 0 for it",
                "Lightning Bolt as a True cast withheld: it costs 3 mana, past "
                "Fighter 1's Channel 2 for it",
            ],
        )
        # And one for the True cast of the Bolt of each other element.
        bolts = [line for line in decision.withheld if "Bolt" in line]
        self.assertEqual(len(bolts), 6)
        self.assertEqual(
            [line for line in bolts if "as a True cast" not in line], bolts[:1]
        )
        self.assertEqual(
            [
                candidate
                for candidate in decision.candidates
                if candidate.spell_key.endswith("_bolt")
            ],
            [],
        )

    def test_each_is_judged_by_its_own_channel(self):
        # The recipe cast past its Channel, the True cast within its own.
        true_only = menu(crafter())
        self.assertIn(
            "Fire Missile withheld: it costs 1 mana, past Fighter 1's Channel 0 for it",
            true_only.withheld,
        )
        offered = [
            candidate
            for candidate in plain(true_only.candidates)
            if candidate.spell_key == "fire_missile"
        ]
        self.assertEqual([candidate.true_cast for candidate in offered], [True])
        # The True cast past its Channel, the recipe cast within its own
        # (2 x 4 - 1).
        recipe_only = menu(
            crafter(
                spells=["lightning_bolt"], spell_skill_levels={"lightning_bolt": 4}
            ),
            self.TIGHT,
        )
        offered = [
            candidate
            for candidate in plain(recipe_only.candidates)
            if candidate.spell_key == "lightning_bolt"
        ]
        self.assertEqual([candidate.true_cast for candidate in offered], [False])
        bolts = [line for line in recipe_only.withheld if "Bolt" in line]
        self.assertEqual(len(bolts), 5)
        for line in bolts:
            self.assertIn("as a True cast withheld", line)

    def test_no_working_past_channel_is_offered(self):
        # Five tiers a level: the cap is 10, past the Channel of 2.
        rules = replace(self.TIGHT, true_magic=craft_true_magic(tiers_per_level=5))
        decision = menu(crafter(), rules)
        self.assertEqual(tiers_of(plain(decision.candidates)), [1, 2])
        self.assertIn(
            "Scorch (tier 3) withheld: it costs 3 mana, past Fighter 1's "
            "Channel 2 for it",
            decision.withheld,
        )
        self.assertEqual(
            len([line for line in decision.withheld if line.startswith("Scorch")]), 8
        )


class CraftMenuTest(TestCase):
    """A True mage is offered the workings it may begin to craft."""

    def test_one_per_effect_and_tier_under_the_craft_option(self):
        offered = crafts(casts(crafter()))
        self.assertEqual(
            [candidate.spell_key for candidate in offered],
            [
                working_key(effect, tier)
                for effect in ("scorch", "salve", "hush")
                for tier in (1, 2, 3, 4)
            ],
        )
        first = offered[0]
        self.assertEqual(
            (first.name, first.target_id, first.push_mana, first.borrow),
            ("CRAFT WORKING Scorch (tier 1)", 2, 0, False),
        )
        self.assertFalse(first.true_cast)
        # The engaged cast letter offers them under the craft option too.
        engaged = policy._cast_candidates(
            crafter(), crafter().by_id(1), "r", magic=RULES
        )
        self.assertEqual(len(crafts(engaged)), 12)

    def test_the_tiers_stop_at_the_tier_cap(self):
        # 10 mana would pay for more than "knotwork" 1 allows: 2 x 1.
        state = crafter(skill_levels={KNOT_SKILL: 1})
        self.assertEqual(tiers_of(casts(state)), [1, 2])

    def test_the_tiers_stop_at_the_purse(self):
        # "knotwork" 2 allows 4; the purse covers 3.
        self.assertEqual(tiers_of(casts(crafter(mana=3))), [1, 2, 3])
        self.assertEqual(tiers_of(casts(crafter(mana=0))), [])

    def test_the_tiers_stop_where_the_effect_can_be_built_no_higher(self):
        # 40 dice a tier: a dice expression holds two tiers of it.
        gale = WorkingEffect(
            key="gale", label="Gale", kind=SpellEffectKind.DAMAGE, dice_per_tier="40d4"
        )
        rules = craft_rules(effects=(gale,))
        self.assertEqual(gale.highest_buildable_tier(), 2)
        self.assertEqual(tiers_of(casts(crafter(), rules), "gale"), [1, 2])

    def test_with_channel_lifted_the_tiers_pass_the_cap_and_stop_at_the_purse(self):
        state = crafter(
            skill_levels={KNOT_SKILL: 1}, mana=6, lifted_magic_bounds=[CHANNEL]
        )
        self.assertEqual(tiers_of(casts(state)), [1, 2, 3, 4, 5, 6])
        offered = {
            candidate.spell_key: candidate
            for candidate in crafts(plain(menu(state).candidates))
        }
        self.assertEqual(offered[working_key("scorch", 2)].lifted_bounds, ())
        # Past the cap and within Channel; then past both.
        past_cap = offered[working_key("scorch", 3)]
        self.assertEqual(past_cap.lifted_bounds, (CHANNEL,))
        self.assertTrue(
            past_cap.rationale.endswith("; 3 mana, Channel 4, past the tier cap 2")
        )
        past_both = offered[working_key("scorch", 5)]
        self.assertEqual(past_both.lifted_bounds, (CHANNEL,))
        self.assertTrue(
            past_both.rationale.endswith(
                "; 5 mana, past Channel 4, past the tier cap 2"
            )
        )
        # A pushed working says the same of its own tier: at the cap it is
        # within it, whatever is pushed, and one tier up it is past it.
        pushed = {
            (candidate.spell_key, candidate.push_mana): candidate.rationale
            for candidate in crafts(menu(state).candidates)
            if not candidate.borrow
        }
        self.assertTrue(
            pushed[(working_key("scorch", 2), 1)].startswith(
                "Push 1 extra mana (3 in all, Channel 4): "
            )
        )
        self.assertTrue(
            pushed[(working_key("scorch", 2), 3)].startswith(
                "Push 3 extra mana (5 in all, past Channel 4): "
            )
        )
        self.assertTrue(
            pushed[(working_key("scorch", 3), 1)].startswith(
                "Push 1 extra mana (4 in all, Channel 4, past the tier cap 2): "
            )
        )

    def test_past_the_purse_the_tiers_stop_at_the_borrowing_ceiling(self):
        # 1 mana and 2 of the debt cap's 4 already owed: 1 + 2, under the
        # tier cap of 4 and the Channel of 7.
        state = crafter(mana=1, debt=2)
        self.assertEqual(tiers_of(casts(state)), [1])
        self.assertEqual(tiers_of(casts(state, beyond_purse=True)), [1, 2, 3])
        # With Channel lifted the debt cap still bounds it: 2 + (4 - 1).
        lifted = crafter(
            skill_levels={KNOT_SKILL: 1},
            mana=2,
            debt=1,
            lifted_magic_bounds=[CHANNEL],
        )
        self.assertEqual(tiers_of(casts(lifted, beyond_purse=True)), [1, 2, 3, 4, 5])
        # And with no debt cap, the mana and the Channel together: 2 + 4.
        uncapped = replace(RULES, borrowing=example_borrowing_rules(max_debt=None))
        self.assertEqual(
            tiers_of(casts(lifted, uncapped, beyond_purse=True)), [1, 2, 3, 4, 5, 6]
        )

    def test_past_the_purse_needs_borrowing_rules_to_end_the_list(self):
        with self.assertRaisesRegex(ValueError, "no Borrowing rules"):
            casts(crafter(), replace(RULES, borrowing=None), beyond_purse=True)

    def test_an_effect_that_needs_a_grant_appears_only_at_the_keys_granted(self):
        self.assertEqual(tiers_of(casts(crafter()), "omen"), [])
        granted = crafter(granted_workings=[working_key("omen", 2)])
        self.assertEqual(tiers_of(casts(granted), "omen"), [2])
        # A grant lifts no cap: tier 5 is past "knotwork" 2's four.
        past_cap = crafter(
            granted_workings=[working_key("omen", 5), working_key("omen", 4)]
        )
        self.assertEqual(tiers_of(casts(past_cap), "omen"), [4])
        # An effect that needs none is not held to the grants.
        self.assertEqual(tiers_of(casts(granted), "hush"), [1, 2, 3, 4])

    def test_a_grant_of_a_working_the_rules_do_not_build_is_refused(self):
        state = crafter(granted_workings=["working:fog:1"])
        with self.assertRaisesRegex(ValueError, "build no working"):
            casts(state)
        with self.assertRaisesRegex(ValueError, "build no working"):
            menu(state)

    def test_a_true_mage_with_no_recipe_spell_is_offered_its_crafts(self):
        state = crafter(spells=[], mana=2)
        caster = state.by_id(1)
        self.assertTrue(policy._has_castings(caster, RULES))
        self.assertFalse(policy._has_castings(caster, NO_TRUE_MAGIC))
        self.assertFalse(policy._has_castings(caster, None))
        decision = menu(state)
        self.assertEqual(true_casts(decision.candidates), [])
        offered = crafts(decision.candidates)
        self.assertEqual(tiers_of(plain(offered)), [1, 2])
        # And their borrowing variants, up to the tier cap.
        borrowing = [candidate for candidate in offered if candidate.borrow]
        self.assertEqual(
            tiers_of([candidate for candidate in borrowing if not candidate.push_mana]),
            [3, 4],
        )
        self.assertIn(
            "CRAFT WORKING Scorch (tier 3) (BORROW 1 MANA)",
            [candidate.name for candidate in borrowing],
        )
        # With no True Magic it has nothing to cast, and no cast letter.
        self.assertEqual(crafts(menu(state, NO_TRUE_MAGIC).candidates), [])

    def test_a_figure_whose_only_spell_is_a_generic_counter_has_nothing_to_cast(self):
        learned = crafter(spells=["counter_2"], skill_levels={})
        caster = learned.by_id(1)
        self.assertFalse(policy._has_castings(caster, RULES))
        self.assertTrue(policy._is_mage(caster, RULES))
        self.assertTrue(
            policy._has_castings(crafter(spells=["counter_2"]).by_id(1), RULES)
        )
        self.assertTrue(
            policy._has_castings(
                crafter(spells=["counter_2", "heal"], skill_levels={}).by_id(1), None
            )
        )


class WhereACraftMayBeginTest(TestCase):
    """A True cast or a new craft is on the menu exactly where a cast is."""

    def offered(self, state: BattleState) -> tuple[int, int]:
        """How many True casts and crafts figure 1's whole menu holds."""
        candidates = menu(state, every_target=True).candidates
        return len(true_casts(candidates)), len(crafts(candidates))

    def test_engaged_or_not_both_are_offered(self):
        for state in (crafter(), crafter_beside()):
            true_cast_count, craft_count = self.offered(state)
            self.assertGreater(true_cast_count, 0)
            self.assertGreater(craft_count, 0)

    def test_neither_is_offered_prone_outside_hand_to_hand(self):
        for state in (crafter(prone=True), crafter_beside(prone=True)):
            decision = menu(state, every_target=True)
            self.assertEqual(self.offered(state), (0, 0))
            self.assertEqual(
                [candidate.name for candidate in decision.candidates], ["STAND UP"]
            )

    def test_in_hand_to_hand_only_empty_hands_are_offered_them(self):
        for prone in (False, True):
            with self.subTest(prone=prone):
                armed = hand_to_hand(crafter_beside(prone=prone))
                self.assertEqual(self.offered(armed), (0, 0))
                bare = hand_to_hand(crafter_beside(prone=prone, weapon=WeaponState()))
                true_cast_count, craft_count = self.offered(bare)
                self.assertGreater(true_cast_count, 0)
                self.assertGreater(craft_count, 0)

    def test_with_a_weapon_in_hand_only_a_no_gesture_spell_is_true_cast(self):
        # Shield known without gestures: its True cast is allowed, and no
        # working or unstudied sibling has a mastery to be.
        state = hand_to_hand(crafter_beside(spell_mastery={"shield": 2}))
        candidates = menu(state, every_target=True).candidates
        self.assertEqual(
            {candidate.spell_key for candidate in true_casts(candidates)}, {"shield"}
        )
        self.assertEqual(crafts(candidates), [])

    def test_neither_is_offered_in_a_grapple_but_a_mastered_true_cast(self):
        gripped = held(crafter_beside(weapon=WeaponState()))
        self.assertEqual(self.offered(gripped), (0, 0))
        holding = crafter_beside(weapon=WeaponState())
        holding.by_id(1).grappling = 2
        holding.by_id(2).grappled_by = 1
        self.assertEqual(self.offered(holding), (0, 0))
        # A copy of a cast made for the grapple's menu stays a True cast.
        mastered = held(
            crafter_beside(
                spell_mastery={"shield": 3}, spell_skill_levels={"shield": 1}
            )
        )
        candidates = plain(menu(mastered).candidates)
        shields = [
            candidate for candidate in candidates if candidate.spell_key == "shield"
        ]
        self.assertEqual([candidate.true_cast for candidate in shields], [False, True])
        self.assertEqual(
            {candidate.score for candidate in shields}, {policy.PLAYER_ONLY_SCORE}
        )
        self.assertTrue(shields[1].rationale.startswith("Spell Mastery 3: "))
        self.assertEqual(crafts(menu(mastered).candidates), [])


class CraftTargetsTest(TestCase):
    """Whom a craft action names, on the AI's menu and on a player's."""

    def targets(self, effect: str, **keywords) -> list[int | str | None]:
        state = three_sided()
        state.by_id(1).granted_workings = [working_key("omen", 1)]
        return [
            candidate.target_id
            for candidate in crafts(casts(state, **keywords))
            if candidate.spell_key == working_key(effect, 1)
        ]

    def test_a_damaging_working_is_aimed_as_a_damaging_cast_is(self):
        # The nearest enemy, and for a player each active enemy.
        self.assertEqual(self.targets("scorch"), [4])
        self.assertEqual(self.targets("scorch", every_target=True), [4, 2])

    def test_a_healing_working_is_offered_at_the_caster_and_each_teammate(self):
        self.assertEqual(self.targets("salve"), [1])
        self.assertEqual(self.targets("salve", every_target=True), [1, 3])

    def test_a_narrated_working_names_no_one_or_any_living_figure(self):
        for effect in ("omen", "hush"):
            with self.subTest(effect=effect):
                self.assertEqual(self.targets(effect), [None])
                self.assertEqual(
                    self.targets(effect, every_target=True), [None, 1, 2, 3, 4]
                )

    def test_a_damaging_working_with_no_enemy_left_is_not_offered(self):
        alone = crafter()
        alone.by_id(2).alive = False
        offered = crafts(casts(alone))
        self.assertEqual(tiers_of(offered, "scorch"), [])
        self.assertEqual(tiers_of(offered, "salve"), [1, 2, 3, 4])

    def test_how_each_kind_of_working_reads(self):
        state = three_sided()
        state.by_id(1).granted_workings = [working_key("omen", 1)]
        by_key = {
            (candidate.spell_key, candidate.target_id): candidate.rationale
            for candidate in crafts(casts(state, every_target=True))
        }
        roll = "3d6 ≤ WIS+Knotwork 16 — P(cast) 0.981"
        drain = "drains 3 body when it goes off, leaving 24"
        self.assertEqual(
            by_key[(working_key("salve", 1), 3)],
            f"Craft turn 1 of 2, gathering 0 of its 1 mana: {roll} — restores "
            f"1d8 fatigue to Fighter 3; {drain}; the AI crafts only a damaging "
            "working = 0.000",
        )
        self.assertEqual(
            by_key[(working_key("omen", 1), None)],
            f"Craft turn 1 of 2, gathering 0 of its 1 mana: {roll} — the Game "
            f"Master describes the effect, magnitude 3d4-1; {drain}; the AI "
            "crafts only a damaging working = 0.000",
        )
        self.assertEqual(
            by_key[(working_key("hush", 2), 4)],
            f"Craft turn 1 of 4, gathering 0 of its 2 mana: {roll} — the Game "
            "Master describes the effect, named for Fighter 4; drains 6 body "
            "when it goes off, leaving 21; the AI crafts only a damaging "
            "working = 0.000",
        )


class CraftScoreTest(TestCase):
    """How the AI weighs a craft action against its other options."""

    def scorch(self, state: BattleState, tier: int = 1, **keywords) -> policy.Candidate:
        (candidate,) = (
            candidate
            for candidate in crafts(casts(state, **keywords))
            if candidate.spell_key == working_key("scorch", tier)
        )
        return candidate

    def test_a_damaging_working_scores_its_forecast_over_its_turns(self):
        first = self.scorch(crafter())
        self.assertEqual(forecast_of(first).score, SCORCH_ONE_FORECAST)
        self.assertEqual(first.score, 1.272)
        self.assertEqual(
            first.rationale,
            "Craft turn 1 of 2, gathering 0 of its 1 mana: 3d6 ≤ WIS+Knotwork "
            "16 — P(cast) 0.981 x P(aim) 0.741 (3d6 ≤ DEX 12) — P(land) 0.727 "
            "x 3.500 expected damage on Fighter 2 (1d4+1 less 0 armour stops) "
            "= 2.544; drains 3 body when it goes off, leaving 24; 2.544 / 2 "
            "craft turn(s) left = 1.272",
        )
        third = self.scorch(crafter(), 3)
        self.assertEqual(forecast_of(third).damage_expression, "3d4+3")
        self.assertEqual(third.score, policy.round_score(forecast_of(third).score / 6))

    def test_the_roll_takes_the_level_bonus_and_the_injury_band_alone(self):
        # Hurt enough for the band, and owing an off-balance penalty the
        # craft action will have spent before the working's roll.
        hurt = crafter(fatigue=10, off_balance=True)
        injury, off_balance = policy.action_roll_penalties(hurt.by_id(1))
        self.assertGreater(injury, 0)
        self.assertGreater(off_balance, 0)
        rationale = self.scorch(hurt).rationale
        self.assertIn(
            f"3d6 ≤ WIS+Knotwork 16 less {injury} injury = {16 - injury} —", rationale
        )
        self.assertIn(f"(3d6 ≤ DEX 12 less {injury} injury = {12 - injury})", rationale)
        self.assertNotIn("off-balance", rationale)
        # A cast, made as the action itself, owes both.
        recipe = casts(crafter(fatigue=10, off_balance=True), None)[0]
        self.assertIn("off-balance", recipe.rationale)

    def test_the_fumble_floor_bounds_the_working_s_roll(self):
        rules = replace(RULES, casting_failure=example_casting_failure_rules())
        rationale = self.scorch(crafter(), rules=rules).rationale
        self.assertIn(
            "3d6 ≤ WIS+Knotwork 16, under the fumble floor 12 — P(cast) 0.625",
            rationale,
        )

    def test_a_working_whose_drain_would_fell_the_caster_scores_nothing(self):
        # At 20 Body a point, tier 1 drains 20: 21 leaves 1, and 20 none.
        # None of these is hurt enough to roll at a penalty.
        rules = craft_rules(drain_per_mana=20)
        survives = self.scorch(crafter(body=21), rules=rules)
        self.assertEqual(survives.score, 1.272)
        self.assertTrue(
            survives.rationale.endswith(
                "; drains 20 body when it goes off, leaving 1; 2.544 / 2 craft "
                "turn(s) left = 1.272"
            )
        )
        for body in (20, 19):
            with self.subTest(body=body):
                felled = self.scorch(crafter(body=body), rules=rules)
                self.assertEqual(felled.score, policy.PLAYER_ONLY_SCORE)
                self.assertEqual(felled.forecast, survives.forecast)
                self.assertTrue(
                    felled.rationale.endswith(
                        f"; drains 20 body when it goes off, leaving {body - 20}; "
                        "the AI crafts no working whose drain would fell it = 0.000"
                    )
                )
        # The pool the drain is taken from is the one that counts: 21
        # Fatigue would not survive a drain of 40, and is not asked to.
        self.assertEqual(self.scorch(crafter(fatigue=21), 2).score, 1.272)
        # A higher tier drains more: 40 from the 21 that tier 1 survives.
        self.assertEqual(self.scorch(crafter(body=21), 2, rules=rules).score, 0.0)

    def test_a_casting_that_drains_nothing_fells_no_one_and_names_no_drain(self):
        # A recipe cast, pushed or not, drains nothing whatever is left in
        # the pool the rules drain from.
        frail = crafter(body=1)
        drain = policy._drain_of(
            frail.by_id(1), RULES, get_spell("fire_missile"), 3, true_cast=False
        )
        self.assertEqual((drain.amount, drain.note), (0, ""))
        self.assertTrue(drain.survivable)
        # The same cast as a True cast drains for its pushed mana.
        true_cast = policy._drain_of(
            frail.by_id(1), RULES, get_spell("fire_missile"), 3, true_cast=True
        )
        self.assertEqual((true_cast.amount, true_cast.left), (9, 1 - 9))
        self.assertFalse(true_cast.survivable)

    def test_a_working_that_drains_nothing_names_no_drain(self):
        rules = craft_rules(drain_per_mana=0)
        candidate = self.scorch(crafter(), rules=rules)
        self.assertEqual(candidate.score, 1.272)
        self.assertTrue(
            candidate.rationale.endswith(
                "= 2.544; 2.544 / 2 craft turn(s) left = 1.272"
            )
        )

    def test_healing_and_narrated_workings_and_every_variant_score_nothing(self):
        state = crafter(mana=3, granted_workings=[working_key("omen", 1)])
        candidates = crafts(menu(state, every_target=True).candidates)
        scored = [candidate for candidate in candidates if candidate.score]
        self.assertEqual(tiers_of(scored), [1, 2, 3])
        for candidate in scored:
            self.assertEqual(
                (candidate.push_mana, candidate.borrow), (0, False), candidate.name
            )
            self.assertIn("scorch", candidate.spell_key)
        pushed = [candidate for candidate in candidates if candidate.push_mana]
        borrowing = [candidate for candidate in candidates if candidate.borrow]
        self.assertGreater(len(pushed), 0)
        self.assertGreater(len(borrowing), 0)
        for effect in ("salve", "omen", "hush"):
            self.assertGreater(len(tiers_of(candidates, effect)), 0)

    def test_a_craft_that_stands_scores_higher_as_it_nears_its_end(self):
        # A tier-2 Scorch takes four turns and forecasts 5.089.
        scores = []
        for turns_done in (0, 1, 2, 3):
            state = crafting("scorch", 2, turns_done) if turns_done else crafter()
            (candidate,) = (
                candidate
                for candidate in crafts(menu(state).candidates)
                if candidate.spell_key == working_key("scorch", 2)
                and not candidate.push_mana
                and not candidate.borrow
            )
            self.assertEqual(forecast_of(candidate).score, 5.089)
            scores.append(candidate.score)
        self.assertEqual(scores, [1.272, 1.696, 2.545, 5.089])

    def test_the_ai_begins_the_craft_that_scores_best_never_pushed_or_borrowing(
        self,
    ):
        # Bare-handed and out of reach: a move scores 0.5, each Scorch more.
        decision = menu(empty_handed())
        chosen = decision.chosen
        self.assertEqual(
            (chosen.letter, chosen.spell_key, chosen.push_mana, chosen.borrow),
            (actions.CRAFT_OPTION, working_key("scorch", 1), 0, False),
        )
        self.assertFalse(chosen.true_cast)

    def test_the_ai_may_craft_any_tier_up_to_its_cap(self):
        # An effect armour stops, at an enemy with 3 stops: the higher the
        # tier, the more gets past them a turn.
        maul = replace(SCORCH, key="maul", label="Maul", ignores_armour=False)
        rules = craft_rules(effects=(maul,))
        state = empty_handed()
        state.by_id(2).stops = 3
        self.assertEqual(menu(state, rules).chosen.spell_key, "working:maul:4")
        # One level down, its cap is 2.
        lower = empty_handed(skill_levels={KNOT_SKILL: 1})
        lower.by_id(2).stops = 3
        self.assertEqual(menu(lower, rules).chosen.spell_key, "working:maul:2")
        # And it crafts the highest tier whose drain it survives: tier 4's
        # is 12.
        frail = empty_handed(body=12)
        frail.by_id(2).stops = 3
        self.assertEqual(menu(frail, rules).chosen.spell_key, "working:maul:3")

    def test_an_ai_mage_crafts_a_working_through_to_its_end(self):
        state = empty_handed()
        roller = ScriptedRoller()
        events: list[dict] = []
        choose = partial(policy.choose_option, magic=RULES)
        for _turn in range(2):
            engine.run_turn(
                state, roller, events.append, choose, profile=TarmarProfile(magic=RULES)
            )
        self.assertEqual(
            [(action["craft"], action["turn"]) for action in craft_actions(events)],
            [(engine.CRAFT_BEGUN, 1), (engine.CRAFT_CONTINUED, 2)],
        )
        (completed,) = completions(events)
        self.assertEqual(completed["payload"]["spell"], working_key("scorch", 1))
        self.assertIsNone(state.by_id(1).crafting)


class ContinuationTest(TestCase):
    """A figure with a craft standing is offered its continuation."""

    def continuation(self, state: BattleState, **keywords) -> list[policy.Candidate]:
        return crafts(menu(state, **keywords).candidates)

    def test_it_carries_the_craft_and_no_new_craft_is_offered_beside_it(self):
        state = crafting("scorch", 2, 2)
        (carried,) = self.continuation(state)
        self.assertEqual(
            (
                carried.letter,
                carried.name,
                carried.spell_key,
                carried.push_mana,
                carried.borrow,
                carried.target_id,
            ),
            (
                actions.CRAFT_OPTION,
                "CRAFT WORKING Scorch (tier 2)",
                working_key("scorch", 2),
                0,
                False,
                2,
            ),
        )
        self.assertEqual(
            carried.rationale,
            "Craft turn 3 of 4, gathering 0 of its 2 mana: 3d6 ≤ WIS+Knotwork "
            "16 — P(cast) 0.981 x P(aim) 0.741 (3d6 ≤ DEX 12) — P(land) 0.727 "
            "x 7.000 expected damage on Fighter 2 (2d4+2 less 0 armour stops) "
            "= 5.089; drains 6 body when it goes off, leaving 21; 5.089 / 2 "
            "craft turn(s) left = 2.545",
        )
        # The casts are still on the menu: choosing one ends the craft.
        self.assertGreater(len(true_casts(menu(state).candidates)), 0)
        # The cast letter itself offers no craft while one stands.
        self.assertEqual(crafts(casts(state)), [])

    def test_a_pushed_or_borrowing_craft_is_carried_on_by_a_player_alone(self):
        # The AI begins no such craft, so it weighs none: the continuation
        # carries what its craft began with, names it, forecasts nothing and
        # scores nothing.
        begun = {
            "pushed": (
                crafting("scorch", 2, 2, push=2),
                "CRAFT WORKING Scorch (tier 2) (PUSH 2 MANA)",
                "Craft turn 3 of 4, gathering 1 of its 4 mana: 3d6 ≤ "
                "WIS+Knotwork 16 — P(cast) 0.981 — aimed at Fighter 2; drains "
                "12 body when it goes off, leaving 15; the AI does not push = "
                "0.000",
            ),
            "borrowing": (
                crafting("scorch", 2, 2, borrow=True),
                "CRAFT WORKING Scorch (tier 2) (BORROWING)",
                "Craft turn 3 of 4, gathering 0 of its 2 mana: 3d6 ≤ "
                "WIS+Knotwork 16 — P(cast) 0.981 — aimed at Fighter 2; drains "
                "6 body when it goes off, leaving 21; the AI does not borrow = "
                "0.000",
            ),
            "pushed and borrowing": (
                crafting("scorch", 2, 2, push=2, borrow=True),
                "CRAFT WORKING Scorch (tier 2) (PUSH 2 MANA, BORROWING)",
                "Craft turn 3 of 4, gathering 1 of its 4 mana: 3d6 ≤ "
                "WIS+Knotwork 16 — P(cast) 0.981 — aimed at Fighter 2; drains "
                "12 body when it goes off, leaving 15; the AI does not push or "
                "borrow = 0.000",
            ),
        }
        for how, (state, name, rationale) in begun.items():
            with self.subTest(begun=how):
                craft = standing_craft(state.by_id(1))
                (carried,) = self.continuation(state)
                self.assertEqual(
                    (carried.push_mana, carried.borrow, carried.target_id),
                    (craft.push_mana, craft.borrow, 2),
                )
                self.assertEqual(carried.name, name)
                self.assertEqual(carried.score, policy.PLAYER_ONLY_SCORE)
                self.assertIsNone(carried.forecast)
                self.assertEqual(carried.rationale, rationale)
        # A healing craft begun pushed is named for its push too.
        (salve,) = self.continuation(crafting("salve", 1, 1, push=1))
        self.assertEqual(salve.name, "CRAFT WORKING Salve (tier 1) (PUSH 1 MANA)")
        self.assertTrue(salve.rationale.endswith("; the AI does not push = 0.000"))

    def test_with_no_enemy_active_a_damaging_craft_that_stands_is_still_offered(self):
        def downed() -> BattleState:
            state = crafting("scorch", 2, 2)
            state.by_id(2).conscious = False
            return state

        state = downed()
        (carried,) = self.continuation(state, every_target=True)
        self.assertEqual(
            (carried.name, carried.target_id, carried.score, carried.forecast),
            ("CRAFT WORKING Scorch (tier 2)", None, policy.PLAYER_ONLY_SCORE, None),
        )
        self.assertEqual(
            carried.rationale,
            "Craft turn 3 of 4, gathering 0 of its 2 mana: 3d6 ≤ WIS+Knotwork "
            "16 — P(cast) 0.981 — no enemy is left to aim it at; drains 6 body "
            "when it goes off, leaving 21; nothing for it to land on = 0.000",
        )
        # Chosen, it carries the craft on.
        events = run_turn(state, carried)
        self.assertEqual(losses(events), [])
        self.assertEqual(standing_craft(state.by_id(1)).turns_done, 3)
        # Passed over with the continuation on offer, the craft is abandoned.
        state = downed()
        (lost,) = losses(run_turn(state, DEFEND))
        self.assertEqual(lost["ending"], "abandoned")
        # A new craft of a damaging working is not begun with no one to aim
        # at, as a damaging cast is not.
        idle = crafter()
        idle.by_id(2).conscious = False
        self.assertEqual(tiers_of(crafts(menu(idle).candidates)), [])

    def test_it_is_offered_with_no_mana_in_the_pool(self):
        # The cast letter needs mana; the continuation is no cast letter's.
        state = crafting("scorch", 2, 2)
        state.by_id(1).mana = 0
        offered = menu(state).candidates
        self.assertEqual(len(crafts(offered)), 1)
        self.assertEqual(
            [candidate for candidate in plain(offered) if candidate.letter == "h"], []
        )

    def test_it_gains_no_pushed_or_borrowing_variant(self):
        # With the purse below the working's level, where a cast would be
        # offered borrowing; and with push room to spare.
        short = steady_craft(2)
        short.by_id(1).mana = 1
        roomy = crafting("scorch", 1, 1)
        for name, state in (("short purse", short), ("push room", roomy)):
            with self.subTest(state=name):
                craft = standing_craft(state.by_id(1))
                offered = self.continuation(state, every_target=True)
                self.assertEqual(len(offered), 1)
                self.assertEqual(
                    (offered[0].push_mana, offered[0].borrow),
                    (craft.push_mana, craft.borrow),
                )
        # A cast in the same purse does gain them.
        self.assertTrue(
            any(candidate.push_mana for candidate in menu(roomy).candidates)
        )
        self.assertTrue(any(candidate.borrow for candidate in menu(short).candidates))

    def test_it_is_offered_at_each_figure_the_working_may_name(self):
        state = three_sided()
        steady_craft(2, state=state)
        self.assertEqual(
            [candidate.target_id for candidate in self.continuation(state)], [4]
        )
        self.assertEqual(
            [
                candidate.target_id
                for candidate in self.continuation(state, every_target=True)
            ],
            [4, 2],
        )

    def test_a_prone_figure_is_offered_it_beside_stand_up(self):
        state = crafting("scorch", 2, 2)
        state.by_id(1).prone = True
        self.assertEqual(
            [candidate.name for candidate in menu(state).candidates],
            ["STAND UP", "CRAFT WORKING Scorch (tier 2)"],
        )

    def test_in_hand_to_hand_it_is_withheld_from_a_weapon_in_hand(self):
        for prone in (False, True):
            with self.subTest(prone=prone):
                armed = hand_to_hand(steady_craft(2, state=crafter_beside(prone=prone)))
                self.assertEqual(self.continuation(armed), [])
                bare = hand_to_hand(
                    steady_craft(
                        2, state=crafter_beside(prone=prone, weapon=WeaponState())
                    )
                )
                self.assertEqual(len(self.continuation(bare)), 1)

    def test_it_does_not_answer_to_channel_again(self):
        # The craft began at "knotwork" 2, a tier-2 Scorch pushed by 3. A
        # level since lost, its tier is still within the cap of 2 and its 5
        # mana are past the Channel of 4: it is offered all the same, and
        # the bounds it carries count its pushed mana.
        state = crafting("scorch", 2, 2, push=3)
        state.by_id(1).skill_levels = {KNOT_SKILL: 1}
        decision = menu(state)
        (carried,) = crafts(decision.candidates)
        self.assertEqual(carried.lifted_bounds, (CHANNEL,))
        self.assertFalse(any("Scorch" in line for line in decision.withheld))
        # With no level at all it is offered still.
        state.by_id(1).skill_levels = {}
        self.assertEqual(len(crafts(menu(state).candidates)), 1)
        state.by_id(1).skill_levels = {KNOT_SKILL: 1}
        # A craft action it reports the same bounds on.
        state.by_id(1).chosen_letter = actions.CRAFT_OPTION
        turn_runner, events = runner_on(state, (), TarmarProfile(magic=RULES))
        turn_runner.craft_working(state.by_id(1))
        (action,) = craft_actions(events)
        self.assertEqual(action["lifted_bounds"], list(carried.lifted_bounds))

    def test_a_craft_standing_with_no_true_magic_rules_to_name_it_is_refused(self):
        state = steady_craft(2)
        caster = state.by_id(1)
        for rules in (None, MagicRules(), NO_TRUE_MAGIC):
            with self.subTest(rules=rules):
                with self.assertRaisesRegex(ValueError, "no True Magic rules"):
                    policy.choose_option(state, caster, rules)

    def test_the_menus_continuation_is_the_one_the_engine_carries_on(self):
        state = steady_craft(2)

        def choose(state: BattleState, actor: CombatantState) -> policy.Decision:
            decision = policy.choose_option(state, actor, RULES)
            if actor.combatant_id == 1:
                (decision.chosen,) = crafts(decision.candidates)
            return decision

        events: list[dict] = []
        engine.run_turn(
            state,
            ScriptedRoller(),
            events.append,
            choose,
            profile=TarmarProfile(magic=RULES),
        )
        (action,) = craft_actions(events)
        self.assertEqual((action["craft"], action["turn"]), ("continued", 3))


class VariantsTest(TestCase):
    """A pushed or borrowing variant is the candidate it varies, with only
    what the variant changes changed."""

    def of(self, candidates, spell_key: str, *, true_cast: bool) -> list:
        return [
            candidate
            for candidate in candidates
            if candidate.spell_key == spell_key and candidate.true_cast == true_cast
        ]

    def test_a_pushed_true_cast_stays_one_and_names_its_drain(self):
        candidates = menu(crafter(spell_skill_levels={"fire_missile": 3})).candidates
        base, first, *_rest = self.of(candidates, "fire_missile", true_cast=True)
        self.assertEqual((base.push_mana, first.push_mana), (0, 1))
        self.assertEqual(
            (first.letter, first.name, first.target_id, first.score),
            ("h", "CAST SPELL Fire Missile (TRUE CAST) (PUSH 1 MANA)", 2, 0.0),
        )
        self.assertIsNone(first.forecast)
        self.assertEqual(
            first.rationale,
            "Push 1 extra mana (2 in all, Channel 7): Control Roll 2d6 ≤ WIS "
            "12 less 2 x 1 invested, 0 injury and 0 off-balance = 10; drains "
            "3 body when it goes off, leaving 24; the AI does not push = 0.000",
        )
        # The recipe cast of the same spell drains nothing, and says so by
        # saying nothing.
        _plain, recipe_pushed, *_rest = self.of(
            candidates, "fire_missile", true_cast=False
        )
        self.assertEqual(
            recipe_pushed.rationale,
            "Push 1 extra mana (2 in all, Channel 5): Control Roll 2d6 ≤ WIS "
            "12 less 2 x 1 invested, 0 injury and 0 off-balance = 10; the AI "
            "does not push = 0.000",
        )

    def test_only_a_pushed_or_borrowing_casting_is_left_to_a_player_for_it(self):
        self.assertEqual(
            policy._left_to_a_player(push=True, borrow=True),
            "the AI does not push or borrow = 0.000",
        )
        with self.assertRaisesRegex(ValueError, "neither pushes nor borrows"):
            policy._left_to_a_player(push=False, borrow=False)

    def test_a_pushed_craft_stays_a_craft_and_names_its_whole_drain(self):
        candidates = menu(crafter()).candidates
        pushed = [
            candidate
            for candidate in candidates
            if candidate.spell_key == working_key("scorch", 2) and candidate.push_mana
        ]
        self.assertEqual([candidate.push_mana for candidate in pushed], [1, 2, 3])
        first = pushed[0]
        self.assertEqual(
            (first.letter, first.name, first.target_id, first.borrow),
            (
                actions.CRAFT_OPTION,
                "CRAFT WORKING Scorch (tier 2) (PUSH 1 MANA)",
                2,
                False,
            ),
        )
        # Tier 2 and 1 pushed: 3 mana, at 3 Body a point.
        self.assertIn("; drains 9 body when it goes off, leaving 18;", first.rationale)
        # A narrated working rolls nothing to push.
        self.assertFalse(
            any(
                candidate.push_mana
                for candidate in candidates
                if "hush" in candidate.spell_key
            )
        )

    def test_the_push_room_is_by_the_channel_the_casting_names(self):
        # Fire Missile at level 3: its own Channel is 2 x 3 - 1, and the
        # True cast's is the "knot" kind's 7. No push cap, 10 mana.
        rules = replace(RULES, push=replace(example_push_rules(), max_push_mana=None))
        candidates = menu(
            crafter(spell_skill_levels={"fire_missile": 3}), rules
        ).candidates
        recipe = self.of(candidates, "fire_missile", true_cast=False)
        true = self.of(candidates, "fire_missile", true_cast=True)
        self.assertEqual([candidate.push_mana for candidate in recipe], [0, 1, 2, 3, 4])
        self.assertEqual(
            [candidate.push_mana for candidate in true], [0, 1, 2, 3, 4, 5, 6]
        )
        self.assertIn("(5 in all, Channel 5)", recipe[-1].rationale)
        self.assertIn(f"(7 in all, Channel {KNOT_CHANNEL})", true[-1].rationale)

    def test_a_borrowing_true_cast_stays_one_and_keeps_its_forecast(self):
        state = crafter(mana=0, spell_skill_levels={"fire_missile": 3})
        candidates = menu(state).candidates
        borrowed, pushed, *_rest = self.of(candidates, "water_missile", true_cast=True)
        self.assertEqual(
            (borrowed.name, borrowed.borrow, borrowed.push_mana, borrowed.score),
            ("CAST SPELL Water Missile (TRUE CAST) (BORROW 1 MANA)", True, 0, 0.0),
        )
        self.assertIsNotNone(borrowed.forecast)
        self.assertEqual(
            (pushed.name, pushed.borrow, pushed.push_mana, pushed.score),
            (
                "CAST SPELL Water Missile (TRUE CAST) (PUSH 1 MANA, BORROW 2 MANA)",
                True,
                1,
                0.0,
            ),
        )
        self.assertIsNone(pushed.forecast)
        self.assertIn("; drains 3 body when it goes off, leaving 24;", pushed.rationale)

    def test_the_borrowing_ceiling_is_by_the_channel_the_casting_names(self):
        # No debt cap, so Channel alone bounds a borrowing casting: the
        # Missile's own 5, and 7 as a True cast. 2 mana, no push cap.
        rules = replace(
            RULES,
            push=replace(example_push_rules(), max_push_mana=None),
            borrowing=example_borrowing_rules(max_debt=None),
        )
        state = crafter(mana=2, spell_skill_levels={"fire_missile": 3})
        candidates = [
            candidate for candidate in menu(state, rules).candidates if candidate.borrow
        ]
        recipe = self.of(candidates, "fire_missile", true_cast=False)
        true = self.of(candidates, "fire_missile", true_cast=True)
        self.assertEqual([candidate.push_mana for candidate in recipe], [2, 3, 4])
        self.assertEqual([candidate.push_mana for candidate in true], [2, 3, 4, 5, 6])

    def test_a_borrowing_craft_stays_a_craft(self):
        candidates = menu(crafter(mana=1)).candidates
        borrowed = [
            candidate
            for candidate in candidates
            if candidate.spell_key == working_key("salve", 3) and candidate.borrow
        ]
        # Tier 3 from 1 mana, and pushed up to the debt cap: 1 + 4.
        self.assertEqual(
            [
                (candidate.letter, candidate.push_mana, candidate.target_id)
                for candidate in borrowed
            ],
            [
                (actions.CRAFT_OPTION, 0, 1),
                (actions.CRAFT_OPTION, 1, 1),
                (actions.CRAFT_OPTION, 2, 1),
            ],
        )
        self.assertEqual(
            borrowed[0].name, "CRAFT WORKING Salve (tier 3) (BORROW 2 MANA)"
        )


class TrueMageCountersAndAnchorsTest(TestCase):
    """A level in the True skill makes a mage, and something to cast."""

    COUNTER_RULES = replace(RULES, counterspell=example_counterspell_rules())
    FAMILIARITY = replace(RULES, counterspell=FAMILIARITY_RULES)
    RUNAWAY_RULES = replace(RULES, runaway=example_runaway_rules())

    def versus(self, **defender_overrides) -> BattleState:
        """The crafter, spells unlearned, against a mage (Shield, 6 mana)."""
        state = crafter(spells=[])
        defender = state.by_id(2)
        defender.spells = ["shield"]
        defender.mana = 6
        for name, value in defender_overrides.items():
            setattr(defender, name, value)
        return state

    def countering(self, state: BattleState, actor_id: int, rules=None) -> list:
        """The counters on a figure's menu, under the counter rules."""
        return counters(decide(state, actor_id, rules or self.COUNTER_RULES))

    def test_a_true_mage_with_no_spell_may_counter_and_be_countered(self):
        state = self.versus()
        against = self.countering(state, 2)
        self.assertEqual({candidate.target_id for candidate in against}, {1})
        by_it = self.countering(state, 1)
        self.assertEqual({candidate.target_id for candidate in by_it}, {2})
        # With no level in the True skill it is no mage and casts nothing.
        state.by_id(1).skill_levels = {}
        self.assertEqual(self.countering(state, 2), [])
        self.assertEqual(self.countering(state, 1), [])

    def test_a_true_level_makes_a_caster_to_counter_of_a_figure_with_only_counters(
        self,
    ):
        # Its only spells are Counter rungs, which are never cast, but at a
        # level in the True skill it may craft: a counter may name it.
        state = self.versus()
        state.by_id(1).spells = ["counter_2", "counter_3"]
        named = self.countering(state, 2)
        self.assertEqual({candidate.target_id for candidate in named}, {1})
        # With no level in the True skill it has nothing to cast, and none
        # does; it may still counter.
        state.by_id(1).skill_levels = {}
        self.assertEqual(self.countering(state, 2), [])
        self.assertEqual(
            {candidate.target_id for candidate in self.countering(state, 1)}, {2}
        )

    def test_a_blind_counter_forecasts_the_true_level_it_is_sure_of(self):
        # A defender at "knotwork" 2 adds 3 a level whatever is cast; its
        # level in a spell and its Counter rung count only for what they
        # reach, so they are no part of a forecast made blind.
        state = self.versus(
            skill_levels={KNOT_SKILL: 2},
            spell_skill_levels={"fire_missile": 5},
            spells=["shield", "counter_3"],
        )
        offered = self.countering(state, 2, self.FAMILIARITY)
        self.assertEqual(
            offered[0].rationale,
            "Oppose whatever Fighter 1 casts this turn (not yet known): commit "
            "1 mana (5 left) for +0 on the opposed roll 2d8 + WIS 10 less 0 "
            "injury, and at least +6 for familiarity (true level 2), whatever "
            "is cast; the AI does not counter = 0.000",
        )
        # No level in the True skill: nothing it is sure of, and the line
        # is the one it has with no familiarity injected.
        unsure = self.versus(
            spell_skill_levels={"fire_missile": 5}, spells=["shield", "counter_3"]
        )
        self.assertEqual(
            self.countering(unsure, 2, self.FAMILIARITY)[0].rationale,
            self.countering(unsure, 2)[0].rationale,
        )

    def test_against_a_known_cast_the_forecast_counts_the_defenders_true_level(self):
        # The caster has chosen Fire Missile, which the defender never
        # studied and no Counter rung of its reaches: its familiarity is its
        # "knotwork" 3, at 3 a level.
        state = self.versus(skill_levels={KNOT_SKILL: 3})
        caster = state.by_id(1)
        caster.spells = ["fire_missile"]
        caster.chosen_letter = "h"
        caster.chosen_spell = "fire_missile"
        offered = self.countering(state, 2, self.FAMILIARITY)
        self.assertEqual(
            offered[0].rationale,
            "Oppose Fighter 1's Fire Missile: commit 1 mana (5 left) for +0 on "
            "the opposed roll 2d8 + WIS 10 less 0 injury, and +9 for "
            "familiarity (true level 3); the AI does not counter = 0.000",
        )

    def test_a_working_s_runaway_is_named_on_the_anchor_options(self):
        state = self.versus()
        caster = state.by_id(1)
        caster.runaway = RunawayState(
            spell_key=working_key("scorch", 2), original_cost=2, turns_running=1
        )
        own = anchors(menu(state, self.RUNAWAY_RULES).candidates)
        self.assertEqual(
            [candidate.name for candidate in own], ["Anchor Scorch (tier 2)"]
        )
        helping = anchors(decide(state, 2, self.RUNAWAY_RULES).candidates)
        self.assertEqual(
            [candidate.name for candidate in helping],
            ["Anchor Fighter 1's Scorch (tier 2)"],
        )

    def test_a_true_mage_with_no_spell_may_anchor_anothers_runaway(self):
        state = self.versus()
        state.by_id(2).runaway = RunawayState(
            spell_key="shield", original_cost=1, turns_running=1
        )
        helping = anchors(menu(state, self.RUNAWAY_RULES).candidates)
        self.assertEqual(
            [candidate.name for candidate in helping], ["Anchor Fighter 2's Shield"]
        )
        state.by_id(1).skill_levels = {}
        self.assertEqual(anchors(menu(state, self.RUNAWAY_RULES).candidates), [])
