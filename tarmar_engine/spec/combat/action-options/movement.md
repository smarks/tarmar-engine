# Movement

**Hex Size:** 5 feet (1.5 meters). Each movement phase is approximately 5 seconds.

**Megahex:** 7 hexes (1 center + 6 surrounding). Used for missile range.

![Megahex diagram](/static/images/megahex.png)

**Movement Modifier:** CON modifier + STR modifier + DEX modifier

| Speed       | Distance (hexes) | Fatigue Cost                 | Available Actions               |
| ----------- | ---------------- | ---------------------------- | ------------------------------- |
| Sprint      | 18 + modifier    | 6 per turn                   | None                            |
| Run         | 12 + modifier    | 1 per turn                   | None                            |
| Jog         | 7 + modifier     | None (combat) / 1 per 10 min | Charge Attack, Dodge, Drop      |
| Walk        | 4 + modifier     | None                         | Ready Weapon                    |
| Walk (slow) | Up to 2 hex      | None                         | Cast Spell, Missile, Disbelieve |
| Stand Still | 0                | None                         | Stand Up, Pick Up Weapon        |

*Example: A character with CON 14 (+1), STR 10 (0), DEX 12 (0) has a movement modifier of +1. They walk 5, jog 8, run 13, sprint 19.*

## Encumbrance

Equipment weights are listed in pounds (lbs). For reference, 1 stone = 14 lbs. Based on weight carried compared to STR:

| Load Level   | Weight Carried     | Movement Penalty | Restrictions               |
| ------------ | ------------------ | ---------------- | -------------------------- |
| Unencumbered | Up to STR lbs      | 0                | —                          |
| Light        | STR+1 to STR×1.5   | −1               | —                          |
| Medium       | STR×1.5+1 to STR×2 | −2               | Cannot Run or Sprint       |
| Heavy        | STR×2+1 to STR×2.5 | −4               | Cannot Jog, Run, or Sprint |
| Overloaded   | Over STR×2.5       | —                | Walk only, 1 hex max       |

**Dropping a Pack:** Free action at start of movement. Picking up takes one full turn.

## Facing

Each figure faces one hex side, determining front, side, and rear hexes.

![Facing diagram](/static/images/facing.png)

- Physical attacks: Only into your front hexes
- Spells: Your hex, adjacent hexes, or any hex "in front"
- Prone/crawling: All hexes count as rear

## Engagement

| Figure Type    | Engaged When...                                       |
| -------------- | ----------------------------------------------------- |
| One-hex figure | In an armed enemy's front hex                         |
| 3–6 hex figure | In front hexes of 2+ one-hex figures (or 1 multi-hex) |
| 7-hex figure   | In front hexes of 3+ one-hex figures (or 1 multi-hex) |

Figures **stop immediately** when engaged.
