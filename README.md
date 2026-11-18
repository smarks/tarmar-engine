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
snapshot = game.to_dict()  # JSON-safe; store it
game = GameState.from_dict(snapshot)  # resumes exactly, dice included
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

decision = policy.choose_option(game, figure)  # pure: the scored menu + a pick
policy.enact(game, figure, decision.chosen)  # or enact a *player's* pick
policy.declare_attacks(game)  # combat phase, after everyone moved
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

policy.enact(game, wizard, chosen)  # records the declaration
policy.declare_attacks(game)  # queues the spell that was declared
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
  and whether it must drop to `t` is carried to tarmar-studio #867 (since
  ruled: inside hand-to-hand the menu is the HTH table, see "The
  hand-to-hand menu and placed moves" below);
- an engaged figure holding a bow gets One Last Shot (`l`), not a bow-club
  `j` (#776, #815);
- ATTEMPT HTH (`o`) is offered only when an entry condition holds (#823),
  and never at an enemy already in a hold, which the grapple refuses
  (`target_held`, tarmar-engine #60);
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
payload keys. (Since v0.9.15 the casting roll's failure totals are injected
too, so the default profile no longer carries them; see "The casting roll's
failure totals" below.) The tests use invented numbers.

| `MagicRules` field | What the engine does with it |
|---|---|
| `casting_success_tiers` (`CastingSuccessTier`: `key`, `label`, `natural_totals`, `effect_bonus`, `mana_refund`) | After a **successful** casting roll, the natural total picks a tier; it is narrated (a `status` event carrying `success_tier`), its refund is paid back before any Control Roll (capped at the cost, pushed mana included), and its bonus adds to the spell's rolled damage or healing. A tier never rescues a failed roll (tarmar-engine #26). |
| `push` (`PushRules`: `control_dice`, `control_attribute`, `penalty_per_mana`, `base_cost_counts_as_invested`, `failure_bands`, `automatic_runaway_totals`, `effect_bonus_per_mana_by_kind`, `max_push_mana` (a secondary cap, `None` for none; Channel supersedes it), `spell_effect_bonus_per_mana`) | Each cast on the menu gains pushed variants (`Candidate.push_mana`, score 0, for a player; the AI does not push), but only for a spell whose kind of rolled effect (`SpellEffectKind`: damage, healing) the rules give a push bonus; a continuing spell is never pushed. In phase 5, after a successful casting roll, a Control Roll is made roll-under against the attribute less the penalty per mana invested and the casting roll's injury and off-balance penalties; a failure's margin picks a `ControlBand` (injected `key`, `label`, `is_runaway`, `spell_takes_effect`, `push_bonus_applies`), and an automatic-Runaway natural total takes the first Runaway band. The pushed mana is paid with the cost (tarmar-engine #27). |
| `channel` (`ChannelRules`: `derivations` by kind-of-magic key, `kind_by_spell`, `default_kind`; each `ChannelDerivation`: `source_skill`, `multiplier`, `offset`) | A caster's Channel for a spell is `multiplier` x a recorded level + `offset`, never below 0, by the derivation for the spell's kind of magic. The level read is the one recorded for the spell being cast (`CombatantState.spell_skill_levels`) when `source_skill` is `None`, or in the named general skill (`CombatantState.skill_levels`) otherwise; an unrecorded level reads as 0. Channel bounds the whole mana of one casting, cost and pushed mana together: the menu drops any cast and any pushed variant past it (the AI takes the best of what remains), the engine raises on a casting past it, and the cast event names it (`channel`, `channel_kind`, and "Channel N" in the message). `PushRules.max_push_mana` still applies beneath it; the tighter holds (tarmar-engine #29). |

To put Push on the AI's menu, drive the turn with
`functools.partial(policy.choose_option, magic=rules)`; with Channel rules
injected, a menu built without them can offer a cast the engine refuses. A
Runaway is flagged (`runaway: True`); with injected Runaway rules its later
turns run too (#30, v0.9.13, below). Borrowing (#28) arrived in v0.9.12 and
Counterspell (#31) in v0.9.14, below.

## The hand-to-hand menu and placed moves (v0.9.9)

Spencer's rulings of 2026-10-09 (tarmar-studio #867, #819, #866), in
tarmar-engine #19 and #20. Both change the default path.

**Inside hand-to-hand the menu is the HTH table** (#19). A figure whose
`hth_with` names an active enemy beside it is offered action-options.md's
Hand-to-Hand Combat table and nothing else, standing or down:
`actions.legal_actions(..., in_hth=True, can_draw_dagger=...)` returns `t`
(bare hands, or the dagger in hand, at +4), `u` when a dagger is carried and
not in hand, `v` DISENGAGE, and `r` for the casts the table allows ("If
hands free or no-gesture spell": no weapon in hand, or Spell Mastery 2). No
`j`, at a partner or at a third enemy beside it (coordinator's ruling under
the standing rule on the issue's open question; Spencer may overrule). The
struck figure strikes back at +4 with no entry condition. `v` outside a
grapple is `TurnRunner.hth_disengage`: 4d6 ≤ effective DEX, stand, step to an
adjacent clear hex, leave hand-to-hand with every partner; a Struggle Free
that succeeds leaves it too. `cast_spell` checks the cast's condition again
when it is cast. A beast keeps its own menu, and a caster in hand-to-hand takes no walk-slow step out of it.

**A candidate may carry a hex** (#20). `Candidate.destination` is an axial
`(q, r)` or `None`; `to_payload()` writes `"destination": [q, r]` only when
one is named, so a candidate without one logs as before.
`tarmar_engine.movement` holds the rule: `placement(state, figure, key)`
says which kind of placed move an option allows, `legal_destinations` lists
the hexes, `refusal` says why a hex is not one.

| Kind | Options | Hex | When |
|---|---|---|---|
| shift | `j`, `k`, `m`, `o`, `r` | adjacent, footprint clear, still engaged with every enemy it was engaged with | Initial Movement |
| step | `n`, `v` (and Struggle Free) | adjacent, footprint clear | Actions |
| gait | `a`, `sprint`, `c`, `d`, `a_yield`, `sprint_yield`, `c_yield` | reached within the gait along a clear path, stopping where it would be engaged | its movement phase |

A figure in hand-to-hand or a grapple has only `v`'s step. A CHARGE goes at
its target and takes no hex. The engine checks the hex when the option is
chosen and again when the move is made; a hex it may not take is refused in
an `info` event (`destination_refused`) and the option makes the move it
makes with none. Leaving an engagement is DISENGAGE's (`n`), so a Shift
that would leave one is refused (coordinator's ruling under the standing
rule; Spencer may overrule). The AI names no hex: its engaged options stand
still, its DROP drops in place, its MOVE, CHARGE and DODGE close on their
target, and its `n`, `v` and Struggle Free leave the step to the engine when
the figure acts, the forecast printing the step it expects. `movement.gait_allowance` is the one jog rule the engine moves
by and the policy prices a charge by (#17). `CombatantState` gains
`chosen_destination`.

**Whom a step away steps from (tarmar-engine #62, v0.9.20).**
`movement.step_away_from(state, actor, letter)` is the figure a step away
(`n` or `v`) with no hex picked steps straight back from, then to either
flank (`movement.step_away_hex`), or `None` when there is nobody: for `n`
the first active enemy beside it in the state's order, for a held figure's
`v` (Struggle Free) its captor, for any other `v` the first active partner
beside it in `hth_with` order. `movement.left_behind(state, actor, letter)`
is the whole list, the one stepped from first: the enemies a DISENGAGE
records in `disengaged_from`, or the partners a break away leaves
hand-to-hand with. Both raise `ValueError` for any other letter.
`TurnRunner.disengage_step` and `TurnRunner.hth_disengage` read
`left_behind` (the list, for `disengaged_from` and `hth_ended`); the
policy's `n` forecast and a held figure's Struggle Free forecast read
`step_away_from`, and its `v` break-away forecast reads `left_behind`. The
engine's `TurnRunner.grapple_struggle_free` still reads the captor from
`grappled_by` directly, which is what the held branch returns. The policy's
private `_hth_partners` is gone. No battle's events change.

## A Game Master's agreement to hand-to-hand (v0.9.10)

"Entering Hand-to-Hand" ends "or they simply agree". The engine reads two
agreements off the board (an enemy closing in, two bare-handed figures);
anything beyond them is a Game Master's ruling the consuming game records
(Spencer's ruling of 2026-10-09 on tarmar-studio #867: "a Game Master marks
a battle, or a pair, as 'HTH by agreement'; players alone still need the
engine's entry condition"). `CombatantState.hth_agreed_with` lists the
enemies a figure may enter hand-to-hand with on that ruling. The agreement
is symmetric: when either figure lists the other, `hth_entry_reason` returns
`combat_math.GAME_MASTER_AGREEMENT` ("a Game Master rules they agree") both
ways, checked after the two read off the board. It is additive: empty, nothing
changes, and `BattleState.to_dict` writes it only when it names someone.

## The engaged cast stands still (v0.9.11)

An engaged cast (`r`) no longer takes the walk-slow step of two hexes that
the disengaged `f` and `h` take in Final Movement (#34): its Move column is
"Shift/still" (action-options.md), so it shifts one hex only to a chosen
hex. The option catalog's cap for `r` is "none".

## Borrowing, numbers injected (v0.9.12)

A caster short of mana may borrow the rest (tarmar-engine #28).
`MagicRules.borrowing` takes a `BorrowingRules`: `debt_dice`,
`debt_attributes` (summed, each `INT` or `WIS`), `penalty_per_debt`,
`failure` (a `BorrowingFailure`: `key`, `label`, `is_runaway`; replaced by
`failure_bands` in v0.9.21, below), `automatic_failure_totals`, `max_debt`
(`None`: Channel alone bounds the casting, so Channel rules must be injected
too) and `refunds_pay_debt_first`.
As with the rest of `tarmar_engine.magic`, every number and label is the
caller's; the tests use invented ones.

- **Menu.** `choose_option(..., magic=rules)` appends a borrowing variant
  (`Candidate.borrow`, score 0, `"borrow": true` in its payload) for each
  cast, and each pushed cast, the actor cannot afford, wherever its
  situation allows a cast, within the debt the cap still allows and within
  the Channel. The rationale names the shortfall and the debt roll's target.
  The AI never borrows.
- **Cast.** A cast chosen with `chosen_borrow` that costs more than the
  pool empties it and adds the shortfall to `CombatantState.debt`, when the
  cast pays (a success, or a failure that loses mana). The cast event
  carries `borrowed` and `debt`. Past the cap, or with no Borrowing rules,
  the engine raises; without the choice the caster lacks the mana as before.
- **Debt roll.** After the spell's aim and effect, whatever became of the
  spell, a `debt` roll is made roll-under against the attributes less the
  penalty per point owed; a natural total on the automatic list fails
  whatever the target. A `status` event carries `debt_roll`, `dice`,
  `natural`, `target`, `debt`, `held` and `automatic`; a failure adds a
  second naming the injected failure (`debt_failure`, `label`, `runaway`).
  The spell stands either way.
- **Refunds.** With `refunds_pay_debt_first`, a tier's refund pays the debt
  before it refills the pool, before the debt roll.
- **Snapshot.** `debt` persists across turns; `BattleState.to_dict` writes
  `debt` and `chosen_borrow` only when set, so a battle without Borrowing
  snapshots as it always has. The caller writes `debt` back to the
  character after the battle.

The readings the engine takes on the page's silences are in
`tarmar_engine.house_rulings` (`debt_roll_after_effect`,
`borrowed_when_the_cast_pays`, `borrowing_counts_toward_channel`,
`ai_never_borrows`, `debt_persists_on_the_combatant`,
`refunds_pay_debt_first`), each marked for Spencer.

## Runaway's later turns, numbers injected (v0.9.13)

A spell whose control is lost feeds on its caster every later turn until it
is anchored again (tarmar-engine #30). `MagicRules.runaway` takes a
`RunawayRules`: `drain_multiplier_of_cost` and `drain_flat` (the drain a
turn is the multiplier x the original cost + the flat amount),
`overflow_pool` (`"body"` or `"fatigue"`), `death_multiplier_of_max`
(`None`: no death line), `self_anchor` and `helper_anchor` (each a
`RollUnderRule`: `dice`, `attribute`), `helper_cost_multiplier_of_cost`,
`helper_failure_catches_helper` and `effect_bonus_per_turn`. Every number
is the caller's; the tests use invented ones. Without it a Runaway is the
payload flag, as before.

- **Where it begins.** The Control Roll's Runaway band and automatic totals,
  a Borrowing failure that is a Runaway, and the casting roll's Runaway
  total (injected through `MagicRules.casting_failure` since v0.9.15) each
  set `CombatantState.runaway` (a `RunawayState`: `spell_key`,
  `original_cost`, pushed mana included, `turns_running`, `caught_from`)
  and emit a `status` event carrying `runaway_begins`, `spell` and
  `original_cost`. A combatant already feeding one keeps it, and an `info`
  event names the one kept (`runaway_kept`).
- **Each later turn.** In phase 2, before the renewals, the drain comes off
  the caster's mana; when the mana is gone a conscious caster falls
  unconscious by the engine's usual collapse, and the rest of the drain,
  and every later turn's, comes off the overflow pool, which may go
  negative. A `status` event carries `runaway_drain`, `spell`, `turn`,
  `drained_mana`, `drained_pool`, `pool`, `pool_left`, `mana`,
  `unconscious` and `effect_bonus_per_turn`. At or past the death line the
  caster dies: a `status` event carrying `runaway_death` (`pool`,
  `pool_left`, `death_line`), then the usual `death` event, and the Runaway
  ends. The spell's effect is not applied again; the turn count and the
  growth per turn are for the caller to narrate.
- **Anchoring.** `choose_option(..., magic=rules)` offers `anchor`
  (`Candidate.anchor_target`, `"anchor_target"` in its payload): to a
  conscious combatant for its own Runaway, which the AI always takes, and
  to any other mage (a spell known or a spell skill recorded, and since
  v0.9.22 a level in the True skill; see "True Magic" below) with the mana
  for the helper's cost, for each living combatant's Runaway, at a score of
  0. The choice is `CombatantState.chosen_anchor`. In phase 5 the anchor is
  a whole action that stands still: a helper pays its cost, then an
  `anchor` roll, roll-under against the injected attribute less the injury
  band and off-balance penalty. Held, the Runaway ends: a continuing spell
  becomes an ordinary active spell of the anchorer, to renew or let lapse,
  and any other spell, its effect already had, simply stops. Failed, it
  runs on, and a failed helper is caught by one of its own when the rules
  say so (one already feeding a Runaway keeps it). A held anchor records
  the turns the Runaway ran in the anchorer's
  `CombatantState.anchored_turns_running` (by spell key). The anchorer's
  next cast of that spell adds `effect_bonus_per_turn` x those turns where
  a tier's bonus is added, and uses the record up. A continuing spell rolls
  no effect, so for it the growth is narrative only, and its record goes
  when the spell leaves the anchorer's active spells. A `status` event
  carries `spell`, `anchor_target`, `helper` (the helper's id, `None` for
  the caster's own), `held`, `anchor_roll`, `target`, `paid`, `mana_left`,
  `caught`, and on a hold `runaway_turns_running`.
- **Snapshot.** `runaway`, `chosen_anchor` and `anchored_turns_running`
  are written only when set, so a battle without Runaway rules snapshots as
  it always has. A death ends the dead combatant's Runaway.

The readings on the page's silences are in `tarmar_engine.house_rulings`
(`one_runaway_at_a_time`, `runaway_drain_in_renew_phase`,
`runaway_effect_is_narrated_not_reapplied`,
`anchor_takes_casting_penalties`, `ai_anchors_only_itself`,
`anchored_spell_becomes_the_anchorers`), each marked for Spencer.

## Counterspell, numbers injected (v0.9.14)

Any mage may commit mana to counter an enemy caster's spell this turn, and
a countered caster may press the spell again on later turns
(tarmar-engine #31).
`MagicRules.counterspell` takes a `CounterspellRules`: `opposed_dice` and
`opposed_attribute` (`"INT"` or `"WIS"`) for the opposed roll,
`mana_per_bonus` (a side gains 1 per that much mana invested),
`ties_go_to_caster`, and `defender_min_mana` and `defender_max_mana`
(`None`: the purse alone bounds a commitment; Channel does not). Every
number is the caller's; the tests use invented ones. Without it nothing
changes.

- **The counter.** `choose_option(..., magic=rules)` offers `counter`
  (`actions.COUNTER_OPTION`, keyed outside the lettered tables;
  `Candidate.counter_mana`, `"counter_mana"` in its payload, `target_id` the
  caster) to any mage against each enemy mage (since v0.9.22 a level in the
  True skill makes a mage too; see "True Magic" below), once per commitment
  it can afford, at a score of 0: the AI never counters. A counter names a
  caster, not a cast: it is offered whether or not the caster has chosen its action
  yet, so a defender that chooses first counters blind, and it opposes that
  caster's cast this turn whatever its target. The choice is
  `CombatantState.chosen_counter`
  (`(caster id, mana)`). A counter is a whole action that stands still. In
  phase 5 it pays its commitment and sets `CombatantState.counter` (a
  `CounterState`: `caster_id`, `mana_committed`, `declared_turn`), with a
  `status` event carrying `counter_declared`, `caster`, `mana` and
  `mana_left`; a defender short of the mana declares nothing.
- **The opposed roll.** When the caster casts, after its cost is known to
  be payable and before the casting roll, every defender that chose a
  counter against it and has not yet acted declares now, and each declared
  counter opposes: one `roll` event per side with purpose `counter`, each
  side rolling `opposed_dice` + its `opposed_attribute` + the bonus its mana
  buys (the caster's whole casting cost, pushed mana included, less the
  casting roll's injury and off-balance penalties; the defender's
  commitment, less its own injury band). The caster must beat the highest
  defender, ties by `ties_go_to_caster`. A `status` event carries
  `counter_result`, `spell`, `caster_total`, `caster_bonus`, `caster_roll`,
  `defender_totals` (`defender`, `total`, `mana`, `bonus`, `roll` each) and
  `countered`; since v0.9.22, and only with a familiarity bonus injected
  (`CounterspellRules.familiarity_per_level` above 0), also
  `caster_familiarity`, and `familiarity` and `familiarity_source` on each
  `defender_totals` entry (see "True Magic" below). Every declared counter is
  spent either way.
- **Countered.** The spell makes no casting roll (no tier, Control Roll or
  Runaway), and its whole mana is paid, borrowing included, with the debt
  roll after. A `status` event carries `spell_countered`, `spell`,
  `defenders`, `contest_turns`, `mana_left` and the cast's Push, Channel and
  Borrowing keys where those rules are injected. `CombatantState.contest` (a
  `ContestState`: `spell_key`, `target_id`, `defender_ids`, `turns`) stands
  on the caster.
- **Escalation.** A caster whose `contest` is set is offered its casts of
  that spell at that target flagged `escalate` (`"escalate"` in the payload)
  at a score of 0; the AI takes its next best option. Casting it again pays
  again and meets whatever counters the target chose this turn. The contest
  ends, with a `status` event carrying `contest_ended`, `spell`, `reason`
  and `turns`, when the caster chooses anything else (`another_action`, in
  phase 3), when the pressed cast meets no counter (`unopposed`; it then
  casts as an ordinary cast), or when the caster gets through (`through`).
- **Lapsed.** A counter no cast meets lapses at the end of the Actions
  phase, its mana spent: a `status` event carrying `counter_lapsed`,
  `caster` and `mana`.
- **Snapshot.** `counter`, `contest` and `chosen_counter` are written only
  when set, so a battle without Counterspell rules snapshots as it always
  has.

The readings on the page's silences are in `tarmar_engine.house_rulings`
(`counter_resolves_at_the_cast`, `counter_names_a_caster`,
`ai_neither_counters_nor_escalates`, `committed_mana_is_spent`,
`countered_cast_makes_no_casting_roll`, `contest_ends_on_any_other_action`),
each marked for Spencer.

## The casting roll's failure totals, numbers injected (v0.9.15)

Which natural totals fail a casting roll whatever the caster's attribute,
which failures still spend the spell's mana, and which total starts a
Runaway came from a DM-only page, so they are injected like every other
magic number (tarmar-engine #46). `MagicRules.casting_failure` takes a
`CastingFailureRules`: `fumble_floor` (natural totals at or above it fail
whatever the attribute; at least 1), `mana_lost_totals` (failing totals on
which the mana is spent anyway; default none) and `runaway_total` (the total
that starts a Runaway, `None` for none; it must be a failing total, at or
above the floor, and among the mana-losing totals, since a Runaway's first
drain is paid out of the casting's mana). Every mana-losing total must be at
or above the floor too, so whether a failure spends the mana never depends on
the caster's attribute. Every number is the caller's; the tests use invented
ones.

- **The verdict.** A casting roll succeeds at or under the caster's
  effective attribute and, with `casting_failure` injected, under
  `fumble_floor`. The success tiers, the Control Roll and the spell's
  effect follow a success only, as before.
- **The mana.** A success pays the cast; a failure pays it only on a total
  in `mana_lost_totals`, and borrows only then. Every other failure keeps
  the mana ("no mana spent" in the cast message).
- **The Runaway.** A failing cast whose natural total is `runaway_total`
  begins a Runaway, run on later turns when `MagicRules.runaway` is
  injected too.
- **The estimate (since v0.9.19).** The policy prices a cast by the same
  ceiling the engine judges the roll by (tarmar-engine #47):
  `choose_option(..., magic=rules)` estimates each cast's chance as 3d6 at
  most `magic.casting_success_ceiling(attribute, rules.casting_failure)`,
  the highest natural total that succeeds, and the rationale names the
  fumble floor where it lowers that ceiling.
  `policy.cast_success_chance(attribute, rules.casting_failure)` is that
  chance, for a caller showing a spell's odds to show the same number. The
  engine applies the ceiling to the effective attribute, less the injury
  band and any owed off-balance penalty, and since v0.9.20 so does the menu
  (tarmar-engine #56); a caller passes the effective attribute too.
- **Without it.** With `casting_failure=None`, or no `MagicRules` at all,
  the attribute alone judges the roll, no failure costs mana, and no cast
  starts a Runaway. **This changes the default profile:** before v0.9.15
  `TARMAR` failed the casting roll's top totals, spent the mana on some of
  them and started a Runaway on one, from constants in `engine.py` that are
  now gone. A caller that wants that behaviour injects the numbers from its
  own canon.

The `mana_on_failure` house ruling now states the structure without the
totals (`tarmar_engine.house_rulings`, marked for Spencer).

## A Game Master's lifted magic bounds (v0.9.17)

Spencer's ruling of 2026-10-14 on tarmar-studio #37: a Game Master may lift
Channel, Push's own cap and a counter's commitment bounds for one figure,
each separately, as a recorded ruling on the battle. The consuming game
keeps the record (who, when) and fills `CombatantState.lifted_magic_bounds`
from it, as it fills `hth_agreed_with`; the engine reads it through
`magic.bound_lifted(combatant, bound)`, which raises on a key outside
`magic.LIFTABLE_BOUNDS`. The keys are `magic.CHANNEL` (`"channel"`),
`magic.PUSH_CAP` (`"push_cap"`) and `magic.COUNTER_BOUNDS`
(`"counter_bounds"`). Empty, nothing changes, and `BattleState.to_dict`
writes it only when it lifts something; an older snapshot loads with it
empty.

- **Channel.** The lifted caster's menu keeps a cast past its Channel and
  offers pushes past it, and the AI may choose such a cast;
  `cast_spell` runs it. `ChannelRules.bound_for` is the Channel as the bound
  (`None` when lifted) and `MagicRules.channel_bound(caster, spell,
  borrowing=...)` the bound a casting is held to. A spell the Channel rules
  give no kind is still withheld and refused, lifted or not. A borrowing
  casting stays within the debt cap; where Borrowing has no cap, Channel
  bounds only the shortfall, so the casting may take the caster's mana plus
  its Channel (house reading `lifted_channel_bounds_the_shortfall`, marked
  for Spencer). The menu's push lines name the Channel, and say "past
  Channel N" where a push passes it, and likewise "past the push cap N".
- **Push's cap.** `PushRules.cap(cap_lifted=...)` is the cap a caster is
  held to (`None` when lifted), and `PushRules.within_cap(push_mana,
  cap_lifted=...)` the check `cast_spell` makes;
  `PushRules.push_room(..., cap_lifted=True)` ignores `max_push_mana`; the menu offers pushes up to the Channel (or the purse,
  with Channel lifted too) and `cast_spell` runs them.
- **Counter bounds.** `CounterspellRules.bounds(bounds_lifted=...)` is the
  least and most a defender is held to: lifted, `magic.LIFTED_COUNTER_MINIMUM`
  (0) to the whole purse. `CounterspellRules.commitments(mana,
  bounds_lifted=...)` offers every commitment within them and
  `CounterspellRules.allows(mana, bounds_lifted=...)` is the check
  `declare_counter` makes. A commitment of 0 buys no bonus but still gets a
  full opposed roll, so a lifted mage is offered a counter every turn, even
  with an empty purse (house reading `lifted_counter_minimum_is_zero`,
  marked for Spencer).
- **The flag.** A cast that went past a lifted bound carries
  `"lifted_bounds"` (`magic.LIFTED_BOUNDS_KEY`) in its payload, on the cast
  event and on a countered cast's `spell_countered` event, listing the bounds
  it actually exceeded (`push_cap` before `channel`); it still names its
  Channel (`channel`, `channel_kind`). A counter past its lifted bounds
  carries `"lifted_bounds": ["counter_bounds"]` on its `counter_declared`
  event. Within the bounds, lifted or not, no key is written (house reading
  `lifted_cast_names_its_channel`). The messages are unchanged: narrating the
  ruling is the consuming game's.
- **One answer, on the menu too (v0.9.18).** Which lifted bounds a casting
  or a counter goes past is computed in one place:
  `MagicRules.lifted_bounds_passed(caster, spell, push_mana=0)` for a cast
  (`push_cap` when the push is past Push's own cap, then `channel` when the
  casting's whole mana, `magic.casting_mana(spell, push_mana)`, is past the
  caster's Channel) and `CounterspellRules.lifted_bounds_passed(mana)` for a
  counter. The engine writes its payload from them, and every menu option
  carries the same answer as `Candidate.lifted_bounds` (a tuple, empty for
  every other option; `"lifted_bounds"` in `Candidate.to_payload()` only
  when non-empty), so a caller reads it instead of recomputing it.
- **The ruling is the caller's to word (since v0.9.19).** A menu line names
  the bound it goes past and its value ("past Channel 3", "past the push
  cap 3") and no longer adds "by a Game Master's ruling":
  `policy.PAST_BY_RULING` is gone (tarmar-engine #55). **A caller that
  imported `policy.PAST_BY_RULING` must stop.** As with the event
  messages, saying that a ruling allowed it is the consuming game's, from
  `Candidate.lifted_bounds`, so the engine and the game do not each print
  the fact in their own wording.

## Public answers, so callers need not restate the engine (v0.9.19)

A consuming game that must explain a menu (why a cast is withheld, where a
move ends, whom a charge strikes) asks the engine's own rule, not a copy of
it. Each helper below is what the engine and its policy call themselves.

- **Channel (tarmar-engine #41).** `MagicRules.cast_within_channel(caster,
  spell, push_mana=0, *, borrowing=False)` says whether Channel lets the
  casting's whole mana (`magic.casting_mana`) through: within
  `MagicRules.channel_bound`, so `True` with no Channel rules and for a
  caster a Game Master has lifted Channel for, except a borrowing casting
  where Borrowing has no debt cap, which is held to the caster's mana plus
  its Channel. The debt cap is the caller's own check
  (`BorrowingRules.borrowing_ceiling`; `cast_spell` refuses past it).
  It raises `ChannelUnclassifiedSpell` for a spell the rules give no kind,
  lifted or not, as `channel_for` does. `cast_spell` refuses by it and the
  menu withholds by it; it says nothing of the purse, Push's cap or the
  debt cap.
- **Where a move ends (tarmar-engine #50).**
  `movement.default_approach(state, combatant, option_key, target)` is the
  `Approach` (its `route`, empty for a move that stands, and its end
  `distance`) of the option's default move toward the target: the option
  key to its gait (`movement.option_gait`; MOVE runs, SPRINT sprints,
  CHARGE ATTACK and DODGE jog, a yielded key at its own option's gait), to
  the gait's allowance (`movement.option_allowance`), to
  `movement.approach_route`. `TurnRunner.move_towards_target` walks it and
  the policy prices MOVE, CHARGE ATTACK and DODGE by it. Each raises
  `ValueError` for an option that makes no move toward a target.
  `movement.charge_reaches(approach)` says whether a charge's approach ends
  within `movement.CHARGE_REACH` (1: beside its target), the reach the menu
  prices a charge by; an approach that ends there leaves the charger
  adjacent, which is what `TurnRunner.melee_attack` strikes.
- **Two changes to `TurnRunner.move_towards_target(combatant)` come with
  it.** It no longer takes a `gait` argument (it was only ever passed the
  chosen option's own gait); the gait is always the chosen option's. And it
  raises `ValueError` when the chosen option makes no move toward a target,
  where it used to jog the figure. The turn loop calls it only for a moving
  option, so no battle's events change.
- **Whom a classic attack strikes (tarmar-engine #54).**
  `classic.ai.attack_blow(game, figure)` is the attack
  `classic.ai.queue_attack_for` would declare from where the figure stands,
  without declaring it: an `ai.Blow` naming the `target` and its `kind`
  (`ai.BlowKind.MELEE`, `RANGED` or `HAND_TO_HAND`, the
  `GameState.attack_candidates` field each is drawn from), or `None`. The
  declaration itself now declares from it. `classic.ai.planned_blow(game,
  figure, plan)` asks it after a plan (a CHARGE ATTACK's, say) played on a
  copy of the game, so a pole's two-hex jab, a multi-hex foe reached through
  its footprint, a charge that ends beside a weaker foe and bare hands'
  grapple all come out as the combat phase will declare them; the live game
  is untouched and the target is its own figure. Only that figure's plan is
  played: figures moving later in the turn are where they stand now.

## A classic attack forecast (v0.9.19)

A classic turn menu can say what a blow faces without reading the engine's
private terms (tarmar-engine #23). `GameState.attack_forecast(attacker,
target, option)` returns an `AttackForecast` (`classic.combat`) for a weapon
attack from where both figures stand:

- **To hit.** `needed`, the adjDX `queue_attack` would declare the blow at
  (the same derivation, `_attack_terms`, now serves both: situational
  modifiers, the missile or thrown range penalty, the facing bonus, which a
  missile never takes), with its `to_hit_breakdown`; `dice_count`, 4 against
  a target dodging a missile or thrown weapon or defending against a melee
  blow; `hit_chance`, through `Ruleset.classify_roll` over every total
  (`Ruleset.hit_odds`), so a 3, 4 or 5 always hits and a 16 to 18 always
  misses; and `hit_multipliers`, the chance of a triple, double or plain hit.
- **A hit.** `expected_damage`, the average hits a hit takes off the target's
  ST (`Ruleset.expected_hit_damage`): the weapon's dice, the special totals'
  multipliers, a pole weapon's `extra_dice` in or against a charge (after the
  multiplier), a practice bout's halving, then `armour_stops` (armor, a
  frontal shield, protection, a ready main-gauche's parry), floored at 0 per
  roll.
- **The option.** `option` stands in for the attacker's `current_option`
  where the declaration reads it (a pole's charge terms, a parting shot's
  single arrow), so a menu can forecast an option not yet chosen. Everything
  else is the board's; a charge is forecast from where it ends by asking a
  rehearsed copy of the game (`to_dict`/`from_dict`).
- **Not covered.** Legality (`queue_attack` checks it), the off-hand
  main-gauche jab, a shot's random member of a hand-to-hand pile, and a
  blow that fails to land because its target moved away. A figure with no
  ready weapon, or off the field, raises `IllegalAction`.

The forecast draws no dice and changes nothing. Its tests compare it, for
every attack the existing classic boards declare, with the declared
`PendingAttack` and with `Ruleset.resolve_attack` rolled on every die face.
`classic.data.DIE_FACES` (6) and `tarmar_engine.dice.sum_distribution` are
public alongside it.

## Side labels in the classic narrative (v0.9.19)

A side key is an identifier, not a reader's word: a consumer may key a
teamless figure's side by its own uid, and the narrative printed that key as
if it were a team's name ("Yaria Hawkridge (743) advances", "The 743 hold the
field"; tarmar-engine #25). A figure now carries an optional
`Figure.side_label`, a `classic.figure.SideLabel(name, team)` the consuming
game supplies; give every figure on a side the same one.

- **A team** (`team=True`): a member reads "Ada (Red)" and the win "🏆 The
  Red hold the field — victory!".
- **A side of one** (`team=False`): the label stands for the figure, so it
  is the figure's own name, or whatever the game calls a nameless one ("Side
  3"): "Yaria Hawkridge swings…", "🏆 Yaria Hawkridge holds the field —
  victory!".
- **No label** (the default): every line reads exactly as before, the side
  key in the parens and in the win. The tests pin a duel's lines as v0.9.18
  wrote them.

`narrate_victory(side, label=None)` takes the label directly; the engine's
own victory line passes the winning side's, `GameState.side_label(side)`,
which raises `ValueError` when the side's figures disagree (a label on some
and not others, or two different labels). The label round-trips through
`to_dict`/`from_dict` (`"side_label"`, absent from an older snapshot, which
loads unlabelled). A label name that is blank or has spaces at an end raises
`ValueError`.

## A menu a person can read (v0.9.19)

- **Forecasts as fields** (#22). A menu option that forecasts damage
  carries the figures as `Candidate.forecast`, a frozen
  `policy.Forecast`: `hit_chance` (P(hit) for a d20 attack, P(land) for a
  cast), `damage_expression` (the dice, e.g. `2d6-1`), `armour_stops`,
  `expected_damage` per landed hit, and for a d20 attack `target_number`,
  `bonus` and `winning_faces` (of `resolution.DIE_FACES`; `None` for a cast,
  whose chance comes from 3d6 roll-unders). ATTACK, a CHARGE that reaches,
  MISSILE (a throw of the only hand weapon carried included, though the AI
  scores it 0), ONE LAST SHOT, an HTH strike and a damaging cast carry one,
  and so do the copies made of them (a yielded CHARGE, a borrowing cast, a
  cast in a grapple or in hand-to-hand); every other option carries `None`,
  a pushed cast too. `to_payload()` carries it under `"forecast"`
  (`policy.FORECAST_KEY`) only when present. Each figure is held at the
  precision the rationale prints, the rationale's working is
  `Forecast.working()`, and `Forecast.score` is the product of the printed
  factors, so the prose and the fields cannot disagree. An option's own
  score may be adjusted after its forecast, as its rationale says: a wounded
  beast's caution scales it, and the thrown last hand weapon, a borrowing
  cast and an escalated cast score 0 with their forecasts kept. The HTH
  strike's line now names its winning faces, as every other d20 forecast's
  does.
- **Breaking for callers of the private helpers.** `policy._melee_score`
  and `policy._missile_score` are gone, and `policy._attack_forecast` and
  `policy._hth_strike_forecast` now return `(Forecast, rationale)` rather
  than `(score, rationale)` (the score is `Forecast.score`). tarmar-studio's
  `battle/turn_menu.py` imported them to widen the menu itself; it moves to
  the public every-target menu below in the same bump.
- **Every legal target** (#21). `choose_option(state, actor, magic=...,
  every_target=True)` returns the same `Decision` with each aimed option
  also offered at every other enemy it may be aimed at, right after the AI's
  own target and priced by the same code (score, rationale and
  `forecast`): ATTACK (j) and ONE LAST SHOT (l) at each adjacent enemy
  (j the AI's best first, l lowest id first), CHARGE (b and `b_yield`) at
  each enemy its jog ends beside, MISSILE (f) at each active enemy, nearest
  first, an HTH strike (t, best first) and ATTEMPT HTH (o) at each enemy an
  entry condition admits (o not at one already held, #60), and each
  damaging cast at each active enemy, nearest first (a spell has no range),
  with its pushed, borrowing and escalated variants; a grappled or
  grappling mage's Spell Mastery 3 casts too. `chosen` is the AI's own
  choice, unchanged, and is one of the candidates (by identity), even
  where an option aimed elsewhere scores higher. Without the flag the menu
  is as before. The menu is built twice for it, once for the AI's choice.
  A player's pick at any target is resolved by the engine as always.
  `weapons.throw_leaves_no_hand_weapon` is the policy's own test for a
  MISSILE throw that would leave the figure bare-handed (scored 0 for the
  AI at every target), for a consumer to read rather than copy.

## The indefinite article in narration (v0.9.20)

`combat_math.with_article(word)` is a word with its indefinite article:
"an" before a vowel letter, "a" before any other, whatever the case ("an
Elven bow", "an elf", "a Lucky cast"), and `ValueError` for an empty word
(tarmar-engine #59). The first letter suffices for every weapon, armour,
race and tier name the engine's catalogs and the studio's default canon
held on 2026-10-21; a name opening on a silent "h" or a "you" or "w"
sound ("hour", "unit", "one") would take the wrong one, and so would a
leading digit ("a 8-hex", "an 11-hex"), whose spoken number decides:
that is `hex_span_text`'s numeric form. The second-shot
line ("for an Elven bow"), a casting tier ("is an uncanny cast"), the
classic race floor ("an elf's ST may not begin below 6") and the classic
narrative's weapon lines all read it; `hex_span_text` keeps the numeric
form. tarmar-studio's own `gm.engine._with_article` makes the same choice
and could read this one.

## The menu forecasts the roll the dice make (v0.9.20)

A figure's action rolls lose the injury band (-1 or -2) and an owed
off-balance -2 (the engine's one reading of them, #43), and the menu's
forecasts now take them off as well, so a hurt or off-balance figure is
shown the odds the dice will give rather than better ones (tarmar-engine
#56). **This changes the AI's scores, and some of its choices, wherever a
figure is hurt or off-balance, and wherever a strike in close is on the
menu (below).**

- **A d20 attack.** ATTACK, a CHARGE that reaches, MISSILE, ONE LAST SHOT
  and an HTH strike forecast the bonus the engine rolls with:
  `Forecast.bonus`, the printed `d20 +X`, P(hit) and the winning faces are
  all of the penalised roll, and the rationale names what came off ("to hit
  less 2 injury and 2 off-balance"). DEFEND's threat prices each adjacent
  enemy's swing less that enemy's own penalties.
- **A cast.** P(cast) is the chance under the penalised attribute ("3d6 ≤
  INT 14 less 2 injury and 2 off-balance = 10"), and a targeted spell's
  P(aim) under the penalised DEX less, at a dodging target, the dodge's 4,
  as the engine's aim roll takes it; `Forecast.hit_chance` is their
  product. `policy.action_roll_penalties(actor)` is that reading, public:
  `(injury, off_balance)`, each an amount to subtract, so a caller showing
  a spell's odds passes `cast_success_chance(attribute - injury -
  off_balance, casting_failure)` and shows the menu's number.
- **Breaking away.** DISENGAGE (v) from hand-to-hand prices next turn's
  blows less the injury band only: the break-away spends this turn's
  off-balance.
- **A strike's damage.** An HTH strike's expected damage now counts a
  natural 20's extra damage roll, as the engine rolls it and as every other
  attack's forecast already did: `combat_math.expected_landed_damage(
  expression, stops)`, which `expected_attack_damage` now reads too.
  **The AI strikes in close more often for it.** A strike's score rises (a
  STR 12 figure's bare hands, 1.000 to 1.100 in the test boards), so it
  now beats a defensive buff it lost to, and tarmar-engine #38's armoured
  swordsman at DEX 11, held by a lightly armoured archer, strikes in close
  (0.316) rather than breaking away (0.312) as it did on v0.9.19. Over 100
  seeds that pair stays in hand-to-hand 13.6 turns on average, where
  v0.9.19 broke away after 3.0: the break-away is priced over two turns
  although a figure once free stays free, so it is under-priced whenever
  the weapon outclasses bare hands (tarmar-engine #64; at DEX 10 the pair
  already stayed in on v0.9.19).

An unhurt figure that owes no off-balance reads as before, and scores as
before except for these: a strike in close's expected damage, the
break-away that prices it, and a targeted spell's aim at a dodging target,
which now takes the dodge's 4.

## The gaits' Fatigue costs live with movement (v0.9.20)

`movement.RUN_FATIGUE_COST` (1), `movement.SPRINT_FATIGUE_COST` (6) and
`movement.GAIT_FATIGUE_COSTS` (by gait; walk and jog are free in combat)
now sit beside `movement.MOVEMENT_GAITS` and `movement.gait_allowance`, so
the policy's SPRINT rationale ("for 6 Fatigue") reads the number rather
than restating it (tarmar-engine #58). `engine.py` re-exports all three
under their old names, so no caller changes and no battle's events do.

## The debt roll graded in bands (v0.9.21)

A failed debt roll now lands in a band by how far it missed, as the Control
Roll's failures do (tarmar-engine #74, Spencer's ruling of 2026-11-17).
**`BorrowingRules.failure` and `BorrowingFailure` are gone**:
`BorrowingRules.failure_bands` takes a tuple of `DebtBand`s (`key`, `label`,
`is_runaway`, `lowest_margin`, `highest_margin`, `cost_pool`,
`cost_per_debt`). Every number and label is still the caller's.

**tarmar-studio must move with this release.** It imports
`BorrowingFailure` and builds `BorrowingRules(failure=...)` from its canon,
so this engine's pin bump has to land together with the studio's loader
change and the canon's bands (smarks/tarmar-studio#190), not before them.

- **One band shape, one validator.** `MarginBand` holds what the two rolls'
  bands share (`key`, `label`, `is_runaway`, the margins, `covers`);
  `ControlBand` and `DebtBand` extend it. `magic.validate_margin_bands`
  (contiguous from margin 1, the last and only the last open-ended) and
  `magic.band_for_margin` serve `PushRules` and `BorrowingRules` alike, so
  the two cannot drift. Push behaves exactly as before; one message changes,
  "band ... ends before it starts" now naming the band's `key`.
- **The cost.** A band's `cost_pool` is `"fatigue"`, `"body"` or `None`
  (`CASTER_POOLS`, with `FATIGUE_POOL` and `BODY_POOL`, now declared in
  `tarmar_engine.state` and still importable from `tarmar_engine.magic`; it
  replaces `RUNAWAY_OVERFLOW_POOLS`), and it takes `cost_per_debt` times the
  debt owed at the roll, any refund already applied. It comes off through
  `TurnRunner.apply_pool_cost`, which replaces `apply_fatigue_cost` and
  takes a gait's or a debt band's cost (a gait's events are unchanged), so
  unconsciousness and the survival saves apply as for any other loss. A
  debt cost's roll becomes the caster's fatal chain, so a death that
  follows, even of a caster already down, cites the debt roll.
- **Automatic totals.** A natural total on `automatic_failure_totals` lands
  in the last, open-ended band whatever the margin.
- **Events.** The roll and the `debt_roll` status event are unchanged. A
  failure's second status event carries `debt_failure` (the band's key),
  `label`, `runaway`, and now `cost_pool` and `cost` (`None` and 0 when
  nothing is taken); its message adds ", costing N pool", and the cost
  path's own event follows ("spends N body on the failed debt for ...").
  A Runaway band starts a Runaway as the single failure did.

The reading is recorded as `house_rulings` `debt_roll_graded_bands`.

## Every element has a Missile, Ball and Bolt (v0.9.21)

Every Elemental element (water, fire, lightning, earth, wind) now has a
Missile, Ball and Bolt in the spell catalog, built from one per-tier table
(tarmar-engine #75, Spencer's ruling of 2026-11-17 that an element is
learned tier by tier). The three spells that were already there keep their
keys and stats.

- `spells.ELEMENTS` names the five; `Spell.element` names a spell's element,
  and an Elemental spell must name one while no other spell may.
- `spells.lower_tier(spell)` returns the rung below: `None` at tier 1 or for
  a spell with no element. A ladder with a missing or doubled rung is refused
  when the catalog is built.
- A spell's `damage_pool` is checked against `CASTER_POOLS`.
- `house_rulings` `spell_magnitudes` is stated per tier, and its value keys
  are now `missile`, `ball` and `bolt`.

## True Magic: workings and True casts, numbers injected (v0.9.22)

A caster with a level in the True skill may do two new things
(tarmar-engine #77): **True cast** a catalog spell, instantly, its Channel
derived from the True skill; and **craft a working** over several turns.
`MagicRules.true_magic` takes a `TrueMagicRules`: `channel_kind`,
`skill_label`, `school`, `effects`, `tiers_per_level`, `turns_per_tier`,
`roll_attribute`, `roll_bonus_per_level`, `interruption`, `drain_pool`,
`drain_per_mana` and `lost_drain_per_mana`. Every number, label and key is
the caller's, the True skill's own included; the tests use invented ones.
Without it nothing changes but the spell catalog, which gains five generic
counters (below).

- **What is injected.** `channel_kind` names a key of
  `ChannelRules.derivations` whose `source_skill` is the True skill, so the
  skill's key is written once, in that derivation; `MagicRules` refuses True
  Magic rules without Channel rules, or naming a kind with no derivation or
  one that derives per spell. `MagicRules.true_level(caster)` is the
  caster's level in it (`ChannelDerivation.skill_level`, read off
  `CombatantState.skill_levels`), 0 with no True Magic rules; "may this
  caster True cast or craft" is `true_level(caster) > 0`, asked no other
  way. Each of `effects` is a `WorkingEffect`: `key`, `label`, `kind`
  (`SpellEffectKind.DAMAGE` or `HEALING`, or `None` for an effect the
  engine reports and the Game Master describes), `dice_per_tier`, `pool`,
  `targeted`, `ignores_armour` and `needs_grant`; a narrated effect may be
  neither targeted nor armour-piercing. `interruption` is an
  `InterruptionRules`: `roll` (a `RollUnderRule`), `penalty_per_mana`,
  `failure_bands` (plain `MarginBand`s, checked by
  `validate_margin_bands`) and `automatic_runaway_totals`, which need a
  Runaway band. `lost_drain_per_mana` maps a `magic.CastingEnding`
  (`interrupted`, `abandoned`, `blocked`, `unpaid`, `unconscious`,
  `grappled`, `countered`, `failed`, `stopped`) to a drain per mana; an
  ending with no entry drains nothing, and a key that is no ending is
  refused. Beside them, `CounterspellRules.familiarity_per_level` (default
  0, below).
- **The Channel by kind.** `MagicRules.cast_kind(spell, *, true_cast=False)`
  is the True kind (`channel_kind`) for a True cast or a working and `None`,
  the spell's own kind, for any other casting. `ChannelRules.kind_for`,
  `ChannelRules.channel_for` and `bound_for`, and `MagicRules.channel_for`,
  `channel_bound`, `cast_within_channel` and `lifted_bounds_passed` take a
  `kind`; `None` is today's behaviour, except that on `MagicRules` a working
  takes the True kind unasked. House ruling
  `channel_kind_belongs_to_the_spell` now reads "per spell, unless the cast
  names a kind". `LIFTABLE_BOUNDS` is unchanged.
- **A working** is a `Spell` the rules build, not a catalog entry:
  `TrueMagicRules.working(effect_key, tier)`, keyed
  `working:<effect key>:<tier>` (`magic.WORKING_KEY_PREFIX` and
  `WORKING_KEY_SEPARATOR`; `magic.is_working_key` is the one test). Its
  name is "<label> (tier N)", its school `school`, its level the tier (so
  its mana is the tier plus any push), its roll under `roll_attribute`, and
  its dice `WorkingEffect.dice_at(tier)`, the per-tier dice with count and
  modifier times the tier (`1d4+1` per tier is `3d4+3` at tier 3); it never
  continues. `magic.spell_for(key, rules)` is the one lookup for a spell key
  read from state or a candidate, a working's included; with no True Magic
  rules a working's key raises `KeyError`. The highest tier a caster may
  craft is `tiers_per_level` x its True level (`MagicRules.tier_cap`,
  `None` when a Game Master has lifted Channel for it, which lifts the cap
  too; `unlifted_tier_cap`, `past_tier_cap`), and a tier past it is
  reported as `channel` by `lifted_bounds_passed`. A tier takes
  `turns_per_tier` x the tier craft turns (`TrueMagicRules.turns_for`). An
  effect with `needs_grant` may be crafted only at a key in
  `CombatantState.granted_workings`, which the consuming game fills from its
  record of a Game Master's grant and the engine reads through
  `MagicRules.granted`, raising for a listed key the rules do not build.
- **The craft.** `actions.CRAFT_OPTION` (`"craft"`, "CRAFT WORKING" in
  `MAGIC_ACTIONS`) is a whole action that stands still, run by
  `TurnRunner.craft_working`. Its candidate carries the working in
  `spell_key`, with `push_mana`, `borrow` and `target_id`.
  - *Begin.* A caster with no craft standing begins one. A craft the menu
    never offers raises `ValueError`: no True Magic rules or True level 0, a
    key the rules do not build, an ungranted `needs_grant` key, a tier past
    the cap, a push Push's rules or cap do not allow (a narrated working
    cannot be pushed), whole mana past Channel by the True kind, or a
    borrowing with no Borrowing rules or past the debt cap. Where a cast is
    blocked (a grapple, or hand-to-hand with a weapon in hand), or the pool
    cannot cover the whole working and the caster did not choose to borrow,
    the action is a logged no-op. Otherwise `CombatantState.crafting` takes a
    `CraftState` (`spell_key`, `push_mana`, `borrow`, `turns_done`,
    `borrowed`, `target_id`), and each spell the caster keeps up ends, with a
    `status` event apiece.
  - *Shares.* Each craft action, the first included, gathers one share of
    the whole mana: `TrueMagicRules.share_due(craft)`, where the mana
    gathered after k of n turns is the whole x k / n rounded down
    (`gathered_after`; `gathered(craft)`, never stored), so 5 mana over 3
    turns is 1, 2, 2. A share the pool cannot cover empties the pool and
    adds the rest to `debt` and `CraftState.borrowed` when the craft
    borrows; when it does not, nothing is taken and the working is lost,
    `unpaid`. No share emits a cost event of its own: the payment is in the
    craft's `action` event. A share of 0 is logged like any other, its
    action reading "no mana due".
  - *Continue.* While a craft stands, the only craft the menu offers is its
    continuation, the craft option again with the craft's working, push and
    borrowing (the mage's other options stay on offer, and choosing one ends
    the craft); a craft option that differs from the craft raises
    `ValueError` in phase 3, where the choice is recorded. Each craft action
    names the working's target anew. Neither a continuation nor the
    completion re-checks the True level, the grant, the tier cap or Channel,
    so a grant withdrawn while a craft is under way does not stop it; the
    completion does not re-check the cast guard either. A craft standing on
    a figure when no True Magic rules are injected raises `ValueError`, in
    the engine and on the menu.
  - *Endings.* A craft ends early through one path: a `status` event, a
    Runaway if one starts, one debt roll if any share was borrowed and the
    mage is still active, then the drain, `lost_drain_per_mana[ending]` x
    the mana gathered. Any other option chosen in phase 3 loses it
    `abandoned`, or `blocked` where the continuation was withheld (in
    hand-to-hand with a weapon in hand), and a craft action where no cast
    may be made loses it `blocked`. Damage that lands, at least 1 point
    after armour, on a crafting mage still active forces an interruption
    roll, purpose `concentration`, roll-under `interruption.roll` less
    `penalty_per_mana` x the mana gathered and less the injury band; a
    failure's band (or an automatic total's Runaway band) loses it
    `interrupted`, and a Runaway band also starts a Runaway costing the mana
    gathered, unless nothing has been gathered. A grapple that takes hold
    loses it `grappled`, and a fall into unconsciousness `unconscious`, with
    no roll; a fall prone does not end it. A mage whose craft is lost in
    phase 5 before its own slot has that turn's craft action as a logged
    no-op.
  - *Completion.* At the end of the Actions phase, after the last action and
    before unmet counters lapse, each active combatant whose craft has had
    every turn, in the phase's DEX order, completes its working. Its casting
    roll is under `roll_attribute` + `roll_bonus_per_level` x its True level,
    less the injury band, and names its attribute as
    "<attribute>+<skill label>" (`TrueMagicRules.roll_target` and
    `versus`). From there it runs the code a cast runs, in a cast's order:
    the counter, the roll, the success tier, a pushed working's one Control
    Roll, the aim, the effect, the debt roll. A damaging working lands as a
    damaging spell does; a healing one heals the caster or the living
    teammate its craft named (`BattleState.teammates_of`), conscious or not,
    and never an enemy; a narrated one rolls its dice (purpose `working
    magnitude`) and applies nothing. A failure on a total that keeps the mana
    hands every share back, to the pool and off the debt, and drains
    nothing; a total that loses the mana spends it.
  - *The drain* comes last: `drain_per_mana` x the working's whole mana,
    borrowed mana included, from `drain_pool`, through
    `TurnRunner.apply_pool_cost`. **The pool may go below 0**; the caster
    falls unconscious at 0 or below, and the survival saves apply, as for any
    other loss. The craft is off the caster before the roll, so a fall by its
    own drain or a debt band loses nothing a second time. A working a Control
    band stops, or that fails on a total that loses the mana, drains
    `lost_drain_per_mana` for `stopped` or `failed` instead.
- **True casts.** `Candidate.true_cast` (`"true_cast"` in its payload only
  when set) becomes `CombatantState.chosen_true_cast`.
  `MagicRules.true_castable(caster)` is each spell the caster knows, but a
  generic counter, in order, each followed by the same rung of every other
  element's ladder (`spells.element_siblings`), with no repeats; empty at
  True level 0. `cast_spell` raises `ValueError` for a True cast with no
  True Magic rules, at True level 0, or of a spell outside it, and for any
  cast of a working. A True cast is instant, derives its Channel by the True
  kind, and makes the spell's own casting roll with no True-skill bonus.
  Casting deeper is a push, with Push's Control Roll and bonus, and it
  drains `drain_per_mana` x the mana pushed, so an unpushed True cast drains
  nothing; countered, stopped by a Control band, or failed on a total that
  loses the mana, it drains `lost_drain_per_mana` for `countered`,
  `stopped` or `failed`.
- **Five generic counters.** The catalog gains `counter_1` to `counter_5`,
  "Counter 1" to "Counter 5", school `spells.PROTECTION_SCHOOL`, levels 1 to
  5, attribute WIS, flagged `Spell.generic_counter`: **learned, never
  cast.** `Spell` refuses one that is targeted, continuing or healing, or
  that sets `damage`, `tn_bonus`, `attacker_penalty` or `element`; the menu
  offers no cast of one, `true_castable` leaves them out and `cast_spell`
  raises `ValueError`. They stand on a ladder of their own,
  `spells.GENERIC_COUNTER_LADDER`, so `lower_tier` steps down it.
  `spells.generic_counter_level(spell_keys)` is the highest level known, 0
  for none. A figure whose only spells are generic counters has no cast
  letter and may still counter; with no True level it is not offered as a
  caster to counter. **A consumer that lists the catalog now lists these
  five.**
- **Familiarity on the opposed roll.** With `familiarity_per_level` above 0
  (`CounterspellRules.counts_familiarity`) each side adds
  `familiarity_bonus(level)`, the per-level bonus x a level.
  `caster_familiarity` is the caster's recorded level in the exact spell
  (`magic.spell_skill_level`, from `CombatantState.spell_skill_levels`), or
  its True level for a working and for a True cast of a spell it holds no
  level in. `defender_familiarity` is the highest, never the sum, of the
  defender's level in the exact spell, its True level, and its generic
  counter level when the spell's level (a working's tier) is no higher, with
  the `magic.FamiliaritySource` it rests on (`spell`, `true`, `generic`, the
  first of those on a tie, `None` at 0). The payload's familiarity keys
  carry these levels, not the bonuses they buy. A working is countered only
  when it goes off, with its whole mana as the caster's investment; a counter
  declared on an earlier craft turn meets no cast and lapses, and a
  countered working is lost, `countered`, and leaves no contest to press.
  At 0 neither side's familiarity is read.
- **The menu and AI.** `choose_option(..., magic=rules)` offers, wherever a
  cast is legal, a True mage's recipe casts, then its True casts under the
  same letter (named with `policy.TRUE_CAST_MARK`, score 0), then the
  crafts it may begin under `CRAFT_OPTION`: one per effect and tier, from 1
  up to the least of the tier cap (none with Channel lifted), the mana the
  working may take (the purse, or for borrowing variants the borrowing
  ceiling) and `WorkingEffect.highest_buildable_tier`, a `needs_grant`
  effect only at granted keys. A damaging working is aimed as a damaging
  cast is; a healing one is offered at the caster, and under `every_target`
  at each teammate; a narrated one names no figure, and under
  `every_target` each living one. A level in the True skill makes a mage
  (`_is_mage`): with no spell known, such a figure may counter, may be named
  by a counter, and may anchor another's Runaway, and a True mage with no
  recipe spell is offered its crafts and their borrowing variants. With no
  active enemy no new damaging craft is offered, as no damaging cast is,
  but a standing damaging craft's continuation still is, naming no figure,
  at score 0. A crafting mage is
  offered the continuation and no new craft, prone beside STAND UP too,
  withheld in hand-to-hand with a weapon in hand, with no pushed or
  borrowing variant, and named with "(PUSH N MANA)", "(BORROWING)" or both
  where its craft carries them. Pushed and borrowing variants keep
  `true_cast`, and a recipe cast and a True cast of one spell past Channel
  each keep their own withheld reason. The AI never True casts. It may
  begin a craft at any tier up to its cap, never pushed or borrowing, and
  scores a damaging working its forecast divided by the craft turns left,
  this one included; a working whose drain would take its pool to 0 or below
  scores 0, as healing, narrated, pushed and borrowing crafts do. It still
  neither counters nor escalates. A counter's rationale adds the defender's
  own familiarity bonus, against a cast not yet known its True level. The
  menu's helpers take `magic` as a keyword, and a `casting_failure` passed
  beside it that is not `magic.casting_failure` raises `ValueError`.
- **Events.** The new events and keys, each appearing only with True Magic
  or a familiarity bonus injected.

  | When | Event | Payload |
  |---|---|---|
  | Craft action | `action` | `craft` (`engine.CRAFT_BEGUN` `"begun"` or `CRAFT_CONTINUED` `"continued"`), `spell`, `tier`, `turn`, `turns`, `share`, `gathered`, `mana_total`, `mana_left`, and a cast's cost keys |
  | A spell kept up ends as a craft begins | `status` | `spell`, `ended: true`, `crafting: true` |
  | Interruption | `roll` (`concentration`), then `status` | `craft_interrupted`, `spell`, `gathered`, `target`, `natural`, `margin`, `held`, `band`, `runaway` |
  | Working lost | `status` | `craft_lost`, `ending`, `spell`, `mana_spent`, `drain`, and `defenders` when countered |
  | Completion | a cast's own events | the cast payload plus `working: true`, `tier`, `true_cast: true`; on a hand-back `mana_returned`, and `debt_returned` with Borrowing injected |
  | Healing a teammate | the heal's `status` | plus `target` |
  | Narrated effect | `roll` (`working magnitude`) if it has dice, then `status` | `working_effect`, `tier`, `narrated: true`, `target`, `magnitude` |
  | Drain | `apply_pool_cost`'s `status`, only above 0 | the pool, `cost`, `reason` (`engine.DRAIN_REASON`) |
  | True cast | a cast's own events, the countered one too | plus `true_cast: true` |
  | Opposed roll | `counter_result` | plus `caster_familiarity`, and `familiarity` and `familiarity_source` per defender |

- **Snapshot.** `crafting`, `granted_workings` and `chosen_true_cast` are
  written only when set, and `BattleState.from_dict` rebuilds `CraftState`,
  so a battle that uses none snapshots as it always has.
- **Public names added.** In `magic`: `TrueMagicRules`, `WorkingEffect`,
  `InterruptionRules`, `CastingEnding`, `FamiliaritySource`,
  `WORKING_KEY_PREFIX`, `WORKING_KEY_SEPARATOR`, `is_working_key`,
  `spell_for`, `runaway_band`, `failed_band`, `spell_skill_level`;
  `MagicRules.true_magic`, `true_level`,
  `true_castable`, `tier_cap`, `unlifted_tier_cap`, `past_tier_cap`,
  `granted`, `cast_kind`; `ChannelDerivation.skill_level`;
  `CounterspellRules.familiarity_per_level`,
  `counts_familiarity`, `familiarity_bonus`, `caster_familiarity`,
  `defender_familiarity`; and on `TrueMagicRules`, `working_key`,
  `effect_and_tier`, `working`, `working_from_key`, `tier_cap`,
  `turns_for`, `gathered_after`, `gathered`, `share_due`, `roll_target`,
  `versus`, `drained_mana`, `drain`, with `WorkingEffect.dice_at`,
  `highest_buildable_tier` and `check_tier`. In `state`: `CraftState`,
  `CombatantState.crafting`, `chosen_true_cast` and `granted_workings`,
  `BattleState.teammates_of`. In `spells`: `Spell.generic_counter`,
  `element_siblings`, `generic_counter_level`, `GENERIC_COUNTER_LADDER`,
  `PROTECTION_SCHOOL`. In `actions`: `CRAFT_OPTION`. In `engine`:
  `TurnRunner.craft_working`, `CASTING_DICE`, `DRAIN_REASON`,
  `CRAFT_BEGUN`, `CRAFT_CONTINUED`, `CRAFT_LOSS_REASONS`. In `policy`:
  `Candidate.true_cast`, `TRUE_CAST_MARK`.
- **Unchanged without it.** With `true_magic=None` and
  `familiarity_per_level=0`, every battle and every menu is byte-identical
  to v0.9.21. `tests/test_event_stream_regression.py` replays 22 seeded
  battles recorded on v0.9.21, under the default profile and with every
  v0.9.21 magic mechanic injected, and compares each event, decision menus
  included, and the state each battle ends in. The one change a caller sees
  without injecting anything is the catalog's five generic counters.

The readings the engine takes on the page's silences are in
`tarmar_engine.house_rulings` (`craft_gathers_even_shares`,
`working_mana_is_its_tier`, `crafting_ends_continuing_spells`,
`another_action_abandons_the_craft`, `craft_begins_where_a_cast_may`,
`working_aimed_by_its_last_craft_action`,
`craft_fixes_push_and_borrow_at_the_start`,
`craft_hit_is_damage_that_lands`, `interruption_takes_the_injury_band`,
`working_runaway_costs_the_mana_gathered`, `what_else_breaks_a_craft`,
`working_goes_off_after_the_actions`, `failed_working_keeps_its_mana`,
`drain_counts_the_whole_working`, `healing_working_names_its_figure`,
`narrated_working_rolls_its_tier`, `pushed_working_makes_one_control_roll`,
`craft_borrows_share_by_share`, `true_cast_rolls_the_spells_own_roll`,
`true_cast_familiarity`, `working_is_countered_when_it_goes_off`,
`channel_lift_lifts_the_tier_cap`, `grant_stands_until_withdrawn`,
`generic_counter_is_learned_never_cast`,
`generic_counter_level_is_the_highest_rung_known`,
`generic_counters_climb_one_ladder`, `generic_counter_reads_a_workings_tier`,
`ai_crafts_up_to_its_cap`, `ai_crafts_what_it_can_survive`), each marked
for Spencer.

## The schools page without Counter or True Magic (v0.9.23)

The vendored `spec/magic/schools-of-magic.md` no longer lists Counter or
Arcane/True Magic among the less common schools, and a line below that list
points to the True Magic page instead (tarmar-engine #81, following
smarks/tarmar-studio#208). Protection's Counter rungs are unchanged. It is a
spec-only release: no code, catalog entry or event changes, and the drift
guards read only the spell lists and the Elemental line, which stay as they
were.

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
