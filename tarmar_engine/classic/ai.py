"""The classic heuristic opponent — ported from melee's ``engine/ai.py``.

Milestone 4 turned melee's ``engine/`` into facades over this package, and
milestone 5 took its spell layer; ``ai.py`` was one of the last modules with
real code still living there. tarmar-studio#251 is what finally needs it
here: a classic battle served from a database has to be *decided* somewhere,
and the decision vocabulary belongs to the profile that owns the state.

The tactics are melee's, unchanged — no LLM, just rules-aware heuristics, and
above all it **manoeuvres** rather than standing and holding (melee #210):

  * **Posture** — a prone/kneeling figure stands up first.
  * **Engaged (an adjacent enemy):** a loaded bow takes its one last shot
    (option l); a blade in hand strikes (shift-attack); a bow still reloading —
    which can neither strike nor parry (p.13) — drops for a carried melee
    weapon (change weapons) so it can fight, or holds if it carries none.
  * **Missile weapon, not yet in contact:**
      - *Loaded* — **move-and-fire**: it steps up to one hex toward the target
        while it shoots (a missile attack allows a 1-hex step, p.16).
      - *Reloading / no shot* — it **advances at a full run** toward the
        target. Only a boxed-in figure just faces the foe.
  * **Melee weapon:** charge into contact when it can reach an enemy this
    turn, else close the distance at a full run.
  * **Targeting** — focus-fire: it manoeuvres toward, and attacks, the enemy
    with the lowest remaining hit pool (nearest as a tie-break). This reads
    ``current_st``, which both stat models expose (Melee ST or Tarmar
    Fatigue), so the tactics are profile-agnostic while *resolution* stays
    profile-correct in the ruleset.

**What the port changed, and why.** melee's ``take_action`` fused three steps
that a menu-driven UI has to keep apart: work out what to do, choose it, and
carry it out. tarmar-studio draws the option menu, logs every candidate with
its rationale, and — since #250's remote play — replays that menu to validate
a *human's* submitted choice; none of that is possible if deciding and acting
are the same call. So every branch that used to end in ``state.move(...)`` now
returns a :class:`Plan` describing the same move, and :func:`apply` performs
it. The branch structure, the comments and the rulebook citations are
verbatim; only the terminal statements moved.

:func:`take_action` survives as ``apply(plan(...))``, which is what lets
melee's own AI test suite come across unchanged as the acceptance bar (the
pattern milestone 3 used for the Combat Example).

Determinism: :func:`plan` is dice-free and reads only board state, so planning
a figure's turn twice gives the same plan — which is what a replayed menu
rests on.
"""

# pyright: reportArgumentType=false
# (the port keeps melee's idioms verbatim: Optional positions assumed placed
# once on the board, and hexarena's Hashable pathfinding Node — the same
# relaxation tarmar_engine.classic.state applies to the identical
# hexarena-facing code it was ported alongside)
from __future__ import annotations

from dataclasses import dataclass, field

from .data import WeaponKind
from .facing import facing_toward as _facing_toward
from .figure import Figure, Posture
from .options import Option, spec
from .spells import SPELLS, spell_cost_for
from .state import GameState, cast_block_reason

# The simple wizard policy's ST floor (melee #431): a cast is only taken when
# it leaves the caster at least this much ST — the pool is also its hit points
# and casting to 0 is self-knockout (p.3-4), so the AI keeps a fighting margin
# rather than spending itself unconscious. A tactic knob, not a rulebook
# number.
CAST_RESERVE_ST = 4

# The debuffs the simple policy will throw, in fixed preference order — each an
# obviously good effect with no judgment needed (melee #431: "no clever spell
# selection"): fell the foe, disarm it, shatter its blade. Anything subtler
# (Clumsiness/Slow/Stop trade-offs) is left to human players.
_OBVIOUS_DEBUFFS = ("trip", "drop_weapon", "break_weapon")


