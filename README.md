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
| Grapple/HTH | `profile.GrappleRules` | movement lock, grappled/grappler vocabularies, HTH +4 | the full classic HTH pile machinery (`classic.state._HthMixin`, milestone 4) |
| Reactions data | `reactions.HitCountReactions` thresholds | — | `classic.data.classic_reactions()`: 5+ hits wound (-2 DX), 8+ knock down, ST 0 fells, ST -1 kills, ST ≤ 3 lasting -3 |

Arc classification is deliberately **shared, not profiled**: both games split
the six directions front/side/rear identically and award +2 side / +4 rear,
so `hexes.arc_of` serves every profile.

`profile.TARMAR` is the default everywhere (`run_turn` without a profile is
the Tarmar profile, bit-for-bit — the milestone-1 suite passes unchanged).

### The classic Melee profile (milestone 3)

`get_profile("classic-melee")` lazily loads `tarmar_engine.classic` — the
**segregated** subpackage holding everything SJG-derived: the rulebook data
(`classic/data.py` — weapon/armor/shield tables, Section III constants,
injury thresholds, the special to-hit and spell-cast totals — beside the
`classic/spells.py` Wizard spell catalog) and the classic combat machinery
ported from the melee project's engine (figure, arena, facing, ruleset,
`classic.state.GameState`, and — since milestone 4, when melee itself became
a consumer of this package — hand-to-hand piles, the shield rush, the
combat-phase general disengage, practice bouts, Section IX experience, and
the prose narrative layer). The profile wires the
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
into the shared mechanics.

### The classic spell layer (milestone 5)

Since milestone 5 the SPELL layer (TFT: Wizard) lives here natively, under
Spencer's spell-canon ruling: **Tarmar's magic.md-faithful magic stays canon
for the Tarmar profile** (`tarmar_engine/spells.py`, untouched), and **the
14-spell classic Wizard suite is classic-profile-only** — ported verbatim
from melee into the segregated subpackage. `classic/spells.py` carries the
catalog (IQ tiers, ST costs, durations, heavy-target variants, every number
cited against the reference text); the cast flow (target legality, the
declare/resolve queue, missile-spell line-of-flight, dodging's four-dice
shift, fizzles, lasting-spell bookkeeping and expiry) fills the spell hooks
milestone 4 installed (`_pending_casts`/`spell_results`, `_resolve_cast`,
`_expire_active_spells`, `Figure.SPELL_CATALOG` — now bound to the classic
catalog by default); `Ruleset` gains melee's `resolve_spell` composition
method and its mutation hooks; the spell narrations join
`classic/narrative.py`. Melee's spell/wizard tests ride along verbatim
(`tests/test_classic_spells.py`, `test_classic_spell_batch.py`,
`test_classic_staff.py`, `test_classic_wizard_weapons.py`).

### Snapshotting a classic battle

Both state types now carry the same snapshot contract, so a consumer that
serves battles out of a database can hold either profile's state:

```python
snapshot = game.to_dict()          # JSON-safe; store it
game = GameState.from_dict(snapshot)   # resumes exactly, dice included
```

`classic/persistence.py` (ported from melee's `board/persistence.py`, trimmed
to the engine) round-trips the arena, the ruleset identity, the turn and
initiative-selection state, the log, the dropped weapons, the queued attacks
AND casts, and every figure — gear from the catalogs by name, a monster's
ad-hoc weapon or hide by value, the wizard's identity and lasting-spell
records intact. Drift guards assert the persisted key set equals the
dataclass field set for `Figure`, `PendingAttack` and `PendingCast`, and that
every `GameState` attribute is either serialized or in the documented
omission set.

The one departure from melee's save/load: **the dice stream round-trips too**
— the scripted queue and the RNG state both — so a restored battle draws the
same future rolls. Melee restarted the stream on each load; turn rewind,
deterministic resume, and validating a remote player's choice against a
replay of the option menu all need it not to. The write-only audit trails
(`spell_results`, `damage_events`, `applied_results`) are deliberately left
out; the rules never read them back and `end_turn` clears them.

### Deciding a classic turn

A classic turn can now be *shown* before it is *taken* — what a database-backed
UI needs, and what melee's board never had to separate. `classic/policy.py`
splits the decision three ways, over the classic heuristics ported into
`classic/ai.py` from melee's `engine/ai.py`:

```python
from tarmar_engine.classic import policy

decision = policy.choose_option(game, figure)   # pure: the scored menu + a pick
policy.enact(game, figure, decision.chosen)     # or enact a *player's* pick
policy.declare_attacks(game)                    # combat phase, after everyone moved
```

