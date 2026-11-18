"""Numbers and readings the engine uses that no rules page states.

tarmar-studio #826 asked, for each figure the battle engine uses with no
page behind it, for "Spencer's ruling or a line on the rules page, and then
a drift test". This module is the engine's record of those: each entry says
what the engine does, where, and on what authority — a page the pass found,
or a ruling made under the standing rule (the page as written governs;
where it is silent, the reading closest to it) and **marked for Spencer**,
who may overrule any of them. ``tests/test_house_rulings.py`` pins every
value here to the code that uses it, so a change to either side fails.

The 2026-09-29 pass's own rulings (#776, #779, #813, #814, #816, #821,
#824) are recorded here too, so every reading the engine makes on the
pages' silences sits in one place.

The Tarmar-studio adapter's numbers (a beast's 4:7:12 gaits, the one-hex
floor on a gait, the 50-turn stalemate) live in that repository and are not
recorded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Authority for an entry.
PAGE = "page"
RULING = "ruling — marked for Spencer"
TACTIC = "AI tactic, not a rule — marked for Spencer"

#: What each reading on the generic counters rests on (tarmar-engine #77).
_WHILE_COUNTERS_ARE_FIVE_SPELLS = (
    " It stands only while the consuming game holds the Counter rungs as five spells."
)


@dataclass(frozen=True)
class HouseRuling:
    """One number or reading, with where it lives and why."""

    key: str
    reading: str
    value: Any
    where: str
    authority: str
    issue: str


HOUSE_RULINGS: tuple[HouseRuling, ...] = (
    HouseRuling(
        "spell_magnitudes",
        "Spell effects: every element's Missile 1d6, Ball 2d6 to one target "
        "(the description's area is not modelled) and Bolt 3d6, Heal 1d6 "
        "Fatigue to the caster only, Fatigue 1d6+1, Wound 1d6 to Body, "
        "Shield +1 TN, Blur -2 to hit. schools-of-magic.md names the spells "
        "and levels; no page gives these numbers.",
        {
            "missile": "1d6",
            "ball": "2d6",
            "bolt": "3d6",
            "heal": "1d6",
            "fatigue": "1d6+1",
            "wound": "1d6",
            "shield_tn_bonus": 1,
            "blur_attacker_penalty": 2,
        },
        "tarmar_engine/spells.py SPELLS",
        RULING,
        "#826",
    ),
    HouseRuling(
        "spell_damage_armour",
        "Damage from a spell that does not ignore armour is reduced by the "
        "target's armour stops, as a weapon's is.",
        True,
        "tarmar_engine/engine.py _resolve_cast (_roll_and_resolve)",
        RULING,
        "#826",
    ),
    HouseRuling(
        "megahex_step",
        "One megahex of missile range spans 3 hexes centre to centre "
        "(movement.md defines the 7-hex megahex, not the step).",
        3,
        "tarmar_engine/hexes.py HEXES_PER_MEGAHEX_STEP",
        RULING,
        "#826",
    ),
    HouseRuling(
        "walk_slow_step",
        "The phase-4 step of a disengaged missile or cast option (f, h) is at "
        "most 2 hexes; the engaged cast (r) is 'Shift/still' and takes none (#34).",
        2,
        "tarmar_engine/engine.py WALK_SLOW_MAX",
        PAGE + ": movement.md, 'Walk (slow) | Up to 2 hex'",
        "#826",
    ),
    HouseRuling(
        "preferred_standoff",
        "An archer or caster uses its phase-4 step to open the range to 3 "
        "hexes from the nearest enemy.",
        3,
        "tarmar_engine/engine.py PREFERRED_STANDOFF",
        TACTIC,
        "#826",
    ),
    HouseRuling(
        "plate_cracking_rounding",
        "Plate-cracking's 'ignores **half** the armour's stops' rounds the "
        "stops that still apply down (stops // 2), in the attacker's favour.",
        "stops // 2",
        "tarmar_rules damage_after_armour; tarmar_engine/resolution.py",
        RULING,
        "#826",
    ),
    HouseRuling(
        "forced_retreat_physical_hits",
        "Forced retreat needs physical hits dealt and none taken: a weapon or "
        "bare-handed blow that gets damage past the armour. A blow the armour "
        "stops entirely is not one, and spells never are (derived-pools.md "
        "'normal hits reduce Fatigue'; turn-sequence.md 'dealt damage').",
        ("dealt_physical_hit_this_turn", "took_physical_hit_this_turn"),
        "tarmar_engine/retreat.py TarmarForcedRetreat.pusher_eligible",
        RULING,
        "#813",
    ),
    HouseRuling(
        "forced_retreat_choice",
        "The pusher chooses the hex ('any direction') and whether to advance; "
        "the AI pushes straight back when that hex is clear and advances "
        "unless it holds a missile weapon.",
        "policy.choose_retreat",
        "tarmar_engine/policy.py choose_retreat",
        PAGE + " (the choice); " + TACTIC + " (the AI's pick)",
        "#779",
    ),
    HouseRuling(
        "unconscious_caster_spells_end",
        "An unconscious caster renews nothing, so its continuing spells end "
        "(read from casting-spells.md's 'Unrenewed spells end immediately.'; "
        "no page speaks of an unconscious caster).",
        True,
        "tarmar_engine/engine.py phase_renew_spells",
        RULING,
        "#826",
    ),
    HouseRuling(
        "squeeze_defences",
        "Squeeze strips the held target's shield, Defend stance, DEX dodge "
        "modifier and Shield-spell TN bonus: the page's 'no Dodge/Defend "
        "bonus' is read as the DEX dodge, since option c DODGE covers "
        "missiles only. A third party's blow on a held figure keeps the DEX "
        "dodge and the spell.",
        ("shield", "defend stance", "dex dodge", "spell tn bonus"),
        "tarmar_engine/engine.py grapple_squeeze; combat_math.attack_numbers",
        RULING,
        "#826",
    ),
    HouseRuling(
        "one_last_shot_per_engagement",
        "ONE LAST SHOT is one shot per engagement: taken, it returns only "
        "after the figure starts a turn disengaged.",
        1,
        "tarmar_engine/engine.py missile_attack, _start_of_turn_missile_state",
        RULING,
        "#776",
    ),
    HouseRuling(
        "critical_modifier_once",
        "A critical rolls the damage dice twice and adds the modifier once; "
        "a confirmed severe critical's 'triple damage' rolls the dice three "
        "times, modifier once.",
        {"critical_dice_rolls": 2, "severe_dice_rolls": 3, "modifier_counts": 1},
        "tarmar_engine/engine.py resolve_attack; combat_math.expected_damage",
        RULING,
        "#824",
    ),
    HouseRuling(
        "stressed_weapon_second_fumble",
        "A stressed weapon breaks on any second fumble; an off-balance result "
        "on that fumble applies as well.",
        True,
        "tarmar_engine/engine.py apply_fumble",
        RULING,
        "#814",
    ),
    HouseRuling(
        "mana_on_failure",
        "A spell's mana is spent on success and on the failing natural totals "
        "the injected casting failure rules name "
        "(CastingFailureRules.mana_lost_totals); every other failure keeps it. "
        "The Runaway total is among them, because a Runaway's first effect, "
        "the drain, is paid at once out of the casting's mana.",
        True,
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, _complete_working)",
        RULING,
        "#816",
    ),
    HouseRuling(
        "grappled_casting_mastery",
        "In a grapple, holder and held alike, a spell is cast only at Spell "
        "Mastery 3 (no gestures and no words) and renewed at 2 or better; "
        "either side is offered its Mastery 3 casts.",
        {"cast": 3, "renew": 2},
        "tarmar_engine/engine.py _cast_blocked (cast_spell, craft_working, "
        "_begin_craft), phase_renew_spells",
        RULING + " (cast: the grappling page's 'no hand gestures and no "
        "verbal component'; the HTH table's 'no-gesture spell' would allow 2)",
        "#821",
    ),
    HouseRuling(
        "engagers_are_armed_standing",
        "Only an armed, standing enemy engages: a held weapon or a beast's "
        "natural ones, any weapon including a bow.",
        True,
        "tarmar_engine/combat_math.py engages",
        PAGE + ": movement.md 'armed enemy'; " + RULING + " (beasts are armed)",
        "#820",
    ),
    HouseRuling(
        "pick_up_when_disengaged",
        "PICK UP WEAPON is offered disengaged as well as engaged.",
        True,
        "tarmar_engine/actions.py legal_actions",
        PAGE + ": movement.md Stand Still row lists 'Pick Up Weapon'",
        "#780",
    ),
    HouseRuling(
        "thrown_weapon_lands",
        "A thrown weapon lands in its target's hex, hit or miss.",
        "target hex",
        "tarmar_engine/engine.py missile_attack",
        RULING,
        "#812",
    ),
    HouseRuling(
        "reload_alongside_other_options",
        "A crossbow reloads over the turns its note names while the figure "
        "takes other options.",
        True,
        "tarmar_engine/engine.py _start_of_turn_missile_state",
        RULING,
        "#781",
    ),
    HouseRuling(
        "slower_enemy_strikes_disengager",
        "A slower enemy that was adjacent when a figure disengaged, and that "
        "chose to attack it, strikes it a hex away at the adjDEX gap.",
        "target.dexterity - attacker.dexterity",
        "tarmar_engine/engine.py _strike_at_disengager",
        PAGE + ": special-combat-situations.md; " + RULING + " (who qualifies)",
        "#777",
    ),
)


HOUSE_RULINGS += (
    HouseRuling(
        "dodge_closes_on_missile_threat",
        "DODGE's 'Jog or less' is spent closing on the nearest enemy holding "
        "a missile weapon; with none, the dodger stands.",
        "nearest missile threat",
        "tarmar_engine/policy.py choose_option (letter c)",
        TACTIC,
        "#819",
    ),
    HouseRuling(
        "arena_edge_is_a_wall",
        "The open arena's edge is the only wall: a target whose rear hex lies "
        "outside the arena has its back to the wall for HTH entry.",
        "rear hex outside the arena",
        "tarmar_engine/combat_math.py hth_entry_reason",
        RULING,
        "#823",
    ),
    HouseRuling(
        "hth_by_agreement",
        "'or they simply agree' is read off the board: an enemy that has "
        "itself chosen ATTEMPT HTH or an HTH strike at the actor this turn, "
        "or two bare-handed figures, enter hand-to-hand; bare hands against "
        "an armed figure keep the entry conditions. Once in, the pair need no "
        "condition until they part, and both strike (t) at the HTH +4. "
        "Agreement beyond these two cases is a Game Master's call, made in the "
        "consuming game (Spencer's ruling of 2026-10-09 on tarmar-studio "
        "#867) and read as hth_agreement_by_game_master.",
        ("they close in too", "both bare-handed"),
        "tarmar_engine/combat_math.py hth_entry_reason; engine.py _enter_hth",
        RULING,
        "#867",
    ),
    HouseRuling(
        "hth_agreement_by_game_master",
        "A pair a Game Master has ruled agree may enter hand-to-hand: when "
        "either figure lists the other in CombatantState.hth_agreed_with, "
        "that is an entry condition for both, checked after the two "
        "agreements read off the board. Empty, nothing changes. Spencer, "
        "2026-10-09: 'a Game Master marks a battle, or a "
        'pair, as "HTH by agreement"; players alone still need the '
        "engine's entry condition.'",
        "a Game Master rules they agree",
        "tarmar_engine/combat_math.py hth_entry_reason (GAME_MASTER_AGREEMENT)",
        PAGE + ": hand-to-hand-and-grappling.md 'or they simply agree'; "
        "Spencer's ruling of 2026-10-09 on tarmar-studio #867",
        "#867",
    ),
    HouseRuling(
        "hth_table_is_the_menu",
        "A figure in hand-to-hand is offered the Hand-to-Hand Combat table and "
        "nothing else, standing or down: t (bare hands, or the dagger in "
        "hand), u (a carried dagger not in hand), v DISENGAGE, and the casts "
        "the table allows. It has no j, against a partner or against a third "
        "enemy beside it it is not in hand-to-hand with. The menu is fixed "
        "when the option is chosen: a figure drawn into hand-to-hand later in "
        "the turn takes the action it chose, except that a cast is checked "
        "again when it is cast, as a grappled cast is. A beast, with no hands, "
        "keeps its own menu.",
        ("t", "u", "v", "r"),
        "tarmar_engine/actions.py legal_actions (in_hth); policy.py _score_options",
        PAGE + ": action-options.md, Hand-to-Hand Combat (Spencer's ruling of "
        "2026-10-09 on tarmar-studio #867); "
        + RULING
        + " (the third enemy, a figure down, the menu fixed at the choice: "
        "coordinator's ruling under the standing rule)",
        "#19",
    ),
    HouseRuling(
        "hth_casting_hands_free",
        "The table's 'If hands free or no-gesture spell': hands are free with "
        "no weapon in hand (a shield is a standing fact of the snapshot, not a "
        "held item); a no-gesture spell is one known at Spell Mastery 2.",
        2,
        "tarmar_engine/policy.py _hth_casts; engine.py _cast_blocked "
        "(cast_spell, craft_working, _begin_craft)",
        RULING,
        "#19",
    ),
    HouseRuling(
        "hth_disengage_leaves_every_partner",
        "A successful v DISENGAGE, or a Struggle Free ('the same roll as a "
        "plain HTH Disengage'), leaves hand-to-hand with every partner, even "
        "with no clear hex to step to; a slower partner gets no parting strike "
        "(the page gives v none, unlike n).",
        True,
        "tarmar_engine/engine.py _leave_hand_to_hand (hth_disengage, "
        "grapple_struggle_free)",
        RULING,
        "#19",
    ),
    HouseRuling(
        "ai_destinations",
        "The AI names no hex. Its engaged options stand still, its DROP drops "
        "in place, its MOVE, CHARGE and DODGE close on their target, and its "
        "DISENGAGE (n, v) and Struggle Free leave the step to the engine when "
        "the figure acts (straight back, then either flank); the forecast "
        "prints the step it expects. A refused hex leaves the option's move as "
        "it is with none.",
        "stand still",
        "tarmar_engine/policy.py _score_options; movement.py step_away_hex",
        TACTIC,
        "#20",
    ),
    HouseRuling(
        "shift_keeps_engagement",
        "A Shift ('Shift 1 hex or stand still during movement') leaves the "
        "figure engaged with every enemy it was engaged with; a hex that would "
        "take it out of one is refused, since leaving an engagement is "
        "DISENGAGE's (n), with its strike from a slower enemy.",
        True,
        "tarmar_engine/movement.py keeps_engagement",
        RULING,
        "#20",
    ),
)


# The magic mechanics' readings (tarmar-studio #825, split into tarmar-engine
# #26-#31). Structural readings only: every number those mechanics use is
# injected through tarmar_engine.magic.MagicRules, and none is recorded here.
HOUSE_RULINGS += (
    HouseRuling(
        "success_tier_needs_a_success",
        "A casting success tier applies only to a roll that already succeeds; "
        "it never turns a failure into a success.",
        "success first",
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, "
        "_complete_working), _resolve_cast (_roll_and_resolve)",
        "Spencer's ruling on tarmar-studio #292's scope (" + RULING + ")",
        "#825 (tarmar-engine #26)",
    ),
    HouseRuling(
        "magic_bonus_on_rolled_effect",
        "A tier's and a held push's effect bonuses add to the spell's rolled "
        "damage or healing, before armour, at the injected rate for that kind "
        "of effect. A spell with no rolled effect (a continuing one) takes no "
        "tier bonus and cannot be pushed, and a kind the rules give no push "
        "bonus cannot be pushed either. No page gives the size of either "
        "bonus; both are injected.",
        ("damage", "healing"),
        "tarmar_engine/engine.py _resolve_cast (_roll_and_resolve), _check_push "
        "(cast_spell, _begin_craft)",
        RULING,
        "#825 (tarmar-engine #26/#27)",
    ),
    HouseRuling(
        "push_control_after_casting",
        "The Control Roll is rolled only after a successful casting roll, "
        "before the spell's aim and effect, and takes the casting roll's "
        "injury band and off-balance penalty.",
        ("casting", "control", "spell aim"),
        "tarmar_engine/engine.py _push_control_roll",
        RULING,
        "#825 (tarmar-engine #27)",
    ),
    HouseRuling(
        "pushed_mana_paid_with_cost",
        "Pushed mana is paid with the spell's cost whenever the cast pays: on "
        "a success, and on the failures that lose mana (#816).",
        "with the cost",
        "tarmar_engine/engine.py _pay_cast (_roll_and_resolve)",
        RULING,
        "#825 (tarmar-engine #27)",
    ),
    HouseRuling(
        "tier_refund_before_control",
        "A tier's mana refund is paid as soon as the casting roll succeeds, "
        "before a pushed spell's Control Roll, and a Runaway on that roll "
        "does not take it back.",
        "before the Control Roll",
        "tarmar_engine/engine.py _casting_success_tier",
        RULING,
        "#825 (tarmar-engine #26/#27)",
    ),
    HouseRuling(
        "tier_refund_cap_includes_push",
        "A tier's mana refund is capped at what the cast cost, pushed mana included.",
        "spell cost + pushed mana",
        "tarmar_engine/engine.py _casting_success_tier",
        RULING,
        "#825 (tarmar-engine #26/#27)",
    ),
    HouseRuling(
        "channel_bounds_whole_casting",
        "Channel bounds the whole mana of one casting: the spell's cost and "
        "its pushed mana together, before any tier refund. A spell whose own "
        "cost is past the caster's Channel for it cannot be cast, and leaves "
        "the menu; where Push's own optional cap is injected too, the "
        "tighter of the two holds.",
        "spell cost + pushed mana",
        "tarmar_engine/magic.py MagicRules.cast_within_channel, called by "
        "tarmar_engine/engine.py _check_within_channel (cast_spell, "
        "_begin_craft) and tarmar_engine/policy.py _within_channel",
        RULING,
        "#825 (tarmar-engine #29)",
    ),
    HouseRuling(
        "channel_unrecorded_level",
        "A spell or general skill the caster's snapshot records no level for "
        "is read at level 0 when Channel derives from it, as a weapon "
        "missing from the weapon-skill map is.",
        0,
        "tarmar_engine/magic.py UNRECORDED_SKILL_LEVEL",
        RULING,
        "#825 (tarmar-engine #29)",
    ),
    HouseRuling(
        "channel_kind_belongs_to_the_spell",
        "The kind of magic that picks a Channel derivation belongs to the "
        "spell (injected per spell key, with an injected default), not to the "
        "caster: two casters casting one spell derive Channel the same way. "
        "The page does not say which: the alternative reading makes the kind "
        "belong to the way a caster casts the spell, keyed by caster and "
        "spell together, so one spell could take either derivation. Amended "
        "for True Magic toward that alternative, in one case: a cast may name "
        "its kind, which then stands in for the spell's own, and a True cast "
        "or a working names the True kind. Every other cast names none and "
        "derives by the spell's own.",
        "per spell, unless the cast names a kind",
        "tarmar_engine/magic.py ChannelRules.kind_for, MagicRules.cast_kind",
        "Spencer's decision of 2026-11-17 that a True cast derives Channel "
        "from the True skill (" + RULING + ")",
        "#825 (tarmar-engine #29); #208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "channel_not_on_renewal",
        "Channel bounds a casting (phase 5), not a phase-2 renewal: a renewal "
        "pays only the spell's cost again, which its casting already held "
        "within the Channel, and is not checked a second time.",
        "casting only",
        "tarmar_engine/engine.py phase_renew_spells",
        RULING,
        "#825 (tarmar-engine #29)",
    ),
    HouseRuling(
        "debt_roll_after_effect",
        "The debt roll is made after the spell's aim and effect, so the spell "
        "is paid for before the caster learns the cost, and it is made on "
        "every cast that borrowed, whatever became of the spell: a failed "
        "casting roll that paid, a Control Roll that stopped the spell and "
        "an aim that missed all still owe. A failure cannot take the spell "
        "back; it lands in an injected band, which is narrated, takes its "
        "cost and starts a Runaway when it is one (debt_roll_graded_bands). "
        "It is made even when a tier's refund has paid the "
        "whole debt back, since the caster borrowed on this cast. The target "
        "is the injected attributes summed less the injected penalty per "
        "point owed, and less the casting roll's injury band and off-balance "
        "penalty, as the Control Roll takes them (orchestrator's ruling: one "
        "roll-under reading for the magic rolls).",
        ("casting", "spell aim", "spell damage", "debt"),
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, "
        "_complete_working), _debt_roll (_roll_and_resolve, _lose_working)",
        RULING,
        "#825 (tarmar-engine #28)",
    ),
    HouseRuling(
        "borrowed_when_the_cast_pays",
        "A borrowing cast borrows only when the cast pays (#816): on a "
        "success, and on the failures that lose mana. A failure that keeps "
        "its mana borrows nothing and makes no debt roll.",
        "when the cast pays",
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, _complete_working)",
        RULING,
        "#825 (tarmar-engine #28)",
    ),
    HouseRuling(
        "borrowing_counts_toward_channel",
        "Channel bounds the whole casting, the spell's cost and its pushed "
        "mana, whether paid from the pool or borrowed: a casting past Channel "
        "is refused, borrowing or not. With no injected debt cap Channel is "
        "the only bound, so Borrowing without a cap needs Channel rules.",
        "spell cost + pushed mana, borrowed or not",
        "tarmar_engine/engine.py _check_within_channel (cast_spell, "
        "_begin_craft); tarmar_engine/policy.py "
        "_borrow_candidates; tarmar_engine/magic.py "
        "MagicRules.cast_within_channel and MagicRules.channel_bound",
        RULING,
        "#825 (tarmar-engine #28)",
    ),
    HouseRuling(
        "ai_never_borrows",
        "As with Push, only a player's choice borrows: the borrowing variants "
        "are on the menu at a score of 0 and the AI's choice is the one it "
        "makes without them.",
        "player only",
        "tarmar_engine/policy.py _borrow_candidates",
        RULING,
        "#825 (tarmar-engine #28)",
    ),
    HouseRuling(
        "debt_persists_on_the_combatant",
        "What a caster owes is kept on the combatant across turns and in its "
        "snapshot, for the caller to write back to the character after the "
        "battle; the engine settles it only by refunds.",
        "CombatantState.debt",
        "tarmar_engine/state.py CombatantState",
        RULING,
        "#825 (tarmar-engine #28)",
    ),
    HouseRuling(
        "refunds_pay_debt_first",
        "When the injected rules say refunds pay the debt first, the mana a "
        "tier's refund hands back (the only mana the engine hands back) "
        "reduces the debt before it refills the pool, before the debt roll. "
        "When they say it does not, the refund refills the pool and is "
        "capped at what the pool paid (the cost less what was borrowed), so "
        "a borrowing cast never leaves the pool above where it started.",
        "tier refund",
        "tarmar_engine/engine.py _regain_mana",
        RULING,
        "#825 (tarmar-engine #28)",
    ),
    HouseRuling(
        "debt_roll_graded_bands",
        "A failed debt roll is graded as the Control Roll is: its margin "
        "picks an injected band, and a natural total on the automatic list "
        "lands in the last, open-ended band whatever the margin. A band's "
        "cost is its injected cost per point of debt times the debt owed at "
        "the roll, after any refund has paid some back (the debt the roll's "
        "penalty counts), taken from the band's pool by the path a gait's "
        "Fatigue cost takes, so unconsciousness and the survival saves apply "
        "as for any other loss and a death cites the debt roll; a band with "
        "no pool, or a cost of 0, takes nothing.",
        ("debt owed at the roll", "automatic total in the last band"),
        "tarmar_engine/engine.py _debt_roll, apply_pool_cost",
        "Spencer's ruling of 2026-11-17 on tarmar-engine #74 (" + RULING + ")",
        "#825 (tarmar-engine #74)",
    ),
)

HOUSE_RULINGS += (
    HouseRuling(
        "one_runaway_at_a_time",
        "A combatant already feeding a Runaway that starts another keeps the "
        "first: the second is logged and starts nothing, so one spell at a "
        "time feeds on a combatant.",
        1,
        "tarmar_engine/engine.py _begin_runaway (_roll_and_resolve, "
        "_push_control_roll, _debt_roll, _lose_working, anchor_runaway)",
        RULING,
        "#825 (tarmar-engine #30)",
    ),
    HouseRuling(
        "runaway_drain_in_renew_phase",
        "A Runaway feeds on its caster in phase 2, Renew Spells, before the "
        "renewals, on every turn after the one it began (that turn's cost is "
        "the casting's own), conscious caster or not.",
        2,
        "tarmar_engine/engine.py phase_renew_spells, _feed_runaway",
        RULING,
        "#825 (tarmar-engine #30)",
    ),
    HouseRuling(
        "runaway_effect_is_narrated_not_reapplied",
        "The page leaves the growth of a Runaway's effect to the Game Master, "
        "so the engine does not apply the spell's effect again on its later "
        "turns: the drain event carries the turns it has run and the "
        "injected growth per turn, for the caller to narrate. Once anchored, "
        "the growth (the injected bonus per turn x the turns it ran) is added "
        "where a tier's bonus is to the anchorer's next cast of that spell, "
        "and consumed by it, with no time limit. A continuing spell's "
        "renewal rolls no effect, so for it the growth is narrative only, "
        "and its record goes when the spell leaves the anchorer's active "
        "spells.",
        ("turn", "effect_bonus_per_turn"),
        "tarmar_engine/engine.py _feed_runaway",
        RULING,
        "#825 (tarmar-engine #30)",
    ),
    HouseRuling(
        "anchor_takes_casting_penalties",
        "Both anchor rolls, the caster's own and a helper's, are roll-under "
        "against the injected attribute less the casting roll's injury band "
        "and off-balance penalty, as the Control Roll and the debt roll take "
        "them (one roll-under reading for the magic rolls).",
        "injury band and off-balance",
        "tarmar_engine/engine.py anchor_runaway; tarmar_engine/policy.py _anchor_note",
        RULING,
        "#825 (tarmar-engine #30)",
    ),
    HouseRuling(
        "ai_anchors_only_itself",
        "The page says nothing of the AI. It always anchors its own Runaway "
        "when it acts, since anchoring is the only way to survive one, and "
        "never another's: a helper's anchor is on the menu at a score of 0, "
        "for a player.",
        "its own only",
        "tarmar_engine/policy.py choose_option, _anchor_candidates",
        RULING,
        "#825 (tarmar-engine #30)",
    ),
    HouseRuling(
        "anchored_spell_becomes_the_anchorers",
        "A continuing spell's Runaway, anchored, becomes an ordinary active "
        "spell of whoever anchored it, the caster or a helper; the caster "
        "loses it if it held it. The anchorer keeps it going by renewing it "
        "in phase 2 or it lapses unpaid, as any continuing spell (the engine "
        "offers no other choice to end it). Any other spell has had its "
        "effect, so anchoring it simply ends the Runaway.",
        "CombatantState.active_spells",
        "tarmar_engine/engine.py anchor_runaway",
        RULING,
        "#825 (tarmar-engine #30)",
    ),
)

HOUSE_RULINGS += (
    HouseRuling(
        "counter_resolves_at_the_cast",
        "The page orders a counter's declaration by adjusted DEX. In this "
        "engine every action is chosen before the Actions phase runs, so a "
        "counter is always chosen before the cast it opposes: the opposed roll "
        "is made when the spell is cast, before its casting roll, against "
        "every defender who chose a counter against that caster, whatever "
        "their DEX order. A faster defender pays its commitment at its own "
        "action; a slower one pays it at the cast, and its own action is then "
        "spent. The page's two timings collapse into one roll at the cast.",
        "at the cast",
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, "
        "_complete_working), _oppose_cast (_roll_and_resolve), declare_counter",
        RULING,
        "#825 (tarmar-engine #31)",
    ),
    HouseRuling(
        "counter_names_a_caster",
        "The page has a faster defender commit before it sees the cast, so a "
        "counter names a caster, not a cast: any mage (a spell known or a "
        "spell skill recorded) may counter any enemy mage, whether or not "
        "that caster has chosen its action yet, and the counter opposes that "
        "caster's cast this turn whatever its target. The page does not say "
        "who may counter; whether only the spell's target may is left to the "
        "canon's own rulings. A counter is a whole action, standing still.",
        "any mage against an enemy mage",
        "tarmar_engine/policy.py _counter_candidates",
        RULING,
        "#825 (tarmar-engine #31)",
    ),
    HouseRuling(
        "ai_neither_counters_nor_escalates",
        "The page says nothing of the AI. As with Push and Borrowing, the AI "
        "never counters and never presses a countered spell again: both are "
        "on the menu at a score of 0, for a player, and an AI caster whose "
        "spell stands countered takes its next best option, which ends the "
        "contest.",
        "player only",
        "tarmar_engine/policy.py choose_option, _mark_escalations, _counter_candidates",
        RULING,
        "#825 (tarmar-engine #31)",
    ),
    HouseRuling(
        "committed_mana_is_spent",
        "The defender commits its mana as its action. A counter whose caster "
        "casts nothing that meets it this turn lapses at the end of the "
        "Actions phase, and the mana stays spent.",
        "spent",
        "tarmar_engine/engine.py declare_counter, _lapse_counters",
        RULING,
        "#825 (tarmar-engine #31)",
    ),
    HouseRuling(
        "countered_cast_makes_no_casting_roll",
        "The opposed roll comes before the casting roll. A countered spell "
        "makes no casting roll, so no tier, Control Roll or Runaway can come "
        "of it; its whole mana is paid all the same (pushed and borrowed mana "
        "included, and a borrowing cast's debt roll follows), where an "
        "ordinary failed casting roll would keep it.",
        "no casting roll, mana paid",
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, _complete_working)",
        RULING,
        "#825 (tarmar-engine #31)",
    ),
    HouseRuling(
        "contest_ends_on_any_other_action",
        "A countered spell's contest stands on its caster across turns. It "
        "ends when the caster chooses anything but casting that spell at that "
        "target again, when such a cast meets no counter (the cast then goes "
        "on as an ordinary one), or when the caster gets past the counter. The "
        "defender cannot start one: a counter needs a cast to oppose.",
        "another_action",
        "tarmar_engine/engine.py phase_initial_movement, _escalates, _end_contest",
        RULING,
        "#825 (tarmar-engine #31)",
    ),
    HouseRuling(
        "defender_off_balance_not_on_counter",
        "A defender owing an off-balance penalty spends it on its counter "
        "action, which makes no roll of its own, so the penalty never reaches "
        "its side of the opposed roll; that roll takes only its injury band. "
        "The caster's side, made inside the cast, takes both, as the casting "
        "roll does.",
        "injury band only",
        "tarmar_engine/engine.py _oppose_cast",
        RULING,
        "#825 (tarmar-engine #31)",
    ),
    HouseRuling(
        "lifted_counter_minimum_is_zero",
        "Spencer's ruling of 2026-10-14 lets a Game Master lift a counter's "
        "commitment bounds for one figure without saying what then bounds it. "
        "The lifted least is 0 and the lifted most is the defender's whole "
        "purse; a negative commitment is refused for everyone. A commitment "
        "of 0 buys no bonus but still gets a full opposed roll on the "
        "defender's attribute, so a mage with the bounds lifted is offered a "
        "counter every turn, even with an empty purse. Awaiting Spencer's "
        "word: the Game Master lifts the bounds knowingly.",
        0,
        "tarmar_engine/magic.py LIFTED_COUNTER_MINIMUM, CounterspellRules.bounds",
        RULING,
        "#37",
    ),
    HouseRuling(
        "lifted_cast_names_its_channel",
        "A cast that goes past a bound a Game Master lifted for its caster "
        "still names the caster's Channel for the spell (channel, "
        "channel_kind and 'Channel N' in the message), and lists each lifted "
        "bound it actually went past under lifted_bounds, push_cap before "
        "channel; a counter past its lifted bounds lists counter_bounds on "
        "its counter_declared event. A cast or counter within the bounds "
        "carries no such key, lifted or not.",
        "lifted_bounds",
        "tarmar_engine/engine.py cast_spell, _working_channel (craft_working, "
        "_complete_working), _channel_used, _cast_cost_keys, declare_counter",
        RULING,
        "#37",
    ),
    HouseRuling(
        "lifted_channel_bounds_the_shortfall",
        "With Channel lifted for a caster, a casting from its own mana is "
        "bounded by the purse alone, and a borrowing casting by the injected "
        "debt cap where there is one. Where Borrowing has no debt cap, "
        "Channel bounds only the shortfall: a borrowing casting may take the "
        "caster's mana plus its Channel. The bound is finite, so the menu can "
        "list the amounts; more mana never refuses a cast that less mana "
        "allowed; and the lifted caster borrows more than it could unlifted. "
        "Awaiting Spencer's word: the ruling lifts Channel without saying "
        "what then bounds a borrowing.",
        "mana + Channel, when Borrowing has no debt cap",
        "tarmar_engine/magic.py MagicRules.channel_bound",
        RULING,
        "#37",
    ),
    HouseRuling(
        "generic_counter_is_learned_never_cast",
        "schools-of-magic.md lists Counter 1 to Counter 5 among Protection's "
        "spells. spell-descriptions.md's Counter row describes one spell of "
        "levels 1 to 5 that is cast, investing mana in the Counterspell duel: "
        "'Its five tiers are the five levels of investment a caster can bring "
        "to that duel, not five different spells.' The catalog departs from "
        "that row, on the consuming game's reading that the rungs are five "
        "spells, which Spencer confirmed on 2026-11-17. It holds each rung as "
        "a generic counter: a figure learns it as it learns any spell, and "
        "its level is the highest spell level its holder's counter is helped "
        "against. It is never cast: the menu offers no cast of one, the "
        "engine refuses one, and it names no aim, no effect, no renewal and "
        "no element." + _WHILE_COUNTERS_ARE_FIVE_SPELLS,
        "learned, never cast",
        "tarmar_engine/spells.py Spell.generic_counter; "
        "tarmar_engine/policy.py _cast_candidates; "
        "tarmar_engine/engine.py cast_spell",
        "Spencer's confirmation of 2026-11-17 that the Counter rungs are five "
        "spells (" + RULING + ")",
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "generic_counter_level_is_the_highest_rung_known",
        "A figure's generic counter level is the level of the highest generic "
        "counter among the spells it knows, and 0 with none. The rungs do not "
        "add together, and a rung's mastery level changes nothing: the level "
        "is read off the list of spells known and nothing else."
        + _WHILE_COUNTERS_ARE_FIVE_SPELLS,
        "highest rung known",
        "tarmar_engine/spells.py generic_counter_level",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "generic_counters_climb_one_ladder",
        "The generic counters stand on one ladder of their own, indexed "
        "beside the elements' ladders, so a rung's lower tier is the generic "
        "counter one level down, whatever its school. A caller that holds a "
        "student to an element's ladder rung by rung can hold the Counter "
        "rungs to theirs the same way. A generic counter has no element and "
        "no element siblings." + _WHILE_COUNTERS_ARE_FIVE_SPELLS,
        "generic counter",
        "tarmar_engine/spells.py GENERIC_COUNTER_LADDER, _index_ladders, lower_tier",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "working_mana_is_its_tier",
        "A working's mana is its tier and any mana pushed into it, by the "
        "public rule that a spell costs its level: a built working's level "
        "is its tier, so whatever reads a casting's mana counts a working as "
        "it counts a catalog spell.",
        "tier",
        "tarmar_engine/magic.py TrueMagicRules.working, casting_mana; "
        "tarmar_engine/engine.py _begin_craft, craft_working",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "craft_gathers_even_shares",
        "A working's whole mana is gathered in even shares, one per craft "
        "turn: the mana gathered after a turn is the whole mana times the "
        "craft turns taken, over the turns the working takes, rounded down. "
        "The mana gathered never runs ahead of an even pace, no two shares "
        "differ by more than a point, and the last turn brings the whole. "
        "The alternative reading pays the whole mana when the craft begins.",
        "even shares, rounded down",
        "tarmar_engine/magic.py TrueMagicRules.gathered_after; "
        "tarmar_engine/engine.py craft_working",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "channel_lift_lifts_the_tier_cap",
        "A Game Master lets a figure craft a working above its tier cap by "
        "lifting Channel for it: the one lift lifts the cap too, since both "
        "bound how large a casting the figure may make. No bound of its own "
        "names the cap, so the liftable bounds are as they were, and a "
        "working whose tier is past the cap reports Channel as the lifted "
        "bound it went past. The alternative reading is a further bound, "
        "lifted on its own.",
        "channel",
        "tarmar_engine/magic.py MagicRules.tier_cap, lifted_bounds_passed; "
        "tarmar_engine/engine.py _begin_craft",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "grant_stands_until_withdrawn",
        "A Game Master's grant of a working is not spent by a craft: it "
        "stands for the battle, as a lifted bound does, until the Game Master "
        "withdraws it in the consuming game's record. The engine reads a "
        "figure's granted workings and never writes them. The alternative "
        "reading is a single use, which the engine would take off the list "
        "when the craft begins.",
        "stands until withdrawn",
        "tarmar_engine/magic.py MagicRules.granted; "
        "tarmar_engine/state.py CombatantState.granted_workings; "
        "tarmar_engine/engine.py _begin_craft",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "true_cast_rolls_the_spells_own_roll",
        "A True cast of a spell makes that spell's ordinary casting roll, "
        "under the spell's own attribute, with no bonus for the caster's "
        "level in the True skill. Only a working's roll takes that bonus. "
        "The alternative reading adds the bonus to a True cast too.",
        "the spell's own roll",
        "tarmar_engine/engine.py cast_spell",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "crafting_ends_continuing_spells",
        "A caster who crafts a working does nothing else, and that includes "
        "keeping a spell up: each spell the caster has running ends when the "
        "craft begins, in the action that begins it. Ending them in the Renew "
        "Spells phase instead would treat workings unevenly, since that phase "
        "comes before the turn's choice: a working of one turn would never "
        "lose them, and a longer one would lose them before its caster chose "
        "whether to go on. The alternative reading is that renewing a spell "
        "is no action, and goes on as usual through a craft.",
        "end when the craft begins",
        "tarmar_engine/engine.py _begin_craft",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "another_action_abandons_the_craft",
        "A crafting caster does not move, defend, dodge or counter. The craft "
        "option chosen again is the craft's continuation; choosing any other "
        "option ends the working when the choice is recorded in Initial "
        "Movement, before the caster moves. The working is lost with the "
        "mana it had gathered.",
        "abandoned",
        "tarmar_engine/engine.py phase_initial_movement, _settle_craft_choice",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "craft_begins_where_a_cast_may",
        "A craft is begun exactly where a cast may be made: disengaged or "
        "engaged, in hand-to-hand with empty hands, and never in a grapple. "
        "A working has no Spell Mastery level, so it reads at level 1 under "
        "the one guard a cast answers to. As for a cast, a prone figure "
        "outside hand-to-hand has STAND UP alone on its menu, so no craft "
        "begins there, and the engine does not check it again: nothing knocks "
        "a standing figure prone between its choice and its action but "
        "unconsciousness.",
        "where a cast may be made",
        "tarmar_engine/engine.py _begin_craft, _cast_blocked",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "working_aimed_by_its_last_craft_action",
        "The figure a working is aimed at is the one its latest craft action "
        "named: each craft action that is counted names the target anew, and "
        "may name another figure than the last did, or none.",
        "latest craft action",
        "tarmar_engine/engine.py craft_working, _complete_working "
        "(_complete_workings); tarmar_engine/state.py CraftState.target_id",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "craft_fixes_push_and_borrow_at_the_start",
        "The mana pushed into a working, and whether a share its caster's "
        "pool cannot pay may be borrowed, are chosen when the craft begins "
        "and change on no later turn. The continuation carries both as they "
        "began; a craft option that names another working, another push or "
        "another borrowing while a craft stands is a caller's error, refused "
        "where the choice is recorded.",
        "fixed when the craft begins",
        "tarmar_engine/engine.py _begin_craft, _settle_craft_choice; "
        "tarmar_engine/state.py CraftState",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "craft_hit_is_damage_that_lands",
        "A hit on a crafting caster is any damage that takes at least a "
        "point from them after armour: a weapon's, a bare-handed blow's or a "
        "spell's. Each forces its own roll to keep the working, made once "
        "the damage is applied, by a caster it has left active. A blow the "
        "armour stops entirely forces none, and neither does a cost the "
        "caster pays from their own pool (a debt band's, a Runaway's feed, a "
        "drain), which is no damage. The alternative readings count a "
        "stopped blow, or a cost.",
        "damage past the armour",
        "tarmar_engine/engine.py apply_damage (resolve_attack, _resolve_cast), "
        "_interruption_roll (apply_damage)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "interruption_takes_the_injury_band",
        "The roll to keep a working takes its caster's injury band, by the "
        "one reading every roll a figure makes as its action takes it, and "
        "reads the band after the hit's damage is applied, so the blow that "
        "forces the roll can itself bring the band on. The working's own "
        "roll takes the band too, as it stands when the working goes off, "
        "and so does its side of a counter. An off-balance penalty reaches "
        "none of these rolls. A figure is put off-balance only by its own "
        "fumble, which a crafting caster makes none of, or by a grapple "
        "that takes hold on a natural 20, which ends the craft; and a "
        "penalty owed as a craft begins is spent by the action that begins "
        "it, which rolls nothing, before the working goes off.",
        "injury band, read after the hit",
        "tarmar_engine/engine.py _interruption_roll (apply_damage), "
        "_roll_and_resolve (cast_spell, _complete_working), _oppose_cast "
        "(_roll_and_resolve), _action_roll_penalty",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "working_runaway_costs_the_mana_gathered",
        "A working that runs away as a hit breaks its craft starts a Runaway "
        "whose original cost is the mana the craft had gathered by then, as "
        "a cast's Runaway feeds on what the cast took: not the working's "
        "whole mana, and not only the part of it borrowed. It feeds, is "
        "kept and is anchored as any Runaway is; anchored, it simply stops, "
        "a working being no continuing spell. A working whose craft has "
        "gathered nothing has nothing to run away with: it is lost and "
        "starts no Runaway, even on a band or a total that is one, and its "
        "event says it does not run away. With no Runaway rules injected a "
        "working that runs away is flagged and nothing starts.",
        "mana gathered",
        "tarmar_engine/engine.py _interruption_roll (apply_damage), "
        "_lose_working (_settle_craft_choice, craft_working, "
        "_interruption_roll, _fall_unconscious, _establish_grapple, "
        "_roll_and_resolve), "
        "_begin_runaway (_roll_and_resolve, _push_control_roll, _debt_roll, "
        "_lose_working, anchor_runaway)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "what_else_breaks_a_craft",
        "Three things besides a hit can reach a crafting caster. A grapple "
        "that takes hold ends the working with no roll, since a held figure "
        "casts only at a Spell Mastery a working has none of. An enemy "
        "entering hand-to-hand with a caster who has a weapon in hand ends "
        "it with no roll, at the caster's next craft action, where the one "
        "guard a cast answers to refuses it; a caster with empty hands "
        "crafts on, and the blow that opened the hand-to-hand forces the "
        "usual roll if it lands. A fall does not end it: a prone caster's "
        "craft action goes on. Unconsciousness ends it, which is no choice. "
        "The alternative reading for the grapple and the hand-to-hand is "
        "that they force the roll a hit does.",
        {
            "grapple": "grappled",
            "hand-to-hand, weapon in hand": "blocked",
            "fall": None,
            "unconsciousness": "unconscious",
        },
        "tarmar_engine/engine.py _establish_grapple (attempt_grapple), "
        "craft_working, _cast_blocked (cast_spell, craft_working, "
        "_begin_craft), _fall_unconscious (check_unconsciousness, "
        "_feed_runaway)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "working_goes_off_after_the_actions",
        "A working goes off at the end of the Actions phase of the turn its "
        "last craft action is taken, after every figure has acted and before "
        "the counters no spell met lapse. A working whose craft takes one "
        "turn, begun this turn, can therefore still be broken by a slower "
        "enemy's hit. Several workings go off in the phase's own order. The "
        "alternative reading has it go off in its caster's own action.",
        "end of the Actions phase",
        "tarmar_engine/engine.py phase_actions, _complete_workings "
        "(phase_actions), _complete_working (_complete_workings), "
        "_in_action_order (phase_actions, _complete_workings)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "failed_working_keeps_its_mana",
        "A working whose roll fails on a total that keeps the mana gets back "
        "every share its craft gathered: the pool what it paid, and the debt "
        "what the craft borrowed. It owes no drain and makes no debt roll, "
        "as a failed cast keeps its mana (mana_on_failure). A failing total "
        "that loses the mana keeps the shares spent, and the working is "
        "drained as one that failed. The alternative reading leaves the "
        "shares spent on any failure, since they had already left the pool.",
        "shares handed back",
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, "
        "_complete_working), _return_shares (_roll_and_resolve)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "drain_counts_the_whole_working",
        "A working's drain counts all its mana, the mana pushed into it and "
        "the mana its craft borrowed included, before any tier refund, as "
        "Channel counts a casting. It is taken last, after the debt roll, so "
        "a caster it fells has made that roll. A working that goes off and "
        "misses its aim, or lands on no one, still owes it. A True cast of a "
        "catalog spell is drained for its pushed mana alone.",
        {"working": "whole mana", "catalog spell": "pushed mana"},
        "tarmar_engine/engine.py _roll_and_resolve (cast_spell, "
        "_complete_working), _take_drain (_roll_and_resolve, _lose_working)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "healing_working_names_its_figure",
        "A healing working heals the figure its craft named when that is the "
        "caster or one of the caster's living teammates, conscious or not, "
        "and the caster in every other case: an enemy named, a dead figure, "
        "or none. It wakes no one: the engine has no path back to "
        "consciousness. A catalog healing spell heals its caster, as it "
        "always has.",
        "the caster or a living teammate",
        "tarmar_engine/engine.py _healed_figure (_resolve_cast), _resolve_cast "
        "(_roll_and_resolve); tarmar_engine/state.py BattleState.teammates_of",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "narrated_working_rolls_its_tier",
        "A working whose effect the engine does not apply still takes its "
        "tier's turns, mana and drain and makes its roll. When it goes off "
        "the engine rolls the effect's dice at that tier, where it has any, "
        "and reports the total, with the figure the craft named passed "
        "through as given; the Game Master says what the total does. It "
        "makes no aim roll and applies nothing, takes no tier's or push's "
        "bonus, and cannot be pushed, there being no rolled effect for a "
        "bonus to add to. Its tier is a Game Master's to name: an effect "
        "that needs a grant is crafted only at the keys granted.",
        "the effect's dice at the tier, reported and not applied",
        "tarmar_engine/engine.py _narrated_effect (_resolve_cast), "
        "_narrate_working (_resolve_cast), _check_push (cast_spell, "
        "_begin_craft), _begin_craft (craft_working); tarmar_engine/magic.py "
        "WorkingEffect.dice_at",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "pushed_working_makes_one_control_roll",
        "A pushed working makes Push's Control Roll once, when it goes off, "
        "after its own roll succeeds, with Push's rules and bands as "
        "injected: no craft turn makes one. The roll to keep a working "
        "through a hit is a separate roll with its own bands, one for each "
        "hit, and its penalty already counts the pushed shares, which are "
        "part of the mana gathered.",
        "once, when the working goes off",
        "tarmar_engine/engine.py _resolve_cast (_roll_and_resolve), "
        "_push_control_roll (_resolve_cast), craft_working, _interruption_roll "
        "(apply_damage)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
    HouseRuling(
        "craft_borrows_share_by_share",
        "A borrowing craft checks the debt cap against its whole projected "
        "shortfall when it begins, adds each share's shortfall to the debt "
        "as that share falls due, and makes one debt roll when the craft "
        "ends, whether its working goes off, is stopped, fails on a total "
        "that loses the mana or is lost. It makes none on a failure that "
        "keeps the mana, whose borrowed shares come off the debt again, nor "
        "for a caster no longer active when the craft is lost.",
        "one debt roll, when the craft ends",
        "tarmar_engine/engine.py _begin_craft (craft_working), craft_working, "
        "_lose_working (_settle_craft_choice, craft_working, "
        "_interruption_roll, _fall_unconscious, _establish_grapple, "
        "_roll_and_resolve), _roll_and_resolve (cast_spell, _complete_working), "
        "_debt_roll (_roll_and_resolve, _lose_working)",
        RULING,
        "#208 (tarmar-engine #77)",
    ),
)


def ruling(key: str) -> HouseRuling:
    """Look one entry up by key.

    Raises:
        KeyError: for a key no entry carries.
    """
    for entry in HOUSE_RULINGS:
        if entry.key == key:
            return entry
    raise KeyError(key)
