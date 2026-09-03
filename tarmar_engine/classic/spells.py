"""
The classic spell catalog (Classic *The Fantasy Trip: Wizard*, 3rd ed.) — SJG data.

COPYRIGHT NOTE: everything in this module is derived from Steve Jackson Games'
classic *Wizard* rules data; like the rest of ``tarmar_engine.classic`` it is
segregated here and never imported by a Tarmar-canon module (guard-tested).
Ported verbatim from melee's ``engine/spells.py`` at the battle/melee
unification's milestone 5 (tarmar-studio#240).

A :class:`Spell` is the frozen data a cast reads -- its IQ tier (which gates
whether a wizard may know it), its ST cost, and the type-specific numbers a
missile, protection, buff, or debuff spell needs. The *resolution* of a cast
(the to-hit roll, the fizzle table, the ST drain) lives in
:mod:`.ruleset` / :mod:`.combat`, exactly as a weapon's data lives
here while its attack is resolved by the ruleset; this module is pure data,
mutating nothing.

Every number below is transcribed from the searchable reference booklet
``docs/reference/the-fantasy-trip-wizard-spell-reference.txt`` in the melee
repository (the numeric Spell
Table) and, for the missile cap and casting mechanics,
``docs/reference/the-fantasy-trip-wizard-rules.txt``. The exact source line is
cited on each field so a value is auditable against the rulebook (#229/#270: no
fabricated numbers).

**Durations** (#431). The rulebook gives every non-instant spell one of two
duration models (rules lines 229-232):

* a **stated duration** -- "lasts a stated number of turns after casting. The
  turn such a spell is cast is always counted as the first turn." These carry
  :attr:`Spell.duration` and expire by turn count
  (:meth:`.state.GameState.end_turn`).
* a **continuing** spell -- "cost ST each turn after being cast until the
  wizard turns them off" (rules lines 166-167), renewed at the turn's Renew
  stage or it "ends immediately, before movement" (rules lines 229-231), and
  renewable only by its caster, so it also ends when "the wizard dies or goes
  unconscious" (rules lines 229-231, 803). The Renew stage (and its per-turn ST
  charge) is still DEFERRED: until it lands, a continuing spell is treated as
  renewed at no cost each turn -- the caster-felled bound is the expiry this
  batch implements for them.

**Refresh, not stack** (#419, rules lines 683-684): "Only one Blur, one Stone
Flesh, one Shock Shield, etc., can be cast on any given figure at a time. These
spells are not cumulative." A recast REPLACES the running casting -- except the
Slow/Speed pair, whose reference text overrides the duration half: "Slow spells
do not multiply, but do add... they keep him at half speed twice as long"
(spell-ref lines 22-24, 82-84) -- :attr:`Spell.durations_add`.
"""
from __future__ import annotations

from dataclasses import dataclass

# Spell type codes, as printed in the Spell Table (the "(M)"/"(T)"/"(C)"/"(S)"
# tag after each spell's name). Missile spells fly in a line and roll damage per
# ST; Thrown spells act on a figure/object; Creation spells summon; Special
# spells are setup/utility.
MISSILE = "M"
THROWN = "T"
CREATION = "C"
SPECIAL = "S"


