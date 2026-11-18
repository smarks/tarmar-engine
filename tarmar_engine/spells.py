"""Data-driven combat spell catalog for the battle simulator.

Spell names come from the repo's own
``reference/content/public-rules/magic/schools-of-magic.md`` spell lists
(Elemental Missile/Ball/Bolt, Protection Shield and Counter 1 to 5, Passage
Blur, Control Fatigue/Wound, Mending Heal); levels match that document's
numbering.

Casting mechanics follow ``reference/content/public-rules/magic/
casting-spells.md`` exactly:

* Cost: mana equal to the spell's level.
* Roll: 3d6 ≤ INT or WIS (per spell).
* Targeted spells ("magic requiring hitting a target") need an additional
  DEX roll — 3d6 ≤ DEX, the rules' standard attribute-check form
  (``attack-rolls.md``: "spellcasting still rolls 3d6 ≤ attribute").
* Continuing spells are renewed in Phase 2 by paying the mana again, or end.

The Counter rungs are **generic counters** (``Spell.generic_counter``):
learned like any spell and never cast. Knowing one helps its holder's counter
against a spell of its level or lower (:func:`generic_counter_level`); by how
much is the injected rules' to say.

Mana pool: ``public-rules/magic.md`` defines the Mana Pool as set at
character creation and separate from attributes, and the skills glossary
defines Spell Points as fuel toward it — the ``Character.free_spell_points``
field. The engine reads that field as the pool. (The design doc's fallback
"mana pool = mental pool" is NOT used; the rules doc wins.)

Effect magnitudes (damage dice, TN bonuses) are engine adaptations: the rules
name these spells but publish no combat numbers for them, so conservative
values are chosen here and kept in one data table for easy revision.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from .state import BODY_POOL, FATIGUE_POOL, check_caster_pool

# The school whose spells are studied element by element.
ELEMENTAL_SCHOOL = "Elemental"

# schools-of-magic.md: "Elemental (water, fire, lightning, earth, wind)". A
# student of Elemental magic studies one element as a track and climbs it
# tier by tier (Missile, then Ball, then Bolt); switching element means
# starting the new one at tier 1 (Spencer's ruling, tarmar-engine #75).
ELEMENTS: tuple[str, ...] = ("water", "fire", "lightning", "earth", "wind")

# The school of Shield and of the Counter rungs.
PROTECTION_SCHOOL = "Protection"

# The ladder the generic counters climb, as the ladder index names it beside
# the elements' (house reading ``generic_counters_climb_one_ladder``).
GENERIC_COUNTER_LADDER = "generic counter"

# What a generic counter leaves unset: it is never cast, so it has no aim, no
# effect to roll, nothing to keep up and no element's ladder to stand on.
_FIELDS_A_GENERIC_COUNTER_LEAVES_UNSET: tuple[str, ...] = (
    "targeted",
    "damage",
    "heals",
    "continuing",
    "tn_bonus",
    "attacker_penalty",
    "element",
)

# Dodge's +4-to-TN (special-combat-situations.md) is a d20 concept; for a
# targeted spell's 3d6 ≤ DEX check the equivalent is a penalty on the caster's
# effective DEX. Kept equal in magnitude, documented as the re-mapping.
DODGE_DEX_CHECK_PENALTY = 4


@dataclass(frozen=True)
class Spell:
    key: str
    name: str
    school: str
    level: int  # mana cost = level (casting-spells.md)
    attribute: str  # "INT" or "WIS" — the 3d6 casting roll's target
    targeted: bool  # needs the additional DEX roll
    damage: str | None = None  # dice expression, or None
    damage_pool: str = FATIGUE_POOL  # which pool the damage hits
    ignores_armour: bool = False  # mental/direct effects skip stops
    heals: bool = False  # damage expression restores instead
    continuing: bool = False  # renewed each turn in Phase 2 or ends
    tn_bonus: int = 0  # added to the caster's TN while active
    attacker_penalty: int = 0  # to-hit penalty on attacks at the caster
    element: str | None = None  # an Elemental spell's element, else None
    # Learned and never cast (house reading
    # ``generic_counter_is_learned_never_cast``): its level is the highest
    # spell level its holder's counter is helped against.
    generic_counter: bool = False

    def __post_init__(self) -> None:
        check_caster_pool(f"spell {self.key!r}'s damage_pool", self.damage_pool)
        if self.generic_counter:
            set_fields = [
                field_name
                for field_name in _FIELDS_A_GENERIC_COUNTER_LEAVES_UNSET
                if getattr(self, field_name)
            ]
            if set_fields:
                raise ValueError(
                    f"generic counter {self.key!r} is never cast, so it cannot "
                    f"set {set_fields}"
                )
        if self.element is None:
            if self.school == ELEMENTAL_SCHOOL:
                # An Elemental spell outside the ladders would escape the
                # one-element-at-a-time ruling (#75).
                raise ValueError(f"Elemental spell {self.key!r} names no element")
            return
        if self.element not in ELEMENTS:
            raise ValueError(
                f"spell {self.key!r} names the element {self.element!r}, which is "
                f"not one of {ELEMENTS}"
            )
        if self.school != ELEMENTAL_SCHOOL:
            raise ValueError(
                f"spell {self.key!r} names the element {self.element!r} but is "
                f"of the {self.school} school, not {ELEMENTAL_SCHOOL}"
            )


@dataclass(frozen=True)
class _ElementalTier:
    """One rung of every element's ladder: the fields its spell carries."""

    name_suffix: str  # "Missile" -> "Fire Missile", key "fire_missile"
    level: int
    attribute: str
    targeted: bool
    damage: str


