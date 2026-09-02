"""Drift guards: the engine's tables must match the rules markdown.

Same pattern as ``characters/tests/test_combat.py``'s §6 guard — the rules
documents are authoritative, and an edit to either side without the other
fails loudly here.
"""

import re
from pathlib import Path
from unittest import TestCase

import tarmar_engine
from tarmar_engine import actions, engine, hexes
from tarmar_engine.spells import SPELLS
from tarmar_engine.state import BARE_HANDED_DAMAGE_TABLE

RULES = Path(tarmar_engine.__file__).resolve().parent / "spec"
COMBAT = RULES / "combat"


def _table_rows(markdown: str, heading: str) -> list[list[str]]:
    """The cell texts of the pipe table under ``heading`` (## or #)."""
    section = markdown.split(heading, 1)[1]
    rows = []
    for line in section.splitlines():
        stripped = line.strip()
        if rows and not stripped.startswith("|"):
            if any(cell for row in rows for cell in row):
                break
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= {"-", " "} for cell in cells):
            continue  # separator row
        rows.append(cells)
    return rows


class TurnSequenceDriftGuard(TestCase):
    def test_phase_numbers_and_names_match_turn_sequence_md(self):
        markdown = (COMBAT / "turn-sequence.md").read_text()
        rows = _table_rows(markdown, "# Turn Sequence")[1:]  # drop header
        documented = [(int(row[0]), row[1].strip("* ").strip()) for row in rows]
        self.assertEqual(documented, list(engine.PHASES))

    def test_initiative_row_pins_the_per_combatant_ruling(self):
        # Spencer's 2026-08-31 ruling (issue #199): initiative is 1d6 +
        # adjDEX per combatant, ordered by descending total with ties to the
        # higher adjDEX, then stable order. The engine's phase_initiative
        # implements exactly this; the old "winner chooses to move first or
        # second" clause is superseded and must not resurface.
        markdown = (COMBAT / "turn-sequence.md").read_text()
        rows = _table_rows(markdown, "# Turn Sequence")[1:]
        initiative_row = rows[0]
        self.assertEqual(initiative_row[2], "1d6 + adjDEX per combatant")
        self.assertEqual(
            initiative_row[3],
            "Descending total; ties: higher adjDEX, then stable order",
        )
        self.assertIn("supersede", markdown)
        self.assertNotIn("Winner chooses", markdown)


class ActionOptionsDriftGuard(TestCase):
    def _lettered_options(self, heading: str) -> dict[str, str]:
        markdown = (COMBAT / "action-options.md").read_text()
        rows = _table_rows(markdown, heading)[1:]
        options = {}
        for row in rows:
            letter = row[0]
            if letter:  # unlettered rows (CRAWL, the HTH cast line) skipped
                options[letter] = row[1]
        return options

    def test_disengaged_letters_match_action_options_md(self):
        self.assertEqual(
            self._lettered_options("## Disengaged Figures"),
            actions.DISENGAGED_OPTIONS,
        )

    def test_engaged_letters_match_action_options_md(self):
        self.assertEqual(
            self._lettered_options("## Engaged Figures"),
            actions.ENGAGED_OPTIONS,
        )

    def test_hth_letters_match_action_options_md(self):
        self.assertEqual(
            self._lettered_options("## Hand-to-Hand Combat"),
            actions.HTH_OPTIONS,
        )

    def test_implemented_letters_are_all_documented(self):
        self.assertLessEqual(actions.IMPLEMENTED, set(actions.ALL_OPTIONS))


class DexAdjustmentsDriftGuard(TestCase):
    def test_positional_bonuses_match_dex_adjustments_md(self):
        markdown = (COMBAT / "action-options" / "dex-adjustments.md").read_text()
        rows = _table_rows(markdown, "## Positional Advantage")[1:]
        bonuses = {row[0]: row[1] for row in rows}
        self.assertEqual(
            bonuses["Striking from enemy's side hex"], f"+{hexes.SIDE_HEX_BONUS}"
        )
        self.assertEqual(
            bonuses["Striking from enemy's rear hex"], f"+{hexes.REAR_HEX_BONUS}"
        )

    def test_missile_range_bands_match_dex_adjustments_md(self):
        markdown = (COMBAT / "action-options" / "dex-adjustments.md").read_text()
        rows = _table_rows(markdown, "## Range (Missile Weapons)")[1:]
        for row in rows:
            band_match = re.fullmatch(r"(\d+)–(\d+) MH", row[0])
            if not band_match:
                continue  # the "(continues)" row
            low, high = int(band_match.group(1)), int(band_match.group(2))
            documented = int(row[1].replace("−", "-"))
            for megahexes in range(max(low, 1), high + 1):
                distance = megahexes * hexes.HEXES_PER_MEGAHEX_STEP
                self.assertEqual(
                    hexes.missile_range_penalty(distance),
                    documented,
                    f"{megahexes} MH should be {documented}",
                )

    def test_thrown_range_is_minus_one_per_hex(self):
        markdown = (COMBAT / "action-options" / "dex-adjustments.md").read_text()
        self.assertIn("−1 to hit per hex to target", markdown)
        self.assertEqual(hexes.thrown_range_penalty(4), -4)


