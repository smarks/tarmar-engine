"""Classic game state and the turn engine (Section IV sequencing) — ported from melee.

``GameState`` is the single source of truth for a fight: the arena, every
figure, the turn counter, and the dice. It exposes the action verbs a UI or a
test calls -- roll initiative, move a figure under a chosen option, queue and
resolve attacks in adjDX order, force a retreat, end the turn -- and each verb
enforces the relevant rules, raising :class:`IllegalAction` on a violation.

A turn runs:
  1. initiative -- each side rolls a die; the winner picks who moves first;
  2. movement -- each side, in order, picks one option per figure and moves;
  3. combat -- queued attacks resolve highest-adjDX first (Section VII);
  4. force retreats -- a figure that hit and was not hit may push its foe;
  5. cleanup / end -- knockdowns settle and injury flags roll forward.

Ported faithfully from melee's ``engine/state.py``. As of the battle/melee
unification's milestone 5 (tarmar-studio#240) the SPELL layer (TFT: Wizard)
lives here natively too — the cast flow (target legality, the queue, DX-ordered
resolution beside the attacks, missile-spell line-of-flight, lasting-spell
bookkeeping and expiry) ported verbatim from melee's ``_SpellcastingMixin``
into :class:`_CombatMixin`'s spell section, reading the classic catalog in
:mod:`.spells`. The nine-turn rulebook Combat Example (p.23-24) drives exactly
this surface (``tests/test_combat_example.py``).
"""
# pyright: reportArgumentType=false, reportReturnType=false, reportOptionalMemberAccess=false, reportAssignmentType=false
# (the port keeps melee's idioms verbatim: Optional positions assumed placed
# once on the board, and hexarena's Hashable pathfinding Node — the same
# relaxation tarmar-studio applies to the identical hexarena-facing code)
from __future__ import annotations

from dataclasses import dataclass

from hexarena.dice import Dice
from hexarena.hex import Hex
from hexarena.pathfinding import Reach

from .arena import BODY_COST, CLEAR_COST, Arena
from .combat import (
    SPELL_LANE_HIT,
    SPELL_MISSED_PAST,
    AttackResult,
    DamageEvent,
    SpellResult,
    classify_spell_roll,
    classify_spell_roll_to_miss,
    roll_missile_spell_damage,
)
from .data import (
    DAGGER,
    MAIN_GAUCHE,
    NO_SHIELD,
    THREE_DICE,
    DamageDice,
    WeaponKind,
    max_missile_shots,
    missile_reload_turns,
)
from .experience import PRACTICE_DROPOUT_ST, CombatType
from .facing import (
    FRONT,
    REAR,
    attack_zone,
    front_hexes,
    is_engaged,
    is_engaged_by,
    zone_of_direction,
    zone_toward,
)
from .figure import PER_TURN_FLAGS, Figure, Posture, Race, footprint_for
from .megahex import megahex_distance
from .movement import reachable_moves
from .narrative import (
    narrate_attack,
    narrate_cascade,
    narrate_cast_lost,
    narrate_dropout,
    narrate_fumble,
    narrate_hth,
    narrate_hth_disengage,
    narrate_move,
    narrate_pass,
    narrate_ready,
    narrate_retreat,
    narrate_shield_rush,
    narrate_spell,
    narrate_spell_applied,
    narrate_spell_disarm,
    narrate_spell_expired,
    narrate_spell_trip,
    narrate_status,
    narrate_trip,
    narrate_turn,
    narrate_victory,
)
from .options import Option, options_for, spec
from .ruleset import DEAD, KNOCKDOWN, UNCONSCIOUS, Ruleset, has_offhand_main_gauche
from .spells import SPELLS, THROWN, spell_cost_for

# Engaged moves that are a "shift": they keep the figure engaged, so the
# destination must stay adjacent to every foe engaging it (p.8, #120). DISENGAGE
# is excluded -- it is the one engaged move allowed to break away.
# CAST belongs here for its ENGAGED form — option (r) "Shift one hex or stand
# still" (wizard-rules line 312, #422): the hex must keep engager adjacency. A
# disengaged caster has no engagers, so the same membership leaves its option
# (h) one-hex move (line 286) unrestricted.
_SHIFT_OPTIONS = frozenset({
    Option.SHIFT_ATTACK, Option.SHIFT_DEFEND, Option.CHANGE_WEAPONS, Option.CAST,
})


class IllegalAction(Exception):
    """Raised when an action violates the rules."""


@dataclass
class PendingAttack:
    """One queued attack, resolved later in the combat phase.

    The flags accumulate across the four attack kinds, and most are specific to
    one of them — grouped here so it's clear which apply where (the field order
    is unchanged; some callers build a PendingAttack positionally):

    * **All kinds:** ``attacker``, ``target``, ``zone`` (target facing struck),
      ``ignore_facing``, ``range_penalty``, ``situational``/``situational_note``
      (circumstantial DX mod + its label), and ``weapon`` (a weapon override,
      e.g. the off-hand main-gauche jab; ``None`` means the ready weapon).
    * **Missile (bow/crossbow):** ``shots`` (>1 = a high-adjDX bow firing twice)
      and ``second_target`` (the second arrow may aim elsewhere — p.5, p.10).
    * **Thrown:** ``thrown`` (the weapon leaves the thrower's hand).
    * **Melee with a pole weapon in/against a charge:** ``damage_dice_bonus``
      (extra damage dice) and ``charge_resolve_first`` (strikes first).
    * **Hand-to-hand (grapple):** ``hth_damage`` (a DamageDice override).
    * **Shield-rush:** ``shield_rush`` (resolved in adjDX order — p.13, #151).
    """

    attacker: Figure
    target: Figure
    zone: str | None
    ignore_facing: bool
    range_penalty: int
    shots: int = 1            # >1 for a high-adjDX bow firing twice in a turn
    situational: int = 0      # circumstantial DX mod (prone, pole-vs-charge, bodies)
    situational_note: str = ""
    damage_dice_bonus: int = 0  # extra damage dice (pole weapon in/against a charge)
    # pole weapon used in/against a charge: strikes first
    charge_resolve_first: bool = False
    thrown: bool = False        # a hurled weapon — it leaves the thrower's hand
    hth_damage: object | None = None  # DamageDice override for a grapple (HTH) attack
    # Weapon override (off-hand main-gauche jab); else ready
    weapon: object | None = None
    # a two-shot bow's 2nd arrow may aim elsewhere (p.5, p.10)
    second_target: Figure | None = None
    # this "attack" is a shield-rush, resolved in adjDX order (p.13, #151)
    shield_rush: bool = False


@dataclass
class PendingCast:
    """One queued spell cast, resolved later in the combat phase (TFT: Wizard).

    Parallel to :class:`PendingAttack`: a wizard declares a cast in the select
    phase and it resolves in :meth:`GameState.resolve_combat`, DX-ordered with the
    attacks. ``st_used`` is the ST the caster commits (1..spell.max_st for a
    missile spell; the flat cost otherwise); ``target`` is the figure the spell
    acts on (the caster itself, for self-protection).
    """

    caster: Figure
    spell: object          # a .spells.Spell (duck-typed for a rebound catalog)
    target: Figure
    st_used: int
    zone: str | None = None
    range_penalty: int = 0
    situational: int = 0
    situational_note: str = ""


# A wizard's staff is the ONLY weapon it may hold and still cast (Wizard p.19,
# p.23): "A wizard who has a staff may keep it in hand at all times, even when
# he is casting spells" (rules lines 947-948). A wizard who knows the Staff
# spell starts with the staff readied (engine.figure.create_wizard); any OTHER
# ready weapon still blocks a cast.
STAFF_WEAPON_NAME = "Staff"

# The explicit "ready nothing" choice for a weapon switch (#425): the figure
# re-slings its ready weapon — it stays carried, nothing drops to the ground —
# and stands bare-handed. This wires the rules' "the weapon must be dropped or
# re-slung" alternative (Wizard p.23, rules lines 1159-1162) for the STAFFLESS
# wizard whose readied weapon blocks its casting with no staff to swap to.
# Offered to ANY figure with a weapon in hand: the rulebook puts no class gate
# on re-slinging; a fighter simply has no reason to use it.
BARE_HANDS_CHOICE = "(bare hands)"


def cast_block_reason(figure: Figure) -> str | None:
    """Why ``figure`` may not cast a spell right now, or ``None`` if it may (p.23).

    The single gate shared by :meth:`GameState.option_availability` (to grey the
    CAST option) and :meth:`GameState.queue_spell` (to reject an illegal cast), so
    the menu and the queue can never disagree. A fighter (no ``spells_known``) is
    not a wizard; a wizard may not cast with a shield up or a non-staff weapon
    ready (Wizard p.23 — a non-staff weapon is a -4 DX so heavy it is treated as
    unusable for casting here).
    """
    if not figure.spells_known:
        return "not a wizard"
    if figure.shield_ready and figure.shield.name != "None":
        return "cannot cast with a shield ready"
    ready = figure.ready_weapon
    if ready is not None and ready.name != STAFF_WEAPON_NAME:
        return "cannot cast with a weapon ready"
    return None


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
        """Hexes held by conscious figures (each figure holds its whole footprint).

        A single-hex figure holds just its position (unchanged); a giant holds
        all three of its footprint hexes, so none of them can be moved into.
        """
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
    # ---- per-character initiative-ordered action selection (#192) ----
    #
    # Each turn every living figure sets one action, in initiative order. Order
    # is adjusted DX (``base_adj_dx``) highest first, ties broken by uid — a pure
    # ordering over an existing stat, so it draws ZERO dice and leaves the seeded
    # combat stream byte-identical (combat RESOLUTION still runs adjDX-ordered and
    # unchanged). The order is frozen at turn start; a figure that PASSes defers
    # and chooses last, once every non-passer has committed.
    def initiative(self) -> list[str]:
        """uids of the living figures, in action-selection order (adjDX desc, uid)."""
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
        whole selection pass is complete.

        First pass: walk ``initiative_order`` from ``active_index``, skipping any
        figure that cannot act (dead or unconscious), those that already set an
        option, and those that passed. Once the first pass is exhausted, the
        passers act last — in the initiative order they deferred in
        (``self.passed``) — each seeing everyone's committed choices. ``None``
        when nobody is left to act.
        """
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
        """Defer ``figure``'s action to choose last (the Pass rule, #192).

        A passer waits until every non-passing figure has committed, then acts in
        initiative order among the passers. It does NOT set ``current_option`` (so
        it still counts as unset), and it may not pass a second time.
        """
        self._require_active(figure)
        if figure.uid in self.passed:
            raise IllegalAction(f"{figure.name} already passed and must act now")
        self.passed.append(figure.uid)
        self._advance_active()
        self.log.append(narrate_pass(figure))

    def set_do_nothing(self, figure: Figure) -> None:
        """Set ``figure``'s action to a deliberate no-op (a real, set action)."""
        self.move(figure, Option.DO_NOTHING)

    def stand_down(self, figure: Figure) -> None:
        """Hold ``figure``'s fire this combat step so the turn can still resolve.

        The combat-phase counterpart to :meth:`set_do_nothing`. A figure that
        committed to an attack option in the select pass but is left with no shot
        the player wants (or is able) to take would otherwise sit in the must-attack
        gate forever, blocking Resolve (#397/#398). Standing it down flips its option
        to a deliberate no-op — which drops it from ``_must_attack`` (DO_NOTHING is
        not an attack) and the combat-actionable set — and cancels any attack it had
        already queued this step. Unlike :meth:`set_do_nothing` it does NOT re-run
        movement (the figure already moved in the select pass); it only clears the
        attack commitment.

        A wizard that declared CAST stands down the same way ("Don't cast", #409):
        the flip to DO_NOTHING drops it from the cast gate, and any spell it had
        already queued this step is cancelled alongside the attacks.
        """
        figure.current_option = Option.DO_NOTHING
        self._pending = [
            pending for pending in self._pending if pending.attacker is not figure]
        self._pending_casts = [
            pending for pending in self._pending_casts if pending.caster is not figure]

    # ---- end of turn ----
    def end_turn(self) -> None:
        """Settle injury flags and reset per-turn state, then advance the turn."""
        for figure in self.figures:
            # Option (g): a STAND UP chosen in movement takes effect now, at the
            # end of the combat phase (p.6-7). The figure stayed prone/kneeling
            # through this turn's combat and only now rises to its feet. (Crawl
            # keeps it grounded and never sets this option.) But a figure knocked
            # down THIS turn — or knocked out / killed — cannot complete the rise:
            # the fresh knockdown cancels the pending stand (p.20), so gate it on
            # a figure that was not just felled and can still act.
            if (figure.current_option == Option.STAND_UP
                    and figure.posture != Posture.STANDING
                    and not figure.knocked_down_this_turn
                    and figure.can_act()):
                figure.posture = Posture.STANDING
            figure.wounded_last_turn = (
                figure.hits_this_turn >= figure.wound_hits_threshold
            )
            for flag, default in PER_TURN_FLAGS.items():
                # Copy list defaults so every figure gets its own fresh list, never
                # a shared alias of the one literal in PER_TURN_FLAGS.
                fresh = list(default) if isinstance(default, list) else default
                setattr(figure, flag, fresh)
            figure.current_option = None
            # A crossbow reloads a turn closer — but an engaged figure cannot
            # reload (p.16), so its bolt stays unspent until it breaks free.
            if figure.missile_cooldown > 0 and not self.engaged(figure):
                figure.missile_cooldown -= 1
            # A grappler who got time to ready a dagger (a 3-4 defense roll) has
            # it in hand from next turn on.
            if figure.hth_drew_dagger:
                dagger = next((w for w in figure.weapons
                               if w.name in self._DAGGERS), None)
                if dagger is not None:
                    figure.ready_weapon = dagger
                    self.log.append(narrate_ready(figure, dagger))
                figure.hth_drew_dagger = False
        # Spell durations tick at the turn's end (#431): stated-duration spells
        # count down (the cast turn was their first), continuing spells end with
        # a felled caster (rules lines 229-232).
        self._expire_active_spells()
        self._pending.clear()
        self._pending_casts.clear()
        self.spell_results.clear()
        self.applied_results.clear()
        self.turn_number += 1
        self.log.append(narrate_turn(self.turn_number))
        # Freeze a fresh initiative order for the new turn (skips the dead). No
        # dice are drawn — ordering is by an existing stat — so the seeded combat
        # stream stays byte-identical across turns.
        self.begin_selection()


