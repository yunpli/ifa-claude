# Scoring Principles — Continuous Strength Functions

> **Audience**: anyone modifying setup / factor / regime scoring across the
> TA, SmartMoney, Ningbo families.
> **Status**: enforced as of 2026-05-04 (commits `4b700b4` TA, `ec3df2d` SmartMoney).

---

## Why this matters

Boolean trigger scoring (`if X: score += V`) produces only **2-4 distinct
values per strategy per day**. With 5000 stocks scanned, hundreds saturate
at the maximum and lose any rank ordering. The system can't tell the
"truly exceptional" from the "barely qualified".

Real example (pre-fix):

```
T1_BREAKOUT 0428: 63 hits, only 2 distinct raw scores [0.70, 0.80]
C1_CHIP_CONCENTRATED 0428: 103 hits, only 4 distinct scores
                                                                 ↑ saturation
```

After continuous fix:

```
T1_BREAKOUT 0428: 75 hits, 58 distinct scores in [0.65, 0.80]
C1_CHIP_CONCENTRATED 0428: 91 hits, 89 distinct scores
```

Top-stock differentiation restored — `Tier A` 10 stocks now have 10
distinct ranks instead of all tying at 1.000.

---

## The pattern

**Replace** every boolean `if X: score += V`:

```python
# ❌ binary — crushes rank info
if close >= 1.02 * ma20:
    score += 0.20
    triggers.append("decisive_above_ma20")
```

**With** a continuous strength function:

```python
# ✅ continuous — preserves rank info
break_strength = max(0.0, min(1.0, (close / ma20 - 1.0) / 0.05))
score += 0.20 * break_strength    # 0% → 0,  5%+ → full +0.20
if break_strength >= 0.4:
    triggers.append("decisive_above_ma20")    # label-only threshold
```

### Anatomy of a strength function

```
strength = clip((measured - LO) / (HI - LO), 0, 1)
```

| Term | Meaning |
|---|---|
| `measured` | The signal you observe (e.g. `close/ma20 - 1`) |
| `LO` | Floor below which the signal is irrelevant (strength = 0) |
| `HI` | Ceiling above which more is meaningless (strength = 1) |
| `clip(..., 0, 1)` | Hard bounded so downstream math stays predictable |

The result is in `[0, 1]` and gets multiplied by the **bonus budget** for that
trigger (typically 0.20 for primary, 0.10 for secondary).

### Trigger labels

The `triggers.append("decisive_above_ma20")` line is **separate** from scoring
— it's just a tag for the audit/UI. We append it when `strength ≥ display_threshold`
(usually 0.3 - 0.5) so the report still shows readable trigger names instead
of raw numbers.

---

## Score budget per setup

Each TA setup has the same total budget so resonance / family math behaves predictably:

```
raw_score = 0.50  (base — all gate conditions passed)
          + 0.20 × primary_strength    (the dominant signal)
          + 0.10 × secondary_strength  (a confirming signal)
          ≤ 0.80
```

Some setups (e.g. R1 double_bottom) have 3 secondaries summing to 0.30
instead — same total budget.

---

## Multi-stage continuous flow

```
┌──────────────────┐  raw_score ∈ [0.5, 0.8]
│  Setup detector  │
└─────────┬────────┘
          ↓
┌──────────────────┐  + regime_boost (0.10 if regime ∈ suitable_regimes)
│  Ranker per-row  │  × winrate_factor (clip(winrate/30%, 0.4, 1.0))
└─────────┬────────┘
          ↓ adj_score
┌──────────────────┐  per-stock aggregation
│  Per-stock pass  │  primary = max(family_best.values())
└─────────┬────────┘  bonus = Σ family_score × [0.08, 0.05, 0.03] for 2nd-4th
          ↓
        stock_score ∈ [0, ~1.05]
```

### Why "decreasing weights" for resonance

```python
EXTRA_FAMILY_WEIGHTS = [0.08, 0.05, 0.03]   # 2nd, 3rd, 4th confirming family
```

A single setup at 0.80 + 3 extra families at 0.50 each:
```
stock_score = 0.80 + 0.50 × 0.08 + 0.50 × 0.05 + 0.50 × 0.03
            = 0.80 + 0.04 + 0.025 + 0.015
            = 0.880
```

A single setup at 0.80 + 3 extra families at 0.80 each (rare):
```
stock_score = 0.80 + 0.80 × 0.08 + 0.80 × 0.05 + 0.80 × 0.03
            = 0.80 + 0.064 + 0.040 + 0.024
            = 0.928
```

The bonus depends on **actual strength of confirming signals**, not just count.
Weak resonance (`[0.80, 0.50, 0.50, 0.50]`) gets a small boost; strong resonance
(`[0.80, 0.80, 0.80, 0.80]`) gets the most. No more "all 4-family stocks tie at the cap".

---

## Display rescaling

Internal `stock_score` ranges roughly `[0, 1.05]` (natural ceiling). We
rescale to `[0, 99.999]` for display so reports show 3-decimal scores like
`87.638` instead of `0.876`.

```python
display = stock_score / DISPLAY_MAX × 99.999    # DISPLAY_MAX = 1.05
```

Star thresholds use the display percentage:
```python
def stars(score):
    pct = score / DISPLAY_MAX
    if pct >= 0.85: return 5     # display ≥ 85
    if pct >= 0.75: return 4
    ...
```

---

## Where this is enforced

| Family · file | Pattern | Status |
|---|---|---|
| `ta/setups/*.py` (19 setups) | continuous strength | ✓ as of `4b700b4` |
| `ta/setups/ranker.py` | continuous resonance bonus | ✓ as of `4b700b4` |
| `smartmoney/factors/liquidity.py` | continuous attack/retreat/defense | ✓ as of `ec3df2d` |
| `smartmoney/factors/flow.py` | rank-based × weights (already continuous) | ✓ |
| `smartmoney/factors/role.py`, `cycle.py` | classifies on continuous inputs | ✓ |
| `ningbo/strategies/*.py` | `clip + weighted sum` (model pattern) | ✓ |
| `market/macro/asset/tech` | LLM narrative reports — no scoring engine | n/a |

---

## When adding a new setup / factor

1. **Identify gate conditions** — boolean checks that must all pass to enter
   (e.g., `close > ma20`, `ma20 > ma60`). These stay as `if not X: return None`.
2. **Identify strength signals** — the continuous magnitudes that determine
   conviction (e.g., how decisively above MA20). Encode each as a `clip` formula.
3. **Allocate budget** — base 0.5 + primary 0.2 + secondary 0.1 = max 0.8.
4. **Append trigger labels** at a display threshold (0.3-0.5) so audit story
   reads naturally.
5. **Test distribution**: run the new setup over 4-5 trade days, count distinct
   raw scores. Should be ≥30 (continuous), not ≤5 (boolean).

If you find yourself writing `if X: score += V`, stop. Use a strength function.

---

## See also

- `ifa/families/ta/setups/t1_breakout.py` — canonical example of continuous
  strength scoring
- `ifa/families/smartmoney/factors/flow.py` — rank-based continuous reference
- `ifa/families/ningbo/strategies/half_year_double.py` — multi-component
  weighted continuous scoring (oldest model)