class SpecialSituationsDriftGuard(TestCase):
    def test_defend_dodge_bonus_matches_special_combat_situations_md(self):
        markdown = (
            COMBAT / "action-options" / "special-combat-situations.md"
        ).read_text()
        self.assertIn(
            f"+{hexes.DEFEND_DODGE_TN_BONUS}\nto your Target Number", markdown
        )

    def test_bare_handed_damage_matches_special_combat_situations_md(self):
        markdown = (
            COMBAT / "action-options" / "special-combat-situations.md"
        ).read_text()
        rows = _table_rows(markdown, "### Bare-Handed Damage by STR")[1:]
        documented: dict[int, str] = {}
        for row in rows:
            # Two STR/Damage column pairs per row.
            for str_cell, damage_cell in ((row[0], row[1]), (row[2], row[3])):
                top = int(re.findall(r"\d+", str_cell)[-1])
                documented[top] = damage_cell.replace("−", "-")
        self.assertEqual(documented, dict(BARE_HANDED_DAMAGE_TABLE))


class MovementDriftGuard(TestCase):
    def test_megahex_is_seven_hexes(self):
        markdown = (COMBAT / "action-options" / "movement.md").read_text()
        self.assertIn("**Megahex:** 7 hexes (1 center + 6 surrounding)", markdown)
        # A 7-hex cluster spans 3 hexes centre to centre — the step the
        # range-band conversion uses.
        self.assertEqual(hexes.HEXES_PER_MEGAHEX_STEP, 3)

    def test_run_fatigue_cost_matches_movement_md(self):
        markdown = (COMBAT / "action-options" / "movement.md").read_text()
        rows = _table_rows(markdown, "| Speed")
        costs = {row[0]: row[2] for row in rows[1:]}
        self.assertEqual(costs["Run"], f"{engine.RUN_FATIGUE_COST} per turn")
        self.assertEqual(costs["Sprint"], f"{engine.SPRINT_FATIGUE_COST} per turn")


class EngagementDriftGuard(TestCase):
    """movement.md's Engagement table vs the multi-hex engagement code."""

    def _engagement_rows(self):
        markdown = (COMBAT / "action-options" / "movement.md").read_text()
        return _table_rows(markdown, "## Engagement")[1:]

    def test_thresholds_match_the_engagement_table(self):
        for figure_type, condition in self._engagement_rows():
            if figure_type.startswith("One-hex"):
                sizes = [1]
                documented = 1  # "In an armed enemy's front hex"
            else:
                numbers = [int(n) for n in re.findall(r"\d+", figure_type)]
                sizes = list(range(numbers[0], numbers[-1] + 1))
                threshold_match = re.search(r"(\d+)\+", condition)
                assert threshold_match is not None, condition
                documented = int(threshold_match.group(1))
            for size in sizes:
                self.assertEqual(
                    hexes.engagement_threshold(size),
                    documented,
                    f"size {size} should engage at {documented}+ per movement.md",
                )

    def test_a_single_multi_hex_enemy_engages_at_every_size(self):
        # Every multi-hex row carries "(or 1 multi-hex)".
        for figure_type, condition in self._engagement_rows():
            if not figure_type.startswith("One-hex"):
                self.assertIn("(or 1 multi-hex)", condition)
        # And the code honours it: one 3-hex enemy engages even a megahex.
        body = hexes.footprint((0, 0), 0, 7)
        enemy = (hexes.front_hexes((2, 0), 3, 3), 3)
        self.assertTrue(hexes.figure_engaged(body, 7, [enemy]))

    def test_engaged_figures_stop_immediately(self):
        markdown = (COMBAT / "action-options" / "movement.md").read_text()
        self.assertIn("**stop immediately** when engaged", markdown)


