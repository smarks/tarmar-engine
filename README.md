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
`failure` (a `BorrowingFailure`: `key`, `label`, `is_runaway`),
`automatic_failure_totals`, `max_debt` (`None`: Channel alone bounds the
casting, so Channel rules must be injected too) and `refunds_pay_debt_first`.
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
  to any other mage (a spell known or a spell skill recorded) with the mana
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
  caster) to any mage against each enemy mage, once per commitment it can
  afford, at a score of 0: the AI never counters. A counter names a caster,
  not a cast: it is offered whether or not the caster has chosen its action
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
  `countered`. Every declared counter is spent either way.
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
  for Spencer). The menu's push lines name the Channel and say "past
  Channel N by a Game Master's ruling" (`policy.PAST_BY_RULING`) where a
  push passes it, and likewise for Push's cap.
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
  when non-empty), so a caller reads it instead of recomputing it. The
  menu's "by a Game Master's ruling" wording is decided from it too.

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
