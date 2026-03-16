#!/usr/bin/env python3
"""
Backtest the 15-minute strategy with realistic slippage and fees.
Tests multiple bankroll sizes: $40, $400, $4000
"""

import pandas as pd
import numpy as np
import math
from dataclasses import dataclass
from typing import Optional, List, Tuple
from datetime import datetime
import json

# Strategy parameters
MIN_ENTRY_PRICE = 80  # cents
MAX_ENTRY_PRICE = 92  # cents
RECOVERY_PRICE_CAP = 85  # cents - more conservative for recovery
ENTRY_WINDOW_MINS = 5.0  # Enter when <= 5 mins remaining
SLIPPAGE = 1  # cents - assume we get filled 1c worse
MAX_RECOVERY_ATTEMPTS = 2  # Max 2 recovery bets (3 total including base)


@dataclass
class Trade:
    """Record of a single trade."""
    timestamp: str
    ticker: str
    side: str  # "yes" or "no"
    entry_price: int  # cents (after slippage)
    contracts: int
    cost: float
    fee: float
    outcome: str  # "win" or "loss"
    payout: float
    net_pnl: float
    bet_number: int  # 1 = base, 2 = recovery 1, 3 = recovery 2
    strike_price: float
    final_btc_price: float


def calc_fee(price_cents: int, contracts: int) -> float:
    """Calculate Kalshi fee. Formula: ceil(0.07 * p * (1-p)) per contract, min 1c."""
    p = price_cents / 100
    fee_per = max(0.01, math.ceil(0.07 * p * (1 - p) * 100) / 100)
    return fee_per * contracts


def calc_contracts_for_recovery(loss_to_recover: float, entry_price_cents: int) -> int:
    """
    Calculate contracts needed to recover a loss.
    Accounts for fees on the recovery bet.
    """
    profit_per_contract = (100 - entry_price_cents) / 100

    # Need to recover: loss + fees on this bet
    # Profit = contracts * profit_per - fee
    # fee ≈ contracts * 0.02 (rough estimate at 85c)
    # So: contracts * (profit_per - 0.02) >= loss
    # contracts >= loss / (profit_per - 0.02)

    # More accurate: iterate to find minimum contracts
    for contracts in range(1, 10000):
        fee = calc_fee(entry_price_cents, contracts)
        gross_profit = contracts * profit_per_contract
        net_profit = gross_profit - fee
        if net_profit >= loss_to_recover:
            return contracts

    return 10000  # Cap at 10k


def find_entry_opportunities(df: pd.DataFrame) -> List[dict]:
    """
    Find all entry opportunities from the data.
    Returns list of potential entries with their outcomes.
    """
    opportunities = []

    # Group by ticker (each ticker is one 15-minute market)
    for ticker, group in df.groupby('ticker'):
        group = group.sort_values('timestamp')

        # Get strike price and final BTC price (last row before close)
        strike_price = group['strike_price'].iloc[0]
        final_btc_price = group['crypto_price'].iloc[-1]

        # Determine outcome: YES wins if final_btc >= strike
        yes_wins = final_btc_price >= strike_price

        # Find entries in our window (5 mins or less remaining)
        entry_window = group[group['mins_left'] <= ENTRY_WINDOW_MINS]

        if len(entry_window) == 0:
            continue

        # Get the first row in our entry window
        entry_row = entry_window.iloc[0]

        # Check YES side
        yes_ask = entry_row['yes_ask']
        if MIN_ENTRY_PRICE <= yes_ask <= MAX_ENTRY_PRICE:
            opportunities.append({
                'ticker': ticker,
                'timestamp': entry_row['timestamp'],
                'side': 'yes',
                'entry_price': yes_ask,
                'strike_price': strike_price,
                'final_btc_price': final_btc_price,
                'outcome': 'win' if yes_wins else 'loss',
                'mins_left': entry_row['mins_left'],
            })

        # Check NO side
        no_ask = entry_row['no_ask']
        if MIN_ENTRY_PRICE <= no_ask <= MAX_ENTRY_PRICE:
            opportunities.append({
                'ticker': ticker,
                'timestamp': entry_row['timestamp'],
                'side': 'no',
                'entry_price': no_ask,
                'strike_price': strike_price,
                'final_btc_price': final_btc_price,
                'outcome': 'win' if not yes_wins else 'loss',
                'mins_left': entry_row['mins_left'],
            })

    # Sort by timestamp
    opportunities.sort(key=lambda x: x['timestamp'])
    return opportunities


