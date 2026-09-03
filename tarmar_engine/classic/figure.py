"""A classic combat figure (Section III) — SJG-derived, segregated.

Ported faithfully from melee's ``engine/figure.py`` for the classic profile's
combat slice. A figure is created with Strength (ST) and Dexterity (DX), then
equipped with armor, an optional shield, and weapons. ST governs how many hits
it can take and which weapons it can wield; DX governs how likely it is to
hit. Armor and a ready shield lower the *adjusted* DX (adjDX) used for to-hit
rolls and reduce the movement allowance.

Deliberate trims from the melee original (out of this milestone's scope, per
the unification plan): the wizard/spell machinery (classic magic reconciles
in phase-3 milestone 5 — the spell-penalty hooks below return 0 until then),
experience/advancement, and the nonhuman monster factories. The fields those
systems read stay, defaulted inert, so the ported mechanics keep melee's
exact shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from hexarena.hex import Hex, HexLayout

from .data import (
    HUMAN_MIN_ATTRIBUTE,
    HUMAN_START_TOTAL,
    KNOCKDOWN_HITS,
    LOW_ST_DX_PENALTY,
    LOW_ST_THRESHOLD,
    NO_ARMOR,
    NO_SHIELD,
    WOUND_DX_PENALTY,
    WOUND_HITS_THRESHOLD,
    Armor,
    Shield,
    Weapon,
)


def footprint_for(
    layout: HexLayout, anchor: Hex, facing: int, size: int
) -> list[Hex]:
    """The hexes a figure of ``size`` occupies, anchored at ``anchor``.

    A ``size`` of 1 (the default for every normal figure) is just
    ``[anchor]``. A giant (``size`` 3) holds a triangle of three mutually
    adjacent hexes — its anchor plus the two hexes forward of it (in the
    ``facing`` and ``facing + 1`` directions).
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
    """A race's starting ST/DX limits (Section VIII, p.21)."""

    min_strength: int
    min_dexterity: int
    total: int


# Section VIII "Fantasy Fighters" (p.21). Humans are the Section III baseline.
RACE_SPREADS: dict[Race, RaceSpread] = {
    Race.HUMAN: RaceSpread(HUMAN_MIN_ATTRIBUTE, HUMAN_MIN_ATTRIBUTE, HUMAN_START_TOTAL),
    Race.ORC: RaceSpread(8, 8, 24),
    Race.ELF: RaceSpread(6, 10, 24),
    Race.DWARF: RaceSpread(10, 6, 24),
    Race.HALFLING: RaceSpread(4, 12, 22),
    Race.GOBLIN: RaceSpread(6, 8, 22),
    Race.HOBGOBLIN: RaceSpread(7, 6, 20),
}

# Armors in which an elf keeps its +2 MA (p.21): cloth, leather, or none.
_ELF_LIGHT_ARMOR_NAMES = frozenset({"None", "Cloth", "Leather"})


