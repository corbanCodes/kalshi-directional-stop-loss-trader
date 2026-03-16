#!/usr/bin/env python3
"""
Comprehensive backtest with actual settlement results and HTML output.
"""

import pandas as pd
import numpy as np
import math
from dataclasses import dataclass, asdict
from typing import List
import json

# Strategy parameters
MIN_ENTRY_PRICE = 80
MAX_ENTRY_PRICE = 92
RECOVERY_PRICE_CAP = 85
ENTRY_WINDOW_MINS = 5.0
SLIPPAGE = 1
MAX_RECOVERY_ATTEMPTS = 2


def calc_fee(price_cents: int, contracts: int) -> float:
    """Calculate Kalshi fee."""
    p = price_cents / 100
    fee_per = max(0.01, math.ceil(0.07 * p * (1 - p) * 100) / 100)
    return fee_per * contracts


def calc_contracts_for_recovery(loss_to_recover: float, entry_price_cents: int) -> int:
    """Calculate contracts needed to recover a loss (accounts for fees)."""
    for contracts in range(1, 10000):
        cost = contracts * entry_price_cents / 100
        fee = calc_fee(entry_price_cents, contracts)
        gross_profit = contracts * (100 - entry_price_cents) / 100
        net_profit = gross_profit - fee
        if net_profit >= loss_to_recover:
            return contracts
    return 10000


def run_backtest(opportunities: List[dict], starting_bankroll: float,
                 use_martingale: bool = True, min_price: int = 80) -> dict:
    """Run backtest with optional martingale."""
    bankroll = starting_bankroll
    trades = []

    consecutive_losses = 0
    total_loss_to_recover = 0.0
    in_recovery = False

    peak_bankroll = starting_bankroll

    for opp in opportunities:
        # Filter by minimum price
        if opp['entry_price'] < min_price:
            continue

        # Recovery price cap
        if in_recovery and opp['entry_price'] > RECOVERY_PRICE_CAP:
            continue

        entry_price = min(opp['entry_price'] + SLIPPAGE, 99)

        # Calculate contracts
        if use_martingale and in_recovery:
            contracts = calc_contracts_for_recovery(total_loss_to_recover, entry_price)
        else:
            base_bet_pct = 0.02
            contracts = max(1, int(bankroll * base_bet_pct / (entry_price / 100)))

        cost = contracts * entry_price / 100
        fee = calc_fee(entry_price, contracts)
        total_cost = cost + fee

        # Check affordability
        if total_cost > bankroll:
            if in_recovery:
                # Record as bust
                trades.append({
                    'ticker': opp['ticker'],
                    'timestamp': opp['timestamp'],
                    'side': opp['side'],
                    'entry_price': entry_price,
                    'contracts': contracts,
                    'cost': cost,
                    'fee': fee,
                    'outcome': 'BUST',
                    'payout': 0,
                    'net_pnl': 0,
                    'bet_number': consecutive_losses + 1,
                    'bankroll_after': bankroll,
                    'note': f'Cannot afford ${total_cost:.2f}'
                })
                break
            else:
                continue

        bet_number = consecutive_losses + 1

        if opp['result'] == opp['side']:
            # WIN
            payout = contracts * 1.0
            net_pnl = payout - cost - fee
            bankroll += net_pnl
            outcome = 'WIN'
            consecutive_losses = 0
            total_loss_to_recover = 0.0
            in_recovery = False
        else:
            # LOSS
            payout = 0
            net_pnl = -cost - fee
            bankroll += net_pnl
            outcome = 'LOSS'
            consecutive_losses += 1
            total_loss_to_recover += cost + fee
            in_recovery = True

            if consecutive_losses >= MAX_RECOVERY_ATTEMPTS + 1:
                consecutive_losses = 0
                total_loss_to_recover = 0.0
                in_recovery = False

        if bankroll > peak_bankroll:
            peak_bankroll = bankroll

        trades.append({
            'ticker': opp['ticker'],
            'timestamp': str(opp['timestamp']),
            'side': opp['side'],
            'entry_price': entry_price,
            'contracts': contracts,
            'cost': cost,
            'fee': fee,
            'outcome': outcome,
            'payout': payout,
            'net_pnl': net_pnl,
            'bet_number': bet_number,
            'bankroll_after': bankroll,
            'note': ''
        })

        if bankroll <= 0:
            break

    wins = sum(1 for t in trades if t['outcome'] == 'WIN')
    losses = sum(1 for t in trades if t['outcome'] == 'LOSS')

    return {
        'starting_bankroll': starting_bankroll,
        'final_bankroll': bankroll,
        'total_pnl': bankroll - starting_bankroll,
        'roi': (bankroll - starting_bankroll) / starting_bankroll * 100,
        'wins': wins,
        'losses': losses,
        'win_rate': wins / (wins + losses) * 100 if (wins + losses) > 0 else 0,
        'total_trades': len(trades),
        'max_drawdown': (peak_bankroll - min(t['bankroll_after'] for t in trades)) / peak_bankroll * 100 if trades else 0,
        'ended_in_recovery': in_recovery,
        'final_consecutive_losses': consecutive_losses,
        'trades': trades,
    }


