#!/usr/bin/env python3
"""
Compare S2 (Dynamic Scaled Wait 5) vs S3 (Sentiment Odds 80 Wait 10)
With correct Kalshi fees and martingale recovery simulation.
"""

import pandas as pd
import math
from datetime import datetime

# Correct Kalshi fee formula
def kalshi_fee(price_cents):
    """Kalshi fee: 0.07 * price * (1 - price), min 1 cent"""
    price = price_cents / 100
    fee = 0.07 * price * (1 - price)
    return max(0.01, fee)

def load_and_analyze(filepath, name):
    """Load CSV and compute basic stats."""
    df = pd.read_csv(filepath)

    wins = (df['Outcome'] == 'win').sum()
    losses = (df['Outcome'] == 'loss').sum()
    total = len(df)
    win_rate = wins / total * 100

    # Date range
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    start = df['Timestamp'].min()
    end = df['Timestamp'].max()
    days = (end - start).days + 1

    # Final bankroll (started at 1000)
    final_bankroll = df['Bankroll After'].iloc[-1]
    total_profit = final_bankroll - 1000
    roi = (final_bankroll / 1000 - 1) * 100

    print(f"\n{'='*60}")
    print(f"STRATEGY: {name}")
    print(f"{'='*60}")
    print(f"Period: {start.date()} to {end.date()} ({days} days)")
    print(f"Total trades: {total}")
    print(f"Wins/Losses: {wins}/{losses} ({win_rate:.1f}% win rate)")
    print(f"Final bankroll: ${final_bankroll:.2f}")
    print(f"Total profit: ${total_profit:.2f}")
    print(f"ROI: {roi:.1f}%")
    print(f"Daily ROI: {roi/days:.2f}%")
    print(f"Projected monthly ROI: {roi/days*30:.1f}%")

    return df

def simulate_with_martingale(df, name, starting_bankroll=100, bet_pct=0.05, max_recovery=2):
    """
    Simulate strategy with:
    - Correct Kalshi fees
    - Martingale recovery (up to max_recovery attempts)
    - Dynamic betting as % of bankroll
    """
    bankroll = starting_bankroll
    consecutive_losses = 0
    total_loss_to_recover = 0

    trades = 0
    wins = 0
    losses = 0
    max_drawdown = 0
    peak_bankroll = starting_bankroll

    for _, row in df.iterrows():
        entry_price = row['Entry Price']
        outcome = row['Outcome']

        # Calculate correct fee
        fee_per_contract = kalshi_fee(entry_price)

        # Determine bet size
        if consecutive_losses == 0:
            # Base bet: % of bankroll
            base_bet = bankroll * bet_pct
            contracts = max(1, int(base_bet / (entry_price / 100)))
        else:
            # Recovery bet: need to recover losses + 10% buffer
            recovery_target = total_loss_to_recover * 1.10
            net_profit_per = (1.0 - entry_price/100) - fee_per_contract
            if net_profit_per > 0:
                contracts = math.ceil(recovery_target / net_profit_per)
            else:
                contracts = 1

        cost = contracts * (entry_price / 100)
        fee = contracts * fee_per_contract

        # Check if we can afford
        if cost > bankroll:
            # Can't afford, skip or bust
            continue

        trades += 1

        if outcome == 'win':
            wins += 1
            payout = contracts * 1.0  # $1 per contract
            profit = payout - cost - fee
            bankroll += profit

            # Reset recovery
            consecutive_losses = 0
            total_loss_to_recover = 0
        else:
            losses += 1
            loss = cost + fee
            bankroll -= loss

            consecutive_losses += 1
            total_loss_to_recover += loss

            # Check if bust (max recovery exceeded)
            if consecutive_losses > max_recovery:
                # Accept the loss, reset
                consecutive_losses = 0
                total_loss_to_recover = 0

        # Track drawdown
        if bankroll > peak_bankroll:
            peak_bankroll = bankroll
        drawdown = (peak_bankroll - bankroll) / peak_bankroll * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    roi = (bankroll / starting_bankroll - 1) * 100

    print(f"\n{'='*60}")
    print(f"MARTINGALE SIM: {name}")
    print(f"Starting: ${starting_bankroll} | Bet: {bet_pct*100:.0f}% of bankroll")
    print(f"{'='*60}")
    print(f"Trades: {trades} | W/L: {wins}/{losses} ({wins/trades*100:.1f}%)")
    print(f"Final bankroll: ${bankroll:.2f}")
    print(f"Total profit: ${bankroll - starting_bankroll:.2f}")
    print(f"ROI: {roi:.1f}%")
    print(f"Max drawdown: {max_drawdown:.1f}%")

    return bankroll, roi