# One table for every element's ladder (schools-of-magic.md's Elemental
# levels 1-3); each element's Missile, Ball and Bolt are this table crossed
# with ELEMENTS.
_ELEMENTAL_TIERS: tuple[_ElementalTier, ...] = (
    _ElementalTier(
        name_suffix="Missile", level=1, attribute="INT", targeted=True, damage="1d6"
    ),
    _ElementalTier(
        name_suffix="Ball", level=2, attribute="INT", targeted=True, damage="2d6"
    ),
    _ElementalTier(
        name_suffix="Bolt", level=3, attribute="INT", targeted=True, damage="3d6"
    ),
)


def _elemental_spell(element: str, tier: _ElementalTier) -> Spell:
    """Build one element's spell at one tier of the ladder."""
    return Spell(
        key=f"{element}_{tier.name_suffix.lower()}",
        name=f"{element.capitalize()} {tier.name_suffix}",
        school=ELEMENTAL_SCHOOL,
        level=tier.level,
        attribute=tier.attribute,
        targeted=tier.targeted,
        damage=tier.damage,
        element=element,
    )


# schools-of-magic.md's Protection list: "Counter 1" at level 1 up to
# "Counter 5" at level 5. Each rung's name and key are built from its level.
_GENERIC_COUNTER_NAME = "Counter"
_GENERIC_COUNTER_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)


def _generic_counter_spell(level: int) -> Spell:
    """Build the generic counter at one level of the Counter ladder."""
    return Spell(
        key=f"{_GENERIC_COUNTER_NAME.lower()}_{level}",
        name=f"{_GENERIC_COUNTER_NAME} {level}",
        school=PROTECTION_SCHOOL,
        level=level,
        # spell-descriptions.md's Protection table gives Counter WIS, as it
        # does Shield. The attribute is recorded and never rolled.
        attribute="WIS",
        targeted=False,
        generic_counter=True,
    )


SPELLS: dict[str, Spell] = {
    spell.key: spell
    for spell in (
        *(
            _elemental_spell(element, tier)
            for element in ELEMENTS
            for tier in _ELEMENTAL_TIERS
        ),
        Spell(
            key="shield",
            name="Shield",
            school=PROTECTION_SCHOOL,
            level=1,
            # spell-descriptions.md's Protection table is explicit and
            # self-consistent: Shield is "a defensive instinct, not a shaped
            # effect" and rolls against WIS, not INT (tarmar-studio#291).
            attribute="WIS",
            targeted=False,
            continuing=True,
            tn_bonus=1,
        ),
        *(_generic_counter_spell(level) for level in _GENERIC_COUNTER_LEVELS),
        Spell(
            key="blur",
            name="Blur",
            school="Passage",
            level=2,
            attribute="INT",
            targeted=False,
            continuing=True,
            attacker_penalty=2,
        ),
        Spell(
            key="fatigue",
            name="Fatigue",
            school="Control",
            level=2,
            attribute="INT",
            targeted=False,
            damage="1d6+1",
            ignores_armour=True,
        ),
        Spell(
            key="wound",
            name="Wound",
            school="Control",
            level=3,
            attribute="INT",
            targeted=False,
            damage="1d6",
            damage_pool=BODY_POOL,
            ignores_armour=True,
        ),
        Spell(
            key="heal",
            name="Heal",
            school="Mending",
            level=1,
            attribute="WIS",
            targeted=False,
            damage="1d6",
            heals=True,
        ),
    )
}