`Candidate`/`Decision` are the **shared** types from `tarmar_engine.policy`, so
one menu payload serves both profiles; `Candidate.target_id` therefore admits
either profile's identifier (Tarmar's int combatant id, classic's string
`uid`). Scores are thin on purpose — melee's AI is a decision tree, not a
scorer, so the pick carries the tactic that produced it and the alternatives
come back at zero rather than with invented numbers.

`ClassicMeleeProfile.run_turn` accepts either shape of `choose_option`: return
a `Candidate` and the runner enacts it; drive the game's verbs yourself and
return `None` (melee's board, and the mechanics tests). Attacks are declared
between selection and resolution either way, so a blow is aimed at where its
target actually stands.

**A behavioural difference worth naming:** enacting an option lets the *engine*
derive the movement and facing, as the Tarmar engine already does. Melee's own
board instead lets a human click the destination hex.

### Choosing a classic *spell*

Deciding the turn left one thing still the AI's private business: **which
spell**. The menu offered a bare `CAST` option, and the combat phase re-derived
the spell from the heuristics — so a cast chosen by anyone but the AI was
silently overruled. The menu now names the spell:

```python
for candidate in policy.menu(game, wizard):
    candidate.letter, candidate.spell_key, candidate.target_id
    # "cast", "magic_fist", "dummy"   <- one entry per castable spell

policy.enact(game, wizard, chosen)   # records the declaration
policy.declare_attacks(game)         # queues the spell that was declared
```

Each entry comes from `GameState.spell_targets` — the engine's single source
for what a spell may be aimed at — so nothing is offered that `queue_spell`
would reject, and a caster with nothing castable is offered no `CAST` at all
rather than an option that dead-ends.

The choice is *recorded* on the figure (`declared_spell_id` /
`declared_spell_target`, per-turn like every other declaration) rather than
queued on the spot, because melee declares attacks only once every figure has
moved. A declaration that has since gone illegal — the target felled, the ST
spent — stands the caster down, which is what melee already did when its own
re-derivation came back empty.

**Affordability is the rules' bound, not the AI's.** `ai.CAST_RESERVE_ST` is a
tactic knob: the AI keeps ST back because the pool is also its hit points. A
menu shown to a player applies no reserve, since the rules let a cast take a
wizard to 0 ST — hiding a legal cast behind the AI's caution would offer less
than the queue accepts. `ai.cast_st_for` is the one affordability rule both
read.

## The battle-rules pass (v0.9.5)

tarmar-studio #776–#826 (and #867's agreement rule) held the Tarmar profile
to its rules pages. A v0.9.4 snapshot still loads. **An adapter that fills
none of the new fields does not play as before**: these changes take effect
on the default path, with no field set:

- bare-handed figures have no plain attack (`j`/`b`); they fight through
  HTH (`t`), which needs an entry condition, except that two bare-handed
  figures, or an enemy closing on you, enter it by agreement (#815, #823,
  #867). Inside hand-to-hand only a partner using bare hands or a dagger
  strikes at +4; an armed partner keeps its weapon at its normal bonus,
  and whether it must drop to `t` is carried to tarmar-studio #867;
- an engaged figure holding a bow gets One Last Shot (`l`), not a bow-club
  `j` (#776, #815);
- ATTEMPT HTH (`o`) is offered only when an entry condition holds (#823);
- a forced retreat pushes into any clear hex, the save only when none is
  clear (#779); a push needs damage past the armour, never a spell (#813);
- the injury band applies to initiative and the retreat save (#817);
- a critical adds the damage modifier once (#824);
- a dropped weapon lies in its hex and the figure picks it up (`q`) (#780);
- a second fumble on a stressed weapon breaks it whatever the roll (#814);
- off-balance is spent by the next action, whatever it is (#811);
- mana is kept on an ordinary failed cast (#816);
- prone and unarmed enemies do not engage (#820);
- a slower enemy strikes a disengaging figure at the adjDEX gap (#777);
- an unconscious caster's spells end (#826);
- the menu gains yielded movement, DODGE jogs toward a missile threat, and
  DROP is offered (#819).

Pinning 0.9.5 in tarmar-studio (at 4e3aee2) fails 22 of its tests, each
because it pinned one of these changes. 17 are the studio's copies of engine
tests (draw dagger unimplemented, #822; crit expectations, #824; retreat
setups, #813/#779; grapple setups, #823; the engaged archer defending,
#776; the beast and grapple menus, #819/#823). Two control-mode grapple
tests and a dice-fidelity grapple test need an HTH entry condition in
their setup (#823). The dice-fidelity blocked retreat needs its victim
boxed in (#779). The seed-42 golden battle's hash moves: it ends at turn
61, not 143, and its first changed decision is an archer no longer engaged
by a bare-handed figure (#820/#815).

What a consuming game now fills in:

| Field | What it carries |
|---|---|
| `move_run` / `move_sprint` | the gait distances; **0 bars the gait** (chainmail, a Medium load: "Cannot Run or Sprint"). `move_sprint` defaults to 0. |
| `movement_modifier` | movement.md's modifier, read by the HTH entry condition "lower movement modifier" |
| `spare_weapons`, `weapon_skills` | weapons carried but not in hand, and the figure's effective Weapon-skill level with each weapon by `item_id` |
| `spell_mastery` | Spell Mastery level per spell key (a grappled caster renews at 2+, casts at 3+) |
| `WeaponState.hth_usable` | a dagger (HTH strikes, DRAW DAGGER) |
| `WeaponState.double_shot_dex` / `reload_turns` / `quick_reload_*` | missile rates; `tarmar_engine.weapons.missile_rate_from_note` reads them off weapons.md's Notes cell |

The menu (`policy.choose_option`'s candidates) is the legal menu, and gains
keys a manual player can pick: `sprint`, the yielded movers (`a_yield`,
`sprint_yield`, `b_yield`, `c_yield`), `d`, `e`, `l`, `m`, `q`, standalone
`t`, and `u` for a held figure. `run_turn` takes an optional
`choose_retreat` for the pusher's forced-retreat choices.
`tarmar_engine.house_rulings` records every reading the engine makes where a
page is silent, each marked for Spencer.

## Magic mechanics: structure here, numbers injected (v0.9.8)

The magic-mechanics pages are DM-only and this repository is public, so the
engine follows the classic profile's precedent: **all injected, no rulebook
numbers.** `tarmar_engine.magic` holds each mechanic's structure, and every
number and table arrives in a `MagicRules` the caller builds from its own
canon and passes as `TarmarProfile(magic=rules)`. The default profile
(`TARMAR`, `magic=None`) runs exactly as before: no new events and no new
payload keys. The tests use invented numbers.

| `MagicRules` field | What the engine does with it |
|---|---|
| `casting_success_tiers` (`CastingSuccessTier`: `key`, `label`, `natural_totals`, `effect_bonus`, `mana_refund`) | After a **successful** casting roll, the natural total picks a tier; it is narrated (a `status` event carrying `success_tier`), its refund is paid back before any Control Roll (capped at the cost, pushed mana included), and its bonus adds to the spell's rolled damage or healing. A tier never rescues a failed roll (tarmar-engine #26). |
| `push` (`PushRules`: `control_dice`, `control_attribute`, `penalty_per_mana`, `base_cost_counts_as_invested`, `failure_bands`, `automatic_runaway_totals`, `effect_bonus_per_mana_by_kind`, `max_push_mana` (a secondary cap, `None` for none; Channel supersedes it), `spell_effect_bonus_per_mana`) | Each cast on the menu gains pushed variants (`Candidate.push_mana`, score 0, for a player; the AI does not push), but only for a spell whose kind of rolled effect (`SpellEffectKind`: damage, healing) the rules give a push bonus; a continuing spell is never pushed. In phase 5, after a successful casting roll, a Control Roll is made roll-under against the attribute less the penalty per mana invested and the casting roll's injury and off-balance penalties; a failure's margin picks a `ControlBand` (injected `key`, `label`, `is_runaway`, `spell_takes_effect`, `push_bonus_applies`), and an automatic-Runaway natural total takes the first Runaway band. The pushed mana is paid with the cost (tarmar-engine #27). |
| `channel` (`ChannelRules`: `derivations` by kind-of-magic key, `kind_by_spell`, `default_kind`; each `ChannelDerivation`: `source_skill`, `multiplier`, `offset`) | A caster's Channel for a spell is `multiplier` x a recorded level + `offset`, never below 0, by the derivation for the spell's kind of magic. The level read is the one recorded for the spell being cast (`CombatantState.spell_skill_levels`) when `source_skill` is `None`, or in the named general skill (`CombatantState.skill_levels`) otherwise; an unrecorded level reads as 0. Channel bounds the whole mana of one casting, cost and pushed mana together: the menu drops any cast and any pushed variant past it (the AI takes the best of what remains), the engine raises on a casting past it, and the cast event names it (`channel`, `channel_kind`, and "Channel N" in the message). `PushRules.max_push_mana` still applies beneath it; the tighter holds (tarmar-engine #29). |

To put Push on the AI's menu, drive the turn with
`functools.partial(policy.choose_option, magic=rules)`; with Channel rules
injected, a menu built without them can offer a cast the engine refuses. A
Runaway is flagged (`runaway: True`) but its later turns are not modelled
yet; Borrowing, Runaway and Counterspell are tarmar-engine #28, #30 and #31.

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
