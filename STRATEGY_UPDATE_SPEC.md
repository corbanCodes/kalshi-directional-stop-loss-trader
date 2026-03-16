# 15-Minute Strategy Bot - Update Specification

**Date:** March 8, 2026
**Status:** IMPLEMENTED

---

## Implementation Summary

All core changes have been implemented:

- [x] **max_consecutive_losses: 2** (3 bets total: base + 2 recovery stages)
- [x] **Recovery formula: loss + profit → LOSS ONLY** (saves money)
- [x] **Recovery price cap: 85c** (more conservative than base 80-92c)
- [x] **Base strategy range: 80-90c → 80-92c**
- [x] **Distance filter: 0.15% BTC from strike** (recovery mode only)
- [x] **Kraken zero handling: retry logic** (waits for valid price)
- [x] **Downloadable logs** (/api/logs endpoint)
- [x] **Strategy explanation page** (/strategy endpoint)
- [x] **Recovery mode dashboard banner**

### Math Verification (at $442 bankroll, 85c recovery price)

```
Bet 1 (Base):       11 contracts @ 85c = $9.35
Bet 2 (Recovery 1): 63 contracts @ 85c = $53.55 (total: $62.90)
Bet 3 (Recovery 2): 420 contracts @ 85c = $357.00 (total: $419.90)
─────────────────────────────────────────────────────────
TOTAL RISK:   $419.90 (multiplier: ~45x)
BUFFER:       $22.10 remaining
PROFIT/WIN:   ~$1.55
```

---

## Executive Summary

This document outlines all requested changes to the 15-minute BTC trading strategy bot. The core strategy is working, but needs refinements to the martingale recovery system, addition of distance filters, bankroll management improvements, and enhanced logging.

---

## Table of Contents

