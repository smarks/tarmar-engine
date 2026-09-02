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


SPELLS: dict[str, Spell] = {
    spell.key: spell
    for spell in (
        Spell(
            key="fire_missile",
            name="Fire Missile",
            school="Elemental",
            level=1,
            attribute="INT",
            targeted=True,
            damage="1d6",
        ),
        Spell(
            key="fire_ball",
            name="Fire Ball",
            school="Elemental",
            level=2,
            attribute="INT",
            targeted=True,
            damage="2d6",
        ),
        Spell(
            key="lightning_bolt",
            name="Lightning Bolt",
            school="Elemental",
            level=3,
            attribute="INT",
            targeted=True,
            damage="3d6",
        ),
        Spell(
            key="shield",
            name="Shield",
            school="Protection",
            level=1,
            attribute="INT",
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
