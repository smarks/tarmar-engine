"""Lossless snapshot serialization for the classic :class:`~.state.GameState`.

The profile seam runs each profile over its own state type, and only one of
the two could be persisted: :class:`tarmar_engine.state.BattleState` is a
dataclass with ``to_dict``/``from_dict``, while the classic ``GameState`` — a
mutable object composed from behaviour mixins — had no serialization at all.
A consumer that serves battles out of a database (tarmar-studio holds a
snapshot in ``Battle.state_json`` and re-snapshots it every turn) therefore
could not host a classic battle: nothing to store, and nothing to replay a
turn from. This module gives ``GameState`` the same snapshot contract, so the
state type and its serialization stay together in the profile that owns them.

Ported from melee's ``board/persistence.py``, trimmed to the engine (melee's
Tarmar figure/ruleset and its board game-wrapper stay in melee, which has its
own copies of both), with one deliberate departure noted below.

What round-trips
----------------
Everything a resumed turn needs:

* the arena (dimensions, name, walls) and the ruleset identity;
* the turn number, the combat type, the per-character initiative selection
  state (``initiative_order`` / ``active_index`` / ``passed``), and the
  one-shot victory flag;
* the narrative ``log`` and the dropped-weapons list;
* queued-but-unresolved attacks AND casts, so a snapshot taken mid-combat
  resumes exactly;
* per figure: identity, attributes, gear (catalog entries by name, anything
  else by value), board position/facing/posture, accumulated damage, every
  per-turn flag, the option chosen this turn, the missile cooldown, HTH
  grapple links, and the wizard's identity and lasting-spell records; and
* **the dice stream** — both the scripted queue and the underlying RNG state.

That last one is the departure from melee, whose save/load deliberately
restarted the random stream on every load ("a tabletop fight draws fresh dice
every roll anyway"). A database-backed consumer cannot accept that: turn
rewind, deterministic resume, and validating a remote player's submitted
choice against a replay of the menu all require the restored battle to draw
the *same* future rolls. The RNG state is ``random.Random.getstate()``, which
is stable for a given Mersenne Twister state version; a payload carrying no
dice state (or one written by a different version) simply loads with a fresh
source, which is melee's old behaviour rather than a failure.

What does NOT round-trip (deliberate)
-------------------------------------
The per-turn observational trails — ``spell_results``, ``damage_events``,
``applied_results`` — and the ``_same_side_hit_ok`` flag. All four are
write-only records the rules never read back (the flag is True only inside a
single synchronous HTH cascade, never across a snapshot boundary), and
``end_turn`` clears the trails anyway. They are named in
:data:`OMITTED_STATE_ATTRIBUTES`, and a drift-guard test asserts every
``GameState`` attribute is either serialized or listed there — so a new piece
of state cannot appear without a decision about whether it survives.

Catalog weapons, armour and shields are referenced by name and restored as
the shared singletons, so a restored figure's ``ready_weapon`` is the same
object as the matching entry in its ``weapons`` list — preserving the identity
comparisons the engine relies on.
"""

from __future__ import annotations

import dataclasses
import random

from hexarena.dice import Dice
from hexarena.hex import Hex

from .arena import Arena
from .data import (
    ARMORS,
    SHIELDS,
    STAFF,
    WEAPONS,
    Armor,
    DamageDice,
    Weapon,
    WeaponKind,
)
from .experience import CombatType
from .figure import (
    CARRY_OVER_STATE,
    MONSTER_FIELDS,
    PER_TURN_FLAGS,
    Figure,
    Posture,
    Race,
)
from .options import Option
from .ruleset import Ruleset
from .spells import SPELLS
from .state import GameState, PendingAttack, PendingCast

SCHEMA_VERSION = 1

#: The classic ruleset, and any subclass a consumer registers. A ``Ruleset``
#: subclass swaps real mechanics (to-hit, damage, injury, movement), so a
#: snapshot names the one it was played under and a reload restores exactly
#: that: silently falling back to the base ruleset would change the fight.
CLASSIC_RULESET_NAME = "classic"
RULESETS: dict[str, Ruleset] = {CLASSIC_RULESET_NAME: Ruleset()}


