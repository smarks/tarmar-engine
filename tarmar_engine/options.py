"""The option-catalog structure — the "what can a figure do" profile area.

Both games hand each figure exactly one option per turn, chosen from a menu
that depends on whether the figure is engaged, and every option bundles a
movement allowance with an action and a handful of flags (is it an attack?
a missile shot? does it dodge, defend, or cast?). That shape is shared
structure — :class:`OptionSpec` / :class:`OptionCatalog` here — while the
entries and their movement vocabulary are profile data:

* :func:`tarmar_catalog` mirrors ``actions.py``'s drift-guarded letter
  tables (the letters stay the catalog keys; ``actions.legal_actions``
  remains the Tarmar profile's situational legality filter). Tarmar's
  movement economy is gait-based (movement.md), so its ``movement_cap``
  tokens name gaits: ``"run"`` (option a), ``"jog"`` (the charge), and
  ``"adjust"`` (the phase-4 walk-slow step for f/h/r).
* :func:`melee_structure_catalog` ports melee's option taxonomy
  (``engine/options.py``) — contexts, fraction-of-MA movement caps, and
  flags. Structure only: option names and caps, no weapon tables or other
  SJG rules data (classic data is milestone 3's segregated module).

:func:`movement_budget` (ported from melee's ``engine/movement.py``)
translates the fraction vocabulary — ``full``/``half``/``two``/``one``/
``none`` — into a hex budget for catalogs that use it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from . import actions

# Option contexts: which menu an option belongs to.
DISENGAGED = "disengaged"
ENGAGED = "engaged"
HTH = "hth"
ANY = "any"
# Turn-flow options injected by a selection pass (melee's DO_NOTHING/PASS):
# never part of the engaged/disengaged menus.
SPECIAL = "special"


@dataclass(frozen=True)
class OptionSpec:
    """One option's structural facts, shared vocabulary across profiles."""

    key: str
    name: str
    context: str
    #: Movement allowance token. The profile interprets it: melee-structure
    #: catalogs use the fraction vocabulary ``movement_budget`` understands;
    #: the Tarmar catalog uses gait tokens ("run"/"jog"/"adjust"/"none").
    movement_cap: str = "none"
    is_attack: bool = False
    is_missile: bool = False
    sets_dodge: bool = False
    sets_defend: bool = False
    casts_spell: bool = False


class OptionCatalog:
    """An ordered, keyed collection of :class:`OptionSpec`."""

    def __init__(self, specs: Iterable[OptionSpec]) -> None:
        self._specs: dict[str, OptionSpec] = {}
        for spec in specs:
            if spec.key in self._specs:
                raise ValueError(f"duplicate option key {spec.key!r}")
            self._specs[spec.key] = spec

    def __contains__(self, key: str) -> bool:
        return key in self._specs

    def keys(self) -> list[str]:
        return list(self._specs)

    def spec(self, key: str) -> OptionSpec:
        return self._specs[key]

    def options_for(self, *, engaged: bool) -> list[str]:
        """The menu for a standing figure: context match plus ANY."""
        wanted = ENGAGED if engaged else DISENGAGED
        return [
            key
            for key, spec in self._specs.items()
            if spec.context in (wanted, ANY)
        ]


def movement_budget(movement_allowance: int, option_cap: str) -> int:
    """Translate a fraction-vocabulary movement cap into a hex budget.

    Ported from melee's ``engine/movement.py``: ``full`` is the whole
    allowance, ``half`` rounds down, ``two``/``one``/``none`` are absolute.
    """
    if option_cap == "full":
        return movement_allowance
    if option_cap == "half":
        return movement_allowance // 2
    if option_cap == "two":
        return 2
    if option_cap == "one":
        return 1
    if option_cap == "none":
        return 0
    raise ValueError(f"unknown movement cap {option_cap!r}")


