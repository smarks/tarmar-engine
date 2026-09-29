"""The action-options catalog (``action-options.md``), letter for letter.

The three tables in the rules markdown are mirrored here as data so the
drift-guard test can compare the letters and names against the document, the
same way tarmar-studio's ``characters/tests/test_combat.py`` guards the §6
matrix. The engine
implements the subset in :data:`IMPLEMENTED`.

Issue #231 lit up ``o``/``t``/``v`` for the HTH grapple sub-flow: ``o``
ATTEMPT HTH is implemented as an in-place grapple attempt (see
``tarmar_engine.hexes`` module docstring for why it never actually shares a
hex). The 2026-09-29 rules pass (tarmar-studio #776–#826) added the rest:
``o``'s entry conditions, a standalone ``t`` strike, ``u`` DRAW DAGGER,
``e``/``m``/``q`` weapon handling over carried and dropped weapons, ``l``
ONE LAST SHOT, ``d`` DROP, the Sprint gait, and yielded movement. Only
``i``/``s`` DISBELIEVE remain, needing illusions the simulator lacks.

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

# The options the engine executes. i/s (DISBELIEVE) need illusions the
# simulator does not model. o/t/v are HTH options: o needs one of "Entering
# Hand-to-Hand"'s conditions (tarmar-studio #823), t is a bare-handed or
# dagger strike at HTH range (#815), and v is a grappled figure's Struggle
# Free. u DRAW DAGGER is offered to a grappled figure with a dagger to draw.
IMPLEMENTED: frozenset[str] = frozenset(
    {
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "j",
        "k",
        "l",
        "m",
        "n",
        "o",
        "p",
        "q",
        "r",
        "t",
        "u",
        "v",
    }
)

# movement.md's Speed table lets option a MOVE at Run *or* Sprint ("Run/
# Sprint", action-options.md). The letter is one; the gaits are two, and a
# figure chooses between them (tarmar-studio #818). "sprint" is option a at
# the Sprint gait, keyed by its own token as the grapple vocabularies are.
MOVE_VARIANTS: dict[str, str] = {"sprint": "MOVE (SPRINT)"}

# turn-sequence.md: "any combatant may hold their Initial Movement and move
# in Final Movement instead" (tarmar-studio #819). A yielded option is the
# same option with its movement taken in phase 4; its key is the option's
# key plus this suffix. Offered for the options whose movement the engine
# derives toward a target: MOVE (both gaits), CHARGE ATTACK and DODGE.
YIELD_SUFFIX = "_yield"
YIELDABLE: tuple[str, ...] = ("a", "sprint", "b", "c")


def yield_key(key: str) -> str:
    """The yielded variant of a movement option."""
    return f"{key}{YIELD_SUFFIX}"


def base_option(key: str) -> str:
    """The option a key performs: a yielded variant's own option, else itself."""
    if key.endswith(YIELD_SUFFIX):
        return key[: -len(YIELD_SUFFIX)]
    return key


def is_yielded(key: str) -> bool:
    """Does this key take its movement in Final Movement?"""
    return key.endswith(YIELD_SUFFIX)


def option_name(key: str) -> str:
    """The printed name of any option key the menu can carry."""
    base = base_option(key)
    name = MOVE_VARIANTS.get(base) or ALL_OPTIONS.get(base) or base.upper()
    if is_yielded(key):
        return f"{name} (YIELD: MOVE IN FINAL MOVEMENT)"
    return name


# hand-to-hand-and-grappling.md's own turn-choice vocabulary for a live
# grapple. Keyed by the rules' bold prose terms (module docstring explains
# the letter gap); "Struggle Free"/"Strike Back" keep the real "v"/"t"
# letters since the page explicitly says they reuse those rolls.
GRAPPLED_ACTIONS: dict[str, str] = {
    "v": "STRUGGLE FREE",
    "t": "STRIKE BACK",
    "hold_still": "HOLD STILL",
}

# What else a held figure may do, by the same page: draw a dagger to strike
# back with (u, "Roll 3d6 ≤ DEX to ready dagger") and cast a spell it can
# cast with no gestures and no incantation (Spell Mastery 3, tarmar-studio
# #821/#822). Offered beside the three above when the figure can.
GRAPPLED_EXTRA_ACTIONS: dict[str, str] = {
    "u": "DRAW DAGGER",
    "r": "CAST SPELL",
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
    can_run: bool = True,
    can_sprint: bool = False,
    has_melee_weapon: bool = True,
    has_last_shot: bool = False,
    can_strike_hth: bool = False,
    can_pick_up: bool = False,
    can_change_weapon: bool = False,
    can_ready_weapon: bool = False,
    can_drop: bool = False,
    with_yields: bool = False,
) -> list[str]:
    """The implemented option keys legal for an actor's situation.

    Prone figures must stand (g/p) — movement.md counts all their hexes as
    rear, and the engine gives them no crawling attacks. Missile attacks
    need a missile or throwable weapon ready and no engagement; casting
    needs a castable spell. ``can_grapple`` (engaged, an adjacent target an
    HTH entry condition admits, not already grappled or grappling) appends
    "o" ATTEMPT HTH after every scored-higher option, so a tie never picks
    it over plain ATTACK.

    Every keyword after ``can_grapple`` arrived with the 2026-09-29 rules
    pass, and each defaults to the menu the engine offered before it, so a
    caller that does not pass one sees no change:

    * ``can_run`` (default True) — option a needs the Run gait (#778).
    * ``can_sprint`` — the Sprint gait, "sprint" (#818).
    * ``has_melee_weapon`` (default True) — j and b are "Melee attack
      (non-missile)": bare hands are HTH (t), a bow is no club (#815).
    * ``has_last_shot`` — l ONE LAST SHOT, engaged with a missile weapon
      "ready before engaged" (#776).
    * ``can_strike_hth`` — t HTH ATTACK against an enemy an HTH entry
      condition admits, bare-handed or with a dagger (#815/#823).
    * ``can_pick_up`` / ``can_change_weapon`` / ``can_ready_weapon`` — q, m
      and e (#780).
    * ``can_drop`` — d DROP, disengaged and standing.
    * ``with_yields`` — append the yielded variant of every movement option
      on the menu (#819).

    New keys are appended after the historic ones, so a menu scored as
    before ties the way it did before.

    A live grapple's turn choices (:data:`GRAPPLED_ACTIONS`/
    :data:`GRAPPLER_ACTIONS`) are not returned here — they replace this
    function's whole vocabulary for a grappled or grappling actor, handled
    directly by ``battle.policy.choose_option``.
    """
    if prone:
        return ["p" if engaged else "g"]
    if engaged:
        letters = ["k", "n"]
        if has_melee_weapon:
            letters.insert(0, "j")
        if has_spells:
            letters.append("r")
        if can_grapple:
            letters.append("o")
        if has_last_shot:
            letters.append("l")
        if can_strike_hth:
            letters.append("t")
        if can_change_weapon:
            letters.append("m")
        if can_pick_up:
            letters.append("q")
        return letters
    letters = ["c"]
    if can_run:
        letters.insert(0, "a")
    if has_melee_target and has_melee_weapon:
        letters.insert(1 if can_run else 0, "b")
    if has_missile:
        letters.append("f")
    if has_spells:
        letters.append("h")
    if can_sprint:
        letters.append("sprint")
    if can_drop:
        letters.append("d")
    if can_strike_hth:
        letters.append("t")
    if can_ready_weapon:
        letters.append("e")
    if can_pick_up:
        letters.append("q")
    if with_yields:
        letters += [yield_key(key) for key in YIELDABLE if key in letters]
    return letters
