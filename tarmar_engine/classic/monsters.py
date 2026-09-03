"""
Monsters and beasts (Section VIII, p.21) — ported from melee's
``engine/monsters.py`` alongside the heuristic AI (tarmar-engine#3),
whose multi-hex branches (the three-hex giant that translates without
turning) have no other creature to exercise them. SJG-derived statlines,
so quarantined here in the classic subpackage like the rest.


Unlike fighters, monsters are not point-bought: each has a fixed statline taken
straight from the rulebook (MA, ST, DX, natural armour, and a natural attack).
We model each as a :class:`~tarmar_engine.classic.figure.Figure` built by
:func:`create_monster`,
which bypasses the human point-spread check in
:func:`~tarmar_engine.classic.figure.create_fighter`.

Two pieces of existing machinery are reused so a monster drops into combat with
no special-casing:

* **Natural armour and MA** ride on a synthetic
  :class:`~tarmar_engine.classic.data.Armor`
  (``stops`` = hits its hide absorbs, ``movement_allowance`` = its MA, no DX
  penalty). ``Figure.hits_stopped`` and ``Figure.movement_allowance`` already
  read those fields, so nothing downstream changes.
* **The natural attack** (bite / claws / club) is a
  :class:`~tarmar_engine.classic.data.Weapon`
  with no strength requirement, set as the monster's ready weapon, so the normal
  to-hit / damage path resolves it.

Implemented quirks:

* the giant snake's *side = front* (``all_front``) and its *very hard to hit* -3
  (``hard_to_hit``);
* the **giant** occupies **three hexes** (``size`` 3), is engaged only by **two**
  foes in its front (``needs_two_to_engage``), and is sturdier than a normal
  figure -- it loses 2 DX only at 9 hits/turn and falls only at 16/turn (its own
  ``wound_hits_threshold`` / ``knockdown_hits_threshold``);
* the **gargoyle** flies (``fly_movement_allowance`` 16; ground MA 8) and must
  land to attack.

All of these ride on plain :class:`~tarmar_engine.classic.figure.Figure` fields
that default to
single-hex / grounded behaviour, so the rest of the engine is unchanged for
ordinary figures.
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import (
    KNOCKDOWN_HITS,
    WOUND_HITS_THRESHOLD,
    Armor,
    DamageDice,
    Weapon,
)
from .figure import Figure


@dataclass(frozen=True)
class Monster:
    """A fixed-statline creature template from the Monsters table (p.21)."""

    species: str
    strength: int
    dexterity: int
    hide: Armor              # natural armour: carries both ``stops`` and MA
    attack: Weapon           # natural bite / claws / weapon
    all_front: bool = False  # every facing is "front" (giant snake)
    hard_to_hit: int = 0     # DX penalty imposed on attackers (giant snake: 3)
    size: int = 1            # hexes occupied (giant: 3)
    needs_two_to_engage: bool = False         # giant: two foes needed to engage it
    fly_movement_allowance: int = 0           # airborne MA (gargoyle: 16)
    wound_hits_threshold: int = WOUND_HITS_THRESHOLD   # hits/turn for -2 DX
    knockdown_hits_threshold: int = KNOCKDOWN_HITS     # hits/turn to fall
    notes: str = ""

    def __post_init__(self) -> None:
        # A creature's injury thresholds follow strictly from its beginning ST
        # (ITL p.20), so derive them here rather than hand-setting each monster --
        # a ST-30+ creature (bear, giant) gets 9/16, a ST-50+ one 15/25 (#336).
        wound, knockdown = injury_thresholds(self.strength)
        object.__setattr__(self, "wound_hits_threshold", wound)
        object.__setattr__(self, "knockdown_hits_threshold", knockdown)


def injury_thresholds(strength: int) -> tuple[int, int]:
    """``(wound_hits_threshold, knockdown_hits_threshold)`` for a *beginning* ST.

    ITL p.20 ("Reactions to Injury"): an ordinary figure loses 2 DX at 5 hits in
    one turn and falls at 8. A creature whose beginning ST is 30+ is sturdier --
    it loses 2 DX only at 9 hits and falls at 16; one with beginning ST 50+ only
    at 15 and 25. Deriving this from ST keeps the rule in a single place so every
    high-ST creature gets it automatically.
    """
    if strength >= 50:
        return 15, 25
    if strength >= 30:
        return 9, 16
    return WOUND_HITS_THRESHOLD, KNOCKDOWN_HITS


def _hide(species: str, stops: int, movement_allowance: int) -> Armor:
    """A creature's natural armour, which also carries its movement allowance."""
    return Armor(f"{species} hide", stops, movement_allowance, 0)


