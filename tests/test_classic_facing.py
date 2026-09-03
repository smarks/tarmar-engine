"""Classic facing zones and engagement (Section VI).

Ported verbatim from melee's ``engine/tests/test_facing.py`` (imports adapted
to ``tarmar_engine.classic``; expectations untouched).
"""
from __future__ import annotations

from hexarena.hex import Hex

from tarmar_engine.classic.arena import DEFAULT_LAYOUT as LAYOUT
from tarmar_engine.classic.facing import (
    FRONT,
    REAR,
    SIDE,
    attack_zone,
    facing_bonus,
    is_engaged,
    is_engaged_by,
    zone_of_direction,
)
from tarmar_engine.classic.figure import Figure, Posture


def test_zone_split_is_three_front_two_side_one_rear() -> None:
    facing = 0
    zones = [zone_of_direction(facing, d) for d in range(6)]
    assert zones.count(FRONT) == 3
    assert zones.count(SIDE) == 2
    assert zones.count(REAR) == 1


def test_facing_bonus_values() -> None:
    assert facing_bonus(FRONT) == 0
    assert facing_bonus(SIDE) == 2
    assert facing_bonus(REAR) == 4


def _place(name: str, hex_position: Hex, facing: int) -> Figure:
    fighter = Figure(name, 12, 12, "a")
    fighter.position = hex_position
    fighter.facing = facing
    return fighter


def test_attack_zone_front_side_rear() -> None:
    target = _place("T", Hex(5, 5), facing=0)
    front_hex = LAYOUT.neighbor(Hex(5, 5), 0)
    rear_hex = LAYOUT.neighbor(Hex(5, 5), 3)
    side_hex = LAYOUT.neighbor(Hex(5, 5), 2)

    attacker_front = _place("F", front_hex, facing=3)
    attacker_rear = _place("R", rear_hex, facing=0)
    attacker_side = _place("S", side_hex, facing=0)

    assert attack_zone(LAYOUT, attacker_front, target) == FRONT
    assert attack_zone(LAYOUT, attacker_rear, target) == REAR
    assert attack_zone(LAYOUT, attacker_side, target) == SIDE


def test_engagement_requires_adjacency_and_front() -> None:
    target = _place("T", Hex(5, 5), facing=0)
    front_hex = LAYOUT.neighbor(Hex(5, 5), 0)
    in_front = _place("A", front_hex, facing=3)
    # standing in the enemy's front hex while adjacent -> engaged
    assert is_engaged_by(LAYOUT, in_front, target)

    # two hexes away in the same direction is NOT engagement
    far = _place("B", LAYOUT.neighbor(front_hex, 0), facing=3)
    assert not is_engaged_by(LAYOUT, far, target)


def test_prone_figure_engages_no_one() -> None:
    target = _place("T", Hex(5, 5), facing=0)
    target.posture = Posture.PRONE
    front_hex = LAYOUT.neighbor(Hex(5, 5), 0)
    attacker = _place("A", front_hex, facing=3)
    assert not is_engaged_by(LAYOUT, attacker, target)


def test_engagement_is_one_directional_behind_a_foe_is_free() -> None:
    enemy = _place("E", Hex(5, 5), facing=0)          # the enemy faces direction 0
    behind = LAYOUT.neighbor(Hex(5, 5), 3)            # stand in its rear hex
    me = _place("M", behind, facing=0)                # turned to face the enemy's back
    # I'm in the enemy's rear, so it does not engage me...
    assert not is_engaged_by(LAYOUT, me, enemy)
    # ...and engagement is one-directional (p.9), so I am NOT engaged either, even
    # though the enemy sits in my front. I stay free to move and strike its rear.
    assert not is_engaged(LAYOUT, me, [enemy])


def test_face_to_face_figures_are_both_engaged() -> None:
    a = _place("A", Hex(5, 5), facing=0)              # faces direction 0
    ahead = LAYOUT.neighbor(Hex(5, 5), 0)
    b = _place("B", ahead, facing=3)                  # directly ahead, facing back at A
    # Each occupies the other's front hex, so both are engaged and may Shift & Attack.
    assert is_engaged(LAYOUT, a, [b])
    assert is_engaged(LAYOUT, b, [a])


def test_not_engaged_when_neither_faces_the_other() -> None:
    enemy = _place("E", Hex(5, 5), facing=0)
    side = LAYOUT.neighbor(Hex(5, 5), 2)              # the enemy's side hex
    me = _place("M", side, facing=2)                  # facing away from the enemy
    assert not is_engaged(LAYOUT, me, [enemy])


def test_format_situational_parts_shared_breakdown_fragments() -> None:
    from tarmar_engine.classic.facing import format_situational_parts
    assert format_situational_parts(REAR, ignore_facing=False, range_penalty=-3,
                                    situational_note="") == ["+4 rear", "-3 range"]
    assert format_situational_parts(
        SIDE, ignore_facing=False, range_penalty=0,
        situational_note="-2 over body") == ["+2 flank", "-2 over body"]
    # facing is suppressed for missiles (ignore_facing)
    assert format_situational_parts(REAR, ignore_facing=True, range_penalty=0,
                                    situational_note="") == []