@dataclass(frozen=True)
class Plan:
    """What the AI intends for one figure this turn — data, not yet done.

    A plan is exactly the arguments :meth:`GameState.move` would have been
    called with, plus the foe the manoeuvre is *about* (``target_uid``), which
    the decision layer needs to name the candidate it shows a player.
    ``Option.DO_NOTHING`` is the held no-op and goes through
    :meth:`GameState.set_do_nothing` instead.
    """

    option: Option
    facing: int | None = None
    path: list = field(default_factory=list)
    ready: str = ""
    target_uid: str = ""


def apply(state: GameState, figure: Figure, plan: Plan) -> None:
    """Carry out ``plan`` through the game's own verbs.

    Raises whatever the verbs raise: a plan is built from the engine's own
    legality (:meth:`GameState.legal_options` / :meth:`GameState.reach_for`),
    so a rejection here is a bug worth surfacing rather than swallowing.
    """
    if plan.option == Option.DO_NOTHING:
        state.set_do_nothing(figure)
        return
    state.move(
        figure,
        plan.option,
        path=list(plan.path) or None,
        facing=plan.facing,
        ready=plan.ready or None,
    )


def _cast_plan(state: GameState, figure: Figure):
    """The simple AI cast for ``figure``: ``(spell, target, st)`` or ``None``.

    Deterministic (no dice) and derived from the engine's own legality
    (:meth:`GameState.spell_targets` — the melee #362 single source), so select
    and combat phases re-derive the same way and a queued cast is always legal:

    * prefer the strongest known **missile spell** (highest damage per die) at
      the focus-fire target, investing the most ST the reserve allows (1..3);
    * else the first **obvious debuff** (trip/drop/break) it can afford at the
      nearest offered target.
    """
    if not figure.spells_known or cast_block_reason(figure) is not None:
        return None
    missiles = sorted(
        (SPELLS[spell_id] for spell_id in figure.spells_known
         if spell_id in SPELLS and SPELLS[spell_id].is_missile),
        key=lambda spell: -spell.damage_per_st)   # 1d > 1d-1 > 1d-2
    for spell in missiles:
        st_max = min(spell.max_st, figure.current_st - CAST_RESERVE_ST)
        if st_max < spell.st_cost:
            continue
        targets = state.spell_targets(figure, spell)
        if targets:
            return spell, _best_target(state, figure, targets), st_max
    for spell_id in _OBVIOUS_DEBUFFS:
        if spell_id not in figure.spells_known or spell_id not in SPELLS:
            continue
        spell = SPELLS[spell_id]
        layout = state.arena.layout
        for target in sorted(
                state.spell_targets(figure, spell),
                key=lambda enemy: layout.distance(figure.position, enemy.position)):
            cost = spell_cost_for(spell, target.strength)
            if figure.current_st - cost >= CAST_RESERVE_ST:
                return spell, target, cost
    return None


def _best_target(state: GameState, figure: Figure, candidates: list[Figure]) -> Figure:
    """Focus-fire: lowest remaining pool, nearest as a tie-break."""
    return min(
        candidates,
        key=lambda e: (e.current_st,
                       state.arena.layout.distance(figure.position, e.position)),
    )


def _adjacent_enemies(
    state: GameState, figure: Figure, enemies: list[Figure]
) -> list[Figure]:
    """The enemies touching ``figure``'s footprint (distance 1 from any of its
    hexes) — the foes it could face and strike this turn without moving."""
    layout = state.arena.layout
    footprint = figure.footprint(layout)
    return [enemy for enemy in enemies
            if min(layout.distance(hex_position, enemy.position)
                   for hex_position in footprint) <= 1]


def _pick_target(state: GameState, figure: Figure) -> Figure | None:
    """The foe to manoeuvre toward and attack: the weakest reachable enemy on
    the field (lowest remaining ST, nearest as a tie-break, via
    :func:`_best_target`), so the AI focus-fires rather than chasing whoever
    happens to be nearest.

    When ``figure`` is engaged it focus-fires among the **adjacent** foes only,
    so it faces and strikes the enemy actually engaging it instead of turning
    its back on it to chase a weaker foe far away — which left it eating rear
    (+4) hits and never swinging (melee #240)."""
    enemies = [e for e in state.enemies_of(figure) if e.position is not None]
    if not enemies or figure.position is None:
        return None
    if state.engaged(figure):
        adjacent = _adjacent_enemies(state, figure, enemies)
        if adjacent:
            return _best_target(state, figure, adjacent)
    return _best_target(state, figure, enemies)


