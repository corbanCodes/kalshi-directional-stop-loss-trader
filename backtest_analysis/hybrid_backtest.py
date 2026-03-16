#!/usr/bin/env python3
"""
HYBRID BACKTEST: Best of Both Worlds

S2 Dynamic Edge system + Bankroll % scaling

S2's edge detection + dynamic scaling BUT:
- Scale with % of bankroll (not fixed $10-$50)
- Optional: Add martingale recovery on top
"""

import pandas as pd
import math

def kalshi_fee(price_cents):
    price = price_cents / 100
    fee = 0.07 * price * (1 - price)
    return max(0.01, fee)


def run_hybrid_backtest(
    df,
    starting_bankroll=1000,
    base_pct=0.02,          # Base: 2% of bankroll
    max_pct=0.08,           # Max: 8% of bankroll when edge is high
    edge_scale_factor=0.20,  # How much edge scales bet (0.20 = 20% edge = max bet)
    use_martingale=False,
    max_recovery=2,
    name="Hybrid"
):
    """
    Hybrid system:
    - Uses S2's edge-based entries
    - Scales bet as % of bankroll based on edge
    - Optional martingale recovery
    """
    bankroll = starting_bankroll
    trades = 0
    wins = 0
    losses = 0
    max_drawdown = 0
    peak = starting_bankroll

    # Martingale state
    consecutive_losses = 0
    total_loss_to_recover = 0

    for _, row in df.iterrows():
        entry_price = row['Entry Price']
        outcome = row['Outcome']
        edge_pct = row['Edge %']

        # Get edge
        if pd.isna(edge_pct) or edge_pct == 'N/A':
            edge = 0.10
        else:
            edge = float(edge_pct) / 100

        # Scale bet based on edge
        # Higher edge = bigger % of bankroll
        scale = min(1, max(0, (edge - 0.10) / edge_scale_factor))
        bet_pct = base_pct + scale * (max_pct - base_pct)

        # Calculate bet size
        if use_martingale and consecutive_losses > 0:
            # Recovery mode: need to recover losses + 10%
            fee_per = kalshi_fee(entry_price)
            net_profit_per = (1.0 - entry_price/100) - fee_per
            recovery_target = total_loss_to_recover * 1.10
            if net_profit_per > 0:
                contracts = math.ceil(recovery_target / net_profit_per)
            else:
                contracts = 1
            bet_amount = contracts * (entry_price / 100)
        else:
            # Normal bet
            bet_amount = bankroll * bet_pct
            contracts = max(1, int(bet_amount / (entry_price / 100)))

        fee = contracts * kalshi_fee(entry_price)
        cost = contracts * (entry_price / 100)

        # Check affordability
        if cost > bankroll:
            # Can't afford this recovery - skip or reset
            if use_martingale and consecutive_losses > max_recovery:
                consecutive_losses = 0
                total_loss_to_recover = 0
            continue

        trades += 1

        if outcome == 'win':
            wins += 1
            payout = contracts * 1.0
            profit = payout - cost - fee
            bankroll += profit

            if use_martingale:
                consecutive_losses = 0
                total_loss_to_recover = 0
        else:
            losses += 1
            loss = cost + fee
            bankroll -= loss

            if use_martingale:
                consecutive_losses += 1
                total_loss_to_recover += loss
                if consecutive_losses > max_recovery:
                    consecutive_losses = 0
                    total_loss_to_recover = 0

        # Track drawdown
        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    profit = bankroll - starting_bankroll
    roi = (bankroll / starting_bankroll - 1) * 100

    print(f"\n{'='*60}")
    print(f"{name}")
    print(f"Base: {base_pct*100:.0f}% | Max: {max_pct*100:.0f}% | Martingale: {use_martingale}")
    print(f"{'='*60}")
    print(f"Trades: {trades} | W/L: {wins}/{losses} ({wins/trades*100:.1f}%)")
    print(f"Final: ${bankroll:,.2f}")
    print(f"PROFIT: ${profit:,.2f}")
    print(f"ROI: {roi:.1f}%")
    print(f"Max Drawdown: {max_drawdown:.1f}%")

    return bankroll, profit, roi, max_drawdown


if __name__ == "__main__":
    s2_path = "/Users/corbandamukaitis/Downloads/s2_dynamic_scaled_wait5_trades.csv"
    df = pd.read_csv(s2_path)

    print("\n" + "="*70)
    print("HYBRID BACKTEST: S2 Edge Detection + Bankroll % Scaling")
    print("Starting bankroll: $1,000 | Period: 11 days")
    print("="*70)

    results = []

    # Test 1: Original S2 behavior (fixed $10-$50)
    print("\n### BASELINE: Original S2 (fixed amounts) ###")
    run_hybrid_backtest(df, base_pct=0.01, max_pct=0.05, name="Original S2 (~$10-$50)")

    # Test 2: Bankroll % scaling (no martingale)
    print("\n### HYBRID: Bankroll % Scaling (no martingale) ###")

    for base, mx in [(0.01, 0.03), (0.02, 0.05), (0.02, 0.08), (0.03, 0.10)]:
        b, p, r, d = run_hybrid_backtest(df, base_pct=base, max_pct=mx, use_martingale=False,
                                         name=f"Hybrid {base*100:.0f}%-{mx*100:.0f}%")
        results.append((f"{base*100:.0f}%-{mx*100:.0f}%", p, r, d))

    # Test 3: Hybrid + Martingale
    print("\n### HYBRID + MARTINGALE ###")

    for base, mx in [(0.01, 0.03), (0.02, 0.05)]:
        b, p, r, d = run_hybrid_backtest(df, base_pct=base, max_pct=mx, use_martingale=True,
                                         name=f"Hybrid+Martingale {base*100:.0f}%-{mx*100:.0f}%")
        results.append((f"MART {base*100:.0f}%-{mx*100:.0f}%", p, r, d))

    # Summary
    print("\n" + "="*70)
    print("SUMMARY: All Configurations")
    print("="*70)
    print(f"{'Config':<25} {'Profit':>12} {'ROI':>10} {'Max DD':>10}")
    print("-"*60)
    for name, profit, roi, dd in sorted(results, key=lambda x: -x[1]):
        print(f"{name:<25} ${profit:>10,.2f} {roi:>9.1f}% {dd:>9.1f}%")
