"""Classic game state and turn verbs (Section IV sequencing) — ported from melee.

``GameState`` is the single source of truth for a classic fight: the arena,
every figure, the turn counter, and the dice. It exposes the action verbs a
runner or a test calls — pick options in initiative order, move a figure,
queue and resolve attacks in adjDX order, force a retreat, end the turn — and
each verb enforces the relevant rules, raising :class:`IllegalAction` on a
violation. The nine-turn rulebook Combat Example (p.23-24) drives exactly
this surface (``tests/test_combat_example.py``).

Ported faithfully from melee's ``engine/state.py`` combat slice. Deliberate
trims, each arriving with a later unification milestone rather than silently
diverging: hand-to-hand grappling and its pile mechanics, the shield rush,
spell casting (milestone 5), practice bouts, and the prose narrative module
(log lines here are plain strings). The structural mechanics this module
enforces are the same ones milestone 2 ported onto the shared state types
(engagement, the option taxonomy, per-target force-retreat entitlements,
hit-count injury reactions) — the ported melee tests pin both.
"""
# pyright: reportArgumentType=false, reportReturnType=false
# (the port keeps melee's idioms verbatim: Optional positions assumed placed
# once on the board, and hexarena's Hashable pathfinding Node — the same
# relaxation tarmar-studio applies to the identical hexarena-facing code)

from __future__ import annotations

from dataclasses import dataclass

from hexarena.dice import Dice
from hexarena.hex import Hex
from hexarena.pathfinding import Reach, reachable

from .arena import BODY_COST, CLEAR_COST, Arena
from .combat import AttackResult, DamageEvent
from .data import (
    WeaponKind,
    max_missile_shots,
    missile_reload_turns,
)
from .facing import (
    FRONT,
    attack_zone,
    front_hexes,
    is_engaged,
    is_engaged_by,
    zone_of_direction,
)
from .figure import PER_TURN_FLAGS, Figure, Posture, Race, footprint_for
from .megahex import megahex_distance
from .options import Option, options_for, spec
from .ruleset import DEAD, KNOCKDOWN, UNCONSCIOUS, Ruleset, has_offhand_main_gauche

# Engaged moves that are a "shift": they keep the figure engaged, so the
# destination must stay adjacent to every foe engaging it (p.8). DISENGAGE is
# excluded — it is the one engaged move allowed to break away.
_SHIFT_OPTIONS = frozenset({
    Option.SHIFT_ATTACK, Option.SHIFT_DEFEND, Option.CHANGE_WEAPONS,
})

# The explicit "ready nothing" choice for a weapon switch: the figure
# re-slings its ready weapon and stands bare-handed.
BARE_HANDS_CHOICE = "(bare hands)"


class IllegalAction(Exception):
    """Raised when an action violates the rules."""


@dataclass
class PendingAttack:
    """One queued attack, resolved later in the combat phase.

    * **All kinds:** ``attacker``, ``target``, ``zone`` (target facing
      struck), ``ignore_facing``, ``range_penalty``, ``situational``/
      ``situational_note``, and ``weapon`` (an override, e.g. the off-hand
      main-gauche jab; ``None`` means the ready weapon).
    * **Missile:** ``shots`` (>1 = a high-adjDX bow firing twice) and
      ``second_target`` (the second arrow may aim elsewhere — p.5, p.10).
    * **Thrown:** ``thrown`` (the weapon leaves the thrower's hand).
    * **Pole weapon in/against a charge:** ``damage_dice_bonus`` and
      ``charge_resolve_first``.
    """

    attacker: Figure
    target: Figure
    zone: str | None
    ignore_facing: bool
    range_penalty: int
    shots: int = 1
    situational: int = 0
    situational_note: str = ""
    damage_dice_bonus: int = 0
    charge_resolve_first: bool = False
    thrown: bool = False
    weapon: object | None = None
    second_target: Figure | None = None


def reachable_moves(
    arena: Arena,
    start: Hex,
    budget: int,
    *,
    blocked: set[Hex] | None = None,
    stop_hexes: set[Hex] | None = None,
    body_hexes: set[Hex] | None = None,
) -> Reach:
    """Hexes a figure can finish movement on within ``budget`` hexes (Section V).

    Uses the shared :func:`hexarena.pathfinding.reachable`; this supplies the
    Melee-specific blocked set (standing figures), stop set (enemy front
    hexes), and body-hex entry cost (p.8).
    """
    blocked = blocked or set()
    stop_hexes = stop_hexes or set()
    body_hexes = body_hexes or set()
    return reachable(
        start,
        arena.neighbors,
        lambda _from, to_hex: BODY_COST if to_hex in body_hexes else CLEAR_COST,
        budget,
        must_stop_fn=lambda hex_position: hex_position in stop_hexes,
        blocked=blocked,
    )


class _RosterMixin:
    # ---- rosters / occupancy ----
    @property
    def sides(self) -> list[str]:
        seen: list[str] = []
        for figure in self.figures:
            if figure.side not in seen:
                seen.append(figure.side)
        return seen

    def living(self) -> list[Figure]:
        return [f for f in self.figures if not f.is_dead]

    def enemies_of(self, figure: Figure) -> list[Figure]:
        return [f for f in self.living() if f.side != figure.side and not f.collapsed]

    def occupied(self, *, exclude: Figure | None = None) -> dict[Hex, Figure]:
        """Hexes held by conscious figures (each holds its whole footprint)."""
        held: dict[Hex, Figure] = {}
        layout = self.arena.layout
        for figure in self.figures:
            if figure is exclude or figure.position is None:
                continue
            if figure.is_dead or figure.collapsed:
                continue
            for hex_position in figure.footprint(layout):
                held[hex_position] = figure
        return held

    def figure_at(self, hex_position: Hex) -> Figure | None:
        layout = self.arena.layout
        for figure in self.figures:
            if figure.is_dead:
                continue
            if hex_position in figure.footprint(layout):
                return figure
        return None

    def engaged(self, figure: Figure) -> bool:
        return is_engaged(self.arena.layout, figure, self.enemies_of(figure))