def run_backtest(opportunities: List[dict], starting_bankroll: float) -> dict:
    """
    Run backtest with martingale recovery.

    Returns dict with results.
    """
    bankroll = starting_bankroll
    trades: List[Trade] = []

    consecutive_losses = 0
    total_loss_to_recover = 0.0
    in_recovery = False

    # Track stats
    wins = 0
    losses = 0
    max_drawdown = 0
    peak_bankroll = starting_bankroll
    busted = False

    i = 0
    while i < len(opportunities):
        opp = opportunities[i]

        # Determine entry price cap based on recovery mode
        price_cap = RECOVERY_PRICE_CAP if in_recovery else MAX_ENTRY_PRICE

        # Skip if price exceeds cap
        if opp['entry_price'] > price_cap:
            i += 1
            continue

        # Calculate entry price with slippage
        entry_price = min(opp['entry_price'] + SLIPPAGE, 99)

        # Calculate contracts needed
        if in_recovery:
            # Need to recover previous losses
            contracts = calc_contracts_for_recovery(total_loss_to_recover, entry_price)
        else:
            # Base bet: use ~2% of bankroll or minimum viable
            base_bet_cost = bankroll * 0.02
            contracts = max(1, int(base_bet_cost / (entry_price / 100)))

        # Calculate cost and fee
        cost = contracts * entry_price / 100
        fee = calc_fee(entry_price, contracts)
        total_cost = cost + fee

        # Check if we can afford
        if total_cost > bankroll:
            if in_recovery:
                # Can't afford recovery - we're bust
                busted = True
                break
            else:
                # Can't afford base bet - reduce size
                contracts = max(1, int((bankroll - 1) / (entry_price / 100 + 0.02)))
                cost = contracts * entry_price / 100
                fee = calc_fee(entry_price, contracts)
                total_cost = cost + fee

                if total_cost > bankroll:
                    i += 1
                    continue

        # Execute trade
        bet_number = consecutive_losses + 1

        if opp['outcome'] == 'win':
            # Win! Payout = $1 per contract
            payout = contracts * 1.0
            net_pnl = payout - cost - fee
            bankroll += net_pnl
            wins += 1

            # Reset recovery
            consecutive_losses = 0
            total_loss_to_recover = 0.0
            in_recovery = False
        else:
            # Loss
            payout = 0
            net_pnl = -cost - fee
            bankroll += net_pnl
            losses += 1
            consecutive_losses += 1
            total_loss_to_recover += cost + fee
            in_recovery = True

            if consecutive_losses >= MAX_RECOVERY_ATTEMPTS + 1:
                # Max losses hit - reset and accept loss
                consecutive_losses = 0
                total_loss_to_recover = 0.0
                in_recovery = False

        # Track drawdown
        if bankroll > peak_bankroll:
            peak_bankroll = bankroll
        drawdown = (peak_bankroll - bankroll) / peak_bankroll * 100
        max_drawdown = max(max_drawdown, drawdown)

        # Record trade
        trade = Trade(
            timestamp=str(opp['timestamp']),
            ticker=opp['ticker'],
            side=opp['side'],
            entry_price=entry_price,
            contracts=contracts,
            cost=cost,
            fee=fee,
            outcome=opp['outcome'],
            payout=payout,
            net_pnl=net_pnl,
            bet_number=bet_number,
            strike_price=opp['strike_price'],
            final_btc_price=opp['final_btc_price'],
        )
        trades.append(trade)

        # Check if busted
        if bankroll <= 0:
            busted = True
            break

        i += 1

    # Calculate final stats
    total_pnl = bankroll - starting_bankroll
    roi = total_pnl / starting_bankroll * 100
    win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

    return {
        'starting_bankroll': starting_bankroll,
        'final_bankroll': bankroll,
        'total_pnl': total_pnl,
        'roi': roi,
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'total_trades': wins + losses,
        'max_drawdown': max_drawdown,
        'busted': busted,
        'trades': trades,
    }