def _turn_in_place_facing(
    state: GameState, figure: Figure, target: Figure
) -> int | None:
    """Facing for a STATIONARY figure turning to face ``target``.

    A single-hex figure turns freely to face its target. A multi-hex figure
    turns only when its rotated footprint fits; otherwise it keeps its current
    facing (``None``) rather than requesting a turn the engine must reject — a
    giant that used to crash its engaged/fire-in-place turns this way (melee
    #153/#250).
    """
    facing = _facing_toward(state.arena.layout, figure.position, target.position)
    return facing if state.turn_in_place_fits(figure, facing) else None


def _has_free_adjacent_hex(state: GameState, figure: Figure) -> bool:
    """Whether a free (unoccupied, on-arena) hex adjoins ``figure`` — somewhere
    a disengage could step into."""
    if figure.position is None:
        return False
    held = set(state.occupied(exclude=figure))
    return any(state.arena.contains(hex_position) and hex_position not in held
               for hex_position in state.arena.neighbors(figure.position))


def _travel_facing(layout, figure: Figure, dest, target: Figure) -> int | None:
    """Facing to set when ``figure`` moves along a path ending on ``dest``.

    A multi-hex figure may translate OR turn-in-place, but not both in one move
    (the engine defers combined rotation+translation, melee #153), so when it
    moves it keeps its facing (``None``). A single-hex figure turns to face its
    target.
    """
    return None if figure.size > 1 else _facing_toward(layout, dest, target.position)


def _closing_move(state: GameState, figure: Figure, target: Figure, option: Option):
    """The reachable destination (with its path) under ``option`` that most
    reduces the distance to ``target``, or ``None`` when nothing closes the gap.

    Reachability comes straight from the engine (:meth:`reach_for`), so every
    destination is legal and multi-hex footprints are already honoured.
    """
    reach = state.reach_for(figure, option)
    hexes = reach.reachable_hexes()
    if not hexes:
        return None
    layout = state.arena.layout
    here = layout.distance(figure.position, target.position)
    dest = min(hexes, key=lambda h: layout.distance(h, target.position))
    if layout.distance(dest, target.position) >= here:
        return None                          # boxed in — nothing gets it closer
    return dest, reach.path_to(dest)


def _weapon_power(weapon) -> float:
    """Expected damage — the AI's yardstick for which weapon to take up."""
    return weapon.damage.count * 3.5 + weapon.damage.modifier


