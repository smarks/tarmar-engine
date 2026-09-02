"""The battle turn loop — six phases per ``turn-sequence.md``.

Pure: mutates a :class:`~tarmar_engine.state.BattleState`, consumes a seeded
``common.rolling.Roller``, and emits every event through a sink callback.
No Django, no ORM, no module-global randomness — the same seed and state
always reproduce the same event stream.

Phase map (turn-sequence.md):

1. **Initiative** — every combatant rolls 1d6 + their adjDEX modifier
   (``tarmar_rules.dex_modifier``; the per-combatant roll is the
   codified rule as of issue #199, no longer a free-for-all adaptation).
   Higher totals act earlier; ties break by higher combat DEX, then
   combatant id.
2. **Renew Spells** — DEX+INT+WIS order, high→low; continuing spells are
   paid for (mana = level) or end immediately.
3. **Initial Movement** — initiative order. Each actor's policy first picks
   the turn's action option (the option constrains both movement and the
   phase-5 action); movers move now, ranged/static options yield.
4. **Final Movement** — those who yielded take their (small) move now.
5. **Actions** — adjusted-DEX order, high→low. Attacks resolve through
   ``tarmar_rules`` (via :mod:`.resolution`) with situational modifiers from
   ``tarmar_engine.combat_math``. A grappled pair's Struggle Free/Strike
   Back/Hold Still and Maintain/Squeeze/Release also resolve here (issue
   #231, ``hand-to-hand-and-grappling.md``).
6. **Forced Retreat** — those who dealt damage and took none push their
   victim back one hex (special-combat-situations.md); a victim with no
   retreat hex rolls 3d6 ≤ DEX or falls. Not available against or to a
   grappled figure. Survival saves for combatants deep below zero
   (tarmar-studio's characters.models injury_thresholds semantics) are also
   rolled here —
   "every turn" — and a failed save is death.

Rules gaps deliberately noted rather than invented: bleeding from a severe
critical is reported as a status event but not ticked (the rules publish no
rate — same stance as ``tarmar_rules``'s report-only flags), and
casting's "very low and very high rolls have special effects — your GM will
tell you" (casting-spells.md) has no table to implement. The grapple
sub-flow (option o and the grapple-only t/v — ``tarmar_engine.actions``
module docstring) never literally shares a hex with the enemy the way
"Entering Hand-to-Hand" describes; it treats an already-adjacent, engaged
pair as HTH range instead, since merging footprints would break every
occupied-hex invariant the rest of the engine relies on. Casting while
grappled is refused outright rather than modelled against Spell Mastery
levels the engine does not track (``battle.policy`` module docstring).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from . import combat_math, hexes
from . import resolution as combat
from .spells import DODGE_DEX_CHECK_PENALTY, get_spell
from .state import BattleState, CombatantState, WeaponState, bare_handed_damage

# turn-sequence.md phase table: number -> name. Drift-guarded against the
# markdown by battle/tests/test_rules_drift.py.
PHASES: tuple[tuple[int, str], ...] = (
    (1, "Initiative"),
    (2, "Renew Spells"),
    (3, "Initial Movement"),
    (4, "Final Movement"),
    (5, "Actions"),
    (6, "Forced Retreat"),
)

# movement.md fatigue costs per gait (combat turns).
RUN_FATIGUE_COST = 1
SPRINT_FATIGUE_COST = 6

# Distance archers/casters try to keep open with their phase-4 adjustment.
PREFERRED_STANDOFF = 3
WALK_SLOW_MAX = 2

EventSink = Callable[[dict], None]


class TurnRunner:
    """Runs exactly one full turn, keeping the event-sequence bookkeeping."""

    def __init__(self, state: BattleState, roller, sink: EventSink) -> None:
        self.state = state
        self.roller = roller
        self.sink = sink
        self.phase = 0

    # ------------------------------------------------------------------ events
    def emit(
        self,
        event_type: str,
        message: str,
        *,
        actor: str = "",
        payload: dict | None = None,
    ) -> int:
        """Emit one event; returns its battle-global sequence number."""
        sequence = self.state.next_sequence
        self.state.next_sequence += 1
        self.sink(
            {
                "turn": self.state.turn,
                "phase": self.phase,
                "sequence": sequence,
                "event_type": event_type,
                "actor": actor,
                "payload": payload or {},
                "message": message,
            }
        )
        return sequence

    def roll(
        self,
        specification: str,
        *,
        purpose: str,
        actor: str,
        modifier: int = 0,
        target_number: int | None = None,
        outcome: str | None = None,
    ):
        """Roll via the Roller and emit the roll event. Returns (record, seq)."""
        record = self.roller.roll(
            specification,
            purpose=purpose,
            modifier=modifier,
            target_number=target_number,
            outcome=outcome,
        )
        faces = ", ".join(str(face) for face in record.faces)
        message = f"{actor} {purpose} {record.specification}: [{faces}]"
        if record.modifier:
            message += f" {record.modifier:+d}"
        message += f" = {record.total}"
        if record.target_number is not None:
            message += f" vs TN {record.target_number}"
        sequence = self.emit(
            "roll",
            message,
            actor=actor,
            payload={
                "purpose": record.purpose,
                "specification": record.specification,
                "faces": list(record.faces),
                "modifier": record.modifier,
                "total": record.total,
                "target_number": record.target_number,
                "outcome": record.outcome,
            },
        )
        return record, sequence

    def begin_phase(self, number: int, name: str, detail: str = "") -> None:
        self.phase = number
        message = f"Phase {number}: {name}"
        if detail:
            message += f" — {detail}"
        self.emit("phase", message, payload={"phase_name": name})

    # -------------------------------------------------------------- turn logic
    def run(self, choose_option) -> None:
        """Run the six phases of one turn. ``choose_option`` is the AI policy."""
        self.state.turn += 1
        for combatant in self.state.combatants:
            combatant.reset_for_turn()

        order = self.phase_initiative()
        self.phase_renew_spells()
        self.phase_initial_movement(order, choose_option)
        self.phase_final_movement(order)
        self.phase_actions()
        self.phase_forced_retreat()

    # Phase 1 -----------------------------------------------------------------
    def phase_initiative(self) -> list[CombatantState]:
        self.begin_phase(
            1,
            "Initiative",
            "every combatant rolls 1d6 + adjDEX modifier",
        )
        rolls: dict[int, int] = {}
        for combatant in self.state.active_combatants():
            record, _sequence = self.roll(
                "1d6",
                purpose="initiative",
                actor=combatant.name,
                modifier=combatant.dex_bonus,
            )
            rolls[combatant.combatant_id] = record.total
        # Descending total; ties to the higher adjDEX, then stable order.
        order = sorted(
            self.state.active_combatants(),
            key=lambda combatant: (
                -rolls[combatant.combatant_id],
                -combatant.dexterity,
                combatant.combatant_id,
            ),
        )
        if order:
            self.emit(
                "info",
                "Movement order: " + ", ".join(c.name for c in order),
                payload={"order": [c.combatant_id for c in order]},
            )
        return order

    # Phase 2 -----------------------------------------------------------------
    def phase_renew_spells(self) -> None:
        self.begin_phase(2, "Renew Spells", "DEX+INT+WIS order, high to low")
        casters = sorted(
            (c for c in self.state.active_combatants() if c.active_spells),
            key=lambda c: (-c.renewal_order_key, c.combatant_id),
        )
        for caster in casters:
            for key in list(caster.active_spells):
                spell = get_spell(key)
                if caster.grappled_by is not None:
                    # hand-to-hand-and-grappling.md: "A grappled caster can
                    # only renew a spell that needs no gestures (Spell
                    # Mastery 2+); anything else lapses." The engine tracks
                    # no per-spell Spell Mastery level (a data-model gap —
                    # see actions.py's GRAPPLED_ACTIONS docstring), so rather
                    # than guess which spells would qualify, every active
                    # spell conservatively lapses while grappled.
                    caster.active_spells.remove(key)
                    self.emit(
                        "status",
                        f"{caster.name} is grappled and cannot sustain "
                        f"{spell.name}; it lapses",
                        actor=caster.name,
                        payload={"spell": key, "ended": True, "grappled": True},
                    )
                    continue
                if caster.mana >= spell.level:
                    caster.mana -= spell.level
                    self.emit(
                        "action",
                        f"{caster.name} renews {spell.name} "
                        f"({spell.level} mana, {caster.mana} left)",
                        actor=caster.name,
                        payload={"spell": key, "mana_left": caster.mana},
                    )
                else:
                    caster.active_spells.remove(key)
                    self.emit(
                        "status",
                        f"{caster.name} cannot pay for {spell.name}; it ends",
                        actor=caster.name,
                        payload={"spell": key, "ended": True},
                    )

    # Phase 3 -----------------------------------------------------------------
    def phase_initial_movement(self, order, choose_option) -> None:
        self.begin_phase(3, "Initial Movement", "initiative order; move or yield")
        for combatant in order:
            if not combatant.active:
                continue
            decision = choose_option(self.state, combatant)
            combatant.chosen_letter = decision.chosen.letter
            combatant.chosen_target = decision.chosen.target_id
            combatant.chosen_spell = decision.chosen.spell_key
            self.emit(
                "decision",
                f"{combatant.name} chooses {decision.chosen.name} "
                f"({decision.chosen.letter}): {decision.chosen.rationale}",
                actor=combatant.name,
                payload={
                    "chosen": decision.chosen.to_payload(),
                    "candidates": [c.to_payload() for c in decision.candidates],
                },
            )
            if hexes.figure_locked_by_grapple(
                combatant.grappled_by, combatant.grappling
            ):
                # turn-sequence table: "Neither combatant moves — both are
                # locked to the shared hex until the grapple ends." Neither
                # Initial nor Final Movement applies.
                continue
            if decision.chosen.letter in ("a", "b"):
                self.move_towards_target(combatant)
            else:
                combatant.yielded = True

    # Phase 4 -----------------------------------------------------------------
    def phase_final_movement(self, order) -> None:
        self.begin_phase(4, "Final Movement", "those who yielded now move")
        for combatant in order:
            if not combatant.active or not combatant.yielded:
                continue
            if combatant.chosen_letter in ("f", "h", "r"):
                self.kite_step(combatant)

    # Phase 5 -----------------------------------------------------------------
    def phase_actions(self) -> None:
        self.begin_phase(5, "Actions", "adjusted-DEX order, high to low")
        order = sorted(
            self.state.active_combatants(),
            key=lambda c: (-c.dexterity, c.combatant_id),
        )
        for combatant in order:
            if not combatant.active:
                continue  # felled earlier in this very phase
            self.execute_action(combatant)

    # Phase 6 -----------------------------------------------------------------
    def phase_forced_retreat(self) -> None:
        self.begin_phase(6, "Forced Retreat")
        for combatant in self.state.combatants:
            if not combatant.active:
                continue
            if hexes.figure_locked_by_grapple(
                combatant.grappled_by, combatant.grappling
            ):
                # hand-to-hand-and-grappling.md: "Not available against — or
                # to — a grappled figure; there's no hex to push someone
                # into while you're holding them."
                continue
            if not combatant.dealt_damage_this_turn or combatant.took_damage_this_turn:
                continue
            victim = self._retreat_victim(combatant)
            if victim is not None:
                self.push_back(combatant, victim)
        self.survival_saves()

    # ------------------------------------------------------------- subroutines
    def _footprint_clear(self, combatant: CombatantState, anchor, facing) -> bool:
        """Would the combatant's footprint fit at ``anchor`` facing ``facing``?"""
        occupied = self.state.occupied_hexes() - set(combatant.footprint)
        return all(
            cell not in occupied and hexes.in_arena(cell, self.state.arena_radius)
            for cell in hexes.footprint(anchor, facing, combatant.size_hexes)
        )

    def face_towards(self, combatant: CombatantState, target_hex) -> None:
        """Rotate toward ``target_hex`` — unless a multi-hex body cannot swing.

        A multi-hex footprint rotates with its facing; when the rotated body
        would overlap another figure or leave the arena, the figure keeps its
        old facing (the rules publish no partial-rotation case).
        """
        new_facing = hexes.direction_towards(combatant.position, target_hex)
        if new_facing == combatant.facing:
            return
        if combatant.size_hexes > 1 and not self._footprint_clear(
            combatant, combatant.position, new_facing
        ):
            return
        combatant.facing = new_facing

    def _retreat_victim(self, combatant: CombatantState) -> CombatantState | None:
        target_id = combatant.chosen_target
        if target_id is None:
            return None
        victim = self.state.by_id(target_id)
        if not victim.alive or not combat_math.figures_adjacent(combatant, victim):
            return None
        if hexes.figure_locked_by_grapple(victim.grappled_by, victim.grappling):
            # Exempt even when a third party (not the grapple pair itself)
            # dealt the damage — the rule is "against — or to — a grappled
            # figure," not just within the pair.
            return None
        return victim

    def push_back(self, pusher: CombatantState, victim: CombatantState) -> None:
        away = hexes.direction_towards(pusher.position, victim.position)
        destination = hexes.add(victim.position, away)
        if not self._footprint_clear(victim, destination, victim.facing):
            record, _sequence = self.roll(
                "3d6",
                purpose="retreat save",
                actor=victim.name,
                target_number=victim.dexterity,
                outcome=None,
            )
            if record.total > victim.dexterity:
                victim.prone = True
                self.emit(
                    "status",
                    f"{victim.name} has no retreat hex and falls",
                    actor=victim.name,
                    payload={"prone": True},
                )
            else:
                self.emit(
                    "info",
                    f"{victim.name} has no retreat hex but keeps their feet",
                    actor=victim.name,
                )
            return
        vacated = victim.position
        victim.position = destination
        # The pusher may advance into the vacated hex (the rules make it a
        # choice; the simulator always advances). A multi-hex pusher stays —
        # its whole body cannot follow one hex cleanly — and a multi-hex
        # victim's shifted body may still cover the vacated hex.
        advanced = (
            hexes.footprint_size_class(pusher.size_hexes) == 1
            and vacated not in victim.footprint
        )
        if advanced:
            pusher.position = vacated
        self.emit(
            "movement",
            f"{pusher.name} forces {victim.name} back a hex"
            + (" and advances" if advanced else ""),
            actor=pusher.name,
            payload={
                "victim": victim.combatant_id,
                "victim_to": list(destination),
                "pusher_to": list(pusher.position),
            },
        )

    def survival_saves(self) -> None:
        """3d6 ≤ CON for every combatant deep below zero; failure is death.

        Thresholds follow tarmar-studio's
        ``characters.models.Character.injury_thresholds``:
        a pool at or below −ceil(max/2) forces a save every turn, and past
        −max the save is penalized by how far past that threshold the pool
        sits. The fatal chain on a death references the rolls that put the
        combatant here plus this save.
        """
        for combatant in self.state.combatants:
            if not combatant.alive or combatant.conscious:
                continue
            worst_penalty = None
            for pool_value, pool_maximum in (
                (combatant.fatigue, combatant.max_fatigue),
                (combatant.body, combatant.max_body),
            ):
                save_at = -math.ceil(pool_maximum / 2)
                if pool_value > save_at:
                    continue
                penalized_at = -pool_maximum
                penalty = max(0, penalized_at - pool_value)
                if worst_penalty is None or penalty > worst_penalty:
                    worst_penalty = penalty
            if worst_penalty is None:
                continue
            record, sequence = self.roll(
                "3d6",
                purpose="survival",
                actor=combatant.name,
                modifier=worst_penalty,
                target_number=combatant.constitution,
            )
            if record.total <= combatant.constitution:
                self.emit(
                    "info",
                    f"{combatant.name} clings to life (survival save made)",
                    actor=combatant.name,
                )
                continue
            combatant.alive = False
            combatant.fatal_chain.append(sequence)
            self.emit(
                "death",
                f"{combatant.name} dies",
                actor=combatant.name,
                payload={"fatal_chain": list(combatant.fatal_chain)},
            )

    def move_towards_target(self, combatant: CombatantState) -> None:
        """Phase-3 movement for MOVE (run) and CHARGE ATTACK (jog).

        Steps one hex at a time toward the chosen target, stopping the moment
        the mover becomes engaged (movement.md: figures stop immediately when
        engaged). Running costs fatigue (movement.md); jogging is free in
        combat.
        """
        if combatant.chosen_target is None:
            return
        target = self.state.by_id(combatant.chosen_target)
        gait = "run" if combatant.chosen_letter == "a" else "jog"
        allowance = combatant.move_run if gait == "run" else combatant.move_jog
        start = combatant.position
        steps = 0
        for _step in range(allowance):
            if combat_math.is_engaged(self.state, combatant):
                break
            if combat_math.figures_adjacent(combatant, target):
                break
            occupied = self.state.occupied_hexes() - set(combatant.footprint)
            stepped = hexes.step_towards(
                combatant.position,
                target.position,
                occupied,
                self.state.arena_radius,
                combatant.size_hexes,
            )
            if stepped == combatant.position:
                break
            combatant.position = stepped
            combatant.facing = hexes.direction_towards(
                combatant.position, target.position
            )
            steps += 1
        self.face_towards(combatant, target.position)
        combatant.moved_this_turn = steps > 0
        if steps == 0:
            return
        self.emit(
            "movement",
            f"{combatant.name} {gait}s {steps} hex(es) toward {target.name}",
            actor=combatant.name,
            payload={
                "from": list(start),
                "to": list(combatant.position),
                "gait": gait,
                "hexes": steps,
            },
        )
        if gait == "run":
            self.apply_fatigue_cost(combatant, RUN_FATIGUE_COST, "running")

    def kite_step(self, combatant: CombatantState) -> None:
        """Phase-4 walk-slow adjustment: open distance to the nearest enemy."""
        enemies = self.state.enemies_of(combatant)
        if not enemies:
            return
        nearest = min(
            enemies,
            key=lambda enemy: (
                hexes.distance(combatant.position, enemy.position),
                enemy.combatant_id,
            ),
        )
        start = combatant.position
        steps = 0
        for _step in range(WALK_SLOW_MAX):
            if (
                hexes.distance(combatant.position, nearest.position)
                >= PREFERRED_STANDOFF
            ):
                break
            away = hexes.direction_towards(nearest.position, combatant.position)
            candidates = [away, (away + 1) % 6, (away - 1) % 6]
            moved = False
            for direction in candidates:
                destination = hexes.add(combatant.position, direction)
                if not self._footprint_clear(combatant, destination, combatant.facing):
                    continue
                if hexes.distance(destination, nearest.position) <= hexes.distance(
                    combatant.position, nearest.position
                ):
                    continue
                combatant.position = destination
                steps += 1
                moved = True
                break
            if not moved:
                break
        self.face_towards(combatant, nearest.position)
        if steps:
            combatant.moved_this_turn = True
            self.emit(
                "movement",
                f"{combatant.name} steps {steps} hex(es) back from {nearest.name}",
                actor=combatant.name,
                payload={
                    "from": list(start),
                    "to": list(combatant.position),
                    "gait": "walk (slow)",
                    "hexes": steps,
                },
            )

    def apply_fatigue_cost(
        self, combatant: CombatantState, cost: int, reason: str
    ) -> None:
        combatant.fatigue -= cost
        self.emit(
            "status",
            f"{combatant.name} spends {cost} fatigue {reason} "
            f"({combatant.fatigue} left)",
            actor=combatant.name,
            payload={"fatigue": combatant.fatigue, "cost": cost, "reason": reason},
        )
        self.check_unconsciousness(combatant, [])

    # ------------------------------------------------------------ action phase
    def execute_action(self, combatant: CombatantState) -> None:
        letter = combatant.chosen_letter
        if letter in ("g", "p"):
            combatant.prone = False
            self.emit(
                "action",
                f"{combatant.name} stands up (entire turn)",
                actor=combatant.name,
                payload={"letter": letter},
            )
            return
        if letter == "a":
            return  # movement only
        if letter == "c":
            combatant.dodging = True
            self.emit(
                "status",
                f"{combatant.name} dodges (+{hexes.DEFEND_DODGE_TN_BONUS} TN "
                "vs missiles this turn)",
                actor=combatant.name,
                payload={"dodging": True},
            )
            return
        if letter == "k":
            combatant.defending = True
            self.emit(
                "status",
                f"{combatant.name} defends (+{hexes.DEFEND_DODGE_TN_BONUS} TN "
                "vs melee this turn)",
                actor=combatant.name,
                payload={"defending": True},
            )
            return
        if letter == "n":
            self.disengage_step(combatant)
            return
        if letter == "o":
            self.attempt_grapple(combatant)
            return
        if letter == "v":  # Struggle Free (only ever chosen while grappled)
            self.grapple_struggle_free(combatant)
            return
        if letter == "t":  # Strike Back (only ever chosen while grappled)
            self.grapple_strike_back(combatant)
            return
        if letter == "hold_still":
            self.emit(
                "action",
                f"{combatant.name} holds still, waiting out the hold",
                actor=combatant.name,
                payload={"letter": letter},
            )
            return
        if letter == "maintain":
            target = (
                self.state.by_id(combatant.grappling)
                if combatant.grappling is not None
                else None
            )
            message = (
                f"{combatant.name} maintains the hold on {target.name}"
                if target is not None
                else f"{combatant.name} maintains the hold"
            )
            self.emit(
                "action", message, actor=combatant.name, payload={"letter": letter}
            )
            return
        if letter == "squeeze":
            self.grapple_squeeze(combatant)
            return
        if letter == "release":
            if combatant.grappling is not None:
                target = self.state.by_id(combatant.grappling)
                self._end_grapple(
                    combatant,
                    target,
                    message=f"{combatant.name} releases {target.name}",
                    actor_name=combatant.name,
                )
            return
        if letter in ("h", "r"):
            self.cast_spell(combatant)
            return
        if letter in ("b", "j"):
            self.melee_attack(combatant)
            return
        if letter == "f":
            self.missile_attack(combatant)

    def _living_target(self, combatant: CombatantState) -> CombatantState | None:
        """The chosen target if still a valid mark, else the nearest active enemy."""
        if combatant.chosen_target is not None:
            target = self.state.by_id(combatant.chosen_target)
            if target.active:
                return target
        enemies = self.state.enemies_of(combatant)
        if not enemies:
            return None
        return min(
            enemies,
            key=lambda enemy: (
                hexes.distance(combatant.position, enemy.position),
                enemy.combatant_id,
            ),
        )

    def melee_attack(self, combatant: CombatantState) -> None:
        target = self._living_target(combatant)
        if target is None:
            return
        if not combat_math.figures_adjacent(combatant, target):
            self.emit(
                "info",
                f"{combatant.name}'s charge fell short of {target.name}",
                actor=combatant.name,
            )
            return
        combatant.chosen_target = target.combatant_id
        self.face_towards(combatant, target.position)
        self.resolve_attack(combatant, target, ranged=False)

    def missile_attack(self, combatant: CombatantState) -> None:
        target = self._living_target(combatant)
        if target is None:
            return
        if combat_math.is_engaged(self.state, combatant):
            # One Last Shot (l) needs "ready before engaged" state the engine
            # does not track; the archer defends instead.
            combatant.defending = True
            self.emit(
                "status",
                f"{combatant.name} is engaged before loosing and defends instead",
                actor=combatant.name,
                payload={"defending": True},
            )
            return
        combatant.chosen_target = target.combatant_id
        self.face_towards(combatant, target.position)
        self.resolve_attack(combatant, target, ranged=True)

    def resolve_attack(
        self,
        attacker: CombatantState,
        defender: CombatantState,
        *,
        ranged: bool,
        weapon_override: WeaponState | None = None,
        extra_situational: int = 0,
        ignore_defender_bonuses: bool = False,
        verb: str = "attacks",
    ) -> None:
        """Resolve one attack roll through to damage.

        ``weapon_override``/``extra_situational``/``ignore_defender_bonuses``
        are the HTH grapple sub-flow's hooks (hand-to-hand-and-grappling.md):
        a grappled figure's Strike Back and a grappler's Squeeze both go
        through this exact same crit/fumble/damage pipeline, bare-handed and
        with the HTH +4, rather than duplicating it.
        """
        weapon = weapon_override or attacker.weapon
        numbers = combat_math.attack_numbers(
            attacker,
            defender,
            ranged=ranged,
            weapon_class=weapon.weapon_class,
            extra_situational=extra_situational,
            ignore_attacker_skill=weapon_override is not None,
            ignore_defender_bonuses=ignore_defender_bonuses,
        )
        situational_penalty = 0
        if attacker.off_balance:
            situational_penalty = combat_math.OFF_BALANCE_PENALTY
            attacker.off_balance = False
        bonus = numbers.bonus - situational_penalty
        record, attack_sequence = self.roll(
            "1d20",
            purpose="attack",
            actor=attacker.name,
            modifier=bonus,
            target_number=numbers.target_number,
        )
        die = record.faces[0]
        confirm_roll = None
        if die == combat.DIE_FACES:
            confirm_record, _sequence = self.roll(
                "1d20",
                purpose="confirm",
                actor=attacker.name,
                modifier=bonus,
                target_number=numbers.target_number,
            )
            confirm_roll = confirm_record.faces[0]
        fumble_roll = None
        if die == 1:
            fumble_record, _sequence = self.roll(
                "1d6", purpose="fumble", actor=attacker.name
            )
            fumble_roll = fumble_record.faces[0]
        result = combat.resolve_attack(
            die,
            numbers.target_number,
            bonus,
            confirm_roll=confirm_roll,
            fumble_roll=fumble_roll,
        )
        arc_note = f" from the {numbers.arc}" if numbers.arc != "front" else ""
        self.emit(
            "action",
            f"{attacker.name} {verb} {defender.name}{arc_note} with "
            f"{weapon.name}: {result['outcome']}",
            actor=attacker.name,
            payload={
                "target": defender.combatant_id,
                "outcome": result["outcome"],
                "arc": numbers.arc,
                "ranged": ranged,
                "attack_roll": attack_sequence,
            },
        )
        if result["fumble"]:
            self.apply_fumble(attacker, result["fumble_detail"], weapon=weapon)
            return
        if not result["hit"]:
            return
        damage_total = 0
        damage_sequences: list[int] = []
        for _repetition in range(result["damage_multiplier"]):
            damage_record, damage_sequence = self.roll(
                weapon.damage, purpose="damage", actor=attacker.name
            )
            damage_total += damage_record.total
            damage_sequences.append(damage_sequence)
        # Floored once, matching tarmar-studio's characters/attack.py damage handling.
        damage_total = max(0, damage_total)
        net = combat.damage_after_armour(
            damage_total,
            defender.stops,
            weapon.weapon_class,
            defender.armour_tier,
        )
        self.apply_damage(
            attacker,
            defender,
            net,
            raw=damage_total,
            reaches_body=result["severe"],
            chain=[attack_sequence, *damage_sequences],
        )
        if result["severe"]:
            self.emit(
                "status",
                f"{defender.name} is bleeding (severe critical; "
                "GM-adjudicated, not ticked by the engine)",
                actor=defender.name,
                payload={"bleeding": True},
            )

    def apply_fumble(
        self,
        attacker: CombatantState,
        detail: dict | None,
        *,
        weapon: WeaponState | None = None,
    ) -> None:
        if detail is None:
            return
        key = detail["key"]
        acting_weapon = weapon or attacker.weapon
        if (attacker.is_beast or acting_weapon.item_id == "") and key != "off_balance":
            # A beast's natural weapons, and a bare-handed HTH action alike,
            # can neither drop nor break: the §7 drop/stress fumbles degrade
            # to a stumble (off-balance) instead. attack-rolls.md's own
            # Naturals note for unarmed strikes: "no weapon to break."
            attacker.off_balance = True
            reason = (
                "natural weapons cannot drop or break"
                if attacker.is_beast
                else "no weapon to drop or break bare-handed"
            )
            self.emit(
                "status",
                f"{attacker.name} stumbles and is off-balance ({reason})",
                actor=attacker.name,
                payload={"fumble": "off_balance"},
            )
            return
        if key == "off_balance":
            attacker.off_balance = True
            message = f"{attacker.name} is off-balance ({detail['effect']})"
        elif key == "drop_weapon":
            message = (
                f"{attacker.name} drops their {attacker.weapon.name} "
                "and fights bare-handed"
            )
            attacker.weapon = self._unarmed_weapon(attacker)
            attacker.weapon_skill_level = 0
        else:  # weapon_stress
            if attacker.weapon_stressed:
                message = (
                    f"{attacker.name}'s {attacker.weapon.name} breaks (second fumble)"
                )
                attacker.weapon = self._unarmed_weapon(attacker)
                attacker.weapon_skill_level = 0
            else:
                attacker.weapon_stressed = True
                message = (
                    f"{attacker.name}'s {attacker.weapon.name} takes stress "
                    f"({detail['effect']})"
                )
        self.emit(
            "status",
            message,
            actor=attacker.name,
            payload={"fumble": key},
        )

    @staticmethod
    def _unarmed_weapon(combatant: CombatantState) -> WeaponState:
        return WeaponState(damage=bare_handed_damage(combatant.strength))

    def _step_away_from(
        self, combatant: CombatantState, threat: CombatantState
    ) -> tuple[int, int] | None:
        """The nearest empty, in-arena hex stepping ``combatant`` away from
        ``threat`` — straight back first, then either flank. Shared by plain
        Disengage (n) and a grappled figure's Struggle Free (v), which both
        need "one hex away, footprint clear" and nothing more."""
        away = hexes.direction_towards(threat.position, combatant.position)
        for direction in (away, (away + 1) % 6, (away - 1) % 6):
            destination = hexes.add(combatant.position, direction)
            if self._footprint_clear(combatant, destination, combatant.facing):
                return destination
        return None

    def disengage_step(self, combatant: CombatantState) -> None:
        """Option n: move one hex away from adjacent enemies instead of attacking."""
        enemies = self.state.enemies_of(combatant)
        adjacent = [
            enemy for enemy in enemies if combat_math.figures_adjacent(combatant, enemy)
        ]
        if not adjacent:
            return
        threat = adjacent[0]
        destination = self._step_away_from(combatant, threat)
        if destination is None:
            self.emit(
                "info",
                f"{combatant.name} has nowhere to disengage to",
                actor=combatant.name,
            )
            return
        start = combatant.position
        combatant.position = destination
        combatant.moved_this_turn = True
        self.emit(
            "movement",
            f"{combatant.name} disengages one hex from {threat.name}",
            actor=combatant.name,
            payload={"from": list(start), "to": list(destination), "gait": "shift"},
        )

    # --------------------------------------------------------------- grapple
    def attempt_grapple(self, attacker: CombatantState) -> None:
        """Option o, implemented as "Initiating a Grapple"
        (hand-to-hand-and-grappling.md): the same attack roll as an unarmed
        strike, checked against the Flexible/Snare row instead of Striking,
        with the HTH +4 to the attacker and no weapon skill. A hit holds the
        target in place (:func:`hexes.figure_locked_by_grapple`) rather than
        dealing damage. A natural 20 grapples automatically and stuns the
        target off-balance (no confirm roll — there is no damage to double);
        a natural 1 fumbles onto the grapple-specific table
        (:meth:`apply_grapple_fumble`)."""
        target_id = attacker.chosen_target
        if target_id is None:
            return
        defender = self.state.by_id(target_id)
        if not defender.active or not combat_math.figures_adjacent(attacker, defender):
            return
        if hexes.figure_locked_by_grapple(attacker.grappled_by, attacker.grappling):
            return
        if hexes.figure_locked_by_grapple(defender.grappled_by, defender.grappling):
            return
        numbers = combat_math.attack_numbers(
            attacker,
            defender,
            ranged=False,
            weapon_class="Flexible / Snare",
            extra_situational=hexes.HTH_TO_HIT_BONUS,
            ignore_attacker_skill=True,
        )
        situational_penalty = 0
        if attacker.off_balance:
            situational_penalty = combat_math.OFF_BALANCE_PENALTY
            attacker.off_balance = False
        bonus = numbers.bonus - situational_penalty
        record, attack_sequence = self.roll(
            "1d20",
            purpose="grapple attempt",
            actor=attacker.name,
            modifier=bonus,
            target_number=numbers.target_number,
        )
        die = record.faces[0]
        if die == combat.DIE_FACES:
            self._establish_grapple(attacker, defender)
            defender.off_balance = True
            self.emit(
                "action",
                f"{attacker.name} grapples {defender.name} with a natural 20 "
                f"— {defender.name} is briefly stunned off-balance",
                actor=attacker.name,
                payload={
                    "target": defender.combatant_id,
                    "outcome": "critical grapple",
                    "attack_roll": attack_sequence,
                },
            )
            return
        if die == 1:
            fumble_record, _sequence = self.roll(
                "1d6", purpose="fumble", actor=attacker.name
            )
            self.emit(
                "action",
                f"{attacker.name} fumbles the grapple attempt on {defender.name}",
                actor=attacker.name,
                payload={
                    "target": defender.combatant_id,
                    "outcome": "fumble",
                    "attack_roll": attack_sequence,
                },
            )
            self.apply_grapple_fumble(
                attacker, combat.fumble_result(fumble_record.faces[0])
            )
            return
        hit = die + bonus >= numbers.target_number
        if hit:
            self._establish_grapple(attacker, defender)
            self.emit(
                "action",
                f"{attacker.name} grapples and holds {defender.name}",
                actor=attacker.name,
                payload={
                    "target": defender.combatant_id,
                    "outcome": "hit",
                    "attack_roll": attack_sequence,
                },
            )
        else:
            self.emit(
                "action",
                f"{attacker.name} fails to grapple {defender.name}",
                actor=attacker.name,
                payload={
                    "target": defender.combatant_id,
                    "outcome": "miss",
                    "attack_roll": attack_sequence,
                },
            )

    def apply_grapple_fumble(self, attacker: CombatantState, detail: dict) -> None:
        """The §7 fumble subtable, reworded by "Initiating a Grapple" for a
        bare-handed grapple attempt: off-balance is unchanged, "drop weapon"
        becomes ending up prone, and "weapon takes stress" becomes
        off-balance instead — there is no weapon to lose or break."""
        key = detail["key"]
        if key == "drop_weapon":
            attacker.prone = True
            message = f"{attacker.name} overextends and ends up prone"
        else:  # off_balance or weapon_stress -> off-balance
            attacker.off_balance = True
            message = f"{attacker.name} overextends and is off-balance"
        self.emit(
            "status",
            message,
            actor=attacker.name,
            payload={"fumble": key},
        )

    @staticmethod
    def _establish_grapple(attacker: CombatantState, defender: CombatantState) -> None:
        attacker.grappling = defender.combatant_id
        defender.grappled_by = attacker.combatant_id

    def _end_grapple(
        self,
        grappler: CombatantState,
        grapplee: CombatantState,
        *,
        message: str,
        actor_name: str,
    ) -> None:
        grappler.grappling = None
        grapplee.grappled_by = None
        self.emit(
            "status",
            message,
            actor=actor_name,
            payload={
                "grapple_ended": True,
                "grappler": grappler.combatant_id,
                "grapplee": grapplee.combatant_id,
            },
        )

    def grapple_struggle_free(self, combatant: CombatantState) -> None:
        """Struggle Free (letter v): "the same roll as a plain HTH
        Disengage" — 4d6 <= effective DEX. Success stands the figure up and
        moves it to an adjacent empty hex; failure leaves it held."""
        grappler_id = combatant.grappled_by
        if grappler_id is None:
            return
        grappler = self.state.by_id(grappler_id)
        record, _sequence = self.roll(
            "4d6",
            purpose="escape",
            actor=combatant.name,
            target_number=combatant.dexterity,
        )
        if record.total > combatant.dexterity:
            self.emit(
                "info",
                f"{combatant.name} fails to struggle free and remains held",
                actor=combatant.name,
            )
            return
        destination = self._step_away_from(combatant, grappler)
        if destination is not None:
            combatant.position = destination
            combatant.moved_this_turn = True
        combatant.prone = False
        self._end_grapple(
            grappler,
            combatant,
            message=f"{combatant.name} struggles free of {grappler.name}"
            + ("" if destination is not None else " but has nowhere to step to"),
            actor_name=combatant.name,
        )

    def grapple_strike_back(self, combatant: CombatantState) -> None:
        """Strike Back (letter t): "a normal unarmed strike ... against your
        captor, at the same HTH +4 both of you already have." Full
        crit/fumble resolution via :meth:`resolve_attack`, bare-handed."""
        grappler_id = combatant.grappled_by
        if grappler_id is None:
            return
        grappler = self.state.by_id(grappler_id)
        self.resolve_attack(
            combatant,
            grappler,
            ranged=False,
            weapon_override=self._unarmed_weapon(combatant),
            extra_situational=hexes.HTH_TO_HIT_BONUS,
            verb="strikes back at",
        )

    def grapple_squeeze(self, combatant: CombatantState) -> None:
        """Squeeze: the grappler's bare-handed strike on the held target,
        "using the same Bare-Handed Damage table and armour stops as any
        unarmed strike. The target gets no Dodge/Defend bonus against it —
        they're already held.\" """
        target_id = combatant.grappling
        if target_id is None:
            return
        target = self.state.by_id(target_id)
        self.resolve_attack(
            combatant,
            target,
            ranged=False,
            weapon_override=self._unarmed_weapon(combatant),
            extra_situational=hexes.HTH_TO_HIT_BONUS,
            ignore_defender_bonuses=True,
            verb="squeezes",
        )

    def cast_spell(self, combatant: CombatantState) -> None:
        spell = get_spell(combatant.chosen_spell)
        if combatant.mana < spell.level:
            self.emit(
                "info",
                f"{combatant.name} lacks the mana for {spell.name}",
                actor=combatant.name,
            )
            return
        # casting-spells.md: spells cost mana equal to the spell's level —
        # paid on the attempt.
        combatant.mana -= spell.level
        attribute_name = spell.attribute
        attribute = (
            combatant.intelligence if attribute_name == "INT" else combatant.wisdom
        )
        record, cast_sequence = self.roll(
            "3d6",
            purpose="casting",
            actor=combatant.name,
            target_number=attribute,
            outcome=None,
        )
        succeeded = record.total <= attribute
        self.emit(
            "action",
            f"{combatant.name} casts {spell.name} "
            f"(3d6 ≤ {attribute_name} {attribute}): "
            f"{'success' if succeeded else 'failure'} "
            f"({spell.level} mana, {combatant.mana} left)",
            actor=combatant.name,
            payload={
                "spell": spell.key,
                "success": succeeded,
                "mana_left": combatant.mana,
                "casting_roll": cast_sequence,
            },
        )
        if not succeeded:
            return
        if spell.continuing:
            combatant.active_spells.append(spell.key)
            self.emit(
                "status",
                f"{spell.name} shimmers around {combatant.name}",
                actor=combatant.name,
                payload={"spell": spell.key, "active": True},
            )
            return
        if spell.heals:
            heal_record, _sequence = self.roll(
                spell.damage or "1d6", purpose="healing", actor=combatant.name
            )
            healed = min(
                max(0, heal_record.total), combatant.max_fatigue - combatant.fatigue
            )
            combatant.fatigue += healed
            self.emit(
                "status",
                f"{combatant.name} heals {healed} fatigue "
                f"({combatant.fatigue}/{combatant.max_fatigue})",
                actor=combatant.name,
                payload={"healed": healed, "fatigue": combatant.fatigue},
            )
            return
        target = self._living_target(combatant)
        if target is None:
            return
        if spell.targeted:
            # casting-spells.md: magic requiring hitting a target needs an
            # additional DEX roll — 3d6 ≤ DEX. A dodging target's +4-TN is
            # re-mapped onto the caster's effective DEX for this check.
            effective_dex = combatant.dexterity
            if target.dodging:
                effective_dex -= DODGE_DEX_CHECK_PENALTY
            aim_record, _sequence = self.roll(
                "3d6",
                purpose="spell aim",
                actor=combatant.name,
                target_number=effective_dex,
            )
            if aim_record.total > effective_dex:
                self.emit(
                    "info",
                    f"{combatant.name}'s {spell.name} misses {target.name}",
                    actor=combatant.name,
                )
                return
        damage_record, damage_sequence = self.roll(
            spell.damage or "1d6", purpose="spell damage", actor=combatant.name
        )
        raw = max(0, damage_record.total)
        net = raw if spell.ignores_armour else max(0, raw - target.stops)
        self.apply_damage(
            combatant,
            target,
            net,
            raw=raw,
            reaches_body=spell.damage_pool == "body",
            chain=[cast_sequence, damage_sequence],
            body_only=spell.damage_pool == "body",
        )

    # ------------------------------------------------------------------ damage
    def apply_damage(
        self,
        attacker: CombatantState,
        defender: CombatantState,
        net: int,
        *,
        raw: int,
        reaches_body: bool,
        chain: list[int],
        body_only: bool = False,
    ) -> None:
        """Apply post-armour damage and record the roll chain that caused it.

        attack-rolls.md: damage applies to Fatigue first; a severe critical
        reaches Body as well. The ``chain`` (to-hit and damage roll sequence
        numbers) is remembered on the defender so a later death event can
        cite the exact rolls that killed them.
        """
        if net <= 0:
            self.emit(
                "damage",
                f"{defender.name}'s armour stops the blow ({raw} rolled)",
                actor=attacker.name,
                payload={"target": defender.combatant_id, "net": 0, "raw": raw},
            )
            return
        if body_only:
            defender.body -= net
        else:
            defender.fatigue -= net
            if reaches_body:
                defender.body -= net
        defender.took_damage_this_turn = True
        attacker.dealt_damage_this_turn = True
        defender.fatal_chain = list(chain)
        pools = f"fatigue {defender.fatigue}/{defender.max_fatigue}"
        if reaches_body or body_only:
            pools += f", body {defender.body}/{defender.max_body}"
        self.emit(
            "damage",
            f"{defender.name} takes {net} damage ({pools})",
            actor=attacker.name,
            payload={
                "target": defender.combatant_id,
                "net": net,
                "raw": raw,
                "fatigue": defender.fatigue,
                "body": defender.body,
                "chain": list(chain),
            },
        )
        self.check_unconsciousness(defender, chain)

    def check_unconsciousness(
        self, combatant: CombatantState, chain: list[int]
    ) -> None:
        """Pool at or below 0 → unconscious (injury-thresholds semantics)."""
        if not combatant.conscious:
            return
        if combatant.fatigue > 0 and combatant.body > 0:
            return
        combatant.conscious = False
        combatant.prone = True
        if chain:
            combatant.fatal_chain = list(chain)
        self.emit(
            "status",
            f"{combatant.name} collapses unconscious",
            actor=combatant.name,
            payload={"unconscious": True, "chain": list(chain)},
        )


def run_turn(state: BattleState, roller, sink: EventSink, choose_option) -> None:
    """Run exactly one full turn of the battle. Mutates ``state``."""
    TurnRunner(state, roller, sink).run(choose_option)
