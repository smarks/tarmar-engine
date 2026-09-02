# Hand-to-Hand & Grappling

Fighting bare-handed and grappling are both **Hand-to-Hand (HTH)** actions —
they only happen once you and an enemy share the tight, in-close range HTH
requires. This page covers unarmed strikes, initiating and resisting a
grapple, and how armour changes both. For the conditions that put you in
HTH range in the first place, and the base bare-handed damage table, see
[Special Combat Situations](../special-combat-situations).

## Entering Hand-to-Hand

You may move into an enemy's hex — the HTH option `o` **ATTEMPT HTH** from
[Action Options](../../action-options) — only if one of these holds: the
enemy has their back to a wall, is down/prone/kneeling, has a lower
movement modifier than you, you're attacking from their rear, or they
simply agree. Once you're sharing a hex, **both combatants get +4** to
their to-hit rolls (see [DEX Adjustments](../dex-adjustments)) — grappling
range cuts both ways.

## Unarmed Strikes

An unarmed strike (HTH option `t`, bare hands) uses the normal
[Attack Roll](../attack-rolls): `d20 + to-hit bonus ≥ Target Number`.

- **To-hit bonus:** your DEX combat modifier, the HTH +4, and any
  situational modifiers — same formula as any other attack. No Weapon
  skill applies; there is no bare-hands entry in the skill catalog; if you
  drew a dagger first (option `u`), use its own Weapon skill instead.
- **Target Number:** bare hands resolve on the **Striking** row of the
  [Target Number matrix](../attack-rolls#base-target-number-matrix) — the
  closest published class to a punch or a grab-and-strike. A shield still
  adds its usual TN bonus against an incoming punch; it's a physical
  object in the way regardless of what's swinging.
- **Damage:** use the [Bare-Handed Damage by
  STR](../special-combat-situations#bare-handed-damage-by-str) table, then
  subtract the target's armour `stops` exactly as in the normal damage
  formula. Against a Striking TN of 13–18, most unarmed strikes land; against
  Heavy armour's stops, most of them do nothing.
- **Naturals:** the usual [Natural Rolls](../attack-rolls#natural-rolls)
  apply — a natural 20 still confirms for a severe crit, a natural 1 still
  fumbles. Read "weapon takes stress" on the fumble table as "you jam or
  wrench something" — no weapon to break.

## Initiating a Grapple

Grappling is a separate HTH option: instead of striking, you attempt to
seize and hold the enemy. Make the same attack roll as an unarmed strike,
but check it against the **Flexible/Snare** row of the Target Number
matrix instead of Striking — grabbing and controlling a body is a control
technique, not a blow, and the matrix already prices control techniques as
"poor vs. anything rigid." A hit grapples the target instead of dealing
damage; no damage dice are rolled.

**Armour interaction:** this is where armour matters most. Flexible/Snare
TN runs 13 (unarmoured) → 16 (Light) → 19 (Medium) → 22 (Heavy) — a target
in plate is nearly ungrabbable (a 22 needs a natural 20, same as a dagger
against plate). Leather and cloth barely slow a grapple down; chainmail and
plate are real protection against it. A shield adds its normal TN bonus
here too — you can't close a grip around someone who's got a shield
between you.

**Naturals:** a natural 20 grapples automatically and the target is
briefly stunned — off-balance (−2 to their next action), no separate
confirm roll (there's no damage to double). A natural 1 fumbles: roll 1d6
on the usual [fumble table](../attack-rolls#natural-rolls), reading "drop
weapon" as "you overextend and end up prone" and "weapon takes stress" as
"off-balance instead" — there's no weapon to lose or break.

## Being Grappled

While grappled, you are held in your attacker's hex and your options
shrink to three, chosen in the Actions phase like anything else:

- **Struggle Free** — see [Escaping](#escaping), below.
- **Strike Back** — a normal unarmed strike (or dagger, if already drawn)
  against your captor, at the same HTH +4 both of you already have. A
  grappled hand can still throw an elbow or a headbutt.
- **Hold still** — do nothing and eat whatever your captor does next.

What you **cannot** do: move independently (Phases 3–4 below), ready a new
weapon or swap to one that needs a free hand, fire a missile weapon, gain
any benefit from a shield, or Dodge/Defend. Spellcasting is limited to a
spell you can cast with no hand gestures and no verbal component — Spell
Mastery level 2 and 3 respectively (see [Spell Mastery](../../../magic/spell-mastery)) — since one or both hands are occupied holding on or being held.

The grappler, meanwhile, chooses each turn between:

- **Maintain** — keep the hold; nothing else happens this turn.
- **Squeeze** — a bare-handed strike against the held target, using the
  same Bare-Handed Damage table and armour `stops` as any unarmed strike.
  The target gets no Dodge/Defend bonus against it — they're already held.
- **Release** — a free action that ends the grapple immediately.

## Escaping

Struggling free uses the same roll as a plain HTH [Disengage](../special-combat-situations#disengaging)
(option `v`) — **roll 4d6 ≤ effective DEX** — success stands you up and
moves you to an adjacent empty hex; failure leaves you held. Heavy armour's
DEX penalty is already baked into effective DEX, so the same armour that
made you hard to grab in the first place also makes you slower to break
free of one — there's no separate penalty to track.

## The Six-Phase Turn

Grappling doesn't add phases; it changes what happens inside the existing
[six phases](../../turn-sequence):

| Phase | What changes for a grapple |
| ----- | --------------------------- |
| 1. Initiative | Rolled normally by both combatants — it decides action order in Phase 5, not whether the grapple holds. |
| 2. Renew Spells | A grappled caster can only renew a spell that needs no gestures (Spell Mastery 2+); anything else lapses. |
| 3. Initial Movement | Neither combatant moves — both are locked to the shared hex until the grapple ends. |
| 4. Final Movement | Same as above — a grapple has no movement to yield or take. |
| 5. Actions | Grapple, Strike, Squeeze, Struggle Free, Strike Back, Hold Still, and Release are all resolved here, in adjDEX order, with the HTH +4 applying to both sides. |
| 6. Forced Retreat | Not available against — or to — a grappled figure; there's no hex to push someone into while you're holding them. Retreat becomes available again the turn after the grapple ends. |
