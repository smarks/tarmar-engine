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
# Striking attack (hand-to-hand-and-grappling.md: "bare hands resolve on the
# **Striking** row"). Every bare-handed blow is an HTH action (option t):
# a grappled figure's Strike Back, a grappler's Squeeze, and a standalone
# strike once an HTH entry condition holds (tarmar-studio #815/#823).
UNARMED_WEAPON_CLASS = "Striking"


def bare_handed_damage(strength: int) -> str:
    """Damage expression for a bare-handed strike at the given STR."""
    for top_of_band, expression in BARE_HANDED_DAMAGE_TABLE:
        if strength <= top_of_band:
            return expression
    return BARE_HANDED_DAMAGE_TABLE[-1][1]


@dataclass
class WeaponState:
    """A weapon as the engine sees it. ``item_id`` empty = unarmed.

    The rate fields carry weapons.md's Notes column for missile weapons
    (tarmar-studio #781); :func:`tarmar_engine.weapons.missile_rate_from_note`
    reads them off the published note so a game never retypes the numbers.
    Their defaults — never a second shot, loaded every turn — are the
    behaviour of every weapon the table gives no note.
    """

    item_id: str = ""
    name: str = "bare hands"
    weapon_class: str = UNARMED_WEAPON_CLASS
    damage: str = "1d6-3"
    str_req: int = 0
    is_missile: bool = False
    is_thrown: bool = False
    #: A dagger: usable at HTH range ("Bare hands or dagger", the HTH table),
    #: and what DRAW DAGGER (u) readies.
    hth_usable: bool = False
    #: attack-rolls.md fumble 6: "weapon takes stress (breaks on a second
    #: fumble)". The stress belongs to the weapon, so it travels with it.
    stressed: bool = False
    #: weapons.md "2 shots/turn if adjDEX N+": N, or 0 for never.
    double_shot_dex: int = 0
    #: weapons.md crossbow notes: turns per shot at base (2 = every other
    #: turn, 3 = every 3rd turn); 1 = every turn.
    reload_turns: int = 1
    #: …and the adjDEX that shortens it ("every if 14+"), with the cycle it
    #: shortens to. 0 = no quicker cycle.
    quick_reload_dex: int = 0
    quick_reload_turns: int = 1
    #: Turns until this weapon is loaded again (0 = loaded). It belongs to
    #: the weapon, so a fired crossbow dropped and picked up is still
    #: unloaded (review of #781).
    reload_turns_left: int = 0


