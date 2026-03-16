#!/usr/bin/env python3
"""
ATTACK/RECOVER HYBRID BACKTEST

- ATTACK MODE: S2 system (wait 5 min, edge-based, dynamic scaling)
- RECOVER MODE: S3 system (wait 10 min, 80-92c, 90% win rate, martingale)
- Switch to RECOVER after a loss, back to ATTACK after recovery win
"""

import pandas as pd
import math
from collections import defaultdict

# Persistence data from 5-year BTC analysis
PERSISTENCE = {
    1: 0.5869, 2: 0.6258, 3: 0.6565, 4: 0.6812, 5: 0.7128,
    6: 0.7407, 7: 0.7674, 8: 0.7920, 9: 0.8143, 10: 0.8408,
    11: 0.8695, 12: 0.8992, 13: 0.9318
}

def kalshi_fee(price_cents):
    price = price_cents / 100
    return max(0.01, 0.07 * price * (1 - price))

def get_edge(minute, price_cents):
    """Calculate edge: historical persistence - implied odds"""
    if minute < 1 or minute > 13:
        return 0
    true_prob = PERSISTENCE.get(minute, 0.5)
    implied_prob = price_cents / 100
    return true_prob - implied_prob

def simulate_attack_recover(
    df_s2,  # S2 trades (for ATTACK mode data)
    df_s3,  # S3 trades (for RECOVER mode data)
    starting_bankroll=1000,
    attack_base_pct=0.02,
    attack_max_pct=0.08,
    recover_base_pct=0.03,
    max_recovery_bets=2,
):
    """
    Hybrid strategy:
    - Start in ATTACK mode (S2)
    - On loss -> switch to RECOVER mode (S3 with martingale)
    - On recovery win -> back to ATTACK mode
    """

    bankroll = starting_bankroll
    mode = "ATTACK"

    # Recovery state
    consecutive_losses = 0
    total_loss_to_recover = 0

    # Stats
    trades = 0
    wins = 0
    losses = 0
    attack_trades = 0
    recover_trades = 0
    max_drawdown = 0
    peak = starting_bankroll

    # Merge and sort by timestamp
    df_s2 = df_s2.copy()
    df_s3 = df_s3.copy()
    df_s2['source'] = 's2'
    df_s3['source'] = 's3'

    # We need to interleave based on time
    df_s2['Timestamp'] = pd.to_datetime(df_s2['Timestamp'])
    df_s3['Timestamp'] = pd.to_datetime(df_s3['Timestamp'])

    # Create unified timeline
    all_trades = pd.concat([df_s2, df_s3]).sort_values('Timestamp').reset_index(drop=True)

    traded_windows = set()

    for _, row in all_trades.iterrows():
        window = row['Window']
        source = row['source']
        entry_price = row['Entry Price']
        outcome = row['Outcome']

        # Skip if already traded this window
        if window in traded_windows:
            continue

        # Determine if we should take this trade based on mode
        should_trade = False
        bet_contracts = 0

        if mode == "ATTACK" and source == 's2':
            # S2 criteria: edge >= 10%
            edge_pct = row.get('Edge %', 'N/A')
            if edge_pct != 'N/A' and not pd.isna(edge_pct):
                edge = float(edge_pct) / 100
                if edge >= 0.10:
                    # Dynamic scaling based on edge
                    scale = min(1, max(0, (edge - 0.10) / 0.20))
                    bet_pct = attack_base_pct + scale * (attack_max_pct - attack_base_pct)
                    bet_amount = bankroll * bet_pct
                    bet_contracts = max(1, int(bet_amount / (entry_price / 100)))
                    should_trade = True

        elif mode == "RECOVER" and source == 's3':
            # S3 criteria: 80-92c prices
            if 80 <= entry_price <= 92:
                # Martingale recovery sizing
                fee_per = kalshi_fee(entry_price)
                net_profit_per = (1.0 - entry_price/100) - fee_per
                recovery_target = total_loss_to_recover * 1.10

                if net_profit_per > 0:
                    bet_contracts = math.ceil(recovery_target / net_profit_per)
                else:
                    bet_contracts = 1
                should_trade = True

        if not should_trade:
            continue

        # Check affordability
        cost = bet_contracts * (entry_price / 100)
        fee = bet_contracts * kalshi_fee(entry_price)

        if cost > bankroll:
            # Can't afford - reset recovery if busted
            if mode == "RECOVER":
                mode = "ATTACK"
                consecutive_losses = 0
                total_loss_to_recover = 0
            continue

        # Execute trade
        trades += 1
        traded_windows.add(window)

        if mode == "ATTACK":
            attack_trades += 1
        else:
            recover_trades += 1

        if outcome == 'win':
            wins += 1
            payout = bet_contracts * 1.0
            profit = payout - cost - fee
            bankroll += profit

            if mode == "RECOVER":
                # Recovery successful - back to attack!
                print(f"  RECOVERY WIN! Recovered ${total_loss_to_recover:.2f} -> ATTACK MODE")
                mode = "ATTACK"
                consecutive_losses = 0
                total_loss_to_recover = 0
        else:
            losses += 1
            loss = cost + fee
            bankroll -= loss

            if mode == "ATTACK":
                # Loss in attack mode - switch to recover!
                print(f"  ATTACK LOSS (${loss:.2f}) -> RECOVER MODE")
                mode = "RECOVER"
                consecutive_losses = 1
                total_loss_to_recover = loss
            else:
                # Loss in recover mode
                consecutive_losses += 1
                total_loss_to_recover += loss

                if consecutive_losses > max_recovery_bets:
                    # Max recovery exceeded - accept loss, back to attack
                    print(f"  RECOVERY FAILED after {consecutive_losses} attempts -> ATTACK MODE")
                    mode = "ATTACK"
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
    print(f"ATTACK/RECOVER HYBRID RESULTS")
    print(f"{'='*60}")
    print(f"Starting: ${starting_bankroll:,}")
    print(f"Final: ${bankroll:,.2f}")
    print(f"PROFIT: ${profit:,.2f}")
    print(f"ROI: {roi:.1f}%")
    print(f"{'='*60}")
    print(f"Total trades: {trades} | W/L: {wins}/{losses} ({wins/trades*100:.1f}%)")
    print(f"Attack trades: {attack_trades}")
    print(f"Recover trades: {recover_trades}")
    print(f"Max drawdown: {max_drawdown:.1f}%")

    return bankroll, profit, roi, max_drawdown