def _rearm_or_close(state: GameState, figure: Figure, target: Figure) -> Plan:
    """Recover from a lost weapon (melee #249/#275) — the fumble table (a
    Tarmar natural 1, classic Melee's 17/18) leaves ``ready_weapon`` empty, and
    a figure that never re-arms can neither attack nor be attacked into
    progress: the fight wedges. So, in order of preference:

    * **engaged, a carried melee weapon** — swap to it (option m).
    * **engaged, only a missile weapon carried** — it can neither ready a bow
      while engaged (p.13) nor fire empty-handed. A dropped MELEE weapon in
      reach is taken up in one step (option q; PICK_UP is engaged-legal — no
      free hex needed). Failing that, if only a missile weapon lies in reach
      and there's a free hex to step to, break away (option n) toward it and
      ready it once free next turn; otherwise hold (a grapple may still be
      declared in the combat phase).
    * **free, a weapon lying in reach** — pick the best one up (option q; a
      fumbled weapon lands in the fumbler's own hex, so this is usually its
      own blade at its feet).
    * **free, carrying a spare** — ready the best carried weapon (option e).
    * **nothing to recover** — close toward the target bare-handed (the
      combat phase may offer a grapple).
    """
    layout = state.arena.layout
    facing = _turn_in_place_facing(state, figure, target)
    if state.engaged(figure):
        melee = next((w for w in figure.weapons
                      if w.kind != WeaponKind.MISSILE), None)
        if melee is not None:
            return Plan(Option.CHANGE_WEAPONS, facing=facing, ready=melee.name,
                        target_uid=target.uid)
        # Carrying only a missile weapon (unreadyable and unfireable while
        # engaged, p.13). A dropped MELEE weapon in reach is the best recovery:
        # PICK_UP is engaged-legal, so it re-arms in ONE step — no free hex
        # needed — where the old two-step disengage could silently no-op when
        # the only "free" hex was blocked by a downed figure (melee #290).
        dropped_melee = [weapon for weapon in state.dropped_in_reach(figure)
                         if weapon.kind != WeaponKind.MISSILE]
        if dropped_melee:
            return Plan(Option.PICK_UP,
                        ready=max(dropped_melee, key=_weapon_power).name,
                        target_uid=target.uid)
        if state.dropped_in_reach(figure) and _has_free_adjacent_hex(state, figure):
            # Only a missile weapon lies in reach — useless to pick up while
            # engaged. Disengage toward it (the step happens in the combat
            # phase), then ready it once free next turn.
            return Plan(Option.DISENGAGE, target_uid=target.uid)
        return Plan(Option.DO_NOTHING, target_uid=target.uid)
    dropped = state.dropped_in_reach(figure)
    if dropped:
        return Plan(Option.PICK_UP, ready=max(dropped, key=_weapon_power).name,
                    target_uid=target.uid)
    if figure.weapons:
        return Plan(Option.READY_WEAPON, facing=facing,
                    ready=max(figure.weapons, key=_weapon_power).name,
                    target_uid=target.uid)
    advance = _closing_move(state, figure, target, Option.MOVE)
    if advance is not None:
        dest, path = advance
        return Plan(Option.MOVE, path=path, target_uid=target.uid,
                    facing=_travel_facing(layout, figure, dest, target))
    return Plan(Option.MOVE, facing=facing, target_uid=target.uid)


def _fight_without_missiles(state: GameState, figure: Figure, target: Figure) -> Plan:
    """Arm for melee in a practice bout, where no missile may ever be loosed
    (p.22).

    A readied bow is dead weight — the engine forbids every missile option — so
    a practice archer must take up a carried melee weapon (Change Weapons when
    engaged, Ready Weapon otherwise) and fight. With no melee weapon carried it
    closes toward the target bare-handed (a grapple may still come in the
    combat phase), or holds when engaged with nothing to do. Prevents the AI
    requesting a shot the practice gate rejects, which used to crash or wedge
    the game (melee #239).
    """
    layout = state.arena.layout
    facing = _turn_in_place_facing(state, figure, target)
    melee = next((weapon for weapon in figure.weapons
                  if weapon.kind != WeaponKind.MISSILE), None)
    if melee is not None:
        option = Option.CHANGE_WEAPONS if state.engaged(figure) else Option.READY_WEAPON
        return Plan(option, facing=facing, ready=melee.name, target_uid=target.uid)
    if state.engaged(figure):
        return Plan(Option.DO_NOTHING, target_uid=target.uid)
    advance = _closing_move(state, figure, target, Option.MOVE)
    if advance is not None:
        dest, path = advance
        return Plan(Option.MOVE, path=path, target_uid=target.uid,
                    facing=_travel_facing(layout, figure, dest, target))
    return Plan(Option.MOVE, facing=facing, target_uid=target.uid)


