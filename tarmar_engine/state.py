"""Pure engine state — dataclasses, DB-free, serializable to Battle.state_json.

The engine never touches the ORM: the service layer snapshots each Character
into a :class:`CombatantState` once at battle creation, and from then on the
battle lives entirely in these dataclasses, round-tripped through
``Battle.state_json`` between turns.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from tarmar_rules import dex_modifier

from . import hexes

# special-combat-situations.md — Bare-Handed Damage by STR, as
# (top-of-band STR, damage expression) rows; the first band the STR fits wins.
BARE_HANDED_DAMAGE_TABLE: tuple[tuple[int, str], ...] = (
    (8, "1d6-4"),
    (10, "1d6-3"),
    (12, "1d6-2"),
    (14, "1d6-1"),
    (16, "1d6"),
    (20, "1d6+1"),
    (24, "1d6+2"),
    (30, "1d6+3"),
    (40, "2d6+1"),
    (50, "3d6+1"),
)

# Unarmed strikes have no catalog row; they resolve on the matrix as a
# Striking attack (one-handed blows — the closest published class). A plain
# standalone unarmed strike outside a grapple (HTH option t on its own) is
# still out of scope for v1; t is implemented only as a grappled figure's
# Strike Back (tarmar_engine.engine.grapple_strike_back) and reuses this
# same class.
UNARMED_WEAPON_CLASS = "Striking"


def bare_handed_damage(strength: int) -> str:
    """Damage expression for a bare-handed strike at the given STR."""
    for top_of_band, expression in BARE_HANDED_DAMAGE_TABLE:
        if strength <= top_of_band:
            return expression
    return BARE_HANDED_DAMAGE_TABLE[-1][1]


@dataclass
class WeaponState:
    """The readied weapon as the engine sees it. ``item_id`` empty = unarmed."""

    item_id: str = ""
    name: str = "bare hands"
    weapon_class: str = UNARMED_WEAPON_CLASS
    damage: str = "1d6-3"
    str_req: int = 0
    is_missile: bool = False
    is_thrown: bool = False


@dataclass
class CombatantState:
    """One combatant's full mutable state plus the frozen stat snapshot."""

    combatant_id: int
    name: str
    archetype: str = ""
    # Attribute snapshot (effective values; DEX is combat DEX with the
    # armour penalty folded in, per attack-rolls.md).
    strength: int = 10
    dexterity: int = 10
    intelligence: int = 10
    wisdom: int = 10
    constitution: int = 10
    # Pools. ``fatigue``/``body`` are current values and may go negative —
    # the injury thresholds live below zero (tarmar-studio's characters.models).
    max_fatigue: int = 20
    max_body: int = 14
    fatigue: int = 20
    body: int = 14
    # Spatial state.
    q: int = 0
    r: int = 0
    facing: int = 0
    # Loadout snapshot.
    weapon: WeaponState = field(default_factory=WeaponState)
    weapon_skill_level: int = 0
    armour_tier: str = "None"
    stops: int = 0
    shield_bonus: int = 0
    move_walk: int = 4
    move_jog: int = 7
    move_run: int = 12
    # Magic.
    max_mana: int = 0
    mana: int = 0
    spells: list[str] = field(default_factory=list)
    active_spells: list[str] = field(default_factory=list)
    # Figure size and kind. ``size_hexes`` > 1 means a multi-hex footprint
    # (tarmar_engine.hexes); ``is_beast`` routes the AI's melee-only subset
    # and body-based flee/defend thresholds (battle.policy).
    size_hexes: int = 1
    is_beast: bool = False
    # Turn state.
    alive: bool = True
    conscious: bool = True
    prone: bool = False
    # Fumble state (attack-rolls.md §7): off-balance costs −2 on the next
    # action; a stressed weapon breaks on a second fumble.
    off_balance: bool = False
    weapon_stressed: bool = False
    defending: bool = False  # Defend chosen this turn (+4 TN vs melee)
    dodging: bool = False  # Dodge chosen this turn (+4 TN vs missiles)
    yielded: bool = False  # yielded initial movement, moves in phase 4
    # HTH grapple state (hand-to-hand-and-grappling.md). Exactly one of a
    # held pair: the captive's grappled_by names their captor, the captor's
    # grappling names who they hold. Persistent across turns — deliberately
    # not cleared by reset_for_turn — the hold lasts until Struggle Free
    # succeeds or the grappler Releases, not just for the turn it starts.
    grappled_by: int | None = None
    grappling: int | None = None
    # The turn's chosen action-option (action-options.md letter) and its
    # parameters, decided by the policy before movement because the option
    # constrains both the move allowance and the phase-5 action.
    chosen_letter: str = ""
    chosen_target: int | None = None
    chosen_spell: str = ""
    moved_this_turn: bool = False
    dealt_damage_this_turn: bool = False
    took_damage_this_turn: bool = False
    # Fatal roll chain: sequence numbers of the to-hit/damage/threshold roll
    # events that led to this combatant's death (filled by the engine).
    fatal_chain: list[int] = field(default_factory=list)

    @property
    def position(self) -> tuple[int, int]:
        return (self.q, self.r)

    @position.setter
    def position(self, value: tuple[int, int]) -> None:
        self.q, self.r = value

    @property
    def active(self) -> bool:
        """Still fighting: alive and conscious."""
        return self.alive and self.conscious

    @property
    def footprint(self) -> tuple[tuple[int, int], ...]:
        """The hex cluster this figure occupies (head hex first)."""
        return hexes.footprint(self.position, self.facing, self.size_hexes)

    @property
    def front_hexes(self) -> frozenset[tuple[int, int]]:
        """The hexes this figure attacks into and engages through."""
        return hexes.front_hexes(self.position, self.facing, self.size_hexes)

    @property
    def dex_bonus(self) -> int:
        """d20 to-hit bonus from combat DEX (``tarmar_rules.dex_modifier``)."""
        return dex_modifier(self.dexterity)

    @property
    def renewal_order_key(self) -> int:
        """Phase-2 ordering: DEX+INT+WIS, high first (turn-sequence.md)."""
        return self.dexterity + self.intelligence + self.wisdom

    def reset_for_turn(self) -> None:
        """Clear the per-turn flags at the start of a turn."""
        self.defending = False
        self.dodging = False
        self.yielded = False
        self.chosen_letter = ""
        self.chosen_target = None
        self.chosen_spell = ""
        self.moved_this_turn = False
        self.dealt_damage_this_turn = False
        self.took_damage_this_turn = False


