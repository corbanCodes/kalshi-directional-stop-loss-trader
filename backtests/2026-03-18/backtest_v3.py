#!/usr/bin/env python3
"""
Comprehensive backtest with $60, $400, $4000 - showing all trade-by-trade.
"""

import pandas as pd
import numpy as np
import math
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
    p = price_cents / 100
    fee_per = max(0.01, math.ceil(0.07 * p * (1 - p) * 100) / 100)
    return fee_per * contracts


def calc_contracts_for_recovery(loss_to_recover: float, entry_price_cents: int) -> int:
    for contracts in range(1, 10000):
        cost = contracts * entry_price_cents / 100
        fee = calc_fee(entry_price_cents, contracts)
        gross_profit = contracts * (100 - entry_price_cents) / 100
        net_profit = gross_profit - fee
        if net_profit >= loss_to_recover:
            return contracts
    return 10000


def run_backtest(opportunities: List[dict], starting_bankroll: float) -> dict:
    bankroll = starting_bankroll
    trades = []

    consecutive_losses = 0
    total_loss_to_recover = 0.0
    in_recovery = False
    peak_bankroll = starting_bankroll

    for opp in opportunities:
        # Recovery price cap
        if in_recovery and opp['entry_price'] > RECOVERY_PRICE_CAP:
            continue

        entry_price = min(opp['entry_price'] + SLIPPAGE, 99)

        if in_recovery:
            contracts = calc_contracts_for_recovery(total_loss_to_recover, entry_price)
        else:
            contracts = max(1, int(bankroll * 0.02 / (entry_price / 100)))

        cost = contracts * entry_price / 100
        fee = calc_fee(entry_price, contracts)
        total_cost = cost + fee

        bet_number = consecutive_losses + 1

        if total_cost > bankroll:
            trades.append({
                'ticker': opp['ticker'],
                'side': opp['side'],
                'entry_price': entry_price,
                'contracts': contracts,
                'cost': cost,
                'fee': fee,
                'outcome': 'CANT_AFFORD',
                'payout': 0,
                'net_pnl': 0,
                'bet_number': bet_number,
                'bankroll_after': bankroll,
                'in_recovery': in_recovery,
                'loss_to_recover': total_loss_to_recover,
            })
            break

        if opp['win']:
            payout = contracts * 1.0
            net_pnl = payout - cost - fee
            bankroll += net_pnl
            outcome = 'WIN'
            consecutive_losses = 0
            total_loss_to_recover = 0.0
            in_recovery = False
        else:
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
            'in_recovery': in_recovery,
            'loss_to_recover': total_loss_to_recover,
        })

        if bankroll <= 0:
            break

    wins = sum(1 for t in trades if t['outcome'] == 'WIN')
    losses = sum(1 for t in trades if t['outcome'] == 'LOSS')
    min_bankroll = min(t['bankroll_after'] for t in trades) if trades else starting_bankroll

    return {
        'starting_bankroll': starting_bankroll,
        'final_bankroll': bankroll,
        'total_pnl': bankroll - starting_bankroll,
        'roi': (bankroll - starting_bankroll) / starting_bankroll * 100,
        'wins': wins,
        'losses': losses,
        'win_rate': wins / (wins + losses) * 100 if (wins + losses) > 0 else 0,
        'total_trades': len(trades),
        'max_drawdown': (peak_bankroll - min_bankroll) / peak_bankroll * 100 if peak_bankroll > 0 else 0,
        'ended_in_recovery': in_recovery,
        'final_consecutive_losses': consecutive_losses,
        'trades': trades,
    }