def simulate_flat_bet(df, name, starting_bankroll=100, contracts_per_trade=1):
    """
    Simulate with flat betting (fixed contracts per trade).
    Uses correct Kalshi fees.
    """
    bankroll = starting_bankroll
    trades = 0
    wins = 0
    losses = 0

    for _, row in df.iterrows():
        entry_price = row['Entry Price']
        outcome = row['Outcome']

        fee_per_contract = kalshi_fee(entry_price)
        cost = contracts_per_trade * (entry_price / 100)
        fee = contracts_per_trade * fee_per_contract

        if cost > bankroll:
            continue

        trades += 1

        if outcome == 'win':
            wins += 1
            payout = contracts_per_trade * 1.0
            profit = payout - cost - fee
            bankroll += profit
        else:
            losses += 1
            bankroll -= (cost + fee)

    roi = (bankroll / starting_bankroll - 1) * 100

    print(f"\n--- FLAT BET ({contracts_per_trade} contract): {name} ---")
    print(f"Trades: {trades} | W/L: {wins}/{losses}")
    print(f"Final: ${bankroll:.2f} | ROI: {roi:.1f}%")

    return bankroll, roi


def simulate_s2_dynamic_betting(df, starting_bankroll=1000):
    """
    Simulate S2 strategy the way IT actually works:
    - Dynamic bet sizing based on edge %
    - Bet size scales with bankroll
    - NO martingale (it doesn't use that)
    """
    bankroll = starting_bankroll

    trades = 0
    wins = 0
    losses = 0
    max_drawdown = 0
    peak = starting_bankroll

    for _, row in df.iterrows():
        entry_price = row['Entry Price']
        outcome = row['Outcome']
        edge_pct = row['Edge %']

        # S2 dynamic bet sizing: higher edge = bigger bet
        # Base: ~1% of bankroll, scales with edge
        if pd.isna(edge_pct) or edge_pct == 'N/A':
            edge = 10  # default
        else:
            edge = float(edge_pct)

        # More edge = more contracts
        # At 10% edge: ~1% of bankroll
        # At 25% edge: ~4% of bankroll
        bet_pct = (edge / 10) * 0.01  # scales linearly
        bet_pct = min(bet_pct, 0.05)  # cap at 5%

        bet_amount = bankroll * bet_pct
        contracts = max(1, int(bet_amount / (entry_price / 100)))

        fee = contracts * kalshi_fee(entry_price)
        cost = contracts * (entry_price / 100)

        if cost > bankroll:
            continue

        trades += 1

        if outcome == 'win':
            wins += 1
            payout = contracts * 1.0
            profit = payout - cost - fee
            bankroll += profit
        else:
            losses += 1
            bankroll -= (cost + fee)

        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    roi = (bankroll / starting_bankroll - 1) * 100
    profit = bankroll - starting_bankroll

    print(f"\n{'='*60}")
    print(f"S2 DYNAMIC BETTING (as designed)")
    print(f"Starting: ${starting_bankroll}")
    print(f"{'='*60}")
    print(f"Trades: {trades} | W/L: {wins}/{losses} ({wins/trades*100:.1f}%)")
    print(f"Final bankroll: ${bankroll:.2f}")
    print(f"PROFIT: ${profit:.2f}")
    print(f"ROI: {roi:.1f}%")
    print(f"Max drawdown: {max_drawdown:.1f}%")

    return bankroll, profit, roi