@dataclass(frozen=True)
class Spell:
    """One castable spell's rules data (frozen; shared like a catalog weapon).

    Attributes:
        id: Stable machine identifier (``"magic_fist"``), used in ``spells_known``
            and ``active_spells`` and never shown to a player.
        name: Display name as printed in the Spell Table ("Magic Fist").
        type: One of :data:`MISSILE`, :data:`THROWN`, :data:`CREATION`,
            :data:`SPECIAL` -- the "(M)/(T)/(C)/(S)" tag.
        iq_tier: Minimum IQ to learn it (the IQ heading it sits under in the
            table). A wizard may know it only when ``intelligence >= iq_tier``.
        st_cost: ST paid to cast it (the minimum for a variable-ST spell).
        max_st: For a variable-ST spell, the catalog ceiling on ST invested in
            one cast (rules line 620: "maximum 3" for every missile spell); 0
            means no catalog ceiling (a variable-ST thrown spell like
            Clumsiness is bounded only by the caster's own ST pool).
        variable_st: True when the caster chooses the ST invested (all three
            missile spells, 1..3; Clumsiness, -2 DX per ST). False = the flat
            :attr:`st_cost` (or its heavy-target variant) exactly.
        damage_per_st: For a missile spell, the per-ST damage-die modifier
            (Magic Fist 1d-2 -> ``-2``; Fireball 1d-1 -> ``-1``; Lightning 1d
            -> ``0``); 0 otherwise. The "never less damage than the ST used"
            floor is applied by :func:`.combat.roll_missile_spell_damage`.
        stops: For a protection spell, hits stopped per attack (folded into
            ``Ruleset.absorbed``); 0 otherwise.
        continuing: True if the spell persists and must be re-energized each turn
            (the Renew stage -- still deferred; see the module docstring). Its
            per-turn upkeep is :attr:`renew_cost`. A continuing spell has no
            :attr:`duration`; it ends when its caster is felled.
        renew_cost: ST paid each turn a continuing spell is maintained (0 for a
            fire-and-forget spell). Recorded now; the Renew turn-stage that spends
            it is still deferred.
        trips_at: For a missile spell that also trips, the PRE-armour damage at
            which the target must save (3 dice vs ST or DX, whichever is higher)
            or fall down — Magic Fist trips at 6 (spell-ref lines 18-21, #421);
            0 for a spell with no trip effect.
        duration: Stated duration in turns ("Lasts 3 turns"); 0 for an instant
            effect or a continuing spell. Expiry ticks at end of turn, the cast
            turn counted as the first (rules lines 231-232).
        duration_heavy: Duration against a heavy target -- a figure whose BASIC
            ST is :attr:`heavy_st` or more (Clumsiness: "1 turn if victim's ST
            is 30 or more", spell-ref line 39). 0 = no heavy variant.
        st_cost_heavy: Flat cost against a heavy target (Drop Weapon: "2 ST if
            victim's basic ST is 20 or more"; Trip: "4 ST if target has 30 ST
            or over"). 0 = no heavy variant.
        heavy_st: The basic-ST threshold the heavy cost/duration variants key
            on; 0 = the spell has no heavy variant.
        durations_add: A recast ADDS its duration to the running casting instead
            of replacing it (Slow/Speed Movement: "Slow spells do not multiply,
            but do add", spell-ref lines 22-24, 82-84).
        targets_self: A buff/protection cast on the caster itself this batch
            (allies deferred, matching Stone Flesh's existing gate).
        exclusive_with: Spell ids this one cannot coexist with on a figure --
            landing it removes them (Stone Flesh is "not with Iron Flesh",
            spell-ref lines 204-206).
        dx_penalty_per_st: DX penalty on the SUBJECT per ST invested, negative
            (Clumsiness: "-2 for every ST in the spell", spell-ref lines 38-39,
            DX table lines 353-354); 0 otherwise.
        defense_dx_penalty: DX penalty on attacks/casts AGAINST the subject,
            negative (Blur: "Subtracts 4 from DX of all attacks/spells against
            subject" -> ``-4``); 0 otherwise.
        ma_percent: The subject's MA scaling while active: 100 = unchanged,
            50 = halved (Slow Movement), 200 = doubled (Speed Movement),
            0 = "a MA of zero" (Stop).
        drops_weapon: Instant effect -- the victim drops what one hand holds
            (Drop Weapon).
        breaks_weapon: Instant effect -- the victim's held weapon shatters
            (Break Weapon).
        knocks_down: Instant effect -- the victim falls down (Trip).
    """

    id: str
    name: str
    type: str
    iq_tier: int
    st_cost: int
    max_st: int = 0
    variable_st: bool = False
    damage_per_st: int = 0
    stops: int = 0
    continuing: bool = False
    renew_cost: int = 0
    trips_at: int = 0
    duration: int = 0
    duration_heavy: int = 0
    st_cost_heavy: int = 0
    heavy_st: int = 0
    durations_add: bool = False
    targets_self: bool = False
    exclusive_with: tuple[str, ...] = ()
    dx_penalty_per_st: int = 0
    defense_dx_penalty: int = 0
    ma_percent: int = 100
    drops_weapon: bool = False
    breaks_weapon: bool = False
    knocks_down: bool = False

    @property
    def is_missile(self) -> bool:
        """A Missile spell (Magic Fist/Fireball/Lightning) -- flies in a line and
        rolls damage per ST (rules p.12)."""
        return self.type == MISSILE

    @property
    def is_protection(self) -> bool:
        """A hit-stopping protection spell (Stone Flesh/Iron Flesh) -- its
        :attr:`stops` fold into the target's armour via ``Ruleset.absorbed``."""
        return self.stops > 0

    @property
    def has_lasting_effect(self) -> bool:
        """True when a landed cast is recorded in the subject's ``active_spells``
        (a stated duration or a continuing spell); False for an instant effect
        (Drop/Break Weapon, Trip) and for missile damage."""
        return self.continuing or self.duration > 0