class _MovementMixin:
    # ---- movement ----
    def _can_fire_from_posture(self, figure: Figure) -> bool:
        """A grounded figure may still loose a missile (p.16): a crossbow from
        prone, any bow from kneeling. A figure knocked prone by damage this turn
        may not. No missile may be loosed at all in a practice bout (p.22)."""
        if self.practice:
            return False
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
        """The options ``figure`` may legally choose this phase.

        Derived from :meth:`option_availability` — exactly the options it leaves
        untagged (reason ``None``) — so the legal set and the availability menu
        share one source of truth and can never drift (#160). Many callers and
        tests rely on this membership.
        """
        return [option for option, reason in self.option_availability(figure)
                if reason is None]

    def option_availability(self, figure: Figure) -> list[tuple[Option, str | None]]:
        """The full candidate option set for ``figure`` this phase, each tagged with
        whether it is currently available and, if not, a short reason.

        The single source of truth for "what is legal": :meth:`legal_options` is
        exactly the options whose reason is ``None`` here. The UI uses the full
        list to show unavailable options disabled — greyed with a why — instead of
        silently hiding them.
        """
        standing = figure.posture == Posture.STANDING
        weapon = figure.ready_weapon
        has_missile = weapon is not None and weapon.kind == WeaponKind.MISSILE
        # A practice bout blunts every weapon and forbids missiles entirely (p.22).
        can_fire = has_missile and figure.missile_cooldown == 0 and not self.practice
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
                # A crossbow (prone) or any bow (kneeling) may still fire (#152).
                if (option == Option.MISSILE_ATTACK
                        and self._can_fire_from_posture(figure)):
                    reason = None
                elif option == Option.GO_PRONE and figure.posture == Posture.PRONE:
                    # Can't drop into a posture you're already in (#206).
                    reason = "already prone"
                elif option == Option.KNEEL and figure.posture == Posture.KNEELING:
                    reason = "already kneeling"
                else:
                    # From prone you stand or crawl (p.16); everything else — Full
                    # move, Charge, Dodge, Grapple, Pick up, Ready, and a bow that
                    # can't fire from this posture — needs standing up first.
                    reason = "must stand up first"
            elif spec(option).is_missile and not can_fire:
                if self.practice:
                    reason = "no missiles in a practice bout"
                else:
                    reason = ("still reloading" if has_missile
                              else "no missile weapon ready")
            elif spec(option).is_attack and not spec(option).is_missile and has_missile:
                # A readied missile weapon has no melee blow (#79): a non-missile
                # attack option (charge/shift-attack/grapple) is unavailable.
                reason = "missile weapon ready — no melee attack"
            elif option == Option.SHIFT_DEFEND and weapon is None:
                # A figure defends only with a real weapon in hand to parry with
                # (p.20; ITL p.117). A weaponless figure — disarmed on a 17, or an
                # archer whose bow was auto-dropped after its one last shot — has
                # nothing to parry with, so it earns no four-dice defense (#304).
                reason = "nothing to parry with — no weapon ready"
            elif option == Option.SHIFT_DEFEND and has_missile:
                reason = "nothing to parry with — missile weapon ready"
            elif option == Option.PICK_UP and not self.dropped_in_reach(figure):
                reason = "nothing on the ground in reach"
            # Known, deliberate deviation from Melee option (d), the general DROP
            # (move up to half MA then drop prone/kneeling) (#311): the engine
            # models only the firing-posture use of prone/kneel -- a missile user
            # steadying its shot, with movement_cap "none" (no half-MA move). A
            # melee fighter never gains from voluntarily dropping in this engine,
            # so the extra DROP path is intentionally omitted rather than a defect.
            elif option == Option.GO_PRONE and not (has_missile and weapon.reload > 0):
                reason = ("only a crossbow may fire prone" if has_missile
                          else "only when firing a missile weapon")
            elif option == Option.KNEEL and not has_missile:
                reason = "only when firing a missile weapon"
            elif option == Option.HTH_ATTACK and not any(
                    self._can_initiate_hth(figure, enemy)
                    for enemy in self.enemies_of(figure)):
                reason = "no foe in reach to grapple"
            elif option == Option.CAST:
                # A wizard casts only with its hands free of a shield and any
                # non-staff weapon (Wizard p.23); a fighter (no spells) never casts.
                reason = cast_block_reason(figure)
            result.append((option, reason))
        # DO NOTHING is always a legal, deliberate no-op — so "action is set"
        # means current_option is not None, telling "held" apart from "not yet
        # chosen". PASS is offered only while the figure is still in the first
        # selection pass; a passer already resolving last cannot pass again (#192).
        result.append((Option.DO_NOTHING, None))
        pass_reason = (None if figure.uid not in self.passed
                       else "already deferred — must act now")
        result.append((Option.PASS, pass_reason))
        return result

    def reach_for(self, figure: Figure, option: Option) -> Reach:
        """The reachability (with paths) of ``figure`` under ``option``.

        The movement budget comes from the ruleset, so a custom movement economy
        is honoured everywhere -- engine, board highlighting, and path-finding.
        """
        budget = self.rules.movement_budget(
            figure.movement_allowance, spec(option).movement_cap
        )
        if budget == 0 or figure.position is None:
            return Reach(cost={})
        occupied = self.occupied(exclude=figure)
        if figure.flying:
            # Flight ignores ground obstacles: it explores freely (passing over
            # anyone), then any occupied destination is dropped -- a flyer may
            # pass over a figure but not finish on one (p.21). Enemy front hexes
            # don't stop an airborne mover.
            reach = reachable_moves(
                self.arena, figure.position, budget, blocked=set(), stop_hexes=set()
            )
            self._drop_unfittable(figure, reach, occupied)
            return reach
        blocked = set(occupied)
        stop_hexes = self._enemy_front_hexes(figure)
        # A fallen body is a 3-MA obstacle on the ground (p.8); a flyer passing
        # overhead ignores it (handled in the flight branch above).
        body_hexes = self._body_hexes(exclude=figure)
        reach = reachable_moves(
            self.arena, figure.position, budget,
            blocked=blocked, stop_hexes=stop_hexes, body_hexes=body_hexes,
        )
        if figure.size > 1:
            # A multi-hex figure can only finish where its whole footprint fits.
            self._drop_unfittable(figure, reach, occupied)
        if option in _SHIFT_OPTIONS:
            # A shift keeps the figure engaged, so drop any destination that would
            # break adjacency to a foe engaging it (p.8, #120).
            self._restrict_shift_to_engagers(figure, reach)
        return reach

    def _engagers(self, figure: Figure) -> list[Figure]:
        """Enemies currently engaging ``figure`` -- those whose front hex it
        stands in (p.9). A shift must stay adjacent to all of them (p.8)."""
        layout = self.arena.layout
        return [enemy for enemy in self.enemies_of(figure)
                if is_engaged_by(layout, figure, enemy)]

    def _stays_adjacent_to_engagers(
        self, figure: Figure, dest: Hex, engagers: list[Figure]
    ) -> bool:
        """Whether ``figure`` ending a shift on ``dest`` keeps it adjacent to
        every engaging enemy (p.8). Footprint-aware for multi-hex figures."""
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
        """Drop shift destinations that would break adjacency to an engager
        (p.8, #120). Only DISENGAGE may break away. Mutates ``reach``."""
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
        """Remove from ``reach`` any destination whose footprint won't fit there.

        A flyer cannot end on an occupied hex; a multi-hex figure needs every one
        of its footprint hexes in-bounds and unoccupied. Mutates ``reach``.
        """
        layout = self.arena.layout
        for hex_position in list(reach.cost):
            footprint = footprint_for(layout, hex_position, figure.facing, figure.size)
            if any(h in occupied or not self.arena.contains(h) for h in footprint):
                reach.cost.pop(hex_position, None)
                reach.came_from.pop(hex_position, None)

    def reachable(self, figure: Figure, option: Option) -> list[Hex]:
        """Hexes ``figure`` may finish on this turn under ``option``."""
        return self.reach_for(figure, option).reachable_hexes()

    def _enemy_front_hexes(self, figure: Figure) -> set[Hex]:
        fronts: set[Hex] = set()
        for enemy in self.enemies_of(figure):
            # A PRONE enemy has no front: it engages no one (see
            # ``is_engaged_by`` / ``zone_toward``, which treat a prone figure as
            # having no front), so a mover is not forced to halt entering its
            # "front" hex. A KNEELING enemy keeps its front and its stop-hexes
            # per Spencer's rulebook ruling (#354). A staffless wizard is
            # *unarmed* (Wizard p.9) and engages no one either, so its front
            # hexes don't stop a mover — matching ``is_engaged_by``.
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

        Reach 1 = the three front hexes. A pole weapon (reach 2) also *jabs* the
        front hexes two away (p.12); the straight-ahead jab is blocked by anyone
        standing in the hex between, the diagonal jabs are not.
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
        reachable: list[Figure] = []
        for enemy in self.enemies_of(attacker):
            if enemy.position is None:
                continue
            # An attacker reaches a multi-hex foe (the giant) if it reaches any
            # hex of the foe's footprint.
            enemy_hexes = enemy.footprint(layout)
            if any(hex_position in fronts for hex_position in enemy_hexes):   # reach 1
                reachable.append(enemy)
            elif can_jab and any(
                    layout.distance(attacker.position, hex_position) == 2
                    and zone_toward(layout, attacker, hex_position) == FRONT
                    for hex_position in enemy_hexes):
                if enemy_hexes == [straight2] and x_blocked:
                    continue                                     # straight jab blocked
                reachable.append(enemy)
        return reachable

    def _body_in_hex(self, hex_position: Hex, *, exclude: Figure | None = None) -> bool:
        """A fallen body (dead/collapsed figure) lies in ``hex_position``."""
        return any(f is not exclude and f.position == hex_position
                   and f.out_of_play for f in self.figures)

    def _body_hexes(self, *, exclude: Figure | None = None) -> set[Hex]:
        """Every hex holding a fallen body — the costly-to-enter obstacles (p.8)."""
        return {f.position for f in self.figures
                if f is not exclude and f.position is not None
                and f.out_of_play}

    def _hth_pile_at(self, hex_position: Hex | None) -> list[Figure]:
        """The figures grappling in the HTH pile that shares ``hex_position``.

        Members are enumerated by HTH membership, not :meth:`occupied` (which
        excludes the prone/collapsed grapplers in a pile), so the whole brawl is
        captured even though they all sit on one hex (p.18).
        """
        if hex_position is None:
            return []
        return [f for f in self.figures if f.in_hth and f.position == hex_position]

    def _drop_to_ground(self, weapon, hex_position) -> None:
        """Lay a weapon on the field where it can be picked up later (p.7, q)."""
        if (weapon is not None and hex_position is not None
                and weapon.name != "Thrown rock"):
            self.dropped.append((hex_position, weapon))

    def _resolve_flight(self, pending, results: list, *, target=None) -> None:
        """A flying weapon's line-of-flight (p.15-16): roll to miss anyone in the
        way, strike the intended target, then fly on if it misses.

        Thrown and missile weapons share these rules — the target must be in the
        attacker's front, every standing figure in the way is rolled to miss, and
        a stray shot flies on until it hits a figure or leaves the field. The one
        difference is what's left behind: a hurled weapon leaves the hand and
        lands where it strikes (recoverable, ``pending.thrown``); a fired missile
        (arrow, bolt, sling stone) is expendable and drops nothing to pick up.

        ``target`` overrides ``pending.target`` for a two-shot bow's second arrow
        aimed at a different foe (p.5, p.10); the zone, range penalty, and
        situational mods are then recomputed for that foe (#154)."""
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
        # Three sequential phases, each able to end the flight: a blocker in the
        # lane, the intended target, then a stray fly-on (p.15-16).
        if self._flight_blockers_strike(pending, target, adjdx, held, results):
            return
        if self._flight_hit_target(pending, target, declared_zone, range_penalty,
                                   situational, situational_note, results):
            return
        self._flight_fly_on(pending, target, adjdx, held, results)

    def _flight_blockers_strike(
        self, pending, target, adjdx: int, held: dict, results: list
    ) -> bool:
        """Phase 1: figures standing in the lane each roll to be missed — a low
        roll flies past (p.15). The first one the weapon does not miss is struck
        and the flight ends; returns True iff a blocker was hit."""
        attacker = pending.attacker
        layout = self.arena.layout
        for hex_pos in layout.line(attacker.position, target.position)[1:-1]:
            blocker = held.get(hex_pos)
            if blocker is None or blocker is target:
                continue
            if blocker.side == attacker.side:
                continue                        # never shoot your own side (#229)
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
        """Resolve one attack and funnel it through the single record path (#371).

        The "resolve -> tag thrown -> apply -> record" tail that every full attack
        site shares: roll the attack via the ruleset, tag it with the thrown flag,
        apply it (``_apply`` is the damage/status/DamageEvent chokepoint), and
        append it to the returned ``results`` list. Extra keyword arguments pass
        straight through to :meth:`Ruleset.resolve_attack`. Making this one seam
        means a new attack path cannot forget ``result.thrown`` (mis-narrating a
        fumble drop) or ``results.append`` (dropping the blow from the audited
        list). The partial free-hit counter in :meth:`hth_attack` stays outside:
        it deliberately omits the thrown tag and the results append.
        """
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
        """Phase 2: the intended target — a normal thrown/missile attack. A thrown
        weapon takes the target's facing bonus (ignore_facing False); a missile
        never does (p.16) — both are carried on the pending attack.

        A shot aimed at a pile of figures in HTH combat strikes a RANDOM member of
        the pile, not necessarily the one aimed at (p.18). The classic to-hit
        number is the attacker's adjDX and does not depend on which member is
        struck, so the pile member is rolled first and the shot then resolves
        against it (zone/absorption recomputed for whoever it caught).

        Returns True when the flight ends here — a hit lands the weapon, or a
        fumble (17 drops it, 18 breaks it; p.10) takes it out of the air so it does
        NOT fly on. Returns False on a clean miss, which flies on (phase 3)."""
        attacker = pending.attacker
        struck = target
        pile = self._hth_pile_at(target.position)
        if len(pile) >= 2:
            struck = pile[self.dice.dn(len(pile)) - 1]
        zone = (declared_zone if struck is target
                else attack_zone(self.arena.layout, attacker, struck))
        # A shot the pile roll redirected onto the shooter's OWN side is the
        # p.18 rule working as written (like the melee miss-cascade): flag it so
        # the recorded DamageEvent is not read as friendly fire (#231). The flag
        # wraps the whole strike (resolve is pure over the figures and never
        # reads it, so setting it before the roll is harmless).
        redirected_to_friend = struck is not target and struck.side == attacker.side
        if redirected_to_friend:
            self._same_side_hit_ok = True
        try:
            result = self._strike(
                attacker, struck, results, thrown=pending.thrown, zone=zone,
                ignore_facing=pending.ignore_facing,
                dice_count=self.rules.attack_dice_count(struck, ranged=True),
                range_penalty=range_penalty,
                situational=situational,
                situational_note=situational_note,
                ranged=True, blunted=self.practice)
        finally:
            if redirected_to_friend:
                self._same_side_hit_ok = False
        if result.hit:
            self._land_flight(pending, struck.position)
            return True
        if result.dropped_weapon or result.broke_weapon:
            # ``_apply`` already placed the dropped weapon or removed the broken
            # one, so nothing lands here — and it does not strike a figure behind.
            return True
        return False

    def _flight_fly_on(
        self, pending, target, adjdx: int, held: dict, results: list
    ) -> None:
        """Phase 3: a clean miss flies on up to ten hexes (p.15), striking the
        first figure it does not miss; otherwise it spends itself and lands by the
        target.

        The stray follows the exact attacker->target line continued past the
        target (:meth:`Arena.ray_past` — the cube-space ray shared with the
        missile-spell fly-on), not a fixed neighbor direction stepped from the
        lane's last hex, which bends at the target with column parity on this
        offset grid (#429)."""
        attacker = pending.attacker
        layout = self.arena.layout
        for current in self.arena.ray_past(attacker.position, target.position)[:10]:
            if not self.arena.contains(current):
                break
            figure = held.get(current)
            if figure is None:
                continue
            if figure.side == attacker.side:
                continue                        # never shoot your own side (#229)
            dist = layout.distance(attacker.position, current)
            if self.dice.total(3) <= adjdx - dist:        # the stray weapon strikes
                self._flight_strike(pending, figure, dist, results)
                return
        self._land_flight(pending, target.position)   # spent; lands by the target

    def _flight_strike(self, pending, victim, dist, results: list) -> None:
        """A flying weapon that connected mid-flight: apply its damage, then land."""
        attacker = pending.attacker
        self._strike(
            attacker, victim, results, thrown=pending.thrown,
            zone=attack_zone(self.arena.layout, attacker, victim),
            ignore_facing=True, range_penalty=-dist, force_hit=True, ranged=True,
            blunted=self.practice)
        self._land_flight(pending, victim.position)

    def _land_flight(self, pending, landing_hex=None) -> None:
        """Where a spent flying weapon comes to rest. A hurled weapon drops to the
        field (pick-up-able); a fired missile is expendable and leaves nothing."""
        if pending.thrown:
            self._discard_thrown(pending.attacker, landing_hex)

    def _may_take_dropped(self, figure: Figure, weapon) -> bool:
        """Whether ``figure`` may take ``weapon`` off the ground (option q).

        Any dropped weapon may be recovered — except a wizard's staff. "If
        anyone other than the owner of a staff picks it up against his will, it
        explodes, doing the fool who touched it 3 dice damage. A dead wizard's
        staff eventually becomes safer to touch. You don't know when." (Wizard
        p.19, rules lines 950-952.) The zap's mechanics are deliberately vague
        (you never know when it becomes safe), so the simple faithful cut is:
        a non-owner simply cannot pick a staff up; the owner can — a wizard who
        dropped his staff (HTH, a 17 fumble) recovers it like any weapon.
        Ownership is keyed on ``has_staff`` (the wizard who started with one):
        exact for the one-staffed-wizard board; two staffed wizards' identical
        staffs are indistinguishable, an accepted edge.
        """
        if weapon.name != STAFF_WEAPON_NAME:
            return True
        return figure.has_staff

    def dropped_in_reach(self, figure: Figure) -> list:
        """Dropped weapons in ``figure``'s hex or an adjacent one (option q).

        Only what ``figure`` may actually take: a wizard's dropped staff is
        never offered to a non-owner (see :meth:`_may_take_dropped`).
        """
        if figure.position is None:
            return []
        reach = {figure.position, *self.arena.neighbors(figure.position)}
        return [weapon for hex_pos, weapon in self.dropped
                if hex_pos in reach and self._may_take_dropped(figure, weapon)]

    def pick_up_weapon(self, figure: Figure, weapon_name: str) -> None:
        """Take a named dropped weapon in reach, dropping the current one (p.7, q)."""
        if figure.position is None:
            raise IllegalAction(f"{figure.name} is not on the board")
        reach = {figure.position, *self.arena.neighbors(figure.position)}
        entry = next(((hex_pos, weapon) for (hex_pos, weapon) in self.dropped
                      if weapon.name == weapon_name and hex_pos in reach), None)
        if entry is None:
            raise IllegalAction(f"no {weapon_name} within reach to pick up")
        if not self._may_take_dropped(figure, entry[1]):
            # A non-owner cannot take a wizard's staff (Wizard p.19 — it would
            # occult-zap the fool who touched it; see _may_take_dropped).
            raise IllegalAction(
                f"{figure.name} cannot take a wizard's staff — it is not theirs")
        if figure.ready_weapon is not None:        # drop what you're holding first
            if figure.ready_weapon in figure.weapons:
                figure.weapons.remove(figure.ready_weapon)
            self._drop_to_ground(figure.ready_weapon, figure.position)
        self.dropped.remove(entry)
        weapon = entry[1]
        figure.weapons.append(weapon)
        figure.ready_weapon = weapon
        self.log.append(narrate_ready(figure, weapon))

    def _discard_thrown(self, attacker: Figure, landing_hex=None) -> None:
        """A thrown weapon leaves the hand and lands on the field (p.15) where it
        can be recovered. A thrown rock is replenishable so it stays; otherwise the
        thrower is left holding a carried weapon (its dagger), or empty-handed."""
        weapon = attacker.ready_weapon
        if weapon is None or weapon.name == "Thrown rock":
            return
        if weapon in attacker.weapons:
            attacker.weapons.remove(weapon)
        self._drop_to_ground(weapon, landing_hex or attacker.position)
        attacker.ready_weapon = next((carried for carried in attacker.weapons), None)
        if attacker.ready_weapon is not None:
            self.log.append(narrate_ready(attacker, attacker.ready_weapon))

    # ---- general disengage (option n, p.19) ----
    def disengage_destinations(self, figure: Figure) -> list[Hex]:
        """Adjacent hexes a disengaging figure may step into (p.19).

        Empty unless the figure chose to disengage this turn, is standing, and
        has not yet acted — a grounded figure must stand before it can disengage.
        Includes free hexes and the hex of any adjacent enemy it may move onto to
        attempt hand-to-hand combat that same turn (p.19).
        """
        if (figure.current_option != Option.DISENGAGE
                or figure.posture != Posture.STANDING
                or figure.attacked_this_turn
                or figure.position is None):
            return []
        held = set(self.occupied(exclude=figure))
        free = [hex_position for hex_position in self.arena.neighbors(figure.position)
                if self.arena.contains(hex_position) and hex_position not in held]
        hth = [enemy.position for enemy in self.enemies_of(figure)
               if enemy.position is not None
               and self._can_initiate_hth(figure, enemy)]
        return free + hth

    def disengage_move(self, figure: Figure, dest: Hex) -> None:
        """Carry out option (n) general disengage in the combat phase (p.19).

        A figure that chose to disengage moves one hex in any direction instead
        of attacking, breaking engagement. It must be standing first (a
        kneeling/prone/fallen figure must rise before it can disengage). It may
        not also make a normal attack — except that it may move onto an adjacent
        enemy's hex to attempt hand-to-hand combat that same turn (p.19), in which
        case the grapple is initiated. Higher-DX enemies still get their strike
        this turn — the engine resolves their queued attacks normally.
        """
        if figure.current_option != Option.DISENGAGE:
            raise IllegalAction(f"{figure.name} did not choose to disengage this turn")
        if figure.posture != Posture.STANDING:
            raise IllegalAction(f"{figure.name} must stand up before it can disengage")
        if figure.attacked_this_turn:
            raise IllegalAction(f"{figure.name} has already acted this turn")
        if figure.position is None or dest is None:
            raise IllegalAction("a disengage needs a destination hex")
        if self.arena.distance(figure.position, dest) != 1:
            raise IllegalAction("a disengage moves exactly one hex")
        occupant = self.figure_at(dest)
        if occupant is not None and occupant in self.enemies_of(figure):
            # Disengage straight into a grapple on an eligible adjacent foe (p.19).
            if not self._can_initiate_hth(figure, occupant):
                raise IllegalAction(
                    f"{figure.name} cannot grapple {occupant.name}"
                )
            self.hth_attack(figure, occupant)
            return
        if not self.arena.contains(dest) or dest in self.occupied(exclude=figure):
            raise IllegalAction(f"{dest} is not a free hex to disengage into")
        figure.position = dest
        figure.attacked_this_turn = True          # the move replaces its attack
        # Flag the disengage so resolve_combat's melee branch applies the p.19 DX
        # timing: a higher-or-equal-DX foe still strikes as the figure leaves, a
        # lower-DX foe gets no chance (#147).
        figure.disengaged_this_turn = True
        line = narrate_move(figure, Option.DISENGAGE, True)
        if line:
            self.log.append(line)

    def _queue_hth_strike(self, attacker: Figure, defender: Figure) -> None:
        """Queue a grapple strike — always at the +4 'rear' adjustment (p.18)."""
        self._pending.append(PendingAttack(
            attacker, defender, zone=REAR, ignore_facing=False, range_penalty=0,
            hth_damage=self._hth_damage(attacker, defender)))

    def _clear_hth(self, figure: Figure) -> None:
        """Break every grapple ``figure`` is in (it died, or broke free)."""
        for uid in figure.hth_opponents:
            foe = self._by_uid(uid)
            if foe is not None and figure.uid in foe.hth_opponents:
                foe.hth_opponents.remove(figure.uid)
        figure.hth_opponents = []

    def _disperse_pile_survivors(self, survivors: list[Figure]) -> None:
        """Spread freed grapplers onto distinct hexes after their foe leaves (#287).

        To grapple, a figure moves onto its foe's hex (p.17-18), so two allies
        both piling one foe legitimately share that hex while the hand-to-hand
        lock holds. When the foe dies (or collapses) the lock dissolves and the
        survivors are no longer in hand-to-hand -- but they are still stacked on
        the vacated hex, which is only legal for figures actually grappling. One
        survivor may stay put (a corpse blocks no one), and any others stand and
        step to the nearest open hex, mirroring an HTH disengage (p.19), so no two
        conscious same-side figures end the resolution sharing a hex. A 3+ ally
        pile whose vacated hex has every immediate neighbour taken forces the
        search past the first ring (a two-step relocation), so even the boxed-in
        case leaves each freed survivor its own distinct hex (#311).
        """
        occupied = set(self.occupied())
        claimed: set[Hex] = set()
        for survivor in survivors:
            if survivor.position is None or not survivor.can_act():
                continue
            if survivor.in_hth:            # still locked with another foe -- fine
                continue
            if survivor.position not in claimed:
                claimed.add(survivor.position)
                continue
            destination = self._nearest_free_hex(survivor, occupied | claimed)
            if destination is not None:
                survivor.position = destination
                survivor.posture = Posture.STANDING
                claimed.add(destination)

    def _nearest_free_hex(self, figure: Figure, blocked: set[Hex]) -> Hex | None:
        """Breadth-first search outward for the closest hex whose whole footprint
        is in-bounds and clear of ``blocked``.

        One freed grappler needs an adjacent open hex, but a pile with every
        immediate neighbour taken must reach past the first ring. Returns
        ``None`` only when no hex anywhere on the arena can hold the figure -- a
        board packed solid, which a real bout never reaches.
        """
        layout = self.arena.layout
        seen: set[Hex] = {figure.position}
        frontier: list[Hex] = [figure.position]
        while frontier:
            next_frontier: list[Hex] = []
            for here in frontier:
                for neighbor in self.arena.neighbors(here):
                    if neighbor in seen or not self.arena.contains(neighbor):
                        continue
                    seen.add(neighbor)
                    footprint = footprint_for(
                        layout, neighbor, figure.facing, figure.size)
                    if all(self.arena.contains(cell) and cell not in blocked
                           for cell in footprint):
                        return neighbor
                    next_frontier.append(neighbor)
            frontier = next_frontier
        return None