# ---- Monster catalog (Section VIII, p.21) -----------------------------------
# A BEAR (a big one): MA 8, ST 30, DX 11, fur stops 2/attack, 2d+2 (3d in HTH).
BEAR = Monster(
    species="Bear", strength=30, dexterity=11,
    hide=_hide("Bear", 2, 8),
    attack=Weapon("Bear claws", DamageDice(2, 2), 0,
                  hth_damage=DamageDice(3, 0), notes="3 dice in HTH combat"),
)

# A WOLF: MA 12, ST 10, DX 14, fur stops 1/attack, bite 1d+1.
WOLF = Monster(
    species="Wolf", strength=10, dexterity=14,
    hide=_hide("Wolf", 1, 12),
    attack=Weapon("Wolf bite", DamageDice(1, 1), 0),
    notes="dire wolves are stronger",
)

# A GIANT SNAKE: MA 6, ST 12, DX 12, no hide armour, bite 1d+1. Very hard to hit
# (-3 to attackers), and its side hexes count as front for all purposes.
GIANT_SNAKE = Monster(
    species="Giant snake", strength=12, dexterity=12,
    hide=_hide("Giant snake", 0, 6),
    attack=Weapon("Snake bite", DamageDice(1, 1), 0),
    all_front=True, hard_to_hit=3,
)

# A GARGOYLE: ST 20, DX 11, stony flesh stops 3/attack, rocklike hands 2 dice
# (regular or HTH). MA 8 on the ground, 16 flying; it lands to attack.
GARGOYLE = Monster(
    species="Gargoyle", strength=20, dexterity=11,
    hide=_hide("Gargoyle", 3, 8),
    attack=Weapon("Gargoyle hands", DamageDice(2, 0), 0,
                  hth_damage=DamageDice(2, 0)),
    fly_movement_allowance=16,
    notes="MA 8 on the ground, 16 flying; lands to attack",
)

# A GIANT (9-12 ft): occupies 3 hexes (a tri-hex cluster). MA 10, ST 30 example,
# DX 9. Spiked club does 1d+1 per full 10 starting ST -> 3d+3 at ST 30; 2d-1 in
# HTH. Engaged only when two foes are in its front; loses 2 DX at 9 hits/turn and
# falls at 16 hits/turn (not the normal 8).
GIANT = Monster(
    species="Giant", strength=30, dexterity=9,
    hide=_hide("Giant", 0, 10),
    attack=Weapon("Spiked club", DamageDice(3, 3), 0,
                  hth_damage=DamageDice(2, -1),
                  notes="1d+1 per full 10 starting ST"),
    size=3, needs_two_to_engage=True,
    notes="occupies 3 hexes; engaged only by two foes; -2 DX at 9 hits, "
          "falls at 16 hits/turn",
)

MONSTERS: dict[str, Monster] = {
    monster.species: monster
    for monster in (BEAR, WOLF, GIANT_SNAKE, GARGOYLE, GIANT)
}


def create_monster(species: str, name: str, side: str, **state) -> Figure:
    """Build a catalogued monster as a single-hex :class:`Figure`.

    Monsters have fixed stats rather than a point-bought spread, so this builds
    the :class:`Figure` directly (skipping the human point check) with the
    creature's natural armour, MA, and natural attack already readied.
    """
    if species not in MONSTERS:
        raise ValueError(f"unknown monster {species!r}; "
                         f"choose one of {sorted(MONSTERS)}")
    template = MONSTERS[species]
    return Figure(
        name=name, strength=template.strength, dexterity=template.dexterity,
        side=side, armor=template.hide,
        weapons=[template.attack], ready_weapon=template.attack,
        all_front=template.all_front, hard_to_hit=template.hard_to_hit,
        size=template.size, needs_two_to_engage=template.needs_two_to_engage,
        fly_movement_allowance=template.fly_movement_allowance,
        wound_hits_threshold=template.wound_hits_threshold,
        knockdown_hits_threshold=template.knockdown_hits_threshold,
        **state,
    )