def spell_cost_for(spell: Spell, target_basic_st: int) -> int:
    """The flat ST cost of ``spell`` against a target of ``target_basic_st``.

    Applies the heavy-target cost variant (Drop Weapon "2 ST if victim's basic
    ST is 20 or more", spell-ref lines 12-13; Trip "4 ST if target has 30 ST or
    over", lines 90-91). The threshold reads BASIC ST (the printed attribute),
    not current ST — the reference says "basic ST".
    """
    if spell.st_cost_heavy and spell.heavy_st and target_basic_st >= spell.heavy_st:
        return spell.st_cost_heavy
    return spell.st_cost


def spell_duration_for(spell: Spell, target_basic_st: int) -> int:
    """The stated duration of ``spell`` against a target of ``target_basic_st``.

    Applies the heavy-target duration variant (Clumsiness "Lasts 3 turns (1
    turn if victim's ST is 30 or more)", spell-ref lines 38-39). 0 for an
    instant or continuing spell.
    """
    if spell.duration_heavy and spell.heavy_st and target_basic_st >= spell.heavy_st:
        return spell.duration_heavy
    return spell.duration


# --- Magic Fist -----------------------------------------------------------
# Spell-reference line 7 ("IQ 8 SPELLS" heading) -> iq_tier 8.
# Spell-reference line 16: "Magic Fist (M): A telekinetic blow. Does 1d-2 damage
#   for every ST point used to cast it but never less damage than the ST used."
#   -> type MISSILE ("(M)"), damage_per_st -2 (1d-2), the "never less than the ST
#   used" floor is applied by the damage roll in the ruleset.
# Rules line 620: "the amount of ST (maximum 3) he is using for the spell."
#   -> max_st 3. st_cost is the 1-ST minimum a cast must spend.
# Spell-reference lines 18-21: "A Magic Fist that does 6 or more hits before
#   armor/shield protection will also trip its target, making him/her fall down,
#   unless he/she makes a 3-die roll on ST or DX, whichever is higher."
#   -> trips_at 6 (the save itself is rolled in GameState._magic_fist_trip, #421).
MAGIC_FIST = Spell(
    id="magic_fist",
    name="Magic Fist",
    type=MISSILE,
    iq_tier=8,
    st_cost=1,
    max_st=3,
    variable_st=True,
    damage_per_st=-2,
    trips_at=6,
)

# --- Blur -------------------------------------------------------------------
# Spell-reference line 7 ("IQ 8 SPELLS" heading) -> iq_tier 8.
# Spell-reference lines 8-10: "Blur (T): Defensive spell. Makes subject harder
#   to see/hear/smell. Subtracts 4 from DX of all attacks/spells against
#   subject. Costs 1 ST to cast, and 1 more ST each turn thereafter until
#   turned off."
#   -> type THROWN ("(T)"), defense_dx_penalty -4 (read by
#   GameState._situational_mods for weapon attacks and by queue_spell for casts
#   — the DX table lists "Target is Blurred -4" under adjustments "FOR EITHER
#   CASTING OF SPELLS OR PHYSICAL ATTACKS", spell-ref lines 318-323),
#   st_cost 1, continuing True, renew_cost 1 (the Renew stage is deferred; see
#   the module docstring). Self-cast this batch ("A wizard casting a thrown
#   spell on himself (Blur, for instance)...", rules lines 670-671).
BLUR = Spell(
    id="blur",
    name="Blur",
    type=THROWN,
    iq_tier=8,
    st_cost=1,
    continuing=True,
    renew_cost=1,
    targets_self=True,
    defense_dx_penalty=-4,
)