class _HthMixin:
    # ---- hand-to-hand combat (p.17) ----
    _DAGGERS = ("Dagger", "Main-Gauche")

    def _can_initiate_hth(self, attacker: Figure, defender: Figure) -> bool:
        """Whether ``attacker`` may move onto ``defender``'s hex to grapple (p.17).

        Allowed when the defender (a) has its back to the wall, (b) is
        down/kneeling, (c) has a lower MA, or (d) is taken from the rear. A foe
        already in a brawl can always be piled onto (p.18). (Mutual agreement —
        the rulebook's case (d) — is a table call we skip.)
        """
        if attacker.position is None or defender.position is None:
            return False
        if attacker.flying:
            return False          # must land to grapple (p.21)
        if attacker.in_hth:
            return False          # already grappling — fight who you're locked with
        if self.arena.distance(attacker.position, defender.position) != 1:
            return False
        if defender.in_hth:
            return True           # join the brawl (no defense roll, p.18)
        if defender.posture != Posture.STANDING:
            return True
        if defender.movement_allowance < attacker.movement_allowance:
            return True
        if self._has_back_to_wall(attacker, defender):
            return True           # (a) nowhere to give ground — pinned (p.17)
        return attack_zone(self.arena.layout, attacker, defender) == REAR

    def _has_back_to_wall(self, attacker: Figure, defender: Figure) -> bool:
        """Whether ``defender`` has its "back to the wall" against ``attacker`` —
        no hex to give ground into away from the attacker (p.17, HTH case a).

        Conservatively defined to mirror force-retreat: the defender is pinned
        when every neighbouring hex that lies farther from the attacker than the
        defender now stands is off-board or occupied. The attacker's own hex
        counts as occupied (it is, by the attacker), so a foe backed into a board
        edge or wall of figures cannot retreat and may be grappled head-on.
        """
        layout = self.arena.layout
        occupied = set(self.occupied(exclude=defender))
        start_distance = layout.distance(attacker.position, defender.position)
        return not any(
            self.arena.contains(neighbor)
            and neighbor not in occupied
            and layout.distance(attacker.position, neighbor) > start_distance
            for neighbor in self.arena.neighbors(defender.position)
        )

    def hth_targets(self, attacker: Figure) -> list[Figure]:
        """Enemies ``attacker`` could grapple (or, if already grappling, strike)."""
        if attacker.in_hth:       # locked: can only attack a foe it's grappling
            foes = [self._by_uid(uid) for uid in attacker.hth_opponents]
            return [f for f in foes if f is not None and f.can_act()]
        if not attacker.can_act() or attacker.attacked_this_turn:
            return []
        return [e for e in self.enemies_of(attacker)
                if self._can_initiate_hth(attacker, e)]

    def _by_uid(self, uid: str | None) -> Figure | None:
        return next((f for f in self.figures if f.uid == uid), None) if uid else None

    def _hth_grapplers_of(self, defender: Figure, side: str) -> list[Figure]:
        """Figures on ``side`` currently grappling ``defender``."""
        return [f for f in self.figures
                if f.side == side and defender.uid in f.hth_opponents]

    def _hth_damage(self, attacker: Figure, defender: Figure) -> DamageDice:
        """Damage dice for a grapple strike (p.18): a ready dagger/main-gauche, else
        bare hands — 1d-3 when two-plus gang up, otherwise the lone fighter's
        strength against the *total* of the foes it grapples."""
        ready = attacker.ready_weapon
        if ready is not None and ready.name == "Dagger":
            return DAGGER.hth_damage or DAGGER.damage
        if ready is not None and ready.name == "Main-Gauche":
            return MAIN_GAUCHE.hth_damage or MAIN_GAUCHE.damage
        if len(self._hth_grapplers_of(defender, attacker.side)) >= 2:
            return DamageDice(1, -3)        # two-plus on a side each get 1d-3
        foes = [self._by_uid(uid) for uid in attacker.hth_opponents]
        total = sum(f.strength for f in foes if f is not None) or defender.strength
        if attacker.strength > total:
            return DamageDice(1, -2)        # stronger than all its foes together
        if attacker.strength == total:
            return DamageDice(1, -3)
        return DamageDice(1, -4)            # outmuscled

    def _grapple_bare(self, figure: Figure) -> None:
        """Drop a non-dagger ready weapon and shield to grapple bare-handed.

        On a defense roll of 1-4 the defender "drops his ready weapon and/or
        shield" (Melee p.17 / ITL p.116) — dropped to the *ground*, not merely
        slung. Every HTH strike is forced to REAR (the +4 grapple rule), and a
        slung (unready) shield stops rear attacks, so leaving ``figure.shield``
        in place would let a "dropped" large shield keep absorbing every grapple
        blow. Shed the shield to NO_SHIELD as well so it cannot count while
        grappling (#251)."""
        if figure.ready_weapon is None or figure.ready_weapon.name not in self._DAGGERS:
            if figure.ready_weapon is not None:
                if figure.ready_weapon in figure.weapons:
                    figure.weapons.remove(figure.ready_weapon)
                self._drop_to_ground(figure.ready_weapon, figure.position)
            figure.ready_weapon = None
            figure.shield_ready = False
            figure.shield = NO_SHIELD

    def _link_hth(self, attacker: Figure, defender: Figure) -> None:
        attacker.position = defender.position
        attacker.posture = defender.posture = Posture.PRONE
        if defender.uid not in attacker.hth_opponents:
            attacker.hth_opponents.append(defender.uid)
        if attacker.uid not in defender.hth_opponents:
            defender.hth_opponents.append(attacker.uid)

    def hth_attack(self, attacker: Figure, defender: Figure) -> str:
        """Declare ``attacker``'s hand-to-hand attack on ``defender``.

        Strikes if already grappling; joins a brawl in progress without a roll
        (p.18); otherwise initiates with the defender's 1d6 defense roll (p.17).
        """
        if defender.side == attacker.side:
            raise IllegalAction(
                f"{attacker.name} cannot grapple {defender.name} — same side"
            )
        if defender.uid in attacker.hth_opponents:     # already grappling — just strike
            self._queue_hth_strike(attacker, defender)
            return "grappled"
        if not self._can_initiate_hth(attacker, defender):
            raise IllegalAction(f"{attacker.name} cannot grapple {defender.name}")
        # a fresh grapple: defender rolls
        if not defender.in_hth:
            from_rear = attack_zone(self.arena.layout, attacker, defender) == REAR
            roll = self.dice.dn(6)
            while from_rear and roll == 6:              # a 6 is ignored from behind
                roll = self.dice.dn(6)
            if roll == 5:                               # shrugged off, no grapple
                self.log.append(narrate_hth(attacker, defender, "shrug"))
                return "shrugged"
            if roll == 6:                               # free hit, attacker thrown back
                # The defender "automatically gets a hit" (p.17) — it doesn't roll
                # to-hit, so force the hit rather than letting it whiff (#126).
                counter = self.rules.resolve_attack(
                    self.dice, defender, attacker,
                    zone=attack_zone(self.arena.layout, defender, attacker),
                    dice_count=self.rules.attack_dice_count(attacker, ranged=False),
                    force_hit=True, blunted=self.practice)
                self.log.append(narrate_hth(attacker, defender, "free_hit"))
                self._apply(defender, attacker, counter)
                return "free_hit"
            self._grapple_bare(defender)
            if roll in (3, 4) and any(
                    w.name in self._DAGGERS for w in defender.weapons):
                defender.hth_drew_dagger = True         # readies a dagger next turn
        self._grapple_bare(attacker)
        self._link_hth(attacker, defender)
        self.log.append(narrate_hth(attacker, defender,
                                    "join" if len(defender.hth_opponents) > 1
                                    else "grapple"))
        self._queue_hth_strike(attacker, defender)
        return "grappled"

    def attempt_hth_disengage(self, figure: Figure) -> bool:
        """Try to break out of a grapple (option v, p.19) instead of striking.

        Rolls 1d6: a figure whose DX beats its lone foe's needs 1-3; against an
        equal/superior foe, or more than one, it needs a 1. On success it stands
        and slips to an adjacent empty hex. Either way it forgoes its attack.
        """
        if not figure.in_hth:
            raise IllegalAction(f"{figure.name} is not in hand-to-hand")
        figure.attacked_this_turn = True            # the attempt replaces an attack
        foes = [self._by_uid(uid) for uid in figure.hth_opponents]
        foes = [f for f in foes if f is not None]
        superior = len(foes) == 1 and figure.base_adj_dx > foes[0].base_adj_dx
        needed = 3 if superior else 1
        if self.dice.dn(6) > needed:
            self.log.append(narrate_hth_disengage(figure, False))
            return False
        self._clear_hth(figure)
        figure.posture = Posture.STANDING
        held = set(self.occupied(exclude=figure))
        dest = next((h for h in self.arena.neighbors(figure.position)
                     if self.arena.contains(h) and h not in held), None)
        if dest is not None:
            figure.position = dest
        self.log.append(narrate_hth_disengage(figure, True))
        return True


