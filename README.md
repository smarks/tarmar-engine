# tarmar-engine

The shared Tarmar turn engine: the six-phase battle loop (initiative, movement,
attacks, injury, spells) with its action catalog, combat math, spell
definitions, and the utility AI policy — pure Python, no Django anywhere in
the import chain.

Seeded from tarmar-studio's `battle/engine/` (plus `battle/spells.py` and
`battle/policy.py`) in tarmar-studio #240 milestone 1, as a clean copy taken
at tarmar-studio commit `98d80213` with imports repointed at the shared
packages; the git history of these mechanics up to that point lives in the
tarmar-studio repo.

## Rules profiles

Structural mechanics are governed by **rules profiles** (`tarmar_engine.profile`,
tarmar-studio #240 milestone 2) — melee's proven `Ruleset` seam ported up a
level, from resolution mechanics to turn structure. A `RulesProfile` bundles
six areas, each a small swappable component:

| Area | Seam | Tarmar default | Melee-style variant (structure only) |
|---|---|---|---|
| Turn structure | `RulesProfile.phases` / `run_turn` | the six-phase `TurnRunner` | melee's four-phase turn (movement, adjDX-ordered attacks, forced retreats, end of turn), run by the classic profile |
| Resolution | `resolution_policy.ResolutionPolicy` | d20 roll-over vs the TN matrix (`tarmar-rules`, unchanged) | classic 3d6 roll-under adjDX; four dice vs dodge (missiles) / defend (melee); the p.10 special totals |
| Option catalog | `options.OptionCatalog` | the lettered `actions.py` tables, gait movement caps | melee's taxonomy: contexts, fraction-of-MA caps, attack/dodge/defend/cast flags |
| Facing/engagement | `engagement.EngagementRules` | size-band thresholds, multi-hex auto-engage | one-directional front-hex engagement; downed figures engage no one; large figures need two engagers |
| Forced retreat | `retreat.ForcedRetreatRules` | dealt-and-untouched pushes the chosen target; blocked victim saves 3d6 ≤ DEX | per-target push entitlements armed by melee damage only, spent per push, optional advance, no save |
| Reactions to injury | `reactions.InjuryReactions` | pools ≤ 0 fell; deep-below-zero survival saves | hit-count wound/knockdown thresholds and pool death lines — **all injected**, no rulebook numbers |
| Grapple/HTH | `profile.GrappleRules` | movement lock, grappled/grappler vocabularies, HTH +4 | hooks only (classic HTH is milestone 4 scope) |
| Reactions data | `reactions.HitCountReactions` thresholds | — | `classic.data.classic_reactions()`: 5+ hits wound (-2 DX), 8+ knock down, ST 0 fells, ST -1 kills, ST ≤ 3 lasting -3 |

Arc classification is deliberately **shared, not profiled**: both games split
the six directions front/side/rear identically and award +2 side / +4 rear,
so `hexes.arc_of` serves every profile.

`profile.TARMAR` is the default everywhere (`run_turn` without a profile is
the Tarmar profile, bit-for-bit — the milestone-1 suite passes unchanged).

### The classic Melee profile (milestone 3)

`get_profile("classic-melee")` lazily loads `tarmar_engine.classic` — the
**segregated** subpackage holding everything SJG-derived: the rulebook data
(`classic/data.py` is the one data module — weapon/armor/shield tables,
Section III constants, injury thresholds, the special to-hit totals) and the
classic combat machinery ported from the melee project's engine (figure,
arena, facing, ruleset, `classic.state.GameState`). The profile wires the
shared melee-structure seam components with those numbers, resolves by
`ClassicResolution` (3d6 roll-under), and its `run_turn` drives the classic
`GameState` through the four-phase turn.

Its acceptance bar is the rulebook's nine-turn Combat Example, imported
verbatim from melee (`tests/test_combat_example.py` — expectations untouched,
only imports adapted), plus melee's facing/retreat/reaction edge-case tests.
Watch it run:

```bash
uv run pytest tests/test_combat_example.py -v
```

Per the unification plan's copyright note, no Tarmar-canon module imports the
classic subpackage (a guard test enforces it); the classic data never leaks
into the shared mechanics. Still in melee only (later milestones): classic
hand-to-hand piles, shield rush, spells, practice bouts.

## What it sits on

- **hexarena** — hex geometry: coordinates, facing arcs, range bands,
  reachability (`tarmar_engine.hexes` is the engine-flavoured layer over it).
- **tarmar-rules** — the drift-guarded d20 resolution core
  (`tarmar_engine.resolution` is the engine's face of it, plus the §8
  Hybrid-armour decomposition helpers).

Both are tag-pinned git dependencies in `pyproject.toml`; bump a pin by
editing the `@vX.Y.Z` ref in a commit CI tests.

## The boundary

The engine never imports Django. A consuming game adapts its models to the
`state` dataclasses at its own boundary (tarmar-studio: `battle/adaptation.py`),
injects a roller with the `common.rolling.Roller` interface, and receives
every event — per action, per roll — through a sink callback. Weapon and
armour data arrive as `weapon_class`/`armour_tier` strings; catalogs stay in
the games.

## Spec snapshot

`tarmar_engine/spec/` vendors the rules markdown the mechanics are
drift-guarded against (source of truth:
`tarmar-studio/reference/content/public-rules/`). This package's
`tests/test_rules_drift.py` guards code against the snapshot; tarmar-studio
guards the snapshot against its live rules text, so an edit to either side
without the other fails loudly somewhere.

## Development

```bash
uv sync          # installs the tag-pinned deps + dev group
uv run pytest
uv run ruff check .
uv run pyright
```

Releases are tags: bump `[project] version`, tag `vX.Y.Z` on the same commit
(the tag-version-guard workflow rejects a mismatch), and consumers bump their
pin.
