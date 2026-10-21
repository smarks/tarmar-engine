"""Utility-based AI: score the legal action options, pick the best, show work.

The policy is deliberately shallow — no search, no learning, one turn of
lookahead. For the actor's situation it enumerates the legal letters from
``action-options.md`` (via ``tarmar_engine.actions``), scores each with the
same arithmetic the engine resolves attacks with
(``tarmar_engine.combat_math`` + ``tarmar_rules.hit_probability``), and
returns every candidate with its score and a one-line rationale so the
decision event can log exactly what was considered.

Pure and dice-free: scoring uses probabilities and expectations only, so the
policy never consumes the battle's seeded Roller and cannot perturb replays.

**Grapple AI (issue #231) is deliberately dumb.** Attempting a grapple
(option "o") is always scored :data:`GRAPPLE_ATTEMPT_SCORE` — 0 — so the AI
never initiates one; full grapple tactics (weighing a hold against straight
damage, judging when to Release, etc.) are out of scope. Once a grapple is
live, :func:`_grappled_decision`/:func:`_grappler_decision` replace the
normal scoring entirely with a fixed default: a held figure always attempts
Struggle Free, and a grappler always Squeezes. Neither weighs Hold
Still/Strike Back or Maintain/Release against anything — they are only
listed as zero-score candidates so the decision log still shows what else
was on the table. A held figure's list also carries DRAW DAGGER when it has
one to draw, and a cast for each spell known at Spell Mastery 3 — both at
zero, for a player to choose. Beasts never attempt a grapple at all (no
hands to hold with); this is consistent with the rest of their melee-only
subset.

**The menu is the legal menu.** A game that lets a person choose (tarmar-
studio's manual control) offers exactly these candidates, so every option
the rules allow appears here even when the AI would never take it: Sprint,
DROP, the yielded variants of the movement options, and a weapon option
with nothing to rearm score 0 and sit after the options the AI scores.

**Inside hand-to-hand the menu is the HTH table** (tarmar-engine #19): t,
u, v and the casts its condition allows, from :func:`actions.legal_actions`
with ``in_hth``. The AI scores t and v and the casts; DRAW DAGGER is a
player's option, as it is in a grapple.

**A candidate may carry a hex** (``Candidate.destination``, tarmar-engine
#20; :mod:`.movement`). The AI names none: its engaged options stand still,
its DROP drops in place, its MOVE, CHARGE and DODGE close on their target,
and its DISENGAGE (n, v) and Struggle Free leave the step to the engine when
the figure acts (straight back, then either flank), the forecast printing
the step it expects. A player may give any option
:func:`movement.legal_destinations` allows a hex, and the engine checks it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import TypeVar

from . import actions, combat_math, hexes, movement, weapons
from . import resolution as combat
from .dice import parse_dice_expression
from .magic import (
    CHANNEL,
    COUNTER_BOUNDS,
    LIFTED_BOUNDS_KEY,
    PUSH_CAP,
    BorrowingRules,
    ChannelUnclassifiedSpell,
    CounterspellRules,
    MagicRules,
    PushRules,
    RollUnderRule,
    RunawayRules,
    attribute_score,
    bound_lifted,
    casting_mana,
)
from .reactions import TarmarReactions
from .spells import Spell, get_spell
from .state import BattleState, CombatantState, WeaponState

#: How a menu line says a bound was passed on a Game Master's lifting of it
#: (tarmar-studio #37).
PAST_BY_RULING = "by a Game Master's ruling"
#: Whatever an option is aimed with (tarmar-engine #21): its targets, or its
#: candidates at them.
Aimed = TypeVar("Aimed")

#: The key a candidate's forecast is carried under in its payload
#: (:class:`Forecast`, tarmar-engine #22).
FORECAST_KEY = "forecast"

# Utility knobs. Centralized so tuning the AI is a data change.
MOVE_BASE_SCORE = 1.0
DODGE_BASE_SCORE = 0.3
DEFEND_BASE_SCORE = 0.3
DISENGAGE_BASE_SCORE = 0.1
STAND_UP_SCORE = 5.0
HURT_THRESHOLD = 0.5  # fraction of the hurt gauge below which caution kicks in
BUFF_SCORE = 1.2  # flat value of a defensive continuing spell
HEAL_SCORE_PER_POINT = 0.45
# A wounded beast guards itself: flat Defend bonus once its body gauge is
# below HURT_THRESHOLD (beasts read the hurt gauge from body, not fatigue —
# an animal presses on through exhaustion but shields a bleeding flank).
WOUNDED_BEAST_DEFEND_BONUS = 0.8
# Grapple valued at 0: full grapple tactics are out of scope (module
# docstring). The AI never spends its turn attempting one.
GRAPPLE_ATTEMPT_SCORE = 0.0
GRAPPLE_TACTICS_RATIONALE = "grapple tactics out of scope"
# Getting a weapon back into an empty (or wrong) hand: flat, above any
# bare-handed or bow-in-melee alternative the menu leaves such a figure.
REARM_SCORE = 1.5
# The HTH table's "no-gesture spell" (tarmar-engine #19): the Spell Mastery
# level at which a spell needs no gestures (spell-mastery.md).
HTH_NO_GESTURE_MASTERY = 2
# Options offered for a player to choose that the AI does not take: Sprint's
# 6 Fatigue a turn, going prone, drawing a dagger mid-hold.
PLAYER_ONLY_SCORE = 0.0
# Anchoring one's own Runaway is the only way to stop it feeding (tarmar-
# engine #30): flat, above every flat score here, and the AI's choice
# whatever else scores (house reading ``ai_anchors_only_itself``).
OWN_RUNAWAY_ANCHOR_SCORE = 10.0
#: The menu key of the anchor option (tarmar-engine #30): a whole action,
#: like a cast, that stands still.
ANCHOR_OPTION = "anchor"

#: Decimal places every score and every factor behind it is printed to.
#: Scores are *built* from factors already rounded to this precision (see
#: :func:`product_of`), so a reader who multiplies the printed factors gets
#: the printed score back exactly — the tarmar-studio #301 complaint was a
#: rationale reading "60% x 4.7" beside a score of 2.835.
SCORE_PRECISION = 3


def round_score(value: float) -> float:
    """A score or factor at the precision the log prints it to."""
    return round(value, SCORE_PRECISION)


def product_of(*factors: float) -> float:
    """The product of the factors *as printed*, not as computed.

    Rounding each factor before multiplying is what makes the log
    checkable: the score stored on the candidate is exactly what a reader
    gets by multiplying the numbers in front of them. The lost precision is
    below any threshold the ranking cares about — candidates that differ by
    less than a thousandth were already a coin flip broken by letter order.
    """
    product = 1.0
    for factor in factors:
        product *= round_score(factor)
    return round_score(product)


def score_text(value: float) -> str:
    """A score or factor rendered for the log, at :data:`SCORE_PRECISION`."""
    return f"{value:.{SCORE_PRECISION}f}"


@dataclass(frozen=True)
class Forecast:
    """What an option expects to do to its target, as the figures its score
    and rationale are built from (tarmar-engine #22).

    Filled for an attack (ATTACK, a CHARGE that reaches, MISSILE, ONE LAST
    SHOT, a hand-to-hand strike) and for a damaging cast; ``None`` on every
    other option. Each figure is held at :data:`SCORE_PRECISION`, as the
    rationale prints it: the rationale's working is :meth:`working`, and the
    forecast's own score, :attr:`score`, is the product of the two factors it
    prints. A caller reads the figures here rather than out of the prose.

    The forecast is what one attempt would do. The option's score may be
    adjusted after it, as its rationale then says: a wounded beast's caution
    scales it, and a throw of the only hand weapon carried, a borrowing cast
    and a cast pressing a countered spell again (:func:`_mark_escalations`)
    are scored :data:`PLAYER_ONLY_SCORE` with their forecasts kept.
    """

    #: The chance the option lands: P(hit) for a d20 attack; P(land) for a
    #: cast, its casting roll times, for a targeted spell, its aim roll.
    hit_chance: float
    #: The dice one landed hit rolls, e.g. ``"2d6-1"``.
    damage_expression: str
    #: The armour stops that come off a landed hit's damage.
    armour_stops: int
    #: The expected damage of one landed hit, after the armour stops.
    expected_damage: float
    #: A d20 attack's Target Number, the bonus its d20 gets, and how many of
    #: the die's faces (``resolution.DIE_FACES``) hit. ``None`` for a cast,
    #: whose chance comes from 3d6 roll-unders the rationale names.
    target_number: int | None = None
    bonus: int | None = None
    winning_faces: int | None = None

    @property
    def score(self) -> float:
        """P(land) times the expected damage, as :func:`product_of` takes it."""
        return product_of(self.hit_chance, self.expected_damage)

    def working(self, aimed_at: str = "") -> str:
        """The forecast as the rationale prints it, ending in its score.

        A d20 attack: "d20 +3 vs TN 15 — P(hit) 0.450 (9 of 20 faces) x
        6.350 expected damage (2d6-1 less 0 armour stops) = 2.857". A cast:
        "P(land) 0.375 x 3.500 expected damage on Foe (1d6 less 0 armour
        stops) = 1.312", ``aimed_at`` naming whom (" on Foe").
        """
        damage = (
            f"x {score_text(self.expected_damage)} expected damage{aimed_at} "
            f"({self.damage_expression} less {self.armour_stops} armour stops) "
            f"= {score_text(self.score)}"
        )
        if (
            self.target_number is None
            or self.bonus is None
            or self.winning_faces is None
        ):
            return f"P(land) {score_text(self.hit_chance)} {damage}"
        return (
            f"d20 {self.bonus:+d} vs TN {self.target_number} "
            f"— P(hit) {score_text(self.hit_chance)} "
            f"({self.winning_faces} of {combat.DIE_FACES} faces) {damage}"
        )

    def to_payload(self) -> dict:
        """The forecast's figures by field name, ``None`` where they do not
        apply (a cast's d20 figures)."""
        return asdict(self)


def _d20_forecast(
    numbers: combat_math.AttackNumbers,
    damage_expression: str,
    armour_stops: int,
    expected_damage: float,
) -> Forecast:
    """The forecast of a d20 attack made with ``numbers``.

    Args:
        numbers: The attack's Target Number and bonus, as
            :func:`.combat_math.attack_numbers` gives them.
        damage_expression: The dice a hit rolls.
        armour_stops: The stops a hit's damage loses.
        expected_damage: The expected damage of one hit after them.

    Returns:
        The forecast, the winning faces counted from the exact P(hit).
    """
    probability = combat.hit_probability(numbers.target_number, numbers.bonus)
    return Forecast(
        hit_chance=round_score(probability),
        damage_expression=damage_expression,
        armour_stops=armour_stops,
        expected_damage=round_score(expected_damage),
        target_number=numbers.target_number,
        bonus=numbers.bonus,
        winning_faces=round(probability * combat.DIE_FACES),
    )


@dataclass
class Candidate:
    """One scored action option."""

    letter: str
    name: str
    score: float
    rationale: str
    #: Who the option is aimed at, in the identifier its own profile uses:
    #: the Tarmar profile numbers its combatants, the classic profile gives
    #: each figure a string ``uid``. The field admits both so one candidate
    #: shape serves both menus — a consumer that stores it must not assume int.
    target_id: int | str | None = None
    spell_key: str = ""
    #: Extra mana a cast pushes into its spell (tarmar-engine #27); 0 is an
    #: ordinary cast.
    push_mana: int = 0
    #: Does the cast borrow the mana the actor lacks (tarmar-engine #28)?
    borrow: bool = False
    #: The hex the option places the figure at, axial ``(q, r)``, or ``None``
    #: for the move it derives itself (tarmar-engine #20). Which options may
    #: take one, and where, is :func:`movement.legal_destinations`; the engine
    #: refuses any other in its log.
    destination: tuple[int, int] | None = None
    #: The combatant whose Runaway the option anchors, the actor's own id for
    #: its own (tarmar-engine #30), or ``None`` for any other option.
    anchor_target: int | None = None
    #: The mana a counter commits against the caster in ``target_id``
    #: (tarmar-engine #31), or ``None`` for any other option.
    counter_mana: int | None = None
    #: Does the cast press a countered spell again (tarmar-engine #31)?
    escalate: bool = False
    #: The bounds a Game Master lifted for the actor that this cast or
    #: counter goes past (tarmar-studio #37), as
    #: :meth:`.magic.MagicRules.lifted_bounds_passed` and
    #: :meth:`.magic.CounterspellRules.lifted_bounds_passed` answer it: the
    #: same answer the engine's event payload gives when it runs, so a caller
    #: reads it here rather than recomputing it. Empty for every other option.
    lifted_bounds: tuple[str, ...] = ()
    #: What the option expects to do to its target (tarmar-engine #22), the
    #: figures its rationale prints, or ``None`` for an option with no
    #: forecast (DEFEND, DODGE, a move, a grapple, a buff or a heal, a pushed
    #: cast).
    forecast: Forecast | None = None

    def to_payload(self) -> dict:
        payload = {
            "letter": self.letter,
            "name": self.name,
            "score": round_score(self.score),
            # The score as the log should print it. A bare float renders at
            # whatever width repr chooses (0.5, 2.94, 4.41), which stops the
            # rationale's "… = 0.500" from matching the score beside it.
            "score_text": score_text(self.score),
            "rationale": self.rationale,
            "target_id": self.target_id,
            "spell_key": self.spell_key,
        }
        # Only a pushed cast carries the key, so a battle with no Push rules
        # injected logs the decision payloads it always has.
        if self.push_mana:
            payload["push_mana"] = self.push_mana
        # Likewise only a borrowing cast carries its key.
        if self.borrow:
            payload["borrow"] = True
        # The same for a hex: a candidate that names none logs as it did.
        if self.destination is not None:
            payload["destination"] = list(self.destination)
        # And for an anchor, only on the anchor option.
        if self.anchor_target is not None:
            payload["anchor_target"] = self.anchor_target
        # And for a counter and an escalation, only on those options.
        if self.counter_mana is not None:
            payload["counter_mana"] = self.counter_mana
        if self.escalate:
            payload["escalate"] = True
        # And for the bounds passed by ruling, only where there are some.
        if self.lifted_bounds:
            payload[LIFTED_BOUNDS_KEY] = list(self.lifted_bounds)
        # And for a forecast, only on an option that has one.
        if self.forecast is not None:
            payload[FORECAST_KEY] = self.forecast.to_payload()
        return payload


@dataclass
class Decision:
    """The chosen candidate plus everything that was considered."""

    chosen: Candidate
    candidates: list[Candidate] = field(default_factory=list)
    #: Why casts were withheld from the menu, one line per spell: past the
    #: actor's Channel, or a spell the Channel rules give no kind of magic
    #: (tarmar-engine #29). Empty with no Channel rules injected.
    withheld: list[str] = field(default_factory=list)


def three_d6_at_most(target: int) -> float:
    """Exact P(3d6 <= target) — the rules' attribute-check success chance."""
    outcomes = 0
    for first in range(1, 7):
        for second in range(1, 7):
            for third in range(1, 7):
                if first + second + third <= target:
                    outcomes += 1
    return outcomes / 216


def four_d6_at_most(target: int) -> float:
    """Exact P(4d6 <= target): the HTH DISENGAGE and Struggle Free roll."""
    outcomes = 0
    for first in range(1, 7):
        for second in range(1, 7):
            for third in range(1, 7):
                for fourth in range(1, 7):
                    if first + second + third + fourth <= target:
                        outcomes += 1
    return outcomes / 1296


def _incoming_melee_threat(state: BattleState, actor: CombatantState) -> float:
    """Expected melee damage per turn from adjacent enemies, if all swing."""
    threat = 0.0
    for enemy in state.enemies_of(actor):
        if not combat_math.figures_adjacent(actor, enemy):
            continue
        numbers = combat_math.attack_numbers(enemy, actor, ranged=False)
        probability = combat.hit_probability(numbers.target_number, numbers.bonus)
        threat += probability * combat_math.expected_attack_damage(enemy, actor)
    return threat


def _hurt_fraction(combatant: CombatantState) -> float:
    """Remaining fraction of the pool that gates caution.

    Characters watch fatigue (it drains first and drops them unconscious);
    beasts watch body — their flee/defend thresholds run off wounds taken,
    per the beast-AI design for #181.
    """
    if combatant.is_beast:
        if combatant.max_body <= 0:
            return 0.0
        return max(0.0, combatant.body / combatant.max_body)
    if combatant.max_fatigue <= 0:
        return 0.0
    return max(0.0, combatant.fatigue / combatant.max_fatigue)


def _beast_caution(
    actor: CombatantState, hurt: bool, score: float, rationale: str
) -> tuple[float, str]:
    """A badly wounded beast loses its appetite for attacking.

    Attack scores scale by the remaining body fraction once the beast's body
    gauge drops below :data:`HURT_THRESHOLD`, so Defend and Disengage win
    the comparison — the flee/defend thresholds run off remaining body.
    Characters (and healthy beasts) are untouched.
    """
    if not actor.is_beast or not hurt:
        return score, rationale
    fraction = _hurt_fraction(actor)
    scaled = product_of(score, fraction)
    # The caution factor joins the printed chain rather than silently
    # re-scaling a score the rationale has already shown its working for.
    return scaled, (
        f"{rationale}, x {score_text(fraction)} body left "
        f"(wounded and wary) = {score_text(scaled)}"
    )


def enemies_by_distance(
    state: BattleState, actor: CombatantState
) -> list[CombatantState]:
    """Active enemies, closest first; ties broken by lowest fatigue, then id."""
    return sorted(
        state.enemies_of(actor),
        key=lambda enemy: (
            combat_math.figure_distance(actor, enemy),
            enemy.fatigue,
            enemy.combatant_id,
        ),
    )


def nearest_enemy(state: BattleState, actor: CombatantState) -> CombatantState | None:
    """Closest active enemy, the first of :func:`enemies_by_distance`."""
    ranked = enemies_by_distance(state, actor)
    return ranked[0] if ranked else None


def _approaches(
    state: BattleState, actor: CombatantState, gait: str
) -> Iterator[tuple[CombatantState, int, movement.Approach]]:
    """Each enemy, closest first, with its distance now and where the
    engine's own approach at ``gait`` would leave the actor
    (:func:`movement.approach_route`), worked out only as far as asked.

    MOVE and CHARGE read past a nearest enemy no clear path gets nearer to,
    so a blocked figure goes for one it can reach rather than standing
    (tarmar-engine #37).
    """
    allowance = movement.gait_allowance(actor, gait)
    for enemy in enemies_by_distance(state, actor):
        yield (
            enemy,
            combat_math.figure_distance(actor, enemy),
            movement.approach_route(state, actor, enemy, allowance),
        )


def _attack_forecast(
    actor: CombatantState, defender: CombatantState, *, ranged: bool
) -> tuple[Forecast, str]:
    """Forecast one prospective swing or shot, showing every input it used.

    The rationale names the four numbers a reader would need to check the
    forecast by hand and could not previously see (tarmar-studio #301): the
    weapon in hand, the distance to the target, the to-hit bonus the d20
    gets, and the armour stops that come off the damage. P(hit) is given
    both as a probability and as the count of winning d20 faces it came
    from, because that count is what the bonus and the Target Number
    actually decide.

    Melee and missile share this one function: they differed only in which
    numbers they printed, which is exactly how melee came to hide the
    distance that missile showed. The figures are the :class:`Forecast`
    returned beside the rationale, which prints them (tarmar-engine #22).
    """
    numbers = combat_math.attack_numbers(actor, defender, ranged=ranged)
    stops = combat.applied_armour_stops(
        defender.stops, actor.weapon.weapon_class, defender.armour_tier
    )
    forecast = _d20_forecast(
        numbers,
        actor.weapon.damage,
        stops,
        combat_math.expected_attack_damage(actor, defender),
    )
    range_note = f" (range {numbers.range_penalty:+d})" if ranged else ""
    reach = combat_math.hexes_text(numbers.distance)
    return forecast, f"{actor.weapon.name} at {reach}{range_note}, {forecast.working()}"


def _aimed_at(targets: list[Aimed], every_target: bool) -> list[Aimed]:
    """The first of ``targets``, the AI's, or with ``every_target`` all of
    them, the AI's first (tarmar-engine #21)."""
    return targets if every_target else targets[:1]


def _best_first(aimed: list[Candidate], every_target: bool) -> list[Candidate]:
    """The best-scoring of one option's candidates at its targets (the first
    on a tie), the AI's pick, then with ``every_target`` the rest in their
    order (tarmar-engine #21)."""
    best = max(aimed, key=lambda candidate: candidate.score)
    if not every_target:
        return [best]
    return [best, *(candidate for candidate in aimed if candidate is not best)]


def _charge_candidate(
    actor: CombatantState,
    target: CombatantState,
    passed_over: CombatantState | None,
    hurt: bool,
    letter: str,
) -> Candidate:
    """A CHARGE (b) at an enemy its jog ends beside, priced as the swing it
    ends in; ``passed_over`` is the nearer enemy it cannot reach, if any."""
    forecast, rationale = _attack_forecast(actor, target, ranged=False)
    if passed_over is not None:
        rationale = f"{passed_over.name} is nearer but out of charge reach; {rationale}"
    score, rationale = _beast_caution(actor, hurt, forecast.score, rationale)
    return _charge(letter, target, score, rationale, forecast)


def _charge(
    letter: str,
    target: CombatantState,
    score: float,
    rationale: str,
    forecast: Forecast | None = None,
) -> Candidate:
    """A CHARGE (b) candidate at ``target``, reaching it or not."""
    return Candidate(
        letter,
        actions.option_name(letter),
        score,
        f"Charge {target.name}: {rationale}",
        target_id=target.combatant_id,
        forecast=forecast,
    )


def _cast_candidates(
    state: BattleState,
    actor: CombatantState,
    letter: str,
    beyond_purse: bool = False,
    every_target: bool = False,
) -> list[Candidate]:
    """A candidate per castable spell the actor can afford.

    ``beyond_purse`` lists the spells the actor cannot afford too, for the
    borrowing variants to be built from (:func:`_borrow_candidates`).

    The AI aims a damaging spell at the nearest enemy. ``every_target``
    offers it at every active enemy as well, the nearest first, each priced
    against its own target (tarmar-engine #21): a spell has no range, and the
    engine lands a cast on whichever active enemy it names.
    """
    candidates = []
    aimed_at = _aimed_at(enemies_by_distance(state, actor), every_target)
    for key in actor.spells:
        spell = get_spell(key)
        if spell.level > actor.mana and not beyond_purse:
            continue
        if spell.continuing and key in actor.active_spells:
            continue  # already up; renewal happens in phase 2
        attribute = actor.intelligence if spell.attribute == "INT" else actor.wisdom
        cast_probability = three_d6_at_most(attribute)
        attribute_name = spell.attribute
        cast_note = (
            f"3d6 ≤ {attribute_name} {attribute} — P(cast) "
            f"{score_text(cast_probability)}"
        )
        if spell.heals:
            missing = actor.max_fatigue - actor.fatigue
            healed = min(missing, 3.5)
            value = product_of(cast_probability, healed, HEAL_SCORE_PER_POINT)
            rationale = (
                f"{cast_note} x {score_text(healed)} fatigue restored "
                f"({missing} fatigue missing, capped at 3.500 per cast) "
                f"x {score_text(HEAL_SCORE_PER_POINT)} per point "
                f"= {score_text(value)}"
            )
            candidates.append(
                Candidate(
                    letter, f"CAST SPELL {spell.name}", value, rationale, spell_key=key
                )
            )
            continue
        if spell.damage is None:
            value = product_of(cast_probability, BUFF_SCORE)
            rationale = (
                f"{cast_note} x {score_text(BUFF_SCORE)} flat value of a "
                f"defensive buff = {score_text(value)}"
            )
            candidates.append(
                Candidate(
                    letter, f"CAST SPELL {spell.name}", value, rationale, spell_key=key
                )
            )
            continue
        aim_note = ""
        probability = cast_probability
        if spell.targeted:
            aim_probability = three_d6_at_most(actor.dexterity)
            probability = probability * aim_probability
            aim_note = (
                f" x P(aim) {score_text(aim_probability)} "
                f"(3d6 ≤ DEX {actor.dexterity})"
            )
        for enemy in aimed_at:
            stops = 0 if spell.ignores_armour else enemy.stops
            forecast = Forecast(
                hit_chance=round_score(probability),
                damage_expression=spell.damage,
                armour_stops=stops,
                expected_damage=round_score(
                    combat_math.expected_damage(spell.damage, stops)
                ),
            )
            working = forecast.working(f" on {enemy.name}")
            candidates.append(
                Candidate(
                    letter,
                    f"CAST SPELL {spell.name}",
                    forecast.score,
                    f"{cast_note}{aim_note} — {working}",
                    target_id=enemy.combatant_id,
                    spell_key=key,
                    forecast=forecast,
                )
            )
    return candidates


def _grappled_decision(
    state: BattleState,
    actor: CombatantState,
    beyond_purse: bool = False,
    every_target: bool = False,
) -> Decision:
    """A held figure's fixed turn choice: always Struggle Free.

    hand-to-hand-and-grappling.md's "Being Grappled" options — Struggle
    Free, Strike Back, Hold Still — replace the whole normal candidate set.
    No weighing: escaping always outscores fighting back or waiting, by
    construction (module docstring's "deliberately dumb" grapple AI).
    """
    grappler_id = actor.grappled_by
    # The step the engine expects to take, for the forecast; the engine works
    # it out when the figure acts, so the candidate names no hex (#20).
    escape_to = (
        None
        if grappler_id is None
        else movement.step_away_hex(state, actor, state.by_id(grappler_id))
    )
    where = f" toward {escape_to}" if escape_to is not None else ""
    candidates = [
        Candidate(
            "v",
            actions.GRAPPLED_ACTIONS["v"],
            1.0,
            f"Always attempts to escape{where} ({GRAPPLE_TACTICS_RATIONALE})",
            target_id=grappler_id,
        ),
        Candidate(
            "t",
            actions.GRAPPLED_ACTIONS["t"],
            0.0,
            f"Not attempted by default ({GRAPPLE_TACTICS_RATIONALE})",
            target_id=grappler_id,
        ),
        Candidate(
            "hold_still",
            actions.GRAPPLED_ACTIONS["hold_still"],
            0.0,
            f"Not chosen by default ({GRAPPLE_TACTICS_RATIONALE})",
            target_id=grappler_id,
        ),
    ]
    # The same page's other two: a dagger to draw (#822), and a spell cast
    # with neither gestures nor words (Spell Mastery 3, #821). Listed for a
    # player to choose; the fixed default above stands.
    if not actor.weapon.hth_usable and any(
        spare.hth_usable for spare in actor.spare_weapons
    ):
        candidates.append(
            Candidate(
                "u",
                actions.GRAPPLED_EXTRA_ACTIONS["u"],
                PLAYER_ONLY_SCORE,
                "Roll 3d6 ≤ DEX to ready a dagger to strike back with "
                f"({GRAPPLE_TACTICS_RATIONALE})",
                target_id=grappler_id,
            )
        )
    candidates += _mastery_three_casts(state, actor, beyond_purse, every_target)
    return Decision(chosen=candidates[0], candidates=candidates)


def _mastery_three_casts(
    state: BattleState,
    actor: CombatantState,
    beyond_purse: bool = False,
    every_target: bool = False,
) -> list[Candidate]:
    """Casts open to either side of a grapple: Spell Mastery 3 spells only."""
    return [
        Candidate(
            candidate.letter,
            candidate.name,
            PLAYER_ONLY_SCORE,
            f"Spell Mastery 3: cast without gestures or words "
            f"({GRAPPLE_TACTICS_RATIONALE}; {candidate.rationale})",
            target_id=candidate.target_id,
            spell_key=candidate.spell_key,
            forecast=candidate.forecast,
        )
        for candidate in _cast_candidates(
            state, actor, "r", beyond_purse, every_target
        )
        if actor.spell_mastery.get(candidate.spell_key, 1) >= 3
    ]


def _grappler_decision(
    state: BattleState,
    actor: CombatantState,
    beyond_purse: bool = False,
    every_target: bool = False,
) -> Decision:
    """A grappler's fixed turn choice: always Squeeze.

    hand-to-hand-and-grappling.md's grappler options — Maintain, Squeeze,
    Release — replace the whole normal candidate set. No weighing between
    them (module docstring's "deliberately dumb" grapple AI): the point of
    grappling is landing Squeeze, so that is what the AI always does once
    it holds someone (even though it never chooses to start holding one).
    """
    target_id = actor.grappling
    candidates = [
        Candidate(
            "squeeze",
            actions.GRAPPLER_ACTIONS["squeeze"],
            1.0,
            f"Always squeezes the held target ({GRAPPLE_TACTICS_RATIONALE})",
            target_id=target_id,
        ),
        Candidate(
            "maintain",
            actions.GRAPPLER_ACTIONS["maintain"],
            0.0,
            f"Not chosen by default ({GRAPPLE_TACTICS_RATIONALE})",
            target_id=target_id,
        ),
        Candidate(
            "release",
            actions.GRAPPLER_ACTIONS["release"],
            0.0,
            f"Not chosen by default ({GRAPPLE_TACTICS_RATIONALE})",
            target_id=target_id,
        ),
    ]
    # The grappler's hands are full too ("holding on or being held"): a
    # spell known at Spell Mastery 3 is on its list, for a player (#821).
    candidates += _mastery_three_casts(state, actor, beyond_purse, every_target)
    return Decision(chosen=candidates[0], candidates=candidates)


@dataclass(frozen=True)
class RetreatChoice:
    """Where a forced retreat pushes its victim, and whether the pusher follows."""

    destination: tuple[int, int]
    advance: bool


def choose_retreat(
    state: BattleState,
    pusher: CombatantState,
    victim: CombatantState,
    destinations: list[tuple[int, int]],
    may_advance: bool,
) -> RetreatChoice:
    """The AI's forced-retreat choice (special-combat-situations.md, #779).

    The rules let the pusher push "in any direction" and "Choose to advance
    ... or stand still". The AI takes the first hex offered — straight back
    whenever that is clear, as the engine always pushed — and follows the
    victim in unless it fights with a missile weapon, which wants the room.
    """
    return RetreatChoice(
        destination=destinations[0],
        advance=may_advance and not pusher.weapon.is_missile,
    )


def _mean_damage(weapon: WeaponState) -> float:
    count, sides, modifier = parse_dice_expression(weapon.damage)
    return count * (sides + 1) / 2 + modifier


def best_weapon(
    actor: CombatantState, choices: list[WeaponState]
) -> WeaponState | None:
    """The weapon the engine readies when an option leaves the pick to it.

    READY WEAPON, CHANGE WEAPON and PICK UP WEAPON carry no weapon on the
    menu, so the figure takes the one it is best with: highest Weapon-skill
    level, then highest mean damage, then catalog order.
    """
    usable = [choice for choice in choices if choice.item_id]
    if not usable:
        return None
    return max(
        usable,
        key=lambda weapon: (
            actor.weapon_skills.get(weapon.item_id, 0),
            _mean_damage(weapon),
        ),
    )


def weapons_in_reach(state: BattleState, actor: CombatantState) -> list[WeaponState]:
    """Weapons lying in the actor's hex or next to it."""
    return [
        lying.weapon
        for lying in state.ground_weapons
        if hexes.distance(lying.position, actor.position) <= 1
    ]


def _hth_targets(state: BattleState, actor: CombatantState) -> list[CombatantState]:
    """Adjacent enemies an HTH entry condition admits (#823), lowest id first."""
    return sorted(
        (
            enemy
            for enemy in state.enemies_of(actor)
            if combat_math.hth_entry_reason(state, actor, enemy) is not None
        ),
        key=lambda enemy: enemy.combatant_id,
    )


def _hth_partners(state: BattleState, actor: CombatantState) -> list[CombatantState]:
    """The active enemies beside the actor that it is in hand-to-hand with."""
    return [
        state.by_id(other_id)
        for other_id in actor.hth_with
        if state.by_id(other_id).active
        and combat_math.figures_adjacent(actor, state.by_id(other_id))
    ]


def _hth_casts(
    state: BattleState,
    actor: CombatantState,
    beyond_purse: bool = False,
    every_target: bool = False,
) -> list[Candidate]:
    """Casts the HTH table allows: "If hands free or no-gesture spell".

    Hands free is no weapon in hand (a shield is a standing fact of the
    snapshot, not a held item); a no-gesture spell is one known at Spell
    Mastery 2 (spell-mastery.md; tarmar-engine #19).
    """
    hands_free = actor.weapon.item_id == ""
    return [
        candidate
        for candidate in _cast_candidates(
            state, actor, "r", beyond_purse, every_target
        )
        if hands_free
        or actor.spell_mastery.get(candidate.spell_key, 1) >= HTH_NO_GESTURE_MASTERY
    ]


def _hth_disengage_candidate(
    state: BattleState,
    actor: CombatantState,
    partners: list[CombatantState],
) -> Candidate:
    """v DISENGAGE from hand-to-hand: "Roll 4d6 ≤ DEX → stand, move to
    adjacent", scored by what breaking away buys (tarmar-engine #38).

    Over this turn and the next: this turn goes on the roll, and next turn
    the figure fights its partner with the weapon in its hand if it got free
    (P(escape)), and strikes in close as it could have this turn if not. The
    two turns' expected damage, halved, is a per-turn figure the HTH strike's
    own forecast (t) compares with directly. A figure whose weapon outclasses
    what it strikes with in close breaks away when the chance of getting free
    pays for the turn; one whose weapon is no better never does, since half
    of two strikes' worth is never more than one.
    """
    partner = partners[0]
    injury = TarmarReactions().injury_penalty(actor)
    off_balance = combat_math.OFF_BALANCE_PENALTY if actor.off_balance else 0
    effective_dex = actor.dexterity - injury - off_balance
    probability = four_d6_at_most(effective_dex)
    held = 1 - probability
    strike = _hth_strike_forecast(state, actor, partner)[0].score
    if actor.weapon.item_id:
        weapon_forecast, _rationale = _attack_forecast(
            actor, partner, ranged=actor.weapon.is_missile
        )
        free = weapon_forecast.score
        once_free = f"the {actor.weapon.name} {score_text(free)}"
    else:
        free = 0.0
        once_free = f"no weapon in hand, {score_text(free)}"
    escaped = product_of(probability, free)
    still_held = product_of(held, strike)
    score = round_score((escaped + still_held) / 2)
    # The forecast names the step expected; the engine works it out when the
    # figure acts, so the candidate names no hex (#20).
    step = movement.step_away_hex(state, actor, partner)
    where = f" toward {step}" if step is not None else ""
    return Candidate(
        "v",
        actions.option_name("v"),
        score,
        f"Break away{where}: 4d6 ≤ DEX {effective_dex} — P(escape) "
        f"{score_text(probability)}; next turn against {partner.name}, "
        f"{once_free} if free, {score_text(strike)} striking in close if "
        f"not; this turn spent, so ({score_text(probability)} x "
        f"{score_text(free)} + {score_text(held)} x {score_text(strike)}) / 2 "
        f"= {score_text(score)}",
    )


def _hth_strike_forecast(
    state: BattleState, actor: CombatantState, defender: CombatantState
) -> tuple[Forecast, str]:
    """Forecast a standalone HTH strike (t): bare hands or a dagger, +4."""
    dagger = actor.weapon.hth_usable
    numbers = combat_math.attack_numbers(
        actor,
        defender,
        ranged=False,
        weapon_class=None if dagger else "Striking",
        extra_situational=hexes.HTH_TO_HIT_BONUS,
        ignore_attacker_skill=not dagger,
    )
    damage_expression = (
        actor.weapon.damage if dagger else combat_math.bare_handed_expression(actor)
    )
    stops = combat.applied_armour_stops(
        defender.stops,
        actor.weapon.weapon_class if dagger else "Striking",
        defender.armour_tier,
    )
    forecast = _d20_forecast(
        numbers,
        damage_expression,
        stops,
        combat_math.expected_damage(damage_expression, stops),
    )
    reason = combat_math.hth_entry_reason(state, actor, defender)
    held = f"the {actor.weapon.name}" if dagger else "bare hands"
    return forecast, f"in close ({reason}) with {held}, {forecast.working()}"


def choose_option(
    state: BattleState,
    actor: CombatantState,
    magic: MagicRules | None = None,
    *,
    every_target: bool = False,
) -> Decision:
    """Score the actor's legal options and choose the best.

    ``magic`` is the profile's injected magic rules (:mod:`.magic`). With
    Channel rules in them, a cast past the actor's Channel for its spell is
    withdrawn from the menu (:func:`_within_channel`). With Push rules, every
    cast left on the menu gains its pushed variants, up to the Channel, for a
    player to choose; the AI does not push (:func:`_push_candidates`). With
    Borrowing rules, a cast or pushed cast the actor cannot afford is offered
    as a borrowing variant, for a player; the AI does not borrow
    (:func:`_borrow_candidates`). With Runaway rules, a Runaway the actor
    may anchor is offered, and the AI always anchors its own
    (:func:`_anchor_candidates`). With Counterspell rules, a cast pressing a
    countered spell again is flagged and left to a player
    (:func:`_mark_escalations`), and a counter against each enemy mage is
    offered, for a player (:func:`_counter_candidates`);
    the AI neither counters nor escalates. A figure a Game Master has lifted
    Channel, Push's own cap or the counter bounds for
    (``CombatantState.lifted_magic_bounds``) is offered the wider amounts;
    every other figure's menu is unchanged. A caller driving the engine with this
    policy passes the same rules the profile carries, e.g.
    ``functools.partial(choose_option, magic=rules)``: the engine refuses a
    cast past the Channel, so a menu built without them can offer one it
    will not run.

    ``every_target`` is the menu a person aims (tarmar-engine #21). The AI
    aims each attack and each damaging cast at one enemy; this menu also
    offers it at every other enemy the option may legally be aimed at, each
    priced and explained by the code that prices the AI's own (its score,
    rationale and :attr:`Candidate.forecast`), right after the AI's own
    target: ATTACK (j) and ONE LAST SHOT (l) at each adjacent enemy, CHARGE
    (b, and its yielded variant) at each enemy its jog ends beside, MISSILE
    (f) at each active enemy, an HTH strike (t) and ATTEMPT HTH (o) at each
    enemy an entry condition admits, and a damaging cast at each active
    enemy, with its pushed, borrowing and escalated variants. ``chosen`` is
    the AI's own choice, unchanged, and is one of the candidates. A figure
    in a grapple keeps its fixed menu, but for its casts.
    """
    decision = _score_options(state, actor, every_target=every_target)
    if magic is not None and magic.channel is not None:
        decision = _within_channel(actor, decision, magic)
    if magic is not None and magic.push is not None:
        decision.candidates.extend(
            _push_candidates(actor, decision.candidates, magic, magic.push)
        )
    if magic is not None and magic.borrowing is not None:
        decision.candidates.extend(
            _borrow_candidates(state, actor, magic, magic.borrowing, every_target)
        )
    if magic is not None and magic.counterspell is not None:
        _mark_escalations(actor, decision)
    if magic is not None and magic.runaway is not None:
        anchors = _anchor_candidates(state, actor, magic.runaway)
        decision.candidates.extend(anchors)
        own = next(
            (
                candidate
                for candidate in anchors
                if candidate.anchor_target == actor.combatant_id
            ),
            None,
        )
        if own is not None:
            decision.chosen = own
    if magic is not None and magic.counterspell is not None:
        decision.candidates.extend(
            _counter_candidates(state, actor, magic.counterspell)
        )
    if every_target:
        # The AI's choice is made on its own menu, so an option it aims
        # elsewhere can never displace it, whatever it scores.
        decision.chosen = _the_ais_choice(
            decision.candidates, choose_option(state, actor, magic).chosen
        )
    return decision


def _the_ais_choice(candidates: list[Candidate], chosen: Candidate) -> Candidate:
    """The candidate on the every-target menu that is the AI's own choice.

    Both menus are built by the same code, so the AI's choice is on the wider
    one, field for field, ahead of every option aimed elsewhere.

    Raises:
        LookupError: the wider menu lacks it, which would be a policy bug.
    """
    for candidate in candidates:
        if candidate == chosen:
            return candidate
    raise LookupError(
        f"the every-target menu lacks the AI's choice {chosen.letter} "
        f"at {chosen.target_id}"
    )


def _is_mage(actor: CombatantState) -> bool:
    """A spell known, or a spell skill recorded."""
    return bool(actor.spells or actor.spell_skill_levels)


def _mark_escalations(actor: CombatantState, decision: Decision) -> None:
    """Flag every cast that presses the actor's countered spell again.

    A cast of the contest's spell at the contest's target escalates
    (tarmar-engine #31): the caster pays again for another opposed roll. Each
    such cast, pushed and borrowing variants included, is marked
    ``escalate`` and scored :data:`PLAYER_ONLY_SCORE`, for a player; the AI
    does not escalate (house reading ``ai_neither_counters_nor_escalates``),
    so when it had chosen one it takes the best of the rest by the rule it
    chose with (the highest score, the first listed on a tie). Any other
    choice ends the contest when the engine records it.
    """
    contest = actor.contest
    if contest is None:
        return
    spell = get_spell(contest.spell_key)
    for candidate in decision.candidates:
        if (
            candidate.spell_key != contest.spell_key
            or candidate.target_id != contest.target_id
        ):
            continue
        candidate.escalate = True
        candidate.score = PLAYER_ONLY_SCORE
        candidate.rationale = (
            f"Press the countered {spell.name} again (countered "
            f"{contest.turns} time(s)), paying again for another opposed roll; "
            f"{candidate.rationale}; the AI does not escalate = "
            f"{score_text(PLAYER_ONLY_SCORE)}"
        )
    if not decision.chosen.escalate:
        return
    others = [candidate for candidate in decision.candidates if not candidate.escalate]
    decision.chosen = max(others, key=lambda candidate: candidate.score)


def _counter_candidates(
    state: BattleState, actor: CombatantState, counterspell: CounterspellRules
) -> list[Candidate]:
    """The counters the actor may declare (tarmar-engine #31).

    A mage counters a named enemy caster, not a chosen cast (house reading
    ``counter_names_a_caster``): for each enemy mage, one candidate per
    commitment the actor can afford
    (:meth:`.magic.CounterspellRules.commitments`), whether or not that
    caster has chosen its action yet, so a defender that chooses first
    counters blind. The counter opposes that caster's cast this turn
    whatever its target, and lapses, its mana spent, if no cast comes. The
    rationale names the cast when the caster has already chosen one.
    Scored :data:`PLAYER_ONLY_SCORE` and
    appended last, for a player; the AI does not counter (house reading
    ``ai_neither_counters_nor_escalates``). The rationale names the bonus the
    commitment buys on the opposed roll, less the injury band the defender's
    roll takes. A figure whose counter bounds a Game Master has lifted is
    offered every commitment from nothing to its whole purse.
    """
    if not _is_mage(actor):
        return []
    bounds_lifted = bound_lifted(actor, COUNTER_BOUNDS)
    candidates: list[Candidate] = []
    injury, _off_balance = _casting_penalties(actor)
    attribute = attribute_score(actor, counterspell.opposed_attribute)
    for caster in state.enemies_of(actor):
        if not _is_mage(caster):
            continue
        for mana in counterspell.commitments(actor.mana, bounds_lifted=bounds_lifted):
            bonus = counterspell.bonus_for(mana)
            candidates.append(
                Candidate(
                    actions.COUNTER_OPTION,
                    f"{actions.option_name(actions.COUNTER_OPTION)} "
                    f"{caster.name} ({mana} MANA)",
                    PLAYER_ONLY_SCORE,
                    f"{_counter_note(caster)}: "
                    f"commit {mana} mana ({actor.mana - mana} left) for "
                    f"{bonus:+d} on the opposed roll {counterspell.opposed_dice} "
                    f"+ {counterspell.opposed_attribute} {attribute} less "
                    f"{injury} injury; the AI does not counter = "
                    f"{score_text(PLAYER_ONLY_SCORE)}",
                    target_id=caster.combatant_id,
                    counter_mana=mana,
                    lifted_bounds=counterspell.lifted_bounds_passed(mana),
                )
            )
    return candidates


def _counter_note(caster: CombatantState) -> str:
    """What a counter against this caster meets, as far as the menu knows:
    the cast it has chosen, or that it has chosen nothing yet."""
    if actions.base_option(caster.chosen_letter) not in actions.CAST_OPTIONS:
        return f"Oppose whatever {caster.name} casts this turn (not yet known)"
    spell = get_spell(caster.chosen_spell)
    pressed = (
        f", pressed again after {caster.contest.turns} counter(s)"
        if caster.contest is not None
        else ""
    )
    return f"Oppose {caster.name}'s {spell.name}{pressed}"


def _anchor_candidates(
    state: BattleState, actor: CombatantState, runaway: RunawayRules
) -> list[Candidate]:
    """The Runaways the actor may anchor (tarmar-engine #30).

    Its own, when one is running: scored :data:`OWN_RUNAWAY_ANCHOR_SCORE`,
    and :func:`choose_option` makes it the AI's choice. Another living
    combatant's, when the actor is a mage (a spell known, or a spell skill
    recorded) and has the mana the helper pays: scored
    :data:`PLAYER_ONLY_SCORE`, for a player; the AI anchors only its own
    (house reading ``ai_anchors_only_itself``). The rationale gives the
    anchor roll's target as the engine will roll it.
    """
    candidates: list[Candidate] = []
    if actor.runaway is not None:
        spell = get_spell(actor.runaway.spell_key)
        candidates.append(
            Candidate(
                ANCHOR_OPTION,
                f"Anchor {spell.name}",
                OWN_RUNAWAY_ANCHOR_SCORE,
                f"{spell.name} has fed on {actor.name} for "
                f"{actor.runaway.turns_running} turn(s): "
                f"{_anchor_note(actor, runaway.self_anchor)}; the only way to "
                f"stop it, so the AI always anchors its own = "
                f"{score_text(OWN_RUNAWAY_ANCHOR_SCORE)}",
                target_id=actor.combatant_id,
                anchor_target=actor.combatant_id,
            )
        )
    if not _is_mage(actor):
        return candidates
    for caster in state.combatants:
        if caster is actor or not caster.alive or caster.runaway is None:
            continue
        cost = runaway.helper_cost(caster.runaway.original_cost)
        if actor.mana < cost:
            continue
        spell = get_spell(caster.runaway.spell_key)
        candidates.append(
            Candidate(
                ANCHOR_OPTION,
                f"Anchor {caster.name}'s {spell.name}",
                PLAYER_ONLY_SCORE,
                f"Pay {cost} mana ({actor.mana - cost} left): "
                f"{_anchor_note(actor, runaway.helper_anchor)}; the AI anchors "
                f"only its own = {score_text(PLAYER_ONLY_SCORE)}",
                target_id=caster.combatant_id,
                anchor_target=caster.combatant_id,
            )
        )
    return candidates


def _anchor_note(actor: CombatantState, rule: RollUnderRule) -> str:
    """An anchor roll as the menu prints it: less the injury band and an
    owed off-balance penalty, as the engine takes them
    (:func:`_casting_penalties`)."""
    injury, off_balance = _casting_penalties(actor)
    attribute = attribute_score(actor, rule.attribute)
    target = attribute - injury - off_balance
    return (
        f"anchor roll {rule.dice} ≤ {rule.attribute} {attribute} less "
        f"{injury} injury and {off_balance} off-balance = {target}"
    )


def _within_channel(
    actor: CombatantState, decision: Decision, magic: MagicRules
) -> Decision:
    """The decision less every cast the actor's Channel does not allow.

    A cast is withheld when its spell costs more than the actor's Channel
    for it (unless a Game Master has lifted Channel for the actor:
    :meth:`.magic.ChannelRules.bound_for`; the cast is then kept, carries
    :data:`~.magic.CHANNEL` in :attr:`Candidate.lifted_bounds` and its
    rationale says it passes the Channel by ruling), or when the Channel
    rules give the spell no kind of magic (the engine would refuse either), and
    :attr:`Decision.withheld` says why, once per spell. The withheld casts
    leave the player's menu and the AI's choice alike. When the AI had
    chosen one, it takes the best of what remains by the rule it chose with
    (the highest score, the first listed on a tie).

    The MOVE fallback for a menu left empty is defensive: every menu
    :func:`_score_options` builds holds an option that is not a cast (DODGE,
    the engaged options, standing up, Struggle Free), so ``choose_option``
    never empties one. It is probed directly.

    With no Channel rules in ``magic`` the decision comes back unchanged.
    """
    channel = magic.channel
    if channel is None:
        return decision
    kept: list[Candidate] = []
    withheld: dict[str, str] = {}
    for candidate in decision.candidates:
        if not candidate.spell_key:
            kept.append(candidate)
            continue
        spell = get_spell(candidate.spell_key)
        try:
            actor_channel = channel.channel_for(actor, spell)
            limit = channel.bound_for(actor, spell)
        except ChannelUnclassifiedSpell as unclassified:
            withheld.setdefault(spell.key, f"{spell.name} withheld: {unclassified}")
            continue
        if limit is not None and spell.level > limit:
            withheld.setdefault(
                spell.key,
                f"{spell.name} withheld: it costs {spell.level} mana, past "
                f"{actor.name}'s Channel {limit} for it",
            )
            continue
        candidate.lifted_bounds = magic.lifted_bounds_passed(actor, spell)
        if CHANNEL in candidate.lifted_bounds:
            candidate.rationale = (
                f"{candidate.rationale}; {spell.level} mana, "
                f"{_channel_note(actor_channel, candidate.lifted_bounds)}"
            )
        kept.append(candidate)
    reasons = list(withheld.values())
    if any(candidate is decision.chosen for candidate in kept):
        return Decision(chosen=decision.chosen, candidates=kept, withheld=reasons)
    if not kept:
        kept = [Candidate("a", "MOVE", 0.0, "Nothing else is legal")]
    return Decision(
        chosen=max(kept, key=lambda candidate: candidate.score),
        candidates=kept,
        withheld=reasons,
    )


def _push_candidates(
    actor: CombatantState,
    candidates: list[Candidate],
    magic: MagicRules,
    push: PushRules,
) -> list[Candidate]:
    """The pushed variant of each cast on the menu, one per affordable amount.

    With Channel rules, no variant takes the casting's whole mana past the
    actor's Channel for the spell, and each names that Channel; Push's own
    optional cap still applies beneath it. Either bound a Game Master has
    lifted for the actor does not apply, and a variant past it says so
    (:func:`_push_note`) and carries it in :attr:`Candidate.lifted_bounds`.

    Scored :data:`PLAYER_ONLY_SCORE` and appended last, so they never displace
    the AI's choice: pricing a push needs the spell-by-spell payoff, and the
    AI keeps to the plain cast. The rationale gives the Control Roll's target
    as the engine will roll it: less the injury band (the Tarmar profile's,
    the only one Push runs under) and an owed off-balance penalty. A spell
    the rules give no push effect gets no pushed variant.
    """
    pushed: list[Candidate] = []
    cap_lifted = bound_lifted(actor, PUSH_CAP)
    channel_rules = magic.channel
    for candidate in candidates:
        if not candidate.spell_key or candidate.push_mana:
            continue
        spell = get_spell(candidate.spell_key)
        channel, bound = (
            (None, None)
            if channel_rules is None
            else (
                channel_rules.channel_for(actor, spell),
                channel_rules.bound_for(actor, spell),
            )
        )
        room = push.push_room(spell, actor.mana, bound, cap_lifted=cap_lifted)
        for extra_mana in range(1, room + 1):
            passed = magic.lifted_bounds_passed(actor, spell, extra_mana)
            note = _push_note(actor, spell, push, extra_mana, channel, passed)
            pushed.append(
                Candidate(
                    candidate.letter,
                    f"{candidate.name} (PUSH {extra_mana} MANA)",
                    PLAYER_ONLY_SCORE,
                    f"{note}; the AI does not push = "
                    f"{score_text(PLAYER_ONLY_SCORE)}",
                    target_id=candidate.target_id,
                    spell_key=candidate.spell_key,
                    push_mana=extra_mana,
                    lifted_bounds=passed,
                )
            )
    return pushed


def _push_note(
    actor: CombatantState,
    spell: Spell,
    push: PushRules,
    extra_mana: int,
    channel: int | None,
    passed: tuple[str, ...],
) -> str:
    """A pushed cast's Control Roll as the menu prints it.

    The target is the one the engine will roll against: less the injury
    band and an owed off-balance penalty (:func:`_casting_penalties`).
    ``channel`` is the actor's Channel for the spell (``None`` with no
    Channel rules), named whether or not it bounds the actor. ``passed`` is
    the bounds the push goes past (:meth:`.magic.MagicRules.
    lifted_bounds_passed`), which only a Game Master's lifting allows; each
    says so (:data:`PAST_BY_RULING`).
    """
    injury, off_balance = _casting_penalties(actor)
    attribute = attribute_score(actor, push.control_attribute)
    invested = push.mana_invested(spell, extra_mana)
    target = attribute - push.penalty_per_mana * invested - injury - off_balance
    total = casting_mana(spell, extra_mana)
    bound_notes: list[str] = []
    if channel is not None:
        bound_notes.append(_channel_note(channel, passed))
    if PUSH_CAP in passed:
        bound_notes.append(f"past the push cap {push.max_push_mana} {PAST_BY_RULING}")
    channel_note = "".join(f", {note}" for note in bound_notes)
    return (
        f"Push {extra_mana} extra mana ({total} "
        f"in all{channel_note}): Control Roll {push.control_dice} ≤ "
        f"{push.control_attribute} {attribute} less "
        f"{push.penalty_per_mana} x {invested} invested, "
        f"{injury} injury and {off_balance} off-balance = {target}"
    )


def _channel_note(channel: int, passed: tuple[str, ...]) -> str:
    """The actor's Channel as a menu line names it for a casting that goes
    past ``passed`` (:meth:`.magic.MagicRules.lifted_bounds_passed`): past
    it only on a Game Master's lifting (:data:`PAST_BY_RULING`)."""
    if CHANNEL not in passed:
        return f"Channel {channel}"
    return f"past Channel {channel} {PAST_BY_RULING}"


def _borrow_candidates(
    state: BattleState,
    actor: CombatantState,
    magic: MagicRules,
    borrowing: BorrowingRules,
    every_target: bool = False,
) -> list[Candidate]:
    """The borrowing variant of each cast, and each pushed cast, the actor
    cannot afford.

    Built from the menu as it would stand if every spell were affordable
    (:func:`_score_options` with ``beyond_purse``), so a borrowing cast is
    offered exactly where its letter and its situation (hand-to-hand, a
    grapple) allow a cast, and trimmed by Channel as the menu is. A variant
    is offered when the casting, cost and pushed mana together, lies within
    :meth:`.magic.BorrowingRules.borrowing_ceiling`: past the purse, within
    the debt the cap still allows and within the actor's Channel for the
    spell (:meth:`.magic.MagicRules.channel_bound`: with Channel lifted for
    the actor, the debt cap bounds it, or with no cap the actor's mana plus
    its Channel).
    Push's own cap holds unless lifted for the actor too.

    Scored :data:`PLAYER_ONLY_SCORE` and appended last, so they never displace
    the AI's choice: only a player's choice borrows (house reading
    ``ai_never_borrows``). The rationale names the shortfall and the debt
    roll's target as the engine will roll it, before any refund pays some of
    the debt back.
    """
    if not actor.spells:
        return []
    if borrowing.max_debt is not None and actor.debt >= borrowing.max_debt:
        return []
    menu = _score_options(
        state, actor, beyond_purse=True, every_target=every_target
    )
    menu = _within_channel(actor, menu, magic)
    no_borrowing = f"the AI does not borrow = {score_text(PLAYER_ONLY_SCORE)}"
    cap_lifted = bound_lifted(actor, PUSH_CAP)
    borrowed: list[Candidate] = []
    for candidate in menu.candidates:
        if not candidate.spell_key:
            continue
        spell = get_spell(candidate.spell_key)
        channel = magic.channel_for(actor, spell)
        bound = magic.channel_bound(actor, spell, borrowing=True)
        ceiling = borrowing.borrowing_ceiling(actor.mana, actor.debt, bound)
        if actor.mana < spell.level <= ceiling:
            shortfall = spell.level - actor.mana
            borrowed.append(
                Candidate(
                    candidate.letter,
                    f"{candidate.name} (BORROW {shortfall} MANA)",
                    PLAYER_ONLY_SCORE,
                    f"{_borrow_note(actor, borrowing, spell.level)}; "
                    f"{candidate.rationale}; {no_borrowing}",
                    target_id=candidate.target_id,
                    spell_key=candidate.spell_key,
                    borrow=True,
                    lifted_bounds=candidate.lifted_bounds,
                    forecast=candidate.forecast,
                )
            )
        if magic.push is None:
            continue
        first_short = max(1, actor.mana - spell.level + 1)
        last = magic.push.push_room(spell, ceiling, bound, cap_lifted=cap_lifted)
        for extra_mana in range(first_short, last + 1):
            cost = casting_mana(spell, extra_mana)
            passed = magic.lifted_bounds_passed(actor, spell, extra_mana)
            note = _push_note(actor, spell, magic.push, extra_mana, channel, passed)
            borrowed.append(
                Candidate(
                    candidate.letter,
                    f"{candidate.name} (PUSH {extra_mana} MANA, "
                    f"BORROW {cost - actor.mana} MANA)",
                    PLAYER_ONLY_SCORE,
                    f"{note}; {_borrow_note(actor, borrowing, cost)}; "
                    f"the AI does not push or borrow = "
                    f"{score_text(PLAYER_ONLY_SCORE)}",
                    target_id=candidate.target_id,
                    spell_key=candidate.spell_key,
                    push_mana=extra_mana,
                    borrow=True,
                    lifted_bounds=passed,
                )
            )
    return borrowed


def _borrow_note(actor: CombatantState, borrowing: BorrowingRules, cost: int) -> str:
    """A borrowing cast's shortfall and debt roll as the menu prints it.

    The target is the one the engine will roll against, before any refund
    pays some of the debt back: less the injury band and an owed off-balance
    penalty (:func:`_casting_penalties`).
    """
    shortfall = cost - actor.mana
    owed = actor.debt + shortfall
    injury, off_balance = _casting_penalties(actor)
    target = borrowing.debt_target(actor, owed) - injury - off_balance
    return (
        f"Borrow {shortfall} mana ({cost} in all, {actor.mana} in the pool, "
        f"{owed} owed after): debt roll {borrowing.debt_dice} ≤ "
        f"{borrowing.versus} {borrowing.attribute_total(actor)} less "
        f"{borrowing.penalty_per_debt} x {owed} owed, {injury} injury and "
        f"{off_balance} off-balance = {target}"
    )


def _casting_penalties(actor: CombatantState) -> tuple[int, int]:
    """The injury band and owed off-balance penalty the magic rolls take, as
    the engine takes them: the Tarmar profile's band, the only one the
    injected magic rules run under."""
    injury = TarmarReactions().injury_penalty(actor)
    off_balance = combat_math.OFF_BALANCE_PENALTY if actor.off_balance else 0
    return injury, off_balance


def _score_options(
    state: BattleState,
    actor: CombatantState,
    beyond_purse: bool = False,
    every_target: bool = False,
) -> Decision:
    """Score the actor's legal options and choose the best.

    Returns every scored candidate so the caller can emit a decision event
    with the full deliberation. The choice is the highest score; ties break
    toward the earlier letter, keeping replays deterministic.

    A grappled or grappling actor skips this scoring entirely — their turn
    choice is the fixed default from :func:`_grappled_decision`/
    :func:`_grappler_decision` (module docstring).

    ``beyond_purse`` lists every cast the situation allows, affordable or
    not: the menu :func:`_borrow_candidates` builds its variants from. The
    AI's own menu never sets it.

    ``every_target`` adds each aimed option at every other legal target
    (:func:`choose_option`), after the AI's own; the choice made here is
    then not the AI's, and :func:`choose_option` replaces it.
    """
    if actor.grappled_by is not None:
        return _grappled_decision(state, actor, beyond_purse, every_target)
    if actor.grappling is not None:
        return _grappler_decision(state, actor, beyond_purse, every_target)
    engaged = combat_math.is_engaged(state, actor)
    enemy = nearest_enemy(state, actor)
    adjacent_enemies = [
        other
        for other in state.enemies_of(actor)
        if combat_math.figures_adjacent(actor, other)
    ]
    # Beasts land here with no missile weapon and no spells (adaptation gives
    # them neither), so their legal letters are the melee-only subset —
    # a/b/c plus j/k/n when engaged.
    # Beasts have no hands to grapple with — the melee-only subset above
    # already excludes them from missiles/spells for the same reason. A
    # grapple, like any HTH, needs one of "Entering Hand-to-Hand"'s
    # conditions against its target (#823).
    hth_targets = [] if actor.is_beast else _hth_targets(state, actor)
    # Inside hand-to-hand the HTH table is the whole menu (tarmar-engine #19).
    in_hth = movement.in_hand_to_hand(state, actor)
    partners = _hth_partners(state, actor) if in_hth else []
    can_grapple = engaged and bool(hth_targets)
    melee_weapon = weapons.has_melee_weapon(actor)
    in_reach = weapons_in_reach(state, actor)
    spares = [spare for spare in actor.spare_weapons if spare.item_id]
    letters = actions.legal_actions(
        engaged=engaged,
        prone=actor.prone,
        has_missile=weapons.can_shoot(actor),
        has_spells=bool(actor.spells) and (actor.mana > 0 or beyond_purse),
        has_melee_target=enemy is not None and not actor.weapon.is_missile,
        can_grapple=can_grapple,
        can_run=actor.move_run > 0,
        can_sprint=actor.move_sprint > 0,
        has_melee_weapon=melee_weapon,
        has_last_shot=engaged
        and weapons.is_fired_missile(actor.weapon)
        and actor.weapon.reload_turns_left == 0
        and not actor.last_shot_spent,
        can_strike_hth=bool(hth_targets)
        and (actor.weapon.item_id == "" or actor.weapon.hth_usable),
        can_pick_up=not actor.is_beast and bool(in_reach),
        can_change_weapon=engaged
        and not actor.is_beast
        and any(not spare.is_missile for spare in spares),
        can_ready_weapon=not engaged and not actor.is_beast and bool(spares),
        can_drop=not actor.is_beast,
        with_yields=True,
        in_hth=in_hth,
        can_draw_dagger=not actor.weapon.hth_usable
        and any(spare.hth_usable for spare in actor.spare_weapons),
    )
    # A figure left without the weapon its fighting needs — bare hands after
    # a fumble or a throw, or a bow once engaged — wants to rearm (#780).
    needs_weapon = not actor.is_beast and (
        actor.weapon.item_id == "" or (engaged and actor.weapon.is_missile)
    )

    hurt = _hurt_fraction(actor) < HURT_THRESHOLD
    candidates: list[Candidate] = []
    for letter in letters:
        name = actions.option_name(letter)
        if letter in ("g", "p"):
            candidates.append(
                Candidate(
                    letter,
                    name,
                    STAND_UP_SCORE,
                    f"Prone: must stand up, flat = {score_text(STAND_UP_SCORE)}",
                )
            )
        elif letter == "a":
            if enemy is None:
                candidates.append(
                    Candidate(
                        letter, name, 0.0, "No enemies remain to close on = 0.000"
                    )
                )
            else:
                distance = combat_math.figure_distance(actor, enemy)
                # The jog the engine would charge at: a figure barred from
                # jogging charges at its walk (tarmar-engine #17).
                jog = movement.gait_allowance(actor, "jog")
                run_gait = movement.MOVEMENT_GAITS["a"]
                run = movement.gait_allowance(actor, run_gait)
                # The enemy to close on: the nearest one a clear path gets
                # nearer to, not a nearer one it cannot (#37).
                nearable = None
                if distance > 1:
                    nearable = next(
                        (
                            (other, now)
                            for other, now, approach in _approaches(
                                state, actor, run_gait
                            )
                            if approach.distance < now
                        ),
                        None,
                    )
                target, target_distance = nearable or (enemy, distance)
                passed_over = (
                    f" ({enemy.name}, {distance} hexes off, cannot be neared)"
                    if target is not enemy
                    else ""
                )
                distance = target_distance
                # Each band says which side of the line the distance fell on,
                # so a bare 0.5 is no longer an unexplained half (#301).
                if distance <= 1:
                    score = 0.0
                    band = "already adjacent, so moving gains nothing"
                elif nearable is None:
                    # Blocked from every enemy: the move the engine would
                    # make gets no nearer, so it would stand still (#37).
                    score = 0.0
                    band = (
                        f"no clear path within a {run}-hex run gets nearer "
                        "to any enemy, so moving gains nothing"
                    )
                elif distance <= jog:
                    score = round_score(MOVE_BASE_SCORE / 2)
                    band = (
                        f"within a {jog}-hex jog, so half of "
                        f"{score_text(MOVE_BASE_SCORE)}"
                    )
                else:
                    score = round_score(MOVE_BASE_SCORE)
                    band = (
                        f"beyond a {jog}-hex jog, so the full "
                        f"{score_text(MOVE_BASE_SCORE)}"
                    )
                candidates.append(
                    Candidate(
                        letter,
                        name,
                        score,
                        f"Close the {distance} hexes to {target.name}"
                        f"{passed_over}: {band} = {score_text(score)}",
                        target_id=target.combatant_id,
                    )
                )
        elif letter == "b" and enemy is not None:
            distance = combat_math.figure_distance(actor, enemy)
            jog_gait = movement.MOVEMENT_GAITS["b"]
            jog = movement.gait_allowance(actor, jog_gait)
            # The charge's reach is where the engine's own approach ends, so a
            # figure blocked short of its target is not sent at it turn after
            # turn to fall short; it charges the nearest enemy it can reach
            # (#37).
            target = enemy if distance <= 1 else None
            arrival = distance
            if target is None:
                for index, (other, _now, approach) in enumerate(
                    _approaches(state, actor, jog_gait)
                ):
                    if index == 0:
                        arrival = approach.distance
                    if approach.distance <= 1:
                        target = other
                        break
            if target is None:
                target = enemy
                score = 0.0
                rationale = (
                    f"{enemy.name} is {combat_math.hexes_text(distance)} away and "
                    f"a {jog}-hex jog along a clear path ends "
                    f"{combat_math.hexes_text(arrival)} from it, out of charge "
                    f"reach, as is every other enemy = {score_text(score)}"
                )
                score, rationale = _beast_caution(actor, hurt, score, rationale)
                candidates.append(_charge(letter, target, score, rationale))
            else:
                # The nearest enemy, when the charge passes it over.
                passed_over = None if target is enemy else enemy
                candidates.append(
                    _charge_candidate(actor, target, passed_over, hurt, letter)
                )
                if every_target:
                    # Every other enemy the jog ends beside, nearest first, by
                    # the reach test the AI's own pick above makes. Branch
                    # 41-public-channel-approach-and-charge-helpers (commit
                    # 8300730, #50) replaces that pick's `approach.distance
                    # <= 1` with movement.charge_reaches(approach); the batch
                    # merge makes this test the same call.
                    candidates.extend(
                        _charge_candidate(actor, other, passed_over, hurt, letter)
                        for other, now, approach in _approaches(
                            state, actor, jog_gait
                        )
                        if other is not target
                        and (now <= 1 or approach.distance <= 1)
                    )
        elif letter == "c":
            missile_threats = [
                other for other in state.enemies_of(actor) if other.weapon.is_missile
            ]
            score = product_of(DODGE_BASE_SCORE, len(missile_threats))
            # DODGE moves "Jog or less" (movement.md, #819): the AI closes on
            # the nearest missile threat while dodging it.
            closing_on = min(
                missile_threats,
                key=lambda other: (
                    combat_math.figure_distance(actor, other),
                    other.combatant_id,
                ),
                default=None,
            )
            # The move it says it makes is the one the engine will make: a
            # threat no clear jog gets nearer to is dodged standing (#37).
            movement_note = ""
            if closing_on is not None:
                jog = movement.gait_allowance(actor, movement.MOVEMENT_GAITS["c"])
                nearer = movement.approach_route(
                    state, actor, closing_on, jog
                ).distance < combat_math.figure_distance(actor, closing_on)
                movement_note = (
                    f", jogging toward {closing_on.name}"
                    if nearer
                    else f", standing, since no clear jog gets nearer to "
                    f"{closing_on.name}"
                )
            candidates.append(
                Candidate(
                    letter,
                    name,
                    score,
                    f"+4 TN vs missiles; {len(missile_threats)} missile "
                    f"threat(s) x {score_text(DODGE_BASE_SCORE)} each "
                    f"= {score_text(score)}{movement_note}",
                    target_id=None if closing_on is None else closing_on.combatant_id,
                )
            )
        elif letter == "f" and enemy is not None:
            # A hand weapon that can be thrown (dagger, spear, mace) is this
            # figure's only one: the AI keeps it rather than end the turn
            # bare-handed. Still on the menu for a player (#812).
            keeps_it = weapons.throw_leaves_no_hand_weapon(actor)
            # The AI shoots the nearest enemy; the every-target menu any.
            for defender in _aimed_at(enemies_by_distance(state, actor), every_target):
                forecast, rationale = _attack_forecast(actor, defender, ranged=True)
                score = forecast.score
                if keeps_it:
                    score = PLAYER_ONLY_SCORE
                    rationale = (
                        f"{rationale}; but the {actor.weapon.name} is the only "
                        f"hand weapon carried, so the AI keeps it = "
                        f"{score_text(score)}"
                    )
                candidates.append(
                    Candidate(
                        letter,
                        name,
                        score,
                        f"Shoot {defender.name}: {rationale}",
                        target_id=defender.combatant_id,
                        forecast=forecast,
                    )
                )
        elif letter == "j" and adjacent_enemies:
            strikes = []
            for defender in adjacent_enemies:
                forecast, rationale = _attack_forecast(actor, defender, ranged=False)
                score, rationale = _beast_caution(
                    actor, hurt, forecast.score, rationale
                )
                strikes.append(
                    Candidate(
                        letter,
                        name,
                        score,
                        f"Strike {defender.name}: {rationale}",
                        target_id=defender.combatant_id,
                        forecast=forecast,
                    )
                )
            candidates.extend(_best_first(strikes, every_target))
        elif letter == "k":
            # Scaled by the credible incoming melee threat, and deliberately
            # NOT scaled up when hurt: with no healing or reinforcements,
            # mutual turtling is a stalemate machine. Against opponents who
            # can barely dent you, defending is worth almost nothing and the
            # AI keeps swinging instead.
            incoming = _incoming_melee_threat(state, actor)
            threat_scale = min(1.0, incoming / 2.0)
            score = product_of(DEFEND_BASE_SCORE, threat_scale)
            rationale = (
                f"+4 TN vs melee; {score_text(DEFEND_BASE_SCORE)} base "
                f"x {score_text(threat_scale)} threat scale "
                f"({score_text(incoming)} expected incoming damage / 2, "
                f"capped at 1.000) = {score_text(score)}"
            )
            if actor.is_beast and hurt:
                # The one exception to the no-turtling stance above: a
                # badly wounded beast (body gauge) guards itself.
                score = round_score(score + WOUNDED_BEAST_DEFEND_BONUS)
                rationale += (
                    f", + {score_text(WOUNDED_BEAST_DEFEND_BONUS)} wounded beast "
                    f"guarding itself = {score_text(score)}"
                )
            candidates.append(Candidate(letter, name, score, rationale))
        elif letter == "n":
            urgency = 4 if hurt else 1
            score = product_of(DISENGAGE_BASE_SCORE, urgency)
            # The step the engine expects to take with no hex named, straight
            # back from the first adjacent enemy, then either flank, printed
            # in the forecast. The engine works it out when the figure acts,
            # so the candidate names no hex (#20).
            step = (
                movement.step_away_hex(state, actor, adjacent_enemies[0])
                if adjacent_enemies
                else None
            )
            where = f" toward {step}" if step is not None else ""
            candidates.append(
                Candidate(
                    letter,
                    name,
                    score,
                    f"Step away{where} instead of attacking: "
                    f"{score_text(DISENGAGE_BASE_SCORE)} base x {urgency} "
                    f"({'hurt' if hurt else 'unhurt'}) = {score_text(score)}",
                )
            )
        elif letter == "r" and in_hth:
            candidates.extend(_hth_casts(state, actor, beyond_purse, every_target))
        elif letter in ("h", "r"):
            candidates.extend(
                _cast_candidates(state, actor, letter, beyond_purse, every_target)
            )
        elif letter == "u" and in_hth:
            candidates.append(
                Candidate(
                    letter,
                    name,
                    PLAYER_ONLY_SCORE,
                    "Roll 3d6 ≤ DEX to ready a carried dagger; the AI does not "
                    f"= {score_text(PLAYER_ONLY_SCORE)}",
                )
            )
        elif letter == "v" and partners:
            candidates.append(_hth_disengage_candidate(state, actor, partners))
        elif letter == "o" and hth_targets:
            # Deterministic target pick (lowest id among the enemies an HTH
            # entry condition admits, #823) — score is always 0, so this
            # candidate never wins a comparison against a real attack; it
            # only exists to show the AI considered and declined it.
            candidates.extend(
                Candidate(
                    letter,
                    name,
                    GRAPPLE_ATTEMPT_SCORE,
                    f"Grapple valued at 0 ({GRAPPLE_TACTICS_RATIONALE})",
                    target_id=target.combatant_id,
                )
                for target in _aimed_at(hth_targets, every_target)
            )

        elif letter == "sprint" and enemy is not None:
            candidates.append(
                Candidate(
                    letter,
                    actions.option_name(letter),
                    PLAYER_ONLY_SCORE,
                    f"Sprint up to {actor.move_sprint} hexes toward {enemy.name} "
                    f"for 6 Fatigue; the AI keeps to the run = "
                    f"{score_text(PLAYER_ONLY_SCORE)}",
                    target_id=enemy.combatant_id,
                )
            )
        elif letter == "d":
            candidates.append(
                Candidate(
                    letter,
                    name,
                    PLAYER_ONLY_SCORE,
                    f"Go prone where you stand; the AI does not = "
                    f"{score_text(PLAYER_ONLY_SCORE)}",
                )
            )
        elif letter == "l" and enemy is not None:
            # The AI shoots the adjacent enemy with the lowest id; the
            # every-target menu each adjacent enemy.
            shot_targets = sorted(
                adjacent_enemies or [enemy],
                key=lambda other: other.combatant_id,
            )
            for shot_target in _aimed_at(shot_targets, every_target):
                forecast, rationale = _attack_forecast(
                    actor, shot_target, ranged=True
                )
                candidates.append(
                    Candidate(
                        letter,
                        name,
                        forecast.score,
                        f"One last shot at {shot_target.name}, the bow ready "
                        f"before the engagement: {rationale}",
                        target_id=shot_target.combatant_id,
                        forecast=forecast,
                    )
                )
        elif letter == "t" and hth_targets:
            strikes = []
            for defender in hth_targets:
                forecast, rationale = _hth_strike_forecast(state, actor, defender)
                strikes.append(
                    Candidate(
                        letter,
                        name,
                        forecast.score,
                        f"Strike {defender.name} {rationale}",
                        target_id=defender.combatant_id,
                        forecast=forecast,
                    )
                )
            candidates.extend(_best_first(strikes, every_target))
        elif letter in ("e", "m", "q"):
            pool = {
                "e": spares,
                "m": [spare for spare in spares if not spare.is_missile],
                "q": in_reach,
            }[letter]
            choice = best_weapon(actor, pool)
            if choice is None:
                continue
            score = REARM_SCORE if needs_weapon else PLAYER_ONLY_SCORE
            reason = (
                f"rearm with the {choice.name}"
                if needs_weapon
                else f"the {choice.name} is on offer but the hand is not empty"
            )
            candidates.append(
                Candidate(
                    letter,
                    name,
                    score,
                    f"{reason} = {score_text(score)}",
                )
            )
        elif actions.is_yielded(letter):
            # The yielded copy of the option, at each target it is offered at.
            bases = [
                candidate
                for candidate in candidates
                if candidate.letter == actions.base_option(letter)
            ]
            candidates.extend(
                Candidate(
                    letter,
                    actions.option_name(letter),
                    base.score,
                    f"As {base.name}, moving in Final Movement instead: "
                    f"{base.rationale}",
                    target_id=base.target_id,
                    spell_key=base.spell_key,
                    destination=base.destination,
                    forecast=base.forecast,
                )
                for base in _aimed_at(bases, every_target)
            )

    if not candidates:
        candidates = [Candidate("a", "MOVE", 0.0, "Nothing else is legal")]
    chosen = max(candidates, key=lambda candidate: candidate.score)
    return Decision(chosen=chosen, candidates=candidates)