class _TurnMixin:
    # ---- per-character initiative-ordered action selection ----
    def initiative(self) -> list[str]:
        """uids of the living figures, in selection order (adjDX desc, uid)."""
        living = [figure for figure in self.figures if not figure.is_dead]
        ordered = sorted(living, key=lambda figure: (-figure.base_adj_dx, figure.uid))
        return [figure.uid for figure in ordered]

    def begin_selection(self) -> None:
        """Freeze the initiative order for a fresh selection pass (turn start)."""
        self.initiative_order = self.initiative()
        self.active_index = 0
        self.passed = []

    def _figure_by_uid(self, uid: str) -> Figure | None:
        return next((figure for figure in self.figures if figure.uid == uid), None)

    def active_character(self) -> Figure | None:
        """The figure whose turn it is to set an action, or ``None`` when the
        whole selection pass is complete. Passers act last, in the order they
        deferred, each seeing everyone's committed choices."""
        for uid in self.initiative_order[self.active_index:]:
            figure = self._figure_by_uid(uid)
            if figure is None or not figure.can_act():
                continue
            if uid in self.passed or figure.current_option is not None:
                continue
            return figure
        for uid in self.passed:                     # deferred figures resolve last
            figure = self._figure_by_uid(uid)
            if figure is None or not figure.can_act():
                continue
            if figure.current_option is None:
                return figure
        return None

    def _advance_active(self) -> None:
        """Move the first-pass pointer past figures that are now done."""
        while self.active_index < len(self.initiative_order):
            uid = self.initiative_order[self.active_index]
            figure = self._figure_by_uid(uid)
            done = (figure is None or not figure.can_act()
                    or uid in self.passed or figure.current_option is not None)
            if not done:
                break
            self.active_index += 1

    def _require_active(self, figure: Figure) -> None:
        """Raise unless it is ``figure``'s turn to act in the current selection."""
        active = self.active_character()
        if active is None or active.uid != figure.uid:
            raise IllegalAction(f"not {figure.name}'s turn to act")

    def pass_action(self, figure: Figure) -> None:
        """Defer ``figure``'s action to choose last (the Pass rule)."""
        self._require_active(figure)
        if figure.uid in self.passed:
            raise IllegalAction(f"{figure.name} already passed and must act now")
        self.passed.append(figure.uid)
        self._advance_active()
        self.log.append(f"{figure.name} passes, deferring its action.")

    def set_do_nothing(self, figure: Figure) -> None:
        """Set ``figure``'s action to a deliberate no-op (a real, set action)."""
        self.move(figure, Option.DO_NOTHING)

    def stand_down(self, figure: Figure) -> None:
        """Hold ``figure``'s fire this combat step: flip its option to a
        deliberate no-op and cancel any attack it had already queued, without
        re-running movement."""
        figure.current_option = Option.DO_NOTHING
        self._pending = [
            pending for pending in self._pending if pending.attacker is not figure]

    # ---- end of turn ----
    def end_turn(self) -> None:
        """Settle injury flags and reset per-turn state, then advance the turn."""
        for figure in self.figures:
            # Option (g): a STAND UP chosen in movement takes effect now, at
            # the end of the combat phase (p.6-7) — unless a fresh knockdown
            # cancelled the pending rise (p.20).
            if (figure.current_option == Option.STAND_UP
                    and figure.posture != Posture.STANDING
                    and not figure.knocked_down_this_turn
                    and figure.can_act()):
                figure.posture = Posture.STANDING
            figure.wounded_last_turn = (
                figure.hits_this_turn >= figure.wound_hits_threshold
            )
            for flag, default in PER_TURN_FLAGS.items():
                # Copy list defaults so every figure gets its own fresh list.
                fresh = list(default) if isinstance(default, list) else default
                setattr(figure, flag, fresh)
            figure.current_option = None
            # A crossbow reloads a turn closer — but an engaged figure cannot
            # reload (p.16), so its bolt stays unspent until it breaks free.
            if figure.missile_cooldown > 0 and not self.engaged(figure):
                figure.missile_cooldown -= 1
        self._pending.clear()
        self.applied_results.clear()
        self.turn_number += 1
        self.log.append(f"— Turn {self.turn_number} —")
        # Freeze a fresh initiative order for the new turn (skips the dead).
        self.begin_selection()