# --- Drop Weapon ------------------------------------------------------------
# Spell-reference line 7 ("IQ 8 SPELLS" heading) -> iq_tier 8.
# Spell-reference lines 11-13: "Drop Weapon (T): Makes victim drop whatever is
#   in one hand – a weapon, shield, or whatever. Will not make a ring or amulet
#   fall off. Costs 1 ST, or 2 ST if victim's basic ST is 20 or more."
#   -> type THROWN ("(T)"), drops_weapon True (the ready weapon falls to the
#   ground exactly as a 17-fumble drop; with no weapon in hand a ready shield
#   is shed instead, the engine's one shield-shedding model — see
#   GameState._apply_drop_weapon), st_cost 1, st_cost_heavy 2 at heavy_st 20.
#   Instant effect: no duration, nothing recorded in active_spells.
DROP_WEAPON = Spell(
    id="drop_weapon",
    name="Drop Weapon",
    type=THROWN,
    iq_tier=8,
    st_cost=1,
    st_cost_heavy=2,
    heavy_st=20,
    drops_weapon=True,
)

# --- Slow Movement ----------------------------------------------------------
# Spell-reference line 7 ("IQ 8 SPELLS" heading) -> iq_tier 8.
# Spell-reference lines 22-24: "Slow Movement (T): Halves victim's MA for 4
#   turns. Slow spells do not multiply, but do add. Two Slow spells do not
#   reduce a victim to quarter speed; they keep him at half speed twice as
#   long. Cost: 2 ST."
#   -> type THROWN ("(T)"), ma_percent 50, duration 4, durations_add True (a
#   recast extends the running casting instead of replacing it), st_cost 2.
SLOW_MOVEMENT = Spell(
    id="slow_movement",
    name="Slow Movement",
    type=THROWN,
    iq_tier=8,
    st_cost=2,
    duration=4,
    durations_add=True,
    ma_percent=50,
)

# --- Staff ------------------------------------------------------------------
# Spell-reference line 7 ("IQ 8 SPELLS" heading) -> iq_tier 8.
# Spell-reference lines 25-26: "Staff (S): This spell is used to make any piece
#   of wood into a staff (see The Wizard's Staff). This spell is rarely used
#   during a game, because any wizard who knows it can start the game with a
#   staff. If used during a game, its ST cost is 5."
#   -> type SPECIAL ("(S)"), st_cost 5 (the in-game cost only).
# Rules lines 940-942: "If he knows the Staff spell, he starts the game with a
#   staff, without expending any ST to create it." The start-of-game grant is
#   the mechanic the engine implements (.figure.create_wizard equips the
#   staff weapon when this spell is known); the rare in-game re-creation of a
#   broken staff is not modelled — a wizard whose staff breaks does without.
STAFF_SPELL = Spell(
    id="staff",
    name="Staff",
    type=SPECIAL,
    iq_tier=8,
    st_cost=5,
)

# --- Clumsiness ---------------------------------------------------------------
# Spell-reference line 29 ("IQ 9 SPELLS" heading) -> iq_tier 9.
# Spell-reference lines 38-39: "Clumsiness (T): Subtracts 2 from victim's DX for
#   every 1 ST the wizard uses to throw spell. Lasts 3 turns (1 turn if victim's
#   ST is 30 or more)."
#   The DX Adjustment Table repeats it (spell-ref lines 353-354): "You've been
#   hit by a Clumsiness spell: -2 for every ST in the spell."
#   -> type THROWN ("(T)"), dx_penalty_per_st -2, duration 3, duration_heavy 1
#   at heavy_st 30 (basic ST). variable_st True with NO catalog ceiling (the
#   reference states none; max_st 0 leaves the caster's own ST pool as the only
#   bound), st_cost 1 the minimum meaningful investment.
CLUMSINESS = Spell(
    id="clumsiness",
    name="Clumsiness",
    type=THROWN,
    iq_tier=9,
    st_cost=1,
    variable_st=True,
    duration=3,
    duration_heavy=1,
    heavy_st=30,
    dx_penalty_per_st=-2,
)