def register_ruleset(name: str, ruleset: Ruleset) -> None:
    """Register a ``Ruleset`` subclass so states using it can be snapshotted."""
    RULESETS[name] = ruleset


# The weapon catalog for by-name round-tripping. The wizard's staff is
# deliberately absent from ``WEAPONS`` (only wizards may carry one, and that
# dict is exactly what a fighter build may pick), but it is still a shared
# singleton worth restoring as itself rather than rebuilding by value.
_WEAPON_CATALOG: dict[str, Weapon] = {**WEAPONS, STAFF.name: STAFF}


# ---- arena ------------------------------------------------------------------
def arena_to_json(arena: Arena) -> dict:
    return {
        "cols": arena.cols,
        "rows": arena.rows,
        "name": arena.name,
        "walls": sorted([wall.col, wall.row] for wall in arena.walls),
    }


def arena_from_json(data: dict) -> Arena:
    arena = Arena(cols=data["cols"], rows=data["rows"], name=data.get("name", "arena"))
    arena.walls = {Hex(col, row) for col, row in data.get("walls", [])}
    return arena


# ---- dice -------------------------------------------------------------------
def dice_to_json(dice: Dice) -> dict:
    """The dice source as JSON: the unconsumed scripted queue plus the RNG state.

    Reaching for ``Dice``'s internals is deliberate. The alternative — teaching
    hexarena's ``Dice`` to serialize itself — puts a persistence concern in the
    geometry/dice library every game shares, and would need a hexarena release
    and dependency bump before a classic battle could be stored. If that
    contract is ever wanted upstream, this function is the shape to lift.
    """
    rng_state = dice._rng.getstate()
    return {
        "scripted": list(dice._scripted),
        # getstate() is (version, 625 ints, gauss_next); JSON has no tuples.
        "rng_version": rng_state[0],
        "rng_state": list(rng_state[1]),
        "rng_gauss_next": rng_state[2],
    }


def dice_from_json(data: dict | None) -> Dice:
    """Rebuild a dice source. ``None`` (or a state from a different Mersenne
    Twister version) yields a fresh random stream with the scripted queue
    intact — a resumed fight then rolls new dice rather than refusing to load.
    """
    if data is None:
        return Dice()
    dice = Dice(scripted=data.get("scripted") or [])
    stored_state = data.get("rng_state")
    current_version = random.Random().getstate()[0]
    if stored_state is not None and data.get("rng_version") == current_version:
        dice._rng.setstate(
            (data["rng_version"], tuple(stored_state), data.get("rng_gauss_next")))
    return dice


# ---- gear -------------------------------------------------------------------
def _damage_to_json(damage: DamageDice | None) -> list[int] | None:
    return None if damage is None else [damage.count, damage.modifier]


def _damage_from_json(value: list[int] | None) -> DamageDice | None:
    return None if value is None else DamageDice(value[0], value[1])


def weapon_to_json(weapon: Weapon) -> str | dict:
    """A catalog weapon by name (restored as the shared singleton); anything
    else — a monster's ad-hoc natural attack — by value, so it round-trips
    instead of raising ``KeyError``."""
    if _WEAPON_CATALOG.get(weapon.name) is weapon:
        return weapon.name
    return {
        "name": weapon.name,
        "damage": _damage_to_json(weapon.damage),
        "min_strength": weapon.min_strength,
        "kind": weapon.kind.value,
        "two_handed": weapon.two_handed,
        "hth_damage": _damage_to_json(weapon.hth_damage),
        "throwable": weapon.throwable,
        "notes": weapon.notes,
        "reload": weapon.reload,
        "fast_reload_dx": weapon.fast_reload_dx,
        "double_shot_dx": weapon.double_shot_dx,
        "reach": weapon.reach,
    }


def weapon_from_json(value: str | dict) -> Weapon:
    if isinstance(value, str):
        return _WEAPON_CATALOG[value]
    damage = _damage_from_json(value["damage"])
    if damage is None:
        raise ValueError(f"weapon {value['name']!r} has no damage dice")
    return Weapon(
        name=value["name"],
        damage=damage,
        min_strength=value["min_strength"],
        kind=WeaponKind(value["kind"]),
        two_handed=value["two_handed"],
        hth_damage=_damage_from_json(value["hth_damage"]),
        throwable=value["throwable"],
        notes=value["notes"],
        reload=value["reload"],
        fast_reload_dx=value["fast_reload_dx"],
        double_shot_dx=value["double_shot_dx"],
        reach=value["reach"],
    )


