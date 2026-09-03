"""The heuristic computer opponent: it closes, engages, and focus-fires.

Ported verbatim from melee's ``engine/tests/test_ai.py`` as the acceptance bar
for :mod:`tarmar_engine.classic.ai` — the pattern milestone 3 used for the
rulebook Combat Example. Imports adapted to ``tarmar_engine.classic``;
expectations untouched, so the port's plan/apply split is proved to leave
melee's tactics exactly where they were.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (ported melee tests place figures then use positions and options;
# Optional stays as-is — the relaxation the other ported suites take)
from __future__ import annotations

from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic import ai
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.data import (
    BROADSWORD,
    DAGGER,
    NO_ARMOR,
    SMALL_BOW,
    WeaponKind,
)
from tarmar_engine.classic.experience import CombatType
from tarmar_engine.classic.figure import Posture, create_human
from tarmar_engine.classic.options import Option, spec
from tarmar_engine.classic.state import GameState


def _fighter(name: str, side: str, weapon=BROADSWORD, **kw):
    return create_human(name, 12, 12, side, weapons=[weapon, DAGGER],
                        ready_weapon=weapon, armor=NO_ARMOR, **kw)


def _drive(state: GameState, side: str) -> None:
    """Play each of ``side``'s figures through the per-figure AI (#192)."""
    for figure in [f for f in state.figures if f.side == side and f.can_act()]:
        ai.take_action(state, figure)


def test_ai_closes_the_distance() -> None:
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    blue = _fighter("Blue", "blue")
    red = _fighter("Red", "red")
    blue.position, blue.facing = Hex(3, 3), 0
    red.position = blue.position
    for _ in range(3):                       # red starts 3 hexes away
        red.position = layout.neighbor(red.position, 0)
    red.facing = 3
    state = GameState(arena, [red, blue], dice=Dice(seed=1))

    before = layout.distance(red.position, blue.position)
    _drive(state, "red")
    after = layout.distance(red.position, blue.position)
    assert after < before                    # it moved toward the enemy


def test_ai_drives_a_multihex_giant_without_crashing() -> None:
    # Regression (#153): the AI used to pass a facing change together with a path
    # for a multi-hex figure, which the engine rejects (turn-while-move is deferred
    # for giants), crashing mid-turn. It must drive a giant with only legal moves.
    from tarmar_engine.classic.monsters import create_monster
    arena = Arena(cols=13, rows=13)
    layout = arena.layout
    foe = create_human("Foe", 12, 12, "blue", weapons=[BROADSWORD],
                       ready_weapon=BROADSWORD, armor=NO_ARMOR)
    giant = create_monster("Giant", "Grond", "red")
    foe.position, foe.facing = Hex(2, 2), 0
    # far off, facing away from the foe
    giant.position, giant.facing = Hex(9, 9), 0
    state = GameState(arena, [giant, foe], dice=Dice(seed=1))

    assert giant.size > 1                              # really multi-hex
    before = layout.distance(giant.position, foe.position)
    _drive(state, "red")                    # must not raise IllegalAction
    after = layout.distance(giant.position, foe.position)
    assert after <= before                            # made a legal move toward the foe


def test_ai_engaged_attacks_and_resolves() -> None:
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    blue = _fighter("Blue", "blue")
    red = _fighter("Red", "red")
    blue.position, blue.facing = Hex(3, 3), 0
    red.position = layout.neighbor(blue.position, 0)   # adjacent, in blue's front
    red.facing = 3
    # scripted: 3d6 to-hit = 9 (clean hit, not a special), then 2d6 damage = 12.
    state = GameState(arena, [red, blue], dice=Dice(scripted=[3, 3, 3, 6, 6]))

    _drive(state, "red")
    assert spec(red.current_option).is_attack          # chose an attack option
    ai.queue_attacks(state, "red")
    results = state.resolve_combat()
    assert results and results[0].hit
    assert blue.current_st < blue.strength             # blue took damage


def test_ai_stands_and_strikes_an_adjacent_foe_without_shifting() -> None:
    # #300: when the foe is already within reach the AI takes the plain ATTACK
    # (stand still, strike) rather than a pointless SHIFT_ATTACK -- standing and
    # striking is correct, so it must not force a shift.
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    blue = _fighter("Blue", "blue")
    red = _fighter("Red", "red")
    blue.position, blue.facing = Hex(3, 3), 0
    red.position = layout.neighbor(blue.position, 0)    # adjacent, in reach already
    red.facing = 3
    state = GameState(arena, [red, blue], dice=Dice(seed=1))

    _drive(state, "red")
    assert red.current_option == Option.ATTACK         # plain strike, no shift
    assert red.moved_this_turn == 0                     # it stood its ground


