# Backtest Summary - March 18, 2026

## Data
- **Period:** March 5-9, 2026 (3.5 days)
- **Markets:** 292 unique 15-minute BTC markets
- **Data Points:** 167,466 rows

---

## KEY FINDING: Entry Price Matters Enormously

### 80-92c Range (Original Strategy)
| Metric | Value |
|--------|-------|
| Opportunities | 78 |
| Win Rate | 88.5% |
| Max Losing Streak | 2 |
| **Problem** | 80-84c entries have NEGATIVE expected value! |

The 82c entry price had only **25% win rate** - those cheap prices are cheap for a reason!

### 85-92c Range (Refined Strategy)
| Metric | Value |
|--------|-------|
| Opportunities | 56 |
| Win Rate | **94.6%** |
| Max Losing Streak | **1** |
| Flat Bet EV | **+$0.04/contract** |

---

## Results by Bankroll Size

### Flat Betting (2% of bankroll per trade, no martingale)

| Bankroll | Final | P&L | ROI |
|----------|-------|-----|-----|
| $40 | $42.22 | +$2.22 | **+5.6%** |
| $400 | $418.93 | +$18.93 | **+4.7%** |
| $4,000 | $4,198.51 | +$198.51 | **+5.0%** |

### Martingale (loss-only recovery, max 2 attempts)

| Bankroll | Final | P&L | ROI |
|----------|-------|-----|-----|
| $40 | $44.81 | +$4.81 | **+12.0%** |
| $400 | $441.59 | +$41.59 | **+10.4%** |
| $4,000 | $4,433.80 | +$433.80 | **+10.8%** |

---

## Why Original Backtest Lost Money

The original 80-92c strategy hit a **3-loss sequence** early:

```
Bet #1: 8 contracts @ 91c  -> LOSS -> -$7.36
Bet #2: 47 contracts @ 82c -> LOSS -> -$39.48
Bet #3: 293 contracts @ 83c -> LOSS -> -$246.12
TOTAL SEQUENCE LOSS: -$292.96
```

This single sequence wiped out 73% of the $400 bankroll!

**Root Cause:** Taking recovery bets at 82-83c (low prices with poor win rates)

---

## Recommendations

1. **Raise minimum entry to 85c** - Dramatically improves win rate
2. **Keep recovery cap at 85c** - Already in place, good
3. **Consider 88c+ for even safer entries** - 100% win rate in this dataset
4. **More data needed** - 56 opportunities over 3.5 days is small sample

### Expected Performance (85-92c, martingale)
- **Win Rate:** ~95%
- **ROI per day:** ~2-3%
- **Risk:** Max observed losing streak was 1

### Caution
- This is only 3.5 days of data
- 3 consecutive losses WILL eventually happen
- Keep bankroll sized to survive worst case

---

## Files
- `backtest_strategy.py` - Original backtest script
- `backtest_results.json` - Detailed trade-by-trade results