def _armor_to_json(armor: Armor) -> str | dict:
    """A catalog armour by name; a creature's natural hide by value."""
    if ARMORS.get(armor.name) is armor:
        return armor.name
    return {
        "name": armor.name,
        "stops": armor.stops,
        "movement_allowance": armor.movement_allowance,
        "dx_penalty": armor.dx_penalty,
    }


def _armor_from_json(value: str | dict) -> Armor:
    if isinstance(value, str):
        return ARMORS[value]
    return Armor(
        name=value["name"],
        stops=value["stops"],
        movement_allowance=value["movement_allowance"],
        dx_penalty=value["dx_penalty"],
    )


def _resolve_ready_weapon(
    ready_spec: str | dict | None, weapons: list[Weapon]
) -> Weapon | None:
    """The readied weapon as the SAME object already in ``weapons`` (the identity
    the engine relies on for ``ready_weapon in figure.weapons``)."""
    if ready_spec is None:
        return None
    ready_name = ready_spec if isinstance(ready_spec, str) else ready_spec["name"]
    for carried in weapons:
        if carried.name == ready_name:
            return carried
    return weapon_from_json(ready_spec)


# ---- figures ----------------------------------------------------------------
# The carry-over state and the monster/quirk fields both come from the single
# canonical source in .figure (CARRY_OVER_STATE, MONSTER_FIELDS), shared with
# every other consumer of those enumerations so the snapshot and the mid-fight
# edit paths preserve the same fields and cannot drift (melee #359, #369). Each
# monster field round-trips only when present, so an older snapshot loads at the
# dataclass defaults.
def _field_default(figure_field: dataclasses.Field) -> object:
    """A dataclass field's default, resolving a ``default_factory`` (a fresh
    list/dict for the wizard ``spells_known`` / ``active_spells`` carry-over)."""
    if figure_field.default is not dataclasses.MISSING:
        return figure_field.default
    if figure_field.default_factory is not dataclasses.MISSING:
        return figure_field.default_factory()
    return None


_CARRY_OVER_DEFAULTS: dict[str, object] = {
    figure_field.name: _field_default(figure_field)
    for figure_field in dataclasses.fields(Figure)
    if figure_field.name in CARRY_OVER_STATE
}

# Fail at import (not with a confusing AttributeError at runtime) if a shared
# enumeration names a field the figure dataclass no longer has (a rename).
_FIGURE_FIELD_NAMES = {field.name for field in dataclasses.fields(Figure)}
_SHARED_FIGURE_FIELDS = (
    set(CARRY_OVER_STATE) | set(MONSTER_FIELDS) | set(PER_TURN_FLAGS)
)
if not _SHARED_FIGURE_FIELDS <= _FIGURE_FIELD_NAMES:
    raise RuntimeError(
        "figure persistence names unknown fields: "
        f"{sorted(_SHARED_FIGURE_FIELDS - _FIGURE_FIELD_NAMES)}"
    )


def figure_to_json(figure: Figure) -> dict:
    return {
        "name": figure.name,
        "char_class": figure.char_class,
        "side": figure.side,
        "uid": figure.uid,
        "strength": figure.strength,
        "dexterity": figure.dexterity,
        "race": figure.race.value,
        "armor": _armor_to_json(figure.armor),
        "shield": figure.shield.name,
        "weapons": [weapon_to_json(weapon) for weapon in figure.weapons],
        "ready_weapon": (weapon_to_json(figure.ready_weapon)
                         if figure.ready_weapon else None),
        "shield_ready": figure.shield_ready,
        # Nonhuman quirks (Section VIII): size, flight, injury thresholds.
        **{name: getattr(figure, name) for name in MONSTER_FIELDS},
        # ---- mutable fight state ----
        "position": ([figure.position.col, figure.position.row]
                     if figure.position is not None else None),
        "facing": figure.facing,
        "posture": figure.posture.value,
        "damage_taken": figure.damage_taken,
        **{flag: getattr(figure, flag) for flag in PER_TURN_FLAGS},
        # Plain carry-over fight state (wounds/consciousness/death, dropped_out,
        # missile cooldown, HTH dagger, XP, wizard identity and magic).
        **{name: getattr(figure, name) for name in CARRY_OVER_STATE},
        "current_option": (figure.current_option.value
                           if figure.current_option is not None else None),
        "hth_opponents": list(figure.hth_opponents),
    }