@dataclass
class GroundWeapon:
    """A weapon lying in a hex: dropped by a fumble, dropped to change or pick
    up another, or thrown (tarmar-studio #780/#812)."""

    q: int
    r: int
    weapon: WeaponState = field(default_factory=WeaponState)

    @property
    def position(self) -> tuple[int, int]:
        return (self.q, self.r)


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
    #: movement.md gait distances. A gait the figure may not use — chainmail
    #: and heavier "Cannot Run or Sprint", a Medium load likewise, leather
    #: "Cannot Sprint" (armor-and-shields.md, movement.md) — is 0, and the
    #: option that needs it is not offered (tarmar-studio #778/#818). Sprint
    #: defaults to 0 so a game that has not seated a sprint distance gets
    #: none, rather than one invented here.
    move_run: int = 12
    move_sprint: int = 0
    #: movement.md's Movement Modifier (CON + STR + DEX modifiers). Read by
    #: the HTH entry condition "has a lower movement modifier than you".
    movement_modifier: int = 0
    #: Weapons carried but not in hand — slung, sheathed, a spare dagger.
    #: READY WEAPON (e), CHANGE WEAPON (m) and DRAW DAGGER (u) take from here.
    spare_weapons: list[WeaponState] = field(default_factory=list)
    #: Effective Weapon-skill level with each weapon by ``item_id``: what a
    #: figure's skill becomes when it readies, changes to or picks up that
    #: weapon. A weapon missing from the map is used at level 0; the engine
    #: records the readied weapon's level here whenever it leaves the hand.
    weapon_skills: dict[str, int] = field(default_factory=dict)
    # Magic.
    max_mana: int = 0
    mana: int = 0
    spells: list[str] = field(default_factory=list)
    active_spells: list[str] = field(default_factory=list)
    #: spell-mastery.md level per spell key (2 = no gestures, 3 = no verbal
    #: incantation). A spell missing from the map is at level 1. A grappled
    #: caster renews only level 2+ spells and casts only level 3+ ones
    #: (tarmar-studio #821).
    spell_mastery: dict[str, int] = field(default_factory=dict)
    #: Recorded per-spell skill levels, by spell key, as the game
    #: keeps them (tarmar-studio derives them from ``Character.spells``). Read by
    #: injected Channel rules (``tarmar_engine.magic.ChannelRules``); a spell
    #: missing from the map is at level 0. Nothing else reads it.
    spell_skill_levels: dict[str, int] = field(default_factory=dict)
    #: The caster's level in general skills a kind of magic's Channel may
    #: derive from instead of the spell's own level, by the injected skill
    #: key (``ChannelDerivation.source_skill``). Missing: level 0.
    skill_levels: dict[str, int] = field(default_factory=dict)
    # Team tag. Empty means free-for-all — this combatant is its own team of
    # one, an enemy of everybody. A non-empty tag makes every combatant
    # carrying the same tag a teammate: never offered as a target
    # (``BattleState.enemies_of`` is the one gate all targeting reads
    # through), and victory upstream is last *team* standing.
    team: str = ""
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
    # action, whatever that action is (tarmar-studio #811). Weapon stress
    # lives on the weapon (``WeaponState.stressed``).
    off_balance: bool = False
    # Whether this engagement's One Last Shot has been taken — reset the
    # first turn the figure starts disengaged (tarmar-studio #776). A
    # crossbow's reload is the weapon's own (``WeaponState``).
    last_shot_spent: bool = False
    # Enemies this figure is in hand-to-hand with: entered by a grapple
    # attempt or an HTH strike, kept while the two stay adjacent. Inside it
    # neither needs an entry condition again, and a strike with bare hands
    # or a dagger (t) takes the HTH +4 (hand-to-hand-and-grappling.md; #867).
    hth_with: list[int] = field(default_factory=list)
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
    #: Extra mana the chosen cast pushes into its spell (tarmar-engine #27);
    #: 0 is an ordinary cast. Only a profile with injected Push rules acts on
    #: it.
    chosen_push_mana: int = 0
    moved_this_turn: bool = False
    dealt_damage_this_turn: bool = False
    took_damage_this_turn: bool = False
    # special-combat-situations.md's Forced Retreat: "If you dealt physical
    # hits and took none". A landed weapon or bare-handed blow, whatever the
    # armour stopped; spells are not physical hits (tarmar-studio #813).
    dealt_physical_hit_this_turn: bool = False
    took_physical_hit_this_turn: bool = False
    # Enemies this figure was adjacent to when it disengaged (option n) this
    # turn: a slower one may still strike it, at the adjDEX difference
    # (special-combat-situations.md, tarmar-studio #777).
    disengaged_from: list[int] = field(default_factory=list)
    # Post-armour damage taken this turn, as a count. Feeds the profile
    # seam's reaction and retreat mechanics (tarmar_engine.reactions /
    # tarmar_engine.retreat); the Tarmar profile maintains it but keys no
    # behavior off it.
    hits_this_turn: int = 0
    # Melee-structure reaction flag: wounded by this-many-hits-last-turn
    # (rolled forward by HitCountReactions.end_of_turn, read by its
    # dx_penalty). Unused by the Tarmar profile.
    wounded_last_turn: bool = False
    # Melee-structure forced-retreat entitlements: ids of enemies this
    # combatant hit with damaging melee blows this turn, each spent by one
    # push (tarmar_engine.retreat.MeleeStyleForcedRetreat). Unused by the
    # Tarmar profile, whose phase 6 keys off the damage flags above.
    retreat_push_targets_this_turn: list[int] = field(default_factory=list)
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
    def weapon_stressed(self) -> bool:
        """Is the readied weapon stressed? Read-only: the stress is the
        weapon's own (``WeaponState.stressed``) since v0.9.5, and this name
        stays for every reader of the old field."""
        return self.weapon.stressed

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
        self.chosen_push_mana = 0
        self.moved_this_turn = False
        self.dealt_damage_this_turn = False
        self.took_damage_this_turn = False
        self.dealt_physical_hit_this_turn = False
        self.took_physical_hit_this_turn = False
        self.disengaged_from = []
        self.hits_this_turn = 0
        self.retreat_push_targets_this_turn = []


@dataclass
class BattleState:
    """The whole battle between turns: arena, combatants, event counter."""

    arena_radius: int = 8
    turn: int = 0
    next_sequence: int = 1
    combatants: list[CombatantState] = field(default_factory=list)
    #: Weapons lying on the field (tarmar-studio #780).
    ground_weapons: list[GroundWeapon] = field(default_factory=list)

    def active_combatants(self) -> list[CombatantState]:
        return [combatant for combatant in self.combatants if combatant.active]

    def enemies_of(self, combatant: CombatantState) -> list[CombatantState]:
        """Every other active combatant, minus living teammates.

        With no teams in play (every ``team`` empty) this is the historic
        free-for-all: everyone else is an enemy. A non-empty ``team`` names
        teammates, who are never enemies — the single chokepoint the AI's
        targeting, engagement, and combat math all read through.
        """
        return [
            other
            for other in self.combatants
            if other.active
            and other.combatant_id != combatant.combatant_id
            and (not combatant.team or other.team != combatant.team)
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
            # Before v0.9.5 the stress flag sat on the combatant; a snapshot
            # written then carries it there, and it belongs to the weapon.
            if entry.pop("weapon_stressed", False):
                weapon.stressed = True
            spares = [WeaponState(**spare) for spare in entry.pop("spare_weapons", [])]
            combatants.append(
                CombatantState(weapon=weapon, spare_weapons=spares, **entry)
            )
        ground = [
            GroundWeapon(
                q=lying["q"], r=lying["r"], weapon=WeaponState(**lying["weapon"])
            )
            for lying in data.get("ground_weapons", [])
        ]
        return cls(
            arena_radius=data.get("arena_radius", 8),
            turn=data.get("turn", 0),
            next_sequence=data.get("next_sequence", 1),
            combatants=combatants,
            ground_weapons=ground,
        )
