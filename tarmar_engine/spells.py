"""Data-driven combat spell catalog for the battle simulator.

Spell names come from the repo's own
``reference/content/public-rules/magic/schools-of-magic.md`` spell lists
(Elemental Missile/Ball/Bolt, Protection Shield, Passage Blur, Control
Fatigue/Wound, Mending Heal); levels match that document's numbering.

Casting mechanics follow ``reference/content/public-rules/magic/
casting-spells.md`` exactly:

* Cost: mana equal to the spell's level.
* Roll: 3d6 ≤ INT or WIS (per spell).
* Targeted spells ("magic requiring hitting a target") need an additional
  DEX roll — 3d6 ≤ DEX, the rules' standard attribute-check form
  (``attack-rolls.md``: "spellcasting still rolls 3d6 ≤ attribute").
* Continuing spells are renewed in Phase 2 by paying the mana again, or end.

Mana pool: ``public-rules/magic.md`` defines the Mana Pool as set at
character creation and separate from attributes, and the skills glossary
defines Spell Points as fuel toward it — the ``Character.free_spell_points``
field. The engine reads that field as the pool. (The design doc's fallback
"mana pool = mental pool" is NOT used; the rules doc wins.)

Effect magnitudes (damage dice, TN bonuses) are engine adaptations: the rules
name these spells but publish no combat numbers for them, so conservative
values are chosen here and kept in one data table for easy revision.
"""

from dataclasses import dataclass

# The school whose spells are studied element by element.
ELEMENTAL_SCHOOL = "Elemental"

# schools-of-magic.md: "Elemental (water, fire, lightning, earth, wind)". A
# student of Elemental magic studies one element as a track and climbs it
# tier by tier (Missile, then Ball, then Bolt); switching element means
# starting the new one at tier 1 (Spencer's ruling, tarmar-engine #75).
ELEMENTS: tuple[str, ...] = ("water", "fire", "lightning", "earth", "wind")

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
    damage_pool: str = "fatigue"  # which pool the damage hits
    ignores_armour: bool = False  # mental/direct effects skip stops
    heals: bool = False  # damage expression restores instead
    continuing: bool = False  # renewed each turn in Phase 2 or ends
    tn_bonus: int = 0  # added to the caster's TN while active
    attacker_penalty: int = 0  # to-hit penalty on attacks at the caster
    element: str | None = None  # an Elemental spell's element, else None

    def __post_init__(self) -> None:
        if self.element is None:
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
            school="Protection",
            level=1,
            # spell-descriptions.md's Protection table is explicit and
            # self-consistent: Shield is "a defensive instinct, not a shaped
            # effect" and rolls against WIS, not INT (tarmar-studio#291).
            attribute="WIS",
            targeted=False,
            continuing=True,
            tn_bonus=1,
        ),
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
            damage_pool="body",
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


def lower_tier(spell: Spell) -> Spell | None:
    """The spell one rung down the same element's ladder.

    Args:
        spell: the spell whose lower tier is wanted.

    Returns:
        The catalog spell of the same school and element one level below, or
        None for a tier-1 spell or a spell with no element.
    """
    if spell.element is None:
        return None
    for candidate in SPELLS.values():
        if (
            candidate.school == spell.school
            and candidate.element == spell.element
            and candidate.level == spell.level - 1
        ):
            return candidate
    return None