def plan(state: GameState, figure: Figure) -> Plan | None:
    """What the AI would do for ONE figure this turn (melee #192) — as data.

    ``None`` for a figure with no turn to take: one that cannot act, or one
    already locked in a grapple (it fights in the combat phase). Otherwise the
    same branch melee's ``take_action`` would have executed, returned instead
    of performed. Dice-free and side-effect-free, so it can be called to *show*
    a menu and again to act on one.
    """
    layout = state.arena.layout
    if not figure.can_act():
        return None
    if figure.in_hth:
        # Grappling on the ground; it fights in combat. Commit a no-op so the
        # selection pass counts this figure as done and advances.
        return Plan(Option.DO_NOTHING)
    if figure.posture != Posture.STANDING:
        return Plan(Option.STAND_UP)
    target = _pick_target(state, figure)
    if target is None:
        return Plan(Option.DO_NOTHING)

    # A wizard with clear hands and a castable spell CASTS from where it stands
    # (melee #431): the simple policy prefers magic to marching, standing its
    # ground and facing the foe (a cast needs no closing — missiles fly, thrown
    # spells reach any foe it can turn toward). An ENGAGED wizard keeps the old
    # staff behaviour below; the spell itself is picked in declare_attacks,
    # re-derived from the same _cast_plan so the two phases can never disagree.
    if (not state.engaged(figure)
            and _cast_plan(state, figure) is not None
            and Option.CAST in state.legal_options(figure)):
        return Plan(Option.CAST, target_uid=target.uid,
                    facing=_turn_in_place_facing(state, figure, target))

    weapon = figure.ready_weapon
    if weapon is None:
        # Disarmed by a fumble: re-arm (or close bare-handed) instead of
        # committing to an attack it can never make (melee #275).
        return _rearm_or_close(state, figure, target)
    has_missile = weapon.kind == WeaponKind.MISSILE
    if has_missile and state.practice:
        # No missile may be loosed in a practice bout (p.22): a readied bow can
        # never fire, so arm for melee instead of requesting a shot the engine
        # rejects (melee #239).
        return _fight_without_missiles(state, figure, target)
    can_fire = has_missile and figure.missile_cooldown == 0
    # None if a giant can't rotate:
    turn_facing = _turn_in_place_facing(state, figure, target)

    if state.engaged(figure):
        if can_fire:
            # loaded bow: shoot
            return Plan(Option.ONE_LAST_SHOT, facing=turn_facing,
                        target_uid=target.uid)
        if not has_missile:
            # Stand and strike when the foe is already within reach (turning to
            # face it costs nothing and needs no step); only take the optional
            # 1-hex shift when the target is a hex too far to reach in place. A
            # plain Attack grants no charge/shift bonus (melee #300) — neither
            # does a shift, so this is purely about not moving when a move is
            # pointless.
            weapon_reach = weapon.reach
            in_reach = (state.arena.distance(figure.position, target.position)
                        <= weapon_reach)
            strike = Option.ATTACK if in_reach else Option.SHIFT_ATTACK
            # blade in hand: strike
            return Plan(strike, facing=turn_facing, target_uid=target.uid)
        # Engaged with a reloading bow: it can neither shift-attack nor parry
        # with a missile weapon (both illegal, p.13). Drop the bow for a carried
        # melee weapon so it can fight next turn; if it has none, hold (a legal
        # no-op).
        melee = next((w for w in figure.weapons
                      if w.kind != WeaponKind.MISSILE and w is not weapon), None)
        if melee is not None:
            return Plan(Option.CHANGE_WEAPONS, facing=turn_facing,
                        ready=melee.name, target_uid=target.uid)
        # No carried melee weapon — but a dropped one may lie within reach.
        # PICK_UP is engaged-legal, so re-arm from the ground in one step rather
        # than holding uselessly with an unfightable bow (mirrors
        # _rearm_or_close's engaged branch above).
        dropped_melee = [w for w in state.dropped_in_reach(figure)
                         if w.kind != WeaponKind.MISSILE]
        if dropped_melee:
            return Plan(Option.PICK_UP,
                        ready=max(dropped_melee, key=_weapon_power).name,
                        target_uid=target.uid)
        return Plan(Option.DO_NOTHING, target_uid=target.uid)

    if has_missile:
        if can_fire:
            # Loaded and not in contact: MOVE-AND-FIRE. Step up to one hex
            # toward the target while shooting (p.16) so the archer closes as it
            # looses. A single-hex figure turns to keep the target in its front
            # arc after the step; a giant can't turn while moving, so only close
            # when it can do so without a turn — otherwise it fires in place.
            # Fire in place too when nothing gets it closer (already in contact
            # reach / boxed).
            step = _closing_move(state, figure, target, Option.MISSILE_ATTACK)
            if step is not None and figure.size == 1:
                dest, path = step
                return Plan(Option.MISSILE_ATTACK, path=path,
                            target_uid=target.uid,
                            facing=_facing_toward(layout, dest, target.position))
            return Plan(Option.MISSILE_ATTACK, facing=turn_facing,
                        target_uid=target.uid)
        # Reloading (a crossbow) or no shot worth taking: ADVANCE at a full run
        # toward the target instead of holding — the weapon reloads on its own
        # while it moves (p.16). Only a boxed-in figure just faces the foe.
        advance = _closing_move(state, figure, target, Option.MOVE)
        if advance is not None:
            dest, path = advance
            return Plan(Option.MOVE, path=path, target_uid=target.uid,
                        facing=_travel_facing(layout, figure, dest, target))
        # boxed in; face the foe
        return Plan(Option.MOVE, facing=turn_facing, target_uid=target.uid)

    # Melee: charge into contact if reachable this turn, else close distance.
    charge = state.reach_for(figure, Option.CHARGE_ATTACK)
    contact = [
        h for h in charge.reachable_hexes()
        if layout.distance(h, target.position) == 1
    ]
    if contact:
        dest = min(contact, key=lambda h: layout.distance(h, target.position))
        return Plan(Option.CHARGE_ATTACK, path=charge.path_to(dest),
                    target_uid=target.uid,
                    facing=_travel_facing(layout, figure, dest, target))

    advance = _closing_move(state, figure, target, Option.MOVE)
    if advance is not None:
        dest, path = advance
        return Plan(Option.MOVE, path=path, target_uid=target.uid,
                    facing=_travel_facing(layout, figure, dest, target))
    # boxed in; just face the foe
    return Plan(Option.MOVE, facing=turn_facing, target_uid=target.uid)