def _archer(name: str, side: str, **kw):
    return create_human(name, 12, 12, side, weapons=[SMALL_BOW, DAGGER],
                        ready_weapon=SMALL_BOW, armor=NO_ARMOR, **kw)


def test_ai_fires_a_missile_in_movement() -> None:
    arena = Arena(cols=7, rows=9)
    layout = arena.layout
    archer = _archer("Archer", "red")
    foe = _fighter("Foe", "blue")
    archer.position = Hex(3, 4)
    foe.position = archer.position
    for _ in range(3):                       # foe stands three hexes down range
        foe.position = layout.neighbor(foe.position, 0)
    foe.facing = 3
    archer.missile_cooldown = 0              # bow is loaded
    state = GameState(arena, [archer, foe], dice=Dice(seed=1))
    assert not state.engaged(archer)         # out of contact, so it can loose

    _drive(state, "red")
    assert archer.current_option == Option.MISSILE_ATTACK
    assert state.in_front_arc(archer, foe.position)   # it faced the lane first


def test_ai_advances_while_reloading_instead_of_holding() -> None:
    # #210: a reloading missile figure used to hold in place (a no-op MOVE). It
    # must now ADVANCE toward the enemy -- a real move that closes the distance --
    # since a crossbow reloads automatically while it marches (p.16).
    arena = Arena(cols=7, rows=9)
    layout = arena.layout
    archer = _archer("Archer", "red")
    foe = _fighter("Foe", "blue")
    archer.position = Hex(3, 4)
    foe.position = archer.position
    for _ in range(3):                       # distant foe
        foe.position = layout.neighbor(foe.position, 0)
    foe.facing = 3
    archer.missile_cooldown = 1              # mid-reload: cannot fire this turn
    state = GameState(arena, [archer, foe], dice=Dice(seed=1))
    start = archer.position

    before = layout.distance(archer.position, foe.position)
    _drive(state, "red")
    after = layout.distance(archer.position, foe.position)
    assert archer.current_option == Option.MOVE       # a real move, not an attack
    assert archer.position != start                    # it did NOT hold in place
    assert after < before                              # it closed the distance
    assert state.in_front_arc(archer, foe.position)   # still facing the foe


def test_ai_hth_focus_fires_the_weaker_grappler() -> None:
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    grappler = _fighter("Grappler", "red")
    strong = _fighter("Strong", "blue")
    weak = _fighter("Weak", "blue")
    grappler.position = Hex(3, 3)
    strong.position = grappler.position
    weak.position = layout.neighbor(grappler.position, 1)
    weak.damage_taken = 8                    # current_st 4 vs strong's 12
    state = GameState(arena, [grappler, strong, weak])
    # Lock all three into one grapple on the ground (uids assigned by GameState).
    grappler.hth_opponents = [strong.uid, weak.uid]
    strong.hth_opponents = [grappler.uid]
    weak.hth_opponents = [grappler.uid]
    grappler.posture = strong.posture = weak.posture = Posture.PRONE

    ai.queue_attacks(state, "red")
    assert grappler.current_option == Option.HTH_ATTACK
    assert state._pending[-1].target is weak          # struck the lower-ST foe


def test_ai_queues_a_missile_attack_in_combat() -> None:
    arena = Arena(cols=7, rows=9)
    layout = arena.layout
    archer = _archer("Archer", "red")
    foe = _fighter("Foe", "blue")
    archer.position = Hex(3, 4)
    foe.position = archer.position
    for _ in range(3):                       # foe down range, in the front arc
        foe.position = layout.neighbor(foe.position, 0)
    foe.facing = 3
    state = GameState(arena, [archer, foe], dice=Dice(seed=1))
    _drive(state, "red")           # faces and chooses MISSILE_ATTACK
    assert archer.current_option == Option.MISSILE_ATTACK

    ai.queue_attacks(state, "red")
    assert state._pending and state._pending[-1].target is foe


