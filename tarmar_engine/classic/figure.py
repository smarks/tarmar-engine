"""A classic combat figure (Section III) — SJG-derived, segregated.

Ported from melee's ``engine/figure.py``. A figure is created with Strength
(ST) and Dexterity (DX), then equipped, and carries the running state of a
fight. The spell computations read the pluggable
:attr:`Figure.SPELL_CATALOG`, bound by default to the classic spell catalog
(:data:`.spells.SPELLS`) since the battle/melee unification's milestone 5
brought the spell layer into this subpackage; a consumer may still rebind it.

A figure is created with Strength (ST) and Dexterity (DX), then equipped with
armor, an optional shield, and up to two weapons plus a dagger. ST governs how
many hits it can take and which weapons it can wield; DX governs how likely it is
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from hexarena.hex import Hex, HexLayout

from .data import (
    CLOTH,
    HUMAN_MIN_ATTRIBUTE,
    HUMAN_START_TOTAL,
    KNOCKDOWN_HITS,
    LEATHER,
    LOW_ST_DX_PENALTY,
    LOW_ST_THRESHOLD,
    NO_ARMOR,
    NO_SHIELD,
    STAFF,
    WOUND_DX_PENALTY,
    WOUND_HITS_THRESHOLD,
    Armor,
    Shield,
    Weapon,
)
from .spells import SPELLS


def footprint_for(
    layout: HexLayout, anchor: Hex, facing: int, size: int
) -> list[Hex]:
    """The hexes a figure of ``size`` occupies, anchored at ``anchor``.

    A ``size`` of 1 (the default for every normal figure) is just ``[anchor]``,
    so single-hex behaviour is unchanged. A giant (``size`` 3) holds a triangle
    of three mutually adjacent hexes -- its anchor plus the two hexes forward of
    it (in the ``facing`` and ``facing + 1`` directions). Those two forward hexes
    are each adjacent to the anchor and to each other, so the cluster is a solid
    tri-hex whose forward edge is the giant's front.
    """
    if size <= 1:
        return [anchor]
    return [
        anchor,
        layout.neighbor(anchor, facing % 6),
        layout.neighbor(anchor, (facing + 1) % 6),
    ]


class Posture(StrEnum):
    STANDING = "standing"
    KNEELING = "kneeling"
    PRONE = "prone"


class Race(StrEnum):
    HUMAN = "human"
    ELF = "elf"
    DWARF = "dwarf"
    HALFLING = "halfling"
    ORC = "orc"
    GOBLIN = "goblin"
    HOBGOBLIN = "hobgoblin"


@dataclass(frozen=True)
class RaceSpread:
    """A race's starting ST/DX limits (Section VIII, p.21).

    A fresh figure of the race must keep ST and DX at or above the listed
    minimums and spend exactly ``total`` points across the two.
    """

    min_strength: int
    min_dexterity: int
    total: int


# Section VIII "Fantasy Fighters" (p.21). Humans are the Section III baseline
# (min 8/8, 24 points); each race shifts the floors and the point total.
RACE_SPREADS: dict[Race, RaceSpread] = {
    Race.HUMAN: RaceSpread(HUMAN_MIN_ATTRIBUTE, HUMAN_MIN_ATTRIBUTE, HUMAN_START_TOTAL),
    Race.ORC: RaceSpread(8, 8, 24),       # "just like a human figure"
    Race.ELF: RaceSpread(6, 10, 24),      # min ST 6, min DX 10, total 24
    Race.DWARF: RaceSpread(10, 6, 24),    # min ST 10, min DX 6, total 24
    # min ST 4, min DX 12, only 6 added -> total 22
    Race.HALFLING: RaceSpread(4, 12, 22),
    Race.GOBLIN: RaceSpread(6, 8, 22),    # min ST 6, min DX 8, total 22
    Race.HOBGOBLIN: RaceSpread(7, 6, 20), # min ST 7, min DX 6, total 20
}


@dataclass
class Figure:
    """One counter in the arena.

    Construction validates the attribute spread and weapon strength
    requirements; raises ``ValueError`` on an illegal figure.
    """

    #: The spell-layer hook: a mapping of spell id -> catalog entry with
    #: ``ma_percent`` / ``dx_penalty_per_st`` / ``defense_dx_penalty`` fields.
    #: Bound below to the classic catalog (:data:`.spells.SPELLS`) — the
    #: package casts natively as of unification milestone 5; a consumer may
    #: rebind it (``None`` turns every spell computation inert).
    SPELL_CATALOG: ClassVar[Mapping[str, object] | None] = None

    name: str
    strength: int
    dexterity: int
    side: str
    armor: Armor = NO_ARMOR
    shield: Shield = NO_SHIELD
    weapons: list[Weapon] = field(default_factory=list)
    ready_weapon: Weapon | None = None
    shield_ready: bool = True
    race: Race = Race.HUMAN
    # The fighter's archetype/class (Knight, Swordsman, …). It drives the loadout
    # at creation and then survives as a secondary label; the fun ``name`` is the
    # identity. Empty for a figure with no archetype (e.g. a monster).
    char_class: str = ""
    # ---- wizard identity (Classic magic; TFT: Wizard) ----
    # A fighter is IQ 8 (the Melee baseline, p.23) with no spells; a wizard is the
    # SAME Figure class with a raised IQ, a non-empty spell list, and optionally a
    # staff. These are identity fields — chosen at chargen, carried over a rebuild
    # and round-tripped by the editor (CARRY_OVER_STATE).
    intelligence: int = 8       # IQ: gates how many spells and which tiers it knows
    # spell ids (the consumer's spell catalog)
    spells_known: list[str] = field(default_factory=list)
    has_staff: bool = False     # started with a wizard's staff (the Staff spell, p.19)
    # ---- nonhuman quirks (Section VIII) ----
    all_front: bool = False    # every facing is "front" (giant snake: no flank/rear)
    hard_to_hit: int = 0       # DX penalty it imposes on attackers (snake: 3)
    # ---- size / footprint (multi-hex figures: the giant, p.20) ----
    size: int = 1              # hexes occupied; 1 = normal, 3 = giant tri-hex
    needs_two_to_engage: bool = False  # giant: engaged only by two foes in its front
    # ---- flight (gargoyle, p.21) ----
    fly_movement_allowance: int = 0    # MA when airborne; 0 = cannot fly
    flying: bool = False               # currently airborne (lands to attack)
    # ---- per-figure injury thresholds (the giant scales these, p.20) ----
    wound_hits_threshold: int = WOUND_HITS_THRESHOLD      # hits/turn for -2 DX
    knockdown_hits_threshold: int = KNOCKDOWN_HITS        # hits/turn to fall

    # ---- mutable fight state ----
    position: Hex | None = None
    facing: int = 0                  # direction index 0-5 (see .facing)
    posture: Posture = Posture.STANDING
    damage_taken: int = 0            # total hits scored against ST
    hits_this_turn: int = 0          # hits taken so far this turn
    wounded_last_turn: bool = False  # took 5+ hits last turn -> -2 DX this turn
    attacked_this_turn: bool = False
    disengaged_this_turn: bool = False  # took option (n) disengage this turn (p.19)
    knocked_down_this_turn: bool = False  # knocked prone by damage this turn (p.20)
    moved_this_turn: int = 0         # hexes moved this turn (for half-MA limit)
    # this turn's move ran in a straight line (pole charge)
    moved_straight: bool = False
    # chose DODGE (4 dice to hit it with a missile/thrown)
    dodging: bool = False
    defending: bool = False          # chose SHIFT_DEFEND (4 dice to hit it in melee)
    unconscious: bool = False
    dead: bool = False
    # left a practice bout at ST <= 3 (p.22): out, alive
    dropped_out: bool = False
    uid: str = ""                    # stable id for UI / occupancy
    current_option: object | None = None  # the Option chosen this turn
    dealt_st_damage_this_turn: bool = False  # landed a qualifying melee hit this turn
    # uids of enemies this figure dealt qualifying (melee, non-thrown, non-missile)
    # damage to this turn and may STILL force to retreat -- each is a single push
    # (p.20: "force the enemy to retreat one hex at the end of the turn"), removed
    # once spent so no unbounded chain, and per-target so only a foe actually
    # struck can be pushed (never a teammate or an untouched enemy).
    force_retreat_targets_this_turn: list[str] = field(default_factory=list)
    missile_cooldown: int = 0        # turns until a fired missile weapon reloads
    hth_opponents: list[str] = field(default_factory=list)  # uids grappled (HTH)
    hth_drew_dagger: bool = False    # readied a dagger mid-grapple (usable next turn)

    # ---- wizard per-fight state (Classic magic) ----
    # ``active_spells`` maps the id of each lasting spell in effect ON THIS FIGURE
    # to its record: ``{"st": ST invested (drives Clumsiness's -2/ST magnitude and
    # the deferred Renew stage's upkeep), "remaining": turns left for a
    # stated-duration spell / None for a continuing one, "caster": uid of the
    # wizard who cast it (a continuing spell ends when its caster is felled,
    # wizard-rules lines 229-231)}``. ``spell_protection`` is the running
    # hit-stopping from protection spells (Stone/Iron Flesh), read by
    # ``Ruleset.absorbed`` and kept equal to the active protection spells' stops
    # by ``Ruleset.sync_spell_protection`` (invariant-checked, #431). Both persist
    # for the fight (CARRY_OVER_STATE).
    active_spells: dict[str, dict] = field(default_factory=dict)
    spell_protection: int = 0
    cast_this_turn: bool = False     # cast a new spell this turn (one/turn, p.11)
    # The cast DECLARED in the selection phase, honoured in the combat phase
    # (tarmar-engine#5). melee's own driver re-derived the AI's spell when the
    # combat phase came round, which silently overruled any chooser but the AI;
    # a declaration recorded here is what actually gets queued. Empty means "no
    # cast declared" — the AI's own re-derivation, unchanged.
    declared_spell_id: str = ""
    declared_spell_target: str = ""

    # ---- experience / advancement (Section IX) ----
    # XP earned across fights, and how many basic ST/DX points it has bought. The
    # bought points are already folded into ``strength`` / ``dexterity``; these
    # counters track the 8-point lifetime cap and let progression persist (#10).
    experience: int = 0
    added_st: int = 0
    added_dx: int = 0

    def __post_init__(self) -> None:
        if self.strength < 1 or self.dexterity < 1:
            raise ValueError("ST and DX must be positive")
        for weapon in self.weapons:
            if weapon.min_strength and self.strength < weapon.min_strength:
                raise ValueError(
                    f"{self.name} (ST {self.strength}) cannot wield "
                    f"{weapon.name} (needs ST {weapon.min_strength})"
                )
        if self.ready_weapon is not None and self.ready_weapon not in self.weapons:
            self.weapons.append(self.ready_weapon)
        # A two-handed ready weapon leaves no hand for a shield (Section III).
        if self.ready_weapon is not None and self.ready_weapon.two_handed:
            self.shield_ready = False

    # ---- derived combat numbers ----
    @property
    def current_st(self) -> int:
        """ST remaining after accumulated hits."""
        return self.strength - self.damage_taken

    @property
    def collapsed(self) -> bool:
        """Out of the fight but not dead: ST 0 or below — unconscious (p.3) — or
        dropped out of a practice bout at ST <= 3 (p.22). Either way the figure
        can no longer fight; :attr:`is_dead` distinguishes a true kill."""
        return self.current_st <= 0 or self.dropped_out

    @property
    def is_dead(self) -> bool:
        """ST -1 or below: dead (p.3)."""
        return self.current_st <= -1

    @property
    def out_of_play(self) -> bool:
        """Dead or collapsed — the figure can no longer be a legal attack target.

        The single definition of the "#310: don't strike a downed/dead target"
        predicate. Written as ``is_dead or collapsed`` because the two are NOT
        redundant under every stat model: the Tarmar profile re-keys them onto
        separate Fatigue/Body pools (a Tarmar-shaped consumer profile), so a
        figure can be
        dead (Body exhausted) without being collapsed (Fatigue remaining). Both
        subclasses inherit this by reading their own overridden properties.
        """
        return self.is_dead or self.collapsed

    @property
    def movement_allowance(self) -> int:
        """Hexes per turn; set by armor (shields don't change MA).

        A figure that is airborne moves at its flying allowance instead (the
        gargoyle: MA 8 on the ground, 16 in the air, p.21).

        An ELF is fleeter in light armor (p.21): its MA is 12 in cloth or no
        armor and 10 in leather (a flat +2 over the man's 10/10/8). In any
        heavier armor an elf "moves the same as a man".
        """
        if self.flying and self.fly_movement_allowance:
            base = self.fly_movement_allowance
        else:
            base = self.armor.movement_allowance
            if self.race == Race.ELF and self.armor in (NO_ARMOR, CLOTH, LEATHER):
                base += 2
        return self._spell_scaled_ma(base)

    def _spell_scaled_ma(self, base: int) -> int:
        """``base`` MA scaled by active movement spells (TFT: Wizard, #431).

        Slow Movement halves the subject's MA (spell-ref lines 22-24), Speed
        Movement doubles it (lines 82-84), Stop zeroes it (lines 209-211) —
        each spell's ``ma_percent`` applied in turn. Same-spell recasts never
        stack (a recast only extends the duration, #419/spell-ref lines 22-24),
        so at most one of each factor is ever in play.
        """
        if not self.active_spells or self.SPELL_CATALOG is None:
            return base
        for spell_id in self.active_spells:
            spell = self.SPELL_CATALOG.get(spell_id)
            if spell is not None and spell.ma_percent != 100:
                base = base * spell.ma_percent // 100
        return base

    @property
    def can_fly(self) -> bool:
        """Whether this figure has a flight mode at all (gargoyle)."""
        return self.fly_movement_allowance > 0

    def footprint(self, layout: HexLayout) -> list[Hex]:
        """The hexes this figure currently occupies (``[]`` if off the board).

        Single-hex for a normal figure; a tri-hex cluster for the giant. See
        :func:`footprint_for` for the cluster's shape.
        """
        if self.position is None:
            return []
        return footprint_for(layout, self.position, self.facing, self.size)

    def take_off(self) -> None:
        """Become airborne. A no-op for a figure that cannot fly."""
        if self.can_fly:
            self.flying = True

    def land(self) -> None:
        """Return to the ground (a flyer must land to attack, p.21)."""
        self.flying = False

    @property
    def in_hth(self) -> bool:
        """Locked in hand-to-hand combat (grappling on the ground)."""
        return bool(self.hth_opponents)

    @property
    def base_adj_dx(self) -> int:
        """adjDX from armor and a ready shield only (no situational mods)."""
        adjusted = self.dexterity + self.armor.dx_penalty
        if self.shield_ready:
            adjusted += self.shield.dx_penalty
        return adjusted

    def wound_dx_penalty(self) -> int:
        """Situational DX penalty from injury (Reactions to Injury, p.20).

        -2 if the figure took 5+ hits last turn (one turn only); an additional
        -3, permanent for the rest of the fight, once ST drops to 3 or below.
        """
        penalty = 0
        if self.wounded_last_turn:
            penalty += WOUND_DX_PENALTY
        if self.current_st <= LOW_ST_THRESHOLD:
            penalty += LOW_ST_DX_PENALTY
        return penalty

    def spell_dx_penalty(self) -> int:
        """Situational DX penalty on this figure's OWN rolls from spells on it.

        Clumsiness: "-2 for every ST in the spell" (spell-ref lines 38-39; DX
        Adjustment Table lines 353-354). Cumulative with every other adjustment
        ("All applicable DX adjustments are cumulative", spell-ref line 294),
        so it folds into ``Ruleset.to_hit_number`` beside the wound penalty.
        Returns 0 or a negative number.
        """
        if not self.active_spells or self.SPELL_CATALOG is None:
            return 0
        penalty = 0
        for spell_id, record in self.active_spells.items():
            spell = self.SPELL_CATALOG.get(spell_id)
            if spell is not None and spell.dx_penalty_per_st:
                penalty += spell.dx_penalty_per_st * record.get("st", 0)
        return penalty

    def spell_defense_dx_penalty(self) -> int:
        """DX penalty imposed on anyone attacking or casting AT this figure.

        Blur: "Subtracts 4 from DX of all attacks/spells against subject"
        (spell-ref lines 8-10; DX table lines 322-323 "Target is Blurred -4").
        Returns 0 or a negative number.
        """
        if not self.active_spells or self.SPELL_CATALOG is None:
            return 0
        penalty = 0
        for spell_id in self.active_spells:
            spell = self.SPELL_CATALOG.get(spell_id)
            if spell is not None and spell.defense_dx_penalty:
                penalty += spell.defense_dx_penalty
        return penalty

    def hits_stopped(self, *, from_front: bool, from_rear: bool = False) -> int:
        """Hits absorbed per attack by armor plus a shield.

        A *ready* shield covers the three front hexes; an *unready* (slung)
        shield instead covers the single rear hex (p.12). Either way the shield
        stops the same number of hits; only the protected arc differs.
        """
        stopped = self.armor.stops
        if self.shield_ready:
            if from_front:
                stopped += self.shield.stops
        elif from_rear:
            stopped += self.shield.stops
        return stopped

    def can_act(self) -> bool:
        """A figure that is conscious and not dead may take options."""
        return not self.collapsed and not self.dead

    @property
    def unarmed_wizard(self) -> bool:
        """A wizard with no weapon in hand — the one truly *unarmed* figure (p.9).

        "The only 'unarmed' enemy in this game is a wizard who has no staff"
        (Wizard p.9 / rules line 536): such a figure engages no one, so foes walk
        past it freely (:func:`.facing.is_engaged_by`). Deliberately
        NARROW: only a wizard (non-empty ``spells_known``) with nothing readied
        qualifies. A fumble-disarmed *fighter* still engages (Melee-side
        behaviour, unchanged), and a wizard who readies a real weapon — its
        staff, or a dagger — is armed again.
        """
        return bool(self.spells_known) and self.ready_weapon is None


# The per-turn flags reset at end of turn, listed once (name -> reset default) so
# end_turn, the figure rebuild (board.views._update_figure), and the save/load
# round-trip (board.persistence) share one source and can't drift (#155).
# current_option and wounded_last_turn reset differently and stay explicit.
PER_TURN_FLAGS: dict[str, int | bool | str | list[str]] = {
    "hits_this_turn": 0,
    "attacked_this_turn": False,
    "disengaged_this_turn": False,
    "knocked_down_this_turn": False,
    "moved_this_turn": 0,
    "moved_straight": False,
    "dodging": False,
    "defending": False,
    "dealt_st_damage_this_turn": False,
    "force_retreat_targets_this_turn": [],
    "cast_this_turn": False,
    "declared_spell_id": "",
    "declared_spell_target": "",
}


# The mutable "carry-over" fight state that must survive a figure rebuild: the
# running-fight state a figure keeps when it is edited mid-fight
# (board.views._update_figure) AND when a game is saved and reloaded
# (board.persistence). Named ONCE here, and consumed by both, so the edit path
# preserves exactly what the save/load round-trip does and the two can never
# drift (#359, #369). Every entry is a plain scalar copied verbatim; fields that
# need a value transform or a clamp on rebuild -- damage_taken (clamped to the
# new ST), position/facing/posture, current_option, hth_opponents, and the
# added-point fold-in into strength/dexterity -- are deliberately NOT here and
# stay handled explicitly by each consumer.
CARRY_OVER_STATE: tuple[str, ...] = (
    "wounded_last_turn",
    "unconscious",
    "dead",
    "dropped_out",
    "missile_cooldown",
    "hth_drew_dagger",
    "experience",
    "added_st",
    "added_dx",
    # Wizard identity + per-fight magic state (Classic magic). Identity
    # (intelligence/spells_known/has_staff) is chosen at chargen and survives an
    # edit; per-fight (active_spells/spell_protection) is the running magical
    # state a wizard keeps for the whole fight — a mid-fight rebuild or a
    # save/load must not silently drop a raised IQ or an active protection spell.
    "intelligence",
    "spells_known",
    "has_staff",
    "active_spells",
    "spell_protection",
)

# Nonhuman creature traits (Section VIII), set by the consumer's monster factory
# and defaulting to ordinary single-hex/human behaviour on a plain figure.
# chargen.build never reads these -- there is no spec key for them -- so a figure
# rebuilt from a spec gets the dataclass defaults (size 1, grounded, human injury
# thresholds) unless they are carried over from the old figure. They are creature
# state that persists for the whole fight (a giant stays size 3, a gargoyle keeps
# its flight, a snake keeps all_front/hard_to_hit, each keeps its ST-scaled injury
# thresholds), so the save/load round-trip (board.persistence) and the mid-fight
# edit (board.views._update_figure) preserve the SAME set and cannot drift (#359).
MONSTER_FIELDS: tuple[str, ...] = (
    "size", "needs_two_to_engage", "flying", "fly_movement_allowance",
    "all_front", "hard_to_hit", "wound_hits_threshold", "knockdown_hits_threshold",
)



# Bind the classic spell catalog (unification milestone 5): every Figure's
# active-spell computations read the classic Wizard data by default.
Figure.SPELL_CATALOG = SPELLS


def create_fighter(
    name: str,
    strength: int,
    dexterity: int,
    side: str,
    race: Race = Race.HUMAN,
    validate: bool = True,
    **gear,
) -> Figure:
    """Create a fighter of ``race``, enforcing its ST/DX spread (Sections III, VIII).

    Every race floors ST and DX at its own minimums and spends exactly its own
    point total across the two (the human 24-point / min-8 spread is just one row
    of :data:`RACE_SPREADS`). ``validate=False`` skips the spread check so an admin
    can build a fighter outside the rules (#86).
    """
    spread = RACE_SPREADS[race]
    if validate:
        if strength < spread.min_strength or dexterity < spread.min_dexterity:
            raise ValueError(
                f"a {race.value}'s ST may not begin below {spread.min_strength} "
                f"nor its DX below {spread.min_dexterity} "
                f"(got ST {strength}, DX {dexterity})"
            )
        if strength + dexterity != spread.total:
            raise ValueError(
                f"a fresh {race.value} spends exactly {spread.total} points on ST+DX "
                f"(got {strength + dexterity})"
            )
    return Figure(name=name, strength=strength, dexterity=dexterity,
                  side=side, race=race, **gear)


def create_human(
    name: str,
    strength: int,
    dexterity: int,
    side: str,
    **gear,
) -> Figure:
    """Create a human figure, enforcing the 24-point / min-8 spread (Section III)."""
    return create_fighter(name, strength, dexterity, side, race=Race.HUMAN, **gear)


def create_wizard(
    name: str,
    *,
    strength: int,
    dexterity: int,
    intelligence: int,
    side: str,
    spells_known: list[str] | None = None,
    has_staff: bool = False,
    **gear,
) -> Figure:
    """Create a Classic wizard: the SAME :class:`Figure` class, magically armed.

    A wizard is a figure with a raised IQ (``intelligence``), a chosen spell list,
    and optionally a staff. The 3-attribute wizard spread (ST + DX + IQ = 32, each
    >= 8) and the spell-list legality (size and tiers gated by IQ) are validated in
    the consumer's chargen layer; this constructor just assembles the figure,
    so a caller
    that has already validated (or an admin building outside the rules) is not
    re-checked here beyond the Figure's own ST/DX-positive guard.

    Args:
        strength: The injury AND spell-power pool (ST doubles as mana, p.3-4).
        dexterity: The casting to-hit attribute.
        intelligence: IQ -- gates which spells and how many the wizard may know.
        spells_known: Spell ids (see the consumer's spell catalog); empty means an
            unarmed-of-magic figure, which then behaves exactly like a fighter.
        has_staff: Force-grant a staff without the spell (direct engine callers
            only). Normally derived: knowing the Staff spell ("staff" in
            ``spells_known``) is what grants a staff (p.19), so the editor's
            spell picker is the one way to gain or lose it.
        **gear: Armour/shield/weapons, as for :func:`create_fighter`.
    """
    spells = list(spells_known or [])
    # "If he knows the Staff spell, he starts the game with a staff, without
    # expending any ST to create it" (Wizard p.19, rules lines 940-942): equip
    # the Staff weapon at build, readied by default. The staff is the ONE weapon
    # a wizard may hold and still cast (state.cast_block_reason passes
    # it), so a caller that names no ready weapon starts staff-in-hand — but a
    # wizard may carry (and start with) another weapon readied instead (#411),
    # so an explicit ``ready_weapon`` in ``gear`` is honoured.
    grants_staff = bool(has_staff) or "staff" in spells
    if grants_staff:
        gear = dict(gear)
        weapons = list(gear.get("weapons") or [])
        if STAFF not in weapons:
            weapons.append(STAFF)
        gear["weapons"] = weapons
        if gear.get("ready_weapon") is None:
            gear["ready_weapon"] = STAFF
    return Figure(
        name=name, strength=strength, dexterity=dexterity, side=side,
        race=Race.HUMAN, intelligence=intelligence,
        spells_known=spells, has_staff=grants_staff, **gear,
    )
