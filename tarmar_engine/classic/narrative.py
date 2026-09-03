"""Classic plain-language combat narration for the running log — ported from melee.

Turns an :class:`~.combat.AttackResult` into a readable sentence — "The
red Knight swings a Broadsword at the blue Knight, who dodges clear." — so a
player can follow a fight without decoding raw rolls. Pure string-building; it
reads an attack outcome and the figures, and changes no state.

It works for either rules profile: ``result.needed`` is the adjusted-DX target
under classic Melee or the Target Number under Tarmar. The threshold reads
naturally in both directions — classic Melee rolls 3d6 *under* the target
(``needed N or less``) while Tarmar rolls a d20 *over* it (``needed N or
more``) — keyed off ``result.roll_under``.
"""
from __future__ import annotations

from .combat import AttackResult, SpellResult
from .data import WeaponKind
from .facing import REAR, SIDE
from .figure import Figure
from .options import Option
from .ruleset import DEAD, KNOCKDOWN, UNCONSCIOUS
from .spells import SPELLS


def _name(figure: Figure) -> str:
    """A figure by its characterful name, with its side in parens for clarity:
    "Baylor the Bashful (red)". The side keeps the two teams distinguishable at a
    glance now that the name no longer carries the class."""
    return f"{figure.name} ({figure.side})"