def take_action(state: GameState, figure: Figure) -> None:
    """Set the action for ONE computer-controlled ``figure`` (melee #192).

    melee's original entry point, kept exactly: plan, then act. Drives a single
    figure through the same engine verbs a human would use, as its turn comes
    up in the initiative order. The AI never PASSes; a figure with nothing
    useful to do holds position (a real, set action) so the selection pass
    always advances.
    """
    intent = plan(state, figure)
    if intent is not None:
        apply(state, figure, intent)


def _disengage_step(state: GameState, figure: Figure) -> None:
    """Carry out a chosen disengage (option n) in the combat phase (melee #278).

    Steps to the reachable hex that best sets up re-arming: one that keeps a
    dropped weapon in reach, breaking contact so the figure can PICK_UP/READY
    next turn. Among those it prefers the hex furthest from the foes engaging
    it. A boxed-in figure (no free step) keeps its held no-op.
    """
    destinations = [dest for dest in state.disengage_destinations(figure)
                    if state.figure_at(dest) is None]   # free hexes only, no grapple
    if not destinations:
        return
    layout = state.arena.layout
    dropped_hexes = [hex_position for hex_position, _weapon in state.dropped]
    enemies = [enemy for enemy in state.enemies_of(figure)
               if enemy.position is not None]

    def _preference(dest) -> tuple[bool, int]:
        keeps_weapon_in_reach = any(layout.distance(dest, hex_position) <= 1
                                    for hex_position in dropped_hexes)
        distance_from_foes = min((layout.distance(dest, enemy.position)
                                  for enemy in enemies), default=0)
        return (keeps_weapon_in_reach, distance_from_foes)

    state.disengage_move(figure, max(destinations, key=_preference))