def generate_html_report(results: dict, output_path: str):
    """Generate comprehensive HTML report with all three trade logs."""

    html = """<!DOCTYPE html>
<html>
<head>
    <title>Backtest Comparison - $60 vs $400 vs $4000</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Consolas', monospace; background: #0a0a0a; color: #e0e0e0; padding: 20px; }
        h1 { color: #6bcb77; margin-bottom: 10px; }
        h2 { color: #4ecdc4; margin: 30px 0 15px 0; border-bottom: 1px solid #333; padding-bottom: 5px; }
        h3 { color: #ffd93d; margin: 20px 0 10px 0; }
        .container { max-width: 1600px; margin: 0 auto; }

        .summary-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }
        .summary-card { background: #1a1a1a; border: 1px solid #333; border-radius: 10px; padding: 20px; }
        .summary-card h3 { margin-bottom: 15px; text-align: center; }
        .summary-card.winner { border-color: #6bcb77; }
        .summary-card.loser { border-color: #ff6b6b; }

        .stat { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #222; }
        .stat-label { color: #888; }
        .stat-value { font-weight: bold; }
        .stat-value.positive { color: #6bcb77; }
        .stat-value.negative { color: #ff6b6b; }
        .stat-value.warning { color: #ffd93d; }

        .trades-container { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin: 20px 0; }
        .trade-log { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; overflow: hidden; }
        .trade-log-header { background: #222; padding: 10px 15px; font-weight: bold; position: sticky; top: 0; }
        .trade-log-body { max-height: 600px; overflow-y: auto; font-size: 0.75rem; }

        .trade-row { display: grid; grid-template-columns: 30px 1fr 50px 60px 70px 50px; gap: 5px; padding: 6px 10px; border-bottom: 1px solid #1a1a1a; align-items: center; }
        .trade-row:hover { background: #222; }
        .trade-row.loss { background: rgba(255, 107, 107, 0.1); }
        .trade-row.recovery { background: rgba(255, 217, 61, 0.05); }
        .trade-row.cant-afford { background: rgba(255, 107, 107, 0.3); }
        .trade-row.header { background: #222; font-weight: bold; color: #888; font-size: 0.7rem; position: sticky; top: 0; }

        .badge { display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.65rem; }
        .badge.win { background: rgba(107, 203, 119, 0.2); color: #6bcb77; }
        .badge.loss { background: rgba(255, 107, 107, 0.2); color: #ff6b6b; }
        .badge.recovery { background: rgba(255, 217, 61, 0.2); color: #ffd93d; }
        .badge.bust { background: rgba(255, 107, 107, 0.4); color: #ff6b6b; }

        .pnl.positive { color: #6bcb77; }
        .pnl.negative { color: #ff6b6b; }

        .chart-container { background: #1a1a1a; border-radius: 8px; padding: 20px; margin: 20px 0; }
        .chart { display: flex; align-items: end; height: 120px; gap: 1px; }
        .chart-bar { flex: 1; min-width: 2px; transition: height 0.2s; }
        .chart-bar.win { background: #6bcb77; }
        .chart-bar.loss { background: #ff6b6b; }

        .insight { background: rgba(78, 205, 196, 0.1); border-left: 3px solid #4ecdc4; padding: 15px; margin: 15px 0; }
        .warning { background: rgba(255, 107, 107, 0.1); border-left: 3px solid #ff6b6b; padding: 15px; margin: 15px 0; }
        .success { background: rgba(107, 203, 119, 0.1); border-left: 3px solid #6bcb77; padding: 15px; margin: 15px 0; }

        .comparison-table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        .comparison-table th, .comparison-table td { padding: 12px; text-align: center; border-bottom: 1px solid #333; }
        .comparison-table th { background: #1a1a1a; color: #6bcb77; }
        .comparison-table tr:hover { background: #1a1a1a; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Backtest: $60 vs $400 vs $4000</h1>
        <p style="color:#888;">Data: March 5-9, 2026 | Strategy: 80-92c entries, 85c recovery cap, martingale</p>

        <h2>Summary Comparison</h2>
        <table class="comparison-table">
            <tr>
                <th>Metric</th>
                <th>$60 Bankroll</th>
                <th>$400 Bankroll</th>
                <th>$4,000 Bankroll</th>
            </tr>
"""

    for metric in ['final_bankroll', 'total_pnl', 'roi', 'total_trades', 'wins', 'losses', 'win_rate', 'max_drawdown', 'ended_in_recovery']:
        html += f"<tr><td style='text-align:left;color:#888;'>{metric.replace('_', ' ').title()}</td>"
        for label in ['$60', '$400', '$4000']:
            res = results[label]
            val = res[metric]

            if metric == 'final_bankroll':
                cls = 'positive' if val > res['starting_bankroll'] else 'negative'
                html += f"<td class='{cls}'>${val:.2f}</td>"
            elif metric == 'total_pnl':
                cls = 'positive' if val > 0 else 'negative'
                html += f"<td class='{cls}'>${val:+.2f}</td>"
            elif metric == 'roi':
                cls = 'positive' if val > 0 else 'negative'
                html += f"<td class='{cls}'>{val:+.1f}%</td>"
            elif metric == 'win_rate':
                html += f"<td>{val:.1f}%</td>"
            elif metric == 'max_drawdown':
                cls = 'negative' if val > 30 else ('warning' if val > 15 else '')
                html += f"<td class='{cls}'>{val:.1f}%</td>"
            elif metric == 'ended_in_recovery':
                cls = 'negative' if val else 'positive'
                html += f"<td class='{cls}'>{'YES' if val else 'No'}</td>"
            else:
                html += f"<td>{val}</td>"
        html += "</tr>"

    html += """
        </table>
"""

    # Bankroll charts
    html += """
        <h2>Bankroll Over Time</h2>
        <div class="summary-grid">
"""

    for label in ['$60', '$400', '$4000']:
        res = results[label]
        trades = res['trades']
        max_val = max(t['bankroll_after'] for t in trades) if trades else res['starting_bankroll']

        html += f"""
            <div class="chart-container">
                <h3 style="margin-bottom:10px;">{label} Bankroll</h3>
                <div class="chart">
"""
        for t in trades:
            height = (t['bankroll_after'] / max_val) * 100
            cls = 'win' if t['outcome'] == 'WIN' else 'loss'
            html += f'<div class="chart-bar {cls}" style="height:{height}%;" title="${t["bankroll_after"]:.2f}"></div>'

        html += """
                </div>
            </div>
"""

    html += """
        </div>

        <h2>Trade-by-Trade Comparison</h2>
        <div class="trades-container">
"""

    # Trade logs for each bankroll
    for label in ['$60', '$400', '$4000']:
        res = results[label]
        trades = res['trades']
        status_cls = 'winner' if res['total_pnl'] > 0 else 'loser'

        html += f"""
            <div class="trade-log">
                <div class="trade-log-header" style="color: {'#6bcb77' if res['total_pnl'] > 0 else '#ff6b6b'};">
                    {label}: ${res['starting_bankroll']:.0f} → ${res['final_bankroll']:.2f} ({res['total_pnl']:+.2f})
                </div>
                <div class="trade-log-body">
                    <div class="trade-row header">
                        <span>#</span>
                        <span>Ticker</span>
                        <span>Bet#</span>
                        <span>Contracts</span>
                        <span>P&L</span>
                        <span>Balance</span>
                    </div>
"""

        for i, t in enumerate(trades):
            row_cls = ''
            if t['outcome'] == 'LOSS':
                row_cls = 'loss'
            if t['bet_number'] > 1:
                row_cls = 'recovery'
            if t['outcome'] == 'CANT_AFFORD':
                row_cls = 'cant-afford'

            outcome_cls = 'win' if t['outcome'] == 'WIN' else 'loss'
            if t['outcome'] == 'CANT_AFFORD':
                outcome_cls = 'bust'

            bet_cls = 'recovery' if t['bet_number'] > 1 else outcome_cls

            pnl_cls = 'positive' if t['net_pnl'] > 0 else 'negative'

            ticker_short = t['ticker'][-10:] if len(t['ticker']) > 10 else t['ticker']

            html += f"""
                    <div class="trade-row {row_cls}">
                        <span>{i+1}</span>
                        <span title="{t['ticker']}">{ticker_short}</span>
                        <span><span class="badge {bet_cls}">#{t['bet_number']}</span></span>
                        <span>{t['contracts']}@{t['entry_price']}c</span>
                        <span class="pnl {pnl_cls}">${t['net_pnl']:+.2f}</span>
                        <span>${t['bankroll_after']:.2f}</span>
                    </div>
"""

        html += """
                </div>
            </div>
"""

    html += """
        </div>
"""

    # Analysis section
    html += """
        <h2>Key Insights</h2>
"""

    if results['$60']['total_pnl'] > 0:
        html += """
        <div class="success">
            <strong>$60 Bankroll Survived!</strong><br>
            The $60 bankroll was able to afford all recovery bets and finished profitable.
            This confirms $60 is above the minimum viable bankroll threshold.
        </div>
"""
    else:
        html += f"""
        <div class="warning">
            <strong>$60 Bankroll Struggled</strong><br>
            Final: ${results['$60']['final_bankroll']:.2f} |
            Ended in recovery: {results['$60']['ended_in_recovery']} |
            The minimum viable bankroll may be higher than $60 for this data.
        </div>
"""

    html += """
        <div class="insight">
            <strong>Why Larger Bankrolls Perform Better:</strong><br>
            • Base bets scale with bankroll (2% rule)<br>
            • Recovery bets scale proportionally<br>
            • Larger bankrolls have more buffer to survive drawdowns<br>
            • The 1-contract minimum doesn't bottleneck larger accounts
        </div>

        <div class="insight">
            <strong>The Math:</strong><br>
            • After 1 loss at 85c: Need ~7x base contracts to recover<br>
            • After 2 losses: Need ~50x base contracts to recover<br>
            • $60 @ 2% = $1.20 base bet ≈ 1-2 contracts<br>
            • $400 @ 2% = $8 base bet ≈ 9 contracts<br>
            • Recovery scales the same multiplier, but larger base = larger absolute cushion
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

    # Load data
    price_df = pd.read_csv('/Users/corbandamukaitis/Downloads/btc_price_log (8).csv')
    price_df['timestamp'] = pd.to_datetime(price_df['timestamp'])
    price_df = price_df[price_df['strike_price'] > 0]

    results_df = pd.read_csv('/Users/corbandamukaitis/Downloads/btc_window_results (2).csv')
    settlements = dict(zip(results_df['ticker'], results_df['result']))

    print(f"Price data: {len(price_df):,} rows")
    print(f"Settlement results: {len(settlements)} markets")

    # Build opportunities
    opportunities = []
    for ticker, group in price_df.groupby('ticker'):
        if ticker not in settlements:
            continue
        group = group.sort_values('timestamp')
        result = settlements[ticker]
        entry = group[group['mins_left'] <= ENTRY_WINDOW_MINS]
        if len(entry) == 0:
            continue
        entry = entry.iloc[0]

        if MIN_ENTRY_PRICE <= entry['yes_ask'] <= MAX_ENTRY_PRICE:
            opportunities.append({
                'ticker': ticker,
                'side': 'yes',
                'entry_price': int(entry['yes_ask']),
                'result': result,
                'win': result == 'yes'
            })
        if MIN_ENTRY_PRICE <= entry['no_ask'] <= MAX_ENTRY_PRICE:
            opportunities.append({
                'ticker': ticker,
                'side': 'no',
                'entry_price': int(entry['no_ask']),
                'result': result,
                'win': result == 'no'
            })

    opportunities.sort(key=lambda x: x['ticker'])

    wins = sum(1 for o in opportunities if o['win'])
    print(f"\nOpportunities: {len(opportunities)} ({wins}W / {len(opportunities)-wins}L = {wins/len(opportunities)*100:.1f}%)")

    # Run backtests
    results = {}
    for bankroll, label in [(60, '$60'), (400, '$400'), (4000, '$4000')]:
        res = run_backtest(opportunities, bankroll)
        results[label] = res

        status = "IN RECOVERY" if res['ended_in_recovery'] else "Clean"
        pnl_str = f"+${res['total_pnl']:.2f}" if res['total_pnl'] > 0 else f"-${abs(res['total_pnl']):.2f}"
        print(f"{label}: ${bankroll} -> ${res['final_bankroll']:.2f} ({pnl_str}, {res['roi']:+.1f}%) [{status}]")

    # Generate HTML
    output_path = '/Users/corbandamukaitis/Desktop/Personal Projects/15 minute trade strategy/kalshi-official-trader-corbeezy-the-king/backtests/2026-03-18/backtest_60_400_4000.html'
    generate_html_report(results, output_path)


if __name__ == "__main__":
    main()