def figure_from_json(data: dict) -> Figure:
    weapons = [weapon_from_json(spec) for spec in data["weapons"]]
    # Reuse the catalog singleton (or the just-rebuilt non-catalog instance) so
    # ``ready_weapon is weapons[i]`` holds, matching the identity comparisons in
    # .state (e.g. ``ready in figure.weapons``).
    figure = Figure(
        name=data["name"],
        strength=data["strength"],
        dexterity=data["dexterity"],
        side=data["side"],
        armor=_armor_from_json(data["armor"]),
        shield=SHIELDS[data["shield"]],
        weapons=weapons,
        ready_weapon=_resolve_ready_weapon(data["ready_weapon"], weapons),
        shield_ready=data["shield_ready"],
        race=Race(data["race"]),
        char_class=data.get("char_class", ""),
    )
    figure.uid = data["uid"]
    position = data["position"]
    figure.position = Hex(position[0], position[1]) if position is not None else None
    figure.facing = data["facing"]
    figure.posture = Posture(data["posture"])
    figure.damage_taken = data["damage_taken"]
    for flag, default in PER_TURN_FLAGS.items():
        stored = data.get(flag, default)
        # Copy list values so a reloaded figure owns its list outright — never a
        # shared alias of the PER_TURN_FLAGS default nor of a decoded structure.
        setattr(figure, flag, list(stored) if isinstance(default, list) else stored)
    for name in CARRY_OVER_STATE:
        stored = data.get(name, _CARRY_OVER_DEFAULTS[name])
        # Copy the mutable carry-over values (the wizard's spells_known list /
        # active_spells dict) for the same reason.
        if isinstance(stored, list):
            stored = list(stored)
        elif isinstance(stored, dict):
            stored = {key: (dict(value) if isinstance(value, dict) else value)
                      for key, value in stored.items()}
        setattr(figure, name, stored)
    # An older snapshot stored ``active_spells`` as {spell_id: ST invested};
    # duration bookkeeping made each value a record. Normalize a legacy int into
    # the record shape — no countdown was stored (only continuing protection
    # spells existed), so it reloads as a continuing spell cast by its wearer.
    figure.active_spells = {
        spell_id: (value if isinstance(value, dict)
                   else {"st": value, "remaining": None, "caster": data["uid"]})
        for spell_id, value in figure.active_spells.items()
    }
    option = data["current_option"]
    figure.current_option = Option(option) if option is not None else None
    figure.hth_opponents = list(data["hth_opponents"])
    # Quirk traits: restore only what the snapshot carries, so an older one keeps
    # the ordinary single-hex human defaults.
    for name in MONSTER_FIELDS:
        if name in data:
            setattr(figure, name, data[name])
    return figure


# ---- queued attacks ---------------------------------------------------------
# Serialization is driven off the PendingAttack dataclass itself so a newly
# added field can never silently drop from a mid-combat snapshot (the drift that
# caused melee #245, where shield_rush/weapon/second_target/charge_resolve_first
# were omitted and rebuilt at their defaults on reload — turning a queued
# shield-rush into a full damaging weapon attack). Fields referencing live
# objects need special handling; every other field is treated as a JSON-safe
# scalar automatically, so an addition is persisted by default and, if it is not
# JSON-safe, fails loudly at ``json.dumps`` rather than vanishing.
_PENDING_FIGURE_FIELDS = ("attacker", "target", "second_target")
_PENDING_WEAPON_FIELDS = ("weapon",)
_PENDING_DAMAGE_FIELDS = ("hth_damage",)
_PENDING_SPECIAL_FIELDS = (
    _PENDING_FIGURE_FIELDS + _PENDING_WEAPON_FIELDS + _PENDING_DAMAGE_FIELDS
)
_PENDING_FIELD_NAMES = tuple(field.name for field in dataclasses.fields(PendingAttack))
_PENDING_SCALAR_FIELDS = tuple(
    name for name in _PENDING_FIELD_NAMES if name not in _PENDING_SPECIAL_FIELDS
)
_PENDING_SCALAR_DEFAULTS = {
    field.name: field.default
    for field in dataclasses.fields(PendingAttack)
    if field.name in _PENDING_SCALAR_FIELDS
}