def print_results(results: dict, bankroll_label: str):
    """Print formatted results."""
    print(f"\n{'='*60}")
    print(f"BACKTEST RESULTS - ${results['starting_bankroll']:.0f} BANKROLL ({bankroll_label})")
    print(f"{'='*60}")
    print(f"Starting: ${results['starting_bankroll']:.2f}")
    print(f"Final:    ${results['final_bankroll']:.2f}")
    print(f"P&L:      ${results['total_pnl']:+.2f} ({results['roi']:+.1f}%)")
    print(f"Trades:   {results['total_trades']} ({results['wins']}W / {results['losses']}L)")
    print(f"Win Rate: {results['win_rate']:.1f}%")
    print(f"Max DD:   {results['max_drawdown']:.1f}%")
    print(f"Busted:   {'YES' if results['busted'] else 'NO'}")

    # Show recovery sequences
    trades = results['trades']
    recovery_sequences = []
    current_seq = []
    for t in trades:
        if t.bet_number == 1:
            if current_seq:
                recovery_sequences.append(current_seq)
            current_seq = [t]
        else:
            current_seq.append(t)
    if current_seq:
        recovery_sequences.append(current_seq)

    # Count sequence outcomes
    seq_outcomes = {'1-bet win': 0, '2-bet recovery': 0, '3-bet recovery': 0, 'full loss': 0}
    for seq in recovery_sequences:
        if len(seq) == 1 and seq[0].outcome == 'win':
            seq_outcomes['1-bet win'] += 1
        elif len(seq) == 2 and seq[-1].outcome == 'win':
            seq_outcomes['2-bet recovery'] += 1
        elif len(seq) == 3 and seq[-1].outcome == 'win':
            seq_outcomes['3-bet recovery'] += 1
        else:
            seq_outcomes['full loss'] += 1

    print(f"\nSequence Outcomes:")
    print(f"  1-bet wins:      {seq_outcomes['1-bet win']}")
    print(f"  2-bet recoveries: {seq_outcomes['2-bet recovery']}")
    print(f"  3-bet recoveries: {seq_outcomes['3-bet recovery']}")
    print(f"  Full losses:     {seq_outcomes['full loss']}")


def main():
    # Load data
    print("Loading data...")
    df = pd.read_csv('/Users/corbandamukaitis/Downloads/btc_price_log (8).csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    print(f"Data range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Total rows: {len(df):,}")
    print(f"Unique markets: {df['ticker'].nunique()}")

    # Find opportunities
    print("\nFinding entry opportunities...")
    opportunities = find_entry_opportunities(df)
    print(f"Found {len(opportunities)} potential entries in 80-92c range")

    # Count by outcome
    wins = sum(1 for o in opportunities if o['outcome'] == 'win')
    losses = sum(1 for o in opportunities if o['outcome'] == 'loss')
    print(f"Raw opportunities: {wins}W / {losses}L ({wins/(wins+losses)*100:.1f}% win rate)")

    # Run backtests for each bankroll size
    bankrolls = [
        (40, "Small"),
        (400, "Medium"),
        (4000, "Large"),
    ]

    all_results = {}

    for amount, label in bankrolls:
        results = run_backtest(opportunities.copy(), amount)
        print_results(results, label)
        all_results[label] = results

    # Save detailed results
    output_path = '/Users/corbandamukaitis/Desktop/Personal Projects/15 minute trade strategy/kalshi-official-trader-corbeezy-the-king/backtests/2026-03-18/backtest_results.json'

    # Convert trades to dicts for JSON
    json_results = {}
    for label, res in all_results.items():
        json_results[label] = {
            k: v for k, v in res.items() if k != 'trades'
        }
        json_results[label]['trades'] = [
            {
                'timestamp': t.timestamp,
                'ticker': t.ticker,
                'side': t.side,
                'entry_price': t.entry_price,
                'contracts': t.contracts,
                'cost': t.cost,
                'fee': t.fee,
                'outcome': t.outcome,
                'payout': t.payout,
                'net_pnl': t.net_pnl,
                'bet_number': t.bet_number,
            }
            for t in res['trades']
        ]

    with open(output_path, 'w') as f:
        json.dump(json_results, f, indent=2, default=str)

    print(f"\n\nDetailed results saved to: {output_path}")

    # Summary comparison
    print("\n" + "="*60)
    print("COMPARISON SUMMARY")
    print("="*60)
    print(f"{'Bankroll':<12} {'Final':<12} {'P&L':<12} {'ROI':<10} {'Trades':<10}")
    print("-"*60)
    for label, res in all_results.items():
        print(f"${res['starting_bankroll']:<11.0f} ${res['final_bankroll']:<11.2f} ${res['total_pnl']:<+11.2f} {res['roi']:>+7.1f}%   {res['total_trades']}")


if __name__ == "__main__":
    main()