@dataclass
class BattleState:
    """The whole battle between turns: arena, combatants, event counter."""

    arena_radius: int = 8
    turn: int = 0
    next_sequence: int = 1
    combatants: list[CombatantState] = field(default_factory=list)

    def active_combatants(self) -> list[CombatantState]:
        return [combatant for combatant in self.combatants if combatant.active]

    def enemies_of(self, combatant: CombatantState) -> list[CombatantState]:
        """Free-for-all: every other active combatant is an enemy."""
        return [
            other
            for other in self.combatants
            if other.active and other.combatant_id != combatant.combatant_id
        ]

    def occupied_hexes(self) -> set[tuple[int, int]]:
        """Every hex covered by a living figure's footprint."""
        return {
            cell
            for combatant in self.combatants
            if combatant.alive
            for cell in combatant.footprint
        }

    def by_id(self, combatant_id: int) -> CombatantState:
        for combatant in self.combatants:
            if combatant.combatant_id == combatant_id:
                return combatant
        raise KeyError(f"No combatant with id {combatant_id}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> BattleState:
        combatants = []
        for raw_entry in data.get("combatants", []):
            entry = dict(raw_entry)
            weapon = WeaponState(**entry.pop("weapon", {}))
            combatants.append(CombatantState(weapon=weapon, **entry))
        return cls(
            arena_radius=data.get("arena_radius", 8),
            turn=data.get("turn", 0),
            next_sequence=data.get("next_sequence", 1),
            combatants=combatants,
        )