def generate_html_report(results_by_bankroll: dict, opportunities: List[dict], output_path: str):
    """Generate comprehensive HTML report."""

    # Calculate raw opportunity stats
    total_opps = len(opportunities)
    wins = sum(1 for o in opportunities if o['result'] == o['side'])
    losses = total_opps - wins

    # Find losing streaks in raw data
    outcomes = ['W' if o['result'] == o['side'] else 'L' for o in opportunities]
    streak = 0
    max_streak = 0
    streaks = []
    for o in outcomes:
        if o == 'L':
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            if streak > 0:
                streaks.append(streak)
            streak = 0
    if streak > 0:
        streaks.append(streak)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Backtest Results - March 18, 2026</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', monospace; background: #0a0a0a; color: #e0e0e0; padding: 20px; }}
        h1 {{ color: #6bcb77; margin-bottom: 10px; }}
        h2 {{ color: #4ecdc4; margin: 20px 0 10px 0; border-bottom: 1px solid #333; padding-bottom: 5px; }}
        h3 {{ color: #ffd93d; margin: 15px 0 10px 0; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
        .card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 15px; text-align: center; }}
        .card .value {{ font-size: 1.8rem; font-weight: bold; color: #6bcb77; }}
        .card .label {{ color: #888; font-size: 0.9rem; margin-top: 5px; }}
        .card.warn .value {{ color: #ffd93d; }}
        .card.danger .value {{ color: #ff6b6b; }}
        .card.good .value {{ color: #6bcb77; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.85rem; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
        th {{ background: #1a1a1a; color: #6bcb77; }}
        tr:hover {{ background: #1a1a1a; }}
        .win {{ color: #6bcb77; }}
        .loss {{ color: #ff6b6b; }}
        .recovery {{ background: rgba(255, 217, 61, 0.1); }}
        .bust {{ background: rgba(255, 107, 107, 0.2); }}
        .comparison {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }}
        .comparison-card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 20px; }}
        .comparison-card h3 {{ margin-bottom: 15px; }}
        .stat-row {{ display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #222; }}
        .chart {{ background: #1a1a1a; border-radius: 8px; padding: 20px; margin: 20px 0; }}
        .bar {{ height: 20px; background: #6bcb77; margin: 2px 0; border-radius: 3px; transition: width 0.3s; }}
        .bar.negative {{ background: #ff6b6b; }}
        .trade-list {{ max-height: 400px; overflow-y: auto; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; }}
        .badge.win {{ background: rgba(107, 203, 119, 0.2); color: #6bcb77; }}
        .badge.loss {{ background: rgba(255, 107, 107, 0.2); color: #ff6b6b; }}
        .badge.recovery {{ background: rgba(255, 217, 61, 0.2); color: #ffd93d; }}
        .insight {{ background: rgba(78, 205, 196, 0.1); border-left: 3px solid #4ecdc4; padding: 15px; margin: 15px 0; }}
        .warning {{ background: rgba(255, 107, 107, 0.1); border-left: 3px solid #ff6b6b; padding: 15px; margin: 15px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>15-Minute Strategy Backtest</h1>
        <p style="color:#888;">Data: March 5-9, 2026 | Generated: March 18, 2026</p>

        <h2>Raw Opportunity Analysis</h2>
        <div class="summary-grid">
            <div class="card">
                <div class="value">{total_opps}</div>
                <div class="label">Total Opportunities (80-92c)</div>
            </div>
            <div class="card {'good' if wins/total_opps > 0.85 else 'warn'}">
                <div class="value">{wins}/{losses}</div>
                <div class="label">Wins/Losses ({wins/total_opps*100:.1f}%)</div>
            </div>
            <div class="card {'good' if max_streak <= 2 else 'danger'}">
                <div class="value">{max_streak}</div>
                <div class="label">Max Losing Streak</div>
            </div>
            <div class="card">
                <div class="value">{len([s for s in streaks if s >= 2])}</div>
                <div class="label">Times 2+ Losses in Row</div>
            </div>
        </div>

        <div class="insight">
            <strong>Key Finding:</strong> The raw data shows {wins/total_opps*100:.1f}% win rate with max {max_streak} consecutive losses.
            {f'There were {len([s for s in streaks if s >= 3])} instances of 3+ consecutive losses - this is what kills martingale!' if max_streak >= 3 else 'No 3+ consecutive loss sequences observed.'}
        </div>

        <h2>Bankroll Comparison</h2>
        <div class="comparison">
"""

    for label, res in results_by_bankroll.items():
        pnl_class = 'good' if res['total_pnl'] > 0 else 'danger'
        ended_note = '(Ended in Recovery!)' if res['ended_in_recovery'] else ''

        html += f"""
            <div class="comparison-card">
                <h3>${res['starting_bankroll']:.0f} Bankroll</h3>
                <div class="stat-row">
                    <span>Final</span>
                    <span class="{pnl_class}">${res['final_bankroll']:.2f}</span>
                </div>
                <div class="stat-row">
                    <span>P&L</span>
                    <span class="{pnl_class}">${res['total_pnl']:+.2f} ({res['roi']:+.1f}%)</span>
                </div>
                <div class="stat-row">
                    <span>Trades</span>
                    <span>{res['total_trades']} ({res['wins']}W / {res['losses']}L)</span>
                </div>
                <div class="stat-row">
                    <span>Win Rate</span>
                    <span>{res['win_rate']:.1f}%</span>
                </div>
                <div class="stat-row">
                    <span>Max Drawdown</span>
                    <span class="{'danger' if res['max_drawdown'] > 50 else 'warn'}">{res['max_drawdown']:.1f}%</span>
                </div>
                <div class="stat-row">
                    <span>Status</span>
                    <span class="{'loss' if res['ended_in_recovery'] else 'win'}">{ended_note or 'Clean'}</span>
                </div>
            </div>
"""

    html += """
        </div>
"""

    # Add trade-by-trade for $400 bankroll
    if 'Medium ($400)' in results_by_bankroll:
        trades = results_by_bankroll['Medium ($400)']['trades']

        html += """
        <h2>Trade-by-Trade Analysis ($400 Bankroll)</h2>
        <div class="chart">
            <h3>Bankroll Over Time</h3>
            <div style="display: flex; align-items: end; height: 150px; gap: 2px; padding: 10px 0;">
"""

        # Mini chart
        max_bankroll = max(t['bankroll_after'] for t in trades) if trades else 400
        for i, t in enumerate(trades):
            height = (t['bankroll_after'] / max_bankroll) * 100
            color = '#6bcb77' if t['outcome'] == 'WIN' else '#ff6b6b'
            html += f'<div style="flex:1; background:{color}; height:{height}%; min-width:3px;" title="Trade {i+1}: ${t["bankroll_after"]:.2f}"></div>'

        html += """
            </div>
        </div>

        <div class="trade-list">
        <table>
            <tr>
                <th>#</th>
                <th>Ticker</th>
                <th>Side</th>
                <th>Price</th>
                <th>Contracts</th>
                <th>Cost</th>
                <th>Outcome</th>
                <th>P&L</th>
                <th>Bankroll</th>
                <th>Bet #</th>
            </tr>
"""

        for i, t in enumerate(trades):
            row_class = ''
            if t['bet_number'] > 1:
                row_class = 'recovery'
            if t['outcome'] == 'BUST':
                row_class = 'bust'

            outcome_class = 'win' if t['outcome'] == 'WIN' else 'loss'
            badge_class = 'recovery' if t['bet_number'] > 1 else outcome_class

            html += f"""
            <tr class="{row_class}">
                <td>{i+1}</td>
                <td>{t['ticker'][-15:]}</td>
                <td>{t['side'].upper()}</td>
                <td>{t['entry_price']}c</td>
                <td>{t['contracts']}</td>
                <td>${t['cost']:.2f}</td>
                <td><span class="badge {outcome_class}">{t['outcome']}</span></td>
                <td class="{outcome_class}">${t['net_pnl']:+.2f}</td>
                <td>${t['bankroll_after']:.2f}</td>
                <td><span class="badge {badge_class}">#{t['bet_number']}</span></td>
            </tr>
"""

        html += """
        </table>
        </div>
"""

    # Find the catastrophic sequences
    html += """
        <h2>Catastrophic Loss Sequences</h2>
"""

    if 'Medium ($400)' in results_by_bankroll:
        trades = results_by_bankroll['Medium ($400)']['trades']

        # Find 3-loss sequences
        sequences = []
        current_seq = []
        for t in trades:
            if t['bet_number'] == 1:
                if len(current_seq) >= 3:
                    total_loss = sum(x['net_pnl'] for x in current_seq)
                    if total_loss < -50:  # Significant loss
                        sequences.append((current_seq.copy(), total_loss))
                current_seq = [t]
            else:
                current_seq.append(t)

        if sequences:
            html += '<div class="warning"><strong>Found Catastrophic Sequences:</strong></div>'
            for seq, total_loss in sequences:
                html += f"""
        <div class="card" style="margin: 10px 0; text-align: left;">
            <strong style="color:#ff6b6b;">Sequence Loss: ${total_loss:.2f}</strong><br>
"""
                for t in seq:
                    html += f'Bet #{t["bet_number"]}: {t["contracts"]} @ {t["entry_price"]}c → {t["outcome"]} → ${t["net_pnl"]:.2f}<br>'
                html += '</div>'
        else:
            html += '<div class="insight">No catastrophic 3+ loss sequences found in this run.</div>'

    html += """
        <h2>Key Insights</h2>
        <div class="insight">
            <strong>Why Results Vary by Bankroll:</strong><br>
            • Smaller bankrolls can't afford proper recovery bets<br>
            • Larger bankrolls bet proportionally more, so losses are bigger in absolute terms<br>
            • The martingale multiplier compounds losses exponentially
        </div>

        <div class="insight">
            <strong>The Math of Martingale:</strong><br>
            • After 1 loss at 85c: Need ~6x contracts to recover<br>
            • After 2 losses at 85c: Need ~40x contracts to recover<br>
            • A single 3-loss sequence can wipe out 50+ wins
        </div>
    </div>
</body>
</html>
"""

    with open(output_path, 'w') as f:
        f.write(html)

    print(f"HTML report saved to: {output_path}")


def main():
    print("Loading data...")

    # Load price data
    price_df = pd.read_csv('/Users/corbandamukaitis/Downloads/btc_price_log (8).csv')
    price_df['timestamp'] = pd.to_datetime(price_df['timestamp'])
    price_df = price_df[price_df['strike_price'] > 0]  # Filter invalid

    # Load settlement results
    results_df = pd.read_csv('/Users/corbandamukaitis/Downloads/btc_window_results (2).csv')
    results_df['timestamp'] = pd.to_datetime(results_df['timestamp'])

    print(f"Price data: {len(price_df):,} rows, {price_df['ticker'].nunique()} markets")
    print(f"Settlement results: {len(results_df)} markets")

    # Get unique settlement results per ticker
    settlements = results_df.groupby('ticker').first().reset_index()[['ticker', 'result']]
    settlement_map = dict(zip(settlements['ticker'], settlements['result']))

    # Find opportunities
    opportunities = []
    for ticker, group in price_df.groupby('ticker'):
        if ticker not in settlement_map:
            continue

        group = group.sort_values('timestamp')
        result = settlement_map[ticker]

        # Find entry window
        entry_window = group[group['mins_left'] <= ENTRY_WINDOW_MINS]
        if len(entry_window) == 0:
            continue

        entry = entry_window.iloc[0]

        # Check YES side
        if MIN_ENTRY_PRICE <= entry['yes_ask'] <= MAX_ENTRY_PRICE:
            opportunities.append({
                'ticker': ticker,
                'timestamp': entry['timestamp'],
                'side': 'yes',
                'entry_price': int(entry['yes_ask']),
                'result': result,
            })

        # Check NO side
        if MIN_ENTRY_PRICE <= entry['no_ask'] <= MAX_ENTRY_PRICE:
            opportunities.append({
                'ticker': ticker,
                'timestamp': entry['timestamp'],
                'side': 'no',
                'entry_price': int(entry['no_ask']),
                'result': result,
            })

    # Sort by timestamp
    opportunities.sort(key=lambda x: x['timestamp'])

    print(f"\nFound {len(opportunities)} opportunities in 80-92c range")
    wins = sum(1 for o in opportunities if o['result'] == o['side'])
    print(f"Raw: {wins}W / {len(opportunities)-wins}L ({wins/len(opportunities)*100:.1f}%)")

    # Run backtests
    results = {}
    for bankroll, label in [(40, 'Small ($40)'), (400, 'Medium ($400)'), (4000, 'Large ($4000)')]:
        res = run_backtest(opportunities, bankroll, use_martingale=True, min_price=80)
        results[label] = res

        status = "IN RECOVERY" if res['ended_in_recovery'] else "Clean"
        print(f"\n{label}: ${bankroll:.2f} -> ${res['final_bankroll']:.2f} "
              f"({res['total_pnl']:+.2f}, {res['roi']:+.1f}%) [{status}]")

    # Generate HTML report
    output_path = '/Users/corbandamukaitis/Desktop/Personal Projects/15 minute trade strategy/kalshi-official-trader-corbeezy-the-king/backtests/2026-03-18/backtest_report.html'
    generate_html_report(results, opportunities, output_path)

    # Also save JSON
    json_path = output_path.replace('.html', '.json')
    with open(json_path, 'w') as f:
        json.dump({k: {kk: vv for kk, vv in v.items() if kk != 'trades'} for k, v in results.items()}, f, indent=2)

    print(f"\nJSON summary saved to: {json_path}")


if __name__ == "__main__":
    main()