def queue_attacks(state: GameState, side: str) -> None:
    """Declare attacks for every figure on ``side`` that chose an attack option."""
    for figure in [f for f in state.figures if f.side == side and f.can_act()]:
        queue_attack_for(state, figure)


def queue_attack_for(state: GameState, figure: Figure) -> None:
    """The per-figure half of :func:`queue_attacks` — melee's loop body.

    Split out so the classic turn runner can declare one figure's attack
    without owning the side loop, and so a figure whose option a *human* chose
    still gets its attack queued the same way.
    """
    if any(pending.attacker is figure for pending in state._pending):
        # A driver that sets options through the game's verbs directly (melee's
        # board, the runner's raw-callable path) queues its own attacks; a
        # second declaration here is the one the engine rejects outright.
        return
    if figure.attacked_this_turn:
        # This figure has already spent its one attack this turn, so there is
        # nothing left to queue. It happens when a bare-handed foe grappled it
        # earlier in THIS combat phase and its defense roll was a 6: the rules
        # give the defender an automatic free hit (p.17), which counts as its
        # attack. Queuing the attack it had selected would be a second attack
        # the engine correctly rejects — don't ask for it (melee #295).
        return
    if figure.current_option == Option.DISENGAGE:
        # A figure that chose to break away (option n) moves instead of
        # attacking; carry out that step now so it can re-arm next turn
        # (melee #278).
        _disengage_step(state, figure)
        return
    if figure.in_hth:                    # locked in a grapple: keep wrestling
        foes = [f for f in state.figures
                if f.uid in figure.hth_opponents and f.can_act()]
        if foes:
            figure.current_option = Option.HTH_ATTACK
            state.hth_attack(figure, min(foes, key=lambda e: e.current_st))
        return
    if figure.current_option == Option.CAST:
        # The wizard declared CAST in the select phase; pick the spell now,
        # re-derived from the same _cast_plan (the board may have shifted — a
        # target felled, ST lost to a blow). Nothing castable any more -> stand
        # down (the melee #397/#398 pattern), never a wedged cast gate.
        if figure.cast_this_turn or any(
                pending.caster is figure for pending in state._pending_casts):
            return                        # already queued/cast this turn
        cast = _cast_plan(state, figure)
        if cast is None:
            state.stand_down(figure)
        else:
            spell, target, st_used = cast
            state.queue_spell(figure, spell, target, st_used=st_used)
        return
    option = figure.current_option
    weapon = figure.ready_weapon
    if weapon is None:
        # Bare hands (a fumble took the weapon): the one attack left is a
        # grapple — take it when the rules allow one (melee #275). A figure that
        # chose to defend or disengage keeps that choice.
        if option is not None and (spec(option).sets_dodge
                                   or spec(option).sets_defend
                                   or option == Option.DISENGAGE):
            return
        foes = state.hth_targets(figure)
        if foes:
            figure.current_option = Option.HTH_ATTACK
            state.hth_attack(figure, min(foes, key=lambda e: e.current_st))
        return
    if option is None or not spec(option).is_attack:
        return
    by_kind = state.attack_candidates(figure)   # the one engine source (melee #362)
    if weapon.kind == WeaponKind.MISSILE:
        if figure.missile_cooldown > 0:
            return                        # still reloading
        # Drive off the engine's candidate list so a computer archer sees the
        # same foes a human does (melee #362): any foe may be targeted, the
        # shooter turns to aim. Keep the AI's own self-preservation filter --
        # never fire into an HTH pile grappling one of our own, since a shot
        # there strikes a RANDOM member (p.18) and could hit the friend
        # (melee #275).
        candidates = [
            e for e in by_kind.ranged
            if not (e.in_hth and any(
                friend.side == figure.side and friend.position == e.position
                for friend in state.figures))]
        if candidates:
            target = _best_target(state, figure, candidates)
            state.aim(figure, target)       # turn to aim, like the human path
            state.queue_attack(figure, target)
        return
    candidates = by_kind.melee              # front hexes + pole jab
    if candidates:
        state.queue_attack(figure, _best_target(state, figure, candidates))