class _MovementMixin:
    # ---- movement ----
    def _can_fire_from_posture(self, figure: Figure) -> bool:
        """A grounded figure may still loose a missile (p.16): a crossbow from
        prone, any bow from kneeling. A figure knocked prone by damage this
        turn may not."""
        weapon = figure.ready_weapon
        if (weapon is None or weapon.kind != WeaponKind.MISSILE
                or figure.missile_cooldown != 0):
            return False
        if figure.posture == Posture.KNEELING:
            return True
        if figure.posture == Posture.PRONE:
            return weapon.reload > 0 and not figure.knocked_down_this_turn
        return False

    def legal_options(self, figure: Figure) -> list[Option]:
        """The options ``figure`` may legally choose this phase — exactly the
        options :meth:`option_availability` leaves untagged."""
        return [option for option, reason in self.option_availability(figure)
                if reason is None]

    def option_availability(self, figure: Figure) -> list[tuple[Option, str | None]]:
        """The full candidate option set for ``figure`` this phase, each
        tagged with whether it is available and, if not, a short reason. The
        single source of truth for "what is legal"."""
        standing = figure.posture == Posture.STANDING
        weapon = figure.ready_weapon
        has_missile = weapon is not None and weapon.kind == WeaponKind.MISSILE
        can_fire = has_missile and figure.missile_cooldown == 0
        result: list[tuple[Option, str | None]] = []
        for option in options_for(engaged=self.engaged(figure)):
            reason: str | None = None
            if option == Option.STAND_UP:
                if standing:
                    reason = "already standing"
            elif option == Option.CRAWL:
                if standing:
                    reason = "already standing"
                elif not self.reachable(figure, Option.CRAWL):
                    reason = "nowhere to crawl"
            elif not standing:
                # A crossbow (prone) or any bow (kneeling) may still fire.
                if (option == Option.MISSILE_ATTACK
                        and self._can_fire_from_posture(figure)):
                    reason = None
                elif option == Option.GO_PRONE and figure.posture == Posture.PRONE:
                    reason = "already prone"
                elif option == Option.KNEEL and figure.posture == Posture.KNEELING:
                    reason = "already kneeling"
                else:
                    reason = "must stand up first"
            elif spec(option).is_missile and not can_fire:
                reason = "still reloading" if has_missile else "no missile weapon ready"
            elif spec(option).is_attack and not spec(option).is_missile and has_missile:
                # A readied missile weapon has no melee blow.
                reason = "missile weapon ready — no melee attack"
            elif option == Option.SHIFT_DEFEND and weapon is None:
                # A figure defends only with a real weapon in hand to parry
                # with (p.20; ITL p.117).
                reason = "nothing to parry with — no weapon ready"
            elif option == Option.SHIFT_DEFEND and has_missile:
                reason = "nothing to parry with — missile weapon ready"
            elif option == Option.PICK_UP and not self.dropped_in_reach(figure):
                reason = "nothing on the ground in reach"
            elif option == Option.GO_PRONE and not (
                    has_missile and weapon is not None and weapon.reload > 0):
                reason = ("only a crossbow may fire prone" if has_missile
                          else "only when firing a missile weapon")
            elif option == Option.KNEEL and not has_missile:
                reason = "only when firing a missile weapon"
            elif option == Option.HTH_ATTACK:
                reason = "hand-to-hand combat is not yet ported"
            elif option == Option.CAST:
                reason = "classic magic arrives with a later milestone"
            result.append((option, reason))
        result.append((Option.DO_NOTHING, None))
        pass_reason = (None if figure.uid not in self.passed
                       else "already deferred — must act now")
        result.append((Option.PASS, pass_reason))
        return result

    def reach_for(self, figure: Figure, option: Option) -> Reach:
        """The reachability (with paths) of ``figure`` under ``option``.

        The movement budget comes from the ruleset, so a custom movement
        economy is honoured everywhere.
        """
        budget = self.rules.movement_budget(
            figure.movement_allowance, spec(option).movement_cap
        )
        if budget == 0 or figure.position is None:
            return Reach(cost={})
        occupied = self.occupied(exclude=figure)
        if figure.flying:
            reach = reachable_moves(
                self.arena, figure.position, budget, blocked=set(), stop_hexes=set()
            )
            self._drop_unfittable(figure, reach, occupied)
            return reach
        blocked = set(occupied)
        stop_hexes = self._enemy_front_hexes(figure)
        body_hexes = self._body_hexes(exclude=figure)
        reach = reachable_moves(
            self.arena, figure.position, budget,
            blocked=blocked, stop_hexes=stop_hexes, body_hexes=body_hexes,
        )
        if figure.size > 1:
            self._drop_unfittable(figure, reach, occupied)
        if option in _SHIFT_OPTIONS:
            self._restrict_shift_to_engagers(figure, reach)
        return reach

    def reachable(self, figure: Figure, option: Option) -> list[Hex]:
        """Hexes ``figure`` may finish on this turn under ``option``."""
        return self.reach_for(figure, option).reachable_hexes()

    def _engagers(self, figure: Figure) -> list[Figure]:
        """Enemies currently engaging ``figure`` (p.9)."""
        layout = self.arena.layout
        return [enemy for enemy in self.enemies_of(figure)
                if is_engaged_by(layout, figure, enemy)]

    def _stays_adjacent_to_engagers(
        self, figure: Figure, dest: Hex, engagers: list[Figure]
    ) -> bool:
        """Whether ending a shift on ``dest`` keeps ``figure`` adjacent to
        every engaging enemy (p.8). Footprint-aware."""
        if not engagers:
            return True
        layout = self.arena.layout
        dest_footprint = footprint_for(layout, dest, figure.facing, figure.size)
        return all(
            any(layout.distance(here, there) == 1
                for here in dest_footprint for there in enemy.footprint(layout))
            for enemy in engagers
        )

    def _restrict_shift_to_engagers(self, figure: Figure, reach: Reach) -> None:
        """Drop shift destinations that would break adjacency to an engager."""
        engagers = self._engagers(figure)
        if not engagers:
            return
        for hex_position in list(reach.cost):
            if not self._stays_adjacent_to_engagers(figure, hex_position, engagers):
                reach.cost.pop(hex_position, None)
                reach.came_from.pop(hex_position, None)

    def _drop_unfittable(
        self, figure: Figure, reach: Reach, occupied: dict[Hex, Figure]
    ) -> None:
        """Remove destinations whose footprint won't fit there."""
        layout = self.arena.layout
        for hex_position in list(reach.cost):
            footprint = footprint_for(layout, hex_position, figure.facing, figure.size)
            if any(h in occupied or not self.arena.contains(h) for h in footprint):
                reach.cost.pop(hex_position, None)
                reach.came_from.pop(hex_position, None)

    def _enemy_front_hexes(self, figure: Figure) -> set[Hex]:
        fronts: set[Hex] = set()
        for enemy in self.enemies_of(figure):
            # A PRONE enemy has no front and engages no one, so its "front"
            # hexes don't stop a mover; a kneeling enemy keeps both.
            if enemy.posture == Posture.PRONE or enemy.unarmed_wizard:
                continue
            fronts.update(front_hexes(self.arena.layout, enemy))
        return fronts

    def _faced_enemy(self, figure: Figure) -> Figure | None:
        """An enemy standing in ``figure``'s front arc, if any (for the log)."""
        if figure.position is None:
            return None
        fronts = set(front_hexes(self.arena.layout, figure))
        return next((enemy for enemy in self.enemies_of(figure)
                     if enemy.position in fronts), None)

    def melee_targets(self, attacker: Figure, weapon=None) -> list[Figure]:
        """Enemies ``attacker`` can reach with a melee/pole weapon this turn.

        Reach 1 = the three front hexes. A pole weapon (reach 2) also *jabs*
        the front hexes two away (p.12); the straight-ahead jab is blocked by
        anyone standing in the hex between, the diagonal jabs are not.
        """
        layout = self.arena.layout
        weapon = weapon or attacker.ready_weapon
        if attacker.position is None:
            return []
        fronts = set(front_hexes(layout, attacker))
        can_jab = weapon is not None and weapon.reach >= 2
        straight1 = layout.neighbor(attacker.position, attacker.facing)
        straight2 = layout.neighbor(straight1, attacker.facing)
        x_blocked = straight1 in self.occupied(exclude=attacker)
        in_reach: list[Figure] = []
        for enemy in self.enemies_of(attacker):
            if enemy.position is None:
                continue
            enemy_hexes = enemy.footprint(layout)
            if any(hex_position in fronts for hex_position in enemy_hexes):   # reach 1
                in_reach.append(enemy)
            elif can_jab and any(
                    layout.distance(attacker.position, hex_position) == 2
                    and self.in_front_arc(attacker, hex_position)
                    for hex_position in enemy_hexes):
                if enemy_hexes == [straight2] and x_blocked:
                    continue                                     # straight jab blocked
                in_reach.append(enemy)
        return in_reach

    def _body_in_hex(self, hex_position: Hex, *, exclude: Figure | None = None) -> bool:
        """A fallen body (dead/collapsed figure) lies in ``hex_position``."""
        return any(f is not exclude and f.position == hex_position
                   and f.out_of_play for f in self.figures)

    def _body_hexes(self, *, exclude: Figure | None = None) -> set[Hex]:
        """Every hex holding a fallen body — the costly-to-enter obstacles (p.8)."""
        return {f.position for f in self.figures
                if f is not exclude and f.position is not None
                and f.out_of_play}

    def _drop_to_ground(self, weapon, hex_position) -> None:
        """Lay a weapon on the field where it can be picked up later (p.7, q)."""
        if (weapon is not None and hex_position is not None
                and weapon.name != "Thrown rock"):
            self.dropped.append((hex_position, weapon))

    def dropped_in_reach(self, figure: Figure) -> list:
        """Dropped weapons in ``figure``'s hex or an adjacent one (option q)."""
        if figure.position is None:
            return []
        reach = {figure.position, *self.arena.neighbors(figure.position)}
        return [weapon for hex_pos, weapon in self.dropped if hex_pos in reach]

    def pick_up_weapon(self, figure: Figure, weapon_name: str) -> None:
        """Take a named dropped weapon in reach, dropping the current one (p.7, q)."""
        if figure.position is None:
            raise IllegalAction(f"{figure.name} is not on the board")
        reach = {figure.position, *self.arena.neighbors(figure.position)}
        entry = next(((hex_pos, weapon) for (hex_pos, weapon) in self.dropped
                      if weapon.name == weapon_name and hex_pos in reach), None)
        if entry is None:
            raise IllegalAction(f"no {weapon_name} within reach to pick up")
        if figure.ready_weapon is not None:        # drop what you're holding first
            if figure.ready_weapon in figure.weapons:
                figure.weapons.remove(figure.ready_weapon)
            self._drop_to_ground(figure.ready_weapon, figure.position)
        self.dropped.remove(entry)
        weapon = entry[1]
        figure.weapons.append(weapon)
        figure.ready_weapon = weapon
        self.log.append(f"{figure.name} takes up the {weapon.name}.")

    def _discard_thrown(self, attacker: Figure, landing_hex=None) -> None:
        """A thrown weapon leaves the hand and lands on the field (p.15).

        A thrown rock is replenishable so it stays; otherwise the thrower is
        left holding a carried weapon (its dagger), or empty-handed.
        """
        weapon = attacker.ready_weapon
        if weapon is None or weapon.name == "Thrown rock":
            return
        if weapon in attacker.weapons:
            attacker.weapons.remove(weapon)
        self._drop_to_ground(weapon, landing_hex or attacker.position)
        attacker.ready_weapon = next((carried for carried in attacker.weapons), None)

    # ---- flight (thrown and missile weapons, p.15-16) ----
    def _resolve_flight(self, pending, results: list, *, target=None) -> None:
        """A flying weapon's line-of-flight: roll to miss anyone in the way,
        strike the intended target, then fly on if it misses.

        ``target`` overrides ``pending.target`` for a two-shot bow's second
        arrow aimed at a different foe (p.5, p.10).
        """
        attacker = pending.attacker
        layout = self.arena.layout
        if target is None or target is pending.target:
            target = pending.target
            declared_zone = pending.zone
            range_penalty = pending.range_penalty
            situational = pending.situational
            situational_note = pending.situational_note
        else:
            declared_zone = attack_zone(layout, attacker, target)
            megahexes = megahex_distance(layout, attacker.position, target.position)
            range_penalty = self.rules.missile_range_penalty(megahexes)
            situational, situational_note = self._situational_mods(
                attacker, target, attacker.ready_weapon, True)
        held = self.occupied(exclude=attacker)
        adjdx = attacker.base_adj_dx
        # Three sequential phases, each able to end the flight: a blocker in
        # the lane, the intended target, then a stray fly-on (p.15-16).
        if self._flight_blockers_strike(pending, target, adjdx, held, results):
            return
        if self._flight_hit_target(pending, target, declared_zone, range_penalty,
                                   situational, situational_note, results):
            return
        self._flight_fly_on(pending, target, adjdx, held, results)

    def _flight_blockers_strike(
        self, pending, target, adjdx: int, held: dict, results: list
    ) -> bool:
        """Phase 1: figures standing in the lane each roll to be missed — a
        low roll flies past (p.15). Returns True iff a blocker was hit."""
        attacker = pending.attacker
        layout = self.arena.layout
        for hex_pos in layout.line(attacker.position, target.position)[1:-1]:
            blocker = held.get(hex_pos)
            if blocker is None or blocker is target:
                continue
            if blocker.side == attacker.side:
                continue                        # never shoot your own side
            dist = layout.distance(attacker.position, hex_pos)
            if self.dice.total(3) <= adjdx - dist:
                continue                                  # flew past this one
            self._flight_strike(pending, blocker, dist, results)
            return True
        return False

    def _strike(
        self, attacker: Figure, target: Figure, results: list,
        *, thrown: bool = False, **resolve_kwargs
    ) -> AttackResult:
        """Resolve one attack and funnel it through the single record path:
        roll via the ruleset, tag the thrown flag, apply, and append."""
        result = self.rules.resolve_attack(
            self.dice, attacker, target, **resolve_kwargs)
        result.thrown = thrown
        self._apply(attacker, target, result)
        results.append(result)
        return result

    def _flight_hit_target(
        self, pending, target, declared_zone, range_penalty: int,
        situational: int, situational_note: str, results: list
    ) -> bool:
        """Phase 2: the intended target — a normal thrown/missile attack.

        Returns True when the flight ends here — a hit lands the weapon, or a
        fumble (17 drops it, 18 breaks it; p.10) takes it out of the air.
        Returns False on a clean miss, which flies on (phase 3).
        """
        attacker = pending.attacker
        result = self._strike(
            attacker, target, results, thrown=pending.thrown, zone=declared_zone,
            ignore_facing=pending.ignore_facing,
            dice_count=self.rules.attack_dice_count(target, ranged=True),
            range_penalty=range_penalty,
            situational=situational,
            situational_note=situational_note,
            ranged=True)
        if result.hit:
            self._land_flight(pending, target.position)
            return True
        if result.dropped_weapon or result.broke_weapon:
            # ``_apply`` already placed the dropped weapon or removed the
            # broken one; it does not strike a figure behind.
            return True
        return False

    def _flight_fly_on(
        self, pending, target, adjdx: int, held: dict, results: list
    ) -> None:
        """Phase 3: a clean miss flies on up to ten hexes (p.15), striking the
        first figure it does not miss; otherwise it lands by the target."""
        attacker = pending.attacker
        layout = self.arena.layout
        for current in self.arena.ray_past(attacker.position, target.position)[:10]:
            if not self.arena.contains(current):
                break
            figure = held.get(current)
            if figure is None:
                continue
            if figure.side == attacker.side:
                continue                        # never shoot your own side
            dist = layout.distance(attacker.position, current)
            if self.dice.total(3) <= adjdx - dist:        # the stray weapon strikes
                self._flight_strike(pending, figure, dist, results)
                return
        self._land_flight(pending, target.position)   # spent; lands by the target

    def _flight_strike(self, pending, victim, dist, results: list) -> None:
        """A flying weapon that connected mid-flight: apply damage, then land."""
        attacker = pending.attacker
        self._strike(
            attacker, victim, results, thrown=pending.thrown,
            zone=attack_zone(self.arena.layout, attacker, victim),
            ignore_facing=True, range_penalty=-dist, force_hit=True, ranged=True)
        self._land_flight(pending, victim.position)

    def _land_flight(self, pending, landing_hex=None) -> None:
        """Where a spent flying weapon comes to rest: a hurled weapon drops to
        the field; a fired missile is expendable and leaves nothing."""
        if pending.thrown:
            self._discard_thrown(pending.attacker, landing_hex)

    # ---- the movement verb ----
    def move(
        self,
        figure: Figure,
        option: Option,
        *,
        path: list[Hex] | None = None,
        facing: int | None = None,
        ready: str | None = None,
    ) -> None:
        """Execute the movement part of ``option`` for ``figure``.

        ``ready`` names a carried weapon to switch to, valid only with the
        weapon-changing options.
        """
        if not figure.can_act():
            raise IllegalAction(f"{figure.name} cannot act")
        if option == Option.PASS:
            raise IllegalAction("use pass_action to defer a turn")
        # Enforce per-character initiative order, but only once a selection
        # has been opened; while no order is frozen the guard is inert.
        if self.initiative_order:
            self._require_active(figure)
        if option not in self.legal_options(figure):
            raise IllegalAction(f"{option.value} not legal for {figure.name} now")
        path = path or []
        option_spec = spec(option)
        budget = self.rules.movement_budget(
            figure.movement_allowance, option_spec.movement_cap
        )
        path_cost = self._path_cost(figure, path)
        if path_cost > budget:
            raise IllegalAction(
                f"{figure.name} may spend at most {budget} MA on "
                f"{option.value}, but that path costs {path_cost}"
            )
        self._validate_path(figure, path)
        if option in _SHIFT_OPTIONS and path:
            if not self._stays_adjacent_to_engagers(
                    figure, path[-1], self._engagers(figure)):
                raise IllegalAction(
                    f"{figure.name}'s shift must stay adjacent to the foe(s) "
                    f"engaging it -- use Disengage to break away"
                )
        if figure.size > 1:
            self._validate_multihex_turn(figure, path, facing)
        # Validate a weapon SWITCH before mutating the board. Pick-up's reach
        # check intentionally runs after the move.
        if ready is not None and option != Option.PICK_UP:
            self._validate_ready(figure, option, ready)
        if path:
            figure.moved_straight = self._path_is_straight(figure.position, path)
            figure.position = path[-1]
            figure.moved_this_turn = len(path)
        if facing is not None:
            figure.facing = facing % 6
        figure.current_option = option
        figure.dodging = option_spec.sets_dodge
        figure.defending = option_spec.sets_defend
        if option == Option.GO_PRONE:
            figure.posture = Posture.PRONE
        elif option == Option.KNEEL:
            figure.posture = Posture.KNEELING
        # STAND UP is NOT applied here: the figure rises at the end of the
        # combat phase (p.6-7, option g); end_turn performs the rise.
        if ready is not None:
            if option == Option.PICK_UP:
                self.pick_up_weapon(figure, ready)
            else:
                self._ready_weapon(figure, option, ready)
        self.log.append(f"{figure.name}: {option.value}.")
        self._advance_active()

    def turn_in_place_fits(self, figure: Figure, facing: int | None) -> bool:
        """Whether a STATIONARY ``figure`` may turn to ``facing`` — its
        rotated footprint stays on the arena and clear of every figure."""
        if (facing is None or figure.size == 1 or figure.position is None
                or facing % 6 == figure.facing):
            return True
        rotated = footprint_for(
            self.arena.layout, figure.position, facing % 6, figure.size)
        blocked = set(self.occupied(exclude=figure))
        return all(self.arena.contains(hex_position) and hex_position not in blocked
                   for hex_position in rotated)

    def _validate_multihex_turn(
        self, figure: Figure, path: list[Hex], facing: int | None
    ) -> None:
        """Gate the giant's facing changes (footprint rotation is deferred)."""
        if facing is None or facing % 6 == figure.facing:
            return
        if path:
            raise IllegalAction(
                f"{figure.name} cannot turn while moving "
                f"(footprint rotation deferred)"
            )
        if not self.turn_in_place_fits(figure, facing):
            raise IllegalAction(
                f"{figure.name} cannot turn: its rotated footprint would leave "
                f"the arena or hit another figure"
            )

    def ready_choices(self, figure: Figure) -> list[str]:
        """What a Ready Weapon / Change Weapons switch may ready."""
        choices = [carried.name for carried in figure.weapons]
        if figure.ready_weapon is not None:
            choices.append(BARE_HANDS_CHOICE)
        return choices

    def _validate_ready(self, figure: Figure, option: Option, weapon_name: str) -> None:
        """Check a weapon switch is legal, mutating nothing."""
        if weapon_name == BARE_HANDS_CHOICE:
            if option not in (Option.READY_WEAPON, Option.CHANGE_WEAPONS):
                raise IllegalAction(f"{option.value} cannot change weapons")
            if figure.ready_weapon is None:
                raise IllegalAction(
                    f"{figure.name} has nothing readied to re-sling")
            return
        weapon = next((w for w in figure.weapons if w.name == weapon_name), None)
        # A Halfling "may throw any weapon on the same turn he readies it"
        # (p.22): it may ready a THROWABLE weapon as part of a non-missile
        # attack option and then hurl it.
        if (figure.race == Race.HALFLING and weapon is not None
                and weapon.throwable
                and spec(option).is_attack and not spec(option).is_missile):
            return
        if option not in (Option.READY_WEAPON, Option.CHANGE_WEAPONS):
            raise IllegalAction(f"{option.value} cannot change weapons")
        if weapon is None:
            raise IllegalAction(f"{figure.name} is not carrying {weapon_name}")
        if option == Option.CHANGE_WEAPONS and weapon.kind == WeaponKind.MISSILE:
            raise IllegalAction("cannot ready a missile weapon while engaged")

    def _ready_weapon(self, figure: Figure, option: Option, weapon_name: str) -> None:
        """Switch ``figure``'s ready weapon to a carried one (Section IV e/m)
        — or, for :data:`BARE_HANDS_CHOICE`, re-sling it."""
        self._validate_ready(figure, option, weapon_name)
        if weapon_name == BARE_HANDS_CHOICE:
            figure.ready_weapon = None
            self.log.append(f"{figure.name} re-slings its weapon.")
            return
        weapon = next(w for w in figure.weapons if w.name == weapon_name)
        figure.ready_weapon = weapon
        if weapon.two_handed and figure.shield_ready:
            figure.shield_ready = False   # a two-handed weapon needs both hands
        self.log.append(f"{figure.name} readies the {weapon.name}.")

    def _path_cost(self, figure: Figure, path: list[Hex]) -> int:
        """Total MA a move along ``path`` consumes (p.8): entering a hex that
        holds a fallen body costs more; a flyer ignores bodies."""
        if figure.flying:
            return len(path) * CLEAR_COST
        body_hexes = self._body_hexes(exclude=figure)
        return sum(BODY_COST if step in body_hexes else CLEAR_COST for step in path)

    def _validate_path(self, figure: Figure, path: list[Hex]) -> None:
        """Validate each step of ``figure``'s move: in-bounds, adjacent,
        unoccupied, stopping on an enemy front hex; footprint-aware; a flyer
        passes over obstacles but never finishes on one."""
        layout = self.arena.layout
        blocked = set(self.occupied(exclude=figure))
        stop_hexes = self._enemy_front_hexes(figure)
        previous = figure.position
        for index, step in enumerate(path):
            is_last = index == len(path) - 1
            footprint = footprint_for(layout, step, figure.facing, figure.size)
            for hex_position in footprint:
                if not self.arena.contains(hex_position):
                    raise IllegalAction(f"{hex_position} is off the arena")
            if layout.distance(previous, step) != 1:
                raise IllegalAction(f"path step to {step} is not adjacent")
            if figure.flying:
                landing_blocked = any(
                    hex_position in blocked for hex_position in footprint)
                if is_last and landing_blocked:
                    raise IllegalAction(f"{step} is occupied; cannot land there")
            else:
                blocking = next((h for h in footprint if h in blocked), None)
                if blocking is not None:
                    raise IllegalAction(
                        f"{blocking} is occupied; cannot move through it"
                    )
                # must stop on entering an enemy front hex
                if step in stop_hexes and not is_last:
                    raise IllegalAction(
                        f"{figure.name} must stop on entering {step} (enemy front)"
                    )
            previous = step

    def _path_is_straight(self, start: Hex, path: list[Hex]) -> bool:
        """Whether ``start`` + ``path`` runs in a single, unchanging direction
        (p.12, the pole-charge "straight line" rule)."""
        if len(path) < 2:
            return True
        layout = self.arena.layout
        points = [start, *path]
        directions = [
            layout.direction_to(points[index], points[index + 1])
            for index in range(len(points) - 1)
        ]
        return all(direction == directions[0] for direction in directions)

    # ---- aiming ----
    def aim(self, attacker: Figure, target: Figure) -> None:
        """Turn a ranged ``attacker`` to face ``target`` before it fires.

        Option (f) lets a missile attacker change facing, and missiles get no
        facing bonus, so aiming is free and satisfies the front-arc rule
        (p.16) that :meth:`queue_attack` enforces.
        """
        if attacker.position is None or target.position is None:
            return
        line = self.arena.layout.line(attacker.position, target.position)
        if len(line) >= 2:
            direction = self.arena.layout.direction_to(attacker.position, line[1])
            if direction is not None:
                attacker.facing = direction

    def in_front_arc(self, attacker: Figure, point: Hex) -> bool:
        """Whether ``point`` lies in ``attacker``'s front arc, ignoring
        posture — a prone crossbowman still aims along the way it points."""
        if attacker.position is None or point == attacker.position:
            return False
        layout = self.arena.layout
        line = layout.line(attacker.position, point)
        direction = layout.direction_to(attacker.position, line[1])
        if direction is None:
            return False
        return zone_of_direction(attacker.facing, direction) == FRONT

    def _situational_mods(self, attacker: Figure, target: Figure,
                          weapon, is_missile: bool,
                          is_throw: bool = False) -> tuple[int, str]:
        """Circumstantial to-hit modifiers (Section: DX Adjustments, p.16).

        Positive = easier to hit, matching the facing convention.
        """
        mods, notes = 0, []
        layout = self.arena.layout
        # A halfling gets +2 DX whenever it throws something (p.21).
        thrown_attack = is_throw or (
            weapon is not None and weapon.name == "Thrown rock")
        if thrown_attack and attacker.race == Race.HALFLING:
            mods += 2
            notes.append("+2 halfling throw")
        # The giant snake is "very hard to hit": -3 off the attacker's DX (p.21).
        if target.hard_to_hit:
            mods -= target.hard_to_hit
            notes.append(f"-{target.hard_to_hit} hard to hit")
        # A prone crossbowman fires steadied: +1 (p.16).
        if (attacker.posture == Posture.PRONE and is_missile
                and weapon is not None and weapon.reload > 0):
            mods += 1
            notes.append("+1 prone")
        # A braced pole weapon punishes a charging foe: +2 — but only for a
        # figure that stood still (p.12). Not on a 2-hex jab.
        adjacent = (attacker.position is not None and target.position is not None
                    and layout.distance(attacker.position, target.position) == 1)
        if (weapon is not None and weapon.kind == WeaponKind.POLE and adjacent
                and target.current_option == Option.CHARGE_ATTACK
                and attacker.current_option != Option.CHARGE_ATTACK
                and attacker.moved_this_turn == 0):
            mods += 2
            notes.append("+2 vs charge")
        # The ATTACKER fighting from a fallen body's hex has bad footing: -2.
        if attacker.position is not None and self._body_in_hex(
                attacker.position, exclude=attacker):
            mods -= 2
            notes.append("-2 over body")
        # A missile shot at a foe sheltering behind a body in its own hex: -4.
        if (is_missile and target.position is not None
                and self._body_in_hex(target.position, exclude=target)):
            mods -= 4
            notes.append("-4 sheltered")
        return mods, " ".join(notes)