class GrappleDriftGuard(TestCase):
    """hand-to-hand-and-grappling.md vs. the engine's grapple sub-flow.

    Same pattern as ``ActionOptionsDriftGuard``/``SpecialSituationsDriftGuard``
    above, but the source is prose (bold bullets and paragraphs), not a
    pipe table — there is no lettered table row for grapple to diff against
    (the gap ``tarmar_engine.actions``'s module docstring notes), so these
    check literal phrase presence plus the numbers/labels the engine must
    match, rather than parsing rows.
    """

    def setUp(self):
        self.markdown = (
            COMBAT / "action-options" / "hand-to-hand-and-grappling.md"
        ).read_text()

    def test_hth_to_hit_bonus_matches_the_page(self):
        self.assertIn("**both combatants get +4**", self.markdown)
        self.assertEqual(hexes.HTH_TO_HIT_BONUS, 4)

    def test_flexible_snare_tn_progression_matches_the_page(self):
        self.assertIn(
            "TN runs 13 (unarmoured) → 16 (Light) → 19 (Medium) → 22 (Heavy)",
            self.markdown,
        )
        from tarmar_rules import MATRIX

        self.assertEqual(
            MATRIX["Flexible / Snare"],
            {"None": 13, "Light": 16, "Medium": 19, "Heavy": 22},
        )

    def test_escape_roll_matches_the_page(self):
        self.assertIn("**roll 4d6 ≤ effective DEX**", self.markdown)

    def test_grappled_options_match_the_page(self):
        for option in ("Struggle Free", "Strike Back", "Hold still"):
            self.assertIn(f"**{option}**", self.markdown)
        self.assertEqual(
            set(actions.GRAPPLED_ACTIONS.values()),
            {"STRUGGLE FREE", "STRIKE BACK", "HOLD STILL"},
        )

    def test_grappler_options_match_the_page(self):
        for option in ("Maintain", "Squeeze", "Release"):
            self.assertIn(f"**{option}**", self.markdown)
        self.assertEqual(
            set(actions.GRAPPLER_ACTIONS.values()),
            {"MAINTAIN", "SQUEEZE", "RELEASE"},
        )

    def test_forced_retreat_exemption_matches_the_page(self):
        self.assertIn(
            "Not available against — or to — a grappled figure", self.markdown
        )

    def test_movement_lock_matches_the_page(self):
        self.assertIn(
            "Neither combatant moves — both are locked to the shared hex",
            self.markdown,
        )

    def test_implemented_grapple_letters_are_documented(self):
        self.assertLessEqual({"o", "t", "v"}, actions.IMPLEMENTED)
        self.assertLessEqual({"o", "t", "v"}, set(actions.ALL_OPTIONS))


class SpellCatalogDriftGuard(TestCase):
    def test_spell_names_and_levels_come_from_schools_of_magic_md(self):
        markdown = (RULES / "magic" / "schools-of-magic.md").read_text()
        for spell in SPELLS.values():
            school_section = markdown.split(f"### {spell.school}", 1)[1].split(
                "### ", 1
            )[0]
            level_lines = {
                int(match.group(1)): match.group(2)
                for match in re.finditer(
                    r"^(\d+)\. (.+)$", school_section, re.MULTILINE
                )
            }
            self.assertIn(spell.level, level_lines, spell.key)
            # The catalog name (bare, or its Elemental variant like "Fire
            # Missile") must appear in that school's line for that level.
            base_name = spell.name.split()[-1]
            self.assertIn(
                base_name,
                level_lines[spell.level],
                f"{spell.name} is not a level-{spell.level} {spell.school} spell",
            )

    def test_mana_cost_equals_spell_level(self):
        # casting-spells.md: "Spells cost mana equal to the spell's level."
        markdown = (RULES / "magic" / "casting-spells.md").read_text()
        self.assertIn("Spells cost mana equal to the spell's level", markdown)
        for spell in SPELLS.values():
            self.assertGreaterEqual(spell.level, 1)

    def test_catalog_size_is_six_to_ten(self):
        self.assertGreaterEqual(len(SPELLS), 6)
        self.assertLessEqual(len(SPELLS), 10)
