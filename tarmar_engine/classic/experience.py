"""Classic Section IX "Experience" — ported from melee.

XP after a fight, traded for attributes.

A figure that survives combat earns experience points (XP). How many depends on
the *type* of combat and whether the enemy was, on average, stronger or weaker in
total attributes (ST+DX). Accumulated XP can be traded in — 100 XP buys one point
of basic ST or DX — up to a lifetime cap of 8 added points (Section IX, p.22).

The rule text these constants encode (Section IX, p.22):

  * Combat to the Death — "50 experience points (XP) to each survivor, or 100 if
    the enemy averaged more than 3 superior in ST+DX." (Losers die: no XP.)
  * Arena Combat — "Winners get 30 XP; defeated survivors get 20 XP (unless they
    ran away unhurt, in which case they lose 10 XP). If one side averaged 3 or
    more weaker in total attributes, survivors on that side get 10 extra XP each."
  * Practice Combat — "Those still on their feet when one side is eliminated get
    10 XP each. Others get nothing but bruises."
  * Spending — "A figure with 100 XP may 'trade them in' for one additional point
    added to either basic ST or basic DX. Up to 8 attribute points may be added."

These are pure helpers over :class:`~.figure.Figure`; they read and mutate a
figure's XP/attribute state but own no game-loop logic (that wiring lives in the
board views).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from .figure import Figure

# ---- Section IX XP table (p.22) --------------------------------------------
DEATH_SURVIVOR_XP = 50          # each survivor of Combat to the Death
DEATH_SUPERIOR_XP = 100         # ...or this if the enemy averaged >3 superior
ARENA_WINNER_XP = 30            # each surviving winner of Arena Combat
ARENA_DEFEATED_SURVIVOR_XP = 20  # each survivor on the losing side
ARENA_RAN_AWAY_UNHURT_XP = -10  # a defeated survivor who fled unhurt loses XP
ARENA_WEAKER_BONUS_XP = 10      # +10 to survivors whose side averaged >=3 weaker
PRACTICE_XP = 10                # each figure still on its feet at the end

# "more than 3 superior" (Death) and "3 or more weaker" (Arena) both pivot on a
# 3-point gap in average side ST+DX.
SUPERIOR_MARGIN = 3

# A figure drops out of Practice Combat once its ST falls to this or below (p.22).
PRACTICE_DROPOUT_ST = 3

# ---- Section IX spending rule (p.22) ---------------------------------------
XP_PER_ATTRIBUTE_POINT = 100
MAX_ADDED_ATTRIBUTE_POINTS = 8


class CombatType(StrEnum):
    """The three kinds of Melee combat, each with its own XP schedule (p.22)."""

    DEATH = "death"
    ARENA = "arena"
    PRACTICE = "practice"


class Attribute(StrEnum):
    """A basic attribute a figure may raise by trading in XP."""

    STRENGTH = "strength"
    DEXTERITY = "dexterity"


def _attribute_total(figure: Figure) -> int:
    """A figure's total combined attributes (ST+DX), the Section IX yardstick."""
    return figure.strength + figure.dexterity


def _average_attributes(figures: Sequence[Figure]) -> float:
    """Average ST+DX across ``figures`` (0.0 for an empty group)."""
    if not figures:
        return 0.0
    return sum(_attribute_total(figure) for figure in figures) / len(figures)


def _enemy_superiority(figures: Sequence[Figure], side: str) -> float:
    """How much stronger the enemy averages than ``side`` (their avg minus ours).

    Positive means the opposition is, on average, the stronger group in ST+DX.
    """
    own = [figure for figure in figures if figure.side == side]
    enemy = [figure for figure in figures if figure.side != side]
    return _average_attributes(enemy) - _average_attributes(own)


def _survived(figure: Figure) -> bool:
    """A figure that lives to gain experience (not killed). Unconscious figures
    survive — in the arena they may not be slain (p.22)."""
    return not figure.is_dead


