"""Classic attack resolution: special rolls, damage, armor (Section VII).

Ported verbatim from melee's ``engine/tests/test_combat.py`` (imports adapted
to ``tarmar_engine.classic``; expectations untouched).
"""
from __future__ import annotations

from hexarena.dice import Dice

from tarmar_engine.classic.data import (
    BROADSWORD,
    CHAINMAIL,
    LARGE_SHIELD,
    NO_ARMOR,
    SHORTSWORD,
)
from tarmar_engine.classic.facing import FRONT, REAR
from tarmar_engine.classic.figure import create_human
from tarmar_engine.classic.ruleset import Ruleset

# The default (classic Melee) ruleset owns attack resolution.
RULES = Ruleset()


def _attacker(weapon=BROADSWORD):
    return create_human("A", 12, 12, "a", weapons=[weapon], ready_weapon=weapon)


def _target(armor=NO_ARMOR, shield=None):
    kwargs = {"armor": armor}
    if shield is not None:
        kwargs["shield"] = shield
    return create_human("T", 12, 12, "b", **kwargs)


def test_roll_of_three_is_triple_damage_auto_hit() -> None:
    # to-hit total 3 (1,1,1); broadsword damage is 2d -> (2,2)=4, tripled = 12
    dice = Dice(scripted=[1, 1, 1, 2, 2])
    result = RULES.resolve_attack(dice, _attacker(), _target(), zone=FRONT)
    assert result.hit and result.multiplier == 3
    assert result.raw_damage == 12


def test_roll_of_five_always_hits_even_over_dx() -> None:
    from tarmar_engine.classic.figure import Figure
    # adjDX 4 (clumsy); a total of 5 exceeds it but still auto-hits (p.10).
    clumsy = Figure("A", strength=12, dexterity=4, side="a",
                    weapons=[SHORTSWORD], ready_weapon=SHORTSWORD)
    assert clumsy.base_adj_dx == 4
    dice = Dice(scripted=[1, 1, 3, 3, 3])  # to-hit total 5, damage 2d (3,3)
    result = RULES.resolve_attack(dice, clumsy, _target(), zone=FRONT)
    assert result.hit and result.multiplier == 1


def test_roll_of_sixteen_always_misses() -> None:
    dice = Dice(scripted=[6, 6, 4])  # total 16
    result = RULES.resolve_attack(dice, _attacker(), _target(), zone=FRONT)
    assert not result.hit


def test_roll_of_seventeen_drops_weapon() -> None:
    dice = Dice(scripted=[6, 6, 5])  # total 17
    result = RULES.resolve_attack(dice, _attacker(), _target(), zone=FRONT)
    assert not result.hit and result.dropped_weapon


def test_roll_of_eighteen_breaks_weapon() -> None:
    dice = Dice(scripted=[6, 6, 6])  # total 18
    result = RULES.resolve_attack(dice, _attacker(), _target(), zone=FRONT)
    assert not result.hit and result.broke_weapon


def test_armor_and_frontal_shield_absorb_hits() -> None:
    target = _target(armor=CHAINMAIL, shield=LARGE_SHIELD)  # stops 5 frontally
    # to-hit total 8 (<=12 hit), broadsword 2d rolls 4+3=7 raw, minus 5 -> 2
    dice = Dice(scripted=[2, 3, 3, 4, 3])
    result = RULES.resolve_attack(dice, _attacker(), target, zone=FRONT)
    assert result.hit
    assert result.raw_damage == 7
    assert result.damage == 2


def test_rear_attack_ignores_frontal_shield() -> None:
    target = _target(armor=CHAINMAIL, shield=LARGE_SHIELD)
    # from the rear the shield does not help: 7 raw - 3 armor = 4
    dice = Dice(scripted=[2, 3, 3, 4, 3])
    result = RULES.resolve_attack(dice, _attacker(), target, zone=REAR)
    assert result.damage == 4


def test_four_dice_against_dodging_target() -> None:
    target = _target()
    target.dodging = True
    # total 20 on four dice is an automatic miss
    dice = Dice(scripted=[6, 6, 6, 2])
    result = RULES.resolve_attack(dice, _attacker(), target, zone=FRONT, dice_count=4)
    assert not result.hit


def test_roll_damage_floors_at_zero_and_applies_the_multiplier() -> None:
    from tarmar_engine.classic.combat import roll_damage
    from tarmar_engine.classic.data import DamageDice
    # 1d-4 rolling a 1 -> max(0, -3) -> 0, even when doubled
    assert roll_damage(Dice(scripted=[1]), DamageDice(1, -4), 2) == 0
    # 2d-1 rolling 3,3 -> 5, doubled -> 10
    assert roll_damage(Dice(scripted=[3, 3]), DamageDice(2, -1), 2) == 10


def test_roll_damage_extra_dice_are_inside_the_multiplier() -> None:
    from tarmar_engine.classic.combat import roll_damage
    from tarmar_engine.classic.data import DamageDice
    # 1d+0 (rolls 4) + one extra die (rolls 2) -> 6, doubled -> 12
    assert roll_damage(Dice(scripted=[4, 2]), DamageDice(1, 0), 2, extra_dice=1) == 12