if __name__ == "__main__":
    # File paths
    s3_path = "/Users/corbandamukaitis/Downloads/s3_sentiment_odds80_wait10_trades (3).csv"
    s2_path = "/Users/corbandamukaitis/Downloads/s2_dynamic_scaled_wait5_trades.csv"

    print("\n" + "="*70)
    print("HEAD TO HEAD: WHICH STRATEGY MAKES MORE MONEY?")
    print("Starting bankroll: $1000 | Period: 11 days")
    print("="*70)

    df_s3 = pd.read_csv(s3_path)
    df_s2 = pd.read_csv(s2_path)

    # Filter S3 to 80-92c (what we actually trade)
    df_s3_filtered = df_s3[(df_s3['Entry Price'] >= 80) & (df_s3['Entry Price'] <= 92)]

    print(f"\nS3 trades in 80-92c range: {len(df_s3_filtered)}")
    print(f"S2 trades: {len(df_s2)}")

    # ============================================
    # S2: Dynamic betting (as designed)
    # ============================================
    s2_final, s2_profit, s2_roi = simulate_s2_dynamic_betting(df_s2, 1000)

    # ============================================
    # S3: Our martingale system
    # ============================================
    print(f"\n{'='*60}")
    print(f"S3 WITH MARTINGALE (our system)")
    print(f"Starting: $1000 | 3% base bet")
    print(f"{'='*60}")

    bankroll = 1000
    consecutive_losses = 0
    total_loss_to_recover = 0
    wins = 0
    losses = 0
    trades = 0
    peak = 1000
    max_dd = 0

    for _, row in df_s3_filtered.iterrows():
        entry_price = row['Entry Price']
        outcome = row['Outcome']

        fee_per = kalshi_fee(entry_price)

        if consecutive_losses == 0:
            # Base bet: 3% of bankroll
            bet = bankroll * 0.03
            contracts = max(1, int(bet / (entry_price / 100)))
        else:
            # Recovery: need to recover losses + 10%
            recovery_target = total_loss_to_recover * 1.10
            net_profit_per = (1.0 - entry_price/100) - fee_per
            if net_profit_per > 0:
                contracts = math.ceil(recovery_target / net_profit_per)
            else:
                contracts = 1

        cost = contracts * (entry_price / 100)
        fee = contracts * fee_per

        if cost > bankroll:
            continue

        trades += 1

        if outcome == 'win':
            wins += 1
            payout = contracts * 1.0
            profit = payout - cost - fee
            bankroll += profit
            consecutive_losses = 0
            total_loss_to_recover = 0
        else:
            losses += 1
            loss = cost + fee
            bankroll -= loss
            consecutive_losses += 1
            total_loss_to_recover += loss

            if consecutive_losses > 2:
                consecutive_losses = 0
                total_loss_to_recover = 0

        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak * 100
        if dd > max_dd:
            max_dd = dd

    s3_profit = bankroll - 1000
    s3_roi = (bankroll / 1000 - 1) * 100

    print(f"Trades: {trades} | W/L: {wins}/{losses} ({wins/trades*100:.1f}%)")
    print(f"Final bankroll: ${bankroll:.2f}")
    print(f"PROFIT: ${s3_profit:.2f}")
    print(f"ROI: {s3_roi:.1f}%")
    print(f"Max drawdown: {max_dd:.1f}%")

    # ============================================
    # VERDICT
    # ============================================
    print("\n" + "="*70)
    print("VERDICT: WHICH MADE MORE MONEY?")
    print("="*70)
    print(f"\n  S2 (Dynamic Wait5):      ${s2_profit:>10.2f} profit ({s2_roi:.1f}% ROI)")
    print(f"  S3 (Martingale 80-92c):  ${s3_profit:>10.2f} profit ({s3_roi:.1f}% ROI)")
    print()
    if s2_profit > s3_profit:
        print(f"  WINNER: S2 Dynamic by ${s2_profit - s3_profit:.2f}")
    else:
        print(f"  WINNER: S3 Martingale by ${s3_profit - s2_profit:.2f}")
    print("="*70)