@dataclass
class Figure:
    """One counter in the arena.

    Construction validates weapon strength requirements; raises ``ValueError``
    on an illegal figure.
    """

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
    char_class: str = ""
    # ---- inert wizard identity (classic magic arrives in milestone 5) ----
    intelligence: int = 8
    spells_known: list[str] = field(default_factory=list)
    has_staff: bool = False
    # ---- nonhuman quirks (Section VIII) ----
    all_front: bool = False    # every facing is "front" (giant snake)
    hard_to_hit: int = 0       # DX penalty it imposes on attackers (snake: 3)
    # ---- size / footprint (multi-hex figures: the giant, p.20) ----
    size: int = 1
    needs_two_to_engage: bool = False
    # ---- flight (gargoyle, p.21) ----
    fly_movement_allowance: int = 0
    flying: bool = False
    # ---- per-figure injury thresholds (the giant scales these, p.20) ----
    wound_hits_threshold: int = WOUND_HITS_THRESHOLD
    knockdown_hits_threshold: int = KNOCKDOWN_HITS

    # ---- mutable fight state ----
    position: Hex | None = None
    facing: int = 0
    posture: Posture = Posture.STANDING
    damage_taken: int = 0            # total hits scored against ST
    hits_this_turn: int = 0
    wounded_last_turn: bool = False  # took 5+ hits last turn -> -2 DX this turn
    attacked_this_turn: bool = False
    disengaged_this_turn: bool = False
    knocked_down_this_turn: bool = False
    moved_this_turn: int = 0
    moved_straight: bool = False
    dodging: bool = False
    defending: bool = False
    unconscious: bool = False
    dead: bool = False
    dropped_out: bool = False
    uid: str = ""
    current_option: object | None = None
    dealt_st_damage_this_turn: bool = False
    # uids of enemies this figure dealt qualifying (melee, non-thrown,
    # non-missile) damage to this turn and may STILL force to retreat — each is
    # a single push (p.20), removed once spent, and per-target.
    force_retreat_targets_this_turn: list[str] = field(default_factory=list)
    missile_cooldown: int = 0
    hth_opponents: list[str] = field(default_factory=list)  # inert: HTH later

    # ---- inert per-fight magic state (milestone 5) ----
    spell_protection: int = 0
    cast_this_turn: bool = False

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
        """Out of the fight but not dead: ST 0 or below — unconscious (p.3)."""
        return self.current_st <= 0 or self.dropped_out

    @property
    def is_dead(self) -> bool:
        """ST -1 or below: dead (p.3)."""
        return self.current_st <= -1

    @property
    def out_of_play(self) -> bool:
        """Dead or collapsed — no longer a legal attack target."""
        return self.is_dead or self.collapsed

    @property
    def movement_allowance(self) -> int:
        """Hexes per turn; set by armor (shields don't change MA).

        An airborne figure moves at its flying allowance instead. An elf is
        fleeter in light armor (p.21): +2 MA in cloth, leather, or none.
        """
        if self.flying and self.fly_movement_allowance:
            return self.fly_movement_allowance
        base = self.armor.movement_allowance
        if self.race == Race.ELF and self.armor.name in _ELF_LIGHT_ARMOR_NAMES:
            base += 2
        return base

    @property
    def can_fly(self) -> bool:
        return self.fly_movement_allowance > 0

    def footprint(self, layout: HexLayout) -> list[Hex]:
        """The hexes this figure currently occupies (``[]`` if off the board)."""
        if self.position is None:
            return []
        return footprint_for(layout, self.position, self.facing, self.size)

    @property
    def in_hth(self) -> bool:
        """Locked in hand-to-hand combat (inert until HTH is ported)."""
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
        """Inert until classic magic is reconciled (milestone 5)."""
        return 0

    def spell_defense_dx_penalty(self) -> int:
        """Inert until classic magic is reconciled (milestone 5)."""
        return 0

    def hits_stopped(self, *, from_front: bool, from_rear: bool = False) -> int:
        """Hits absorbed per attack by armor plus a shield.

        A *ready* shield covers the three front hexes; an *unready* (slung)
        shield instead covers the single rear hex (p.12).
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
        """A wizard with no weapon in hand (Wizard p.9) — engages no one.

        Inert until milestone 5 (``spells_known`` is always empty here), but
        kept so the ported engagement rules read melee's exact predicate.
        """
        return bool(self.spells_known) and self.ready_weapon is None


# The per-turn flags reset at end of turn, listed once so end_turn and any
# future save/load share one source (melee #155). current_option and
# wounded_last_turn reset differently and stay explicit.
PER_TURN_FLAGS: dict[str, int | bool | list] = {
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
}


def create_fighter(
    name: str,
    strength: int,
    dexterity: int,
    side: str,
    race: Race = Race.HUMAN,
    validate: bool = True,
    **gear,
) -> Figure:
    """Create a fighter of ``race``, enforcing its ST/DX spread (Sections III, VIII)."""
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