# Tarmar per-letter facts the letter tables don't carry: gait tokens and
# flags, matching the engine's phase 3/4/5 handling of each letter exactly.
_TARMAR_CAPS: dict[str, str] = {"a": "run", "b": "jog"}
_TARMAR_ADJUST = frozenset({"f", "h", "r"})  # phase-4 walk-slow kite step
_TARMAR_ATTACKS = frozenset({"b", "f", "j", "l", "t"})
_TARMAR_MISSILES = frozenset({"f", "l"})
_TARMAR_CASTS = frozenset({"h", "r"})


def _tarmar_spec(letter: str, name: str, context: str) -> OptionSpec:
    cap = _TARMAR_CAPS.get(letter, "adjust" if letter in _TARMAR_ADJUST else "none")
    return OptionSpec(
        key=letter,
        name=name,
        context=context,
        movement_cap=cap,
        is_attack=letter in _TARMAR_ATTACKS,
        is_missile=letter in _TARMAR_MISSILES,
        sets_dodge=letter == "c",
        sets_defend=letter == "k",
        casts_spell=letter in _TARMAR_CASTS,
    )


def tarmar_catalog() -> OptionCatalog:
    """The lettered Tarmar catalog, built from the drift-guarded tables."""
    specs = [
        _tarmar_spec(letter, name, DISENGAGED)
        for letter, name in actions.DISENGAGED_OPTIONS.items()
    ]
    specs += [
        _tarmar_spec(letter, name, ENGAGED)
        for letter, name in actions.ENGAGED_OPTIONS.items()
    ]
    specs += [
        _tarmar_spec(letter, name, HTH)
        for letter, name in actions.HTH_OPTIONS.items()
    ]
    return OptionCatalog(specs)


def melee_structure_catalog() -> OptionCatalog:
    """Melee's option taxonomy, ported structurally from ``engine/options.py``.

    Keys are melee's ``Option`` enum values; contexts, movement caps, and
    flags carry over one for one. The classic profile (milestone 3) reads
    its menus from this catalog and its numbers from the segregated data
    module.
    """
    entries: tuple[tuple[str, str, str, str, dict], ...] = (
        ("move", "MOVE", DISENGAGED, "full", {}),
        ("half_move", "HALF MOVE", DISENGAGED, "half", {}),
        ("charge_attack", "CHARGE ATTACK", DISENGAGED, "half", {"is_attack": True}),
        ("dodge", "DODGE", DISENGAGED, "half", {"sets_dodge": True}),
        ("ready_weapon", "READY WEAPON", DISENGAGED, "two", {}),
        (
            "missile_attack",
            "MISSILE ATTACK",
            DISENGAGED,
            "one",
            {"is_attack": True, "is_missile": True},
        ),
        ("stand_up", "STAND UP", ANY, "none", {}),
        ("crawl", "CRAWL", ANY, "two", {}),
        ("attack", "ATTACK", ENGAGED, "none", {"is_attack": True}),
        ("shift_attack", "SHIFT AND ATTACK", ENGAGED, "one", {"is_attack": True}),
        ("shift_defend", "SHIFT AND DEFEND", ENGAGED, "one", {"sets_defend": True}),
        (
            "one_last_shot",
            "ONE LAST SHOT",
            ENGAGED,
            "none",
            {"is_attack": True, "is_missile": True},
        ),
        ("change_weapons", "CHANGE WEAPONS", ENGAGED, "one", {}),
        ("disengage", "DISENGAGE", ENGAGED, "one", {}),
        ("hth_attack", "HTH ATTACK", ANY, "one", {"is_attack": True}),
        ("cast", "CAST SPELL", ANY, "one", {"casts_spell": True}),
        ("pick_up", "PICK UP WEAPON", ANY, "none", {}),
        ("go_prone", "GO PRONE", DISENGAGED, "none", {}),
        ("kneel", "KNEEL", DISENGAGED, "none", {}),
        ("do_nothing", "DO NOTHING", SPECIAL, "none", {}),
        ("pass", "PASS", SPECIAL, "none", {}),
    )
    return OptionCatalog(
        OptionSpec(key=key, name=name, context=context, movement_cap=cap, **flags)
        for key, name, context, cap, flags in entries
    )