def _death_xp(figure: Figure, figures: Sequence[Figure]) -> int:
    if not _survived(figure):
        return 0                                  # losers die; the dead earn nothing
    superior = _enemy_superiority(figures, figure.side) > SUPERIOR_MARGIN
    return DEATH_SUPERIOR_XP if superior else DEATH_SURVIVOR_XP


def _arena_xp(
    figure: Figure,
    figures: Sequence[Figure],
    winning_side: str | None,
    ran_away_unhurt: frozenset[str],
) -> int:
    if not _survived(figure):
        return 0
    weaker_bonus = (
        ARENA_WEAKER_BONUS_XP
        if _enemy_superiority(figures, figure.side) >= SUPERIOR_MARGIN
        else 0
    )
    if figure.side == winning_side:
        return ARENA_WINNER_XP + weaker_bonus
    if figure.uid in ran_away_unhurt:
        return ARENA_RAN_AWAY_UNHURT_XP           # fled unhurt: a flat penalty
    return ARENA_DEFEATED_SURVIVOR_XP + weaker_bonus


def _practice_xp(figure: Figure) -> int:
    """10 XP to a figure still on its feet; nothing to one that dropped out/died."""
    still_standing = not figure.is_dead and figure.current_st > PRACTICE_DROPOUT_ST
    return PRACTICE_XP if still_standing else 0


def award_experience(
    figures: Sequence[Figure],
    combat_type: CombatType,
    *,
    winning_side: str | None = None,
    ran_away_unhurt: Iterable[str] = (),
) -> dict[str, int]:
    """Award Section IX XP to every figure and bank it on ``figure.experience``.

    Returns a ``uid -> xp awarded`` map (the delta, which may be negative for an
    arena runaway). ``winning_side`` and ``ran_away_unhurt`` only matter for arena
    combat; for Death and Practice the outcome is read straight off the figures.
    """
    fled = frozenset(ran_away_unhurt)
    awards: dict[str, int] = {}
    for figure in figures:
        if combat_type is CombatType.DEATH:
            xp = _death_xp(figure, figures)
        elif combat_type is CombatType.ARENA:
            xp = _arena_xp(figure, figures, winning_side, fled)
        else:
            xp = _practice_xp(figure)
        figure.experience += xp
        awards[figure.uid] = xp
    return awards


def added_points(figure: Figure) -> int:
    """How many attribute points the figure has already bought (toward the cap)."""
    return figure.added_st + figure.added_dx


def can_advance(figure: Figure) -> bool:
    """True if the figure has both the XP and cap headroom to buy a point."""
    return (
        figure.experience >= XP_PER_ATTRIBUTE_POINT
        and added_points(figure) < MAX_ADDED_ATTRIBUTE_POINTS
    )


def spend_experience(figure: Figure, attribute: Attribute) -> Figure:
    """Trade 100 XP for +1 basic ST or DX, enforcing the 8-point lifetime cap.

    Raises :class:`ValueError` if the figure lacks 100 XP or has already added the
    maximum 8 points. The bought point is folded into the figure's basic attribute
    (so the whole engine sees the stronger fighter) and tallied on ``added_st`` /
    ``added_dx`` so the cap and persistence can track it.
    """
    if figure.experience < XP_PER_ATTRIBUTE_POINT:
        raise ValueError(
            f"{figure.name} has {figure.experience} XP; "
            f"{XP_PER_ATTRIBUTE_POINT} are needed to add an attribute point"
        )
    if added_points(figure) >= MAX_ADDED_ATTRIBUTE_POINTS:
        raise ValueError(
            f"{figure.name} has already added the maximum "
            f"{MAX_ADDED_ATTRIBUTE_POINTS} attribute points"
        )
    figure.experience -= XP_PER_ATTRIBUTE_POINT
    if attribute is Attribute.STRENGTH:
        figure.strength += 1
        figure.added_st += 1
    else:
        figure.dexterity += 1
        figure.added_dx += 1
    return figure