1. [Current State Analysis](#current-state-analysis)
2. [Strategy Changes](#strategy-changes)
3. [Martingale Recovery Overhaul](#martingale-recovery-overhaul)
4. [Distance Filter Implementation](#distance-filter-implementation)
5. [Bankroll Management](#bankroll-management)
6. [Logging & Verification](#logging--verification)
7. [UI Enhancements](#ui-enhancements)
8. [Implementation Checklist](#implementation-checklist)

---

## Current State Analysis

### What's Working
- Basic strategy: Wait 10 min, bet on 80-90c prices
- Kalshi API integration (orders, balance, settlements)
- Kraken integration (instant BTC price for settlement)
- Dashboard with real-time updates
- Persistent state tracking (trades, martingale state)
- TRUE martingale (recovers loss + original profit target)

### Current Issues
1. **Martingale recovers loss + profit** - Should recover loss ONLY to save money
2. **Max 2 recovery attempts** - Should be 3 recovery attempts
3. **No distance filter** - Backtest shows 0.15% filter improves win rate to ~90%
4. **No recovery price cap** - Should cap recovery entries at 85c
5. **Single bankroll** - Need apportioned bankroll for multiple bots
6. **Limited logging** - Need more timestamps, order details, downloadable logs
7. **Price range 80-90c** - Should be 80-92c for base strategy

---

## Strategy Changes

### Base Strategy (No Recovery)
| Parameter | Current | New |
|-----------|---------|-----|
| Wait time | 10 min (5 min remaining) | 10 min (5 min remaining) - **No change** |
| Price range | 80-90c | **80-92c** |
| Distance filter | None | **None** (only for recovery) |
| Max contracts | Dynamic based on bankroll | **Dynamic based on APPORTIONED bankroll** |

### Recovery Strategy (After Loss)
| Parameter | Current | New |
|-----------|---------|-----|
| Max recovery attempts | 2 (3 total bets) | **3 (4 total bets)** |
| Recovery target | Loss + Original profit | **Loss ONLY** |
| Price cap | 80-90c | **85c max** |
| Distance filter | None | **0.15% minimum** |
| Wait time | 10 min | 10 min - **No change** |

### Recovery Math Change

**CURRENT (recovers loss + profit):**
```
Recovery contracts = (total_loss + original_profit) / net_profit_per_contract
```

**NEW (recovers loss ONLY):**
```
Recovery contracts = total_loss / (1 - price_of_contract)
```

Example at 85c entry:
- Lost 1 contract at 85c = $0.85 loss
- Recovery formula: $0.85 / (1 - 0.85) = $0.85 / $0.15 = 5.67 contracts
- Round up to 6 contracts
- If recovery 1 loses: need to recover $0.85 + (6 * $0.85) = $5.95
- $5.95 / $0.15 = 39.67 = 40 contracts

### Bankroll Factor for 3 Recovery Stages

With 3 recovery attempts capped at 85c:
- Base bet: X contracts
- Recovery 1: ~6X contracts
- Recovery 2: ~40X contracts
- Recovery 3: ~267X contracts

**Total multiplier: ~285X base bet**

Example: $1 base bet requires ~$285 bankroll to survive 3 consecutive losses.

---

## Martingale Recovery Overhaul

### Changes Required in `src/martingale.py`

1. **Change `max_consecutive_losses` from 2 to 3**
```python
def __init__(self, max_consecutive_losses: int = 3):  # Changed from 2
```

2. **Remove profit from recovery calculation**
```python
def calculate_recovery_bet(self, entry_price_cents: int) -> MartingaleBet:
    # OLD: needed_profit = self.state.total_loss_dollars + self.state.base_target_profit_dollars
    # NEW: Recovery only needs to cover losses
    needed_recovery = self.state.total_loss_dollars

    # Use the simplified formula: loss / (1 - price)
    price = entry_price_cents / 100
    contracts_needed = math.ceil(needed_recovery / (1 - price))
```

3. **Add price cap for recovery mode**
```python
def calculate_recovery_bet(self, entry_price_cents: int) -> MartingaleBet:
    # Cap recovery entries at 85c
    if entry_price_cents > 85:
        return None  # Don't take recovery bets above 85c
```

4. **Update `find_max_safe_contracts` for 3 losses**
```python
def find_max_safe_contracts(self, bankroll: float, recovery_price: int = 85) -> int:
    """
    Find max contracts that survive 3 losses at 85c recovery entries.
    Uses ~285x multiplier.
    """
    # Total risk for 4 bets at 85c = base * 285
    return int(bankroll / 285 / 0.85)
```

### Changes Required in `src/trader.py`

1. **Skip recovery opportunities above 85c**
2. **Add distance filter check in recovery mode**

---

## Distance Filter Implementation

### Background

From backtest analysis:
- **77% of losses** occur when BTC is < 0.15% from strike price
- Adding 0.15% distance filter: **90.4% win rate, max 1 consecutive loss**
- Filter only applies during **recovery mode** (base strategy stays as-is for more opportunities)

### Implementation

Add to `src/kraken.py`:
```python
@staticmethod
def get_btc_distance_from_strike(strike_price: float) -> tuple[float, str]:
    """
    Get BTC distance from strike price.

    Returns:
        (distance_percent, direction) where direction is 'above' or 'below'
    """
    btc_price = KrakenClient.get_btc_price()
    if btc_price is None or btc_price == 0:
        return None, None  # Ignore zero values

    distance = (btc_price - strike_price) / strike_price * 100
    direction = 'above' if distance > 0 else 'below'
    return abs(distance), direction
```

Add to `src/market_scanner.py` or `src/trader.py`:
```python
def passes_distance_filter(self, opportunity: TradingOpportunity, min_distance: float = 0.15) -> bool:
    """
    Check if opportunity passes the BTC distance filter.

    For YES bets: BTC must be >= 0.15% ABOVE strike
    For NO bets: BTC must be >= 0.15% BELOW strike
    """
    distance, direction = KrakenClient.get_btc_distance_from_strike(opportunity.floor_strike)

    if distance is None:
        return False  # Skip if no valid BTC price

    if distance < min_distance:
        return False  # Too close to strike

    # Direction must match the bet side
    if opportunity.side == "yes" and direction != "above":
        return False
    if opportunity.side == "no" and direction != "below":
        return False

    return True
```

### When to Apply

| Mode | Apply Distance Filter? |
|------|------------------------|
| Base bet (no recovery) | NO |
| Recovery stage 1 | YES (0.15% minimum) |
| Recovery stage 2 | YES (0.15% minimum) |
| Recovery stage 3 | YES (0.15% minimum) |

---

## Bankroll Management

### Current Problem

- Bot uses total Kalshi balance for calculations
- Can't run multiple bots for different cryptos (BTC, ETH, SOL)
- No way to specify starting contract amount

### Solution: Apportioned Bankroll

Add configuration for:
1. **Apportioned bankroll** - The amount allocated to THIS bot instance
2. **Starting contracts** - Override for initial base contract count
3. **Max base bet** - Cap on base bet size (e.g., $25)

### UI Inputs Needed

```
Apportioned Bankroll: $_________ (defaults to Kalshi balance)
Starting Contracts:   _________ (optional override)
Max Base Bet:         $_________ (optional cap, e.g., $25)
```

### Safety Indicator

Display calculation:
```
Apportioned: $1,000
Max safe contracts: 3 (at 85c = $2.55 base bet)
Total risk for 3 losses: ~$725
Safety margin: $275 (27.5%)
Status: SAFE
```

### Implementation

Add to `src/config.py`:
```python
@dataclass
class TradingConfig:
    apportioned_bankroll: float = None  # None = use full Kalshi balance
    starting_contracts: int = None  # None = calculate from bankroll
    max_base_bet_dollars: float = 25.0  # Cap at $25
```

Add to dashboard HTML:
- Input fields for these values
- Real-time calculation of safety margin
- Warning when unsafe

---

## Logging & Verification

### Current Gaps

1. No timestamps showing when orders were placed
2. No distinction between limit vs market orders
3. No record of intended vs actual fill prices
4. No downloadable logs
5. No verification that Kalshi fees match calculations

### Enhanced Logging Format

Each trade should log:
```json
{
  "timestamp_utc": "2026-03-08T22:15:33.123Z",
  "trade_id": "abc123",
  "ticker": "KXBTC15M-26MAR081500-00",

  "intent": {
    "side": "yes",
    "contracts": 3,
    "target_price_cents": 85,
    "order_type": "limit",
    "limit_price_cents": 86
  },

  "execution": {
    "order_id": "kalshi_order_xyz",
    "filled_contracts": 3,
    "actual_fill_price_cents": 85,
    "slippage_cents": 0,
    "fill_time_ms": 234
  },

  "settlement": {
    "btc_price": 87234.56,
    "floor_strike": 87200.00,
    "result": "yes",
    "won": true,
    "gross_payout_cents": 300,
    "fee_cents": 6,
    "net_profit_cents": 9
  },

  "martingale": {
    "bet_number": 1,
    "is_recovery": false,
    "consecutive_losses_before": 0,
    "recovering_dollars": 0.0
  },

  "bankroll": {
    "before_cents": 44207,
    "after_cents": 44216,
    "apportioned_cents": 100000
  }
}
```

### Downloadable Logs

Add to dashboard:
- Button: "Download Today's Log" (JSON)
- Button: "Download All Logs" (JSON or CSV)
- Activity log should be scrollable and persistent

### Kalshi Fee Verification

Kalshi fee structure:
```
Fee = MIN(7 cents per contract, 15% of profit)

But there's volume discounts:
- 0-99 contracts: 7c per contract
- 100-999: 5c per contract
- 1000-4999: 3c per contract
- 5000+: 1c per contract
```

Need to:
1. Query actual fee from Kalshi API post-trade
2. Compare to our calculation
3. Log any discrepancies

---

## UI Enhancements

### New Dashboard Elements

1. **Apportioned Bankroll Section**
   - Input: Apportioned bankroll amount
   - Input: Starting contracts override
   - Display: Calculated safety margin
   - Display: Max recoverable sequence

2. **Strategy Explanation Link**
   - Link to `/strategy-explanation` page or modal
   - Explains the entire strategy in plain terms

3. **Enhanced Activity Log**
   - Timestamps for every action
   - Order placement details
   - Fill confirmations
   - Settlement results
   - Recovery mode status

4. **Trade History Table**
   - All trades with full details
   - Sortable/filterable
   - Downloadable as CSV

5. **Multi-Crypto Support**
   - Dropdown to select: BTC, ETH, SOL
   - Each runs independently with its own apportioned bankroll

### Strategy Explanation Page Content

Create `/strategy-explanation` or a modal with:

```markdown
# How This Strategy Works

## The Basic Idea
We bet on 15-minute BTC price windows when the odds are heavily in our favor
(80-92% implied probability) with only 5 minutes remaining.

## Entry Criteria
1. Wait 10 minutes into the 15-minute window
2. Look for YES or NO priced at 80-92 cents
3. Place a limit order 1 cent above ask

## Why It Works
- 88%+ historical win rate at these prices/times
- Short window = less time for reversals
- High probability = small profit per trade, but consistent

## The Recovery System (Altered Martingale)
If we lose, we bet enough to recover just the loss (not extra profit).
- Max 3 recovery attempts
- Recovery entries capped at 85 cents
- Distance filter: BTC must be 0.15% away from strike in our direction
- If all 3 fail, we stop (accept the loss)

## Risk Management
- Base bet sized so bankroll survives 3 consecutive losses
- ~285x multiplier means $285 bankroll per $1 base bet
- Never bet money you can't afford to lose
```

---

## Implementation Checklist

### Phase 1: Core Strategy Changes
- [ ] Change max_consecutive_losses from 2 to 3
- [ ] Update recovery formula to loss-only (remove profit target)
- [ ] Add 85c price cap for recovery entries
- [ ] Change base strategy price range to 80-92c
- [ ] Update find_max_safe_contracts for 285x multiplier

### Phase 2: Distance Filter
- [ ] Add get_btc_distance_from_strike to kraken.py
- [ ] Add passes_distance_filter method
- [ ] Apply filter only in recovery mode
- [ ] Handle zero values from Kraken API

### Phase 3: Bankroll Management
- [ ] Add apportioned_bankroll to config
- [ ] Add starting_contracts override
- [ ] Add max_base_bet_dollars cap
- [ ] Update UI with input fields
- [ ] Add safety margin calculation and display

### Phase 4: Logging & Verification
- [ ] Enhance trade logging format
- [ ] Add timestamps to all actions
- [ ] Track intended vs actual execution
- [ ] Add downloadable log buttons
- [ ] Verify Kalshi fees against calculations

### Phase 5: UI Enhancements
- [ ] Add bankroll management section
- [ ] Add strategy explanation link/modal
- [ ] Enhance activity log
- [ ] Add trade history table
- [ ] Add multi-crypto dropdown (future)

---

## Questions to Resolve Before Implementation

1. **Recovery Formula Clarification**
   - You mentioned: `loss / (1 - price)`
   - This assumes we always recover at the same price we entered
   - If we recover at a DIFFERENT price, the formula changes
   - **Question:** Should recovery formula use current market price or be locked at 85c?

2. **Distance Filter Edge Cases**
   - If Kraken returns 0, we skip the trade
   - **Question:** Should we fallback to Kalshi's implied strike, or always skip?

3. **Multiple Bots**
   - You want to run separate bots for BTC, ETH, SOL
   - **Question:** Should these be separate deployments, or tabs in one dashboard?

4. **Max Base Bet Cap**
   - You mentioned $25 cap
   - **Question:** Is this $25 total cost or 25 contracts?

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/martingale.py` | Recovery formula, max losses, price cap |
| `src/market_scanner.py` | Price range 80-92c |
| `src/trader.py` | Distance filter for recovery, logging |
| `src/kraken.py` | Distance calculation, handle zeros |
| `src/config.py` | Apportioned bankroll, starting contracts |
| `src/trade_tracker.py` | Enhanced logging format |
| `main.py` | UI inputs, strategy explanation, downloads |

---

## Risk Acknowledgment

This is a gambling strategy. Even with 90%+ win rate:
- 10% loss rate means 1 in 10 trades loses
- 3 consecutive losses (0.1% chance per sequence) = BUST
- Over 1000 sequences: 63% chance of experiencing at least one bust

**Only use money you can afford to lose completely.**
