"""The classic profile's *cast* decision: a menu of spells, and a pick that sticks.

tarmar-engine#5. v0.7.0 gave the classic profile a decision layer, but its
cast stayed the AI's private business: the menu offered one bare CAST option
with no spell on it, and the combat phase re-derived the spell from
``ai._cast_plan`` — so a spell chosen by anyone other than the AI had no way
through. These pin the two halves that close that gap:

* the menu names each castable spell, from the engine's own
  :meth:`GameState.spell_targets`, so a player is offered exactly what the
  queue would accept;
* enacting one *records* the choice, and the combat phase honours what was
  recorded instead of re-deriving its own.
"""
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# (figures are placed then used, as in the ported classic suites)
from __future__ import annotations

from hexarena.dice import Dice
from hexarena.hex import Hex

from tarmar_engine.classic import persistence, policy
from tarmar_engine.classic.arena import Arena
from tarmar_engine.classic.figure import Figure, create_wizard
from tarmar_engine.classic.options import Option
from tarmar_engine.classic.state import GameState


def _wizard(spells: list[str] | None = None, strength: int = 20) -> Figure:
    """A hands-free wizard at a fixed hex, facing its foe."""
    wizard = create_wizard(
        "Merlin", strength=strength, dexterity=12, intelligence=13, side="red",
        spells_known=spells if spells is not None else ["magic_fist", "stone_flesh"],
    )
    wizard.position = Hex(2, 2)
    wizard.facing = 0
    wizard.uid = "wiz"
    return wizard


def _foe(strength: int = 12) -> Figure:
    dummy = Figure(name="Dummy", strength=strength, dexterity=10, side="blue")
    dummy.position = Hex(4, 2)
    dummy.uid = "dummy"
    return dummy


def _game(*figures: Figure, seed: int = 1) -> GameState:
    return GameState(Arena(cols=12, rows=12), list(figures), dice=Dice(seed=seed))


def _casts(game: GameState, figure: Figure) -> list:
    return [candidate for candidate in policy.menu(game, figure)
            if candidate.letter == Option.CAST.value]


class TestTheCastMenu:
    """One entry per spell the caster could actually get away with."""

    def test_a_castable_spell_gets_its_own_candidate(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        keys = {candidate.spell_key for candidate in _casts(game, wizard)}
        assert keys == {"magic_fist", "stone_flesh"}

    def test_a_cast_candidate_names_the_spell_it_would_cast(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        fist = next(candidate for candidate in _casts(game, wizard)
                    if candidate.spell_key == "magic_fist")
        assert "Magic Fist" in fist.name

    def test_a_missile_spell_aims_at_the_foe(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        fist = next(candidate for candidate in _casts(game, wizard)
                    if candidate.spell_key == "magic_fist")
        assert fist.target_id == foe.uid

    def test_a_self_cast_spell_aims_at_the_caster(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        stone = next(candidate for candidate in _casts(game, wizard)
                     if candidate.spell_key == "stone_flesh")
        assert stone.target_id == wizard.uid

    def test_a_spell_the_caster_cannot_afford_is_not_offered(self) -> None:
        # Stone Flesh is a flat 2 ST; a wizard down to 1 cannot pay it, and the
        # CAST option disappears rather than offering what the queue rejects.
        wizard, foe = _wizard(spells=["stone_flesh"]), _foe()
        game = _game(wizard, foe)
        wizard.damage_taken = wizard.strength - 1
        assert _casts(game, wizard) == []

    def test_a_cast_that_would_empty_the_pool_is_still_offered(self) -> None:
        # The rules let a cast take a wizard to 0 ST; only the AI keeps a
        # reserve back, and its caution must not shrink a player's menu.
        wizard, foe = _wizard(spells=["stone_flesh"]), _foe()
        game = _game(wizard, foe)
        wizard.damage_taken = wizard.strength - 2
        assert [candidate.spell_key for candidate in _casts(game, wizard)] == [
            "stone_flesh"
        ]

    def test_a_fighter_is_offered_no_cast_at_all(self) -> None:
        fighter = Figure(name="Thug", strength=12, dexterity=12, side="red")
        fighter.position, fighter.facing, fighter.uid = Hex(2, 2), 0, "thug"
        game = _game(fighter, _foe())
        assert _casts(game, fighter) == []


class TestTheChoiceSticks:
    """A cast chosen off the menu is the cast that gets queued."""

    def test_enacting_a_cast_records_the_chosen_spell(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        stone = next(candidate for candidate in _casts(game, wizard)
                     if candidate.spell_key == "stone_flesh")
        policy.enact(game, wizard, stone)
        assert wizard.declared_spell_id == "stone_flesh"

    def test_the_combat_phase_queues_the_spell_that_was_chosen(self) -> None:
        # The AI's own pick here is Magic Fist (the missile it prefers); choosing
        # Stone Flesh has to survive into the combat phase, or a player's pick is
        # silently overruled by the heuristics.
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        stone = next(candidate for candidate in _casts(game, wizard)
                     if candidate.spell_key == "stone_flesh")
        policy.enact(game, wizard, stone)
        policy.declare_attacks(game)
        queued = [pending for pending in game._pending_casts
                  if pending.caster is wizard]
        assert [pending.spell.id for pending in queued] == ["stone_flesh"]

    def test_the_ai_s_own_cast_still_goes_through_unchanged(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        decision = policy.choose_option(game, wizard)
        policy.enact(game, wizard, decision.chosen)
        policy.declare_attacks(game)
        assert [pending.spell.id for pending in game._pending_casts] == ["magic_fist"]

    def test_a_declaration_gone_illegal_stands_the_caster_down(self) -> None:
        # A blow lands between selection and combat and the declared spell is
        # no longer affordable. Standing down is melee's own answer to a cast
        # that evaporated (#397/#398) — never a wedged cast gate.
        wizard, foe = _wizard(spells=["stone_flesh"]), _foe()
        game = _game(wizard, foe)
        stone = next(candidate for candidate in _casts(game, wizard)
                     if candidate.spell_key == "stone_flesh")
        policy.enact(game, wizard, stone)
        wizard.damage_taken = wizard.strength - 1
        policy.declare_attacks(game)
        assert game._pending_casts == []

    def test_the_declaration_survives_a_snapshot(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        stone = next(candidate for candidate in _casts(game, wizard)
                     if candidate.spell_key == "stone_flesh")
        policy.enact(game, wizard, stone)
        restored = persistence.state_from_json(persistence.state_to_json(game))
        revived = next(one for one in restored.figures if one.uid == "wiz")
        assert revived.declared_spell_id == "stone_flesh"


class TestOnlyTheCastEntryChanged:
    """Expanding CAST must not disturb the rest of the menu."""

    def test_every_other_option_still_matches_the_engine_s_legality(self) -> None:
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        letters = [candidate.letter for candidate in policy.menu(game, wizard)
                   if candidate.letter != Option.CAST.value]
        assert letters == [option.value for option in game.legal_options(wizard)
                           if option != Option.CAST]

    def test_a_declaration_does_not_outlive_its_turn(self) -> None:
        # A stale declaration would have the wizard re-cast next turn without
        # anyone choosing it; the per-turn reset table is what prevents that.
        wizard, foe = _wizard(), _foe()
        game = _game(wizard, foe)
        stone = next(candidate for candidate in _casts(game, wizard)
                     if candidate.spell_key == "stone_flesh")
        policy.enact(game, wizard, stone)
        game.end_turn()
        assert wizard.declared_spell_id == ""