def get_spell(key: str) -> Spell:
    """Look a spell up by catalog key.

    Raises:
        KeyError: for an unknown key — a loadout naming a spell that does not
            exist is a programming error, not a condition to paper over.
    """
    return SPELLS[key]


def _ladder_name(spell: Spell) -> str | None:
    """The ladder a spell stands on: its element,
    :data:`GENERIC_COUNTER_LADDER` for a generic counter whatever its school,
    or None for a spell on no ladder."""
    if spell.generic_counter:
        return GENERIC_COUNTER_LADDER
    return spell.element


def _index_ladders(spells: Iterable[Spell]) -> dict[tuple[str, int], Spell]:
    """Index the spells that stand on a ladder by (ladder name, level).

    Args:
        spells: the spells to index; those on no ladder are skipped.

    Returns:
        Each ladder's spells keyed by (ladder name, level): an element's
        name, or :data:`GENERIC_COUNTER_LADDER` for the generic counters.

    Raises:
        ValueError: when two spells share a ladder and level, or when a
            ladder's levels do not run 1, 2, ... n without a gap.
    """
    index: dict[tuple[str, int], Spell] = {}
    for spell in spells:
        ladder = _ladder_name(spell)
        if ladder is None:
            continue
        rung = (ladder, spell.level)
        if rung in index:
            raise ValueError(
                f"{spell.key!r} and {index[rung].key!r} are both the level "
                f"{spell.level} {ladder} spell"
            )
        index[rung] = spell
    levels_by_ladder: dict[str, list[int]] = {}
    for ladder, level in index:
        levels_by_ladder.setdefault(ladder, []).append(level)
    for ladder, levels in levels_by_ladder.items():
        expected_levels = list(range(1, len(levels) + 1))
        if sorted(levels) != expected_levels:
            raise ValueError(
                f"the {ladder} ladder holds levels {sorted(levels)}, not "
                f"{expected_levels}"
            )
    return index


_LADDER_INDEX: dict[tuple[str, int], Spell] = _index_ladders(SPELLS.values())


def lower_tier(spell: Spell) -> Spell | None:
    """The spell one rung down the same ladder: an element's, or the generic
    counters' (house reading ``generic_counters_climb_one_ladder``).

    Args:
        spell: the spell whose lower tier is wanted.

    Returns:
        The catalog spell of the same ladder one level below, or None for a
        tier-1 spell or a spell on no ladder.

    Raises:
        KeyError: when the catalog holds no rung below the spell's level, a
            spell that is not on a catalog ladder.
    """
    ladder = _ladder_name(spell)
    if ladder is None or spell.level == 1:
        return None
    rung_below = (ladder, spell.level - 1)
    if rung_below not in _LADDER_INDEX:
        raise KeyError(f"no level {spell.level - 1} {ladder} spell below {spell.key!r}")
    return _LADDER_INDEX[rung_below]


def element_siblings(spell: Spell) -> tuple[Spell, ...]:
    """The same rung of every other element's ladder, in :data:`ELEMENTS`
    order.

    Args:
        spell: the spell whose siblings are wanted.

    Returns:
        The catalog spell at the spell's level in each other element that
        has such a rung; empty for a spell with no element, a generic
        counter included.
    """
    if spell.element is None:
        return ()
    return tuple(
        _LADDER_INDEX[(element, spell.level)]
        for element in ELEMENTS
        if element != spell.element and (element, spell.level) in _LADDER_INDEX
    )


def generic_counter_level(spell_keys: Iterable[str]) -> int:
    """The highest level among the generic counters a figure knows.

    Only the highest rung known counts, and a rung's mastery level changes
    nothing (house reading
    ``generic_counter_level_is_the_highest_rung_known``).

    Args:
        spell_keys: the catalog keys of the spells the figure knows.

    Returns:
        The level of the highest generic counter among them; 0 for none.

    Raises:
        KeyError: for a key the catalog does not hold.
    """
    known_spells = (get_spell(key) for key in spell_keys)
    return max(
        (spell.level for spell in known_spells if spell.generic_counter), default=0
    )
