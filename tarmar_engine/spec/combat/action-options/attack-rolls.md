# Attack Rolls

Combat attacks use a **d20 roll-over**. Roll **1d20, add your to-hit bonus, and
compare to the Target Number (TN)**:

```
d20 + to-hit bonus ≥ Target Number  →  HIT
```

(Only combat attacks use the d20. A **skill check** rolls **2d6 + the
governing attribute's modifier + your skill level** against a difficulty the
GM sets, as the [Skills Glossary](../../../characters/prior-experience/skills-glossary)
defines it — that page is the source. An attribute check with no skill,
survival saves, and spellcasting still roll **3d6 ≤ attribute**.)

## To-Hit Bonus

Add these together:

| Source | Value |
| ------ | ----- |
| DEX combat modifier | `floor((effective DEX − 10) / 2)` — DEX 10 → +0, 14 → +2, 18 → +4 |
| Weapon skill | **+1 per skill level** with the weapon the skill was taken for, and **half your level (rounded down)** with another weapon of the same class (see below; Unusual weapons transfer nothing) |
| Under-strength penalty | −1 per point your effective STR is below the weapon's `str_req` (see below) |
| Situational modifiers | flanking, position, range, footing (see [[dex-adjustments]]) |

Effective DEX already has your armour's DEX penalty baked in, so heavy armour
drags down your aim as well as protecting you.

### Weapon Skill

A **Weapon** skill is taken for one specific weapon, and each level of it is
worth **+1 to hit** with that weapon. There is **no cap** — on the level or on
the bonus. Level 3 in Weapon (Broadsword) is +3 with a broadsword; level 8 is
+8.

With **any other weapon of the same class** — the same classes the Target
Number matrix uses, below — the skill is worth **half your level, rounded
down**. Level 3 in Weapon (Broadsword), a Striking weapon, is therefore +1 with
a mace or a war ax. Against a weapon of a *different* class the skill does
nothing: broadsword training buys you no accuracy with a longbow.

**Unusual weapons** (quarterstaff, net, whip, lasso, and their kin) are the
exception: each needs its **own** Weapon skill, and no half-level transfer
applies in either direction — even between two Unusual weapons.

Levels are bought with skill points and cost the same for every skill (one
exception: **Arcane** costs double — see the glossary): **level 1
costs 1 point, level 2 costs 2 more, level 3 costs 3 more**, and so on — so
reaching level N costs **N(N+1)/2** points in total (1, 3, 6, 10, 15, 21, …).
See [Skills Glossary](../../../characters/prior-experience/skills-glossary).

### Wielding a Weapon You're Too Weak For

You can **always** swing a weapon you lack the strength for — you just do it
badly. If your effective STR is below the weapon's `str_req`, take a to-hit
penalty equal to the shortfall: **−1 per point under**.

`STR-fit penalty = min(0, effective STR − str_req)`

Examples: STR 12 with a weapon needing 12 → no penalty. STR 12 with one needing
14 → **−2 to hit**. STR 10 with one needing 15 → **−5 to hit**. Strength buys
*access*, not accuracy — exceeding the requirement gives no bonus.

## Target Number

```
TN = base TN (weapon-class × armour-tier matrix, below)
   + shield bonus
   + defender's DEX-dodge modifier
```

The base difficulty comes entirely from **how your weapon's attack motion fares
against the defender's armour** — not from DEX. DEX only feeds your aim (above)
and the defender's dodge.

- **Defender's DEX-dodge** = `floor((defender effective DEX − 10) / 2)`, minimum 0.
- **Shield bonus** (added to TN): small/spike shield +1, large shield +2,
  tower shield +3, main-gauche +1 (parry).

### Weapon Classes

Weapons are grouped by how their attack defeats armour, not by shape:

- **Piercing** — light quick points (dagger, rapier): brutal on the soft, futile on plate.
- **Striking** — one-handed cuts/blunt (broadsword, mace, club): even across all armour.
- **Thrusting** — braced/reach points (spear, trident): good vs medium, fades vs heavy.
- **Heavy Striking** — two-handed crush/cleave (great sword, great hammer, battle axe): high floor, but momentum cracks plate.
- **Heavy Thrusting** — braced polearms & lances (halberd, pike, lance): leverage punches through the heaviest armour.
- **Missile — Bows** — bows, slings, thrown: fade hard vs heavy armour.
- **Missile — Crossbows** — crossbows: flat vs armour, punch plate.
- **Flexible / Snare** — whip, net, lasso, bola: control weapons, poor vs anything rigid.

### Armour Tiers

| Tier | Armour |
| ---- | ------ |
| None | unarmoured |
| Light | cloth, leather |
| Medium | chainmail |
| Heavy | half-plate, plate, fine plate |

### Base Target Number Matrix

| Class | None | Light | Medium | Heavy |
| ----- | ---- | ----- | ------ | ----- |
| Piercing | 11 | 14 | 18 | 22 |
| Striking | 13 | 14 | 16 | 18 |
| Thrusting | 12 | 14 | 16 | 19 |
| Heavy Striking | 14 | 14 | 15 | 16 |
| Heavy Thrusting | 14 | 14 | 15 | 15 |
| Missile — Bows | 12 | 14 | 17 | 20 |
| Missile — Crossbows | 13 | 14 | 15 | 16 |
| Flexible / Snare | 13 | 16 | 19 | 22 |

A TN of 21–22 cannot be met on a raw die — a dagger against full plate (22) is
impossible without skill, a found gap (situational bonus), or a natural 20.

## Natural Rolls

- **Natural 20** → auto-hit regardless of TN, and a **critical**: roll the weapon's
  damage dice **twice**. Then **confirm** — roll a second d20 to-hit against the
  same TN; if it also hits, the blow is severe: **triple damage + bleeding**,
  reaching **Body** as well as Fatigue. The confirm uses the same TN with the
  same bonus, and its own naturals only decide the confirmation: a natural 1 on
  the confirm simply fails to confirm — it is not a second fumble — and a
  natural 20 confirms automatically.
- **Natural 1** → auto-miss, and a **fumble**. Roll 1d6: **1–3** off-balance (−2 to
  your next action) · **4–5** drop weapon · **6** weapon takes stress (breaks on a
  second fumble).

## Damage and Armour

Armour does **two jobs**: it raises the TN (above), *and* on a hit its `stops`
still subtract from damage.

```
damage = weapon dice + damage_mod − stops   (floored at 0)
```

**Exception (plate-cracking):** a **Heavy Striking** or **Heavy Thrusting** weapon
that beats a **Heavy**-armour target on the matrix ignores **half** the armour's
`stops` — the impact transfers through the plate.

Damage applies to **Fatigue** first; a severe crit reaches **Body** as well.