class _CombatMixin:
    # ---- pole-charge helpers (p.12) ----
    def _pole_charge_dice(self, attacker: Figure, target: Figure,
                          weapon, adjacent: bool) -> int:
        """Extra damage dice for a pole weapon in/against a charge (p.12):
        a charge of three-plus straight hexes with a pole weapon (or a braced
        pole meeting one) adds one die."""
        if weapon is None or weapon.kind != WeaponKind.POLE or not adjacent:
            return 0
        charged_in = (attacker.current_option == Option.CHARGE_ATTACK
                      and attacker.moved_this_turn >= 3 and attacker.moved_straight)
        met_charge = (target.current_option == Option.CHARGE_ATTACK
                      and attacker.current_option != Option.CHARGE_ATTACK
                      and attacker.moved_this_turn == 0
                      and target.moved_this_turn >= 3 and target.moved_straight)
        return 1 if charged_in or met_charge else 0

    def _pole_charge_resolve_first(self, attacker: Figure, target: Figure,
                                   weapon, adjacent: bool) -> bool:
        """A pole weapon used in or against a charge strikes first (p.12) —
        independent of the extra die."""
        if weapon is None or weapon.kind != WeaponKind.POLE or not adjacent:
            return False
        return (attacker.current_option == Option.CHARGE_ATTACK
                or (target.current_option == Option.CHARGE_ATTACK
                    and attacker.current_option != Option.CHARGE_ATTACK
                    and attacker.moved_this_turn == 0))

    # ---- declaring attacks ----
    def queue_attack(self, attacker: Figure, target: Figure,
                     *, with_main_gauche: bool = False,
                     second_target: Figure | None = None) -> None:
        """Declare ``attacker``'s attack on ``target`` (resolved later).

        ``with_main_gauche`` also queues a separate off-hand main-gauche jab
        at the same foe, rolled at -4 DX (p.13). ``second_target`` aims a
        two-shot bow's second arrow at a different foe (p.5, p.10).
        """
        option = attacker.current_option
        weapon = self._validate_attack(attacker, target, option)
        is_missile = weapon.kind == WeaponKind.MISSILE
        distance = self.arena.distance(attacker.position, target.position)
        # A throwable melee weapon aimed at a non-adjacent foe is hurled
        # (p.15); adjacent, it's a normal melee blow.
        is_throw = not is_missile and weapon.throwable and distance > 1
        ranged = is_missile or is_throw
        zone = attack_zone(self.arena.layout, attacker, target)
        situational, situational_note = self._situational_mods(
            attacker, target, weapon, ranged, is_throw=is_throw)
        if ranged:
            self._queue_ranged_attack(
                attacker, target, option, weapon, is_missile, is_throw, distance,
                zone, situational, situational_note, second_target)
        else:
            self._queue_melee_attack(
                attacker, target, weapon, zone, situational, situational_note,
                second_target)
        if with_main_gauche:
            self._queue_main_gauche_jab(attacker, target)

    def _validate_attack(self, attacker: Figure, target: Figure, option):
        """Shared guards for declaring any attack (Section VII), returning
        the attacker's ready weapon."""
        if option is None or not spec(option).is_attack:
            raise IllegalAction(
                f"{attacker.name} did not choose an attack option this turn"
            )
        if target.side == attacker.side:
            # No friendly fire: a figure can never target its own side.
            raise IllegalAction(
                f"{attacker.name} cannot attack {target.name} — same side"
            )
        if not attacker.can_act():
            raise IllegalAction(f"{attacker.name} cannot attack")
        if attacker.flying:                       # a flyer lands to attack (p.21)
            raise IllegalAction(f"{attacker.name} must land before it can attack")
        if attacker.attacked_this_turn or any(
            pending.attacker is attacker for pending in self._pending
        ):
            raise IllegalAction(f"{attacker.name} has already attacked this turn")
        # "A figure can never attack if it moved more than half its MA": the
        # movement already taken must fit the attack option's own cap.
        budget = self.rules.movement_budget(
            attacker.movement_allowance, spec(option).movement_cap)
        if attacker.moved_this_turn > budget:
            raise IllegalAction(
                f"{attacker.name} moved {attacker.moved_this_turn} hex(es) this "
                f"turn — too far to attack with {option.value} "
                f"(at most {budget})")
        # Dodge/defend permits no attack; the flags outlive an option overwrite.
        if attacker.dodging or attacker.defending:
            raise IllegalAction(
                f"{attacker.name} is dodging/defending this turn and cannot attack")
        weapon = attacker.ready_weapon
        if weapon is None:
            raise IllegalAction(f"{attacker.name} has no ready weapon")
        is_missile = weapon.kind == WeaponKind.MISSILE
        if spec(option).is_missile != is_missile:
            raise IllegalAction(
                f"{weapon.name} cannot be used with option {option.value}"
            )
        if is_missile and attacker.missile_cooldown > 0:
            raise IllegalAction(f"{weapon.name} is still reloading")
        return weapon

    def _queue_ranged_attack(
        self, attacker: Figure, target: Figure, option, weapon,
        is_missile: bool, is_throw: bool, distance: int, zone,
        situational: int, situational_note: str, second_target: Figure | None,
    ) -> None:
        """Queue a missile or thrown attack (p.15-16). The target must lie in
        the attacker's front arc; only true missile weapons suppress the
        target-facing bonus (``ignore_facing``)."""
        if not self.in_front_arc(attacker, target.position):
            raise IllegalAction(
                f"{target.name} is not in {attacker.name}'s front arc"
            )
        if is_throw:
            range_penalty = -distance     # -1 DX per hex of distance (p.15)
            shots = 1
        else:
            # Missile range is penalised by megahex (MH) distance (p.16).
            megahexes = megahex_distance(
                self.arena.layout, attacker.position, target.position)
            range_penalty = self.rules.missile_range_penalty(megahexes)
            shots = max_missile_shots(weapon, attacker.base_adj_dx)
            if option == Option.ONE_LAST_SHOT:
                shots = 1     # the parting shot looses a single arrow (p.7)
        if second_target is not None:
            if second_target.side == attacker.side:
                raise IllegalAction(
                    f"{attacker.name} cannot aim a shot at "
                    f"{second_target.name} — same side"
                )
            if not is_missile:
                raise IllegalAction(
                    "only a missile weapon may split its two shots between targets"
                )
            if shots < 2:
                raise IllegalAction(
                    f"{attacker.name} gets only one shot this turn — no second target"
                )
            if not self.in_front_arc(attacker, second_target.position):
                raise IllegalAction(
                    f"{second_target.name} is not in {attacker.name}'s front arc"
                )
        self._pending.append(
            PendingAttack(attacker, target, zone=zone,
                          ignore_facing=is_missile, range_penalty=range_penalty,
                          shots=shots, thrown=is_throw,
                          situational=situational, situational_note=situational_note,
                          second_target=second_target)
        )

    def _queue_melee_attack(
        self, attacker: Figure, target: Figure, weapon, zone,
        situational: int, situational_note: str, second_target: Figure | None,
    ) -> None:
        """Queue a single melee blow against a foe within reach (Section VII)."""
        if second_target is not None:
            raise IllegalAction("a melee attack strikes a single target")
        if target not in self.melee_targets(attacker, weapon):
            raise IllegalAction(
                f"{target.name} is not within {attacker.name}'s reach"
            )
        adjacent = self.arena.distance(attacker.position, target.position) == 1
        self._pending.append(
            PendingAttack(attacker, target, zone=zone,
                          ignore_facing=False, range_penalty=0,
                          situational=situational, situational_note=situational_note,
                          damage_dice_bonus=self._pole_charge_dice(
                              attacker, target, weapon, adjacent),
                          charge_resolve_first=self._pole_charge_resolve_first(
                              attacker, target, weapon, adjacent))
        )

    def _queue_main_gauche_jab(self, attacker: Figure, target: Figure) -> None:
        """Queue the off-hand main-gauche's separate -4 DX jab (p.13)."""
        if not has_offhand_main_gauche(attacker):
            raise IllegalAction(
                f"{attacker.name} has no ready main-gauche to jab with"
            )
        if (attacker.position is None or target.position is None
                or self.arena.distance(attacker.position, target.position) != 1):
            raise IllegalAction(
                f"{target.name} is not within {attacker.name}'s main-gauche reach"
            )
        main_gauche = next(w for w in attacker.weapons if w.name == "Main-Gauche")
        zone = attack_zone(self.arena.layout, attacker, target)
        self._pending.append(
            PendingAttack(attacker, target, zone=zone, ignore_facing=False,
                          range_penalty=0, situational=-4,
                          situational_note="-4 main-gauche", weapon=main_gauche)
        )

    # ---- resolving attacks ----
    def _order_dx(self, pending: PendingAttack) -> int:
        """The combat-ordering adjDX of a pending attack (Section VII): the
        full adjDX counting everything BUT missile and thrown weapon range."""
        return self.rules.order_dx(
            pending.attacker, zone=pending.zone,
            ignore_facing=pending.ignore_facing,
        ) + pending.situational

    @staticmethod
    def _shot_count(pending: PendingAttack) -> int:
        """Shots/blows ``pending`` resolves this combat phase, at least one."""
        return max(1, pending.shots)

    def resolve_combat(self) -> list[AttackResult]:
        """Resolve all queued attacks, highest adjDX first (Section VII).

        Exact adjDX ties keep declaration order (a stable sort). Missile fire
        is sequenced in ROUNDS: every figure looses its first shot in adjDX
        order, THEN the high-adjDX bows that earn a second arrow loose it.
        """
        def ordering_key(pending: PendingAttack) -> tuple[int, int]:
            # Pole weapons used in/against a charge strike first, then by
            # adjDX (p.12).
            charge_first = 0 if pending.charge_resolve_first else 1
            return (charge_first, -self._order_dx(pending))

        results: list[AttackResult] = []
        ordered = sorted(self._pending, key=ordering_key)
        max_shots = max((self._shot_count(pending) for pending in ordered), default=1)
        for shot_index in range(max_shots):
            for pending in ordered:
                if shot_index < self._shot_count(pending):
                    self._resolve_attack_shot(pending, shot_index, results)
        self._pending.clear()
        self._drop_bows_after_last_shot()
        self._announce_victory()
        return results

    def _drop_bows_after_last_shot(self) -> None:
        """Enforce the parting-shot rule: after One Last Shot resolves the bow
        leaves the hand and lands on the ground (ITL p.116 / Melee p.7 option
        l), so it cannot fire again on a later engaged turn. This matches the
        rulebook Combat Example, where Wulf shoots once then readies his
        two-handed sword (p.23-24)."""
        for figure in self.figures:
            if figure.current_option != Option.ONE_LAST_SHOT:
                continue
            weapon = figure.ready_weapon
            if weapon is None or weapon.kind != WeaponKind.MISSILE:
                continue
            if weapon in figure.weapons:
                figure.weapons.remove(weapon)
            self._drop_to_ground(weapon, figure.position)
            figure.ready_weapon = None

    def _resolve_attack_shot(
        self, pending: PendingAttack, shot_index: int, results: list
    ) -> None:
        """Resolve one shot/blow of ``pending``. Every guard is re-checked
        per shot — the attacker may have been cut down, or its target
        dropped, by an intervening attack."""
        attacker = pending.attacker
        if not attacker.can_act():
            return          # killed/knocked out before its turn to strike
        if not self._can_strike_now(attacker, shot_index):
            return
        weapon = pending.weapon or attacker.ready_weapon
        is_missile = weapon is not None and weapon.kind == WeaponKind.MISSILE
        flying = pending.thrown or is_missile
        # A two-shot bow's second arrow may aim elsewhere (p.5, p.10).
        if flying and shot_index >= 1:
            target = pending.second_target or pending.target
        else:
            target = pending.target
        # The single "don't strike a downed/dead target" chokepoint: a
        # higher-adjDX attacker this phase may already have felled this foe,
        # and a corpse keeps its hex so the reach check would still pass.
        if target.out_of_play:
            return
        if flying:
            self._resolve_flight(pending, results, target=target)
        else:
            self._resolve_one_melee(pending, weapon, results)

    def _can_strike_now(self, attacker: Figure, shot_index: int) -> bool:
        """The prone / knocked-down / crossbow gate, re-checked every round.

        Prone figures can't fight — except a prone crossbowman who may fire
        steadied (p.16), and NOT if it was knocked prone by damage this same
        phase. Only a two-shot bow reaches a later round; if the bow was
        dropped or broken on a first-shot fumble there is nothing left to
        loose the second arrow.
        """
        crossbow = (attacker.ready_weapon is not None
                    and attacker.ready_weapon.kind == WeaponKind.MISSILE
                    and attacker.ready_weapon.reload > 0
                    and not attacker.knocked_down_this_turn)
        if attacker.posture == Posture.PRONE and not crossbow:
            return False
        if shot_index >= 1 and (attacker.ready_weapon is None
                                or attacker.ready_weapon.kind != WeaponKind.MISSILE):
            return False
        return True

    def _resolve_one_melee(self, pending: PendingAttack, weapon, results: list) -> None:
        """Resolve a single melee / main-gauche blow — always one shot.

        Before rolling, a melee blow can fail to land outright — the foe
        disengaged past a slower attacker, or was pushed out of reach.
        """
        attacker = pending.attacker
        if self._melee_whiffs(pending, weapon):
            self._whiff(attacker, pending.target, weapon, pending, results)
            return
        # Recompute the facing zone against the target's CURRENT posture and
        # facing: an earlier attacker this phase may have knocked the target
        # prone (so it now has no front, scoring +4) or turned it.
        zone = pending.zone
        if not pending.ignore_facing:
            zone = attack_zone(self.arena.layout, attacker, pending.target)
        self._strike(
            attacker, pending.target, results, thrown=pending.thrown,
            zone=zone, weapon=weapon,
            dice_count=self.rules.attack_dice_count(pending.target, ranged=False),
            ranged=False,
            ignore_facing=pending.ignore_facing,
            range_penalty=pending.range_penalty,
            situational=pending.situational,
            situational_note=pending.situational_note,
            extra_dice=pending.damage_dice_bonus,
        )

    def _melee_whiffs(self, pending: PendingAttack, weapon) -> bool:
        """Whether a queued melee blow fails to connect before it is rolled:
        the target disengaged past a slower attacker (only a foe whose
        combat-order adjDX is at least the fleer's own catches it), or is now
        simply out of the weapon's reach."""
        attacker, target = pending.attacker, pending.target
        if target.disengaged_this_turn:
            target_adj_dx = self.rules.order_dx(target, zone=None, ignore_facing=True)
            return self._order_dx(pending) < target_adj_dx
        reach = weapon.reach if weapon is not None else 1
        return (attacker.position is None or target.position is None
                or self.arena.layout.distance(
                    attacker.position, target.position) > reach)

    def _whiff(self, attacker: Figure, target: Figure, weapon,
               pending: PendingAttack, results: list) -> None:
        """A melee blow that never lands: it consumes the attack and logs a
        clean miss, but rolls no dice (a deterministic dice stream stays in
        step) and computes no to-hit number — the blow never reached a roll."""
        result = AttackResult(
            hit=False, rolled=0, needed=0,
            dice_count=self.rules.attack_dice_count(target, ranged=False),
            multiplier=1, raw_damage=0, damage=0,
            dropped_weapon=False, broke_weapon=False, weapon=weapon,
            zone=pending.zone, note="whiff",
        )
        self._apply(attacker, target, result)
        results.append(result)

    def victor(self) -> str | None:
        """The side that has won — the only one still standing, once at least
        two sides entered the fight."""
        if len(self.sides) < 2:
            return None
        standing = {figure.side for figure in self.figures
                    if not figure.out_of_play}
        if len(standing) == 1:
            return next(iter(standing))
        return None

    def _announce_victory(self) -> None:
        """Log the victory once, the first combat phase that settles it."""
        winner = self.victor()
        if winner is not None and not self._victory_announced:
            self._victory_announced = True
            self.log.append(f"Victory: {winner} is the last side standing.")

    def _apply(self, attacker: Figure, target: Figure, result: AttackResult) -> None:
        """The damage/status/audit chokepoint every resolved attack funnels
        through."""
        self.applied_results.append(result)
        attacker.attacked_this_turn = True
        # A fired crossbow must reload before firing again.
        if (result.weapon is not None and result.weapon.kind == WeaponKind.MISSILE
                and result.weapon.reload > 0):
            attacker.missile_cooldown = missile_reload_turns(
                result.weapon, attacker.base_adj_dx) + 1
        # A fumble's own story (dropped/shattered weapon) replaces the swing.
        if result.dropped_weapon or result.broke_weapon:
            verb = "breaks" if result.broke_weapon else "drops"
            weapon_name = result.weapon.name if result.weapon else "weapon"
            self.log.append(f"{attacker.name} {verb} the {weapon_name}!")
            if attacker.ready_weapon in attacker.weapons:
                attacker.weapons.remove(attacker.ready_weapon)
            if result.dropped_weapon:           # dropped lands intact; broken is gone
                # A fumbled melee weapon drops in the attacker's own hex; a
                # thrown weapon (a 17 in flight) drops in the TARGET hex (p.10).
                landing = target.position if result.thrown else attacker.position
                self._drop_to_ground(attacker.ready_weapon, landing)
            attacker.ready_weapon = None
        else:
            outcome = "hits" if result.hit else "misses"
            self.log.append(
                f"{attacker.name} {outcome} {target.name} "
                f"({result.rolled} vs {result.needed}, {result.damage} damage)."
            )
        self.rules.apply_attack_side_effects(attacker, result)
        if not result.hit:
            return
        self.rules.apply_damage(target, result.damage)
        # Audit every damaging hit with both sides so a test can prove no
        # figure is harmed by its own side. Zero-damage hits cost no ST.
        if result.damage > 0:
            self.damage_events.append(DamageEvent(
                attacker_side=attacker.side, target_side=target.side,
                attacker_uid=attacker.uid, target_uid=target.uid,
                damage=result.damage))
        # Force-retreat eligibility (p.20) counts only melee damage: "missile
        # or thrown weapon hits ... don't count."
        if result.damage > 0 and not result.thrown and not (
                result.weapon is not None and result.weapon.kind == WeaponKind.MISSILE):
            attacker.dealt_st_damage_this_turn = True
            # Record WHICH enemy was struck so only that foe can be pushed and
            # each push is spent once.
            if (target.side != attacker.side
                    and target.uid not in attacker.force_retreat_targets_this_turn):
                attacker.force_retreat_targets_this_turn.append(target.uid)
        status = self.rules.status_after_hit(target)
        if status == DEAD:
            target.dead = True
            self.log.append(f"{target.name} is dead.")
        elif status == UNCONSCIOUS:
            target.unconscious = True
            # It FALLS unconscious (p.3): the figure is now a body on the map.
            target.posture = Posture.PRONE
            self.log.append(f"{target.name} falls unconscious.")
        elif status == KNOCKDOWN:
            target.posture = Posture.PRONE
            # Knocked down by damage this turn: it may not attack that turn
            # if it has not already (p.20).
            target.knocked_down_this_turn = True
            self.log.append(f"{target.name} is knocked down.")


