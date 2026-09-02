"""tarmar-engine — the shared Tarmar turn engine.

The six-phase Tarmar battle loop (turn sequence, action options, movement,
attack resolution wiring, injury, spells, and the utility AI policy) as a
pure-Python package, seeded verbatim from tarmar-studio's ``battle/engine``
(tarmar-studio #240 milestone 1, extracted at tarmar-studio commit
98d80213). Hex geometry comes from ``hexarena``; d20 resolution from
``tarmar_rules``; this package holds the turn structure between them.

No Django anywhere in the import chain — games adapt their models to the
``state`` dataclasses at their own boundary (tarmar-studio's
``battle/adaptation.py``) and inject a roller with the
``common.rolling.Roller`` interface. Every event and roll is emitted through
a sink callback, so per-action logging stays with the caller.

Rules profiles (six-phase Tarmar vs classic Melee) arrive in milestones 2+
of the unification plan; today the package speaks Tarmar only. The rules
markdown these mechanics are drift-guarded against is vendored under
``spec/`` — tarmar-studio guards that snapshot against its live rules text.
"""

from __future__ import annotations

from . import (  # noqa: F401
    actions,
    combat_math,
    dice,
    engine,
    hexes,
    policy,
    resolution,
    spells,
    state,
)

__all__ = [
    "actions",
    "combat_math",
    "dice",
    "engine",
    "hexes",
    "policy",
    "resolution",
    "spells",
    "state",
]