if not set(_PENDING_SPECIAL_FIELDS) <= set(_PENDING_FIELD_NAMES):
    raise RuntimeError(
        "PendingAttack persistence names unknown fields: "
        f"{sorted(set(_PENDING_SPECIAL_FIELDS) - set(_PENDING_FIELD_NAMES))}"
    )


def pending_attack_to_json(pending: PendingAttack) -> dict:
    payload: dict = {
        name: getattr(pending, name) for name in _PENDING_SCALAR_FIELDS
    }
    for name in _PENDING_FIGURE_FIELDS:
        figure = getattr(pending, name)
        payload[name] = figure.uid if figure is not None else None
    for name in _PENDING_WEAPON_FIELDS:
        weapon = getattr(pending, name)
        payload[name] = weapon_to_json(weapon) if weapon is not None else None
    for name in _PENDING_DAMAGE_FIELDS:
        payload[name] = _damage_to_json(getattr(pending, name))
    return payload


def pending_attack_from_json(data: dict, by_uid: dict[str, Figure]) -> PendingAttack:
    kwargs: dict = {}
    for name in _PENDING_SCALAR_FIELDS:
        default = _PENDING_SCALAR_DEFAULTS[name]
        # Required scalars were always persisted; optional ones fall back to the
        # dataclass default so an older snapshot still loads.
        kwargs[name] = (data[name] if default is dataclasses.MISSING
                        else data.get(name, default))
    for name in _PENDING_FIGURE_FIELDS:
        uid = data.get(name)
        kwargs[name] = by_uid[uid] if uid is not None else None
    for name in _PENDING_WEAPON_FIELDS:
        stored = data.get(name)
        kwargs[name] = weapon_from_json(stored) if stored is not None else None
    for name in _PENDING_DAMAGE_FIELDS:
        kwargs[name] = _damage_from_json(data.get(name))
    return PendingAttack(**kwargs)


# ---- queued casts -----------------------------------------------------------
# The cast mirror of the machinery above (melee #420), driven off the PendingCast
# dataclass for the same reason. Figures ride by uid; the spell rides by its
# catalog id.
_PENDING_CAST_FIGURE_FIELDS = ("caster", "target")
_PENDING_CAST_SPELL_FIELDS = ("spell",)
_PENDING_CAST_SPECIAL_FIELDS = (
    _PENDING_CAST_FIGURE_FIELDS + _PENDING_CAST_SPELL_FIELDS
)
_PENDING_CAST_FIELD_NAMES = tuple(
    field.name for field in dataclasses.fields(PendingCast))
_PENDING_CAST_SCALAR_FIELDS = tuple(
    name for name in _PENDING_CAST_FIELD_NAMES
    if name not in _PENDING_CAST_SPECIAL_FIELDS
)
_PENDING_CAST_SCALAR_DEFAULTS = {
    field.name: field.default
    for field in dataclasses.fields(PendingCast)
    if field.name in _PENDING_CAST_SCALAR_FIELDS
}

if not set(_PENDING_CAST_SPECIAL_FIELDS) <= set(_PENDING_CAST_FIELD_NAMES):
    raise RuntimeError(
        "PendingCast persistence names unknown fields: "
        f"{sorted(set(_PENDING_CAST_SPECIAL_FIELDS) - set(_PENDING_CAST_FIELD_NAMES))}"
    )


def pending_cast_to_json(pending: PendingCast) -> dict:
    payload: dict = {
        name: getattr(pending, name) for name in _PENDING_CAST_SCALAR_FIELDS
    }
    for name in _PENDING_CAST_FIGURE_FIELDS:
        figure = getattr(pending, name)
        payload[name] = figure.uid if figure is not None else None
    for name in _PENDING_CAST_SPELL_FIELDS:
        spell = getattr(pending, name)
        payload[name] = spell.id if spell is not None else None
    return payload