class _ForceRetreatMixin:
    # ---- force retreat (Section: Forcing Retreat, p.20) ----
    def can_force_retreat(self, attacker: Figure, target: Figure) -> bool:
        """Whether ``attacker`` may still shove ``target`` back one hex this
        turn — the single gate the menu and the execution path share.

        Membership in ``force_retreat_targets_this_turn`` already encodes:
        the attacker dealt qualifying melee damage, to THIS opposing figure,
        and the push is unspent. Checked here: the attacker took no hits this
        turn, the target is adjacent, still up, and not locked in
        hand-to-hand.
        """
        if attacker.position is None or target.position is None:
            return False
        return (
            target.uid in attacker.force_retreat_targets_this_turn
            and attacker.hits_this_turn == 0
            and not target.in_hth
            and not target.collapsed
            and not target.is_dead
            and self.arena.layout.distance(attacker.position, target.position) == 1
        )

    def force_retreat(
        self, attacker: Figure, target: Figure, *, advance: bool = False
    ) -> Hex:
        """Push ``target`` one hex farther from ``attacker``; optionally follow."""
        if not self.can_force_retreat(attacker, target):
            raise IllegalAction("force retreat not allowed")
        occupied = set(self.occupied(exclude=target))
        layout = self.arena.layout
        start_distance = layout.distance(attacker.position, target.position)

        def footprint_fits(anchor: Hex) -> bool:
            # A multi-hex target must land its WHOLE footprint in-bounds and
            # unoccupied.
            return all(
                self.arena.contains(cell) and cell not in occupied
                for cell in footprint_for(layout, anchor, target.facing, target.size)
            )

        destinations = [
            hex_position
            for hex_position in self.arena.neighbors(target.position)
            if layout.distance(attacker.position, hex_position) > start_distance
            and footprint_fits(hex_position)
        ]
        if not destinations:
            raise IllegalAction("no hex to retreat into")
        # Tie-break deterministically: the hex furthest from the attacker,
        # settled on (col, row) — never iteration order.
        chosen = max(
            destinations,
            key=lambda hex_position: (
                layout.distance(attacker.position, hex_position),
                hex_position.col,
                hex_position.row,
            ),
        )
        vacated = target.position
        target.position = chosen
        if advance:
            attacker.position = vacated
        # Spend the push: one shove per qualifying hit, never a chain.
        if target.uid in attacker.force_retreat_targets_this_turn:
            attacker.force_retreat_targets_this_turn.remove(target.uid)
        followed = " and follows" if advance else ""
        self.log.append(f"{attacker.name} forces {target.name} back{followed}.")
        return target.position