if __name__ == "__main__":
    # Load both datasets
    s2_path = "/Users/corbandamukaitis/Downloads/s2_dynamic_scaled_wait5_trades.csv"
    s3_path = "/Users/corbandamukaitis/Downloads/s3_sentiment_odds80_wait10_trades (3).csv"

    df_s2 = pd.read_csv(s2_path)
    df_s3 = pd.read_csv(s3_path)

    # Filter S3 to 80-92c only
    df_s3 = df_s3[(df_s3['Entry Price'] >= 80) & (df_s3['Entry Price'] <= 92)]

    print("="*70)
    print("ATTACK/RECOVER HYBRID BACKTEST")
    print("ATTACK = S2 (wait 5 min, edge-based, dynamic scaling)")
    print("RECOVER = S3 (wait 10 min, 80-92c, martingale)")
    print("="*70)

    print(f"\nS2 trades available: {len(df_s2)}")
    print(f"S3 trades available (80-92c): {len(df_s3)}")

    # Run hybrid
    simulate_attack_recover(
        df_s2, df_s3,
        starting_bankroll=1000,
        attack_base_pct=0.02,
        attack_max_pct=0.08,
        recover_base_pct=0.03,
        max_recovery_bets=2,
    )

    # Compare to baselines
    print("\n" + "="*70)
    print("BASELINE COMPARISONS")
    print("="*70)

    # Pure S2
    bankroll = 1000
    for _, row in df_s2.iterrows():
        entry_price = row['Entry Price']
        edge_pct = row.get('Edge %', 'N/A')
        if edge_pct == 'N/A' or pd.isna(edge_pct):
            edge = 0.10
        else:
            edge = float(edge_pct) / 100

        scale = min(1, max(0, (edge - 0.10) / 0.20))
        bet_pct = 0.02 + scale * 0.06
        contracts = max(1, int((bankroll * bet_pct) / (entry_price / 100)))
        cost = contracts * (entry_price / 100)
        fee = contracts * kalshi_fee(entry_price)

        if cost > bankroll:
            continue

        if row['Outcome'] == 'win':
            bankroll += contracts - cost - fee
        else:
            bankroll -= cost + fee

    print(f"\nPure S2 (2%-8%): ${bankroll:,.2f} ({(bankroll/1000-1)*100:.1f}% ROI)")

    # Pure S3 with martingale
    bankroll = 1000
    consec = 0
    loss_to_recover = 0
    for _, row in df_s3.iterrows():
        entry_price = row['Entry Price']

        if consec == 0:
            contracts = max(1, int((bankroll * 0.03) / (entry_price / 100)))
        else:
            fee_per = kalshi_fee(entry_price)
            net_profit = (1.0 - entry_price/100) - fee_per
            if net_profit > 0:
                contracts = math.ceil(loss_to_recover * 1.10 / net_profit)
            else:
                contracts = 1

        cost = contracts * (entry_price / 100)
        fee = contracts * kalshi_fee(entry_price)

        if cost > bankroll:
            consec = 0
            loss_to_recover = 0
            continue

        if row['Outcome'] == 'win':
            bankroll += contracts - cost - fee
            consec = 0
            loss_to_recover = 0
        else:
            bankroll -= cost + fee
            consec += 1
            loss_to_recover += cost + fee
            if consec > 2:
                consec = 0
                loss_to_recover = 0

    print(f"Pure S3 (martingale): ${bankroll:,.2f} ({(bankroll/1000-1)*100:.1f}% ROI)")
