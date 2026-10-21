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
rate — same stance as ``tarmar_rules``'s report-only flags). The casting
success tiers, Push, Channel, Borrowing and Runaway's later turns run only
on a profile with injected :class:`~tarmar_engine.magic.MagicRules`
(tarmar-engine #26/#27/#29/#28/#30/#31), which carry every number they use.
A Runaway feeds on its caster in phase 2, before the renewals, and is
anchored as a phase-5 action. A counter is declared as a phase-5 action and
meets its caster's spell when the spell is cast, before the casting roll,
whatever the two figures' DEX order (house reading
``counter_resolves_at_the_cast``).
HTH (options o/t/u/v — ``tarmar_engine.actions`` module docstring) never
literally shares a hex with the enemy the way "Entering Hand-to-Hand"
describes; it treats an adjacent pair as HTH range instead, since merging
footprints would break every occupied-hex invariant the rest of the engine
relies on. A caster in a grapple casts only a spell known at Spell Mastery 3
and renews only one known at 2 or better (``CombatantState.spell_mastery``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from . import actions, combat_math, hexes, movement, policy, weapons
from . import resolution as combat
from .dice import parse_dice_expression
from .magic import (
    COUNTER_BOUNDS,
    LIFTED_BOUNDS_KEY,
    PUSH_CAP,
    BorrowingRules,
    CastingFailureRules,
    CastingSuccessTier,
    CounterspellRules,
    MagicRules,
    PushRules,
    RunawayRules,
    attribute_score,
    bound_lifted,
    casting_mana,
    casting_success_ceiling,
    spell_effect_kind,
)
from .profile import TARMAR, RulesProfile
from .spells import DODGE_DEX_CHECK_PENALTY, Spell, get_spell
from .state import (
    BattleState,
    CombatantState,
    ContestState,
    CounterState,
    GroundWeapon,
    RunawayState,
    WeaponState,
    bare_handed_damage,
)

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

#: Why a Counterspell contest ended (tarmar-engine #31), by the key its
#: ``contest_ended`` event carries, as the log says it.
CONTEST_END_REASONS: dict[str, str] = {
    "another_action": "chooses another action",
    "unopposed": "presses it again and no counter meets it",
    "through": "gets past the counter",
}

# Gait by movement option, kept under this name for every reader of it; the
# table lives with the rest of the movement rules (tarmar_engine.movement).
MOVEMENT_GAITS = movement.MOVEMENT_GAITS
GAIT_FATIGUE_COSTS: dict[str, int] = {
    "run": RUN_FATIGUE_COST,
    "sprint": SPRINT_FATIGUE_COST,
}

# Distance archers/casters try to keep open with their phase-4 adjustment.
PREFERRED_STANDOFF = 3
WALK_SLOW_MAX = 2

EventSink = Callable[[dict], None]


def _net_text(start: tuple[int, int], end: tuple[int, int]) -> str:
    """The ground a move covered, for a movement line that counts its steps.

    Steps taken and ground covered differ when a figure goes round or is
    turned aside: a line that read "jogs 9 hex(es)" for nine steps ending a
    hex from the start was read as nine hexes covered (tarmar-engine #37).
    """
    return f", ending {hexes.distance(start, end)} hex(es) from its start"


#: Chooses a forced retreat's hex and whether the pusher advances:
#: ``(state, pusher, victim, hexes, may_advance) -> RetreatChoice``.
RetreatChooser = Callable[..., "policy.RetreatChoice"]


class RollRecord(Protocol):
    """One roll as the injected roller records it: the fields the engine
    reads (``common.rolling.Roller``'s record, or a test's stand-in)."""

    @property
    def purpose(self) -> str: ...

    @property
    def specification(self) -> str: ...

    @property
    def faces(self) -> tuple[int, ...]: ...

    @property
    def modifier(self) -> int: ...

    @property
    def total(self) -> int: ...

    @property
    def target_number(self) -> int | None: ...

    @property
    def outcome(self) -> str | None: ...


@dataclass(frozen=True)
class RollUnder:
    """A roll-under magic roll, thrown and not yet logged.

    The Control Roll (tarmar-engine #27) and the debt roll (#28) share it:
    each rolls injected dice under a target, fails on certain natural totals
    whatever the target, and logs the roll with an outcome only it can name.

    Attributes:
        record: The roller's record, to hand to :meth:`TurnRunner.emit_roll`.
        natural_total: The dice faces summed, no modifier.
        margin: How far the total exceeded the target; 0 or less made it.
        automatic: Was the natural total on the injected automatic list?
    """

    record: RollRecord
    natural_total: int
    margin: int
    automatic: bool


class TurnRunner:
    """Runs exactly one full turn, keeping the event-sequence bookkeeping.

    ``profile`` is the rules-profile seam (``tarmar_engine.profile``): the
    runner consults it for engagement, forced-retreat, injury-reaction, and
    grapple decisions. The default — :data:`~tarmar_engine.profile.TARMAR` —
    reproduces the pre-seam six-phase behavior exactly.
    """

    def __init__(
        self,
        state: BattleState,
        roller,
        sink: EventSink,
        profile: RulesProfile | None = None,
        choose_retreat: RetreatChooser | None = None,
    ) -> None:
        self.state = state
        self.roller = roller
        self.sink = sink
        self.profile = profile or TARMAR
        self.phase = 0
        # special-combat-situations.md lets the pusher choose the retreat hex
        # and whether to advance (#779); the default is the AI's choice.
        self.choose_retreat = choose_retreat or policy.choose_retreat
        # The combatant whose phase-5 action is being taken off-balance: the
        # fumble's "−2 to your next action" lands on that one action, whatever
        # it is, and is spent by it (#811).
        self._off_balance_actor: int | None = None
        self._acting = False
        # The defenders whose counter action has been taken this Actions
        # phase, at their own action or at their caster's cast, whichever
        # came first (tarmar-engine #31).
        self._counters_taken: set[int] = set()

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

    def roll_unlogged(
        self,
        specification: str,
        *,
        purpose: str,
        modifier: int = 0,
        target_number: int | None = None,
    ):
        """Roll without emitting, for a verdict only known after later dice.

        Every roll taken this way must be handed to :meth:`emit_roll`. The
        pairing exists so an attack's d20 can be logged *with* its hit/miss
        verdict, which the resolver only knows once the crit-confirm and
        fumble dice have also been thrown (tarmar-studio #301). It is not a
        way to keep a die off the log.
        """
        return self.roller.roll(
            specification,
            purpose=purpose,
            modifier=modifier,
            target_number=target_number,
            outcome=None,
        )

    def emit_roll(
        self,
        record,
        *,
        actor: str,
        versus: str = "TN",
        roll_under: bool = False,
        outcome: str | None = None,
    ) -> int:
        """Emit one roll event; returns its sequence number.

        The message follows character creation's shape (``3d6: [4, 5, 3] =
        12``), then says what the total was measured against and how it came
        out::

            Wulf attack 1d20: [13] +5 = 18 vs TN 14 (need 14+) - hit
            Sara casting 3d6: [4, 5, 3] = 12 vs INT 12 (need 12 or less) - success

        ``roll_under`` is load-bearing: the engine tests some totals for "at
        least" (the d20 attack) and others for "at most" (3d6 attribute
        checks, retreat and survival saves), and a bare "vs TN 12" left a
        reader no way to tell which way the comparison ran.
        """
        faces = ", ".join(str(face) for face in record.faces)
        message = f"{actor} {record.purpose} {record.specification}: [{faces}]"
        if record.modifier:
            message += f" {record.modifier:+d}"
        message += f" = {record.total}"
        if record.target_number is not None:
            need = (
                f"{record.target_number} or less"
                if roll_under
                else f"{record.target_number}+"
            )
            message += f" vs {versus} {record.target_number} (need {need})"
        verdict = outcome if outcome is not None else record.outcome
        if verdict:
            message += f" — {verdict}"
        return self.emit(
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
                "versus": versus,
                "roll_under": roll_under,
                "outcome": verdict,
            },
        )

    def roll(
        self,
        specification: str,
        *,
        purpose: str,
        actor: str,
        modifier: int = 0,
        target_number: int | None = None,
        outcome: str | None = None,
        versus: str = "TN",
        roll_under: bool = False,
        judge=None,
    ):
        """Roll via the Roller and emit the roll event. Returns (record, seq).

        ``judge`` is called with the record and returns the verdict text for
        the log — the hook for a save whose made/failed reading lives at the
        call site rather than inside the roller.
        """
        record = self.roller.roll(
            specification,
            purpose=purpose,
            modifier=modifier,
            target_number=target_number,
            outcome=outcome,
        )
        if outcome is None and judge is not None:
            outcome = judge(record)
        sequence = self.emit_roll(
            record,
            actor=actor,
            versus=versus,
            roll_under=roll_under,
            outcome=outcome,
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
        self._start_of_turn_missile_state()

        order = self.phase_initiative()
        self.phase_renew_spells()
        self.phase_initial_movement(order, choose_option)
        self.phase_final_movement(order)
        self.phase_actions()
        self.phase_forced_retreat()

    def _start_of_turn_missile_state(self) -> None:
        """A crossbow comes a turn closer to loaded; a figure that starts the
        turn disengaged has a fresh One Last Shot for its next engagement."""
        for combatant in self.state.combatants:
            # The weapon in hand is reloaded; one slung or on the ground is not.
            if combatant.weapon.reload_turns_left > 0:
                combatant.weapon = replace(
                    combatant.weapon,
                    reload_turns_left=combatant.weapon.reload_turns_left - 1,
                )
            if combatant.active and not self.profile.engagement.is_engaged(
                self.state, combatant
            ):
                combatant.last_shot_spent = False
            # Hand-to-hand lasts while the pair stays adjacent and fighting.
            combatant.hth_with = [
                other_id
                for other_id in combatant.hth_with
                if combatant.active
                and self.state.by_id(other_id).active
                and combat_math.figures_adjacent(combatant, self.state.by_id(other_id))
            ]

    def phase_initiative(self) -> list[CombatantState]:
        self.begin_phase(
            1,
            "Initiative",
            "every combatant rolls 1d6 + adjDEX modifier",
        )
        rolls: dict[int, int] = {}
        for combatant in self.state.active_combatants():
            # injury-thresholds-death.md: "−2 to all rolls" (or −1) — the
            # initiative roll among them (#817).
            record, _sequence = self.roll(
                "1d6",
                purpose="initiative",
                actor=combatant.name,
                modifier=combatant.dex_bonus
                - self.profile.reactions.injury_penalty(combatant),
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

    def _in_renewal_order(
        self, wanted: Callable[[CombatantState], bool]
    ) -> list[CombatantState]:
        """The living combatants ``wanted`` admits, in phase 2's DEX+INT+WIS
        order, high first."""
        return sorted(
            (c for c in self.state.combatants if c.alive and wanted(c)),
            key=lambda c: (-c.renewal_order_key, c.combatant_id),
        )

    def phase_renew_spells(self) -> None:
        """Phase 2: every Runaway feeds (with injected Runaway rules), then
        the continuing spells are renewed or end."""
        self.begin_phase(2, "Renew Spells", "DEX+INT+WIS order, high to low")
        magic = self.profile.magic
        if magic is not None and magic.runaway is not None:
            for caster in self._in_renewal_order(lambda c: c.runaway is not None):
                self._feed_runaway(caster, magic.runaway)
        for caster in self._in_renewal_order(lambda c: bool(c.active_spells)):
            for key in list(caster.active_spells):
                spell = get_spell(key)
                if not caster.active:
                    # casting-spells.md: "Unrenewed spells end immediately."
                    # An unconscious caster renews nothing (#826).
                    self._end_active_spell(caster, key)
                    self.emit(
                        "status",
                        f"{caster.name} is unconscious and cannot renew "
                        f"{spell.name}; it ends",
                        actor=caster.name,
                        payload={"spell": key, "ended": True, "unconscious": True},
                    )
                    continue
                if (
                    self.profile.grapple.locks_movement(
                        caster.grappled_by, caster.grappling
                    )
                    and caster.spell_mastery.get(key, 1) < 2
                ):
                    # hand-to-hand-and-grappling.md: "A grappled caster can
                    # only renew a spell that needs no gestures (Spell
                    # Mastery 2+); anything else lapses." (#821) Both sides
                    # of a hold have their hands full ("holding on or being
                    # held"), so the grappler too — coordinator's ruling.
                    self._end_active_spell(caster, key)
                    held = "is grappled" if caster.grappled_by is not None else (
                        "is holding a grapple"
                    )
                    self.emit(
                        "status",
                        f"{caster.name} {held} and cannot sustain "
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
                    self._end_active_spell(caster, key)
                    self.emit(
                        "status",
                        f"{caster.name} cannot pay for {spell.name}; it ends",
                        actor=caster.name,
                        payload={"spell": key, "ended": True},
                    )

    @staticmethod
    def _end_active_spell(caster: CombatantState, key: str) -> None:
        """A continuing spell ends for its caster, and with it any growth an
        anchored Runaway of it earned (``anchored_turns_running``)."""
        caster.active_spells.remove(key)
        caster.anchored_turns_running.pop(key, None)

    @staticmethod
    def _decision_payload(decision: policy.Decision) -> dict:
        """A decision event's payload. Only a decision that withheld casts
        (the Channel, tarmar-engine #29) carries ``withheld``, so every other
        decision logs the payload it always has."""
        payload: dict = {
            "forecast": True,
            "chosen": decision.chosen.to_payload(),
            "candidates": [c.to_payload() for c in decision.candidates],
        }
        if decision.withheld:
            payload["withheld"] = list(decision.withheld)
        return payload

    def phase_initial_movement(self, order, choose_option) -> None:
        self.begin_phase(3, "Initial Movement", "initiative order; move or yield")
        for combatant in order:
            if not combatant.active:
                continue
            decision = choose_option(self.state, combatant)
            combatant.chosen_letter = decision.chosen.letter
            combatant.chosen_target = decision.chosen.target_id
            combatant.chosen_spell = decision.chosen.spell_key
            combatant.chosen_push_mana = decision.chosen.push_mana
            combatant.chosen_borrow = decision.chosen.borrow
            combatant.chosen_anchor = decision.chosen.anchor_target
            combatant.chosen_counter = self._counter_choice(decision.chosen)
            combatant.chosen_destination = decision.chosen.destination
            # "Forecast" up front because this block is written before any
            # die is thrown: its P(hit) is what the AI expected, not what
            # happened. The roll and damage events that follow are the record
            # of what actually happened, and a reader had no way to tell the
            # two kinds of line apart (#301).
            self.emit(
                "decision",
                f"Forecast — {combatant.name} chooses {decision.chosen.name} "
                f"({decision.chosen.letter}): {decision.chosen.rationale}",
                actor=combatant.name,
                payload=self._decision_payload(decision),
            )
            if combatant.contest is not None and not self._escalates(combatant):
                self._end_contest(combatant, "another_action")
            # A hex the option may not take is refused here, in the log, and
            # the option makes its derived move instead (tarmar-engine #20).
            self._placed_destination(combatant)
            if self.profile.grapple.locks_movement(
                combatant.grappled_by, combatant.grappling
            ):
                # turn-sequence table: "Neither combatant moves — both are
                # locked to the shared hex until the grapple ends." Neither
                # Initial nor Final Movement applies.
                continue
            option = actions.base_option(decision.chosen.letter)
            placed = combatant.chosen_destination is not None
            kind = movement.placement(self.state, combatant, decision.chosen.letter)
            if actions.is_yielded(decision.chosen.letter):
                combatant.yielded = True
            elif placed and kind == "gait":
                self.move_to_destination(combatant)
            elif placed and kind == "shift":
                # "Shift 1 hex or stand still during movement".
                self.shift_to(combatant)
            elif option in MOVEMENT_GAITS:
                self.move_towards_target(combatant)
            else:
                # Everyone else yields: a yielded mover (#819) takes its
                # movement in phase 4, the missile/cast options their
                # walk-slow step, and the rest stand.
                combatant.yielded = True

    # Phase 4 -----------------------------------------------------------------
    def phase_final_movement(self, order) -> None:
        self.begin_phase(4, "Final Movement", "those who yielded now move")
        for combatant in order:
            if not combatant.active or not combatant.yielded:
                continue
            option = actions.base_option(combatant.chosen_letter)
            if actions.is_yielded(combatant.chosen_letter):
                if self._placed_destination(combatant) is not None:
                    self.move_to_destination(combatant)
                elif option in MOVEMENT_GAITS:
                    self.move_towards_target(combatant)
            elif option in ("f", "h"):
                # MISSILE ATTACK and CAST SPELL move at "Walk (slow)". The
                # engaged cast (r) is "Shift/still" (action-options.md), so it
                # takes no walk-slow step, in hand-to-hand or out of it: it
                # shifts only to a chosen hex (tarmar-engine #20, #34).
                self.kite_step(combatant)

    def phase_actions(self) -> None:
        self.begin_phase(5, "Actions", "adjusted-DEX order, high to low")
        self._counters_taken = set()
        order = sorted(
            self.state.active_combatants(),
            key=lambda c: (-c.dexterity, c.combatant_id),
        )
        for combatant in order:
            if not combatant.active:
                continue  # felled earlier in this very phase
            self.execute_action(combatant)
        self._lapse_counters()

    # Phase 6 -----------------------------------------------------------------
    def phase_forced_retreat(self) -> None:
        """Eligibility and victim selection are the retreat seam's calls
        (``profile.retreat``); the runner keeps the pushing and the events."""
        self.begin_phase(6, "Forced Retreat")
        for combatant in self.state.combatants:
            if not combatant.active:
                continue
            if not self.profile.retreat.pusher_eligible(combatant):
                continue
            victim = self.profile.retreat.victim_of(self.state, combatant)
            if victim is not None:
                self.push_back(combatant, victim)
        self.survival_saves()

    # ------------------------------------------------------------- subroutines
    def _footprint_clear(self, combatant: CombatantState, anchor, facing) -> bool:
        """Would the combatant's footprint fit at ``anchor`` facing ``facing``?"""
        return movement.footprint_clear(self.state, combatant, anchor, facing)

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

    def push_back(self, pusher: CombatantState, victim: CombatantState) -> None:
        """special-combat-situations.md's Forced Retreat, steps 1–3.

        "Push enemy back 1 hex in any direction", "Choose to advance into
        vacated hex or stand still", and only when there is no retreat hex
        at all, "enemy rolls 3d6 ≤ DEX or falls" (#779). The hex and the
        advance are the retreat chooser's; the rules offer the choices.
        """
        destinations = self.profile.retreat.retreat_hexes(self.state, pusher, victim)
        if not destinations:
            # injury-thresholds-death.md's "−2 to all rolls" reaches the
            # save too (#817).
            save_target = self.profile.retreat.blocked_save_target(
                victim
            ) - self.profile.reactions.injury_penalty(victim)
            record, _sequence = self.roll(
                self.profile.retreat.blocked_save_dice,
                purpose="retreat save",
                actor=victim.name,
                target_number=save_target,
                versus="DEX",
                roll_under=True,
                judge=lambda made: (
                    "falls" if made.total > save_target else "keeps their feet"
                ),
            )
            if record.total > save_target:
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
        # A multi-hex pusher cannot follow one hex cleanly, so it may not
        # advance; a single-hex pusher may, unless the victim's shifted body
        # still covers the vacated hex.
        choice = self.choose_retreat(
            self.state,
            pusher,
            victim,
            destinations,
            hexes.footprint_size_class(pusher.size_hexes) == 1,
        )
        if choice.destination not in destinations:
            raise ValueError(
                f"{choice.destination} is not a hex {victim.name} can be pushed into"
            )
        destination = choice.destination
        victim.position = destination
        advanced = (
            choice.advance
            and hexes.footprint_size_class(pusher.size_hexes) == 1
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

        Threshold arithmetic is the reactions seam's call
        (``profile.reactions`` — tarmar-studio's
        ``characters.models.Character.injury_thresholds`` semantics:
        a pool at or below −ceil(max/2) forces a save every turn, and past
        −max the save is penalized by how far past that threshold the pool
        sits). The fatal chain on a death references the rolls that put the
        combatant here plus this save.
        """
        for combatant in self.state.combatants:
            if not combatant.alive or combatant.conscious:
                continue
            worst_penalty = self.profile.reactions.survival_save_penalty(combatant)
            if worst_penalty is None:
                continue
            save_target = self.profile.reactions.survival_save_target(combatant)
            record, sequence = self.roll(
                "3d6",
                purpose="survival",
                actor=combatant.name,
                modifier=worst_penalty,
                target_number=save_target,
                roll_under=True,
                judge=lambda save: (
                    "clings to life" if save.total <= save_target else "dies"
                ),
            )
            if record.total <= save_target:
                self.emit(
                    "info",
                    f"{combatant.name} clings to life (survival save made)",
                    actor=combatant.name,
                )
                continue
            combatant.fatal_chain.append(sequence)
            self._die(combatant)

    def _die(self, combatant: CombatantState) -> None:
        """The engine's one death: the combatant dies, the ``death`` event
        cites its fatal chain, its grapples end, and any Runaway ends with
        it."""
        combatant.alive = False
        combatant.runaway = None
        self.emit(
            "death",
            f"{combatant.name} dies",
            actor=combatant.name,
            payload={"fatal_chain": list(combatant.fatal_chain)},
        )
        # A corpse neither holds nor is held (#11).
        self._release_grapples_involving(combatant)

    def _placed_destination(self, combatant: CombatantState) -> tuple[int, int] | None:
        """The chosen hex, if the chosen option may still move there.

        Checked when the option is chosen and again when the move is made,
        since the board moves in between. A hex it may not take is refused in
        the log and dropped, and the option makes the move it makes with no
        hex: the derived move, a DISENGAGE straight back, a DROP in place, an
        engaged option standing still (tarmar-engine #20).
        """
        destination = combatant.chosen_destination
        if destination is None:
            return None
        reason = movement.refusal(
            self.state, combatant, combatant.chosen_letter, destination
        )
        if reason is None:
            return destination
        combatant.chosen_destination = None
        self.emit(
            "info",
            f"{combatant.name} cannot move to {destination}: {reason}",
            actor=combatant.name,
            payload={"destination_refused": list(destination), "reason": reason},
        )
        return None

    def _face_chosen_target(self, combatant: CombatantState) -> None:
        if combatant.chosen_target is None:
            return
        target = self.state.by_id(combatant.chosen_target)
        if target.active:
            self.face_towards(combatant, target.position)

    def move_to_destination(self, combatant: CombatantState) -> None:
        """A gait option's move to its chosen hex (tarmar-engine #20).

        Along the shortest clear path within the option's gait, stopping the
        moment the mover is engaged (movement.md), then facing the option's
        target if it has one. The hex has been checked by
        :meth:`_placed_destination`.
        """
        destination = combatant.chosen_destination
        if destination is None:
            return
        key = combatant.chosen_letter
        gait = movement.PLACED_GAITS[actions.base_option(key)]
        path = movement.path_to(self.state, combatant, key, destination)
        if path is None:
            raise ValueError(
                f"{combatant.name} has no path to {destination}; "
                "_placed_destination checks it first"
            )
        start = combatant.position
        steps = 0
        for cell in path:
            if self.profile.engagement.is_engaged(self.state, combatant):
                break
            combatant.facing = hexes.direction_towards(combatant.position, cell)
            combatant.position = cell
            steps += 1
        self._face_chosen_target(combatant)
        combatant.moved_this_turn = steps > 0
        if steps == 0:
            return
        self.emit(
            "movement",
            f"{combatant.name} {gait}s {steps} hex(es) to {combatant.position}"
            f"{_net_text(start, combatant.position)}",
            actor=combatant.name,
            payload={
                "from": list(start),
                "to": list(combatant.position),
                "gait": gait,
                "hexes": steps,
                "net_hexes": hexes.distance(start, combatant.position),
                "destination": list(destination),
            },
        )
        cost = GAIT_FATIGUE_COSTS.get(gait, 0)
        if cost:
            self.apply_fatigue_cost(
                combatant, cost, "running" if gait == "run" else "sprinting"
            )

    def shift_to(self, combatant: CombatantState) -> None:
        """An engaged option's one-hex Shift to its chosen hex (#20).

        special-combat-situations.md: "Shift 1 hex or stand still during
        movement". The hex has been checked by :meth:`_placed_destination`.
        """
        destination = combatant.chosen_destination
        if destination is None:
            return
        start = combatant.position
        combatant.position = destination
        combatant.moved_this_turn = True
        self._face_chosen_target(combatant)
        self.emit(
            "movement",
            f"{combatant.name} shifts one hex to {destination}",
            actor=combatant.name,
            payload={"from": list(start), "to": list(destination), "gait": "shift"},
        )

    def move_towards_target(self, combatant: CombatantState) -> None:
        """Movement toward the chosen target at the option's gait.

        Walks the chosen option's :func:`movement.default_approach` toward the
        chosen target, the straight line or, when that is blocked, a clear
        path round, stopping the moment the mover becomes engaged
        (movement.md: figures stop immediately when engaged). Running costs 1
        Fatigue and sprinting 6 (movement.md); jogging is free in combat. The
        gait is the chosen option's (:func:`movement.option_gait`: MOVE runs,
        CHARGE ATTACK jogs).

        Raises:
            ValueError: for a chosen option that makes no move toward a
                target.
        """
        if combatant.chosen_target is None:
            return
        target = self.state.by_id(combatant.chosen_target)
        gait = movement.option_gait(combatant.chosen_letter)
        approach = movement.default_approach(
            self.state, combatant, combatant.chosen_letter, target
        )
        start = combatant.position
        steps = 0
        for cell, facing in approach.route:
            if self.profile.engagement.is_engaged(self.state, combatant):
                break
            combatant.position = cell
            combatant.facing = facing
            steps += 1
        self.face_towards(combatant, target.position)
        combatant.moved_this_turn = steps > 0
        if steps == 0:
            return
        self.emit(
            "movement",
            f"{combatant.name} {gait}s {steps} hex(es) toward {target.name}"
            f"{_net_text(start, combatant.position)}",
            actor=combatant.name,
            payload={
                "from": list(start),
                "to": list(combatant.position),
                "gait": gait,
                "hexes": steps,
                "net_hexes": hexes.distance(start, combatant.position),
            },
        )
        cost = GAIT_FATIGUE_COSTS.get(gait, 0)
        if cost:
            self.apply_fatigue_cost(
                combatant, cost, "running" if gait == "run" else "sprinting"
            )

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
                f"{combatant.name} steps {steps} hex(es) back from {nearest.name}"
                f"{_net_text(start, combatant.position)}",
                actor=combatant.name,
                payload={
                    "from": list(start),
                    "to": list(combatant.position),
                    "gait": "walk (slow)",
                    "hexes": steps,
                    "net_hexes": hexes.distance(start, combatant.position),
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
        """Take one combatant's phase-5 action.

        A figure off-balance from a fumble takes this action at −2, whatever
        it is, and the penalty is spent by it (attack-rolls.md: "−2 to your
        next action"; #811).
        """
        self._acting = True
        self._off_balance_actor = None
        if combatant.off_balance:
            combatant.off_balance = False
            self._off_balance_actor = combatant.combatant_id
        try:
            self._execute_option(combatant)
        finally:
            self._acting = False
            self._off_balance_actor = None

    def _action_roll_penalty(self, combatant: CombatantState) -> int:
        """The situational penalty a roll the figure makes as its action owes.

        Two penalties, both properties of the figure rather than of the roll:
        injury-thresholds-death.md's -1/-2 band for a badly hurt figure (#296)
        and the off-balance -2 owed by the action being taken (#811). One
        reading for every such roll so none can drift (#12, tarmar-engine
        #43): the attack roll (both shots of a double shot) and its critical
        confirm roll, the grapple attempt, the hand-to-hand disengage and
        Struggle Free, drawing a dagger, a targeted spell's aim, the casting
        roll, the Control Roll, the debt roll, the anchor roll and the
        caster's side of a counter contest.
        """
        return self.profile.reactions.injury_penalty(
            combatant
        ) + self._off_balance_penalty(combatant)

    def _off_balance_penalty(self, combatant: CombatantState) -> int:
        """The off-balance −2 owed by the action being taken, else 0.

        Inside a phase-5 action the flag was taken up when the action began,
        so a fumble during the action leaves the next one owing it. A roll
        method called on its own, outside any action (a caller resolving one
        attack), spends the flag on that roll, as it always has.
        """
        if combatant.combatant_id == self._off_balance_actor:
            return combat_math.OFF_BALANCE_PENALTY
        if not self._acting and combatant.off_balance:
            combatant.off_balance = False
            return combat_math.OFF_BALANCE_PENALTY
        return 0

    def _execute_option(self, combatant: CombatantState) -> None:
        letter = actions.base_option(combatant.chosen_letter)
        if letter in ("g", "p"):
            combatant.prone = False
            self.emit(
                "action",
                f"{combatant.name} stands up (entire turn)",
                actor=combatant.name,
                payload={"letter": letter},
            )
            return
        if letter in ("a", "sprint"):
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
        if letter == "d":
            self.drop_prone(combatant)
            return
        if letter == "e":
            self.ready_weapon(combatant)
            return
        if letter == "m":
            self.change_weapon(combatant)
            return
        if letter == "q":
            self.pick_up_weapon(combatant)
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
        if letter == "v":  # Struggle Free when held, else the HTH DISENGAGE
            if combatant.grappled_by is not None:
                self.grapple_struggle_free(combatant)
            else:
                self.hth_disengage(combatant)
            return
        if letter == "t":
            if combatant.grappled_by is not None:
                self.grapple_strike_back(combatant)
            else:
                self.hth_strike(combatant)
            return
        if letter == "u":
            self.draw_dagger(combatant)
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
        if letter == policy.ANCHOR_OPTION:
            self.anchor_runaway(combatant)
            return
        if letter == actions.COUNTER_OPTION:
            self.declare_counter(combatant)
            return
        if letter in ("b", "j"):
            self.melee_attack(combatant)
            return
        if letter == "f":
            self.missile_attack(combatant)
            return
        if letter == "l":
            self.missile_attack(combatant, last_shot=True)

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
            if combatant.combatant_id in target.disengaged_from:
                self._strike_at_disengager(combatant, target)
                return
            self.emit(
                "info",
                f"{combatant.name}'s charge fell short of {target.name}",
                actor=combatant.name,
            )
            return
        combatant.chosen_target = target.combatant_id
        self.face_towards(combatant, target.position)
        self.resolve_attack(combatant, target, ranged=False)

    def _strike_at_disengager(
        self, attacker: CombatantState, target: CombatantState
    ) -> None:
        """special-combat-situations.md, Disengaging: "Slower enemies attack
        at penalty = difference in adjDEX" (#777).

        The disengager stepped away at its own place in the adjDEX order, so
        a slower enemy it was next to reaches it now, a hex off, at a to-hit
        penalty of the gap between their adjDEX. (A faster one struck before
        the step; an equal one strikes at no penalty.)
        """
        penalty = max(0, target.dexterity - attacker.dexterity)
        self.emit(
            "info",
            f"{attacker.name} strikes at {target.name} as they disengage "
            f"({-penalty:+d} to hit: adjDEX {attacker.dexterity} against "
            f"{target.dexterity})",
            actor=attacker.name,
            payload={"disengage_strike": True, "penalty": penalty},
        )
        attacker.chosen_target = target.combatant_id
        self.face_towards(attacker, target.position)
        self.resolve_attack(attacker, target, ranged=False, extra_situational=-penalty)

    def missile_attack(
        self, combatant: CombatantState, *, last_shot: bool = False
    ) -> None:
        """MISSILE ATTACK (f) and ONE LAST SHOT (l).

        An archer who chose f and was engaged before it could loose — charged
        in movement — takes One Last Shot instead when its missile weapon was
        ready before the engagement (action-options.md: "Fire missile (if
        ready before engaged)"; #776); otherwise it defends, as before. A bow
        in quick enough hands looses twice (weapons.md "2 shots/turn if
        adjDEX N+"), a crossbow then needs its turns to reload, and a thrown
        weapon leaves the hand and lies where it fell (#781/#812).
        """
        target = self._living_target(combatant)
        if target is None:
            return
        engaged = self.profile.engagement.is_engaged(self.state, combatant)
        if engaged and not last_shot:
            if self._has_last_shot(combatant):
                self.emit(
                    "info",
                    f"{combatant.name} is engaged before loosing and takes "
                    "One Last Shot",
                    actor=combatant.name,
                    payload={"last_shot": True},
                )
                last_shot = True
            else:
                combatant.defending = True
                self.emit(
                    "status",
                    f"{combatant.name} is engaged before loosing and defends instead",
                    actor=combatant.name,
                    payload={"defending": True},
                )
                return
        if not weapons.can_shoot(combatant):
            self.emit(
                "info",
                f"{combatant.name} has nothing ready to loose",
                actor=combatant.name,
            )
            return
        weapon = combatant.weapon
        shots = 1 if last_shot else weapons.shots_per_turn(weapon, combatant.dexterity)
        for shot in range(shots):
            if shot:
                if combatant.weapon is not weapon or not combatant.active:
                    break  # fumbled the bow away, or felled mid-volley
                target = self._living_target(combatant)
                if target is None:
                    break
                self.emit(
                    "info",
                    f"{combatant.name} looses a second shot this turn "
                    f"(adjDEX {combatant.dexterity}, {weapon.double_shot_dex}+ "
                    f"for a {weapon.name})",
                    actor=combatant.name,
                    payload={"second_shot": True},
                )
            combatant.chosen_target = target.combatant_id
            self.face_towards(combatant, target.position)
            self.resolve_attack(combatant, target, ranged=True)
            if weapon.is_thrown and combatant.weapon is weapon:
                self._weapon_leaves_hand(combatant, lands_at=target.position)
                self.emit(
                    "status",
                    f"{combatant.name}'s {weapon.name} lands in {target.name}'s hex",
                    actor=combatant.name,
                    payload={"thrown": True, "lands_at": list(target.position)},
                )
                break
        if last_shot:
            combatant.last_shot_spent = True
        if combatant.weapon is weapon:
            cycle = weapons.reload_cycle(weapon, combatant.dexterity)
            if cycle > 1:
                combatant.weapon = replace(weapon, reload_turns_left=cycle)
                self.emit(
                    "status",
                    f"{combatant.name} must reload the {weapon.name}: it shoots "
                    f"again in {cycle} turns",
                    actor=combatant.name,
                    payload={"reload_turns": cycle},
                )

    def _has_last_shot(self, combatant: CombatantState) -> bool:
        """A fired missile weapon, loaded, and this engagement's shot unspent."""
        return (
            weapons.is_fired_missile(combatant.weapon)
            and combatant.weapon.reload_turns_left == 0
            and not combatant.last_shot_spent
        )

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
        bonus = numbers.bonus - self._action_roll_penalty(attacker)
        # The three dice are thrown first and logged after, so each one can
        # carry the verdict the resolver reaches — the d20 line says "hit",
        # not just a number a reader has to adjudicate themselves (#301).
        record = self.roll_unlogged(
            "1d20",
            purpose="attack",
            modifier=bonus,
            target_number=numbers.target_number,
        )
        die = record.faces[0]
        confirm_record = None
        confirm_roll = None
        if die == combat.DIE_FACES:
            confirm_record = self.roll_unlogged(
                "1d20",
                purpose="confirm",
                modifier=bonus,
                target_number=numbers.target_number,
            )
            confirm_roll = confirm_record.faces[0]
        fumble_record = None
        fumble_roll = None
        if die == 1:
            fumble_record = self.roll_unlogged("1d6", purpose="fumble")
            fumble_roll = fumble_record.faces[0]
        result = combat.resolve_attack(
            die,
            numbers.target_number,
            bonus,
            confirm_roll=confirm_roll,
            fumble_roll=fumble_roll,
        )
        attack_sequence = self.emit_roll(
            record, actor=attacker.name, outcome=result["outcome"]
        )
        if confirm_record is not None:
            self.emit_roll(
                confirm_record,
                actor=attacker.name,
                outcome=(
                    "severe critical confirmed"
                    if result["severe"]
                    else "not confirmed, ordinary critical"
                ),
            )
        if fumble_record is not None:
            detail = result["fumble_detail"] or {}
            self.emit_roll(
                fumble_record,
                actor=attacker.name,
                outcome=detail.get("label") or "fumble",
            )
        # Everything the score was forecast from, restated as what actually
        # faced the dice: weapon, reach, to-hit bonus and Target Number.
        arc_note = f" from the {numbers.arc}" if numbers.arc != "front" else ""
        self.emit(
            "action",
            f"{attacker.name} {verb} {defender.name}{arc_note} with "
            f"{weapon.name} at {combat_math.hexes_text(numbers.distance)} "
            f"(d20 {bonus:+d} vs TN {numbers.target_number}): "
            f"{result['outcome']}",
            actor=attacker.name,
            payload={
                "target": defender.combatant_id,
                "outcome": result["outcome"],
                "arc": numbers.arc,
                "ranged": ranged,
                "attack_roll": attack_sequence,
                "weapon": weapon.name,
                "distance": numbers.distance,
                "to_hit_bonus": bonus,
                "target_number": numbers.target_number,
            },
        )
        if result["fumble"]:
            self.apply_fumble(attacker, result["fumble_detail"], weapon=weapon)
            return
        if not result["hit"]:
            return
        damage_total = 0
        damage_sequences: list[int] = []
        rolls_due = result["damage_multiplier"]
        # attack-rolls.md: a critical rolls "the weapon's damage **dice**
        # twice" (a confirmed severe one, three times); the damage modifier
        # is added once, with the first roll (#824 — the severe case's
        # "triple damage" read the same way: coordinator's ruling under the
        # standing rule, Spencer may overrule).
        count, sides, modifier = parse_dice_expression(weapon.damage)
        dice_only = weapon.damage if modifier == 0 else f"{count}d{sides}"
        for repetition in range(rolls_due):
            outcome = None
            if rolls_due > 1:
                outcome = f"critical damage roll {repetition + 1} of {rolls_due}"
                if repetition and modifier:
                    outcome += ", dice only: the modifier counts once"
            damage_record, damage_sequence = self.roll(
                weapon.damage if repetition == 0 else dice_only,
                purpose="damage",
                actor=attacker.name,
                outcome=outcome,
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
        if net > 0:
            # #813: a physical hit is a weapon or bare-handed blow that gets
            # damage past the armour (derived-pools.md "normal hits reduce
            # Fatigue"; turn-sequence.md "dealt damage"). A blow the armour
            # stops entirely is not one, and neither is a spell.
            # Coordinator's ruling under the standing rule; Spencer may
            # overrule.
            attacker.dealt_physical_hit_this_turn = True
            defender.took_physical_hit_this_turn = True
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
        if acting_weapon.stressed and acting_weapon is attacker.weapon:
            # attack-rolls.md: "weapon takes stress (breaks on a second
            # fumble)" — any second fumble, whatever its own roll (#814).
            # The roll's own result still lands where there is anything left
            # for it to act on: an off-balance roll leaves the fumbler
            # off-balance as well.
            message = f"{attacker.name}'s {attacker.weapon.name} breaks (second fumble)"
            self._weapon_leaves_hand(attacker, lands_at=None)
            if key == "off_balance":
                attacker.off_balance = True
                message += f" and they are off-balance ({detail['effect']})"
        elif key == "off_balance":
            attacker.off_balance = True
            message = f"{attacker.name} is off-balance ({detail['effect']})"
        elif key == "drop_weapon":
            message = (
                f"{attacker.name} drops their {attacker.weapon.name} "
                "and fights bare-handed"
            )
            # It lies in the fumbler's hex, to be picked up again (#780).
            self._weapon_leaves_hand(attacker, lands_at=attacker.position)
        else:  # weapon_stress
            # A new value, never an in-place edit: a caller may seat one
            # WeaponState object in several hands.
            attacker.weapon = replace(attacker.weapon, stressed=True)
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

    # ------------------------------------------------------------- weapons
    def _weapon_leaves_hand(
        self, combatant: CombatantState, *, lands_at: tuple[int, int] | None
    ) -> None:
        """The readied weapon leaves the hand: onto a hex, or gone (broken).

        Its skill level is remembered, so readying it again restores it.
        """
        weapon = combatant.weapon
        if weapon.item_id:
            combatant.weapon_skills[weapon.item_id] = combatant.weapon_skill_level
            if lands_at is not None:
                self.state.ground_weapons.append(
                    GroundWeapon(q=lands_at[0], r=lands_at[1], weapon=weapon)
                )
        combatant.weapon = self._unarmed_weapon(combatant)
        combatant.weapon_skill_level = 0

    @staticmethod
    def _take_up(combatant: CombatantState, weapon: WeaponState) -> None:
        """Ready ``weapon`` at the figure's skill with it."""
        combatant.weapon = weapon
        combatant.weapon_skill_level = combatant.weapon_skills.get(weapon.item_id, 0)

    def ready_weapon(self, combatant: CombatantState) -> None:
        """READY WEAPON (e): "Re-sling current, ready new weapon" (#780).

        The weapon readied is :func:`policy.best_weapon` among those carried;
        the one in hand goes back on the figure. Shields are not modelled as
        readied items (a shield's bonus is a standing fact of the snapshot).
        """
        choice = policy.best_weapon(combatant, combatant.spare_weapons)
        if choice is None:
            self.emit(
                "info",
                f"{combatant.name} has no other weapon to ready",
                actor=combatant.name,
            )
            return
        combatant.spare_weapons.remove(choice)
        previous = combatant.weapon
        if previous.item_id:
            combatant.weapon_skills[previous.item_id] = combatant.weapon_skill_level
            combatant.spare_weapons.append(previous)
        self._take_up(combatant, choice)
        self.emit(
            "action",
            f"{combatant.name} readies their {choice.name}"
            + (f", slinging the {previous.name}" if previous.item_id else ""),
            actor=combatant.name,
            payload={"letter": "e", "weapon": choice.name},
        )

    def change_weapon(self, combatant: CombatantState) -> None:
        """CHANGE WEAPON (m): "Drop current, ready new non-missile" (#780)."""
        choice = policy.best_weapon(
            combatant,
            [spare for spare in combatant.spare_weapons if not spare.is_missile],
        )
        if choice is None:
            self.emit(
                "info",
                f"{combatant.name} carries no other hand weapon to change to",
                actor=combatant.name,
            )
            return
        combatant.spare_weapons.remove(choice)
        previous = combatant.weapon
        self._weapon_leaves_hand(combatant, lands_at=combatant.position)
        self._take_up(combatant, choice)
        self.emit(
            "action",
            f"{combatant.name} changes to their {choice.name}"
            + (f", dropping the {previous.name}" if previous.item_id else ""),
            actor=combatant.name,
            payload={"letter": "m", "weapon": choice.name},
        )

    def weapons_in_reach(self, combatant: CombatantState) -> list[GroundWeapon]:
        """Weapons lying in the figure's hex or an adjacent one."""
        return [
            lying
            for lying in self.state.ground_weapons
            if hexes.distance(lying.position, combatant.position) <= 1
        ]

    def pick_up_weapon(self, combatant: CombatantState) -> None:
        """PICK UP WEAPON (q): "Drop yours, grab from hex/adjacent" (#780)."""
        in_reach = self.weapons_in_reach(combatant)
        choice = policy.best_weapon(combatant, [lying.weapon for lying in in_reach])
        if choice is None:
            self.emit(
                "info",
                f"{combatant.name} finds no weapon within reach",
                actor=combatant.name,
            )
            return
        lying = next(entry for entry in in_reach if entry.weapon is choice)
        self.state.ground_weapons.remove(lying)
        previous = combatant.weapon
        self._weapon_leaves_hand(combatant, lands_at=combatant.position)
        self._take_up(combatant, choice)
        self.emit(
            "action",
            f"{combatant.name} picks up the {choice.name}"
            + (f", dropping the {previous.name}" if previous.item_id else ""),
            actor=combatant.name,
            payload={
                "letter": "q",
                "weapon": choice.name,
                "from": list(lying.position),
            },
        )

    def drop_prone(self, combatant: CombatantState) -> None:
        """DROP (d): "Go prone or kneeling". Kneeling is not a state the
        engine keeps, so the figure goes prone."""
        combatant.prone = True
        self.emit(
            "action",
            f"{combatant.name} drops prone",
            actor=combatant.name,
            payload={"letter": "d", "prone": True},
        )

    def _step_destination(
        self, combatant: CombatantState, threat: CombatantState
    ) -> tuple[int, int] | None:
        """Where a DISENGAGE (n) or a v steps: the chosen hex while it is
        still next to the figure and clear, else straight back from
        ``threat``, then either flank (:func:`movement.step_away_hex`)."""
        chosen = self._placed_destination(combatant)
        if chosen is not None:
            return chosen
        return movement.step_away_hex(self.state, combatant, threat)

    def disengage_step(self, combatant: CombatantState) -> None:
        """Option n: move one hex instead of attacking.

        "Move 1 hex any direction instead of attack": to the chosen hex, or
        with none, away from the first adjacent enemy (tarmar-engine #20).
        """
        if self.profile.grapple.locks_movement(
            combatant.grappled_by, combatant.grappling
        ):
            # "Escaping": Struggle Free (4d6 <= effective DEX) is the way out
            # of a hold. A plain Disengage must not walk a locked figure out
            # of the shared hex with the hold still standing (#11).
            self.emit(
                "info",
                f"{combatant.name} is locked in a grapple and must "
                "struggle free rather than disengage",
                actor=combatant.name,
            )
            return
        adjacent = movement.left_behind(self.state, combatant, "n")
        if not adjacent:
            return
        threat = adjacent[0]
        destination = self._step_destination(combatant, threat)
        if destination is None:
            self.emit(
                "info",
                f"{combatant.name} has nowhere to disengage to",
                actor=combatant.name,
            )
            return
        start = combatant.position
        # A slower enemy it leaves behind may still strike it (#777).
        combatant.disengaged_from = [enemy.combatant_id for enemy in adjacent]
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
        # Options are chosen in Phase 3 and enacted in Phase 5, with
        # everyone's movement in between, so a declared attempt can arrive at
        # a target that stepped away, was felled, or was grabbed by somebody
        # else first. Each refusal says so, the way melee_attack's "charge
        # fell short" line already did, rather than leaving the player a
        # Forecast line and an otherwise empty turn (#14).
        target_id = attacker.chosen_target
        if target_id is None:
            self.emit(
                "info",
                f"{attacker.name} has no one to close on and the grapple "
                "attempt comes to nothing",
                actor=attacker.name,
                payload={"grapple_refused": "no_target"},
            )
            return
        defender = self.state.by_id(target_id)
        if not defender.active or not combat_math.figures_adjacent(attacker, defender):
            self.emit(
                "info",
                f"{attacker.name} could not close on {defender.name} "
                "to grapple",
                actor=attacker.name,
                payload={
                    "grapple_refused": "out_of_reach",
                    "target": defender.combatant_id,
                },
            )
            return
        if hexes.figure_locked_by_grapple(attacker.grappled_by, attacker.grappling):
            self.emit(
                "info",
                f"{attacker.name} is already in a hold and cannot grapple "
                f"{defender.name}",
                actor=attacker.name,
                payload={
                    "grapple_refused": "attacker_held",
                    "target": defender.combatant_id,
                },
            )
            return
        if hexes.figure_locked_by_grapple(defender.grappled_by, defender.grappling):
            self.emit(
                "info",
                f"{defender.name} is already held and {attacker.name} "
                "cannot take a grip",
                actor=attacker.name,
                payload={
                    "grapple_refused": "target_held",
                    "target": defender.combatant_id,
                },
            )
            return
        if combat_math.hth_entry_reason(self.state, attacker, defender) is None:
            # "Entering Hand-to-Hand": only against an enemy with its back to
            # a wall, down, slower, attacked from the rear, or agreeing (#823).
            self.emit(
                "info",
                f"{attacker.name} finds no way in to grapple {defender.name}",
                actor=attacker.name,
                payload={
                    "grapple_refused": "no_entry",
                    "target": defender.combatant_id,
                },
            )
            return
        self._enter_hth(attacker, defender)
        numbers = combat_math.attack_numbers(
            attacker,
            defender,
            ranged=False,
            weapon_class="Flexible / Snare",
            extra_situational=self.profile.grapple.to_hit_bonus,
            ignore_attacker_skill=True,
        )
        bonus = numbers.bonus - self._action_roll_penalty(attacker)
        record, attack_sequence = self.roll(
            "1d20",
            purpose="grapple attempt",
            actor=attacker.name,
            modifier=bonus,
            target_number=numbers.target_number,
            judge=lambda grab: (
                "natural 20, the hold takes"
                if grab.faces[0] == combat.DIE_FACES
                else "natural 1, fumbled"
                if grab.faces[0] == 1
                else "held"
                if grab.total >= numbers.target_number
                else "slips free"
            ),
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
    def _enter_hth(first: CombatantState, second: CombatantState) -> None:
        """The pair is in hand-to-hand from now until they part (#867)."""
        if second.combatant_id not in first.hth_with:
            first.hth_with.append(second.combatant_id)
        if first.combatant_id not in second.hth_with:
            second.hth_with.append(first.combatant_id)

    def _leave_hand_to_hand(self, combatant: CombatantState) -> None:
        """The figure and every partner part: a v DISENGAGE or a Struggle
        Free that succeeds (special-combat-situations.md, "From HTH")."""
        for other_id in combatant.hth_with:
            other = self.state.by_id(other_id)
            if combatant.combatant_id in other.hth_with:
                other.hth_with.remove(combatant.combatant_id)
        combatant.hth_with = []

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

    def _release_grapples_involving(self, combatant: CombatantState) -> None:
        """End every hold ``combatant`` is part of, as captor or as captive.

        hand-to-hand-and-grappling.md locks both sides "to the shared hex
        until the grapple ends" and publishes no case in which a figure that
        has left the fight keeps holding, or keeps being held. Called
        wherever a combatant stops being ``active`` — unconsciousness and
        death — so a hold never outlives one of its two parties (#11).
        """
        held_id = combatant.grappling
        if held_id is not None:
            held = self.state.by_id(held_id)
            self._end_grapple(
                combatant,
                held,
                message=f"{combatant.name} can hold {held.name} no longer; "
                "the grapple ends",
                actor_name=combatant.name,
            )
        captor_id = combatant.grappled_by
        if captor_id is not None:
            captor = self.state.by_id(captor_id)
            self._end_grapple(
                captor,
                combatant,
                message=f"{captor.name} no longer holds {combatant.name}; "
                "the grapple ends",
                actor_name=captor.name,
            )

    def _escape_roll(
        self, combatant: CombatantState, *, made: str, failed: str
    ) -> bool:
        """The HTH DISENGAGE roll, "Roll 4d6 ≤ DEX", made or not.

        Struggle Free uses "the same roll as a plain HTH Disengage". The DEX
        is effective: injury-thresholds-death.md's -1/-2 band (#296) and an
        off-balance figure's −2 on this action (#811) come off it.
        """
        effective_dex = combatant.dexterity - self._action_roll_penalty(combatant)
        record, _sequence = self.roll(
            "4d6",
            purpose="escape",
            actor=combatant.name,
            target_number=effective_dex,
            versus="DEX",
            roll_under=True,
            judge=lambda attempt: failed if attempt.total > effective_dex else made,
        )
        return record.total <= effective_dex

    def grapple_struggle_free(self, combatant: CombatantState) -> None:
        """Struggle Free (letter v): "the same roll as a plain HTH
        Disengage" — 4d6 <= effective DEX. Success stands the figure up and
        moves it to an adjacent empty hex (the chosen one, tarmar-engine #20);
        failure leaves it held."""
        grappler_id = combatant.grappled_by
        if grappler_id is None:
            return
        grappler = self.state.by_id(grappler_id)
        if not self._escape_roll(combatant, made="struggles free", failed="still held"):
            self.emit(
                "info",
                f"{combatant.name} fails to struggle free and remains held",
                actor=combatant.name,
            )
            return
        destination = self._step_destination(combatant, grappler)
        if destination is not None:
            combatant.position = destination
            combatant.moved_this_turn = True
        combatant.prone = False
        # "the same roll as a plain HTH Disengage", and the same outcome: out
        # of hand-to-hand with every partner, stepped clear or not (#19).
        self._leave_hand_to_hand(combatant)
        self._end_grapple(
            grappler,
            combatant,
            message=f"{combatant.name} struggles free of {grappler.name}"
            + ("" if destination is not None else " but has nowhere to step to"),
            actor_name=combatant.name,
        )

    def hth_disengage(self, combatant: CombatantState) -> None:
        """DISENGAGE (v) from hand-to-hand outside a grapple (tarmar-engine #19).

        special-combat-situations.md, "From HTH (option v)": "Roll 4d6 ≤
        DEX", "Success: stand up and move to adjacent empty hex", "Failure:
        remain in HTH". The hex is the chosen one, else straight back from
        the first partner. A success leaves hand-to-hand with every partner,
        even with no clear hex to step to (coordinator's ruling under the
        standing rule; Spencer may overrule).
        """
        partners = movement.left_behind(self.state, combatant, "v")
        if not partners:
            self.emit(
                "info",
                f"{combatant.name} is no longer in hand-to-hand and has "
                "nothing to disengage from",
                actor=combatant.name,
            )
            return
        if not self._escape_roll(combatant, made="breaks away", failed="still in"):
            self.emit(
                "info",
                f"{combatant.name} fails to disengage and remains in hand-to-hand",
                actor=combatant.name,
            )
            return
        start = combatant.position
        destination = self._step_destination(combatant, partners[0])
        if destination is not None:
            combatant.position = destination
            combatant.moved_this_turn = True
        combatant.prone = False
        self._leave_hand_to_hand(combatant)
        names = ", ".join(partner.name for partner in partners)
        self.emit(
            "movement" if destination is not None else "status",
            f"{combatant.name} disengages from hand-to-hand with {names}"
            + (
                f" and stands in {destination}"
                if destination is not None
                else " but has nowhere to step to"
            ),
            actor=combatant.name,
            payload={
                "from": list(start),
                "to": list(combatant.position),
                "gait": "shift",
                "hth_ended": [partner.combatant_id for partner in partners],
            },
        )

    def grapple_strike_back(self, combatant: CombatantState) -> None:
        """Strike Back (letter t): "a normal unarmed strike (or dagger, if
        already drawn) against your captor, at the same HTH +4 both of you
        already have." Full crit/fumble resolution via :meth:`resolve_attack`
        — with the drawn dagger and its own Weapon skill when one is in hand
        (#822), bare-handed otherwise."""
        grappler_id = combatant.grappled_by
        if grappler_id is None:
            return
        grappler = self.state.by_id(grappler_id)
        if not grappler.active:
            # A captor who was felled earlier in this phase is holding
            # nobody; end the stale hold rather than swinging at a body (#11).
            self._release_grapples_involving(grappler)
            return
        self.resolve_attack(
            combatant,
            grappler,
            ranged=False,
            weapon_override=(
                None if combatant.weapon.hth_usable else self._unarmed_weapon(combatant)
            ),
            extra_situational=self.profile.grapple.to_hit_bonus,
            verb="strikes back at",
        )

    def hth_strike(self, combatant: CombatantState) -> None:
        """HTH ATTACK (t) outside a grapple (#815).

        hand-to-hand-and-grappling.md: bare-handed fighting happens "only
        ... once you and an enemy share the tight, in-close range HTH
        requires", entered only under one of "Entering Hand-to-Hand"'s
        conditions (#823). The strike takes the HTH +4, no Weapon skill
        bare-handed ("there is no bare-hands entry in the skill catalog"),
        and a dagger's own skill if one is in hand.
        """
        target = None
        if combatant.chosen_target is not None:
            candidate = self.state.by_id(combatant.chosen_target)
            if candidate.active:
                target = candidate
        if target is None:
            self.emit(
                "info",
                f"{combatant.name} has no one in reach to close with",
                actor=combatant.name,
            )
            return
        if combat_math.hth_entry_reason(self.state, combatant, target) is None:
            self.emit(
                "info",
                f"{combatant.name} finds no way in close to {target.name}",
                actor=combatant.name,
                payload={"hth_refused": True, "target": target.combatant_id},
            )
            return
        self._enter_hth(combatant, target)
        self.face_towards(combatant, target.position)
        # Inside hand-to-hand "both combatants get +4": the struck figure's
        # own HTH strike (t) back takes it too, with no entry condition
        # needed. Its menu is the HTH table, so an armed partner strikes back
        # with bare hands or a dagger, never its weapon (tarmar-engine #19).
        self.resolve_attack(
            combatant,
            target,
            ranged=False,
            weapon_override=(
                None if combatant.weapon.hth_usable else self._unarmed_weapon(combatant)
            ),
            extra_situational=self.profile.grapple.to_hit_bonus,
            verb="closes and strikes",
        )

    def draw_dagger(self, combatant: CombatantState) -> None:
        """DRAW DAGGER (u): "Roll 3d6 ≤ DEX to ready dagger" (#822).

        A held figure may not ready a weapon that needs a free hand; the
        dagger is the one it may draw, and whatever it held falls to its hex.
        """
        dagger = next(
            (spare for spare in combatant.spare_weapons if spare.hth_usable), None
        )
        if dagger is None or combatant.weapon.hth_usable:
            self.emit(
                "info",
                f"{combatant.name} has no dagger to draw",
                actor=combatant.name,
            )
            return
        effective_dex = combatant.dexterity - self._action_roll_penalty(combatant)
        record, _sequence = self.roll(
            "3d6",
            purpose="draw dagger",
            actor=combatant.name,
            target_number=effective_dex,
            versus="DEX",
            roll_under=True,
            judge=lambda draw: (
                "fumbles the draw" if draw.total > effective_dex else "draws it"
            ),
        )
        if record.total > effective_dex:
            self.emit(
                "info",
                f"{combatant.name} fails to draw the {dagger.name}",
                actor=combatant.name,
            )
            return
        combatant.spare_weapons.remove(dagger)
        self._weapon_leaves_hand(combatant, lands_at=combatant.position)
        self._take_up(combatant, dagger)
        self.emit(
            "action",
            f"{combatant.name} draws the {dagger.name}",
            actor=combatant.name,
            payload={"letter": "u", "weapon": dagger.name},
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
        if not target.active:
            # A target felled earlier in this phase is out of the fight; end
            # the stale hold rather than squeezing a body every turn (#11).
            self._release_grapples_involving(target)
            return
        # "The target gets no Dodge/Defend bonus against it — they're
        # already held." Option c DODGE covers missiles only, so the page's
        # line can only mean the DEX dodge: Squeeze strips it, with the
        # shield and the Defend stance (#826 — coordinator's ruling under the
        # standing rule; Spencer may overrule). A Shield spell's TN bonus
        # goes with them, as it always has.
        self.resolve_attack(
            combatant,
            target,
            ranged=False,
            weapon_override=self._unarmed_weapon(combatant),
            extra_situational=self.profile.grapple.to_hit_bonus,
            ignore_defender_bonuses=True,
            verb="squeezes",
        )

    def cast_spell(self, combatant: CombatantState) -> None:
        """Phase 5's CAST SPELL: the casting roll, then the spell's effect.

        With the profile's injected :class:`~.magic.MagicRules` two more
        hooks run, in this order, between the casting roll and the effect:
        the casting success tier (:meth:`_casting_success_tier`) and a pushed
        cast's Control Roll (:meth:`_push_control_roll`). Their bonuses add
        to the spell's rolled effect. With injected Channel rules the whole
        mana of the casting, cost and pushed mana together, must lie within
        the caster's Channel for the spell (:meth:`.magic.MagicRules.
        cast_within_channel`), and the cast names the Channel it used. With
        injected Borrowing rules a cast the caster chose to borrow for, and
        cannot afford, empties the pool and adds the shortfall to its debt
        when the cast pays; after the spell has resolved, whatever became of
        it, the caster makes the debt roll (:meth:`_debt_roll`). A Game Master may
        have lifted Channel or Push's own cap for the caster
        (``CombatantState.lifted_magic_bounds``): the cast may then go past
        it, still names the Channel, and lists each lifted bound it went past
        under :data:`~.magic.LIFTED_BOUNDS_KEY`. With injected
        Counterspell rules, a cast the caster can pay for first meets every
        counter declared against it (:meth:`_oppose_cast`), before the
        casting roll: countered, the spell makes no casting roll, its whole
        mana is paid (borrowing included, and the debt roll follows), and the
        caster may press it again on a later turn. With no ``MagicRules``
        none of these runs and a cast logs exactly what it always has.

        Raises:
            ValueError: for a push or a borrowing the profile's rules do not
                allow, a casting past the caster's Channel for the spell or a
                push past the cap where neither is lifted for the caster, or
                a borrowing past the injected debt cap.
        """
        spell = get_spell(combatant.chosen_spell)
        magic = self.profile.magic
        borrowing = None if magic is None else magic.borrowing
        if combatant.chosen_borrow and borrowing is None:
            raise ValueError(
                f"{combatant.name} borrows mana for {spell.name} "
                f"but the {self.profile.name!r} profile has no Borrowing rules"
            )
        push_mana = combatant.chosen_push_mana
        if push_mana and (magic is None or magic.push is None):
            raise ValueError(
                f"{combatant.name} pushes {push_mana} mana into {spell.name} "
                f"but the {self.profile.name!r} profile has no Push rules"
            )
        if push_mana and magic is not None and magic.push is not None:
            if not magic.push.can_push(spell):
                raise ValueError(
                    f"{combatant.name} pushes {spell.name}, which the injected "
                    f"rules give no push effect"
                )
            cap_lifted = bound_lifted(combatant, PUSH_CAP)
            if not magic.push.within_cap(push_mana, cap_lifted=cap_lifted):
                raise ValueError(
                    f"{combatant.name} pushes {push_mana} mana into "
                    f"{spell.name}; the cap is {magic.push.max_push_mana}"
                )
        cost = casting_mana(spell, push_mana)
        # The Channel used and its kind, with Channel rules injected.
        channel_used: tuple[int, str] | None = None
        if magic is not None and magic.channel is not None:
            channel_used = (
                magic.channel.channel_for(combatant, spell),
                magic.channel.kind_for(spell),
            )
        channel = None if channel_used is None else channel_used[0]
        # What the cast would borrow: the shortfall, when the caster chose to.
        shortfall = (
            max(0, cost - combatant.mana)
            if borrowing is not None and combatant.chosen_borrow
            else 0
        )
        borrows = shortfall > 0
        if magic is not None and not magic.cast_within_channel(
            combatant, spell, push_mana, borrowing=borrows
        ):
            bound = magic.channel_bound(combatant, spell, borrowing=borrows)
            borrowing_note = (
                "" if bound == channel else f", which lets them borrow to {bound}"
            )
            raise ValueError(
                f"{combatant.name} casts {spell.name} with {cost} mana; "
                f"their Channel for it is {channel}{borrowing_note}"
            )
        # The bounds a Game Master lifted that this cast goes past
        # (tarmar-studio #37), for the payload.
        lifted_past = (
            ()
            if magic is None
            else magic.lifted_bounds_passed(combatant, spell, push_mana)
        )
        if (
            self.profile.grapple.locks_movement(
                combatant.grappled_by, combatant.grappling
            )
            and combatant.spell_mastery.get(spell.key, 1) < 3
        ):
            # hand-to-hand-and-grappling.md: in a grapple, "Spellcasting is
            # limited to a spell you can cast with no hand gestures and no
            # verbal component — Spell Mastery level 2 and 3 respectively"
            # (#821; both, so level 3 — coordinator's ruling, Spencer may
            # overrule).
            self.emit(
                "info",
                f"{combatant.name} is locked in a grapple and cannot cast "
                f"{spell.name} without gestures and words",
                actor=combatant.name,
            )
            return
        if (
            movement.in_hand_to_hand(self.state, combatant)
            and combatant.weapon.item_id
            and combatant.spell_mastery.get(spell.key, 1)
            < policy.HTH_NO_GESTURE_MASTERY
        ):
            # The HTH table: "CAST SPELL — If hands free or no-gesture
            # spell" (tarmar-engine #19), held here as well as on the menu.
            self.emit(
                "info",
                f"{combatant.name} is in hand-to-hand with the "
                f"{combatant.weapon.name} in hand and cannot cast {spell.name} "
                "without gestures",
                actor=combatant.name,
                payload={"hth_cast_refused": True, "spell": spell.key},
            )
            return
        if combatant.mana < cost and not shortfall:
            pushed_note = f" pushed with {push_mana} more" if push_mana else ""
            self.emit(
                "info",
                f"{combatant.name} lacks the mana for {spell.name}{pushed_note}",
                actor=combatant.name,
            )
            return
        if shortfall and borrowing is not None:
            cap = borrowing.max_debt
            if cap is not None and combatant.debt + shortfall > cap:
                raise ValueError(
                    f"{combatant.name} borrows {shortfall} mana for {spell.name} "
                    f"owing {combatant.debt}; the debt cap is {cap}"
                )
        channel_note = "" if channel is None else f"; Channel {channel}"
        contest = self._oppose_cast(combatant, spell, cost)
        if contest is not None:
            cost_note = self._pay_cast(combatant, spell, push_mana, shortfall)
            defenders = ", ".join(
                self.state.by_id(defender_id).name
                for defender_id in contest.defender_ids
            )
            self.emit(
                "status",
                f"{combatant.name}'s {spell.name} is countered by {defenders}: "
                f"no casting roll ({cost_note}{channel_note}); it may be pressed "
                "again on a later turn",
                actor=combatant.name,
                payload={
                    "spell_countered": True,
                    "spell": spell.key,
                    "defenders": list(contest.defender_ids),
                    "contest_turns": contest.turns,
                    "mana_left": combatant.mana,
                    **self._cast_cost_keys(
                        combatant, push_mana, channel_used, shortfall, lifted_past
                    ),
                },
            )
            if shortfall and borrowing is not None:
                self._debt_roll(combatant, spell, borrowing)
            return
        attribute_name = spell.attribute
        base_attribute = (
            combatant.intelligence if attribute_name == "INT" else combatant.wisdom
        )
        # injury-thresholds-death.md's -1/-2 band (#296) applies to the
        # effective attribute the roll checks against, and so does an
        # off-balance figure's −2 on this action (#811).
        attribute = base_attribute - self._action_roll_penalty(combatant)
        # How a casting roll fails on its natural total is injected
        # (tarmar-engine #46); with none, the attribute alone judges it.
        casting_failure = None if magic is None else magic.casting_failure
        record, cast_sequence = self.roll(
            "3d6",
            purpose="casting",
            actor=combatant.name,
            target_number=attribute,
            versus=attribute_name,
            roll_under=True,
            judge=lambda cast: (
                "success"
                if self._casting_roll_succeeds(cast.total, attribute, casting_failure)
                else "failure"
            ),
        )
        succeeded = self._casting_roll_succeeds(
            record.total, attribute, casting_failure
        )
        # casting-spells.md: "Spells cost mana equal to the spell's level";
        # a failure spends it only on the failing totals the injected casting
        # failure rules name (#816, tarmar-engine #46).
        # A pushed cast pays its pushed mana with the spell's cost
        # (tarmar-engine #27).
        # A borrowing cast borrows only when it pays (tarmar-engine #28).
        pays = succeeded or (
            casting_failure is not None
            and record.total in casting_failure.mana_lost_totals
        )
        borrowed = shortfall if pays else 0
        if pays:
            cost_note = self._pay_cast(combatant, spell, push_mana, borrowed)
        else:
            cost_note = f"no mana spent, {combatant.mana} left"
        payload: dict[str, str | int | bool | list[str]] = {
            "spell": spell.key,
            "success": succeeded,
            "mana_left": combatant.mana,
            "casting_roll": cast_sequence,
            **self._cast_cost_keys(
                combatant, push_mana, channel_used, borrowed, lifted_past
            ),
        }
        self.emit(
            "action",
            f"{combatant.name} casts {spell.name} "
            f"(3d6 ≤ {attribute_name} {attribute}): "
            f"{'success' if succeeded else 'failure'} "
            f"({cost_note}{channel_note})",
            actor=combatant.name,
            payload=payload,
        )
        # A refund the pool keeps is capped at what the pool paid, so it never
        # leaves the pool above where it started; one that pays the debt
        # first may run to the whole cost (tarmar-engine #28).
        refund_cap = cost
        if borrowing is not None and not borrowing.refunds_pay_debt_first:
            refund_cap = cost - borrowed
        if succeeded:
            self._resolve_cast(
                combatant, spell, magic, sum(record.faces), cast_sequence, refund_cap
            )
        elif (
            casting_failure is not None
            and sum(record.faces) == casting_failure.runaway_total
        ):
            self._begin_runaway(combatant, spell, cost)
        if borrowed and borrowing is not None:
            self._debt_roll(combatant, spell, borrowing)

    @staticmethod
    def _anchored_growth(
        combatant: CombatantState, spell: Spell, magic: MagicRules
    ) -> int:
        """The growth a Runaway the caster anchored earned, added to its next
        cast of that spell's rolled effect and consumed by it
        (``anchored_turns_running``). 0, and nothing consumed, for a spell
        with no rolled effect (a continuing spell keeps its entry until it
        leaves ``active_spells``) or with no Runaway rules injected."""
        if magic.runaway is None or spell_effect_kind(spell) is None:
            return 0
        turns = combatant.anchored_turns_running.pop(spell.key, None)
        if turns is None:
            return 0
        return magic.runaway.effect_bonus(turns)

    @staticmethod
    def _casting_roll_succeeds(
        natural_total: int,
        attribute: int,
        casting_failure: CastingFailureRules | None,
    ) -> bool:
        """Does a casting roll succeed? At or under the attribute, and under
        the injected fumble floor where one is injected (tarmar-engine #46).

        Args:
            natural_total: The casting roll's total.
            attribute: The effective attribute it is rolled under.
            casting_failure: The injected casting failure rules, or ``None``:
                the attribute alone judges the roll.
        """
        return natural_total <= casting_success_ceiling(attribute, casting_failure)

    def _pay_cast(
        self, caster: CombatantState, spell: Spell, push_mana: int, borrowed: int
    ) -> str:
        """Pay a casting's whole mana, ``borrowed`` of it added to the debt
        (tarmar-engine #28); the payment as the log prints it."""
        cost = casting_mana(spell, push_mana)
        caster.mana -= cost - borrowed
        caster.debt += borrowed
        paid_text = f"{spell.level} + {push_mana} pushed" if push_mana else str(cost)
        cost_note = f"{paid_text} mana, {caster.mana} left"
        if borrowed:
            cost_note += f", borrows {borrowed}, debt {caster.debt}"
        return cost_note

    def _cast_cost_keys(
        self,
        caster: CombatantState,
        push_mana: int,
        channel_used: tuple[int, str] | None,
        borrowed: int,
        lifted_past: tuple[str, ...],
    ) -> dict[str, str | int | list[str]]:
        """The payload keys a cast's mana adds, each only with the rules that
        bring it injected, so a profile without them logs the payload it
        always has: ``push_mana`` with Push, ``channel`` and ``channel_kind``
        with Channel, ``borrowed`` and ``debt`` with Borrowing, and
        :data:`~.magic.LIFTED_BOUNDS_KEY` only when the cast went past a
        bound a Game Master lifted for the caster (``lifted_past``)."""
        magic = self.profile.magic
        keys: dict[str, str | int | list[str]] = {}
        if magic is not None and magic.push is not None:
            keys["push_mana"] = push_mana
        if channel_used is not None:
            keys["channel"], keys["channel_kind"] = channel_used
        if magic is not None and magic.borrowing is not None:
            keys["borrowed"] = borrowed
            keys["debt"] = caster.debt
        if lifted_past:
            keys[LIFTED_BOUNDS_KEY] = list(lifted_past)
        return keys

    def _resolve_cast(
        self,
        combatant: CombatantState,
        spell: Spell,
        magic: MagicRules | None,
        natural_total: int,
        cast_sequence: int,
        refund_cap: int,
    ) -> None:
        """A successful cast's tier, Control Roll, aim and effect, in order.

        Args:
            combatant: The caster.
            spell: The spell cast.
            magic: The profile's injected magic rules, or ``None``.
            natural_total: The casting roll's natural total.
            cast_sequence: The casting roll's event sequence number.
            refund_cap: The most a tier's refund may hand back.
        """
        push_mana = combatant.chosen_push_mana
        effect_bonus = 0
        bonus_sources: list[str] = []
        if magic is not None:
            tier = self._casting_success_tier(
                combatant, spell, magic, natural_total, refund_cap
            )
            # Only a spell with a rolled effect can take a tier's bonus.
            if (
                tier is not None
                and tier.effect_bonus
                and spell_effect_kind(spell) is not None
            ):
                effect_bonus += tier.effect_bonus
                bonus_sources.append(f"{tier.effect_bonus:+d} {tier.label}")
        if push_mana and magic is not None and magic.push is not None:
            takes_effect, push_bonus = self._push_control_roll(
                combatant, spell, magic.push, push_mana
            )
            if not takes_effect:
                return
            if push_bonus:
                effect_bonus += push_bonus
                bonus_sources.append(f"{push_bonus:+d} pushed mana")
        # An anchored Runaway's growth is used up only once the spell is sure
        # to take effect, past any Control Roll.
        growth = 0 if magic is None else self._anchored_growth(combatant, spell, magic)
        if growth:
            effect_bonus += growth
            bonus_sources.append(f"{growth:+d} anchored Runaway")
        if effect_bonus:
            self.emit(
                "status",
                f"{combatant.name}'s {spell.name} gains {effect_bonus:+d} "
                f"to its effect ({', '.join(bonus_sources)})",
                actor=combatant.name,
                payload={"spell": spell.key, "effect_bonus": effect_bonus},
            )
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
                max(0, heal_record.total + effect_bonus),
                combatant.max_fatigue - combatant.fatigue,
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
            # re-mapped onto the caster's effective DEX for this check, and
            # so is the injury-thresholds-death.md -1/-2 band (#296).
            effective_dex = combatant.dexterity - self._action_roll_penalty(combatant)
            if target.dodging:
                effective_dex -= DODGE_DEX_CHECK_PENALTY
            aim_record, _sequence = self.roll(
                "3d6",
                purpose="spell aim",
                actor=combatant.name,
                target_number=effective_dex,
                versus="DEX",
                roll_under=True,
                judge=lambda aim: (
                    "misses" if aim.total > effective_dex else "on target"
                ),
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
        raw = max(0, damage_record.total + effect_bonus)
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

    def _casting_success_tier(
        self,
        combatant: CombatantState,
        spell: Spell,
        magic: MagicRules,
        natural_total: int,
        refund_cap: int,
    ) -> CastingSuccessTier | None:
        """Narrate a successful casting roll's tier and pay its refund
        (tarmar-engine #26).

        The tier comes from the injected table by the roll's natural total;
        its effect bonus is returned to :meth:`cast_spell` to add to the
        spell's rolled effect, and the payload reports it only for a spell
        with one. The refund is paid here, before any Control Roll, so a
        Runaway does not take it back, and is capped at ``refund_cap``: what
        the cast cost, pushed mana included, or, where Borrowing's refund
        refills the pool and leaves the debt, only what the pool paid. With
        injected Borrowing rules that say so, it pays the debt before it
        refills the pool (:meth:`_regain_mana`).
        """
        tier = magic.success_tier(natural_total)
        if tier is None:
            return None
        refund = min(tier.mana_refund, refund_cap)
        applied_bonus = tier.effect_bonus if spell_effect_kind(spell) is not None else 0
        debt_paid = self._regain_mana(combatant, refund)
        refund_note = ""
        if refund:
            debt_note = (
                f", {debt_paid} of it to the debt, {combatant.debt} owed"
                if debt_paid
                else ""
            )
            refund_note = f"; {refund} mana back{debt_note}, {combatant.mana} left"
        tier_payload: dict[str, str | int] = {
            "spell": spell.key,
            "success_tier": tier.key,
            "natural_total": natural_total,
            "effect_bonus": applied_bonus,
            "mana_refund": refund,
            "mana_left": combatant.mana,
        }
        # Only with Borrowing rules injected, as the cast's own keys.
        if magic.borrowing is not None:
            tier_payload["debt_paid"] = debt_paid
            tier_payload["debt"] = combatant.debt
        self.emit(
            "status",
            f"{combatant.name}'s casting of {spell.name} is a {tier.label} "
            f"(natural {natural_total}){refund_note}",
            actor=combatant.name,
            payload=tier_payload,
        )
        return tier

    def _regain_mana(self, combatant: CombatantState, amount: int) -> int:
        """Hand mana back to a caster; returns how much of it paid the debt.

        With injected Borrowing rules whose ``refunds_pay_debt_first`` is
        set, the debt takes the mana first and the pool the rest; otherwise
        the pool takes it all and the debt stands.
        """
        magic = self.profile.magic
        borrowing = None if magic is None else magic.borrowing
        debt_paid = 0
        if borrowing is not None and borrowing.refunds_pay_debt_first:
            debt_paid = min(amount, combatant.debt)
            combatant.debt -= debt_paid
        combatant.mana += amount - debt_paid
        return debt_paid

    def _roll_under(
        self, dice: str, *, purpose: str, target: int, automatic_totals: frozenset[int]
    ) -> RollUnder:
        """Throw a roll-under magic roll without logging it.

        The caller logs it with :meth:`emit_roll`, once it knows the outcome
        to name.
        """
        record = self.roll_unlogged(dice, purpose=purpose, target_number=target)
        natural_total = sum(record.faces)
        return RollUnder(
            record=record,
            natural_total=natural_total,
            margin=record.total - target,
            automatic=natural_total in automatic_totals,
        )

    def _push_control_roll(
        self,
        combatant: CombatantState,
        spell: Spell,
        push: PushRules,
        push_mana: int,
    ) -> tuple[bool, int]:
        """Roll a pushed spell's Control Roll and narrate its outcome
        (tarmar-engine #27).

        Roll-under against the injected attribute, less the injected penalty
        per mana invested, and the same injury and off-balance penalties the
        casting roll took. A natural total on the injected automatic-Runaway
        list is a Runaway whatever the target; otherwise a failure's margin
        picks its band. A Runaway is flagged in the payload and, with injected
        Runaway rules, begins (:meth:`_begin_runaway`).

        Returns:
            Whether the spell takes effect, and the push's effect bonus.
        """
        invested = push.mana_invested(spell, push_mana)
        target = (
            attribute_score(combatant, push.control_attribute)
            - push.penalty_per_mana * invested
            - self._action_roll_penalty(combatant)
        )
        control = self._roll_under(
            push.control_dice,
            purpose="control",
            target=target,
            automatic_totals=push.automatic_runaway_totals,
        )
        margin = control.margin
        if control.automatic:
            band = push.runaway_band()
        elif margin > 0:
            band = push.band_for_margin(margin)
        else:
            band = None
        held = band is None
        runaway = band is not None and band.is_runaway
        control_sequence = self.emit_roll(
            control.record,
            actor=combatant.name,
            versus=push.control_attribute,
            roll_under=True,
            outcome="held" if band is None else band.label,
        )
        takes_effect = band is None or band.spell_takes_effect
        bonus_applies = band is None or band.push_bonus_applies
        push_bonus = push.effect_bonus(spell, push_mana) if bonus_applies else 0
        if runaway:
            message = f"{spell.name} runs away from {combatant.name}"
        elif band is None:
            message = f"{combatant.name} holds the pushed {spell.name}"
        else:
            message = f"{combatant.name}'s pushed {spell.name}: {band.label}"
        if not takes_effect:
            message += "; the spell does not take effect"
        self.emit(
            "status",
            message,
            actor=combatant.name,
            payload={
                "spell": spell.key,
                "push_mana": push_mana,
                "mana_invested": invested,
                "held": held,
                "control_band": None if band is None else band.key,
                "control_margin": margin,
                "control_roll": control_sequence,
                "takes_effect": takes_effect,
                "push_bonus": push_bonus,
                "runaway": runaway,
            },
        )
        if runaway:
            self._begin_runaway(combatant, spell, casting_mana(spell, push_mana))
        return takes_effect, push_bonus

    def _debt_roll(
        self, combatant: CombatantState, spell: Spell, borrowing: BorrowingRules
    ) -> None:
        """Roll a borrowing cast's debt roll and narrate its outcome
        (tarmar-engine #28).

        Made after the spell has resolved, whatever became of it: a failed
        casting roll that paid, a Control Roll that stopped the spell and an
        aim that missed all still owe. Roll-under against the injected
        attributes summed, less the injected penalty per point of debt owed
        now (any refund already applied) and the casting roll's injury band
        and off-balance penalty, as the Control Roll takes them. It is made
        even when a refund has paid the whole debt back: the caster borrowed
        on this cast. A natural total on the injected
        automatic list fails whatever the target. A failure is narrated by
        its injected label, in a second event, and flagged a Runaway when the
        rules make it one, which with injected Runaway rules begins
        (:meth:`_begin_runaway`); the spell has already resolved and stands.
        """
        target = borrowing.debt_target(
            combatant, combatant.debt
        ) - self._action_roll_penalty(combatant)
        debt = self._roll_under(
            borrowing.debt_dice,
            purpose="debt",
            target=target,
            automatic_totals=borrowing.automatic_failure_totals,
        )
        held = not debt.automatic and debt.margin <= 0
        debt_sequence = self.emit_roll(
            debt.record,
            actor=combatant.name,
            versus=borrowing.versus,
            roll_under=True,
            outcome="held" if held else "failed",
        )
        verdict = "holds" if held else "fails"
        self.emit(
            "status",
            f"{combatant.name} {verdict} the debt roll for {spell.name} "
            f"({combatant.debt} owed)",
            actor=combatant.name,
            payload={
                "spell": spell.key,
                "debt_roll": debt_sequence,
                "dice": borrowing.debt_dice,
                "natural": debt.natural_total,
                "target": target,
                "debt": combatant.debt,
                "held": held,
                "automatic": debt.automatic,
            },
        )
        if held:
            return
        failure = borrowing.failure
        self.emit(
            "status",
            f"{combatant.name}'s failed debt for {spell.name}: {failure.label}",
            actor=combatant.name,
            payload={
                "spell": spell.key,
                "debt_failure": failure.key,
                "label": failure.label,
                "runaway": failure.is_runaway,
            },
        )
        if failure.is_runaway:
            self._begin_runaway(
                combatant,
                spell,
                casting_mana(spell, combatant.chosen_push_mana),
            )

    # ----------------------------------------------------------------- runaway
    def _runaway_rules(self, combatant: CombatantState) -> RunawayRules:
        """The profile's injected Runaway rules.

        Raises:
            ValueError: when the profile has none, for an action that needs
                them.
        """
        magic = self.profile.magic
        if magic is None or magic.runaway is None:
            raise ValueError(
                f"{combatant.name} anchors a Runaway but the "
                f"{self.profile.name!r} profile has no Runaway rules"
            )
        return magic.runaway

    def _begin_runaway(
        self,
        combatant: CombatantState,
        spell: Spell,
        original_cost: int,
        caught_from: int | None = None,
    ) -> None:
        """Start a Runaway on a combatant, with injected Runaway rules.

        Without them nothing happens: the payload flag that called it is the
        whole of a Runaway, as before tarmar-engine #30. A combatant already
        feeding a Runaway keeps that one (house reading
        ``one_runaway_at_a_time``), and the log says so.

        Args:
            combatant: The combatant the spell runs away from.
            spell: The spell.
            original_cost: The mana its casting cost, pushed mana included.
            caught_from: The combatant a failed helper's anchor caught it
                from, or ``None``.
        """
        magic = self.profile.magic
        if magic is None or magic.runaway is None:
            return
        if combatant.runaway is not None:
            kept = get_spell(combatant.runaway.spell_key)
            self.emit(
                "info",
                f"{combatant.name} already feeds a runaway {kept.name}; "
                f"{spell.name} starts no second Runaway",
                actor=combatant.name,
                payload={"spell": spell.key, "runaway_kept": kept.key},
            )
            return
        combatant.runaway = RunawayState(
            spell_key=spell.key,
            original_cost=original_cost,
            turns_running=0,
            caught_from=caught_from,
        )
        payload: dict[str, str | int | bool] = {
            "runaway_begins": True,
            "spell": spell.key,
            "original_cost": original_cost,
        }
        caught_note = ""
        if caught_from is not None:
            payload["caught_from"] = caught_from
            caught_note = f", caught from {self.state.by_id(caught_from).name}"
        self.emit(
            "status",
            f"{spell.name} runs away and will feed on {combatant.name} each turn "
            f"({original_cost} mana cast{caught_note})",
            actor=combatant.name,
            payload=payload,
        )

    def _feed_runaway(self, caster: CombatantState, rules: RunawayRules) -> None:
        """One later turn of a caster's Runaway, in phase 2 (house reading
        ``runaway_drain_in_renew_phase``).

        The drain comes off the caster's mana first. When the mana is gone a
        conscious caster falls unconscious by the engine's one path
        (:meth:`_fall_unconscious`), and the rest of the drain, and every
        later turn's, comes off the injected overflow pool. Past the injected
        death line the caster dies (:meth:`_die`) and the Runaway ends with
        them. The spell's effect is not applied again: the turns it has run
        and the injected growth per turn are carried in the payload for the
        caller to narrate (house reading
        ``runaway_effect_is_narrated_not_reapplied``).
        """
        running = caster.runaway
        if running is None:
            return
        spell = get_spell(running.spell_key)
        running.turns_running += 1
        drain = rules.drain(running.original_cost)
        drained_mana = min(drain, max(0, caster.mana))
        caster.mana -= drained_mana
        drained_pool = drain - drained_mana
        pool = rules.overflow_pool
        setattr(caster, pool, getattr(caster, pool) - drained_pool)
        pool_left = getattr(caster, pool)
        falls = caster.conscious and caster.mana <= 0
        drained = f"{drained_mana} mana ({caster.mana} left)"
        if drained_pool:
            drained += f" and {drained_pool} {pool} ({pool_left} left)"
        message = (
            f"{spell.name} feeds on {caster.name}, turn {running.turns_running}: "
            f"it drains {drained}"
        )
        if falls:
            message += f"; {caster.name} falls unconscious"
        drain_sequence = self.emit(
            "status",
            message,
            actor=caster.name,
            payload={
                "runaway_drain": True,
                "spell": spell.key,
                "turn": running.turns_running,
                "drained_mana": drained_mana,
                "drained_pool": drained_pool,
                "pool": pool,
                "pool_left": pool_left,
                "mana": caster.mana,
                "unconscious": falls or not caster.conscious,
                "effect_bonus_per_turn": rules.effect_bonus_per_turn,
            },
        )
        if falls:
            self._fall_unconscious(caster, [])
        death_line = rules.death_line(caster)
        if not drained_pool or death_line is None:
            return
        if not rules.past_death_line(caster):
            return
        self.emit(
            "status",
            f"{spell.name} has drained {caster.name}'s {pool} to {pool_left}, "
            f"at or past the line at {death_line}; {caster.name} dies",
            actor=caster.name,
            payload={
                "runaway_death": True,
                "spell": spell.key,
                "pool": pool,
                "pool_left": pool_left,
                "death_line": death_line,
            },
        )
        caster.fatal_chain.append(drain_sequence)
        self._die(caster)

    def anchor_runaway(self, anchorer: CombatantState) -> None:
        """Phase 5's anchor: take a Runaway back under control.

        A combatant anchoring its own Runaway rolls the injected self-anchor
        roll; one anchoring another's pays the injected helper cost first,
        whatever comes of it, and rolls the helper roll. Either roll is
        roll-under against the injected attribute less the casting roll's
        injury band and off-balance penalty (house reading
        ``anchor_takes_casting_penalties``). Held: the Runaway ends; a
        continuing spell becomes an ordinary active spell of the anchorer, to
        renew or let lapse, and any other spell, its effect already had,
        simply stops (house reading ``anchored_spell_becomes_the_anchorers``).
        Either way the turns it ran are recorded for its growth
        (:meth:`_anchored_growth`).
        Failed: the Runaway runs on, and a failed helper is caught by one of
        its own when the rules say so.

        Raises:
            ValueError: with no Runaway rules injected, or no anchor chosen.
        """
        rules = self._runaway_rules(anchorer)
        if anchorer.chosen_anchor is None:
            raise ValueError(f"{anchorer.name} anchors no one's Runaway")
        owner = self.state.by_id(anchorer.chosen_anchor)
        running = owner.runaway
        if running is None or not owner.alive:
            self.emit(
                "info",
                f"{anchorer.name} finds no Runaway on {owner.name} to anchor",
                actor=anchorer.name,
                payload={"anchor_target": owner.combatant_id},
            )
            return
        spell = get_spell(running.spell_key)
        helping = owner is not anchorer
        rule = rules.helper_anchor if helping else rules.self_anchor
        cost = rules.helper_cost(running.original_cost) if helping else 0
        if anchorer.mana < cost:
            self.emit(
                "info",
                f"{anchorer.name} lacks the {cost} mana to anchor "
                f"{owner.name}'s {spell.name}",
                actor=anchorer.name,
                payload={"anchor_target": owner.combatant_id},
            )
            return
        anchorer.mana -= cost
        target = attribute_score(anchorer, rule.attribute) - self._action_roll_penalty(
            anchorer
        )
        anchor = self._roll_under(
            rule.dice, purpose="anchor", target=target, automatic_totals=frozenset()
        )
        held = anchor.margin <= 0
        anchor_sequence = self.emit_roll(
            anchor.record,
            actor=anchorer.name,
            versus=rule.attribute,
            roll_under=True,
            outcome="held" if held else "failed",
        )
        caught = not held and helping and rules.helper_failure_catches_helper
        payload: dict[str, str | int | bool | None] = {
            "spell": spell.key,
            "anchor_target": owner.combatant_id,
            "helper": anchorer.combatant_id if helping else None,
            "held": held,
            "anchor_roll": anchor_sequence,
            "target": target,
            "paid": cost,
            "mana_left": anchorer.mana,
            "caught": caught,
        }
        whose = f"{owner.name}'s {spell.name}" if helping else spell.name
        paid_note = f" ({cost} mana paid, {anchorer.mana} left)" if helping else ""
        if held:
            owner.runaway = None
            if spell.key in owner.active_spells:
                self._end_active_spell(owner, spell.key)
            if spell.continuing and spell.key not in anchorer.active_spells:
                anchorer.active_spells.append(spell.key)
            anchorer.anchored_turns_running[spell.key] = running.turns_running
            payload["runaway_turns_running"] = running.turns_running
            control = (
                f"it is {anchorer.name}'s to renew or let lapse"
                if spell.continuing
                else "it stops"
            )
            self.emit(
                "status",
                f"{anchorer.name} anchors {whose} after {running.turns_running} "
                f"turn(s){paid_note}; {control}",
                actor=anchorer.name,
                payload=payload,
            )
            return
        caught_note = f"; {anchorer.name} is caught by it" if caught else ""
        self.emit(
            "status",
            f"{anchorer.name} fails to anchor {whose}{paid_note}; it runs on"
            f"{caught_note}",
            actor=anchorer.name,
            payload=payload,
        )
        if caught:
            self._begin_runaway(
                anchorer, spell, running.original_cost, caught_from=owner.combatant_id
            )

    # ------------------------------------------------------------ counterspell
    @staticmethod
    def _counter_choice(candidate: policy.Candidate) -> tuple[int, int] | None:
        """The ``(caster id, mana)`` a chosen counter declares, or ``None``.

        Raises:
            ValueError: for a counter that names its caster by no id.
        """
        if candidate.counter_mana is None:
            return None
        if not isinstance(candidate.target_id, int):
            raise ValueError(f"{candidate.name} names no caster to counter")
        return candidate.target_id, candidate.counter_mana

    @staticmethod
    def _escalates(caster: CombatantState) -> bool:
        """Does the caster's chosen action press its countered spell again:
        a cast of the contest's spell at the contest's target?"""
        contest = caster.contest
        return (
            contest is not None
            and actions.base_option(caster.chosen_letter) in actions.CAST_OPTIONS
            and caster.chosen_spell == contest.spell_key
            and caster.chosen_target == contest.target_id
        )

    def _end_contest(self, caster: CombatantState, reason: str) -> None:
        """End the caster's contest and say why (a key of
        :data:`CONTEST_END_REASONS`)."""
        contest = caster.contest
        if contest is None:
            return
        caster.contest = None
        spell = get_spell(contest.spell_key)
        self.emit(
            "status",
            f"{caster.name} {CONTEST_END_REASONS[reason]}: the contest over "
            f"{spell.name} ends after {contest.turns} counter(s)",
            actor=caster.name,
            payload={
                "contest_ended": True,
                "spell": spell.key,
                "reason": reason,
                "turns": contest.turns,
            },
        )

    def _counterspell_rules(self, combatant: CombatantState) -> CounterspellRules:
        """The profile's injected Counterspell rules.

        Raises:
            ValueError: when the profile has none, for a counter chosen.
        """
        magic = self.profile.magic
        if magic is None or magic.counterspell is None:
            raise ValueError(
                f"{combatant.name} counters a spell but the "
                f"{self.profile.name!r} profile has no Counterspell rules"
            )
        return magic.counterspell

    def declare_counter(self, defender: CombatantState) -> None:
        """Phase 5's counter: pay the commitment and stand against the caster.

        Taken at the defender's own action, or at its caster's cast when the
        caster acts first (:meth:`_oppose_cast`), whichever comes first; the
        other is then a logged no-op. A defender short of the mana declares
        nothing. A declared counter meets the caster's next cast this turn,
        or lapses at the end of the Actions phase with its mana spent
        (:meth:`_lapse_counters`).

        A Game Master may have lifted the commitment bounds for the defender
        (:data:`~.magic.COUNTER_BOUNDS`): any commitment from
        :data:`~.magic.LIFTED_COUNTER_MINIMUM` up is then allowed, and one
        outside the injected bounds is flagged in the payload under
        :data:`~.magic.LIFTED_BOUNDS_KEY`.

        Raises:
            ValueError: with no Counterspell rules injected, no counter
                chosen, or a commitment outside the injected bounds (with
                them lifted, below the lifted minimum).
        """
        rules = self._counterspell_rules(defender)
        if defender.chosen_counter is None:
            raise ValueError(f"{defender.name} counters no one's spell")
        caster_id, mana = defender.chosen_counter
        caster = self.state.by_id(caster_id)
        if defender.combatant_id in self._counters_taken:
            self.emit(
                "info",
                f"{defender.name}'s counter was declared at {caster.name}'s cast",
                actor=defender.name,
                payload={"counter_taken_at_cast": True, "caster": caster_id},
            )
            return
        self._counters_taken.add(defender.combatant_id)
        lifted = bound_lifted(defender, COUNTER_BOUNDS)
        if not rules.allows(mana, bounds_lifted=lifted):
            least, most = rules.bounds(bounds_lifted=lifted)
            raise ValueError(
                f"{defender.name} commits {mana} mana to a counter; the rules "
                f"allow {least} to {'the purse' if most is None else most}"
            )
        if defender.mana < mana:
            self.emit(
                "info",
                f"{defender.name} lacks the {mana} mana to counter {caster.name}",
                actor=defender.name,
                payload={"caster": caster_id},
            )
            return
        defender.mana -= mana
        defender.counter = CounterState(
            caster_id=caster_id, mana_committed=mana, declared_turn=self.state.turn
        )
        payload: dict[str, bool | int | list[str]] = {
            "counter_declared": True,
            "caster": caster_id,
            "mana": mana,
            "mana_left": defender.mana,
        }
        passed = rules.lifted_bounds_passed(mana)
        if passed:
            payload[LIFTED_BOUNDS_KEY] = list(passed)
        self.emit(
            "status",
            f"{defender.name} commits {mana} mana to counter {caster.name}'s "
            f"spell ({defender.mana} left)",
            actor=defender.name,
            payload=payload,
        )

    def _oppose_cast(
        self, caster: CombatantState, spell: Spell, cost: int
    ) -> ContestState | None:
        """Meet a cast with every counter against its caster (tarmar-engine
        #31); the contest a countered cast leaves standing, or ``None``.

        A defender that chose a counter against this caster and has not
        acted yet declares it now (house reading
        ``counter_resolves_at_the_cast``). With no counter declared the cast
        goes on, and a contest it was pressing ends. Otherwise each side rolls
        the injected dice plus its score in the injected attribute plus the
        bonus its mana buys: the caster's whole casting cost, pushed mana
        included, less the casting roll's injury band and off-balance
        penalty; each defender's commitment, less its own injury band. The
        caster must beat the highest defender, ties by the injected rule.
        Every declared counter is spent either way. Through, the contest
        ends; countered, it stands on the caster for a later turn.
        """
        magic = self.profile.magic
        rules = None if magic is None else magic.counterspell
        if rules is None:
            return None
        for defender in self.state.active_combatants():
            choice = defender.chosen_counter
            if (
                choice is not None
                and choice[0] == caster.combatant_id
                and defender.combatant_id not in self._counters_taken
            ):
                self.declare_counter(defender)
        opposing = [
            (defender, counter)
            for defender in sorted(
                self.state.active_combatants(),
                key=lambda combatant: combatant.combatant_id,
            )
            if (counter := defender.counter) is not None
            and counter.caster_id == caster.combatant_id
        ]
        if not opposing:
            self._end_contest(caster, "unopposed")
            return None
        attribute = rules.opposed_attribute
        caster_bonus = rules.bonus_for(cost)
        caster_record = self.roll_unlogged(
            rules.opposed_dice,
            purpose="counter",
            modifier=attribute_score(caster, attribute)
            + caster_bonus
            - self._action_roll_penalty(caster),
        )
        defender_records = [
            (
                defender,
                counter,
                self.roll_unlogged(
                    rules.opposed_dice,
                    purpose="counter",
                    modifier=attribute_score(defender, attribute)
                    + rules.bonus_for(counter.mana_committed)
                    - self.profile.reactions.injury_penalty(defender),
                ),
            )
            for defender, counter in opposing
        ]
        highest = max(record.total for _, _, record in defender_records)
        through = rules.caster_gets_through(caster_record.total, highest)
        caster_sequence = self.emit_roll(
            caster_record,
            actor=caster.name,
            versus=attribute,
            outcome="gets through" if through else "countered",
        )
        defender_totals: list[dict[str, int]] = []
        for defender, counter, record in defender_records:
            sequence = self.emit_roll(
                record,
                actor=defender.name,
                versus=attribute,
                outcome="overcome" if through else "counters",
            )
            defender_totals.append(
                {
                    "defender": defender.combatant_id,
                    "total": record.total,
                    "mana": counter.mana_committed,
                    "bonus": rules.bonus_for(counter.mana_committed),
                    "roll": sequence,
                }
            )
            defender.counter = None
        defender_ids = [defender.combatant_id for defender, _ in opposing]
        self.emit(
            "status",
            f"{caster.name}'s {spell.name} "
            f"{'gets through' if through else 'is countered'}: "
            f"{caster_record.total} against "
            f"{', '.join(str(entry['total']) for entry in defender_totals)}",
            actor=caster.name,
            payload={
                "counter_result": True,
                "spell": spell.key,
                "caster_total": caster_record.total,
                "caster_bonus": caster_bonus,
                "caster_roll": caster_sequence,
                "defender_totals": defender_totals,
                "countered": not through,
            },
        )
        if through:
            self._end_contest(caster, "through")
            return None
        turns = 1 if caster.contest is None else caster.contest.turns + 1
        caster.contest = ContestState(
            spell_key=spell.key,
            target_id=caster.chosen_target,
            defender_ids=defender_ids,
            turns=turns,
        )
        return caster.contest

    def _lapse_counters(self) -> None:
        """At the end of the Actions phase, every counter no cast met lapses,
        its mana spent (house reading ``committed_mana_is_spent``)."""
        for defender in self.state.combatants:
            counter = defender.counter
            if counter is None:
                continue
            defender.counter = None
            caster = self.state.by_id(counter.caster_id)
            self.emit(
                "status",
                f"{defender.name}'s counter against {caster.name} meets no "
                f"spell; its {counter.mana_committed} mana is spent",
                actor=defender.name,
                payload={
                    "counter_lapsed": True,
                    "caster": counter.caster_id,
                    "mana": counter.mana_committed,
                },
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
                f"{defender.name}'s armour stops the blow "
                f"({raw} rolled, all of it stopped)",
                actor=attacker.name,
                payload={
                    "target": defender.combatant_id,
                    "net": 0,
                    "raw": raw,
                    "stopped": raw,
                },
            )
            return
        if body_only:
            defender.body -= net
        else:
            defender.fatigue -= net
            if reaches_body:
                defender.body -= net
        defender.took_damage_this_turn = True
        defender.hits_this_turn += net
        attacker.dealt_damage_this_turn = True
        defender.fatal_chain = list(chain)
        pools = f"fatigue {defender.fatigue}/{defender.max_fatigue}"
        if reaches_body or body_only:
            pools += f", body {defender.body}/{defender.max_body}"
        # The subtraction is shown, not just its answer: "takes 5 damage"
        # beside a damage roll of 7 previously left the missing 2 unexplained
        # (#301).
        stopped = raw - net
        self.emit(
            "damage",
            f"{defender.name} takes {net} damage "
            f"({raw} rolled less {stopped} stopped by armour; {pools})",
            actor=attacker.name,
            payload={
                "target": defender.combatant_id,
                "net": net,
                "raw": raw,
                "stopped": stopped,
                "fatigue": defender.fatigue,
                "body": defender.body,
                "chain": list(chain),
            },
        )
        self.check_unconsciousness(defender, chain)

    def check_unconsciousness(
        self, combatant: CombatantState, chain: list[int]
    ) -> None:
        """Pool at or below 0 → unconscious — the reactions seam's verdict
        (injury-thresholds semantics under the Tarmar profile)."""
        if not combatant.conscious:
            return
        if not self.profile.reactions.unconscious(combatant):
            return
        self._fall_unconscious(combatant, chain)

    def _fall_unconscious(self, combatant: CombatantState, chain: list[int]) -> None:
        """The engine's one fall into unconsciousness: the figure drops,
        the event is logged, and its grapples end."""
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
        # A figure that has stopped fighting neither holds nor is held (#11).
        self._release_grapples_involving(combatant)


def run_turn(
    state: BattleState,
    roller,
    sink: EventSink,
    choose_option,
    profile: RulesProfile | None = None,
    choose_retreat: RetreatChooser | None = None,
) -> None:
    """Run exactly one full turn of the battle. Mutates ``state``.

    ``profile`` selects the rules profile; omitted, the Tarmar profile runs.
    ``choose_retreat`` picks each forced retreat's hex and whether the pusher
    advances (#779); omitted, the AI's :func:`policy.choose_retreat` does.
    """
    TurnRunner(state, roller, sink, profile=profile, choose_retreat=choose_retreat).run(
        choose_option
    )