def test_ai_turns_to_aim_at_a_foe_outside_its_front_arc() -> None:
    # #362: the AI now drives its missile targets off the engine's single source
    # (GameState.attack_candidates) and turns to aim, exactly as the human path
    # does. Before the fix the AI re-derived candidates with an in_front_arc filter
    # and never aimed, so a computer archer with a loaded bow SILENTLY DECLINED a
    # shot at a foe in its flank/rear that a human archer on the same board could
    # take -- option (f) is a free facing change and missiles get no facing bonus
    # (p.16), so aiming to face is always legal. This pins the corrected behavior.
    arena = Arena(cols=7, rows=9)
    layout = arena.layout
    archer = _archer("Archer", "red")
    foe = _fighter("Foe", "blue")
    archer.position, archer.facing = Hex(3, 4), 0     # facing "north" (dir 0)
    foe.position = archer.position
    for _ in range(3):                     # foe stands three hexes to the REAR
        foe.position = layout.neighbor(foe.position, 3)
    foe.facing = 0
    archer.missile_cooldown = 0                        # bow is loaded
    archer.current_option = Option.MISSILE_ATTACK      # committed to shoot
    state = GameState(arena, [archer, foe], dice=Dice(seed=1))

    assert not state.engaged(archer)                   # free to loose
    assert not state.in_front_arc(archer, foe.position)  # foe is behind it to start

    ai.queue_attacks(state, "red")

    assert state._pending and state._pending[-1].target is foe   # it took the shot
    assert state.in_front_arc(archer, foe.position)    # having turned to aim (#362)


def test_ai_stands_a_prone_figure() -> None:
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    downed = _fighter("Downed", "red")
    foe = _fighter("Foe", "blue")
    downed.position = Hex(3, 3)
    foe.position = layout.neighbor(downed.position, 0)
    downed.posture = Posture.PRONE
    state = GameState(arena, [downed, foe], dice=Dice(seed=1))

    _drive(state, "red")
    assert downed.current_option == Option.STAND_UP


def test_ai_focus_fires_the_wounded() -> None:
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    me = _fighter("Me", "red")
    healthy = _fighter("Healthy", "blue")
    hurt = _fighter("Hurt", "blue")
    me.position = Hex(3, 3)
    healthy.position = layout.neighbor(me.position, 0)
    hurt.position = layout.neighbor(me.position, 1)
    hurt.damage_taken = 8                              # current_st 4 vs healthy's 12
    state = GameState(arena, [me, healthy, hurt])

    assert ai._best_target(state, me, [healthy, hurt]) is hurt


def test_ai_moves_and_fires_to_close_the_range() -> None:
    # #210: a loaded missile figure out of contact should MOVE-AND-FIRE -- step a
    # hex toward the target while shooting (p.16), so it closes as it looses --
    # rather than firing from where it stands.
    arena = Arena(cols=7, rows=9)
    layout = arena.layout
    archer = _archer("Archer", "red")
    foe = _fighter("Foe", "blue")
    archer.position = Hex(3, 4)
    foe.position = archer.position
    for _ in range(3):                       # foe three hexes down range
        foe.position = layout.neighbor(foe.position, 0)
    foe.facing = 3
    archer.missile_cooldown = 0              # loaded
    state = GameState(arena, [archer, foe], dice=Dice(seed=1))
    start = archer.position

    before = layout.distance(archer.position, foe.position)
    _drive(state, "red")
    after = layout.distance(archer.position, foe.position)
    assert archer.current_option == Option.MISSILE_ATTACK  # still shooting
    assert archer.position != start                        # but it stepped up
    assert after < before                                  # closing while firing
    assert state.in_front_arc(archer, foe.position)        # target still in front


def test_ai_focus_fires_the_weakest_reachable_foe() -> None:
    # #210: the AI should manoeuvre toward -- and shoot at -- the WEAKEST reachable
    # enemy (focus-fire), not merely the nearest. Here a full-strength foe stands
    # one hex nearer than a badly wounded one; the archer should close on the
    # wounded one.
    arena = Arena(cols=9, rows=11)
    layout = arena.layout
    archer = _archer("Archer", "red")
    healthy = _fighter("Healthy", "blue")
    wounded = _fighter("Wounded", "blue")
    archer.position = Hex(4, 5)
    healthy.position = layout.neighbor(          # 2 away
        layout.neighbor(archer.position, 0), 0)
    wounded.position = archer.position
    for _ in range(4):                       # wounded stands four hexes off
        wounded.position = layout.neighbor(wounded.position, 0)
    healthy.facing = wounded.facing = 3
    wounded.damage_taken = 8                 # current_st 4 vs healthy's 12
    archer.missile_cooldown = 0              # loaded
    state = GameState(arena, [archer, healthy, wounded], dice=Dice(seed=1))

    to_wounded_before = layout.distance(archer.position, wounded.position)
    _drive(state, "red")
    to_wounded_after = layout.distance(archer.position, wounded.position)
    assert archer.current_option == Option.MISSILE_ATTACK
    assert to_wounded_after < to_wounded_before        # closed on the WEAK foe
    assert state.in_front_arc(archer, wounded.position)  # and aimed at it

    ai.queue_attacks(state, "red")
    assert state._pending and state._pending[-1].target is wounded  # shot the weak one