class _ShieldRushMixin:
    # ---- shield-rush (p.13) ----
    def _can_shield_rush(self, attacker: Figure) -> bool:
        """Whether ``attacker`` could shield-rush this combat phase (p.13).

        Needs a ready shield (large or small) and a free hand for the rush — so a
        standing, un-grappled figure that has not yet attacked this turn.
        """
        return (attacker.can_act()
                and not attacker.attacked_this_turn
                and not attacker.in_hth
                and not attacker.flying
                and attacker.posture == Posture.STANDING
                and attacker.shield_ready
                and attacker.shield.name != "None")

    def shield_rush_targets(self, attacker: Figure) -> list[Figure]:
        """Adjacent enemies in ``attacker``'s front it could shield-rush (p.13)."""
        if not self._can_shield_rush(attacker) or attacker.position is None:
            return []
        fronts = set(front_hexes(self.arena.layout, attacker))
        return [enemy for enemy in self.enemies_of(attacker)
                if enemy.position in fronts]

    def shield_rush(self, attacker: Figure, target: Figure) -> str:
        """Declare a shield-rush to floor a foe (p.13), resolved in adjDX order.

        Instead of a weapon attack, a figure with a ready shield rushes an
        adjacent front enemy. "The shield-rush is considered an attack for all
        purposes" (p.13), so — like every other blow — it resolves in adjDX order
        during :meth:`resolve_combat`, not the instant it is declared (#151). It
        is therefore *queued* here (mirroring how :meth:`_queue_hth_strike` queues
        a grapple) and rolled at the rusher's adjDX slot, so a higher-DX victim
        gets its own strike in *before* it is knocked down.

        The one outcome that needs neither a roll nor any ordering is a foe more
        than twice the rusher's (original) ST: the rush simply cannot move it, so
        that is settled at once. Returns ``"no_effect"`` in that case, otherwise
        ``"queued"`` (the hit/save/knockdown land later in :meth:`resolve_combat`,
        which logs the ``miss``/``fall``/``stand`` story).
        """
        if target.side == attacker.side:
            raise IllegalAction(
                f"{attacker.name} cannot shield-rush {target.name} — same side"
            )
        if not self._can_shield_rush(attacker):
            raise IllegalAction(f"{attacker.name} cannot shield-rush")
        layout = self.arena.layout
        if (target.position is None
                or self.arena.distance(attacker.position, target.position) != 1
                or target.position not in set(front_hexes(layout, attacker))):
            raise IllegalAction(
                f"{target.name} is not an adjacent foe in {attacker.name}'s front")
        attacker.attacked_this_turn = True        # the rush replaces its attack
        # Compare ORIGINAL ST (not the wounded current ST); a foe more than twice
        # as strong simply isn't moved — no roll, no ordering, settle it now.
        if target.strength > 2 * attacker.strength:
            self.log.append(narrate_shield_rush(attacker, target, "no_effect"))
            return "no_effect"
        zone = attack_zone(layout, attacker, target)
        self._pending.append(PendingAttack(
            attacker, target, zone=zone, ignore_facing=False, range_penalty=0,
            shield_rush=True))
        return "queued"

    def _resolve_shield_rush(self, pending: PendingAttack, results: list) -> None:
        """Resolve a queued shield-rush at the rusher's adjDX slot (p.13, #151).

        Roll to hit as usual; a miss does nothing. On a hit the target makes a
        saving roll against its adjDX or falls prone — three dice when the rusher's
        *original* ST is at least the target's, two when the rusher is weaker. A 12
        on two dice, or 16/17/18 on three, is an automatic fall. A shield-rush
        never inflicts hits. Resolving here (not at declaration) means a higher-DX
        foe has already struck before it is floored.
        """
        attacker, target = pending.attacker, pending.target
        if not attacker.can_act() or attacker.posture != Posture.STANDING:
            return                       # floored or downed before its slot — rush lost
        layout = self.arena.layout
        if (target.out_of_play or target.position is None
                or self.arena.distance(attacker.position, target.position) != 1
                or target.position not in set(front_hexes(layout, attacker))):
            return                       # the foe is gone, down, or no longer in reach
        zone = attack_zone(layout, attacker, target)
        needed = self.rules.to_hit_number(attacker, zone=zone)
        dice_count = self.rules.attack_dice_count(target, ranged=False)
        rolled = self.dice.total(dice_count)
        hit, _multiplier, _dropped, _broke = self.rules.classify_roll(
            rolled, dice_count, needed)
        if not hit:
            self.log.append(narrate_shield_rush(attacker, target, "miss"))
            return
        saving_dice = 3 if attacker.strength >= target.strength else 2
        save_roll = self.dice.total(saving_dice)
        auto_fall = ((saving_dice == 2 and save_roll == 12)
                     or (saving_dice == 3 and save_roll >= 16))
        if auto_fall or save_roll > target.base_adj_dx:
            target.posture = Posture.PRONE
            if target.in_hth:
                self._clear_hth(target)           # a floored grappler loses its hold
            self.log.append(narrate_shield_rush(attacker, target, "fall"))
        else:
            self.log.append(narrate_shield_rush(attacker, target, "stand"))

    def _pole_charge_dice(self, attacker: Figure, target: Figure,
                          weapon, adjacent: bool) -> int:
        """Extra damage dice for a pole weapon in/against a charge (p.12).

        A pole used against a charging foe always does one extra die. Used *in* a
        charge it earns the die only when the attacker moved three-plus hexes in a
        STRAIGHT line (p.12), not merely three hexes. A jab (non-adjacent strike)
        never earns it.
        """
        if weapon is None or weapon.kind != WeaponKind.POLE or not adjacent:
            return 0
        against_charge = target.current_option == Option.CHARGE_ATTACK
        in_charge = (attacker.current_option == Option.CHARGE_ATTACK
                     and attacker.moved_this_turn >= 3
                     and attacker.moved_straight)
        return 1 if (against_charge or in_charge) else 0

    def _pole_charge_resolve_first(self, attacker: Figure, target: Figure,
                                   weapon, adjacent: bool) -> bool:
        """Whether a pole weapon used in or against a charge resolves before all
        other attacks (p.12).

        This holds for ANY pole weapon used in a charge attack OR against one,
        independent of how far the charger moved and of whether the strike earns
        the +1 damage die — so even a one-hex pole charge strikes first. A jab
        (non-adjacent strike) is a regular attack and never resolves first.
        """
        if weapon is None or weapon.kind != WeaponKind.POLE or not adjacent:
            return False
        return (attacker.current_option == Option.CHARGE_ATTACK
                or target.current_option == Option.CHARGE_ATTACK)

    def _situational_mods(self, attacker: Figure, target: Figure,
                          weapon, is_missile: bool,
                          is_throw: bool = False) -> tuple[int, str]:
        """Circumstantial to-hit modifiers (Section: DX Adjustments, p.16).

        Positive = easier to hit, matching the facing convention.
        """
        mods, notes = 0, []
        layout = self.arena.layout
        # A halfling gets +2 DX whenever it throws something (p.21). "Throwing"
        # means a hurled weapon or a thrown rock, not a fired bow/sling.
        thrown_attack = is_throw or (
            weapon is not None and weapon.name == "Thrown rock")
        if thrown_attack and attacker.race == Race.HALFLING:
            mods += 2
            notes.append("+2 halfling throw")
        # A wizard's "DX is -4 with any weapon except his staff" (Wizard p.23,
        # rules lines 1159-1162) — every attack path funnels through here, so
        # this one line covers melee blows, thrown weapons, and missile fire.
        # The staff is exempt; so are bare hands (no weapon at all).
        if (attacker.spells_known and weapon is not None
                and weapon.name != STAFF_WEAPON_NAME):
            mods -= 4
            notes.append("-4 wizard weapon")
        # The giant snake is "very hard to hit": -3 off the attacker's DX (p.21).
        if target.hard_to_hit:
            mods -= target.hard_to_hit
            notes.append(f"-{target.hard_to_hit} hard to hit")
        # A Blurred target subtracts 4 "from DX of all attacks/spells against
        # subject" (spell-ref lines 8-10; DX table lines 322-323). Weapon attacks
        # take it here; casts take it in queue_spell (the cast path never calls
        # _situational_mods).
        blur = target.spell_defense_dx_penalty()
        if blur:
            mods += blur
            notes.append(f"{blur:+d} blurred")
        # A prone crossbowman fires steadied: +1 (p.16).
        if (attacker.posture == Posture.PRONE and is_missile
                and weapon is not None and weapon.reload > 0):
            mods += 1
            notes.append("+1 prone")
        # A braced pole weapon punishes a charging foe: +2 — but only for a figure
        # that "stands still (or simply changes facing)" (p.12). A pole user that
        # took a shift moving a hex (or charged itself) does not get it. Not on a
        # 2-hex jab.
        adjacent = (attacker.position is not None and target.position is not None
                    and layout.distance(attacker.position, target.position) == 1)
        if (weapon is not None and weapon.kind == WeaponKind.POLE and adjacent
                and target.current_option == Option.CHARGE_ATTACK
                and attacker.current_option != Option.CHARGE_ATTACK
                and attacker.moved_this_turn == 0):
            mods += 2
            notes.append("+2 vs charge")
        # The ATTACKER fighting from a fallen body's hex has bad footing: -2 to its
        # own to-hit (p.16, "Standing in a hex with a fallen body") — #125.
        if attacker.position is not None and self._body_in_hex(
                attacker.position, exclude=attacker):
            mods -= 2
            notes.append("-2 over body")
        # A missile shot at a foe sheltering behind a body: -4. The sheltering
        # body lies in the TARGET's own hex — "Any figure may lie prone or kneel
        # in the same hex with a sheltering body" (ITL p.117) — not one step
        # toward the shooter (#337). Bodies BETWEEN shooter and target are the
        # separate in-flight blocking rule, handled elsewhere.
        if (is_missile and target.position is not None
                and self._body_in_hex(target.position, exclude=target)):
            mods -= 4
            notes.append("-4 sheltered")
        return mods, " ".join(notes)

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
        weapon-changing options (Ready Weapon when disengaged, Change Weapons
        when engaged).
        """
        if not figure.can_act():
            raise IllegalAction(f"{figure.name} cannot act")
        if option == Option.PASS:
            # PASS defers rather than setting an action — it goes through
            # pass_action(), never move() (which commits current_option).
            raise IllegalAction("use pass_action to defer a turn")
        # Enforce per-character initiative order, but only once a selection has
        # actually been opened (begin_selection). While no order is frozen the
        # guard is inert, so movement-mechanics tests still drive move() directly.
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
            # A shift must keep the figure adjacent to every foe engaging it
            # (p.8, #120); use DISENGAGE to break away instead.
            if not self._stays_adjacent_to_engagers(
                    figure, path[-1], self._engagers(figure)):
                raise IllegalAction(
                    f"{figure.name}'s shift must stay adjacent to the foe(s) "
                    f"engaging it -- use Disengage to break away"
                )
        if figure.size > 1:
            self._validate_multihex_turn(figure, path, facing)
        # Validate a weapon SWITCH before mutating the board, so a rejected ready
        # (unknown weapon, or a missile readied while engaged) leaves position,
        # facing, and posture untouched (#77). Pick-up's reach check intentionally
        # runs after the move — you grab from the hex you end on.
        if ready is not None and option != Option.PICK_UP:
            self._validate_ready(figure, option, ready)
        if path:
            # Record straightness for the pole-charge extra-die rule (p.12): the
            # +1 damage die needs a charge of three-plus hexes "in a straight
            # line", not merely three hexes moved. A path is straight when every
            # step runs in the same hex direction (computed over start -> path).
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
        # STAND UP is NOT applied here: a figure rises "at the end of the combat
        # phase" (p.6-7, option g), so it stays prone/kneeling through this turn's
        # combat — still struck as having no front (+4) — and end_turn performs
        # the rise. (Crawl keeps it grounded and is handled by the path move.)
        if ready is not None:
            if option == Option.PICK_UP:
                self.pick_up_weapon(figure, ready)
            else:
                self._ready_weapon(figure, option, ready)
        line = narrate_move(figure, option, bool(path), self._faced_enemy(figure))
        if line:
            self.log.append(line)
        # A valid set advances the initiative pointer to the next actor.
        self._advance_active()

    def turn_in_place_fits(self, figure: Figure, facing: int | None) -> bool:
        """Whether a STATIONARY ``figure`` may turn to ``facing`` — its rotated
        footprint stays on the arena and clear of every other figure.

        A single-hex figure (or a no-op turn) always fits; a multi-hex figure's
        in-place rotation is gated because footprint rotation is deferred (#153).
        Shared by :meth:`_validate_multihex_turn` (the raising authority) and the
        AI's turn-in-place choice, so the legality test has one source of truth and
        the AI can never request a turn the engine must reject (#250).
        """
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
        """Gate the giant's facing changes (footprint rotation is deferred).

        A multi-hex figure may **translate** freely (footprint validated by
        :meth:`_validate_path`) or **turn in place** when stationary (the rotated
        footprint must fit, :meth:`turn_in_place_fits`). Turning *while* moving --
        combined rotation and translation -- is the hard case and is deferred, so
        it's rejected.
        """
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
        """What a Ready Weapon / Change Weapons switch may ready: every carried
        weapon, plus :data:`BARE_HANDS_CHOICE` when something is in hand to
        re-sling (#425). The single source for the client's weapon selector, so
        the menu and :meth:`_validate_ready` can never drift."""
        choices = [carried.name for carried in figure.weapons]
        if figure.ready_weapon is not None:
            choices.append(BARE_HANDS_CHOICE)
        return choices

    def _validate_ready(self, figure: Figure, option: Option, weapon_name: str) -> None:
        """Check a weapon switch is legal, mutating nothing. Called both up front
        (before the board is touched, #77) and again inside :meth:`_ready_weapon`."""
        if weapon_name == BARE_HANDS_CHOICE:
            # Readying bare hands (#425): re-sling whatever is in hand. Legal on
            # the same two switch options as any weapon change; meaningless — so
            # rejected — when nothing is readied.
            if option not in (Option.READY_WEAPON, Option.CHANGE_WEAPONS):
                raise IllegalAction(f"{option.value} cannot change weapons")
            if figure.ready_weapon is None:
                raise IllegalAction(
                    f"{figure.name} has nothing readied to re-sling")
            return
        weapon = next((w for w in figure.weapons if w.name == weapon_name), None)
        # A Halfling "may throw any weapon on the same turn he readies it" (p.22):
        # readying ordinarily ends the action, but a halfling may ready a
        # THROWABLE weapon as part of a (non-missile) attack option and then hurl
        # it. Every other figure must ready on its own turn (option e/m).
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
        """Switch ``figure``'s ready weapon to a carried one (Section IV e/m) —
        or, for :data:`BARE_HANDS_CHOICE`, re-sling it and ready bare hands
        (#425): the weapon stays carried, nothing drops to the ground."""
        self._validate_ready(figure, option, weapon_name)
        if weapon_name == BARE_HANDS_CHOICE:
            figure.ready_weapon = None
            self.log.append(narrate_ready(figure, None))
            return
        weapon = next(w for w in figure.weapons if w.name == weapon_name)
        figure.ready_weapon = weapon
        if weapon.two_handed and figure.shield_ready:
            figure.shield_ready = False   # a two-handed weapon needs both hands
        self.log.append(narrate_ready(figure, weapon))

    def _path_cost(self, figure: Figure, path: list[Hex]) -> int:
        """Total MA a move along ``path`` consumes (p.8).

        Each step normally costs :data:`~engine.arena.CLEAR_COST`; entering a hex
        that holds a fallen body costs :data:`~engine.arena.BODY_COST` instead. A
        flyer overhead ignores bodies, so every step costs the clear rate. This
        mirrors the cost function :func:`~engine.movement.reachable_moves` uses, so
        the move-budget check and the reachability highlight always agree.
        """
        if figure.flying:
            return len(path) * CLEAR_COST
        body_hexes = self._body_hexes(exclude=figure)
        return sum(BODY_COST if step in body_hexes else CLEAR_COST for step in path)

    def _validate_path(self, figure: Figure, path: list[Hex]) -> None:
        """Validate each step of ``figure``'s move.

        For a single-hex figure each step must be in-bounds, adjacent, and
        unoccupied, stopping on an enemy front hex. A multi-hex figure
        **translates** -- the whole footprint slides one hex per step keeping its
        facing -- so every footprint hex of every step is checked. A flyer passes
        over ground obstacles (and over enemy fronts), so only its final
        destination must be clear.
        """
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
                # Airborne: ignore obstacles in transit, but never finish on one.
                if is_last and any(
                        hex_position in blocked for hex_position in footprint):
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
        """Whether ``start`` + ``path`` runs in a single, unchanging direction.

        A move of zero or one step is trivially straight. Otherwise every
        consecutive pair of hexes must share the same direction index (p.12, the
        pole-charge "straight line" diagram).
        """
        if len(path) < 2:
            return True
        layout = self.arena.layout
        points = [start, *path]
        directions = [
            layout.direction_to(points[index], points[index + 1])
            for index in range(len(points) - 1)
        ]
        return all(direction == directions[0] for direction in directions)


@dataclass
class AttackCandidates:
    """The foes a figure may attack this combat phase, grouped by attack kind.

    ``ranged`` covers a weapon attack made *at a distance* — a bow/crossbow's
    missile targets **or** a throwable weapon's thrown targets (mutually exclusive
    for one figure). This is the engine's single source for attack legality (#362);
    the Django view's target list and the AI both consume it, so a human and a
    computer figure standing in the same spot see the same options.
    """

    melee: list[Figure]
    ranged: list[Figure]
    hth: list[Figure]


class _CombatMixin:
    # ---- combat ----
    def declarable_attack_option(self, figure: Figure) -> Option:
        """The option a combat-phase attack declaration would run under (#413).

        A figure that already committed to an attack option keeps it; otherwise
        this is the option the board layer's ``_ensure_attack_option`` would
        assign — the single definition shared by that view helper and
        :meth:`attack_candidates`, so the targets the UI offers and the option a
        declaration is validated against can never diverge.
        """
        option = figure.current_option
        if option is not None and spec(option).is_attack:
            return option
        weapon = figure.ready_weapon
        if weapon is not None and weapon.kind == WeaponKind.MISSILE:
            return (Option.ONE_LAST_SHOT if self.engaged(figure)
                    else Option.MISSILE_ATTACK)
        return (Option.SHIFT_ATTACK if self.engaged(figure)
                else Option.CHARGE_ATTACK)

    def _movement_permits_attack(self, figure: Figure) -> bool:
        """Whether the movement ``figure`` already spent this turn still allows
        an attack declaration (#413): the hexes moved must fit the declarable
        attack option's own cap (options.py movement_cap) — "a figure can never
        attack if it moved more than half its MA" (wizard-rules lines 273-274).
        The mirror of :meth:`_validate_attack`'s guard, applied to the candidate
        lists so the UI never offers a target the queue would reject."""
        option = self.declarable_attack_option(figure)
        budget = self.rules.movement_budget(
            figure.movement_allowance, spec(option).movement_cap)
        return figure.moved_this_turn <= budget

    def attack_candidates(self, figure: Figure) -> AttackCandidates:
        """Which foes ``figure`` may attack this combat phase, by kind (#362).

        The one authority for "who may I hit": based on where the figure stands
        and what weapon is ready. Attacks are chosen in the combat phase, so no
        movement-time attack declaration is required. A figure that committed to
        defending (dodge/defend) or to disengaging does not attack.

        A missile/thrown attacker lists **every** foe with a position — the front
        arc is satisfied by turning to aim (option f is a free facing change and
        missiles get no facing bonus, p.16), which :meth:`aim` does before the
        shot. That is a legality rule, not a preference: a figure *may* fire into
        an HTH pile grappling a friend (a random member is struck, p.18); whether
        that is *wise* is the caller's call (the AI declines it — #275).
        """
        empty = AttackCandidates([], [], [])
        if not (figure.can_act() and not figure.attacked_this_turn
                and figure.position is not None):
            return empty
        option = figure.current_option
        if option is not None and (spec(option).sets_dodge or spec(option).sets_defend):
            return empty
        # A figure that chose to disengage moves instead of attacking (option n,
        # p.19); it may never attack the turn it disengages.
        if option == Option.DISENGAGE:
            return empty
        # Already grappling: the only attack is the hand-to-hand strike on that foe.
        if figure.in_hth:
            return AttackCandidates([], [], self.hth_targets(figure))
        hth = self.hth_targets(figure)          # foes it could grapple
        weapon = figure.ready_weapon
        if weapon is None:
            return AttackCandidates([], [], hth)
        # A figure that already moved beyond what its declarable attack option
        # permits cannot strike or fire this turn (#413) — don't offer targets
        # the queue would reject.
        if not self._movement_permits_attack(figure):
            return AttackCandidates([], [], hth)
        if weapon.kind == WeaponKind.MISSILE:
            if figure.missile_cooldown > 0:
                return AttackCandidates([], [], hth)   # still reloading — can't fire
            ranged = [e for e in self.enemies_of(figure) if e.position is not None]
            return AttackCandidates([], ranged, hth)
        melee = self.melee_targets(figure, weapon)
        # A throwable weapon can be hurled at any foe out of melee reach (p.15); the
        # thrower turns to aim (:meth:`aim`), so the front arc is satisfied.
        throw: list[Figure] = []
        if weapon.throwable:
            in_reach = {e.uid for e in melee}
            throw = [e for e in self.enemies_of(figure)
                     if e.position is not None and e.uid not in in_reach]
        return AttackCandidates(melee, throw, hth)

    def aim(self, attacker: Figure, target: Figure) -> None:
        """Turn a ranged ``attacker`` to face ``target`` before it fires (#362, #117).

        Option (f) lets a missile attacker change facing, and missiles get no facing
        bonus, so aiming is free and satisfies the front-arc rule (p.16) that
        :meth:`queue_attack` enforces. This is the single home for the aim-to-face
        step — the Django view and the AI both call it, so a human and a computer
        archer turn to aim identically rather than only the human doing so. It faces
        the primary target only (a figure has one facing); a split shot's second
        target must independently fall in that arc.
        """
        if attacker.position is None or target.position is None:
            return
        line = self.arena.layout.line(attacker.position, target.position)
        if len(line) >= 2:
            direction = self.arena.layout.direction_to(attacker.position, line[1])
            if direction is not None:
                attacker.facing = direction

    def in_front_arc(self, attacker: Figure, point: Hex) -> bool:
        """Whether ``point`` lies in ``attacker``'s front arc, ignoring posture.

        A missile or thrown attack is legal only against a target in front of the
        attacker (p.15-16). Unlike :func:`zone_toward` (which treats a prone
        figure as having no front), this classifies the bearing purely against the
        attacker's facing — a prone crossbowman still aims along the way it points,
        so it may fire at a foe ahead of it (p.16).
        """
        if attacker.position is None or point == attacker.position:
            return False
        layout = self.arena.layout
        line = layout.line(attacker.position, point)
        direction = layout.direction_to(attacker.position, line[1])
        if direction is None:
            return False
        return zone_of_direction(attacker.facing, direction) == FRONT

    def queue_attack(self, attacker: Figure, target: Figure,
                     *, with_main_gauche: bool = False,
                     second_target: Figure | None = None) -> None:
        """Declare ``attacker``'s attack on ``target`` (resolved later).

        ``with_main_gauche`` also queues a separate off-hand main-gauche jab at
        the same foe, rolled at -4 DX (p.13) — legal only when the off-hand holds
        a ready main-gauche and the foe is within the dagger's reach.

        ``second_target`` aims a two-shot bow's second arrow at a different foe
        (p.5, p.10) — a bow "may fire at two different targets". Legal only for a
        true missile weapon that gets two shots this turn, and the second foe must
        also stand in the attacker's front arc.
        """
        option = attacker.current_option
        weapon = self._validate_attack(attacker, target, option)
        is_missile = weapon.kind == WeaponKind.MISSILE
        distance = self.arena.distance(attacker.position, target.position)
        # A throwable melee weapon aimed at a non-adjacent foe is hurled (p.15);
        # adjacent, it's a normal melee blow.
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
        """Shared guards for declaring any attack (Section VII), returning the
        attacker's ready weapon.

        The figure must have chosen an attack option, be able to act, be on the
        ground (a flyer lands to attack, p.21), not have attacked already this turn
        (one attack per turn — a multi-shot bow is one PendingAttack with
        ``shots>1``, not repeated calls), and hold a ready weapon whose kind matches
        the chosen option (and, for a missile, not be reloading)."""
        if option is None or not spec(option).is_attack:
            raise IllegalAction(
                f"{attacker.name} did not choose an attack option this turn"
            )
        if target.side == attacker.side:
            # No friendly fire: a figure can never target its own side, regardless
            # of how the attack was queued (client, AI, or a side mixup) — #229.
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
        # One action per turn (wizard-rules lines 262-263): a figure that cast
        # (or has a cast queued) may not also strike (#414).
        if attacker.cast_this_turn or any(
            pending.caster is attacker for pending in self._pending_casts
        ):
            raise IllegalAction(
                f"{attacker.name} is casting this turn and cannot also attack")
        # "A figure can never attack if it moved more than half its MA"
        # (wizard-rules lines 273-274): the movement already taken this turn must
        # fit the attack option's own cap (options.py movement_cap), so a full-MA
        # mover whose option was overwritten in the combat phase is caught (#413).
        budget = self.rules.movement_budget(
            attacker.movement_allowance, spec(option).movement_cap)
        if attacker.moved_this_turn > budget:
            raise IllegalAction(
                f"{attacker.name} moved {attacker.moved_this_turn} hex(es) this "
                f"turn — too far to attack with {option.value} "
                f"(at most {budget})")
        # Dodge/defend permits neither an attack nor a cast (wizard-rules lines
        # 1010-1011); the flags outlive an option overwrite, so check them too.
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
        """Queue a missile or thrown attack (p.15-16).

        The target must lie in the attacker's front arc (you fire where you face —
        posture-independent, so a prone crossbowman still shoots along its facing).
        ``zone`` is carried so a ready shield still stops frontal fire, and is the
        target's zone (as for melee) so a thrown weapon striking an exposed
        flank/rear earns the +2/+4 facing bonus — a thrown attack is "treated
        exactly like a regular attack" (p.15). Only true missile weapons "never get
        a bonus for the target's facing" (p.16), so the facing add is suppressed for
        missiles alone (``ignore_facing``)."""
        if not self.in_front_arc(attacker, target.position):
            raise IllegalAction(
                f"{target.name} is not in {attacker.name}'s front arc"
            )
        if is_throw:
            range_penalty = -distance     # -1 DX per hex of distance (p.15)
            shots = 1
        else:
            # Missile range is penalised by megahex (MH) distance, not raw hex
            # count (p.16): the map's 7-hex flowers are the yardstick.
            megahexes = megahex_distance(
                self.arena.layout, attacker.position, target.position)
            range_penalty = self.rules.missile_range_penalty(megahexes)
            shots = max_missile_shots(weapon, attacker.base_adj_dx)
            if option == Option.ONE_LAST_SHOT:
                shots = 1     # the parting shot looses a single arrow (p.7 option
                              # l); two-shot fire belongs to option f
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
        """Queue the off-hand main-gauche's separate -4 DX jab (p.13).

        A figure attacking with its main weapon may also stab the same foe with a
        ready main-gauche, rolled at -4 DX. Legal only when the off-hand actually
        holds a main-gauche and the foe is within the dagger's reach (adjacent).
        """
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
        # A wizard's -4 with any non-staff weapon (p.23) stacks with the jab's
        # own -4: the jab bypasses _situational_mods (its penalty is fixed), so
        # the wizard-weapon penalty is re-applied here.
        situational, situational_note = -4, "-4 main-gauche"
        if attacker.spells_known:
            situational -= 4
            situational_note += " -4 wizard weapon"
        self._pending.append(
            PendingAttack(attacker, target, zone=zone, ignore_facing=False,
                          range_penalty=0, situational=situational,
                          situational_note=situational_note, weapon=main_gauche)
        )

    def _order_dx(self, pending: PendingAttack) -> int:
        """The combat-ordering adjDX of a pending attack (Section VII, p.5/p.16).

        The full adjDX "counting everything BUT missile and thrown weapon range":
        the situational mods (prone-crossbow +1, over-body -2, sheltering -4,
        halfling +2 throw, pole +2 vs charge) shift the order, but the range
        penalty does not — a distant target makes you less accurate, not slower.
        ``pending.situational`` already excludes range (that lives in
        ``range_penalty``). Shared by attack ordering and the p.19 disengage DX
        gate (#147) so both read the same value.
        """
        return self.rules.order_dx(
            pending.attacker, zone=pending.zone,
            ignore_facing=pending.ignore_facing,
        ) + pending.situational

    @staticmethod
    def _shot_count(pending: PendingAttack) -> int:
        """How many shots/blows ``pending`` resolves this combat phase — its
        ``shots`` (a high-adjDX bow fires twice), but never fewer than one. The
        single definition of the ``max(1, shots)`` round count shared by the
        rounds loop in :meth:`resolve_combat`."""
        return max(1, pending.shots)

    def resolve_combat(self) -> list[AttackResult]:
        """Resolve all queued attacks, highest adjDX first (Section VII).

        Exact adjDX ties keep declaration order (a stable sort). The rulebook
        breaks ties with a die roll; in play the initiative winner simply
        declares first, so declaration order is the faithful stand-in and keeps
        the dice stream clean for deterministic resolution.
        """
        def ordering_key(pending: PendingAttack) -> tuple[int, int]:
            # Pole weapons used in/against a charge strike first, then by adjDX
            # (p.12) — so a polearm can drop a charger before it lands its blow.
            # This is independent of the +1 damage die: even a one-hex pole charge
            # (no extra die) resolves first.
            charge_first = 0 if pending.charge_resolve_first else 1
            return (charge_first, -self._order_dx(pending))

        results: list[AttackResult] = []
        ordered = sorted(self._pending, key=ordering_key)
        # Missile fire is sequenced in ROUNDS, not attacker-at-a-time (Section IV,
        # p.5): every figure looses its first shot in adjDX order, THEN the
        # high-adjDX bows that earn a second arrow loose it — again in adjDX order.
        # So two duelling archers fire A1, B1, A2, B2, not A1, A2, B1, B2 (#154).
        # Melee, thrown, HTH and main-gauche attacks are all single-shot and so
        # resolve entirely in the first round; only a two-shot bow reaches round 1.
        max_shots = max((self._shot_count(pending) for pending in ordered), default=1)
        for shot_index in range(max_shots):
            for pending in ordered:
                if shot_index < self._shot_count(pending):
                    self._resolve_attack_shot(pending, shot_index, results)
        # Casts resolve after the weapon rounds, in caster-adjDX order (highest
        # first) — the same ordering key attacks use. This pass draws ZERO dice
        # when no cast is queued, so a non-wizard game (and the gold-standard
        # combat example) keeps a byte-identical dice stream. See resolve_spell
        # for the per-cast draw order.
        for pending_cast in sorted(
            self._pending_casts,
            key=lambda cast: -self.rules.order_dx(
                cast.caster, zone=cast.zone, ignore_facing=True),
        ):
            self._resolve_cast(pending_cast)
        self._pending.clear()
        self._pending_casts.clear()
        self._drop_bows_after_last_shot()
        self._announce_victory()
        return results

    def _drop_bows_after_last_shot(self) -> None:
        """Enforce the parting-shot rule: a figure engaged in melee "can get off
        one shot if suddenly engaged, but must then drop the missile weapon"
        (ITL p.116 / Melee p.7 option l). After the one last shot resolves the
        bow leaves the hand and lands on the ground, so it cannot fire again on a
        later engaged turn (a bow has no reload cooldown to gate it). The figure
        is left weaponless in hand and must Change Weapons or Pick Up next turn;
        option_availability then greys ONE_LAST_SHOT out (no missile ready). This
        matches the rulebook Combat Example, where Wulf shoots once then readies
        his two-handed sword (p.23-24) (#241)."""
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
        """Resolve one shot/blow of ``pending`` (shot ``shot_index``).

        Every guard is re-checked per shot, because a bow's second arrow is loosed
        in a later round (p.5) — the attacker may have been cut down, or its target
        dropped, by an intervening first-round attack.
        """
        attacker = pending.attacker
        if not attacker.can_act():
            return          # killed/knocked out before its turn to strike
        if pending.shield_rush:
            # A shield-rush resolves "for all purposes" as an attack at its slot
            # (p.13, #151) — roll and knock-down here, in adjDX order.
            self._resolve_shield_rush(pending, results)
            return
        if not self._can_strike_now(attacker, shot_index):
            return
        # A flying weapon — hurled or fired — traces a line-of-flight: anyone in the
        # way may be hit, and a clean miss flies on (p.15-16). Thrown weapons are
        # single-shot; a high-adjDX bow looses two arrows, each tracing its own
        # flight and each able to aim at a different foe (p.5, p.10).
        # ``pending.weapon`` overrides the ready weapon for an off-hand main-gauche
        # jab; every other attack strikes with the ready weapon.
        weapon = pending.weapon or attacker.ready_weapon
        is_missile = weapon is not None and weapon.kind == WeaponKind.MISSILE
        flying = pending.thrown or is_missile
        # The effective target this shot lands on: a two-shot bow's second arrow
        # waits for its own round and may aim elsewhere (pending.second_target,
        # p.5/p.10); every other blow strikes the declared target.
        if flying and shot_index >= 1:
            target = pending.second_target or pending.target
        else:
            target = pending.target
        # THE single #310 chokepoint for every flight/melee path: a higher-adjDX
        # attacker this phase may already have felled this foe, and a corpse keeps
        # its hex so the reach check would still pass. Guarding the one effective
        # target here — where all three paths converge — means a newly added
        # flight/melee resolve path inherits the "don't strike a downed/dead
        # target" rule by construction, instead of re-copying the predicate.
        # (The shield-rush path guards separately: it is dispatched above, before
        # _can_strike_now, and folds the down-check into its reach test.)
        if target.out_of_play:
            return
        if flying:
            self._resolve_flight(pending, results, target=target)
        else:
            self._resolve_one_melee(pending, weapon, results)

    def _can_strike_now(self, attacker: Figure, shot_index: int) -> bool:
        """Whether ``attacker`` may still land this shot/blow — the prone /
        knocked-down / crossbow gate, re-checked every round.

        Prone figures can't fight — except a prone crossbowman who may fire, or a
        figure grappling on the ground in hand-to-hand. A prone crossbowman fires
        steadied (p.16) — but NOT if it was knocked prone by damage earlier this
        same phase: a figure knocked down "may not attack that turn" if it has not
        already (p.20). One already prone (chose to go prone/kneel last turn, or
        dropped prone to fire this turn via option f) still fires.

        Only a two-shot bow reaches a later round. If its bow was dropped or broken
        on a first-shot fumble (17/18) there is nothing left in hand to loose the
        second arrow (#154) — and with the weapon gone the melee branch would
        otherwise mis-resolve it as a phantom swing.
        """
        crossbow = (attacker.ready_weapon is not None
                    and attacker.ready_weapon.kind == WeaponKind.MISSILE
                    and attacker.ready_weapon.reload > 0
                    and not attacker.knocked_down_this_turn)
        if attacker.posture == Posture.PRONE and not crossbow and not attacker.in_hth:
            return False
        if shot_index >= 1 and (attacker.ready_weapon is None
                                or attacker.ready_weapon.kind != WeaponKind.MISSILE):
            return False
        return True

    def _resolve_one_melee(self, pending: PendingAttack, weapon, results: list) -> None:
        """Resolve a single melee / HTH / main-gauche blow — always one shot.

        Before rolling, a melee blow can fail to land outright (#147); HTH
        grapples (resolved on a shared hex, ``hth_damage`` set) are exempt. On a
        miss against a foe down in an HTH pile, a standing striker rolls on into
        the pile (Hitting Your Friends, p.17-18).
        """
        attacker = pending.attacker
        if pending.hth_damage is None and self._melee_whiffs(pending, weapon):
            self._whiff(attacker, pending.target, weapon, pending, results)
            return
        # Recompute the facing zone against the target's CURRENT posture and facing:
        # an earlier attacker this phase may have knocked the target prone (so it now
        # has no front, scoring the +4 rear adjustment) or turned it. The declared
        # zone would be stale. Missile/thrown attacks (ignore_facing) and HTH
        # grapples (forced to REAR, hth_damage set) keep their declared zone.
        zone = pending.zone
        if not pending.ignore_facing and pending.hth_damage is None:
            zone = attack_zone(self.arena.layout, attacker, pending.target)
        result = self._strike(
            attacker, pending.target, results, thrown=pending.thrown,
            zone=zone, weapon=weapon,
            dice_count=self.rules.attack_dice_count(pending.target, ranged=False),
            ranged=False,
            ignore_facing=pending.ignore_facing,
            range_penalty=pending.range_penalty,
            situational=pending.situational,
            situational_note=pending.situational_note,
            extra_dice=pending.damage_dice_bonus,
            hth_damage=pending.hth_damage,
            blunted=self.practice,
        )
        # Hitting Your Friends (p.17-18): a STANDING figure that misses a foe down
        # in an HTH pile rolls on — same DX adjustments — against the other piled
        # enemies, then friends, stopping at the first hit. A fumble (dropped/broken
        # weapon) ends the swing.
        if (not result.hit and not result.dropped_weapon
                and not result.broke_weapon
                and not attacker.in_hth and pending.target.in_hth
                and pending.hth_damage is None):
            self._cascade_into_pile(attacker, pending.target, weapon, pending, results)

    def _melee_whiffs(self, pending: PendingAttack, weapon) -> bool:
        """Whether a queued melee blow fails to connect before it is even rolled
        (#147).

        Two cases, both keyed off what has happened since the attack was declared:

        * The target **disengaged** this turn (option n, p.19). Only a foe whose
          combat-order adjDX is at least the fleer's own adjDX caught it "as it
          leaves"; a lower-DX foe gets no chance and whiffs. The reach is *not*
          re-checked here — the higher-DX strike lands at the moment of leaving,
          while the figure was still adjacent, even though it now stands a hex off.
        * The target is simply **out of reach** — now farther than the weapon's
          reach (a force-retreat or other relocation between declaration and
          resolution). Melee cannot reach across the gap.
        """
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
        """A melee blow that never lands — the foe slipped out of reach or fled
        before a slower attacker could catch it (#147).

        It consumes the attack and logs a clean miss, but rolls no dice (so a
        deterministic dice stream stays in step) and deals no damage.

        No to-hit number is computed: the blow never reached a roll, so inventing
        a ``rolled``/``needed`` pair would print a die check that never happened —
        and in a Tarmar (roll-over d20) game it would print a classic roll-under
        number in the wrong direction entirely (#270, the #229 log-truthfulness
        class). The ``"whiff"`` note tells :func:`narrate_attack` to say the blow
        found only air, with no numbers clause.
        """
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
        """The side that has won — the only one still standing, once at least two
        sides started (Combat to the Death). None while the fight is undecided.

        Single source of the win condition, shared by the engine's victory log
        and the board's API payload (#157)."""
        standing = {f.side for f in self.figures
                    if not f.out_of_play}
        if len(self.sides) >= 2 and len(standing) == 1:
            return next(iter(standing))
        return None

    def _announce_victory(self) -> None:
        """Log the win once a single side is left standing."""
        if getattr(self, "_victory_announced", False):
            return
        winner = self.victor()
        if winner is not None:
            self._victory_announced = True
            self.log.append(narrate_victory(winner))

    def _cascade_into_pile(
        self, attacker: Figure, intended: Figure, weapon,
        pending: PendingAttack, results: list,
    ) -> None:
        """Resolve the Hitting Your Friends miss-cascade (p.17-18).

        ``attacker`` is a standing figure that struck ``intended`` — a foe down
        in an HTH pile — and missed. It now rolls, one by one, at the SAME DX
        adjustments, against the other enemies in that pile and then its own
        friends in it, stopping the instant it hits someone. Figures grappling on
        the ground never hit their own friends; only the standing striker rolls.
        """
        pile = [member for member in self._hth_pile_at(intended.position)
                if member is not attacker and member is not intended
                and member.can_act()]
        enemies = [member for member in pile if member.side != attacker.side]
        friends = [member for member in pile if member.side == attacker.side]
        # The friends in this loop are the ONE case the rules let a blow harm its
        # own side; flag it so the recorded DamageEvent is not read as friendly
        # fire (#231). Restored in the finally so a raised error can't leave it set.
        self._same_side_hit_ok = True
        try:
            for victim in [*enemies, *friends]:
                zone = attack_zone(self.arena.layout, attacker, victim)
                result = self._strike(
                    attacker, victim, results, thrown=pending.thrown,
                    zone=zone, weapon=weapon,
                    dice_count=self.rules.attack_dice_count(victim, ranged=False),
                    ranged=False,
                    range_penalty=pending.range_penalty,
                    situational=pending.situational,
                    situational_note=pending.situational_note,
                    blunted=self.practice,
                )
                if result.hit:
                    self.log.append(narrate_cascade(attacker, intended, victim))
                    return
        finally:
            self._same_side_hit_ok = False

    def _apply(self, attacker: Figure, target: Figure, result: AttackResult) -> None:
        # Record every narrated attack so the log-truthfulness audit reaches the
        # select-phase free-hits too, not just resolve_combat's return (#311).
        self.applied_results.append(result)
        attacker.attacked_this_turn = True
        # A fired crossbow must reload before firing again; bows fire every turn
        # (their per-turn limit is the shot count, not a cooldown).
        if (result.weapon is not None and result.weapon.kind == WeaponKind.MISSILE
                and result.weapon.reload > 0):
            attacker.missile_cooldown = missile_reload_turns(
                result.weapon, attacker.base_adj_dx) + 1
        # A fumble's own story (dropped/shattered weapon) replaces the swing line.
        if result.dropped_weapon or result.broke_weapon:
            self.log.append(
                narrate_fumble(attacker, result.weapon, broke=result.broke_weapon)
            )
            if attacker.ready_weapon in attacker.weapons:
                attacker.weapons.remove(attacker.ready_weapon)
            if result.dropped_weapon:           # dropped lands intact; broken is gone
                # A fumbled melee weapon drops in the attacker's own hex; a thrown
                # weapon (a 17 in flight) drops in the TARGET hex instead (p.10).
                landing = target.position if result.thrown else attacker.position
                self._drop_to_ground(attacker.ready_weapon, landing)
            attacker.ready_weapon = None
        else:
            self.log.append(narrate_attack(attacker, target, result))
        # Attacker-side aftermath (Tarmar fumbles: off-balance spent/set, weapon
        # stress marked) — a ruleset hook so resolve_attack stays pure (#233).
        self.rules.apply_attack_side_effects(attacker, result)
        if not result.hit:
            return
        self.rules.apply_damage(target, result.damage, body_hit=result.body_hit)
        # Audit every damaging hit with both sides so a test can prove no figure
        # is harmed by its own side (#231). Zero-damage hits (armour turned it
        # aside) cost no ST, so they are not recorded as damage.
        if result.damage > 0:
            self.damage_events.append(DamageEvent(
                attacker_side=attacker.side, target_side=target.side,
                attacker_uid=attacker.uid, target_uid=target.uid,
                damage=result.damage,
                # A Tarmar crit (body_hit) drives the same hits into Body too;
                # record it so the invariants can see a crit-death (#340).
                body_damage=result.damage if result.body_hit else 0,
                same_side_allowed=self._same_side_hit_ok))
        # Force-retreat eligibility (p.20) counts only melee damage: "missile or
        # thrown weapon hits ... don't count." A missile/thrown hit deals ST damage
        # but must not arm a force retreat.
        if result.damage > 0 and not result.thrown and not (
                result.weapon is not None and result.weapon.kind == WeaponKind.MISSILE):
            attacker.dealt_st_damage_this_turn = True
            # Record WHICH enemy was struck so only that foe can be pushed (never a
            # teammate or an untouched enemy) and so each push is spent once. Only
            # an opposing-side hit grants a retreat ("inflicted hits on an enemy",
            # p.20); a same-side HTH miss-cascade hit never arms one.
            if (target.side != attacker.side
                    and target.uid not in attacker.force_retreat_targets_this_turn):
                attacker.force_retreat_targets_this_turn.append(target.uid)
        status = self.rules.status_after_hit(target)
        if status == DEAD:
            target.dead = True
        elif status == UNCONSCIOUS:
            target.unconscious = True
            # It FALLS unconscious (p.3): the figure is now a body on the map
            # (``_body_hexes`` already counts it), so its posture goes prone —
            # one source of truth, and the renderer draws it sprawled with no
            # facing wedge instead of an upright, faced token (#423). It is
            # never offered STAND UP: ``can_act`` is False while collapsed.
            target.posture = Posture.PRONE
        elif status == KNOCKDOWN:
            target.posture = Posture.PRONE
            # Knocked down by damage this turn: it "may not attack that turn" if
            # it has not already (p.20). This also revokes the prone-crossbow
            # firing exception for a crossbowman floored mid-phase.
            target.knocked_down_this_turn = True
        aftermath = narrate_status(target, status)
        if aftermath:
            self.log.append(aftermath)
        # Practice bout (p.22): a figure worn down to ST <= 3 drops out of the
        # friendly fight — out of play (``collapsed``) but alive (not ``is_dead``).
        if (self.practice and not target.is_dead and not target.dropped_out
                and target.current_st <= PRACTICE_DROPOUT_ST):
            target.dropped_out = True
            target.posture = Posture.PRONE
            self.log.append(narrate_dropout(target))
        if (target.is_dead or target.collapsed) and target.in_hth:
            # A downed grappler releases its hold; capture who was piled on it
            # before the links are cut so the freed survivors can be un-stacked
            # from the vacated hex (#287) -- see :meth:`_disperse_pile_survivors`.
            freed_survivors = [self._by_uid(uid) for uid in target.hth_opponents]
            self._clear_hth(target)
            self._disperse_pile_survivors(
                [survivor for survivor in freed_survivors if survivor is not None])


    # ---- the spell layer (TFT: Wizard; unification milestone 5) --------------
    # Ported verbatim from melee's ``_SpellcastingMixin``; the package engine
    # calls into it through the same documented hooks it always exposed
    # (``_resolve_cast`` / ``_expire_active_spells`` and the ``_pending_casts``
    # / ``spell_results`` containers), now implemented natively.

    def turn_cast_block_reason(self, caster: Figure) -> str | None:
        """Why what ``caster`` already did THIS TURN forbids a cast, or ``None``.

        The turn-flow counterpart of :func:`cast_block_reason` (which covers the
        figure's hands/nature), shared by :meth:`_validate_cast` (rejecting the
        declaration) and :meth:`spell_targets` (so the UI never offers a cast the
        queue would reject):

        * the CAST option moves at most ONE hex (options.py movement_cap;
          wizard-rules line 286 "Move one hex or stand still", line 312 "Shift
          one hex or stand still", melee #422), so a figure that spent more
          movement took a different option and cannot have it overwritten into a
          cast (wizard-rules lines 271-274) (melee #413);
        * dodge/defend permits "the casting of a spell or any sort of attack"
          to neither (wizard-rules lines 1010-1011) (melee #413);
        * one option per turn (wizard-rules lines 262-263): a figure that struck,
          or has a blow queued, may not also cast (melee #414).
        """
        cast_budget = self.rules.movement_budget(
            caster.movement_allowance, spec(Option.CAST).movement_cap)
        if caster.moved_this_turn > cast_budget:
            return f"moved {caster.moved_this_turn} hex(es) this turn"
        if caster.dodging or caster.defending:
            return "dodging/defending this turn"
        if caster.attacked_this_turn or any(
            pending.attacker is caster for pending in self._pending
        ):
            return "already attacked this turn"
        return None

    def spell_targets(self, caster: Figure, spell) -> list[Figure]:
        """Which figures ``caster`` may cast ``spell`` at (TFT: Wizard).

        The single authority for legal spell targets, the magic mirror of
        :meth:`attack_candidates`.

        * A **missile** spell (Magic Fist/Fireball/Lightning) reuses the exact
          melee-#362 ranged-target computation — every foe with a position, the
          front arc satisfied by turning to aim (:meth:`aim`), missiles taking
          no facing bonus (p.16).
        * A **self-cast** buff/protection (Stone Flesh, Iron Flesh, Blur, Speed
          Movement) is cast on the caster itself this batch (allies deferred).
        * A **thrown debuff** targets any foe with a position — "a thrown spell
          can be cast on the wizard's own hex, on any adjacent hex, or on any
          hex in front of the wizard" (wizard-rules lines 664-666), the front
          arc satisfied by the same free turn-to-aim missiles get — filtered by
          the spell's effect having something to act on (Drop Weapon needs a
          held weapon/shield, Break Weapon a held weapon, Trip a figure on its
          feet) and by the caster affording the target's (heavy-aware) cost.
        """
        if not caster.can_act() or caster.position is None:
            return []
        # What the figure already did this turn can rule a cast out entirely
        # (melee #413/#414) — offer no targets the queue would reject.
        if self.turn_cast_block_reason(caster) is not None:
            return []
        if spell.is_missile:
            return [enemy for enemy in self.enemies_of(caster)
                    if enemy.position is not None]
        if spell.targets_self or spell.is_protection:
            return [caster]
        if spell.type == THROWN:
            return [enemy for enemy in self.enemies_of(caster)
                    if enemy.position is not None
                    and self._thrown_spell_applies(spell, enemy)
                    and spell_cost_for(spell, enemy.strength) <= caster.current_st]
        return []

    def _thrown_spell_applies(self, spell, target: Figure) -> bool:
        """Whether ``spell``'s effect has anything to act on for ``target``.

        Filters the offered target list so the UI/AI never queue a cast that
        could only fizzle into nothing: Drop Weapon needs something held in a
        hand ("a weapon, shield, or whatever", spell-ref lines 11-12), Break
        Weapon a weapon in hand ("in target's hand", line 153-154), Trip a
        victim not already down. A lasting debuff (Clumsiness/Slow/Stop)
        applies to anyone.
        """
        if spell.drops_weapon:
            return (target.ready_weapon is not None
                    or (target.shield_ready and target.shield.name != "None"))
        if spell.breaks_weapon:
            return target.ready_weapon is not None
        if spell.knocks_down:
            return target.posture != Posture.PRONE
        return True

    def queue_spell(self, caster: Figure, spell, target: Figure,
                    st_used: int) -> None:
        """Declare ``caster``'s cast of ``spell`` at ``target`` (resolved later).

        Guards mirror :meth:`_validate_attack`: the caster must have chosen CAST,
        be able to act with its hands free (no shield / non-staff weapon, p.23),
        know the spell, afford the ST (a cast may bring ST to 0 but not below), not
        have already cast this turn, and ``target`` must be legal for the spell's
        type. A missile cast turns to face its target (:meth:`aim`) and takes the
        megahex range penalty; a thrown cast at another figure also aims and takes
        the thrown-spell range penalty of -1 DX per hex ("subtract 1 from DX for
        every hex from the wizard to his target", wizard-rules lines 668-670); a
        self-cast has neither ("A wizard casting a thrown spell on himself...
        has no DX penalty for distance", lines 670-671). A blurred target drags
        the cast a further -4 ("Target is Blurred", spell-ref lines 322-323 —
        the table applies "for either casting of spells or physical attacks").
        """
        self._validate_cast(caster, spell, target, st_used)
        zone, range_penalty, situational, situational_note = None, 0, 0, ""
        if target is not caster:
            self.aim(caster, target)               # free turn-to-face (p.16)
            zone = attack_zone(self.arena.layout, caster, target)
            if spell.is_missile:
                megahexes = megahex_distance(
                    self.arena.layout, caster.position, target.position)
                range_penalty = self.rules.missile_range_penalty(megahexes)
            else:
                # Thrown spell: -1 DX per hex of distance (rules lines 668-670).
                range_penalty = -self.arena.distance(
                    caster.position, target.position)
            blur = target.spell_defense_dx_penalty()
            if blur:
                situational += blur
                situational_note = f"{blur:+d} blurred target"
        self._pending_casts.append(PendingCast(
            caster=caster, spell=spell, target=target, st_used=st_used,
            zone=zone, range_penalty=range_penalty,
            situational=situational, situational_note=situational_note))

    def _validate_cast(self, caster: Figure, spell, target: Figure,
                       st_used: int) -> None:
        """Shared guards for declaring a cast; raises ``IllegalAction`` if illegal."""
        if caster.current_option != Option.CAST:
            raise IllegalAction(f"{caster.name} did not choose to cast this turn")
        if not caster.can_act():
            raise IllegalAction(f"{caster.name} cannot cast")
        turn_block = self.turn_cast_block_reason(caster)
        if turn_block is not None:
            raise IllegalAction(f"{caster.name} cannot cast: {turn_block}")
        block = cast_block_reason(caster)
        if block is not None:
            raise IllegalAction(f"{caster.name} cannot cast: {block}")
        if spell.id not in caster.spells_known:
            raise IllegalAction(f"{caster.name} does not know {spell.name}")
        if caster.cast_this_turn or any(
            pending.caster is caster for pending in self._pending_casts
        ):
            raise IllegalAction(f"{caster.name} has already cast this turn")
        # ST bounds: a variable-ST spell may invest floor..ceiling — a missile
        # spell 1..3 (rules line 620), Clumsiness 1..the caster's own pool (the
        # reference caps it nowhere). Any other spell costs its flat st_cost
        # exactly, heavy-target variant applied (Drop Weapon is 2 ST against
        # basic ST 20+, Trip 4 ST against 30+ — spell-ref lines 12-13, 90-91).
        # A cast may reduce ST to exactly 0 but never below (p.3-4) — casting
        # below 0 ST is rejected here.
        floor = spell_cost_for(spell, target.strength)
        if spell.variable_st:
            ceiling = spell.max_st if spell.max_st else caster.current_st
            floor = min(floor, ceiling)
        else:
            ceiling = floor
        if not (floor <= st_used <= ceiling):
            raise IllegalAction(
                f"{spell.name} takes {floor}..{ceiling} ST (got {st_used})")
        if st_used > caster.current_st:
            raise IllegalAction(
                f"{caster.name} lacks the ST to cast {spell.name} "
                f"(needs {st_used}, has {caster.current_st})")
        if target not in self.spell_targets(caster, spell):
            raise IllegalAction(
                f"{target.name} is not a legal target for {spell.name}")

    def _resolve_cast(self, pending: PendingCast) -> None:
        """Resolve one queued cast (TFT: Wizard) — the magic mirror of :meth:`_apply`.

        Rolls the cast (:meth:`.ruleset.Ruleset.resolve_spell`), drains its ST
        (:meth:`.ruleset.Ruleset.apply_spell_cost`), then lands its effect: a
        missile spell's damage on the target (``apply_damage`` + status), a
        protection spell's hit-stopping on the target
        (``apply_spell_protection``). An 18 fizzle knocks the CASTER down (p.11);
        casting to exactly 0 ST leaves the caster unconscious. The narration and
        :class:`.combat.SpellResult` are recorded so the log-truthfulness audit
        reaches casts.
        """
        caster = pending.caster
        spell = pending.spell
        target = pending.target
        if not caster.can_act():
            return                              # felled before its turn to cast
        # A figure knocked down before its turn to act "does not get to act that
        # turn" (rules lines 250-251) — the cast is lost, exactly as
        # _can_strike_now cancels a knocked-down attacker's blow (melee #416). No
        # ST is drained (the spell was never begun); the wizard's action is still
        # spent.
        if caster.posture == Posture.PRONE:
            caster.cast_this_turn = True
            caster.attacked_this_turn = True
            self.log.append(narrate_cast_lost(caster, spell, "knocked_down"))
            return
        # Re-check ST at resolution (melee #415): an enemy blow this phase may
        # have cut the caster below what the cast was declared with. "A wizard
        # cannot cast a spell which would reduce his ST below 0" (rules lines
        # 167-169), so an unaffordable cast fizzles harmlessly — no dice, no ST
        # drained, never a self-kill. (Equal is legal: a cast to exactly 0 ST
        # stands.)
        if pending.st_used > caster.current_st:
            caster.cast_this_turn = True
            caster.attacked_this_turn = True
            self.log.append(narrate_cast_lost(caster, spell, "too_weak"))
            return
        # A spell aimed at another figure needs a live target: a foe already
        # felled this phase is not struck (the melee-#310 rule) — true for a
        # missile spell and a thrown debuff alike. A self-cast always has its
        # caster.
        if target is not caster and target.out_of_play:
            return
        if spell.is_missile:
            # A missile spell traces a line-of-flight like a hurled/fired weapon
            # (melee #417): blockers rolled to miss, the aimed target, then a
            # miss flying on. Each event narrates and records as it happens; the
            # FINAL SpellResult carries the cast's overall ST charge.
            result = self._resolve_spell_flight(pending)
        else:
            result = self.rules.resolve_spell(
                self.dice, caster, spell, target=target, st_used=pending.st_used,
                range_penalty=pending.range_penalty,
                situational=pending.situational)
            self.log.append(narrate_spell(caster, target, result))
            self.spell_results.append(result)
            if result.hit:
                self._apply_thrown_spell_effect(pending, result)
        self.rules.apply_spell_cost(
            caster, spell, result.st_spent, fizzled=result.fizzled)
        caster.cast_this_turn = True
        caster.attacked_this_turn = True        # a cast is the figure's action
        # An 18 fizzle: the shock knocks the caster down (p.11).
        if result.knockdown:
            caster.posture = Posture.PRONE
            caster.knocked_down_this_turn = True
        # Paying the ST cost may have dropped the caster to 0 (unconscious) — a
        # legal cast that spends its last ST (p.3-4). It can never go below 0
        # (queue_spell rejects that), so it is never a self-kill.
        self._apply_cast_status(caster, self.rules.status_after_hit(caster))

    def _resolve_spell_flight(self, pending: PendingCast) -> SpellResult:
        """A missile spell's line-of-flight (Wizard p.12, rules lines 624-652) —
        the spell mirror of :meth:`_resolve_flight` (melee #417).

        Three sequential phases, each able to end the flight, exactly as for a
        flying weapon:

        1. **Blockers** — every ENEMY figure standing in the lane between caster
           and target is "rolled to miss" (adjDX re-figured for the range to that
           figure): success slips the spell past; the 14/15-16/17-18 special
           table strikes it anyway (auto/double/triple); any other failure
           "just fizzles in that hex" (lines 650-652). A FRIENDLY figure in the
           lane is never rolled and never struck — the same friendly-fire guard
           the weapon flight applies (melee #229), the stand-in for the
           rulebook's always-taken option of rolling to miss a friend.
        2. **The aimed target** — the normal cast to-hit (four dice against a
           dodging target, melee #418). A hit lands; a 17/18 fizzle dies in the
           caster's hands (nothing flies anywhere); a plain miss flies on.
        3. **Fly-on** — the missed spell continues along the caster→target line,
           making a fresh to-hit roll (range re-figured) against each enemy
           whose hex it enters, until it hits, leaves the field, or has
           travelled a number of MEGAHEXES equal to the caster's basic ST
           (lines 628-630).

        Returns the FINAL SpellResult of the flight — the one whose ``st_spent``
        (full invested ST on any hit or fizzle, 1 on a clean total miss) and
        ``fizzled``/``knockdown`` flags settle the caster's books. Every event
        result is narrated and appended to ``spell_results`` as it happens.
        """
        caster, spell, target = pending.caster, pending.spell, pending.target
        layout = self.arena.layout
        held = self.occupied(exclude=caster)
        # Phase 1: figures standing in the lane, caster -> target.
        for hex_position in layout.line(caster.position, target.position)[1:-1]:
            blocker = held.get(hex_position)
            if blocker is None or blocker is target:
                continue
            if blocker.side == caster.side:
                continue    # a friend is never harmed by its side (melee #229)
            outcome, result = self._spell_roll_to_miss(pending, blocker)
            if outcome != SPELL_MISSED_PAST:
                return result       # struck it, or fizzled in its hex
        # Phase 2: the aimed target (resolve_spell rolls four dice vs a dodging
        # target, melee #418, and draws the damage dice on a hit).
        aimed = self.rules.resolve_spell(
            self.dice, caster, spell, target=target, st_used=pending.st_used,
            range_penalty=pending.range_penalty, situational=pending.situational)
        self.log.append(narrate_spell(caster, target, aimed))
        self.spell_results.append(aimed)
        if aimed.hit:
            self._apply_missile_spell_hit(pending, target, aimed)
            return aimed
        if aimed.fizzled:
            return aimed            # a 17/18 dies uncast — nothing flies on
        # Phase 3: a clean miss flies on.
        flown = self._spell_fly_on(pending, held)
        return flown if flown is not None else aimed

    def _spell_roll_to_miss(
        self, pending: PendingCast, blocker: Figure
    ) -> tuple[str, SpellResult | None]:
        """Roll the missile spell past ``blocker`` standing in its lane (melee #417).

        The caster rolls its adjDX or less, range re-figured for the blocker's
        distance (rules lines 643-646); :func:`.combat.classify_spell_roll_to_miss`
        applies the special table. Returns the outcome and, when the flight ends
        here, the recorded SpellResult (``None`` when the spell slipped past).
        """
        caster, spell = pending.caster, pending.spell
        range_penalty = self.rules.missile_range_penalty(megahex_distance(
            self.arena.layout, caster.position, blocker.position))
        needed = self.rules.spell_to_hit_number(caster, range_penalty=range_penalty)
        rolled = self.dice.total(THREE_DICE)
        outcome, multiplier = classify_spell_roll_to_miss(rolled, needed)
        if outcome == SPELL_MISSED_PAST:
            return outcome, None
        if outcome == SPELL_LANE_HIT:
            return outcome, self._spell_strike(
                pending, blocker, multiplier, rolled=rolled, needed=needed,
                range_penalty=range_penalty, note="struck_in_lane")
        # A failed roll-to-miss an enemy is NOT a hit — "a missed 'roll to miss'
        # an enemy just fizzles in that hex" (lines 650-652). Not a 17/18 casting
        # fizzle either: the plain-miss ST charge (1) applies, and the caster
        # suffers no knockdown.
        result = SpellResult(
            hit=False, rolled=rolled, needed=needed, dice_count=THREE_DICE,
            multiplier=0, st_spent=1, damage=0, spell_id=spell.id,
            target_uid=blocker.uid, caster_uid=caster.uid, note="fizzled_in_lane",
            to_hit_breakdown=self.rules.spell_to_hit_breakdown(
                caster, range_penalty=range_penalty))
        self.log.append(narrate_spell(caster, blocker, result))
        self.spell_results.append(result)
        return outcome, result

    def _spell_fly_on(
        self, pending: PendingCast, held: dict
    ) -> SpellResult | None:
        """Phase 3 (rules lines 624-631): the missed spell "continues along the
        straight line drawn between the center of the wizard's hex and the
        center of the target hex", making a fresh to-hit roll (DX re-figured for
        the new range) against each enemy whose hex it enters, until it (a) hits
        a figure, (b) misses everyone and leaves the field, or (c) has travelled
        a number of megahexes equal to the caster's basic ST. Friends along the
        way are never rolled or struck (the melee-#229 guard, as in phase 1). A
        16-18 total here is a plain miss — the cast's own fizzle band applied
        only to the aimed roll; mid-flight there is no spell left to fizzle.
        """
        caster, target = pending.caster, pending.target
        layout = self.arena.layout
        # The rulebook's "continues along the straight line" is the exact
        # cube-space ray past the target — :meth:`Arena.ray_past`, the one
        # line-of-flight geometry shared with the weapon fly-on (melee #417,
        # #429).
        for current in self.arena.ray_past(caster.position, target.position):
            if not self.arena.contains(current):
                return None                     # out over the edge of the field
            megahexes = megahex_distance(layout, caster.position, current)
            if megahexes > caster.strength:
                return None                     # spent: the basic-ST megahex cap
            figure = held.get(current)
            if figure is None or figure.side == caster.side:
                continue
            range_penalty = self.rules.missile_range_penalty(megahexes)
            needed = self.rules.spell_to_hit_number(
                caster, range_penalty=range_penalty)
            rolled = self.dice.total(THREE_DICE)
            hit, multiplier, _fizzle, _knockdown = classify_spell_roll(
                rolled, needed)
            if hit:
                return self._spell_strike(
                    pending, figure, multiplier, rolled=rolled, needed=needed,
                    range_penalty=range_penalty, note="flew_on")
            # missed this one too — the spell keeps flying
        return None                             # ran out of extended ray

    def _spell_strike(
        self, pending: PendingCast, victim: Figure, multiplier: int, *,
        rolled: int, needed: int, range_penalty: int, note: str
    ) -> SpellResult:
        """A missile spell that connected mid-flight (lane or fly-on): roll its
        damage against ``victim``, record, narrate, and apply — the spell mirror
        of :meth:`_flight_strike`. A mid-flight hit is a real hit, so the full
        invested ST is charged (``st_spent``)."""
        caster, spell = pending.caster, pending.spell
        raw_damage = roll_missile_spell_damage(
            self.dice, spell, pending.st_used, multiplier)
        damage = max(0, raw_damage - self.rules.absorbed(victim, zone=None))
        result = SpellResult(
            hit=True, rolled=rolled, needed=needed, dice_count=THREE_DICE,
            multiplier=multiplier, st_spent=pending.st_used, damage=damage,
            raw_damage=raw_damage, spell_id=spell.id, target_uid=victim.uid,
            caster_uid=caster.uid, note=note,
            to_hit_breakdown=self.rules.spell_to_hit_breakdown(
                caster, range_penalty=range_penalty))
        self.log.append(narrate_spell(caster, victim, result))
        self.spell_results.append(result)
        self._apply_missile_spell_hit(pending, victim, result)
        return result

    def _apply_missile_spell_hit(
        self, pending: PendingCast, victim: Figure, result: SpellResult
    ) -> None:
        """Land a missile spell's hit on ``victim``: damage, the audited
        DamageEvent, post-hit status, and Magic Fist's trip save (melee #421).
        The one apply path for the aimed target, a lane blocker, and a fly-on
        victim — every one an enemy (the friendly-fire guard upstream), so the
        event is never same-side."""
        caster = pending.caster
        self.rules.apply_damage(victim, result.damage)
        if result.damage > 0:
            self.damage_events.append(DamageEvent(
                attacker_side=caster.side, target_side=victim.side,
                attacker_uid=caster.uid, target_uid=victim.uid,
                damage=result.damage))
        self._apply_cast_status(victim, self.rules.status_after_hit(victim))
        self._magic_fist_trip(pending.spell, victim, result)

    def _magic_fist_trip(self, spell, victim: Figure, result: SpellResult) -> None:
        """Magic Fist's trip effect (spell-ref lines 18-21, melee #421): "A Magic
        Fist that does 6 or more hits before armor/shield protection will also
        trip its target, making him/her fall down, unless he/she makes a 3-die
        roll on ST or DX, whichever is higher."

        The threshold is the spell's ``trips_at`` read against PRE-armour
        ``raw_damage``. The save is 3d6 at or under the higher of the victim's
        CURRENT ST and its adjDX (armour + wounds — the Trip spell's own chasm
        save is "against adjDX", spell-ref lines 88-90, so DX saves use the
        adjusted value), rolled after the blow's damage lands. No dice are drawn
        for a victim the blow already felled or floored — there is nothing left
        to trip, and the stream stays byte-identical for every pre-#421 script
        that ends prone."""
        if spell.trips_at <= 0 or result.raw_damage < spell.trips_at:
            return
        if not victim.can_act() or victim.posture == Posture.PRONE:
            return
        # adjDX for the save carries every cumulative adjustment ("All applicable
        # DX adjustments are cumulative", spell-ref line 294) — armour, wounds,
        # and an active Clumsiness (melee #431).
        needed = max(
            victim.current_st,
            victim.base_adj_dx + self.rules.wound_penalty(victim)
            + victim.spell_dx_penalty())
        rolled = self.dice.total(THREE_DICE)
        if rolled <= needed:
            self.log.append(narrate_trip(
                victim, fell=False, rolled=rolled, needed=needed))
            return
        victim.posture = Posture.PRONE
        victim.knocked_down_this_turn = True
        self.log.append(narrate_trip(
            victim, fell=True, rolled=rolled, needed=needed))

    def _apply_thrown_spell_effect(self, pending: PendingCast,
                                   result: SpellResult) -> None:
        """Land a HIT thrown spell's effect on its target (melee #431).

        The one dispatch for every thrown-spell payload, mirror of
        :meth:`_apply_missile_spell_hit`:

        * a **protection** spell folds its stops in via
          :meth:`.ruleset.Ruleset.apply_spell_protection` (melee #419
          refresh-not-stack);
        * a **lasting** buff/debuff (Blur, Clumsiness, Slow/Speed Movement,
          Stop) is recorded in the target's ``active_spells``
          (:meth:`.ruleset.Ruleset.record_active_spell`) and narrated with its
          real magnitude and duration;
        * an **instant** effect (Drop Weapon, Break Weapon, Trip) acts through
          the same machinery its non-magical twin uses (the 17-fumble drop, the
          18-fumble break, the knockdown posture change).
        """
        spell, target = pending.spell, pending.target
        if spell.is_protection:
            self.rules.apply_spell_protection(target, result)
            return
        if spell.has_lasting_effect:
            self.rules.record_active_spell(target, spell, result)
            record = target.active_spells[spell.id]
            self.log.append(narrate_spell_applied(target, spell, record))
            return
        if spell.drops_weapon:
            self._apply_spell_drop(target)
        elif spell.breaks_weapon:
            self._apply_spell_break(target)
        elif spell.knocks_down:
            self._apply_spell_knockdown(target)

    def _apply_spell_drop(self, target: Figure) -> None:
        """Drop Weapon's effect: "Makes victim drop whatever is in one hand – a
        weapon, shield, or whatever" (spell-ref lines 11-12).

        The ready weapon falls to the ground in the victim's own hex, exactly
        as a 17-fumble drop lands it (:meth:`_apply`) — recoverable with PICK
        UP, and a wizard's dropped staff keeps its owner-only guard. With no
        weapon in hand, a ready shield is shed instead (the engine's one
        shield-shedding model, as :meth:`_grapple_bare` sheds it): off the arm
        and out of play. With neither, the spell clutches at nothing — narrated
        truthfully.
        """
        weapon = target.ready_weapon
        if weapon is not None:
            if weapon in target.weapons:
                target.weapons.remove(weapon)
            self._drop_to_ground(weapon, target.position)
            target.ready_weapon = None
            self.log.append(narrate_spell_disarm(target, weapon.name, broke=False))
        elif target.shield_ready and target.shield.name != "None":
            shield_name = target.shield.name
            target.shield_ready = False
            target.shield = NO_SHIELD
            self.log.append(narrate_spell_disarm(target, shield_name, broke=False))
        else:
            self.log.append(narrate_spell_disarm(target, None, broke=False))

    def _apply_spell_break(self, target: Figure) -> None:
        """Break Weapon's effect: "Shatters one weapon, shield, staff, etc., in
        target's hand" (spell-ref lines 153-155).

        Maps onto the engine's ONE broken-weapon model — the 18-fumble
        (:meth:`_apply`: "dropped lands intact; broken is gone"): the ready
        weapon is destroyed outright, leaving nothing to recover. (The
        reference's half-damage broken state is not modelled for the fumble
        either, so both break paths stay consistent; a broken staff being
        "useless" matches removal exactly.)
        """
        weapon = target.ready_weapon
        if weapon is not None:
            if weapon in target.weapons:
                target.weapons.remove(weapon)
            target.ready_weapon = None
            self.log.append(narrate_spell_disarm(target, weapon.name, broke=True))
        else:
            self.log.append(narrate_spell_disarm(target, None, broke=True))

    def _apply_spell_knockdown(self, target: Figure) -> None:
        """Trip's effect: "Knocks victim down. Does no damage" (spell-ref lines
        88-91). No saving roll — the reference's 4-die adjDX save applies only
        at a chasm/pit/river edge, and this arena has none. A victim already
        down (or felled this phase) has nothing left to knock over."""
        if not target.can_act() or target.posture == Posture.PRONE:
            self.log.append(narrate_spell_trip(target, already_down=True))
            return
        target.posture = Posture.PRONE
        target.knocked_down_this_turn = True
        self.log.append(narrate_spell_trip(target, already_down=False))

    def _expire_active_spells(self) -> None:
        """End-of-turn spell expiry (melee #431) — the duration rules made real.

        * A **stated-duration** spell ticks down one turn, the cast turn counted
          as the first ("Some spells are not renewable, but last a stated number
          of turns after casting. The turn such a spell is cast is always
          counted as the first turn." — wizard-rules lines 231-232) and is
          removed when its count runs out.
        * A **continuing** spell (Stone/Iron Flesh, Blur) ends when its caster
          can no longer renew it — dead or unconscious ("All spells that are
          renewed last until the turn ends (or the wizard dies or goes
          unconscious)... Only the caster can re-energize them.", rules lines
          229-231, 803). The Renew stage itself (and its per-turn ST charge)
          stays deferred: a conscious caster's continuing spells are treated as
          renewed at no cost.

        Every removal re-syncs ``spell_protection`` so the stops pool stays
        equal to the active protection set (the melee #431 invariant).
        """
        by_uid = {figure.uid: figure for figure in self.figures}
        for figure in self.figures:
            if not figure.active_spells:
                continue
            expired: list[str] = []
            for spell_id, record in figure.active_spells.items():
                remaining = record.get("remaining")
                if remaining is None:
                    caster = by_uid.get(record.get("caster", ""))
                    if caster is None or not caster.can_act():
                        expired.append(spell_id)
                else:
                    record["remaining"] = remaining - 1
                    if record["remaining"] <= 0:
                        expired.append(spell_id)
            for spell_id in expired:
                figure.active_spells.pop(spell_id, None)
                spell = SPELLS.get(spell_id)
                if spell is not None and not figure.out_of_play:
                    self.log.append(narrate_spell_expired(figure, spell))
            if expired:
                self.rules.sync_spell_protection(figure)

    def _apply_cast_status(self, figure: Figure, status: str | None) -> None:
        """Apply a post-cast status (DEAD/UNCONSCIOUS/KNOCKDOWN) and narrate it."""
        if status == DEAD:
            figure.dead = True
        elif status == UNCONSCIOUS:
            figure.unconscious = True
            # It falls unconscious — a body on the map goes prone (melee #423),
            # same as the hit path in :meth:`_apply`.
            figure.posture = Posture.PRONE
        elif status == KNOCKDOWN:
            figure.posture = Posture.PRONE
            figure.knocked_down_this_turn = True
        aftermath = narrate_status(figure, status)
        if aftermath:
            self.log.append(aftermath)


class _ForceRetreatMixin:
    # ---- force retreat (Section: Forcing Retreat) ----
    def can_force_retreat(self, attacker: Figure, target: Figure) -> bool:
        """Whether ``attacker`` may still shove ``target`` back one hex this turn.

        The authoritative rule (p.20, "Forcing Retreat"): a figure that put hits
        on an *enemy* with a physical, non-missile attack and took no hits itself
        may force that enemy to retreat one hex at the end of the turn. This gate
        is the single source of truth -- both the option menu and the execution
        path go through it, so the two can never desync (#229A).

        Membership in ``attacker.force_retreat_targets_this_turn`` already encodes
        four of the conditions at once: the attacker dealt qualifying melee damage,
        it landed on THIS specific figure, that figure is on the opposing side, and
        the push has not yet been spent (:meth:`force_retreat` removes the uid once
        used, so no single hit yields an unbounded chain of pushes). The remaining
        conditions are checked explicitly:

        * the attacker took no hits this turn (from any source);
        * the target is adjacent (a push is a one-hex shove);
        * the target is neither dead nor collapsed (a fallen body is not pushed --
          the menu never offers it, so execution must not accept it either); and
        * the target is not locked in hand-to-hand -- the rules give no way to
          force-retreat a grappler out of a pile (an HTH figure moves only by the
          disengagement rules), and shoving it to a neighbouring hex would leave a
          cross-hex grapple no invariant could reconcile.
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
        self, attacker: Figure, target: Figure, *, advance: bool = False,
    ) -> Hex:
        """Push ``target`` one hex farther from ``attacker``; optionally follow."""
        if not self.can_force_retreat(attacker, target):
            raise IllegalAction("force retreat not allowed")
        occupied = set(self.occupied(exclude=target))
        layout = self.arena.layout
        start_distance = layout.distance(attacker.position, target.position)

        def footprint_fits(anchor: Hex) -> bool:
            # A man-sized target validates its single hex; a multi-hex target
            # (a giant) must land its WHOLE footprint in-bounds and unoccupied,
            # so a shove can never overlap another figure or slide part of the
            # giant off the arena (#311).
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
        # Tie-break deterministically rather than leaning on neighbour-iteration
        # order: push the target into the hex *furthest* from the attacker (it
        # gives the most ground), and settle any remaining ties on the hex's own
        # (col, row) so the choice never depends on dict/set ordering.
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
        # Spend the push: this foe has been forced back its one hex for the turn
        # (p.20). Removing the uid makes can_force_retreat False for this pair, so
        # advancing into the vacated hex cannot re-arm an endless chain of shoves.
        if target.uid in attacker.force_retreat_targets_this_turn:
            attacker.force_retreat_targets_this_turn.remove(target.uid)
        self.log.append(narrate_retreat(attacker, target, advance))
        return target.position


class GameState(
    _RosterMixin,
    _TurnMixin,
    _MovementMixin,
    _HthMixin,
    _ShieldRushMixin,
    _CombatMixin,
    _ForceRetreatMixin,
):
    """The single source of truth for a fight, composed from the responsibility
    mixins above (#156).

    ``GameState`` itself owns only the shared state every mixin reads through
    ``self`` -- the arena, the figures, the dice, the queued attacks, the turn
    counter, the ruleset, and the log. Each mixin is stateless behaviour grouped
    by responsibility (rosters, turn sequencing, movement, hand-to-hand,
    shield-rush, combat resolution, force retreat); they call one another through
    ``self``, with the MRO resolving every cross-mixin call.
    """
    def __init__(
        self,
        arena: Arena,
        figures: list[Figure],
        *,
        dice: Dice | None = None,
        ruleset: Ruleset | None = None,
        combat_type: CombatType = CombatType.DEATH,
    ):
        self.arena = arena
        self.figures = figures
        self.dice = dice or Dice()
        # The swappable mechanics. Default: classic Melee. Pass a Ruleset
        # subclass to swap in different combat/injury/movement mechanics.
        self.rules = ruleset or Ruleset()
        # The combat variant (Section IX, p.22). Practice combat is the only one
        # that changes the fight itself — blunted half-damage weapons, no missiles,
        # and a drop-out at ST <= 3 (see ``practice``). Death/Arena differ only in
        # the XP awarded at the end (engine.experience).
        self.combat_type = combat_type
        self.turn_number = 1
        self.log: list[str] = []
        self._pending: list[PendingAttack] = []
        # Queued spell casts, resolved in resolve_combat alongside attacks (DX
        # ordered). Empty in any non-wizard game, so the attack dice stream and
        # the gold-standard combat example stay byte-identical.
        self._pending_casts: list[PendingCast] = []
        # Every SpellResult a combat phase resolved (parallel to applied_results),
        # for the log-truthfulness audit. Cleared each end_turn.
        self.spell_results: list = []      # the spell layer's SpellResults, if any
        # Damage-attribution audit trail (#231). Every damaging hit appends a
        # DamageEvent here so a test can prove no figure was ever harmed by its
        # own side. Purely observational — reading/writing it changes no rules.
        self.damage_events: list[DamageEvent] = []
        # Every AttackResult that _apply narrates into the live log this turn --
        # combat-phase blows AND the select-phase HTH free-hits/cascades that
        # never reach resolve_combat's returned list. assert_log_truthful can
        # audit this superset so no narrated attack escapes the truthfulness
        # check (#311). Cleared each end_turn; purely observational.
        self.applied_results: list[AttackResult] = []
        # Set True only while the p.17-18 HTH miss-cascade resolves, the one path
        # on which a figure may legitimately strike its own side; the recorded
        # DamageEvent carries this so the invariant checker exempts that case.
        self._same_side_hit_ok: bool = False
        # Per-character initiative selection (#192). Left empty until a caller
        # opens a selection with begin_selection(); while empty the move() turn
        # guard is inert, so pure movement-mechanics tests drive move() freely.
        self.initiative_order: list[str] = []
        self.active_index: int = 0
        self.passed: list[str] = []
        # Weapons lying on the ground (dropped, fumbled, or thrown), pick-up-able.
        self.dropped: list[tuple] = []        # (Hex, Weapon)
        for index, figure in enumerate(figures):
            if not figure.uid:
                figure.uid = f"f{index}"

    @property
    def practice(self) -> bool:
        """Whether this is a practice bout (p.22): weapons blunted to half damage,
        no missiles, and figures drop out at ST <= 3. The single mode flag the
        combat rules read."""
        return self.combat_type is CombatType.PRACTICE

    # ---- snapshot contract --------------------------------------------------
    # The same ``to_dict`` / ``from_dict`` pair ``tarmar_engine.state.BattleState``
    # offers, so a consumer that serves battles out of a database can hold either
    # profile's state. The implementation lives in ``.persistence``, imported
    # inside the methods because that module imports this one.
    def to_dict(self) -> dict:
        """This state as a JSON-safe ``dict`` (see :mod:`.persistence`)."""
        from .persistence import state_to_json

        return state_to_json(self)

    @classmethod
    def from_dict(cls, data: dict) -> GameState:
        """Rebuild a state from :meth:`to_dict` output."""
        from .persistence import state_from_json

        return state_from_json(data)