def pending_cast_from_json(data: dict, by_uid: dict[str, Figure]) -> PendingCast:
    kwargs: dict = {}
    for name in _PENDING_CAST_SCALAR_FIELDS:
        default = _PENDING_CAST_SCALAR_DEFAULTS[name]
        kwargs[name] = (data[name] if default is dataclasses.MISSING
                        else data.get(name, default))
    for name in _PENDING_CAST_FIGURE_FIELDS:
        uid = data.get(name)
        kwargs[name] = by_uid[uid] if uid is not None else None
    for name in _PENDING_CAST_SPELL_FIELDS:
        spell_id = data.get(name)
        kwargs[name] = SPELLS[spell_id] if spell_id is not None else None
    return PendingCast(**kwargs)


# ---- the game state ---------------------------------------------------------
#: Every ``GameState`` instance attribute this module persists.
SERIALIZED_STATE_ATTRIBUTES: frozenset[str] = frozenset({
    "arena", "figures", "dice", "rules", "combat_type", "turn_number", "log",
    "_pending", "_pending_casts", "initiative_order", "active_index", "passed",
    "dropped", "_victory_announced",
})

#: The attributes deliberately left out (see the module docstring): write-only
#: audit trails the rules never read back, cleared at ``end_turn``, plus the
#: same-side flag that is only ever True inside one synchronous cascade.
OMITTED_STATE_ATTRIBUTES: frozenset[str] = frozenset({
    "spell_results", "damage_events", "applied_results", "_same_side_hit_ok",
})


def _ruleset_name(ruleset: Ruleset) -> str:
    for name, registered in RULESETS.items():
        if type(registered) is type(ruleset):
            return name
    raise ValueError(
        f"ruleset {type(ruleset).__name__} is not registered, so a snapshot "
        "could not restore it; call register_ruleset(name, ruleset) first"
    )


def state_to_json(state: GameState) -> dict:
    """Serialize a :class:`~.state.GameState` to a JSON-safe ``dict``."""
    return {
        "version": SCHEMA_VERSION,
        "ruleset": _ruleset_name(state.rules),
        "combat_type": state.combat_type.value,
        "arena": arena_to_json(state.arena),
        "turn_number": state.turn_number,
        "initiative_order": list(state.initiative_order),
        "active_index": state.active_index,
        "passed": list(state.passed),
        "victory_announced": getattr(state, "_victory_announced", False),
        "dice_state": dice_to_json(state.dice),
        "figures": [figure_to_json(figure) for figure in state.figures],
        "dropped": [
            {"col": where.col, "row": where.row, "weapon": weapon_to_json(weapon)}
            for where, weapon in state.dropped
        ],
        "pending": [pending_attack_to_json(pending) for pending in state._pending],
        "pending_casts": [
            pending_cast_to_json(pending) for pending in state._pending_casts
        ],
        "log": list(state.log),
    }


def state_from_json(data: dict) -> GameState:
    """Rebuild a :class:`~.state.GameState` from :func:`state_to_json` output."""
    figures = [figure_from_json(figure) for figure in data["figures"]]
    state = GameState(
        arena_from_json(data["arena"]),
        figures,
        dice=dice_from_json(data.get("dice_state")),
        ruleset=RULESETS[data["ruleset"]],
        combat_type=CombatType(data.get("combat_type", CombatType.DEATH.value)),
    )
    state.turn_number = data["turn_number"]
    state.initiative_order = list(data.get("initiative_order", []))
    state.active_index = data.get("active_index", 0)
    state.passed = list(data.get("passed", []))
    state.log = list(data.get("log", []))
    if data.get("victory_announced"):
        state._victory_announced = True
    state.dropped = [
        (Hex(entry["col"], entry["row"]), weapon_from_json(entry["weapon"]))
        for entry in data.get("dropped", [])
    ]
    by_uid = {figure.uid: figure for figure in figures}
    state._pending = [
        pending_attack_from_json(pending, by_uid) for pending in data.get("pending", [])
    ]
    state._pending_casts = [
        pending_cast_from_json(pending, by_uid)
        for pending in data.get("pending_casts", [])
    ]
    return state
