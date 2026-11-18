"""Injected magic rules: the casting success tiers, the casting failure
totals, Push, Channel, Borrowing, Runaway and Counterspell (tarmar-engine #26,
#46, #27, #29, #28, #30, #31; split from tarmar-studio #825).

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
TARMAR`) runs the engine with none of them: no tiers, no Push, no Channel,
and a casting roll judged against the caster's attribute alone, a failure
keeping its mana and starting no Runaway (tarmar-engine #46).

Seven mechanics are modelled so far, and the rules of an eighth are held
here ahead of the turn code that will run them:

* **Casting success tiers.** After a successful casting roll, the roll's
  natural total is looked up in :attr:`MagicRules.casting_success_tiers`. A
  matching tier is narrated, pays its mana refund, and adds its bonus to the
  spell's rolled effect where the spell has one (:func:`spell_effect_kind`).
  A tier never turns a failed roll into a success (Spencer's ruling on
  tarmar-studio #292's scope).
* **Casting failure totals.** A casting roll succeeds at or under the
  caster's attribute; with :class:`CastingFailureRules` injected, a natural
  total at or above the injected floor fails whatever the attribute, the
  injected failing totals spend the spell's mana anyway (every other failure
  keeps it), and the injected Runaway total starts a Runaway.
* **Push.** A cast may invest extra mana (:class:`PushRules`). On a
  successful casting roll the caster then makes a Control Roll, roll-under,
  against an attribute less a penalty per mana invested; a failure's margin
  is looked up in :attr:`PushRules.failure_bands`, and certain natural totals
  are a Runaway regardless. A Runaway is narrated and flagged, and with
  :class:`RunawayRules` injected it runs on later turns. Only a spell whose
  kind of effect the
  rules give a push bonus may be pushed (:meth:`PushRules.can_push`).
* **Channel.** A caster's Channel for a spell bounds the whole mana of one
  casting, the spell's cost and any pushed mana together
  (:class:`ChannelRules`). It is per caster and per spell: it derives from a
  skill level recorded on the caster's snapshot, by a rule injected for each
  kind of magic. The menu offers no cast past it and the engine refuses one.
* **Borrowing.** A caster short of mana may borrow the rest
  (:class:`BorrowingRules`): the cast empties the pool and the shortfall is
  recorded as debt on the combatant, which persists across turns. After the
  spell has resolved the caster makes a debt roll, roll-under, against the
  injected attributes less a penalty per point owed; a failure's margin is
  looked up in :attr:`BorrowingRules.failure_bands` (a :class:`DebtBand`
  says what it costs and whether it is a Runaway), and certain natural
  totals land in the last band regardless. Channel still bounds the whole
  casting, borrowed or not.
* **Runaway.** A spell whose control is lost (:class:`RunawayRules`) feeds on
  its caster every later turn, in the Renew Spells phase: mana first, and
  once the mana is gone the caster falls unconscious and the drain moves to
  another pool, with death at an injected line. The conscious caster may
  roll to anchor it again, and another mage may pay and roll to do it for
  them; whoever anchors it holds an ordinary active spell, to renew or let
  lapse.
* **Counterspell.** Any mage may commit mana to counter an enemy caster's
  spell this turn (:class:`CounterspellRules`). When the spell is cast,
  before its casting roll, caster and defender make an opposed roll on an
  injected attribute, each gaining a bonus per step of mana invested; a
  defender who wins cancels the spell, its mana spent, and the caster may
  press it again on a later turn, paying again for another opposed roll.
* **True Magic** (rules only, so far). A caster with a level in the injected
  True skill may True cast a spell, deriving Channel from that skill
  (:meth:`MagicRules.cast_kind`), or craft a working over several turns
  (:class:`TrueMagicRules`). A working is a spell the rules build from an
  injected effect and a tier (:meth:`TrueMagicRules.working`), not a catalog
  entry. Its mana is gathered in shares, one per craft turn; damage that
  lands on its caster forces an interruption roll
  (:class:`InterruptionRules`); and a working or a pushed True cast drains
  its caster by an injected amount. Each side of a counter's opposed roll
  gains an injected bonus per level of its familiarity with the spell
  (:attr:`CounterspellRules.familiarity_per_level`).

A Game Master may lift three of these bounds for one figure, each separately,
as a recorded ruling on the battle (Spencer's ruling of 2026-10-14 on
tarmar-studio #37, under the standing rule that a Game Master may override
what players are held to): Channel (:data:`CHANNEL`), Push's own cap
(:data:`PUSH_CAP`) and a counter's commitment bounds (:data:`COUNTER_BOUNDS`).
The consuming game lists the lifted bounds in
``CombatantState.lifted_magic_bounds`` from its own record of the ruling; the
menu then offers that figure the wider amounts, the engine runs them, and a
cast or counter that went past a lifted bound says so in its payload
(:data:`LIFTED_BOUNDS_KEY`). Every other figure is held to the bounds as ever.

The outcome names, the success tiers' names and the labels are injected too,
as are the True skill's key and label, a working's school and every effect's
key and label: the engine's own vocabulary is only "borrows", "debt", "held",
"failed", whether an outcome is a Runaway, and True Magic's "working",
"craft", "True cast", "tier" and "familiarity".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, TypeVar

from .dice import MAX_DICE_COUNT, parse_dice_expression
from .spells import Spell, get_spell

# The combatant's pools are declared in .state; magic re-exports them so
# ``tarmar_engine.magic`` keeps naming them.
from .state import BODY_POOL as BODY_POOL
from .state import CASTER_POOLS as CASTER_POOLS
from .state import FATIGUE_POOL as FATIGUE_POOL
from .state import check_caster_pool

if TYPE_CHECKING:
    from .state import CombatantState, CraftState

#: The attributes a roll in this module may be made against.
MAGIC_ATTRIBUTES: frozenset[str] = frozenset({"INT", "WIS"})

#: The bounds a Game Master may lift for one figure (tarmar-studio #37), as
#: ``CombatantState.lifted_magic_bounds`` lists them: the caster's Channel...
CHANNEL = "channel"
#: ...Push's own cap on the extra mana one cast may take...
PUSH_CAP = "push_cap"
#: ...and the least and most a defender may commit to a counter.
COUNTER_BOUNDS = "counter_bounds"
#: Every bound a Game Master may lift.
LIFTABLE_BOUNDS: tuple[str, ...] = (CHANNEL, PUSH_CAP, COUNTER_BOUNDS)
#: The payload key naming the lifted bounds a cast or a counter went past,
#: written only when it went past one.
LIFTED_BOUNDS_KEY = "lifted_bounds"
#: The least a defender whose counter bounds are lifted may commit: nothing.
#: An engine reading, recorded in :mod:`.house_rulings` as
#: ``lifted_counter_minimum_is_zero``.
LIFTED_COUNTER_MINIMUM = 0

#: What the key of every working starts with...
WORKING_KEY_PREFIX = "working"
#: ...and what parts its prefix, its effect's key and its tier:
#: ``working:<effect key>:<tier>`` (:meth:`TrueMagicRules.working_key`).
WORKING_KEY_SEPARATOR = ":"


def is_working_key(key: str) -> bool:
    """Is this the key of a working, not of a catalog spell?

    The one predicate for "this spell is a working". It says nothing of
    whether any injected rules build the working
    (:meth:`TrueMagicRules.effect_and_tier`).
    """
    return key.startswith(WORKING_KEY_PREFIX + WORKING_KEY_SEPARATOR)


def casting_mana(spell: Spell, push_mana: int) -> int:
    """A casting's whole mana: the spell's cost and any pushed mana."""
    return spell.level + push_mana


def bound_lifted(combatant: CombatantState, bound: str) -> bool:
    """Has a Game Master lifted this bound for the combatant?

    Args:
        combatant: The figure, whose ``lifted_magic_bounds`` the consuming
            game filled from its record of the ruling.
        bound: One of :data:`LIFTABLE_BOUNDS`.

    Raises:
        ValueError: for a bound, asked or listed, outside
            :data:`LIFTABLE_BOUNDS`: a misspelt key would otherwise lift
            nothing, or hold a figure the ruling freed, without a word.
    """
    unknown = sorted({bound, *combatant.lifted_magic_bounds} - set(LIFTABLE_BOUNDS))
    if unknown:
        raise ValueError(
            f"{combatant.name}: no magic bound is called {unknown}; the bounds "
            f"a Game Master may lift are {list(LIFTABLE_BOUNDS)}"
        )
    return bound in combatant.lifted_magic_bounds


def attribute_score(caster: CombatantState, attribute: str) -> int:
    """The caster's score in one of :data:`MAGIC_ATTRIBUTES`.

    Raises:
        ValueError: for an attribute outside :data:`MAGIC_ATTRIBUTES`.
    """
    if attribute == "INT":
        return caster.intelligence
    if attribute == "WIS":
        return caster.wisdom
    raise ValueError(
        f"a magic roll is made against one of {sorted(MAGIC_ATTRIBUTES)}, "
        f"not {attribute!r}"
    )


class SpellEffectKind(StrEnum):
    """The kinds of rolled effect a magic bonus can add to.

    Engine vocabulary, from the spell catalog's own fields: a spell that
    rolls damage, and one that rolls healing. A continuing spell rolls
    nothing, so no bonus has anywhere to land.
    """

    DAMAGE = "damage"
    HEALING = "healing"


class CastingEnding(StrEnum):
    """How a casting whose mana was spent ended without going off."""

    #: A failed interruption roll.
    INTERRUPTED = "interrupted"
    #: The caster chose another action.
    ABANDONED = "abandoned"
    #: The craft action was refused, as a cast is where none may be made.
    BLOCKED = "blocked"
    #: A share the pool could not pay, with no borrowing.
    UNPAID = "unpaid"
    #: The caster fell unconscious.
    UNCONSCIOUS = "unconscious"
    #: A grapple took hold of the caster.
    GRAPPLED = "grappled"
    #: A defender's counter cancelled it.
    COUNTERED = "countered"
    #: A casting roll on a total that loses the mana.
    FAILED = "failed"
    #: A Control band whose spell does not take effect.
    STOPPED = "stopped"


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
class CastingFailureRules:
    """How a casting roll fails on its natural total, injected by the caller.

    The structure stays in the engine: a natural total at or above the floor
    fails the spell whatever the caster's attribute, some failing totals spend
    the spell's mana anyway, and one failing total starts a Runaway. The
    numbers arrive here.

    Attributes:
        fumble_floor: Natural totals (dice faces summed) at or above this fail
            the spell whatever the caster's attribute.
        mana_lost_totals: Failing natural totals on which the spell's mana is
            spent anyway. Every other failure keeps it. Each is at or above
            ``fumble_floor``, so whether a total spends the mana never
            depends on the caster's attribute.
        runaway_total: The natural total that starts a Runaway, or None when no
            failing total does. It is one of ``mana_lost_totals``: a
            Runaway's first drain is paid out of the casting's mana.
    """

    fumble_floor: int
    mana_lost_totals: frozenset[int] = frozenset()
    runaway_total: int | None = None

    def __post_init__(self) -> None:
        if self.fumble_floor < 1:
            raise ValueError(
                f"the fumble floor must be at least 1, not {self.fumble_floor}"
            )
        if self.runaway_total is not None and self.runaway_total < self.fumble_floor:
            raise ValueError(
                f"the Runaway total {self.runaway_total} is below the fumble "
                f"floor {self.fumble_floor}; a Runaway total must be a failing "
                "total"
            )
        below_floor = sorted(
            total for total in self.mana_lost_totals if total < self.fumble_floor
        )
        if below_floor:
            raise ValueError(
                f"mana-lost totals {below_floor} are below the fumble floor "
                f"{self.fumble_floor}; whether they spend the mana would depend "
                "on the caster's attribute"
            )
        if (
            self.runaway_total is not None
            and self.runaway_total not in self.mana_lost_totals
        ):
            raise ValueError(
                f"the Runaway total {self.runaway_total} is not a mana-lost "
                "total; a Runaway's first drain is paid out of the casting's mana"
            )


def casting_success_ceiling(
    attribute: int, casting_failure: CastingFailureRules | None
) -> int:
    """The highest natural total on which a casting roll succeeds.

    A casting roll succeeds at or under the effective attribute and, where
    casting failure rules are injected, under their fumble floor
    (tarmar-engine #46). The engine judges each roll by this and the policy
    estimates the chance of success from it (tarmar-engine #47), so the two
    cannot drift.

    Args:
        attribute: The effective attribute the roll is made under.
        casting_failure: The injected casting failure rules, or ``None``: the
            attribute alone bounds the roll.
    """
    if casting_failure is None:
        return attribute
    return min(attribute, casting_failure.fumble_floor - 1)


@dataclass(frozen=True)
class MarginBand:
    """A band of failure margins on a roll-under roll: the base of the
    Control Roll's and the debt roll's bands, and the interruption roll's
    band as it stands.

    The margin is how far the roll exceeded its target (1 = missed by one).

    Attributes:
        key: Stable identifier carried in event payloads.
        label: The band as narration prints it.
        is_runaway: Does landing here start a Runaway?
        lowest_margin: The smallest failure margin in the band.
        highest_margin: The largest, or ``None`` for an open-ended band.
    """

    key: str
    label: str
    is_runaway: bool
    lowest_margin: int
    highest_margin: int | None

    def covers(self, margin: int) -> bool:
        """Is this failure margin inside the band?"""
        if margin < self.lowest_margin:
            return False
        return self.highest_margin is None or margin <= self.highest_margin


BandType = TypeVar("BandType", bound=MarginBand)


def validate_margin_bands(bands: tuple[MarginBand, ...]) -> None:
    """Check a roll's failure bands: contiguous from margin 1, no gaps or
    overlaps, and the last, and only the last, open-ended, so every failure
    lands in exactly one.

    Args:
        bands: The bands, in order of their margins.

    Raises:
        ValueError: for bands that break any of those.
    """
    if not bands or bands[-1].highest_margin is not None:
        raise ValueError("the last failure band must be open-ended")
    expected_lowest = 1
    last_index = len(bands) - 1
    for index, band in enumerate(bands):
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
            raise ValueError(f"band {band.key!r} ends before it starts")
        expected_lowest = band.highest_margin + 1


def band_for_margin(bands: tuple[BandType, ...], margin: int) -> BandType:
    """The failure band a positive margin lands in.

    Args:
        bands: Bands that passed :func:`validate_margin_bands`.
        margin: How far the roll exceeded its target, at least 1.

    Raises:
        ValueError: when no band covers the margin (a margin below 1).
    """
    for band in bands:
        if band.covers(margin):
            return band
    raise ValueError(f"no failure band covers margin {margin}")


def runaway_band(bands: tuple[BandType, ...]) -> BandType | None:
    """The band an automatic Runaway uses: the first Runaway band, or
    ``None`` when no band is one."""
    return next((band for band in bands if band.is_runaway), None)


def _check_automatic_runaway_totals(
    automatic_runaway_totals: frozenset[int], bands: tuple[MarginBand, ...]
) -> None:
    """Refuse automatic Runaway totals on a roll none of whose bands is a
    Runaway: nothing would say what such a total does.

    Raises:
        ValueError: for totals with no Runaway band among ``bands``.
    """
    if automatic_runaway_totals and runaway_band(bands) is None:
        raise ValueError(
            "automatic Runaway totals need a Runaway band to say what one does"
        )


def failed_band(
    bands: tuple[BandType, ...],
    margin: int,
    *,
    automatic: bool,
    automatic_band: BandType | None,
) -> BandType | None:
    """The band a roll-under roll failed into, or ``None`` for one that held.

    The one pick the Control Roll, the debt roll and the interruption roll
    share: an automatic total lands in the band the roll names for it,
    whatever the margin, and any other failure in the band its margin picks.

    Args:
        bands: Bands that passed :func:`validate_margin_bands`.
        margin: How far the roll exceeded its target; 0 or less held.
        automatic: Is the natural total one of the roll's automatic totals?
        automatic_band: The band an automatic total lands in.

    Raises:
        ValueError: for an automatic total with no band to land in, which
            would otherwise read as a roll that held.
    """
    if automatic:
        if automatic_band is None:
            raise ValueError("an automatic total needs a band to land in")
        return automatic_band
    if margin <= 0:
        return None
    return band_for_margin(bands, margin)


@dataclass(frozen=True)
class ControlBand(MarginBand):
    """A band of failure margins on the Control Roll, and what it does.

    Attributes:
        spell_takes_effect: Does the spell still go off?
        push_bonus_applies: Does the pushed mana's effect bonus still apply?
    """

    spell_takes_effect: bool
    push_bonus_applies: bool


@dataclass(frozen=True)
class DebtBand(MarginBand):
    """A band of failure margins on the debt roll, and what it costs.

    The debt roll is made after the spell has resolved (house reading
    ``debt_roll_after_effect``), so a failure cannot take the spell back: it
    takes its cost from the caster and starts a Runaway when it is one.

    Attributes:
        cost_pool: The pool the cost is taken from, one of
            :data:`CASTER_POOLS`, or ``None`` for a band that takes nothing.
        cost_per_debt: Taken from that pool per point of debt owed at the
            roll. Not negative, and 0 when ``cost_pool`` is ``None``.
    """

    cost_pool: str | None
    cost_per_debt: int

    def __post_init__(self) -> None:
        check_caster_pool("cost_pool", self.cost_pool, allow_none=True)
        if self.cost_per_debt < 0:
            raise ValueError("a debt band's cost must not be negative")
        if self.cost_pool is None and self.cost_per_debt:
            raise ValueError("a debt band with a cost must name its pool")

    def cost(self, debt: int) -> int:
        """What the band takes from a caster owing ``debt`` at the roll."""
        return self.cost_per_debt * debt


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
        max_push_mana: A secondary cap on the extra mana one cast may take,
            the same for every caster, or ``None`` for none. Channel
            (:attr:`MagicRules.channel`) supersedes it as the bound on a
            casting: it is per caster and per spell, and it bounds the whole
            mana of the casting, cost included. Where both are injected, the
            tighter one holds.
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
        validate_margin_bands(self.failure_bands)
        _check_automatic_runaway_totals(
            self.automatic_runaway_totals, self.failure_bands
        )

    def band_for_margin(self, margin: int) -> ControlBand:
        """The failure band a positive margin lands in."""
        return band_for_margin(self.failure_bands, margin)

    def runaway_band(self) -> ControlBand | None:
        """The band an automatic Runaway uses: the first Runaway band."""
        return runaway_band(self.failure_bands)

    def mana_invested(self, spell: Spell, pushed_mana: int) -> int:
        """The mana the Control Roll's penalty counts: the whole casting
        (:func:`casting_mana`) when the spell's cost counts, else the pushed
        mana alone."""
        if self.base_cost_counts_as_invested:
            return casting_mana(spell, pushed_mana)
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

    def push_room(
        self,
        spell: Spell,
        mana: int,
        channel: int | None = None,
        *,
        cap_lifted: bool = False,
    ) -> int:
        """How much more than its cost a purse of ``mana`` may push into it.

        Args:
            spell: The spell pushed.
            mana: The caster's mana.
            channel: The Channel bounding the casting (cost and pushed mana
                together), or ``None`` when nothing does: no Channel rules
                injected, or Channel lifted for the caster
                (:meth:`MagicRules.channel_bound`).
            cap_lifted: Has a Game Master lifted :attr:`max_push_mana` for
                the caster (:data:`PUSH_CAP`)?
        """
        if not self.can_push(spell):
            return 0
        spendable = mana if channel is None else min(mana, channel)
        room = spendable - spell.level
        cap = self.cap(cap_lifted=cap_lifted)
        if cap is not None:
            room = min(room, cap)
        return max(0, room)

    def cap(self, *, cap_lifted: bool = False) -> int | None:
        """The most extra mana one cast may take under Push's own cap, or
        ``None`` for no cap: none injected, or lifted for the caster by a Game
        Master (:data:`PUSH_CAP`)."""
        return None if cap_lifted else self.max_push_mana

    def within_cap(self, push_mana: int, *, cap_lifted: bool = False) -> bool:
        """Is this much pushed mana within Push's own cap (:meth:`cap`)?"""
        cap = self.cap(cap_lifted=cap_lifted)
        return cap is None or push_mana <= cap

    def effect_bonus(self, spell: Spell, pushed_mana: int) -> int:
        """The bonus a push adds to this spell's rolled effect.

        Raises:
            ValueError: for a spell that cannot be pushed.
        """
        per_mana = self.bonus_per_mana(spell)
        if per_mana is None:
            raise ValueError(f"{spell.name} cannot be pushed under these rules")
        return per_mana * pushed_mana


#: The level a caster reads at in a skill the snapshot records no level for:
#: the weapon-skill map's precedent (``CombatantState.weapon_skills``). An
#: engine reading, recorded in :mod:`.house_rulings` as
#: ``channel_unrecorded_level``.
UNRECORDED_SKILL_LEVEL = 0


class ChannelUnclassifiedSpell(ValueError):
    """A spell the injected Channel rules give no kind of magic.

    The casting phase lets it propagate: a cast of such a spell is a
    configuration error. The menu catches it and withholds the spell's casts
    instead, naming why, so one unclassified spell cannot crash a turn.
    """

    def __init__(self, spell: Spell) -> None:
        self.spell = spell
        super().__init__(f"the Channel rules give {spell.name} no kind of magic")


@dataclass(frozen=True)
class ChannelDerivation:
    """How one kind of magic turns a recorded skill level into Channel.

    Channel = ``multiplier`` x the level + ``offset``, never below 0. Both
    coefficients are injected; the engine supplies neither.

    Attributes:
        source_skill: ``None``: the level is the caster's recorded level in
            the spell being cast (``CombatantState.spell_skill_levels``).
            A key: the level recorded for the general skill under that key
            (``CombatantState.skill_levels``), the same for every spell of
            this kind.
        multiplier: Channel per recorded level.
        offset: Added after the multiplication.
    """

    source_skill: str | None
    multiplier: int
    offset: int

    def __post_init__(self) -> None:
        if self.multiplier < 0:
            raise ValueError("a Channel multiplier must not be negative")
        if self.source_skill == "":
            raise ValueError("a Channel source skill must be a key or None")

    def skill_level(self, caster: CombatantState) -> int:
        """The caster's recorded level in this derivation's general skill.

        Raises:
            ValueError: for a per-spell derivation, which names no general
                skill.
        """
        if self.source_skill is None:
            raise ValueError(
                "a per-spell Channel derivation reads no general skill's level"
            )
        return caster.skill_levels.get(self.source_skill, UNRECORDED_SKILL_LEVEL)

    def recorded_level(self, caster: CombatantState, spell: Spell) -> int:
        """The level this derivation reads off the caster's snapshot."""
        if self.source_skill is None:
            return caster.spell_skill_levels.get(spell.key, UNRECORDED_SKILL_LEVEL)
        return self.skill_level(caster)

    def channel_at(self, level: int) -> int:
        """The Channel a recorded level gives."""
        return max(0, self.multiplier * level + self.offset)


@dataclass(frozen=True)
class ChannelRules:
    """Channel: the most mana one casting may take, per caster and spell.

    Channel bounds the whole mana of a casting: the spell's cost and any
    pushed mana together. Which rule derives it depends on the spell's kind
    of magic; the kinds, their names and their rules are all injected.

    Attributes:
        derivations: How Channel derives, by kind-of-magic key.
        kind_by_spell: The kind of magic each spell belongs to, by spell key.
        default_kind: The kind of a spell ``kind_by_spell`` does not name, or
            ``None``: such a spell has no kind, and asking its Channel raises
            :class:`ChannelUnclassifiedSpell`.

    The kind is read off the spell, not off the caster (house reading
    ``channel_kind_belongs_to_the_spell``), unless the cast names one: every
    method here takes a ``kind`` that stands in for the spell's own
    (:meth:`MagicRules.cast_kind`). The page leaves open whether a kind of
    magic belongs to the spell or to the way a caster casts it; the other
    reading would key the kind by caster and spell together.
    """

    derivations: Mapping[str, ChannelDerivation]
    kind_by_spell: Mapping[str, str] = field(default_factory=dict)
    default_kind: str | None = None

    def __post_init__(self) -> None:
        if not self.derivations:
            raise ValueError("Channel rules need at least one kind of magic")
        named = set(self.kind_by_spell.values())
        if self.default_kind is not None:
            named.add(self.default_kind)
        unknown = named - set(self.derivations)
        if unknown:
            raise ValueError(
                f"kinds of magic {sorted(unknown)} have no Channel derivation"
            )

    def kind_for(self, spell: Spell, kind: str | None = None) -> str:
        """The kind of magic a casting of the spell derives its Channel by.

        Args:
            spell: The spell cast.
            kind: The kind the cast names (:meth:`MagicRules.cast_kind`), or
                ``None``: the spell's own.

        Raises:
            ChannelUnclassifiedSpell: for a spell the rules give no kind,
                when the cast names none.
            ValueError: for a named kind with no derivation.
        """
        if kind is not None:
            if kind not in self.derivations:
                raise ValueError(
                    f"the kind of magic {kind!r} has no Channel derivation"
                )
            return kind
        own_kind = self.kind_by_spell.get(spell.key, self.default_kind)
        if own_kind is None:
            raise ChannelUnclassifiedSpell(spell)
        return own_kind

    def channel_for(
        self, caster: CombatantState, spell: Spell, kind: str | None = None
    ) -> int:
        """The caster's Channel for this spell: the most mana one casting of
        it may take, cost and pushed mana together.

        Args:
            caster: The caster.
            spell: The spell cast.
            kind: The kind the cast names, or ``None``: the spell's own
                (:meth:`kind_for`).

        Raises:
            ChannelUnclassifiedSpell: for a spell the rules give no kind.
        """
        derivation = self.derivations[self.kind_for(spell, kind)]
        return derivation.channel_at(derivation.recorded_level(caster, spell))

    def bound_for(
        self, caster: CombatantState, spell: Spell, kind: str | None = None
    ) -> int | None:
        """The caster's Channel for this spell as the bound on a casting from
        its own mana: ``None`` when a Game Master has lifted Channel for the
        caster (:data:`CHANNEL`).

        Args:
            caster: The caster.
            spell: The spell cast.
            kind: The kind the cast names, or ``None``: the spell's own
                (:meth:`kind_for`).

        Raises:
            ChannelUnclassifiedSpell: for a spell the rules give no kind,
                lifted or not: the cast still names the Channel it went past.
        """
        channel = self.channel_for(caster, spell, kind)
        return None if bound_lifted(caster, CHANNEL) else channel


@dataclass(frozen=True)
class BorrowingRules:
    """Borrowing's numbers: the debt roll, its penalty, and its failure
    bands.

    Attributes:
        debt_dice: The debt roll's dice expression.
        debt_attributes: The attributes, each one of :data:`MAGIC_ATTRIBUTES`,
            summed for the roll-under target before the penalty. At least one.
        penalty_per_debt: Subtracted from the target per point of debt owed
            once the cast is paid (and any refund has paid some back).
        failure_bands: Contiguous bands of failure margins starting at 1, the
            last open-ended, so every failure lands in exactly one; each
            says what a failure there costs.
        automatic_failure_totals: Natural debt-roll totals that fail whatever
            the target, and land in the last (open-ended) band whatever the
            margin.
        max_debt: The most a caster may owe in all, or ``None``: Channel
            alone bounds a borrowing casting, so Channel rules must be
            injected beside it (:class:`MagicRules` refuses otherwise).
        refunds_pay_debt_first: Does mana handed back (a tier's refund)
            reduce the debt before it refills the pool?
    """

    debt_dice: str
    debt_attributes: tuple[str, ...]
    penalty_per_debt: int
    failure_bands: tuple[DebtBand, ...]
    automatic_failure_totals: frozenset[int]
    max_debt: int | None = None
    refunds_pay_debt_first: bool = True

    def __post_init__(self) -> None:
        parse_dice_expression(self.debt_dice)  # raises on a bad expression
        if not self.debt_attributes:
            raise ValueError("the debt roll needs at least one attribute")
        unknown = sorted(set(self.debt_attributes) - MAGIC_ATTRIBUTES)
        if unknown:
            raise ValueError(
                f"debt_attributes must each be one of {sorted(MAGIC_ATTRIBUTES)}, "
                f"not {unknown}"
            )
        if self.penalty_per_debt < 0:
            raise ValueError("the debt penalty must not be negative")
        if self.max_debt is not None and self.max_debt < 0:
            raise ValueError("the debt cap must not be negative")
        validate_margin_bands(self.failure_bands)

    def band_for_margin(self, margin: int) -> DebtBand:
        """The failure band a positive margin lands in."""
        return band_for_margin(self.failure_bands, margin)

    def automatic_band(self) -> DebtBand:
        """The band an automatic failure total lands in: the last."""
        return self.failure_bands[-1]

    @property
    def versus(self) -> str:
        """The debt roll's attributes as a roll event names them."""
        return "+".join(self.debt_attributes)

    def attribute_total(self, caster: CombatantState) -> int:
        """The caster's debt attributes summed: the target before penalties."""
        return sum(
            attribute_score(caster, attribute) for attribute in self.debt_attributes
        )

    def debt_target(self, caster: CombatantState, debt: int) -> int:
        """The debt roll's target for a caster owing ``debt``, before the
        injury band and off-balance penalty the engine also takes off."""
        return self.attribute_total(caster) - self.penalty_per_debt * debt

    def borrowing_ceiling(self, mana: int, debt: int, channel: int | None) -> int:
        """The most mana one borrowing casting may take: the purse and the
        debt the cap still allows, within the caster's Channel for the spell.

        Args:
            mana: The caster's mana.
            debt: What the caster already owes.
            channel: The caster's Channel for the spell, or ``None`` with no
                Channel rules injected.

        Raises:
            ValueError: with neither a debt cap nor a Channel to bound it.
        """
        bounds = [] if channel is None else [channel]
        if self.max_debt is not None:
            bounds.append(mana + max(0, self.max_debt - debt))
        if not bounds:
            raise ValueError("a borrowing casting needs a debt cap or a Channel")
        return min(bounds)


@dataclass(frozen=True)
class RollUnderRule:
    """An injected roll-under roll: dice against one attribute.

    Attributes:
        dice: The roll's dice expression.
        attribute: One of :data:`MAGIC_ATTRIBUTES`, the target before the
            penalties the engine takes off.
    """

    dice: str
    attribute: str

    def __post_init__(self) -> None:
        parse_dice_expression(self.dice)  # raises on a bad expression
        if self.attribute not in MAGIC_ATTRIBUTES:
            raise ValueError(
                f"a roll-under attribute must be one of {sorted(MAGIC_ATTRIBUTES)}, "
                f"not {self.attribute!r}"
            )


@dataclass(frozen=True)
class RunawayRules:
    """Runaway's numbers: the drain, where it moves, and the anchor rolls.

    Attributes:
        drain_multiplier_of_cost: Each later turn the spell takes this many
            times its original cost (the mana the cast cost, pushed mana
            included)...
        drain_flat: ...plus this. Neither negative, and not both 0.
        overflow_pool: The pool drained once the mana is gone, one of
            :data:`CASTER_POOLS`. It may go negative, as the pools
            already may.
        death_multiplier_of_max: The caster dies when the overflow pool
            reaches this many times its maximum below zero, or ``None``: the
            drain sets no death line of its own.
        self_anchor: The conscious caster's roll to anchor the spell again.
        helper_anchor: Another mage's roll to anchor it for the caster.
        helper_cost_multiplier_of_cost: What the helper pays, in mana, as a
            multiple of the original cost, whatever the roll's outcome.
        helper_failure_catches_helper: Does a failed helper roll start a
            Runaway of the same spell on the helper?
        effect_bonus_per_turn: The growth of the spell's rolled effect per
            turn it ran, for the caller's narration (:meth:`effect_bonus`);
            0 for none.
    """

    drain_multiplier_of_cost: int
    drain_flat: int
    overflow_pool: str
    death_multiplier_of_max: int | None
    self_anchor: RollUnderRule
    helper_anchor: RollUnderRule
    helper_cost_multiplier_of_cost: int
    helper_failure_catches_helper: bool
    effect_bonus_per_turn: int

    def __post_init__(self) -> None:
        check_caster_pool("overflow_pool", self.overflow_pool)
        multipliers = {
            "drain_multiplier_of_cost": self.drain_multiplier_of_cost,
            "drain_flat": self.drain_flat,
            "helper_cost_multiplier_of_cost": self.helper_cost_multiplier_of_cost,
            "death_multiplier_of_max": self.death_multiplier_of_max or 0,
        }
        negative = sorted(name for name, value in multipliers.items() if value < 0)
        if negative:
            raise ValueError(f"{negative} must not be negative")
        if self.drain_multiplier_of_cost == 0 and self.drain_flat == 0:
            raise ValueError("a Runaway must drain something each turn")

    def drain(self, original_cost: int) -> int:
        """What the spell takes each later turn."""
        return self.drain_multiplier_of_cost * original_cost + self.drain_flat

    def helper_cost(self, original_cost: int) -> int:
        """What another mage pays to try to anchor the spell."""
        return self.helper_cost_multiplier_of_cost * original_cost

    def overflow_maximum(self, caster: CombatantState) -> int:
        """The caster's maximum in the overflow pool."""
        return getattr(caster, CASTER_POOLS[self.overflow_pool])

    def death_line(self, caster: CombatantState) -> int | None:
        """The overflow pool's value at which the caster dies, or ``None``
        with no death line injected."""
        if self.death_multiplier_of_max is None:
            return None
        return -self.death_multiplier_of_max * self.overflow_maximum(caster)

    def past_death_line(self, caster: CombatantState) -> bool:
        """Has the overflow pool reached the death line? Never, with none."""
        line = self.death_line(caster)
        return line is not None and getattr(caster, self.overflow_pool) <= line

    def effect_bonus(self, turns_running: int) -> int:
        """The growth a spell that ran this many turns has earned."""
        return self.effect_bonus_per_turn * turns_running


@dataclass(frozen=True)
class CounterspellRules:
    """Counterspell's numbers: the opposed roll, its bonus and its tie, and
    the bounds on what a defender commits.

    Attributes:
        opposed_dice: The dice both sides roll.
        opposed_attribute: One of :data:`MAGIC_ATTRIBUTES`; each side adds
            its own score.
        mana_per_bonus: A side's total gains 1 per this much mana invested:
            the caster's whole casting cost, pushed mana included, and the
            defender's commitment. Greater than 0.
        ties_go_to_caster: Does an equal total let the spell through?
        defender_min_mana: The least a defender may commit; not negative.
        defender_max_mana: The most a defender may commit, or ``None``: the
            purse alone bounds it. Channel does not (an engine reading: the
            defender casts no spell of its own, so no spell's kind of magic
            names a Channel to apply).
        familiarity_per_level: Added to each side's total per level of its
            familiarity with the spell cast; not negative. 0: familiarity
            counts for nothing.
    """

    opposed_dice: str
    opposed_attribute: str
    mana_per_bonus: int
    ties_go_to_caster: bool
    defender_min_mana: int
    defender_max_mana: int | None = None
    familiarity_per_level: int = 0

    def __post_init__(self) -> None:
        parse_dice_expression(self.opposed_dice)  # raises on a bad expression
        if self.opposed_attribute not in MAGIC_ATTRIBUTES:
            raise ValueError(
                f"opposed_attribute must be one of {sorted(MAGIC_ATTRIBUTES)}, "
                f"not {self.opposed_attribute!r}"
            )
        if self.mana_per_bonus <= 0:
            raise ValueError("the mana per bonus step must be greater than 0")
        if self.defender_min_mana < 0:
            raise ValueError("a defender's least commitment must not be negative")
        if (
            self.defender_max_mana is not None
            and self.defender_max_mana < self.defender_min_mana
        ):
            raise ValueError("a defender's most commitment is below its least")
        if self.familiarity_per_level < 0:
            raise ValueError("the familiarity bonus per level must not be negative")

    def bonus_for(self, mana_invested: int) -> int:
        """The opposed roll's bonus for this much mana invested."""
        return mana_invested // self.mana_per_bonus

    def bounds(self, *, bounds_lifted: bool = False) -> tuple[int, int | None]:
        """The least and most a defender may commit, the most ``None`` when
        the purse alone bounds it.

        Args:
            bounds_lifted: Has a Game Master lifted the commitment bounds for
                the defender (:data:`COUNTER_BOUNDS`)? Then from
                :data:`LIFTED_COUNTER_MINIMUM` to the whole purse.
        """
        if bounds_lifted:
            return LIFTED_COUNTER_MINIMUM, None
        return self.defender_min_mana, self.defender_max_mana

    def allows(self, mana_committed: int, *, bounds_lifted: bool = False) -> bool:
        """May a defender commit this much, its purse aside (:meth:`bounds`)?"""
        least, most = self.bounds(bounds_lifted=bounds_lifted)
        return least <= mana_committed and (most is None or mana_committed <= most)

    def commitments(self, mana: int, *, bounds_lifted: bool = False) -> range:
        """Every commitment a defender with ``mana`` may make within
        :meth:`bounds`, least first; empty when it cannot afford the least."""
        least, most = self.bounds(bounds_lifted=bounds_lifted)
        return range(least, (mana if most is None else min(mana, most)) + 1)

    def lifted_bounds_passed(self, mana_committed: int) -> tuple[str, ...]:
        """The bounds a commitment goes past that only a Game Master's
        lifting allows: :data:`COUNTER_BOUNDS` when it lies outside the
        injected least and most, else nothing. The one answer the engine's
        ``counter_declared`` payload and the menu's counter options give
        (:data:`LIFTED_BOUNDS_KEY`). It says nothing of whether the defender
        may commit it (:meth:`allows`)."""
        return () if self.allows(mana_committed) else (COUNTER_BOUNDS,)

    def caster_gets_through(self, caster_total: int, defender_total: int) -> bool:
        """Does the caster's total beat the defender's, by the tie rule?"""
        if caster_total == defender_total:
            return self.ties_go_to_caster
        return caster_total > defender_total


@dataclass(frozen=True)
class WorkingEffect:
    """One effect the injected rules let a working have, at any tier.

    Attributes:
        key: Stable identifier, and the middle part of a built working's key
            (:meth:`TrueMagicRules.working_key`). Not empty, and without
            :data:`WORKING_KEY_SEPARATOR`.
        label: The effect as the menu and narration print it.
        kind: The rolled effect the engine applies, or ``None`` for a
            narrated effect: the engine applies nothing and the Game Master
            describes what the working does.
        dice_per_tier: The dice rolled once per tier of the working. Required
            for a rolled effect, optional for a narrated one.
        pool: The pool damaged or healed, one of :data:`CASTER_POOLS`.
        targeted: Does the working need the aim roll, as a targeted spell
            does? A narrated effect cannot be.
        ignores_armour: Does its damage skip armour? A narrated effect cannot.
        needs_grant: May the working be crafted only by a figure a Game
            Master has named it for?
    """

    key: str
    label: str
    kind: SpellEffectKind | None
    dice_per_tier: str | None
    pool: str = FATIGUE_POOL
    targeted: bool = False
    ignores_armour: bool = False
    needs_grant: bool = False

    def __post_init__(self) -> None:
        if not self.key or WORKING_KEY_SEPARATOR in self.key:
            raise ValueError(
                f"a working effect's key must not be empty or hold "
                f"{WORKING_KEY_SEPARATOR!r}, as {self.key!r} does"
            )
        if self.dice_per_tier is not None:
            parse_dice_expression(self.dice_per_tier)  # raises on a bad expression
        check_caster_pool(f"working effect {self.key!r}'s pool", self.pool)
        if self.kind is not None:
            if self.dice_per_tier is None:
                raise ValueError(
                    f"working effect {self.key!r} rolls {self.kind.value} and "
                    "names no dice per tier"
                )
        elif self.targeted or self.ignores_armour:
            raise ValueError(
                f"working effect {self.key!r} is narrated: the engine applies "
                "nothing, so it can neither be targeted nor ignore armour"
            )

    def highest_buildable_tier(self) -> int | None:
        """The highest tier a working of this effect can be built at: the
        last whose dice (:meth:`dice_at`) a dice expression may hold, or
        ``None`` for an effect that names no dice, which builds at any tier.

        It bounds what the rules can build, whoever the caster: the menu
        lists no tier past it and :meth:`check_tier` refuses one.
        """
        if self.dice_per_tier is None:
            return None
        count, _sides, _modifier = parse_dice_expression(self.dice_per_tier)
        return MAX_DICE_COUNT // count

    def check_tier(self, tier: int) -> None:
        """Refuse a tier no working of this effect can be built at.

        Raises:
            ValueError: for a tier below 1, or past
                :meth:`highest_buildable_tier`.
        """
        _check_tier(tier)
        highest = self.highest_buildable_tier()
        if highest is not None and tier > highest:
            raise ValueError(
                f"working effect {self.key!r} rolls {self.dice_per_tier} per "
                f"tier and cannot be built past tier {highest}, as {tier} is"
            )

    def dice_at(self, tier: int) -> str | None:
        """The dice a working of this effect rolls at a tier: the per-tier
        dice with their count and modifier each multiplied by the tier, or
        ``None`` for an effect that names no dice.

        Raises:
            ValueError: for a tier :meth:`check_tier` refuses.
        """
        self.check_tier(tier)
        if self.dice_per_tier is None:
            return None
        count, sides, modifier = parse_dice_expression(self.dice_per_tier)
        tier_modifier = modifier * tier
        dice = f"{count * tier}d{sides}"
        if tier_modifier:
            dice += f"{tier_modifier:+d}"
        return dice


def _check_tier(tier: int) -> None:
    """Refuse a working's tier below 1.

    Raises:
        ValueError: for such a tier.
    """
    if tier < 1:
        raise ValueError(f"a working's tier must be at least 1, not {tier}")


@dataclass(frozen=True)
class InterruptionRules:
    """The roll a crafting caster makes when damage lands on it, to keep the
    working it is crafting.

    Attributes:
        roll: The roll-under roll: its dice and the attribute it is under.
        penalty_per_mana: Subtracted from the target per point of mana the
            craft has gathered; not negative.
        failure_bands: Contiguous bands of failure margins starting at 1, the
            last open-ended. Every band loses the working; ``is_runaway``
            says whether it also runs away.
        automatic_runaway_totals: Natural totals that are a Runaway whatever
            the target. They need a Runaway band, as Push's do.
    """

    roll: RollUnderRule
    penalty_per_mana: int
    failure_bands: tuple[MarginBand, ...]
    automatic_runaway_totals: frozenset[int]

    def __post_init__(self) -> None:
        if self.penalty_per_mana < 0:
            raise ValueError("the interruption penalty must not be negative")
        validate_margin_bands(self.failure_bands)
        _check_automatic_runaway_totals(
            self.automatic_runaway_totals, self.failure_bands
        )


@dataclass(frozen=True)
class TrueMagicRules:
    """True Magic's numbers: the workings a caster may craft over several
    turns, and what a True cast or a working drains from its caster.

    A working is a :class:`~tarmar_engine.spells.Spell` these rules build
    (:meth:`working`), not a catalog entry: one per injected effect and tier.
    The True skill is a general skill; its key lives in the Channel
    derivation ``channel_kind`` names and nowhere else
    (:class:`MagicRules` checks the two agree).

    Attributes:
        channel_kind: The kind of magic a True cast or a working derives its
            Channel by: a key of :attr:`ChannelRules.derivations` whose
            ``source_skill`` is the True skill.
        skill_label: The True skill as narration prints it.
        school: The ``school`` of a built working.
        effects: The effects a working may have. At least one, keys unique.
        tiers_per_level: A caster's highest tier is this many times its level
            in the True skill; at least 1.
        turns_per_tier: The craft turns a working takes per tier; at least 1.
        roll_attribute: The attribute a working's casting roll is under, one
            of :data:`MAGIC_ATTRIBUTES`.
        roll_bonus_per_level: Added to that roll's target per level in the
            True skill; not negative.
        interruption: The roll a crafting caster makes when damage lands.
        drain_pool: The pool a drain is taken from, one of
            :data:`CASTER_POOLS`.
        drain_per_mana: Taken per point of drained mana by a casting that
            goes off; not negative.
        lost_drain_per_mana: Taken per point of drained mana by a casting
            that ended otherwise, by how it ended. An ending with no entry
            drains nothing. No value is negative. :class:`CastingEnding` is a
            string enumeration, so a key may be written as the member or as
            its value; any other key is refused.
    """

    channel_kind: str
    skill_label: str
    school: str
    effects: tuple[WorkingEffect, ...]
    tiers_per_level: int
    turns_per_tier: int
    roll_attribute: str
    roll_bonus_per_level: int
    interruption: InterruptionRules
    drain_pool: str
    drain_per_mana: int
    lost_drain_per_mana: Mapping[CastingEnding | str, int]

    def __post_init__(self) -> None:
        if not self.effects:
            raise ValueError("True Magic rules need at least one working effect")
        effect_keys = [effect.key for effect in self.effects]
        repeated = sorted({key for key in effect_keys if effect_keys.count(key) > 1})
        if repeated:
            raise ValueError(f"working effect keys {repeated} are repeated")
        if self.tiers_per_level < 1:
            raise ValueError("the tiers per level must be at least 1")
        if self.turns_per_tier < 1:
            raise ValueError("the craft turns per tier must be at least 1")
        if self.roll_attribute not in MAGIC_ATTRIBUTES:
            raise ValueError(
                f"roll_attribute must be one of {sorted(MAGIC_ATTRIBUTES)}, "
                f"not {self.roll_attribute!r}"
            )
        if self.roll_bonus_per_level < 0:
            raise ValueError("a working's roll bonus per level must not be negative")
        check_caster_pool("drain_pool", self.drain_pool)
        unknown_endings = sorted(
            str(ending) for ending in set(self.lost_drain_per_mana) - set(CastingEnding)
        )
        if unknown_endings:
            # A misspelt ending would otherwise drain nothing, without a word.
            raise ValueError(
                f"no casting ending is called {unknown_endings}; the endings are "
                f"{[ending.value for ending in CastingEnding]}"
            )
        if self.drain_per_mana < 0 or any(
            drain < 0 for drain in self.lost_drain_per_mana.values()
        ):
            raise ValueError("a drain per mana must not be negative")
        for effect in self.effects:
            # Every effect must build: Spell refuses a school that needs an
            # element, which no working names.
            self.working(effect.key, 1)

    def _effect(self, effect_key: str) -> WorkingEffect:
        """The injected effect with this key.

        Raises:
            KeyError: for a key no injected effect carries.
        """
        for effect in self.effects:
            if effect.key == effect_key:
                return effect
        raise KeyError(f"these rules build no working of the effect {effect_key!r}")

    def working_key(self, effect_key: str, tier: int) -> str:
        """The key of the working of an effect at a tier:
        ``working:<effect key>:<tier>``.

        Raises:
            KeyError: for an effect these rules do not carry.
            ValueError: for a tier the effect cannot be built at
                (:meth:`WorkingEffect.check_tier`).
        """
        self._effect(effect_key).check_tier(tier)
        return WORKING_KEY_SEPARATOR.join((WORKING_KEY_PREFIX, effect_key, str(tier)))

    def effect_and_tier(self, key: str) -> tuple[WorkingEffect, int]:
        """The effect and the tier a working's key names.

        Raises:
            KeyError: for a key that is not a working these rules build: not
                a working's key at all, an effect they do not carry, a tier
                the effect cannot be built at, or one not written as
                :meth:`working_key` writes it. Never a ``ValueError``: a key
                is read from state, and one these rules do not build is
                unknown, as a catalog key the catalog does not hold is.
        """
        refusal = f"{key!r} is not a working these rules build"
        prefix, _, effect_and_tier_text = key.partition(WORKING_KEY_SEPARATOR)
        effect_key, _, tier_text = effect_and_tier_text.rpartition(
            WORKING_KEY_SEPARATOR
        )
        if prefix == WORKING_KEY_PREFIX and tier_text.isdecimal():
            effect = self._effect(effect_key)
            try:
                tier = int(tier_text)
                written_key = self.working_key(effect_key, tier)
            except ValueError as unbuildable_tier:
                raise KeyError(refusal) from unbuildable_tier
            if written_key == key:
                return effect, tier
        raise KeyError(refusal)

    def working(self, effect_key: str, tier: int) -> Spell:
        """Build the working of an effect at a tier.

        Its level is its tier, so :func:`casting_mana` gives the tier and any
        pushed mana (house reading ``working_mana_is_its_tier``); its casting
        roll is under :attr:`roll_attribute`; it
        rolls :meth:`WorkingEffect.dice_at` for a rolled effect and nothing
        for a narrated one; and it never continues.

        Raises:
            KeyError: for an effect these rules do not carry.
            ValueError: for a tier the effect cannot be built at
                (:meth:`WorkingEffect.check_tier`).
        """
        effect = self._effect(effect_key)
        return Spell(
            key=self.working_key(effect_key, tier),
            name=f"{effect.label} (tier {tier})",
            school=self.school,
            level=tier,
            attribute=self.roll_attribute,
            targeted=effect.targeted,
            damage=None if effect.kind is None else effect.dice_at(tier),
            damage_pool=effect.pool,
            ignores_armour=effect.ignores_armour,
            heals=effect.kind is SpellEffectKind.HEALING,
        )

    def working_from_key(self, key: str) -> Spell:
        """Build the working a key names: :meth:`working` of the key's
        :meth:`effect_and_tier`. The one way a working's key becomes its
        spell.

        Raises:
            KeyError: for a key that is not a working these rules build.
        """
        effect, tier = self.effect_and_tier(key)
        return self.working(effect.key, tier)

    def tier_cap(self, true_level: int) -> int:
        """The highest tier a caster at this level in the True skill may
        craft."""
        return self.tiers_per_level * true_level

    def turns_for(self, tier: int) -> int:
        """The craft turns a working of this tier takes: at least 1.

        Raises:
            ValueError: for a tier below 1, which would take no turns to
                gather its mana over.
        """
        _check_tier(tier)
        return self.turns_per_tier * tier

    def gathered_after(self, mana: int, turns: int, turns_done: int) -> int:
        """The mana a craft has gathered after some of its turns: the whole
        mana at an even pace, rounded down at each turn, so no two shares
        differ by more than 1 and the last turn brings the whole (house
        reading ``craft_gathers_even_shares``).

        Args:
            mana: The working's whole mana, its tier and any pushed mana.
            turns: The craft turns the working takes (:meth:`turns_for`).
            turns_done: The craft turns taken so far.

        Raises:
            ValueError: for a working that takes no turns, or for turns
                taken that are fewer than none or more than it takes.
        """
        if turns < 1 or not 0 <= turns_done <= turns:
            raise ValueError(
                f"a craft of {turns} turns cannot have taken {turns_done} of them"
            )
        return mana * turns_done // turns

    def gathered(self, craft: CraftState) -> int:
        """The mana a craft has gathered so far: :meth:`gathered_after` its
        ``turns_done``, for the working its key names with its pushed mana.

        The craft stores no running total; this is the one place it is
        worked out.

        Raises:
            KeyError: for a craft whose key is not a working these rules
                build.
            ValueError: for a craft that pushes less than no mana, or whose
                turns taken :meth:`gathered_after` refuses.
        """
        if craft.push_mana < 0:
            raise ValueError(f"a craft cannot push {craft.push_mana} mana")
        working = self.working_from_key(craft.spell_key)
        return self.gathered_after(
            casting_mana(working, craft.push_mana),
            self.turns_for(working.level),
            craft.turns_done,
        )

    def drain(self, drained_mana: int, ending: CastingEnding | None) -> int:
        """What a True cast or a working drains from its caster.

        Args:
            drained_mana: The mana the drain counts.
            ending: How the casting ended without going off, or ``None``: it
                went off.
        """
        if ending is None:
            return self.drain_per_mana * drained_mana
        return self.lost_drain_per_mana.get(ending, 0) * drained_mana


@dataclass(frozen=True)
class MagicRules:
    """Every magic-mechanics number the engine needs, injected by the caller.

    Attributes:
        casting_success_tiers: The special results of a successful casting
            roll, by natural total. Empty: no tiers.
        push: Push's numbers, or ``None`` when Push is not offered.
        channel: Channel's derivation, or ``None``: no casting is bounded
            but by the purse (and Push's own optional cap).
        borrowing: Borrowing's numbers, or ``None`` when no caster may borrow.
        runaway: Runaway's numbers, or ``None``: a Runaway is flagged where it
            starts and has no later turns.
        counterspell: Counterspell's numbers, or ``None``: no spell may be
            countered.
        casting_failure: How a casting roll fails on its natural total, or
            ``None``: no natural total fails on its own (the roll is judged
            against the attribute alone), no failure costs mana, and no cast
            starts a Runaway.
        true_magic: True Magic's numbers, or ``None``: no caster may True
            cast or craft a working. They need Channel rules, whose
            derivation for their ``channel_kind`` names the True skill.
    """

    casting_success_tiers: tuple[CastingSuccessTier, ...] = ()
    push: PushRules | None = None
    channel: ChannelRules | None = None
    borrowing: BorrowingRules | None = None
    runaway: RunawayRules | None = None
    counterspell: CounterspellRules | None = None
    casting_failure: CastingFailureRules | None = None
    true_magic: TrueMagicRules | None = None

    def __post_init__(self) -> None:
        seen: set[int] = set()
        for tier in self.casting_success_tiers:
            overlap = seen & tier.natural_totals
            if overlap:
                raise ValueError(
                    f"tier {tier.key!r} repeats natural totals {sorted(overlap)}"
                )
            seen |= tier.natural_totals
        if (
            self.borrowing is not None
            and self.borrowing.max_debt is None
            and self.channel is None
        ):
            raise ValueError(
                "Borrowing with no debt cap needs Channel rules to bound a casting"
            )
        if self.true_magic is not None:
            self._true_derivation(self.true_magic)

    def _true_derivation(self, true_magic: TrueMagicRules) -> ChannelDerivation:
        """The Channel derivation the True Magic rules name: the one place
        the True skill's key is written.

        Raises:
            ValueError: with no Channel rules, for a ``channel_kind`` with no
                derivation, or for one that reads no general skill.
        """
        if self.channel is None:
            raise ValueError(
                "True Magic rules need Channel rules: their channel_kind names "
                "the derivation that holds the True skill"
            )
        derivation = self.channel.derivations.get(true_magic.channel_kind)
        if derivation is None:
            raise ValueError(
                f"the True Magic channel_kind {true_magic.channel_kind!r} has no "
                "Channel derivation"
            )
        if derivation.source_skill is None:
            raise ValueError(
                f"the True Magic channel_kind {true_magic.channel_kind!r} derives "
                "Channel per spell; it must name the True skill as its source"
            )
        return derivation

    def true_level(self, caster: CombatantState) -> int:
        """The caster's level in the True skill; 0 with no True Magic rules.

        "May this caster True cast or craft a working" is
        ``true_level(caster) > 0``, asked no other way.
        """
        if self.true_magic is None:
            return 0
        return self._true_derivation(self.true_magic).skill_level(caster)

    def tier_cap(self, caster: CombatantState) -> int | None:
        """The highest tier of working the caster may craft, or ``None`` when
        a Game Master has lifted Channel for the caster (:data:`CHANNEL`),
        which lifts the cap with it (house reading
        ``channel_lift_lifts_the_tier_cap``). 0 with no True Magic rules,
        lifted or not, as :meth:`true_level` is.
        """
        if self.true_magic is not None and bound_lifted(caster, CHANNEL):
            return None
        return self._unlifted_tier_cap(caster)

    def _unlifted_tier_cap(self, caster: CombatantState) -> int:
        """The tier cap the caster's level in the True skill gives, whether
        or not a Game Master has lifted it; 0 with no True Magic rules."""
        if self.true_magic is None:
            return 0
        return self.true_magic.tier_cap(self.true_level(caster))

    def _builds_working(self, key: str) -> bool:
        """Is the key a working the injected True Magic rules build? Never,
        with none injected."""
        if self.true_magic is None:
            return False
        try:
            self.true_magic.effect_and_tier(key)
        except KeyError:
            return False
        return True

    def granted(self, caster: CombatantState, key: str) -> bool:
        """Has a Game Master named this working for the caster?

        Args:
            caster: The figure, whose ``granted_workings`` the consuming game
                filled from its record of the ruling.
            key: The working's key.

        Raises:
            ValueError: for a key, asked or listed, that is not a working
                these rules build, as :func:`bound_lifted` refuses a misspelt
                bound: a bad grant would otherwise offer nothing, without a
                word.
        """
        unbuilt = sorted(
            listed_key
            for listed_key in {key, *caster.granted_workings}
            if not self._builds_working(listed_key)
        )
        if unbuilt:
            raise ValueError(
                f"{caster.name}: the True Magic rules build no working called {unbuilt}"
            )
        return key in caster.granted_workings

    def cast_kind(self, spell: Spell, *, true_cast: bool = False) -> str | None:
        """The kind of magic a casting names for its Channel: the True kind
        for a True cast or a working (:func:`is_working_key`), otherwise
        ``None``, meaning the spell's own (:meth:`ChannelRules.kind_for`).

        The one place a cast's ``true_cast`` flag becomes a kind; every
        Channel method here takes the result as its ``kind``. Given no kind,
        each asks this for the spell, so a working derives by the True kind
        whether or not its caller named it. Nothing but the flag tells a
        True cast of a catalog spell from a recipe cast: that caller must
        pass the kind.

        Raises:
            ValueError: for a True cast or a working with no True Magic
                rules injected, which has no kind to name.
        """
        if not true_cast and not is_working_key(spell.key):
            return None
        if self.true_magic is None:
            raise ValueError(
                f"{spell.name} is a True cast or a working, and no True Magic "
                "rules are injected to name its kind of magic"
            )
        return self.true_magic.channel_kind

    def _channel_kind(self, spell: Spell, kind: str | None) -> str | None:
        """The kind a Channel method here derives by: the one its caller
        named, else what :meth:`cast_kind` gives the spell unasked, which is
        the True kind for a working and ``None`` (the spell's own) for any
        other."""
        return kind if kind is not None else self.cast_kind(spell)

    def channel_for(
        self, caster: CombatantState, spell: Spell, kind: str | None = None
    ) -> int | None:
        """The caster's Channel for this spell, or ``None`` with no Channel
        rules injected (no bound but the purse).

        Args:
            caster: The caster.
            spell: The spell cast.
            kind: The kind the cast names (:meth:`cast_kind`), or ``None``:
                the True kind for a working, otherwise the spell's own.

        Raises:
            ChannelUnclassifiedSpell: for a spell the rules give no kind.
            ValueError: for a working with no True Magic rules injected.
        """
        kind = self._channel_kind(spell, kind)
        if self.channel is None:
            return None
        return self.channel.channel_for(caster, spell, kind)

    def channel_bound(
        self,
        caster: CombatantState,
        spell: Spell,
        *,
        borrowing: bool = False,
        kind: str | None = None,
    ) -> int | None:
        """The most mana Channel lets this caster's casting of the spell take,
        or ``None`` when Channel bounds nothing but the purse (and Push's own
        cap) does.

        ``None`` with no Channel rules injected, or with Channel lifted for
        the caster by a Game Master (:meth:`ChannelRules.bound_for`). A
        borrowing casting with Channel lifted is bounded by the injected debt
        cap where there is one; where Borrowing has none, Channel bounds only
        the shortfall, so the casting may take the caster's mana plus its
        Channel (house reading ``lifted_channel_bounds_the_shortfall``).

        Args:
            caster: The caster.
            spell: The spell cast.
            borrowing: Does the casting borrow (cost past the purse)?
            kind: The kind the cast names (:meth:`cast_kind`), or ``None``:
                the True kind for a working, otherwise the spell's own.

        Raises:
            ChannelUnclassifiedSpell: for a spell the rules give no kind.
            ValueError: for a working with no True Magic rules injected.
        """
        kind = self._channel_kind(spell, kind)
        if self.channel is None:
            return None
        bound = self.channel.bound_for(caster, spell, kind)
        if (
            bound is not None
            or not borrowing
            or self.borrowing is None
            or self.borrowing.max_debt is not None
        ):
            return bound
        return caster.mana + self.channel.channel_for(caster, spell, kind)

    def cast_within_channel(
        self,
        caster: CombatantState,
        spell: Spell,
        push_mana: int = 0,
        *,
        borrowing: bool = False,
        kind: str | None = None,
    ) -> bool:
        """Does Channel let this caster cast the spell with ``push_mana``
        pushed: the casting's whole mana (:func:`casting_mana`) within
        :meth:`channel_bound`?

        The one Channel test the casting phase refuses by and the menu
        withholds by, public so a caller asks it rather than restating it.
        ``True`` with no Channel rules injected, and for a caster a Game
        Master has lifted Channel for, except a borrowing casting where
        Borrowing has no debt cap: that one is held to the caster's mana plus
        its Channel. It says nothing of the purse, Push's own cap or the debt
        cap, which are the caller's checks (the debt cap through
        :meth:`BorrowingRules.borrowing_ceiling`; ``cast_spell`` refuses a
        borrowing past it).

        Args:
            caster: The caster.
            spell: The spell cast.
            push_mana: The mana pushed past the spell's cost.
            borrowing: Does the casting borrow (cost past the purse)?
            kind: The kind the cast names (:meth:`cast_kind`), or ``None``:
                the True kind for a working, otherwise the spell's own.

        Raises:
            ChannelUnclassifiedSpell: for a spell the Channel rules give no
                kind, lifted or not, as :meth:`channel_for` does.
        """
        bound = self.channel_bound(caster, spell, borrowing=borrowing, kind=kind)
        return bound is None or casting_mana(spell, push_mana) <= bound

    def lifted_bounds_passed(
        self,
        caster: CombatantState,
        spell: Spell,
        push_mana: int = 0,
        *,
        kind: str | None = None,
    ) -> tuple[str, ...]:
        """The bounds a casting of the spell with ``push_mana`` pushed goes
        past that only a Game Master's lifting allows, in order: Push's own
        cap (:data:`PUSH_CAP`) when the push is past it, then the caster's
        Channel (:data:`CHANNEL`) when the casting's whole mana is past it,
        or when the spell is a working whose tier is past the caster's tier
        cap: lifting Channel lifts that cap too, so no bound of its own names
        it. ``kind`` is the kind the cast names (:meth:`cast_kind`), or
        ``None``: the True kind for a working, otherwise the spell's own.

        The one answer the engine's cast payload and the menu's cast options
        give (:data:`LIFTED_BOUNDS_KEY`), so a caller reads it rather than
        recomputing it. It says nothing of whether the casting is allowed:
        the engine refuses one past a bound the caster does not have lifted.

        Raises:
            ChannelUnclassifiedSpell: for a spell the Channel rules give no
                kind.
        """
        passed: list[str] = []
        if push_mana and self.push is not None and not self.push.within_cap(push_mana):
            passed.append(PUSH_CAP)
        channel = self.channel_for(caster, spell, kind)
        past_channel = channel is not None and casting_mana(spell, push_mana) > channel
        if past_channel or self._past_tier_cap(caster, spell):
            passed.append(CHANNEL)
        return tuple(passed)

    def _past_tier_cap(self, caster: CombatantState, spell: Spell) -> bool:
        """Is the spell a working whose tier is past what the caster's level
        in the True skill allows, whether or not the cap is lifted?"""
        return is_working_key(spell.key) and spell.level > self._unlifted_tier_cap(
            caster
        )

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


def spell_for(key: str, rules: MagicRules | None) -> Spell:
    """The spell a key names: a catalog spell, or a working the injected
    rules build.

    The one lookup for a spell key read from state or a candidate, where
    :func:`~tarmar_engine.spells.get_spell` alone would not know a working.

    Args:
        key: A catalog spell's key or a working's.
        rules: The injected magic rules, or ``None`` for none.

    Raises:
        KeyError: for a key the catalog does not hold, and for a working's
            key with no True Magic rules injected or one they do not build.
    """
    if not is_working_key(key):
        return get_spell(key)
    if rules is None or rules.true_magic is None:
        raise KeyError(f"{key!r} is a working, and no True Magic rules are injected")
    return rules.true_magic.working_from_key(key)