# --- Speed Movement -----------------------------------------------------------
# Spell-reference line 71 ("IQ 10 SPELLS" heading) -> iq_tier 10.
# Spell-reference lines 82-84: "Speed Movement (T): Doubles MA of target figure
#   for 4 turns. Speed spells do not multiply, but do add. Two Speed spells do
#   not quadruple the subject's speed; they double it for twice as long.
#   Cost: 2 ST."
#   -> type THROWN ("(T)"), ma_percent 200, duration 4, durations_add True,
#   st_cost 2. Self-cast this batch (ally-targeting deferred, matching the
#   Stone Flesh gate).
SPEED_MOVEMENT = Spell(
    id="speed_movement",
    name="Speed Movement",
    type=THROWN,
    iq_tier=10,
    st_cost=2,
    duration=4,
    durations_add=True,
    targets_self=True,
    ma_percent=200,
)

# --- Trip ---------------------------------------------------------------------
# Spell-reference line 71 ("IQ 10 SPELLS" heading) -> iq_tier 10.
# Spell-reference lines 88-91: "Trip (T): Knocks victim down. Does no damage –
#   but if victim is on the edge of a chasm, pit, river, etc., he must make a
#   4-die saving roll against adjDX to avoid falling in. The Trip spell costs
#   2 ST, or 4 ST if target has 30 ST or over."
#   -> type THROWN ("(T)"), knocks_down True (the victim falls, no save — the
#   4-die adjDX save applies only at a chasm/pit/river edge, and this arena has
#   none), st_cost 2, st_cost_heavy 4 at heavy_st 30. Instant effect.
TRIP = Spell(
    id="trip",
    name="Trip",
    type=THROWN,
    iq_tier=10,
    st_cost=2,
    st_cost_heavy=4,
    heavy_st=30,
    knocks_down=True,
)

# --- Break Weapon -------------------------------------------------------------
# Spell-reference line 144 ("IQ 12 SPELLS" heading) -> iq_tier 12.
# Spell-reference lines 153-155: "Break Weapon (T): Shatters one weapon, shield,
#   staff, etc., in target's hand. Does not work on enchanted swords, shields,
#   and so on... Broken weapons do half damage (round down); broken staffs are
#   useless. Cost: 3 ST."
#   -> type THROWN ("(T)"), breaks_weapon True, st_cost 3. The engine has ONE
#   broken-weapon model — the 18-fumble removes the weapon outright
#   (state._apply: "broken is gone") — and this spell maps onto that same seam;
#   the reference's half-damage broken state is not modelled for the fumble
#   either, so the two break paths stay consistent. No enchanted weapons exist
#   in this engine, so the enchanted-gear exemption never arises. Instant.
BREAK_WEAPON = Spell(
    id="break_weapon",
    name="Break Weapon",
    type=THROWN,
    iq_tier=12,
    st_cost=3,
    breaks_weapon=True,
)

# --- Fireball -----------------------------------------------------------------
# Spell-reference line 144 ("IQ 12 SPELLS" heading) -> iq_tier 12.
# Spell-reference lines 156-157: "Fireball (M): Does 1d-1 damage for every ST
#   point the wizard puts into it, but never less damage than the ST used. Can
#   set fire to flammable objects."
#   -> type MISSILE ("(M)"), damage_per_st -1 (1d-1). Rules line 620: every
#   missile spell invests "the amount of ST (maximum 3)" -> max_st 3, st_cost 1
#   the minimum. The damage floor is the shared roll_missile_spell_damage rule
#   (rules lines 660-661). No flammable objects exist in this arena.
FIREBALL = Spell(
    id="fireball",
    name="Fireball",
    type=MISSILE,
    iq_tier=12,
    st_cost=1,
    max_st=3,
    variable_st=True,
    damage_per_st=-1,
)

# --- Stop ---------------------------------------------------------------------
# Spell-reference line 172 ("IQ 13 SPELLS" heading) -> iq_tier 13.
# Spell-reference lines 209-211: "Stop (T): The victim of this spell has a MA of
#   zero for the next four turns. He or she may do anything else, but may not
#   move to another hex under any circumstances. Cost: 3 ST."
#   -> type THROWN ("(T)"), ma_percent 0, duration 4, st_cost 3. The victim may
#   still attack/defend/cast in place (MA 0 zeroes every movement budget but
#   gates no option).
STOP = Spell(
    id="stop",
    name="Stop",
    type=THROWN,
    iq_tier=13,
    st_cost=3,
    duration=4,
    ma_percent=0,
)

