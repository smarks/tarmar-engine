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
        "Spell effects: Fire Missile 1d6, Fire Ball 2d6 to one target (the "
        "description's area is not modelled), Lightning Bolt 3d6, Heal 1d6 "
        "Fatigue to the caster only, Fatigue 1d6+1, Wound 1d6 to Body, "
        "Shield +1 TN, Blur -2 to hit. schools-of-magic.md names the spells "
        "and levels; no page gives these numbers.",
        {
            "fire_missile": "1d6",
            "fire_ball": "2d6",
            "lightning_bolt": "3d6",
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
        "tarmar_engine/engine.py cast_spell",
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
        "The phase-4 step of a missile or cast option is at most 2 hexes.",
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
        "A spell's mana is spent on success, on a 17 ('mana lost') and on an "
        "18 (Runaway 'drains mana equal to original casting cost'); every "
        "other failure keeps it.",
        frozenset({17, 18}),
        "tarmar_engine/engine.py CASTING_MANA_LOST_ROLLS",
        RULING,
        "#816",
    ),
    HouseRuling(
        "grappled_casting_mastery",
        "In a grapple, holder and held alike, a spell is cast only at Spell "
        "Mastery 3 (no gestures and no words) and renewed at 2 or better; "
        "either side is offered its Mastery 3 casts.",
        {"cast": 3, "renew": 2},
        "tarmar_engine/engine.py cast_spell, phase_renew_spells",
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
        "#867).",
        ("they close in too", "both bare-handed"),
        "tarmar_engine/combat_math.py hth_entry_reason; engine.py _enter_hth",
        RULING,
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
        "tarmar_engine/policy.py _hth_casts; engine.py cast_spell",
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
        "tarmar_engine/engine.py cast_spell",
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
        "tarmar_engine/engine.py cast_spell",
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
        "tarmar_engine/engine.py cast_spell",
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
        "tarmar_engine/engine.py cast_spell; tarmar_engine/policy.py _within_channel",
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
        "spell together, so one spell could take either derivation.",
        "per spell",
        "tarmar_engine/magic.py ChannelRules.kind_for",
        RULING,
        "#825 (tarmar-engine #29)",
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
