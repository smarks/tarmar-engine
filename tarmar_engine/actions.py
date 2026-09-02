"""The action-options catalog (``action-options.md``), letter for letter.

The three tables in the rules markdown are mirrored here as data so the
drift-guard test can compare the letters and names against the document, the
same way tarmar-studio's ``characters/tests/test_combat.py`` guards the §6
matrix. The engine
implements the subset in :data:`IMPLEMENTED`.

Issue #231 lights up ``o``/``t``/``v`` for the HTH grapple sub-flow: ``o``
ATTEMPT HTH is implemented as an in-place grapple attempt (see
``tarmar_engine.hexes`` module docstring for why it never actually shares a
hex), and ``t``/``v`` only fire inside that sub-flow — a grappled figure's
Strike Back and Struggle Free. A plain standalone unarmed strike (``t`` on
its own, outside a grapple) and ``o``'s entry preconditions (back-to-wall/
prone/rear/agreement) remain unimplemented, as does ``u`` DRAW DAGGER
(equipment-ready state the simulator does not model, same as ``e``/``m``/
``q``).

``hand-to-hand-and-grappling.md`` itself introduces a further turn-choice
vocabulary once a grapple is live — "Struggle Free"/"Strike Back"/"Hold
still" for the captive, "Maintain"/"Squeeze"/"Release" for the captor — as
bold prose bullets, not a lettered table row. action-options.md's
Hand-to-Hand Combat table was never updated with letters for these (a
content gap noted here, not invented around — see
:data:`GRAPPLED_ACTIONS`/:data:`GRAPPLER_ACTIONS` below). "Struggle Free"
and "Strike Back" explicitly reuse the documented "v"/"t" rolls, so they keep
those real letters; the rest are keyed by their own tokens rather than a
minted letter.
"""

# Letter -> option name, exactly as action-options.md prints them.
DISENGAGED_OPTIONS: dict[str, str] = {
    "a": "MOVE",
    "b": "CHARGE ATTACK",
    "c": "DODGE",
    "d": "DROP",
    "e": "READY WEAPON",
    "f": "MISSILE ATTACK",
    "g": "STAND UP",
    "h": "CAST SPELL",
    "i": "DISBELIEVE",
}

ENGAGED_OPTIONS: dict[str, str] = {
    "j": "ATTACK",
    "k": "DEFEND",
    "l": "ONE LAST SHOT",
    "m": "CHANGE WEAPON",
    "n": "DISENGAGE",
    "o": "ATTEMPT HTH",
    "p": "STAND UP",
    "q": "PICK UP WEAPON",
    "r": "CAST SPELL",
    "s": "DISBELIEVE",
}

HTH_OPTIONS: dict[str, str] = {
    "t": "HTH ATTACK",
    "u": "DRAW DAGGER",
    "v": "DISENGAGE",
}

ALL_OPTIONS: dict[str, str] = {**DISENGAGED_OPTIONS, **ENGAGED_OPTIONS, **HTH_OPTIONS}

# The options the v1 engine actually executes. The rest are legal in the
# rules but have no engine behaviour yet: d/e/i/l/m/q/s need equipment
# swapping or illusions the simulator does not model. o/t/v are implemented
# only for the grapple sub-flow (module docstring); a standalone unarmed
# strike (plain t) and o's entry preconditions remain unimplemented, and u
# DRAW DAGGER is equipment-ready state like e/m/q.
IMPLEMENTED: frozenset[str] = frozenset(
    {"a", "b", "c", "f", "g", "h", "j", "k", "n", "o", "p", "r", "t", "v"}
)

# hand-to-hand-and-grappling.md's own turn-choice vocabulary for a live
# grapple. Keyed by the rules' bold prose terms (module docstring explains
# the letter gap); "Struggle Free"/"Strike Back" keep the real "v"/"t"
# letters since the page explicitly says they reuse those rolls.
GRAPPLED_ACTIONS: dict[str, str] = {
    "v": "STRUGGLE FREE",
    "t": "STRIKE BACK",
    "hold_still": "HOLD STILL",
}

# The grappler's own turn choices, same gap, same convention.
GRAPPLER_ACTIONS: dict[str, str] = {
    "maintain": "MAINTAIN",
    "squeeze": "SQUEEZE",
    "release": "RELEASE",
}


def legal_actions(
    *,
    engaged: bool,
    prone: bool,
    has_missile: bool,
    has_spells: bool,
    has_melee_target: bool,
    can_grapple: bool = False,
) -> list[str]:
    """The implemented action letters legal for an actor's situation.

    Prone figures must stand (g/p) — movement.md counts all their hexes as
    rear, and v1 gives them no crawling attacks. Missile attacks need a
    missile weapon and no engagement; casting needs a castable spell.
    ``can_grapple`` (engaged, an adjacent target, not already grappled or
    grappling) appends "o" ATTEMPT HTH last, after every scored-higher
    option, so a tie never picks it over plain ATTACK.

    A live grapple's turn choices (:data:`GRAPPLED_ACTIONS`/
    :data:`GRAPPLER_ACTIONS`) are not returned here — they replace this
    function's whole vocabulary for a grappled or grappling actor, handled
    directly by ``battle.policy.choose_option``.
    """
    if prone:
        return ["p" if engaged else "g"]
    if engaged:
        letters = ["j", "k", "n"]
        if has_spells:
            letters.append("r")
        if can_grapple:
            letters.append("o")
        return letters
    letters = ["a", "c"]
    if has_melee_target:
        letters.insert(1, "b")
    if has_missile:
        letters.append("f")
    if has_spells:
        letters.append("h")
    return letters