# --- Stone Flesh ----------------------------------------------------------
# Spell-reference: Stone Flesh sits at line 204, between the "IQ 13 SPELLS"
#   heading (line 172) and "IQ 14 SPELLS" (line 217) -> iq_tier 13.
# Spell-reference line 204-208: "Stone Flesh (T): Gives subject's body the power
#   to act as armor, stopping 4 hits per attack. The protective effect of Stone
#   Flesh is cumulative with any other natural or magical hit-stopping ability
#   (armor, fur, etc.) of its possessor, but not with Iron Flesh. ... Costs 2 ST
#   to cast, plus 1 each turn the spell continues."
#   -> type THROWN ("(T)"), stops 4, st_cost 2, continuing True, renew_cost 1,
#   exclusive_with iron_flesh ("but not with Iron Flesh"). As a continuing
#   spell it ends when its caster is felled (rules lines 229-231; the Renew
#   stage and its 1-ST/turn charge stay deferred — module docstring).
STONE_FLESH = Spell(
    id="stone_flesh",
    name="Stone Flesh",
    type=THROWN,
    iq_tier=13,
    st_cost=2,
    stops=4,
    continuing=True,
    renew_cost=1,
    targets_self=True,
    exclusive_with=("iron_flesh",),
)

# --- Lightning ----------------------------------------------------------------
# Spell-reference line 217 ("IQ 14 SPELLS" heading) -> iq_tier 14.
# Spell-reference lines 221-224: "Lightning (M): Does 1 die damage for each ST
#   point the wizard puts into it. Can also be used to blast through solid
#   objects – for instance, a created Wall hex will vanish after taking 5 hits
#   from lightning..."
#   -> type MISSILE ("(M)"), damage_per_st 0 (a full 1d per ST; rules lines
#   656-658: "subtract... nothing if the spell was Lightning"). Rules line 620
#   -> max_st 3, st_cost 1 minimum. The shared damage floor (rules lines
#   660-661) never binds at 1d/ST. No created walls exist to blast through.
LIGHTNING = Spell(
    id="lightning",
    name="Lightning",
    type=MISSILE,
    iq_tier=14,
    st_cost=1,
    max_st=3,
    variable_st=True,
    damage_per_st=0,
)

# --- Iron Flesh -----------------------------------------------------------------
# Spell-reference line 244 ("IQ 15 SPELLS" heading) -> iq_tier 15.
# Spell-reference lines 255-256: "Iron Flesh (T): Similar to Stone Flesh, but
#   better: lets subject's body stop 6 hits per attack. Costs 3 ST, plus 1 per
#   turn maintained."
#   -> type THROWN ("(T)"), stops 6, st_cost 3, continuing True, renew_cost 1,
#   exclusive_with stone_flesh (Stone Flesh is cumulative with other protection
#   "but not with Iron Flesh", spell-ref lines 204-206 — the exclusion is
#   mutual). Self-cast this batch, like Stone Flesh.
IRON_FLESH = Spell(
    id="iron_flesh",
    name="Iron Flesh",
    type=THROWN,
    iq_tier=15,
    st_cost=3,
    stops=6,
    continuing=True,
    renew_cost=1,
    targets_self=True,
    exclusive_with=("stone_flesh",),
)


# The shipped catalog, keyed by id -- the single source both chargen's catalog
# and the resolve path read (a wizard's ``spells_known`` holds ids). Ordered by
# IQ tier, as the Spell Table prints them.
SPELLS: dict[str, Spell] = {
    spell.id: spell for spell in (
        MAGIC_FIST, BLUR, DROP_WEAPON, SLOW_MOVEMENT, STAFF_SPELL,
        CLUMSINESS,
        SPEED_MOVEMENT, TRIP,
        BREAK_WEAPON, FIREBALL,
        STOP, STONE_FLESH,
        LIGHTNING,
        IRON_FLESH,
    )
}


def spell_by_id(spell_id: str) -> Spell:
    """The :class:`Spell` for ``spell_id``; raises ``ValueError`` if unknown.

    An unknown id is bad input (a player picked a spell that does not exist), so
    it raises a domain ``ValueError`` rather than a bare ``KeyError`` -- matching
    melee's ``engine.chargen._from_catalog``.
    """
    try:
        return SPELLS[spell_id]
    except KeyError:
        raise ValueError(f"unknown spell {spell_id!r}") from None
