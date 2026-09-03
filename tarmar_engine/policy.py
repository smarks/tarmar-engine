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
was on the table. Beasts never attempt a grapple at all (no hands to hold
with); this is consistent with the rest of their melee-only subset. Casting
while grappled is never offered — the engine has no per-spell Spell Mastery
data (hand-to-hand-and-grappling.md's gesture/verbal exemption needs it), so
rather than guess which spells would qualify, both grapple participants are
restricted to their fixed lists, which have no casting option in them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import actions, combat_math
from . import resolution as combat
from .spells import get_spell
from .state import BattleState, CombatantState

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

    def to_payload(self) -> dict:
        return {
            "letter": self.letter,
            "name": self.name,
            "score": round(self.score, 3),
            "rationale": self.rationale,
            "target_id": self.target_id,
            "spell_key": self.spell_key,
        }


@dataclass
class Decision:
    """The chosen candidate plus everything that was considered."""

    chosen: Candidate
    candidates: list[Candidate] = field(default_factory=list)


def three_d6_at_most(target: int) -> float:
    """Exact P(3d6 <= target) — the rules' attribute-check success chance."""
    outcomes = 0
    for first in range(1, 7):
        for second in range(1, 7):
            for third in range(1, 7):
                if first + second + third <= target:
                    outcomes += 1
    return outcomes / 216


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
    return score * fraction, f"{rationale}; wounded and wary"


def nearest_enemy(state: BattleState, actor: CombatantState) -> CombatantState | None:
    """Closest active enemy; ties broken by lowest fatigue, then id."""
    enemies = state.enemies_of(actor)
    if not enemies:
        return None
    return min(
        enemies,
        key=lambda enemy: (
            combat_math.figure_distance(actor, enemy),
            enemy.fatigue,
            enemy.combatant_id,
        ),
    )


def _melee_score(actor: CombatantState, defender: CombatantState) -> tuple[float, str]:
    numbers = combat_math.attack_numbers(actor, defender, ranged=False)
    probability = combat.hit_probability(numbers.target_number, numbers.bonus)
    damage = combat_math.expected_attack_damage(actor, defender)
    score = probability * damage
    rationale = (
        f"P(hit) {probability:.0%} vs TN {numbers.target_number} "
        f"x {damage:.1f} expected damage"
    )
    return score, rationale


def _missile_score(
    actor: CombatantState, defender: CombatantState
) -> tuple[float, str]:
    numbers = combat_math.attack_numbers(actor, defender, ranged=True)
    probability = combat.hit_probability(numbers.target_number, numbers.bonus)
    damage = combat_math.expected_attack_damage(actor, defender)
    score = probability * damage
    rationale = (
        f"P(hit) {probability:.0%} at {numbers.distance} hexes "
        f"(range {numbers.range_penalty:+d}) x {damage:.1f} expected damage"
    )
    return score, rationale


def _cast_candidates(
    state: BattleState, actor: CombatantState, letter: str
) -> list[Candidate]:
    """A candidate per castable spell the actor can afford."""
    candidates = []
    enemy = nearest_enemy(state, actor)
    for key in actor.spells:
        spell = get_spell(key)
        if spell.level > actor.mana:
            continue
        if spell.continuing and key in actor.active_spells:
            continue  # already up; renewal happens in phase 2
        attribute = actor.intelligence if spell.attribute == "INT" else actor.wisdom
        cast_probability = three_d6_at_most(attribute)
        if spell.heals:
            missing = actor.max_fatigue - actor.fatigue
            value = cast_probability * min(missing, 3.5) * HEAL_SCORE_PER_POINT
            rationale = f"P(cast) {cast_probability:.0%}, {missing} fatigue missing"
            candidates.append(
                Candidate(
                    letter, f"CAST SPELL {spell.name}", value, rationale, spell_key=key
                )
            )
            continue
        if spell.damage is None:
            value = cast_probability * BUFF_SCORE
            rationale = f"P(cast) {cast_probability:.0%}, defensive buff"
            candidates.append(
                Candidate(
                    letter, f"CAST SPELL {spell.name}", value, rationale, spell_key=key
                )
            )
            continue
        if enemy is None:
            continue
        stops = 0 if spell.ignores_armour else enemy.stops
        damage = combat_math.expected_damage(spell.damage, stops)
        probability = cast_probability
        if spell.targeted:
            probability *= three_d6_at_most(actor.dexterity)
        value = probability * damage
        rationale = (
            f"P(land) {probability:.0%} x {damage:.1f} expected damage on {enemy.name}"
        )
        candidates.append(
            Candidate(
                letter,
                f"CAST SPELL {spell.name}",
                value,
                rationale,
                target_id=enemy.combatant_id,
                spell_key=key,
            )
        )
    return candidates


def _grappled_decision(state: BattleState, actor: CombatantState) -> Decision:
    """A held figure's fixed turn choice: always Struggle Free.

    hand-to-hand-and-grappling.md's "Being Grappled" options — Struggle
    Free, Strike Back, Hold Still — replace the whole normal candidate set.
    No weighing: escaping always outscores fighting back or waiting, by
    construction (module docstring's "deliberately dumb" grapple AI).
    """
    grappler_id = actor.grappled_by
    candidates = [
        Candidate(
            "v",
            actions.GRAPPLED_ACTIONS["v"],
            1.0,
            f"Always attempts to escape ({GRAPPLE_TACTICS_RATIONALE})",
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
    return Decision(chosen=candidates[0], candidates=candidates)


def _grappler_decision(state: BattleState, actor: CombatantState) -> Decision:
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
    return Decision(chosen=candidates[0], candidates=candidates)


def choose_option(state: BattleState, actor: CombatantState) -> Decision:
    """Score the actor's legal options and choose the best.

    Returns every scored candidate so the caller can emit a decision event
    with the full deliberation. The choice is the highest score; ties break
    toward the earlier letter, keeping replays deterministic.

    A grappled or grappling actor skips this scoring entirely — their turn
    choice is the fixed default from :func:`_grappled_decision`/
    :func:`_grappler_decision` (module docstring).
    """
    if actor.grappled_by is not None:
        return _grappled_decision(state, actor)
    if actor.grappling is not None:
        return _grappler_decision(state, actor)
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
    # already excludes them from missiles/spells for the same reason.
    can_grapple = engaged and not actor.is_beast and bool(adjacent_enemies)
    letters = actions.legal_actions(
        engaged=engaged,
        prone=actor.prone,
        has_missile=actor.weapon.is_missile,
        has_spells=bool(actor.spells) and actor.mana > 0,
        has_melee_target=enemy is not None and not actor.weapon.is_missile,
        can_grapple=can_grapple,
    )

    hurt = _hurt_fraction(actor) < HURT_THRESHOLD
    candidates: list[Candidate] = []
    for letter in letters:
        name = actions.ALL_OPTIONS[letter]
        if letter in ("g", "p"):
            candidates.append(
                Candidate(letter, name, STAND_UP_SCORE, "Prone: must stand up")
            )
        elif letter == "a":
            if enemy is None:
                candidates.append(
                    Candidate(letter, name, 0.0, "No enemies remain to close on")
                )
            else:
                distance = combat_math.figure_distance(actor, enemy)
                if distance <= 1:
                    # Already adjacent — moving accomplishes nothing.
                    score = 0.0
                elif distance <= actor.move_jog:
                    score = MOVE_BASE_SCORE / 2
                else:
                    score = MOVE_BASE_SCORE
                candidates.append(
                    Candidate(
                        letter,
                        name,
                        score,
                        f"Close the {distance} hexes to {enemy.name}",
                        target_id=enemy.combatant_id,
                    )
                )
        elif letter == "b" and enemy is not None:
            score, rationale = _melee_score(actor, enemy)
            distance = combat_math.figure_distance(actor, enemy)
            if distance > actor.move_jog + 1:
                score = 0.0
                rationale = f"{enemy.name} is beyond charge reach ({distance} hexes)"
            score, rationale = _beast_caution(actor, hurt, score, rationale)
            candidates.append(
                Candidate(
                    letter,
                    name,
                    score,
                    f"Charge {enemy.name}: {rationale}",
                    target_id=enemy.combatant_id,
                )
            )
        elif letter == "c":
            missile_threats = [
                other for other in state.enemies_of(actor) if other.weapon.is_missile
            ]
            score = DODGE_BASE_SCORE * len(missile_threats)
            candidates.append(
                Candidate(
                    letter,
                    name,
                    score,
                    f"{len(missile_threats)} missile threat(s); +4 TN vs missiles",
                )
            )
        elif letter == "f" and enemy is not None:
            score, rationale = _missile_score(actor, enemy)
            candidates.append(
                Candidate(
                    letter,
                    name,
                    score,
                    f"Shoot {enemy.name}: {rationale}",
                    target_id=enemy.combatant_id,
                )
            )
        elif letter == "j" and adjacent_enemies:
            best = None
            for defender in adjacent_enemies:
                score, rationale = _melee_score(actor, defender)
                score, rationale = _beast_caution(actor, hurt, score, rationale)
                candidate = Candidate(
                    letter,
                    name,
                    score,
                    f"Strike {defender.name}: {rationale}",
                    target_id=defender.combatant_id,
                )
                if best is None or candidate.score > best.score:
                    best = candidate
            if best is not None:
                candidates.append(best)
        elif letter == "k":
            # Scaled by the credible incoming melee threat, and deliberately
            # NOT scaled up when hurt: with no healing or reinforcements,
            # mutual turtling is a stalemate machine. Against opponents who
            # can barely dent you, defending is worth almost nothing and the
            # AI keeps swinging instead.
            incoming = _incoming_melee_threat(state, actor)
            score = DEFEND_BASE_SCORE * min(1.0, incoming / 2.0)
            rationale = f"+4 TN vs melee; ~{incoming:.1f} expected incoming damage"
            if actor.is_beast and hurt:
                # The one exception to the no-turtling stance above: a
                # badly wounded beast (body gauge) guards itself.
                score += WOUNDED_BEAST_DEFEND_BONUS
                rationale += "; wounded beast guards itself"
            candidates.append(Candidate(letter, name, score, rationale))
        elif letter == "n":
            score = DISENGAGE_BASE_SCORE * (4 if hurt else 1)
            candidates.append(
                Candidate(letter, name, score, "Step away instead of attacking")
            )
        elif letter in ("h", "r"):
            candidates.extend(_cast_candidates(state, actor, letter))
        elif letter == "o" and adjacent_enemies:
            # Deterministic target pick (lowest id) — score is always 0, so
            # this candidate never wins a comparison against a real attack;
            # it only exists to show the AI considered and declined it.
            target = min(adjacent_enemies, key=lambda enemy: enemy.combatant_id)
            candidates.append(
                Candidate(
                    letter,
                    name,
                    GRAPPLE_ATTEMPT_SCORE,
                    f"Grapple valued at 0 ({GRAPPLE_TACTICS_RATIONALE})",
                    target_id=target.combatant_id,
                )
            )

    if not candidates:
        candidates = [Candidate("a", "MOVE", 0.0, "Nothing else is legal")]
    chosen = max(candidates, key=lambda candidate: candidate.score)
    return Decision(chosen=chosen, candidates=candidates)