def test_ai_engaged_with_a_reloading_bow_swaps_to_its_blade() -> None:
    # Regression (#204): when figures start with a missile weapon readied, an AI
    # fighter can be engaged in melee while its bow is still reloading. It can
    # neither shift-attack nor parry with a missile weapon (both illegal, p.13/#79),
    # so the old AI chose the illegal SHIFT_DEFEND and crashed mid-turn. It must
    # instead drop the bow for a carried melee weapon (Change Weapons) -- legally.
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    shooter = create_human("Shooter", 12, 12, "red",
                           weapons=[SMALL_BOW, BROADSWORD], ready_weapon=SMALL_BOW,
                           armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    shooter.position, shooter.facing = Hex(3, 3), 0
    foe.position = layout.neighbor(shooter.position, 0)   # directly in front -> engaged
    foe.facing = 3
    shooter.missile_cooldown = 1                          # bow is still reloading
    state = GameState(arena, [shooter, foe], dice=Dice(seed=1))

    assert state.engaged(shooter) and shooter.missile_cooldown > 0
    ai.take_action(state, shooter)                        # must not raise IllegalAction
    assert shooter.current_option == Option.CHANGE_WEAPONS
    assert shooter.ready_weapon is not None
    assert shooter.ready_weapon.name == "Broadsword"      # swapped off the bow


def test_ai_engaged_with_a_reloading_bow_and_no_blade_holds() -> None:
    # The same corner, but the shooter carries no melee weapon to swap to: it must
    # still set a legal action (a no-op hold) rather than crash.
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    shooter = create_human("Shooter", 12, 12, "red",
                           weapons=[SMALL_BOW], ready_weapon=SMALL_BOW, armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    shooter.position, shooter.facing = Hex(3, 3), 0
    foe.position = layout.neighbor(shooter.position, 0)
    foe.facing = 3
    shooter.missile_cooldown = 1
    state = GameState(arena, [shooter, foe], dice=Dice(seed=1))

    ai.take_action(state, shooter)                        # must not raise
    assert shooter.current_option == Option.DO_NOTHING


def test_ai_engaged_reloading_bow_picks_up_a_dropped_blade_in_reach() -> None:
    # The engaged reloading-bow corner with NO carried melee weapon, but a melee
    # weapon lies dropped within reach. PICK_UP is engaged-legal (#285/#290), so
    # re-arm from the ground in one step rather than holding uselessly with an
    # unfightable bow (#311). Before the fix this fell straight to DO_NOTHING,
    # ignoring the blade at the figure's feet -- unlike _rearm_or_close.
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    shooter = create_human("Shooter", 12, 12, "red",
                           weapons=[SMALL_BOW], ready_weapon=SMALL_BOW, armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    shooter.position, shooter.facing = Hex(3, 3), 0
    foe.position = layout.neighbor(shooter.position, 0)
    foe.facing = 3
    shooter.missile_cooldown = 1
    state = GameState(arena, [shooter, foe], dice=Dice(seed=1))
    state._drop_to_ground(BROADSWORD, shooter.position)   # a blade underfoot

    assert state.engaged(shooter) and shooter.missile_cooldown > 0
    assert state.dropped_in_reach(shooter)                # the blade is in reach
    ai.take_action(state, shooter)                        # must not raise
    assert shooter.current_option == Option.PICK_UP
    assert shooter.ready_weapon is not None
    assert shooter.ready_weapon.name == "Broadsword"      # picked the dropped blade


# ---- fumble-disarm recovery (#275, the #249 audit finding) -------------------
# A natural-roll fumble (Tarmar's nat-1 table, classic Melee's 17/18) empties
# ``ready_weapon``. An AI that never re-arms can neither attack nor be fought
# into progress, and the whole game wedges into a stalemate that reads as a
# hang. These guards pin the recovery behaviours; each FAILED before the fix.


def test_disarmed_engaged_ai_readies_a_carried_blade() -> None:
    # Engaged with a foe, sword gone, dagger still on the belt: swap to the
    # dagger (option m) instead of committing to a bare attack it can never make.
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    fighter = create_human("Fighter", 12, 12, "red", weapons=[DAGGER],
                           ready_weapon=None, armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    fighter.position, fighter.facing = Hex(3, 3), 0
    foe.position = layout.neighbor(fighter.position, 0)
    foe.facing = 3
    state = GameState(arena, [fighter, foe], dice=Dice(seed=1))

    assert state.engaged(fighter) and fighter.ready_weapon is None
    ai.take_action(state, fighter)
    assert fighter.current_option == Option.CHANGE_WEAPONS
    assert fighter.ready_weapon is DAGGER


def test_disarmed_ai_picks_its_dropped_weapon_back_up() -> None:
    # Free of contact with its fumbled sword at its feet (a fumbled melee weapon
    # drops in the fumbler's own hex): pick it back up (option q).
    arena = Arena(cols=9, rows=9)
    fighter = create_human("Fighter", 12, 12, "red", weapons=[],
                           ready_weapon=None, armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    fighter.position, fighter.facing = Hex(3, 3), 0
    foe.position = Hex(7, 7)
    foe.facing = 3
    state = GameState(arena, [fighter, foe], dice=Dice(seed=1))
    state._drop_to_ground(BROADSWORD, fighter.position)

    ai.take_action(state, fighter)
    assert fighter.current_option == Option.PICK_UP
    assert fighter.ready_weapon is BROADSWORD


def test_disarmed_ai_readies_a_carried_spare_when_nothing_lies_in_reach() -> None:
    # Free of contact, nothing on the ground, a dagger still carried: ready it
    # (option e) rather than charging with empty hands.
    arena = Arena(cols=9, rows=9)
    fighter = create_human("Fighter", 12, 12, "red", weapons=[DAGGER],
                           ready_weapon=None, armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    fighter.position, fighter.facing = Hex(3, 3), 0
    foe.position = Hex(7, 7)
    foe.facing = 3
    state = GameState(arena, [fighter, foe], dice=Dice(seed=1))

    ai.take_action(state, fighter)
    assert fighter.current_option == Option.READY_WEAPON
    assert fighter.ready_weapon is DAGGER


def _face_toward(layout, figure, target_position) -> None:
    """Point ``figure`` at ``target_position`` (its front hex holds the target)."""
    figure.facing = next(direction for direction in range(6)
                          if layout.neighbor(figure.position, direction)
                          == target_position)


# ---- #239: the practice-bout missile ban ------------------------------------
# No missile may be loosed in a practice bout (p.22). The AI's can_fire omitted
# that gate, so an AI archer requested MISSILE_ATTACK/ONE_LAST_SHOT and the engine
# rejected it — 500-ing practice-vs-computer creation or wedging the select phase.


def test_practice_bout_ai_archer_takes_up_its_blade_instead_of_firing() -> None:
    arena = Arena(cols=7, rows=9)
    layout = arena.layout
    archer = create_human("Archer", 12, 12, "red",
                          weapons=[SMALL_BOW, BROADSWORD], ready_weapon=SMALL_BOW,
                          armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    archer.position = Hex(3, 4)
    foe.position = archer.position
    for _ in range(3):                       # foe three hexes down range
        foe.position = layout.neighbor(foe.position, 0)
    foe.facing = 3
    archer.missile_cooldown = 0              # loaded — it WOULD fire in a death match
    state = GameState(arena, [archer, foe], dice=Dice(seed=1),
                      combat_type=CombatType.PRACTICE)
    assert state.practice and not state.engaged(archer)

    ai.take_action(state, archer)            # must not raise IllegalAction
    assert archer.current_option not in (Option.MISSILE_ATTACK, Option.ONE_LAST_SHOT)
    assert archer.current_option == Option.READY_WEAPON
    assert archer.ready_weapon.name == "Broadsword"


def test_practice_bout_engaged_ai_archer_swaps_to_its_blade() -> None:
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    archer = create_human("Archer", 12, 12, "red",
                          weapons=[SMALL_BOW, BROADSWORD], ready_weapon=SMALL_BOW,
                          armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    archer.position, archer.facing = Hex(3, 3), 0
    foe.position = layout.neighbor(archer.position, 0)   # engaged, in the front hex
    _face_toward(layout, foe, archer.position)
    archer.missile_cooldown = 0                          # loaded
    state = GameState(arena, [archer, foe], dice=Dice(seed=1),
                      combat_type=CombatType.PRACTICE)
    assert state.practice and state.engaged(archer)

    ai.take_action(state, archer)                        # must not raise
    assert archer.current_option == Option.CHANGE_WEAPONS
    assert archer.ready_weapon.name == "Broadsword"


# ---- #240: an engaged fighter must not turn its back on its engager ----------


def test_engaged_ai_faces_and_strikes_the_foe_engaging_it() -> None:
    # An engaged fighter used to pick the globally weakest enemy — even one far
    # behind it — turning its back on the foe actually engaging it (presenting its
    # rear, +4 to be hit) and queueing zero attacks. Engaged, it must focus on an
    # ADJACENT foe: face it and strike.
    arena = Arena(cols=7, rows=13)
    layout = arena.layout
    me = _fighter("Me", "red")
    engager = _fighter("Engager", "blue")            # full ST, in my front hex
    distant = _fighter("Distant", "blue")            # weaker, but far to my rear
    me.position, me.facing = Hex(3, 3), 0
    engager.position = layout.neighbor(me.position, 0)   # adjacent, engaging me
    _face_toward(layout, engager, me.position)
    distant.position = Hex(3, 11)                     # far off, off my facing
    distant.facing = 0
    distant.damage_taken = 8                          # the weakest figure on the field
    state = GameState(arena, [me, engager, distant], dice=Dice(seed=1))
    assert state.engaged(me)

    ai.take_action(state, me)
    assert spec(me.current_option).is_attack          # it committed to a real attack
    ai.queue_attacks(state, "red")
    # struck the engager
    assert state._pending and state._pending[-1].target is engager


# ---- #250: an engaged multi-hex figure must never request a blocked turn -----


def test_engaged_multihex_ai_never_requests_a_blocked_turn() -> None:
    # An engaged giant used to pass a facing change unconditionally in its
    # stationary branches; move() routes a size>1 turn through
    # _validate_multihex_turn, which raises when the rotated footprint hits an
    # occupied hex — crashing take_action and breaking the #153 "the AI can never
    # pick an illegal option" guarantee. Config found by brute force (probe): giant
    # at (7,7) facing 0, two foes engaging it; facing the weaker one would rotate
    # the footprint onto the other.
    from tarmar_engine.classic.monsters import create_monster
    arena = Arena(cols=15, rows=15)
    layout = arena.layout
    giant = create_monster("Giant", "Grond", "red")
    giant.position, giant.facing = Hex(7, 7), 0
    footprint = set(giant.footprint(layout))
    near = create_human("Near", 12, 12, "blue", weapons=[BROADSWORD],
                        ready_weapon=BROADSWORD, armor=NO_ARMOR)
    far = create_human("Far", 12, 12, "blue", weapons=[BROADSWORD],
                       ready_weapon=BROADSWORD, armor=NO_ARMOR)
    near.position = Hex(7, 5)
    far.position = Hex(6, 6)
    near.facing = next(d for d in range(6)
                       if layout.neighbor(near.position, d) in footprint)
    far.facing = next(d for d in range(6)
                      if layout.neighbor(far.position, d) in footprint)
    far.damage_taken = 8                              # the weaker foe pulls the facing
    state = GameState(arena, [giant, near, far], dice=Dice(seed=1))
    assert giant.size > 1 and state.engaged(giant)

    ai.take_action(state, giant)                     # must NOT raise IllegalAction
    assert giant.current_option is not None          # a real, legal action was set


# ---- #278/#290: an engaged missile-only fighter recovers a weapon ------------
# Post-#276 residual stalemate: a fumble that leaves an engaged figure carrying
# only a missile weapon (which it can neither ready while engaged, p.13/#79, nor
# fire empty-handed) used to hold forever. With a MELEE blade in reach the fix
# is a one-step PICK_UP — engaged-legal since #285, needing no free hex (#290);
# the older two-step DISENGAGE-then-ready remains the recovery when only a MISSILE
# weapon lies in reach (useless to pick up while still engaged).


def _engaged_missile_only_with_blade_underfoot():
    """A disarmed blue fighter engaged by a red foe, carrying only a bow, with a
    fumbled dagger lying in its own hex — the #278 wedge."""
    arena = Arena(cols=9, rows=9)
    layout = arena.layout
    stuck = create_human("Stuck", 12, 12, "blue", weapons=[SMALL_BOW],
                         ready_weapon=None, armor=NO_ARMOR)
    foe = _fighter("Foe", "red")
    stuck.position, stuck.facing = Hex(4, 4), 0
    foe.position = layout.neighbor(stuck.position, 0)     # in the front hex -> engaged
    _face_toward(layout, foe, stuck.position)
    state = GameState(arena, [stuck, foe], dice=Dice(seed=1))
    state._drop_to_ground(DAGGER, stuck.position)         # fumbled blade underfoot
    return state, stuck, foe, layout


def test_engaged_missile_only_ai_picks_up_its_dropped_blade() -> None:
    # A dropped MELEE weapon in reach is recovered in ONE step: PICK_UP is
    # engaged-legal (#285), strictly better than the old two-step disengage (#290).
    state, stuck, foe, layout = _engaged_missile_only_with_blade_underfoot()
    assert state.engaged(stuck) and stuck.ready_weapon is None
    assert all(weapon.kind == WeaponKind.MISSILE for weapon in stuck.weapons)
    assert state.dropped_in_reach(stuck)                  # its blade is in reach

    ai.take_action(state, stuck)
    assert stuck.current_option == Option.PICK_UP        # took the blade up in place
    assert stuck.ready_weapon is DAGGER                   # re-armed in one turn


def _engaged_missile_only_with_only_a_dropped_bow_in_reach():
    """A disarmed blue fighter engaged by a red foe, carrying only a bow, with a
    fumbled bow (no melee weapon) lying underfoot and free hexes to step to."""
    arena = Arena(cols=9, rows=9)
    layout = arena.layout
    stuck = create_human("Stuck", 12, 12, "blue", weapons=[SMALL_BOW],
                         ready_weapon=None, armor=NO_ARMOR)
    foe = _fighter("Foe", "red")
    stuck.position, stuck.facing = Hex(4, 4), 0
    foe.position = layout.neighbor(stuck.position, 0)     # in the front hex -> engaged
    _face_toward(layout, foe, stuck.position)
    state = GameState(arena, [stuck, foe], dice=Dice(seed=1))
    state._drop_to_ground(SMALL_BOW, stuck.position)      # only a bow in reach
    return state, stuck, foe, layout


def test_engaged_with_only_a_dropped_missile_in_reach_still_disengages() -> None:
    # Nothing but a MISSILE weapon in reach — useless to pick up while engaged, so
    # the two-step recovery still applies: DISENGAGE (option n) toward it, then
    # ready it once free next turn (#278).
    state, stuck, foe, layout = _engaged_missile_only_with_only_a_dropped_bow_in_reach()
    assert state.engaged(stuck) and stuck.ready_weapon is None
    assert all(weapon.kind == WeaponKind.MISSILE for weapon in stuck.weapons)
    assert all(weapon.kind == WeaponKind.MISSILE
               for weapon in state.dropped_in_reach(stuck))

    ai.take_action(state, stuck)                          # turn 1 selection: disengage
    assert stuck.current_option == Option.DISENGAGE      # chose to break away
    ai.queue_attacks(state, "blue")                       # turn 1 combat: step away
    assert stuck.attacked_this_turn                       # the step replaced its attack
    assert not state.engaged(stuck)                       # broke contact


def test_barehanded_ai_grapples_in_combat_when_the_rules_allow() -> None:
    # Nothing to recover at all, but a prone foe adjacent (HTH legal, p.17): the
    # combat phase must produce a grapple, not silently skip the weaponless figure.
    arena = Arena(cols=7, rows=7)
    layout = arena.layout
    fighter = create_human("Fighter", 12, 12, "red", weapons=[],
                           ready_weapon=None, armor=NO_ARMOR)
    foe = _fighter("Foe", "blue")
    fighter.position, fighter.facing = Hex(3, 3), 0
    foe.position = layout.neighbor(fighter.position, 0)
    foe.facing = 3
    foe.posture = Posture.PRONE                       # grapple-able (p.17)
    state = GameState(arena, [fighter, foe], dice=Dice(seed=1))

    assert state.hth_targets(fighter), "precondition: a legal grapple exists"
    ai.queue_attacks(state, "red")
    assert fighter.current_option == Option.HTH_ATTACK


# ---- the simple wizard cast policy (#431) -------------------------------------

def _ai_wizard(side: str = "red", strength: int = 14,
               spells: list[str] | None = None):
    from tarmar_engine.classic.figure import create_wizard

    wizard = create_wizard(
        "Circe", strength=strength, dexterity=11, intelligence=13, side=side,
        spells_known=spells if spells is not None
        else ["magic_fist", "fireball", "trip", "drop_weapon",
              "break_weapon", "staff", "stone_flesh"])
    wizard.uid = "circe"
    return wizard


def test_ai_wizard_declares_cast_and_hurls_its_best_missile() -> None:
    """A disengaged AI wizard prefers casting to marching: it declares CAST in
    the select phase and queues its strongest missile (Fireball beats Magic
    Fist at 1d-1 vs 1d-2 per ST) at the focus-fire target, investing the most
    ST the reserve allows."""
    arena = Arena(cols=9, rows=9)
    wizard = _ai_wizard()
    foe = _fighter("Foe", "blue")
    wizard.position, wizard.facing = Hex(2, 4), 0
    foe.position, foe.facing = Hex(6, 4), 3
    state = GameState(arena, [wizard, foe], dice=Dice(seed=5))

    ai.take_action(state, wizard)
    assert wizard.current_option == Option.CAST
    ai.queue_attacks(state, "red")
    pending = state._pending_casts
    assert len(pending) == 1
    assert pending[0].spell.id == "fireball"
    assert pending[0].target is foe
    # ST 14 - reserve 4 = 10, capped by the 3-ST missile ceiling.
    assert pending[0].st_used == 3


def test_ai_wizard_falls_back_to_an_obvious_debuff() -> None:
    """With no missile spell known, the policy throws the first obvious debuff
    (Trip) at the nearest legal target."""
    arena = Arena(cols=9, rows=9)
    wizard = _ai_wizard(spells=["trip", "drop_weapon", "staff"])
    foe = _fighter("Foe", "blue")
    wizard.position, wizard.facing = Hex(2, 4), 0
    foe.position, foe.facing = Hex(6, 4), 3
    state = GameState(arena, [wizard, foe], dice=Dice(seed=5))

    ai.take_action(state, wizard)
    assert wizard.current_option == Option.CAST
    ai.queue_attacks(state, "red")
    assert state._pending_casts and state._pending_casts[0].spell.id == "trip"
    assert state._pending_casts[0].st_used == 2


def test_ai_wizard_keeps_a_st_reserve_and_fights_instead_when_spent() -> None:
    """At or below the reserve the wizard casts nothing — it falls through to
    the ordinary staff-fighter behaviour rather than knocking itself out."""
    arena = Arena(cols=9, rows=9)
    wizard = _ai_wizard(strength=14, spells=["magic_fist", "staff"])
    wizard.damage_taken = 10                        # 4 ST left == the reserve
    foe = _fighter("Foe", "blue")
    wizard.position, wizard.facing = Hex(2, 4), 0
    foe.position, foe.facing = Hex(6, 4), 3
    state = GameState(arena, [wizard, foe], dice=Dice(seed=5))

    ai.take_action(state, wizard)
    assert wizard.current_option != Option.CAST     # it closes with the staff


def test_ai_wizard_stands_down_when_its_cast_evaporates() -> None:
    """A declared CAST whose every plan died between phases (the lone foe was
    felled) stands down instead of wedging the cast gate (#397/#398 pattern)."""
    arena = Arena(cols=9, rows=9)
    wizard = _ai_wizard()
    foe = _fighter("Foe", "blue")
    wizard.position, wizard.facing = Hex(2, 4), 0
    foe.position, foe.facing = Hex(6, 4), 3
    state = GameState(arena, [wizard, foe], dice=Dice(seed=5))

    ai.take_action(state, wizard)
    assert wizard.current_option == Option.CAST
    foe.damage_taken = foe.strength                 # felled before combat
    ai.queue_attacks(state, "red")
    assert not state._pending_casts
    assert wizard.current_option == Option.DO_NOTHING