class GameState(
    _RosterMixin,
    _TurnMixin,
    _MovementMixin,
    _CombatMixin,
    _ForceRetreatMixin,
):
    """The single source of truth for a classic fight, composed from the
    responsibility mixins above.

    ``GameState`` itself owns only the shared state every mixin reads through
    ``self`` — the arena, the figures, the dice, the queued attacks, the turn
    counter, the ruleset, and the log.
    """

    def __init__(
        self,
        arena: Arena,
        figures: list[Figure],
        *,
        dice: Dice | None = None,
        ruleset: Ruleset | None = None,
    ):
        self.arena = arena
        self.figures = figures
        self.dice = dice or Dice()
        # The swappable mechanics. Default: classic Melee.
        self.rules = ruleset or Ruleset()
        self.turn_number = 1
        self.log: list[str] = []
        self._pending: list[PendingAttack] = []
        # Damage-attribution audit trail: every damaging hit appends a
        # DamageEvent. Purely observational.
        self.damage_events: list[DamageEvent] = []
        # Every AttackResult that _apply narrates into the live log this turn.
        # Cleared each end_turn; purely observational.
        self.applied_results: list[AttackResult] = []
        # Per-character initiative selection. Left empty until a caller opens
        # a selection with begin_selection(); while empty the move() turn
        # guard is inert.
        self.initiative_order: list[str] = []
        self.active_index: int = 0
        self.passed: list[str] = []
        # Weapons lying on the ground (dropped, fumbled, or thrown).
        self.dropped: list[tuple] = []        # (Hex, Weapon)
        self._victory_announced: bool = False
        for index, figure in enumerate(figures):
            if not figure.uid:
                figure.uid = f"f{index}"
