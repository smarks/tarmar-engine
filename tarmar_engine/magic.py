"""Injected magic rules: the casting success tiers and Push (tarmar-engine #26,
#27; split from tarmar-studio #825).

**No rulebook numbers live here.** The magic-mechanics pages these mechanics
come from are DM-only, and this package is public. Following the classic
profile's precedent ("all injected, no rulebook numbers"), the engine carries
each mechanic's *structure* — the state it keeps, the turn-phase hook where it
acts, the order its dice are rolled in, its menu option and its narration —
and every number and table arrives in a :class:`MagicRules` the caller
injects through the rules profile (``TarmarProfile(magic=...)``). The caller
that owns the canon (tarmar-studio) builds the real one from it; this
package's tests build one from invented numbers.

A profile with no ``MagicRules`` (the default, :data:`~tarmar_engine.profile.
TARMAR`) runs the engine exactly as before: no tiers, no Push, the same
events and payloads.

Two mechanics are modelled so far:

* **Casting success tiers.** After a successful casting roll, the roll's
  natural total is looked up in :attr:`MagicRules.casting_success_tiers`. A
  matching tier is narrated, pays its mana refund, and adds its bonus to the
  spell's rolled effect where the spell has one (:func:`spell_effect_kind`).
  A tier never turns a failed roll into a success (Spencer's ruling on
  tarmar-studio #292's scope).
* **Push.** A cast may invest extra mana (:class:`PushRules`). On a
  successful casting roll the caster then makes a Control Roll, roll-under,
  against an attribute less a penalty per mana invested; a failure's margin
  is looked up in :attr:`PushRules.failure_bands`, and certain natural totals
  are a Runaway regardless. A Runaway is narrated and flagged; its later
  turns belong to tarmar-engine #30. Only a spell whose kind of effect the
  rules give a push bonus may be pushed (:meth:`PushRules.can_push`).

The outcome names, tier names and labels are injected too: the engine's own
vocabulary is only "held", "failed" and whether a band is a Runaway.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .dice import parse_dice_expression
from .spells import Spell

#: The attributes a roll in this module may be made against.
MAGIC_ATTRIBUTES: frozenset[str] = frozenset({"INT", "WIS"})


class SpellEffectKind(StrEnum):
    """The kinds of rolled effect a magic bonus can add to.

    Engine vocabulary, from the spell catalog's own fields: a spell that
    rolls damage, and one that rolls healing. A continuing spell rolls
    nothing, so no bonus has anywhere to land.
    """

    DAMAGE = "damage"
    HEALING = "healing"


def spell_effect_kind(spell: Spell) -> SpellEffectKind | None:
    """The kind of rolled effect a spell has, or ``None`` when it has none."""
    if spell.continuing:
        return None
    if spell.heals:
        return SpellEffectKind.HEALING
    if spell.damage is not None:
        return SpellEffectKind.DAMAGE
    return None


@dataclass(frozen=True)
class CastingSuccessTier:
    """One special result of a successful casting roll.

    Attributes:
        key: Stable identifier carried in event payloads.
        label: The tier as narration prints it.
        natural_totals: The casting roll's natural totals (dice faces summed,
            no modifier) that land in this tier.
        effect_bonus: Added to the spell's rolled effect, damage or healing,
            before armour. A spell with no rolled effect takes none.
        mana_refund: Mana handed back as soon as the cast is paid, before any
            Control Roll, capped at what the cast cost, pushed mana included.
    """

    key: str
    label: str
    natural_totals: frozenset[int]
    effect_bonus: int = 0
    mana_refund: int = 0

    def __post_init__(self) -> None:
        if not self.natural_totals:
            raise ValueError(f"tier {self.key!r} covers no natural totals")
        if self.mana_refund < 0:
            raise ValueError(f"tier {self.key!r} has a negative mana refund")


@dataclass(frozen=True)
class ControlBand:
    """A band of failure margins on the Control Roll, and what it does.

    The margin is how far the roll exceeded its target (1 = missed by one).

    Attributes:
        key: Stable identifier carried in event payloads.
        label: The band as narration prints it.
        is_runaway: Does landing here start a Runaway?
        lowest_margin: The smallest failure margin in the band.
        highest_margin: The largest, or ``None`` for an open-ended band.
        spell_takes_effect: Does the spell still go off?
        push_bonus_applies: Does the pushed mana's effect bonus still apply?
    """

    key: str
    label: str
    is_runaway: bool
    lowest_margin: int
    highest_margin: int | None
    spell_takes_effect: bool
    push_bonus_applies: bool

    def covers(self, margin: int) -> bool:
        """Is this failure margin inside the band?"""
        if margin < self.lowest_margin:
            return False
        return self.highest_margin is None or margin <= self.highest_margin


@dataclass(frozen=True)
class PushRules:
    """Push's numbers: the Control Roll, its failure bands, and the payoff.

    Attributes:
        control_dice: The Control Roll's dice expression.
        control_attribute: ``"INT"`` or ``"WIS"``, the roll-under target
            before the penalty.
        penalty_per_mana: Subtracted from the target per mana invested.
        base_cost_counts_as_invested: Does the spell's own cost count toward
            the mana invested, or only the pushed mana?
        failure_bands: Contiguous bands of failure margins starting at 1, the
            last open-ended, so every failure lands in exactly one.
        automatic_runaway_totals: Natural Control Roll totals that are a
            Runaway whatever the target.
        effect_bonus_per_mana_by_kind: Added to the spell's rolled effect
            per pushed mana when the bonus applies, by
            :class:`SpellEffectKind`. A kind with no entry cannot be pushed.
        max_push_mana: The most extra mana one cast may take, or ``None`` for
            no cap of Push's own. Channel's cap, per caster and on the whole
            casting, is tarmar-engine #29's.
        spell_effect_bonus_per_mana: Per-spell overrides of the kind's bonus,
            keyed by spell key; only for a spell with a rolled effect.
    """

    control_dice: str
    control_attribute: str
    penalty_per_mana: int
    base_cost_counts_as_invested: bool
    failure_bands: tuple[ControlBand, ...]
    automatic_runaway_totals: frozenset[int]
    effect_bonus_per_mana_by_kind: Mapping[SpellEffectKind, int]
    max_push_mana: int | None = None
    spell_effect_bonus_per_mana: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parse_dice_expression(self.control_dice)  # raises on a bad expression
        if self.control_attribute not in MAGIC_ATTRIBUTES:
            raise ValueError(
                f"control_attribute must be one of {sorted(MAGIC_ATTRIBUTES)}, "
                f"not {self.control_attribute!r}"
            )
        if self.penalty_per_mana < 0:
            raise ValueError("the push penalty must not be negative")
        if self.max_push_mana is not None and self.max_push_mana < 0:
            raise ValueError("the push cap must not be negative")
        if not self.failure_bands or self.failure_bands[-1].highest_margin is not None:
            raise ValueError("the last failure band must be open-ended")
        expected_lowest = 1
        last_index = len(self.failure_bands) - 1
        for index, band in enumerate(self.failure_bands):
            if band.lowest_margin != expected_lowest:
                raise ValueError(
                    f"failure bands must be contiguous from margin 1; "
                    f"expected a band from {expected_lowest}, "
                    f"got {band.lowest_margin}"
                )
            if band.highest_margin is None:
                if index != last_index:
                    raise ValueError("only the last failure band may be open-ended")
                break
            if band.highest_margin < band.lowest_margin:
                raise ValueError(f"band {band} ends before it starts")
            expected_lowest = band.highest_margin + 1
        if self.automatic_runaway_totals and self.runaway_band() is None:
            raise ValueError(
                "automatic Runaway totals need a Runaway band to say what one does"
            )

    def band_for_margin(self, margin: int) -> ControlBand:
        """The failure band a positive margin lands in."""
        for band in self.failure_bands:
            if band.covers(margin):
                return band
        raise ValueError(f"no failure band covers margin {margin}")

    def runaway_band(self) -> ControlBand | None:
        """The band an automatic Runaway uses: the first Runaway band."""
        return next((band for band in self.failure_bands if band.is_runaway), None)

    def mana_invested(self, spell_level: int, pushed_mana: int) -> int:
        """The mana the Control Roll's penalty counts."""
        if self.base_cost_counts_as_invested:
            return spell_level + pushed_mana
        return pushed_mana

    def bonus_per_mana(self, spell: Spell) -> int | None:
        """The push bonus per mana for this spell, or ``None`` when the
        spell has no rolled effect or the rules give its kind no bonus."""
        kind = spell_effect_kind(spell)
        if kind is None:
            return None
        if spell.key in self.spell_effect_bonus_per_mana:
            return self.spell_effect_bonus_per_mana[spell.key]
        return self.effect_bonus_per_mana_by_kind.get(kind)

    def can_push(self, spell: Spell) -> bool:
        """May this spell be pushed at all?"""
        return self.bonus_per_mana(spell) is not None

    def push_room(self, spell: Spell, mana: int) -> int:
        """How much more than its cost a purse of ``mana`` may push into it."""
        if not self.can_push(spell):
            return 0
        room = mana - spell.level
        if self.max_push_mana is not None:
            room = min(room, self.max_push_mana)
        return max(0, room)

    def effect_bonus(self, spell: Spell, pushed_mana: int) -> int:
        """The bonus a push adds to this spell's rolled effect.

        Raises:
            ValueError: for a spell that cannot be pushed.
        """
        per_mana = self.bonus_per_mana(spell)
        if per_mana is None:
            raise ValueError(f"{spell.name} cannot be pushed under these rules")
        return per_mana * pushed_mana


@dataclass(frozen=True)
class MagicRules:
    """Every magic-mechanics number the engine needs, injected by the caller.

    Attributes:
        casting_success_tiers: The special results of a successful casting
            roll, by natural total. Empty: no tiers.
        push: Push's numbers, or ``None`` when Push is not offered.
    """

    casting_success_tiers: tuple[CastingSuccessTier, ...] = ()
    push: PushRules | None = None

    def __post_init__(self) -> None:
        seen: set[int] = set()
        for tier in self.casting_success_tiers:
            overlap = seen & tier.natural_totals
            if overlap:
                raise ValueError(
                    f"tier {tier.key!r} repeats natural totals {sorted(overlap)}"
                )
            seen |= tier.natural_totals

    def success_tier(self, natural_total: int) -> CastingSuccessTier | None:
        """The tier a successful casting roll's natural total lands in."""
        return next(
            (
                tier
                for tier in self.casting_success_tiers
                if natural_total in tier.natural_totals
            ),
            None,
        )