def _article(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _cap(sentence: str) -> str:
    return sentence[0].upper() + sentence[1:] if sentence else sentence


def _approach(attacker: Figure, target: Figure, weapon, zone: str | None = None,
              thrown: bool = False) -> str:
    """The wind-up, ending on the target so a ", who …" clause can follow."""
    if weapon is None:
        return f"{_name(attacker)} lunges at {_name(target)}"
    verb = ("hurls" if thrown
            else "shoots" if weapon.kind == WeaponKind.MISSILE else "swings")
    # A melee blow from the side/rear is easier to land (the facing bonus); call
    # it out so the higher to-hit number reads as deliberate, not a glitch. Phrased
    # "at the flank of <name>" so it ends on the target and a ", who …" clause
    # still follows cleanly (a possessive after "(red)" would read awkwardly).
    spot = ""
    if weapon.kind != WeaponKind.MISSILE and not thrown:
        spot = ("the flank of " if zone == SIDE
                else "the rear of " if zone == REAR else "")
    return f"{_name(attacker)} {verb} {_article(weapon.name)} at {spot}{_name(target)}"


def _defense_beat_this_attack(target: Figure, result: AttackResult) -> bool:
    """Whether the target's dodge/defend actually raised THIS attack's difficulty.

    A dodge only helps against a missile or thrown attack; a defend only against
    a melee blow (Melee p.20; the engine forces four dice on the matching type).
    Crediting the wrong type would narrate "dodges clear" on a miss the defense
    never influenced.
    """
    ranged = getattr(result, "thrown", False) or (
        result.weapon is not None and result.weapon.kind == WeaponKind.MISSILE)
    if ranged:
        return getattr(target, "dodging", False)
    return getattr(target, "defending", False)


def narrate_attack(attacker: Figure, target: Figure, result: AttackResult) -> str:
    """One vivid line for an attack's outcome (hit, miss, dodge, crit)."""
    approach = _approach(attacker, target, result.weapon, result.zone,
                         getattr(result, "thrown", False))
    if not result.hit:
        # A fumble's own story outranks the dodge line — the natural 1 missed
        # on its own, and the table's outcome is the beat worth reading.
        if result.note == "fumble":
            weapon_name = result.weapon.name if result.weapon else "weapon"
            if result.fumble_effect == "off_balance":
                body = (f"{approach} — and fumbles, staggering off-balance "
                        f"(-2 on the next attack)")
            elif result.fumble_effect == "stress":
                body = (f"{approach} — and fumbles, the {weapon_name} cracking "
                        f"under the strain (a second fumble will break it)")
            else:
                # The running log swaps this line for :func:`narrate_fumble`'s
                # drop/shatter story; this rendering still tells the miss.
                body = f"{approach} — and fumbles, the blow flailing wide"
        elif result.note == "whiff":
            # A blow that never reached a roll — the foe slipped out of reach or
            # fled before a slower attacker could catch it (#147/#270). Narrate
            # the truth with NO needed/rolled clause: no dice were thrown, and a
            # fabricated number would print the wrong roll direction in a Tarmar
            # game (#270). "misses" keeps the miss-word the log invariant needs.
            return _cap(
                f"{approach} — and misses, the blow finding only air as "
                f"{_name(target)} slips out of reach.")
        elif _defense_beat_this_attack(target, result):
            # Only credit the dodge/defend when it actually raised the difficulty:
            # dodge helps only against a missile/thrown attack, defend only against
            # a melee blow (ruleset.py forces four dice on the matching type).
            body = f"{approach}, who dodges clear"
        else:
            body = f"{approach} — and misses"
    elif result.damage == 0:
        body = f"{approach} — but the armour turns it aside"
    elif result.severe_crit:
        body = (f"{approach} — a crushing blow for {result.damage}, "
                f"the wound bleeding freely!")
    elif result.multiplier >= 2:
        body = f"{approach} — a crushing blow for {result.damage}!"
    else:
        body = f"{approach} — and connects for {result.damage}"
    stopped = result.raw_damage - result.damage
    if result.hit and result.damage > 0 and stopped > 0:
        body += f" ({stopped} stopped by armour)"
    detail = f" — {result.to_hit_breakdown}" if result.to_hit_breakdown else ""
    # An auto-hit (a flying weapon that struck a figure mid-flight, an HTH free
    # hit) is not decided by the to-hit roll, so `needed`/`rolled` are NOT a
    # hit/miss test — narrating them as one prints "connects (needed 5, rolled
    # 11)". Say plainly that it was unavoidable instead (#229).
    if getattr(result, "auto_hit", False):
        return _cap(f"{body} (an unavoidable hit{detail}).")
    threshold = "or less" if result.roll_under else "or more"
    # A Tarmar natural 20 rolled a second d20 to confirm the severe crit; say
    # how the confirm went so the upgrade (or its absence) is legible (#233).
    confirm = ""
    if result.confirm_roll:
        verdict = "severe crit" if result.severe_crit else "crit not confirmed"
        confirm = f"; confirm rolled {result.confirm_roll} — {verdict}"
    return _cap(f"{body} (needed {result.needed} {threshold}, "
                f"rolled {result.rolled}{confirm}{detail}).")


def narrate_fumble(attacker: Figure, weapon, *, broke: bool) -> str:
    """A dropped or shattered weapon (a natural-roll fumble)."""
    name = weapon.name if weapon else "weapon"
    if broke:
        return _cap(f"{_name(attacker)}'s {name} shatters with the blow!")
    return _cap(f"{_name(attacker)} fumbles and drops {_article(name)}!")


def narrate_status(target: Figure, status: str | None) -> str | None:
    """The aftermath line when a hit drops the target, else None."""
    if status == DEAD:
        return _cap(f"{_name(target)} falls, slain!")
    if status == UNCONSCIOUS:
        return _cap(f"{_name(target)} crumples, unconscious.")
    if status == KNOCKDOWN:
        return _cap(f"{_name(target)} is knocked sprawling.")
    return None



# ---- non-combat operations -------------------------------------------------
_MOVE_VERB = {
    Option.MOVE: "advances",
    Option.HALF_MOVE: "moves up",
    Option.CHARGE_ATTACK: "charges in",
    Option.DODGE: "darts, dodging",
    Option.MISSILE_ATTACK: "takes aim",
    Option.STAND_UP: "rises to their feet",
    Option.CRAWL: "crawls",
    Option.ATTACK: "stands fast to strike",
    Option.SHIFT_ATTACK: "shifts in to attack",
    Option.SHIFT_DEFEND: "raises a guard",
    Option.ONE_LAST_SHOT: "looses a parting shot",
    Option.DISENGAGE: "breaks away",
    Option.GO_PRONE: "drops prone",
    Option.KNEEL: "kneels",
    Option.DO_NOTHING: "holds, taking no action",
}


def narrate_pass(figure: Figure) -> str:
    """A figure that defers its action to choose last (#192, Pass rule)."""
    return _cap(f"{_name(figure)} passes, deferring to act last.")


def narrate_move(figure: Figure, option: Option, moved: bool,
                 facing: Figure | None = None) -> str | None:
    """A line for a figure's movement-phase action (None if not worth narrating).

    Weapon changes are narrated by :func:`narrate_ready` instead. ``facing`` is
    the enemy the figure ends up facing, if any — recorded so the log shows where
    each figure ended up looking (which decides flank/rear bonuses).
    """
    if option in (Option.READY_WEAPON, Option.CHANGE_WEAPONS):
        return None
    verb = _MOVE_VERB.get(option)
    if verb is None:
        return None
    if option == Option.MOVE and not moved:
        verb = "holds position"
    clause = f", now facing {_name(facing)}" if facing is not None else ""
    return _cap(f"{_name(figure)} {verb}{clause}.")


def narrate_hth(attacker: Figure, target: Figure, kind: str) -> str | None:
    """A hand-to-hand grapple beat (p.17): the grab, a shrug-off, or a free hit."""
    if kind == "grapple":
        return _cap(
            f"{_name(attacker)} drags {_name(target)} to the ground, grappling!")
    if kind == "join":
        return _cap(f"{_name(attacker)} piles onto {_name(target)} in the brawl!")
    if kind == "shrug":
        return _cap(f"{_name(target)} shrugs off {_name(attacker)}'s grab "
                    f"and keeps its feet.")
    if kind == "free_hit":
        return _cap(f"{_name(target)} twists free and lands a blow as "
                    f"{_name(attacker)} is thrown back!")
    return None


def narrate_cascade(attacker: Figure, intended: Figure, struck: Figure) -> str:
    """A blow that missed its downed target in an HTH pile and caught someone
    else instead (Hitting Your Friends, p.17)."""
    return _cap(f"{_name(attacker)}'s blow goes wide of {_name(intended)} "
                f"and strikes {_name(struck)} instead!")


def narrate_hth_disengage(figure: Figure, broke_free: bool) -> str:
    """A figure's attempt to wrench free of a grapple (p.19)."""
    if broke_free:
        return _cap(f"{_name(figure)} wrenches free of the grapple and scrambles up!")
    return _cap(f"{_name(figure)} struggles to break free, but can't.")


def narrate_shield_rush(attacker: Figure, target: Figure, outcome: str) -> str:
    """A shield-rush attempt (p.13): a miss, a no-effect bounce, or a knockdown."""
    if outcome == "miss":
        return _cap(f"{_name(attacker)} rushes with a shield, but {_name(target)} "
                    f"slips aside.")
    if outcome == "no_effect":
        return _cap(f"{_name(attacker)} slams a shield into {_name(target)}, far too "
                    f"massive to budge.")
    if outcome == "fall":
        return _cap(f"{_name(attacker)} slams a shield into {_name(target)}, who "
                    f"crashes to the ground!")
    return _cap(f"{_name(attacker)} slams a shield into {_name(target)}, who keeps "
                f"their feet.")


def narrate_victory(side: str) -> str:
    """The game-ending line: one side is the last left standing."""
    return f"🏆 The {side} hold the field — victory!"


def narrate_dropout(figure: Figure) -> str:
    """A practice-bout drop-out (p.22): worn down to ST 3 or less, the figure
    bows out of the friendly fight unhurt — out of play, but alive."""
    return _cap(f"{_name(figure)} drops out of the practice bout (ST 3 or less).")


def narrate_ready(figure: Figure, weapon) -> str:
    """A figure drawing a different carried weapon — or, with ``weapon`` None,
    re-slinging what it held to stand bare-handed (#425)."""
    if weapon is None:
        return _cap(f"{_name(figure)} re-slings its weapon and readies bare hands.")
    return _cap(f"{_name(figure)} readies {_article(weapon.name)}.")


def narrate_initiative(rolls: dict, winner: str) -> str:
    """Who won the initiative roll."""
    detail = ", ".join(f"{side} {value}" for side, value in rolls.items())
    return f"Initiative ({detail}): {winner} wins."


def narrate_move_order(side: str) -> str:
    return f"{side.capitalize()} will move first."


def narrate_retreat(attacker: Figure, target: Figure, advanced: bool) -> str:
    """A forced retreat (and whether the attacker followed up)."""
    line = f"{_name(attacker)} drives {_name(target)} back"
    return _cap(line + (", advancing into the gap." if advanced else "."))


def narrate_turn(number: int) -> str:
    return f"— Turn {number} —"


# ---- spell narrations (TFT: Wizard; unification milestone 5) -----------------

def narrate_cast_lost(caster: Figure, spell, reason: str) -> str:
    """One truthful line for a queued cast that never happened.

    ``reason`` is ``"knocked_down"`` (floored before its turn to act, rules lines
    250-251, melee #416) or ``"too_weak"`` (wounded below the declared ST since
    declaring, rules lines 167-169, melee #415). Either way no dice were rolled
    and no ST was drained — the line says so, so the log stays auditable.
    """
    caster_name = _name(caster)
    if reason == "knocked_down":
        return _cap(
            f"{caster_name} is down — the {spell.name} is lost, uncast.")
    return _cap(
        f"{caster_name} is too weakened to power {spell.name} — "
        f"the spell fizzles harmlessly, costing nothing.")


def narrate_spell(caster: Figure, target: Figure, result: SpellResult) -> str:
    """One truthful line for a cast's outcome (TFT: Wizard).

    Reports what actually happened — a landed missile blow for its rolled damage,
    an armour-turned bolt, a protection spell taking hold, a plain miss, or a
    fizzle (a 17/18 that lost the full ST, an 18 also knocking the caster down) —
    with NO fabricated numbers (the melee #229/#270 class). ``result.hit``
    decides whether a hit-word or a miss-word appears, keeping the log auditable.
    """
    spell = SPELLS.get(result.spell_id)
    spell_name = spell.name if spell is not None else result.spell_id
    caster_name = _name(caster)
    target_name = _name(target)
    # Line-of-flight beats (melee #417): a spell that never reached its aimed
    # target. Each is a pure function of the result's own fields (plus the
    # passed figures), so the truthfulness audit can re-render it faithfully.
    if result.note == "struck_in_lane":
        if result.damage == 0:
            return _cap(
                f"{caster_name}'s {spell_name} catches {target_name} square in "
                f"its path — but the armour turns it aside.")
        crushing = "a crushing " if result.multiplier >= 2 else ""
        return _cap(
            f"{caster_name}'s {spell_name} catches {target_name} square in its "
            f"path — {crushing}blow connects for {result.damage}!")
    if result.note == "fizzled_in_lane":
        return _cap(
            f"{caster_name}'s {spell_name} fizzles out against {target_name}, "
            f"standing square in its path.")
    if result.note == "flew_on":
        if result.damage == 0:
            return _cap(
                f"the stray {spell_name} flies on and catches {target_name} — "
                f"but the armour turns it aside.")
        crushing = "a crushing " if result.multiplier >= 2 else ""
        return _cap(
            f"the stray {spell_name} flies on and catches {target_name} — "
            f"{crushing}blow connects for {result.damage}!")
    if result.fizzled:
        line = f"{caster_name} invokes {spell_name}, but the spell fizzles"
        if result.knockdown:
            line += f" — the backlash knocks {caster_name} sprawling"
        return _cap(line + f" (loses {result.st_spent} ST).")
    if not result.hit:
        return _cap(
            f"{caster_name} casts {spell_name} at {target_name}, but it goes wide "
            f"(needed {result.needed} or less, rolled {result.rolled}).")
    if spell is not None and spell.is_protection:
        return _cap(
            f"{caster_name} weaves {spell_name} — the spell takes hold, "
            f"stopping {result.stops_granted} hits per attack.")
    if spell is not None and not spell.is_missile:
        # A thrown spell that hit: "the spell takes effect immediately" (rules
        # lines 679-680). The effect itself (a dropped weapon, a Trip's fall, a
        # debuff's numbers) narrates its own follow-up line from the applied
        # values, so this line claims only what the result proves: the cast
        # took hold.
        onto = "" if result.target_uid == result.caster_uid else f" on {target_name}"
        return _cap(f"{caster_name} casts {spell_name}{onto} — "
                    f"the spell takes hold.")
    # A missile spell that hit.
    if result.damage == 0:
        return _cap(
            f"{caster_name} hurls {spell_name} at {target_name} — "
            f"but the armour turns it aside.")
    crushing = "a crushing " if result.multiplier >= 2 else ""
    blow = _MISSILE_SPELL_BLOW.get(result.spell_id, "blow")
    return _cap(
        f"{caster_name} hurls {spell_name} at {target_name} — {crushing}"
        f"{blow} connects for {result.damage}!")


# What each missile spell's landed blow is called in the log — flavour keyed to
# the spell, never to numbers (the damage printed is the rolled damage).
_MISSILE_SPELL_BLOW = {
    "magic_fist": "telekinetic blow",
    "fireball": "gout of flame",
    "lightning": "lightning bolt",
}


def narrate_spell_applied(target: Figure, spell, record: dict) -> str:
    """One truthful line for a lasting spell's effect taking hold (melee #431).

    Reports the REAL applied numbers straight off the spell's catalog data and
    the recorded casting (magnitude from the ST actually invested, duration
    from the record — heavy-target variants already folded in), so the log
    never claims an effect the state does not carry (melee #229/#270).
    """
    name = _name(target)
    remaining = record.get("remaining")
    lasts = (f" for {remaining} turn{'s' if remaining != 1 else ''}"
             if remaining is not None else "")
    if spell.dx_penalty_per_st:
        penalty = spell.dx_penalty_per_st * record.get("st", 0)
        return _cap(f"{name}'s limbs turn leaden — DX {penalty}{lasts}.")
    if spell.defense_dx_penalty:
        return _cap(f"{name}'s outline smears and shivers — attacks and spells "
                    f"against {name} are at {spell.defense_dx_penalty}.")
    if spell.ma_percent == 0:
        return _cap(f"{name} is rooted to the spot — MA 0{lasts}.")
    if spell.ma_percent < 100:
        return _cap(f"{name} wades as through deep water — "
                    f"MA halved{lasts}.")
    if spell.ma_percent > 100:
        return _cap(f"{name} quickens, a blur of motion — MA doubled{lasts}.")
    return _cap(f"the {spell.name} settles on {name}{lasts}.")


def narrate_spell_disarm(target: Figure, item_name: str | None, *,
                         broke: bool) -> str:
    """A Drop Weapon / Break Weapon spell's effect — or its lack of one.

    ``item_name`` is what left the victim's hands (``None`` when there was
    nothing to act on, narrated truthfully rather than claiming an effect)."""
    name = _name(target)
    if item_name is None:
        clutch = "shatters nothing" if broke else "finds nothing to wrench loose"
        return _cap(f"the spell {clutch} — {name}'s hands are empty.")
    if broke:
        return _cap(f"{name}'s {item_name} shatters, riven by the spell!")
    return _cap(f"the {item_name} is wrenched from {name}'s grasp and falls!")


def narrate_spell_trip(target: Figure, *, already_down: bool) -> str:
    """The Trip spell's effect: the victim falls — no save, no damage (spell-ref
    lines 88-91) — or was already down, with nothing left to knock over."""
    if already_down:
        return _cap(f"the spell tugs at {_name(target)}, already down — "
                    f"to no effect.")
    return _cap(f"{_name(target)}'s legs are swept away — down they go!")


def narrate_spell_expired(figure: Figure, spell) -> str:
    """A lasting spell's end (melee #431): a stated duration ran out, or a
    continuing spell lost its caster (rules lines 229-231)."""
    return _cap(f"the {spell.name} on {_name(figure)} fades away.")


def narrate_trip(target: Figure, *, fell: bool, rolled: int, needed: int) -> str:
    """Magic Fist's trip save (spell-ref lines 18-21, melee #421): a 6+-hit fist
    sweeps its target off its feet unless it saves — 3 dice at or under the
    higher of ST and DX. Reports the real roll either way."""
    if fell:
        return _cap(
            f"the blow sweeps {_name(target)} off its feet — down it goes "
            f"(needed {needed} or less to keep footing, rolled {rolled})!")
    return _cap(
        f"{_name(target)} staggers under the blow but keeps its feet "
        f"(needed {needed} or less, rolled {rolled}).")
