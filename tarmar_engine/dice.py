"""Dice-notation parsing and dice-sum odds for the engine's expected-damage
arithmetic.

Vendored from tarmar-studio's ``common/rolling.py`` (the project's single
dice-notation parser, issues #105/#175) when the engine moved into this
package (tarmar-studio #240 milestone 1): the engine needs only the parser,
not the studio's roll service, and this package must stay importable without
the Django app. Behavior and error messages are kept identical to the studio
parser so the two cannot diverge observably; folding the studio's copy onto
this one is a milestone-2 cleanup.
"""

from __future__ import annotations

import re

# ``NdS`` / ``NdS±M`` — the notation weapon damage strings use. Bounds keep a
# huge count or die size from exhausting memory on user-supplied input.
DICE_SPECIFICATION_PATTERN = re.compile(r"(\d+)d(\d+)([+-]\d+)?")
MIN_DICE_COUNT = 1
MAX_DICE_COUNT = 100
MIN_DIE_SIDES = 2
MAX_DIE_SIDES = 1000


def parse_dice_expression(specification: str) -> tuple[int, int, int]:
    """Parse a dice specification like ``3d6`` or ``2d6-2``.

    Args:
        specification: A dice specification in ``NdS`` or ``NdS±M`` form.
            Case-insensitive; surrounding whitespace is ignored.

    Returns:
        A ``(count, sides, modifier)`` tuple.

    Raises:
        ValueError: If the specification does not parse, or if the die count
            or side count falls outside the bounds above.
    """
    # fullmatch so trailing/extra input is rejected rather than silently
    # truncated.
    match = DICE_SPECIFICATION_PATTERN.fullmatch(specification.strip().lower())
    if not match:
        raise ValueError(f"Invalid dice expression: {specification}")

    count = int(match.group(1))
    sides = int(match.group(2))
    modifier = int(match.group(3)) if match.group(3) else 0

    if not (MIN_DICE_COUNT <= count <= MAX_DICE_COUNT) or not (
        MIN_DIE_SIDES <= sides <= MAX_DIE_SIDES
    ):
        raise ValueError(f"Dice expression out of range: {specification}")

    return count, sides, modifier


def sum_distribution(count: int, sides: int) -> dict[int, float]:
    """Probability of each possible sum of ``count`` dice, by convolution.

    Pure arithmetic, shared by both profiles' forecasts: the Tarmar policy's
    expected damage (:func:`tarmar_engine.combat_math.expected_damage`) and
    the classic profile's attack forecast (``Ruleset.hit_odds`` and
    ``Ruleset.expected_hit_damage``). No dice at all is a certain 0.

    Args:
        count: How many dice are summed; 0 or more.
        sides: Faces on each die.

    Returns:
        Each reachable total mapped to its probability.
    """
    distribution = {0: 1.0}
    for _die in range(count):
        next_distribution: dict[int, float] = {}
        for total, probability in distribution.items():
            for face in range(1, sides + 1):
                next_distribution[total + face] = (
                    next_distribution.get(total + face, 0.0) + probability / sides
                )
        distribution = next_distribution
    return distribution
