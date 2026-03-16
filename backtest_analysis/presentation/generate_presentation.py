#!/usr/bin/env python3
"""
Generate comprehensive HTML presentation for ATTACK/RECOVER hybrid strategy
"""

import pandas as pd
import math
import json
from datetime import datetime

# Persistence data from 5-year BTC analysis
PERSISTENCE = {
    1: 0.5869, 2: 0.6258, 3: 0.6565, 4: 0.6812, 5: 0.7128,
    6: 0.7407, 7: 0.7674, 8: 0.7920, 9: 0.8143, 10: 0.8408,
    11: 0.8695, 12: 0.8992, 13: 0.9318
}

def kalshi_fee(price_cents):
    price = price_cents / 100
    return max(0.01, 0.07 * price * (1 - price))

def run_detailed_backtest(df_s2, df_s3, starting_bankroll=1000):
    """Run backtest and capture all data for visualization"""

    bankroll = starting_bankroll
    mode = "ATTACK"
    consecutive_losses = 0
    total_loss_to_recover = 0

    # Detailed tracking
    equity_curve = [{"trade": 0, "bankroll": bankroll, "mode": "START"}]
    all_trades = []
    recovery_attempts = []
    mode_switches = []

    # Stats
    attack_wins = 0
    attack_losses = 0
    recover_wins = 0
    recover_losses = 0
    max_drawdown = 0
    peak = starting_bankroll

    # Merge and sort
    df_s2 = df_s2.copy()
    df_s3 = df_s3.copy()
    df_s2['source'] = 's2'
    df_s3['source'] = 's3'
    df_s2['Timestamp'] = pd.to_datetime(df_s2['Timestamp'])
    df_s3['Timestamp'] = pd.to_datetime(df_s3['Timestamp'])

    combined = pd.concat([df_s2, df_s3]).sort_values('Timestamp').reset_index(drop=True)
    traded_windows = set()
    trade_num = 0

    for _, row in combined.iterrows():
        window = row['Window']
        source = row['source']
        entry_price = row['Entry Price']
        outcome = row['Outcome']
        timestamp = row['Timestamp']

        if window in traded_windows:
            continue

        should_trade = False
        bet_contracts = 0
        edge = 0
        bet_pct = 0

        if mode == "ATTACK" and source == 's2':
            edge_pct = row.get('Edge %', 'N/A')
            if edge_pct != 'N/A' and not pd.isna(edge_pct):
                edge = float(edge_pct) / 100
                if edge >= 0.10:
                    # Dynamic scaling: 2% base, up to 8% at high edge
                    scale = min(1, max(0, (edge - 0.10) / 0.20))
                    bet_pct = 0.02 + scale * 0.06  # 2% to 8%
                    bet_amount = bankroll * bet_pct
                    bet_contracts = max(1, int(bet_amount / (entry_price / 100)))
                    should_trade = True

        elif mode == "RECOVER" and source == 's3':
            if 80 <= entry_price <= 92:
                fee_per = kalshi_fee(entry_price)
                net_profit_per = (1.0 - entry_price/100) - fee_per
                recovery_target = total_loss_to_recover * 1.10

                if net_profit_per > 0:
                    bet_contracts = math.ceil(recovery_target / net_profit_per)
                else:
                    bet_contracts = 1
                should_trade = True
                bet_pct = 0  # Recovery uses fixed calculation

        if not should_trade:
            continue

        cost = bet_contracts * (entry_price / 100)
        fee = bet_contracts * kalshi_fee(entry_price)

        if cost > bankroll:
            if mode == "RECOVER":
                mode_switches.append({
                    "trade": trade_num,
                    "from": "RECOVER",
                    "to": "ATTACK",
                    "reason": "Can't afford recovery bet"
                })
                mode = "ATTACK"
                consecutive_losses = 0
                total_loss_to_recover = 0
            continue

        trade_num += 1
        traded_windows.add(window)

        trade_record = {
            "num": trade_num,
            "timestamp": str(timestamp),
            "mode": mode,
            "entry_price": entry_price,
            "contracts": bet_contracts,
            "cost": round(cost, 2),
            "fee": round(fee, 2),
            "edge": round(edge * 100, 1) if edge else 0,
            "bet_pct": round(bet_pct * 100, 1),
            "bankroll_before": round(bankroll, 2),
        }

        if outcome == 'win':
            payout = bet_contracts * 1.0
            profit = payout - cost - fee
            bankroll += profit
            trade_record["outcome"] = "WIN"
            trade_record["profit"] = round(profit, 2)

            if mode == "ATTACK":
                attack_wins += 1
            else:
                recover_wins += 1
                recovery_attempts.append({
                    "trade": trade_num,
                    "recovered": round(total_loss_to_recover, 2),
                    "attempts": consecutive_losses,
                    "success": True
                })
                mode_switches.append({
                    "trade": trade_num,
                    "from": "RECOVER",
                    "to": "ATTACK",
                    "reason": f"Recovered ${total_loss_to_recover:.2f}"
                })
                mode = "ATTACK"
                consecutive_losses = 0
                total_loss_to_recover = 0
        else:
            loss = cost + fee
            bankroll -= loss
            trade_record["outcome"] = "LOSS"
            trade_record["profit"] = round(-loss, 2)

            if mode == "ATTACK":
                attack_losses += 1
                mode_switches.append({
                    "trade": trade_num,
                    "from": "ATTACK",
                    "to": "RECOVER",
                    "reason": f"Lost ${loss:.2f}"
                })
                mode = "RECOVER"
                consecutive_losses = 1
                total_loss_to_recover = loss
            else:
                recover_losses += 1
                consecutive_losses += 1
                total_loss_to_recover += loss

                if consecutive_losses > 2:
                    recovery_attempts.append({
                        "trade": trade_num,
                        "lost": round(total_loss_to_recover, 2),
                        "attempts": consecutive_losses,
                        "success": False
                    })
                    mode_switches.append({
                        "trade": trade_num,
                        "from": "RECOVER",
                        "to": "ATTACK",
                        "reason": f"Max recovery exceeded, lost ${total_loss_to_recover:.2f}"
                    })
                    mode = "ATTACK"
                    consecutive_losses = 0
                    total_loss_to_recover = 0

        trade_record["bankroll_after"] = round(bankroll, 2)
        all_trades.append(trade_record)

        # Track drawdown
        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

        equity_curve.append({
            "trade": trade_num,
            "bankroll": round(bankroll, 2),
            "mode": mode,
            "drawdown": round(dd, 2)
        })

    # Calculate stats
    total_trades = len(all_trades)
    total_wins = attack_wins + recover_wins
    total_losses = attack_losses + recover_losses

    # Time period
    first_trade = pd.to_datetime(all_trades[0]["timestamp"])
    last_trade = pd.to_datetime(all_trades[-1]["timestamp"])
    days = (last_trade - first_trade).days + 1

    results = {
        "starting_bankroll": starting_bankroll,
        "final_bankroll": round(bankroll, 2),
        "profit": round(bankroll - starting_bankroll, 2),
        "roi": round((bankroll / starting_bankroll - 1) * 100, 1),
        "total_trades": total_trades,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "win_rate": round(total_wins / total_trades * 100, 1),
        "attack_trades": attack_wins + attack_losses,
        "attack_wins": attack_wins,
        "attack_losses": attack_losses,
        "attack_win_rate": round(attack_wins / (attack_wins + attack_losses) * 100, 1) if (attack_wins + attack_losses) > 0 else 0,
        "recover_trades": recover_wins + recover_losses,
        "recover_wins": recover_wins,
        "recover_losses": recover_losses,
        "recover_win_rate": round(recover_wins / (recover_wins + recover_losses) * 100, 1) if (recover_wins + recover_losses) > 0 else 0,
        "max_drawdown": round(max_drawdown, 1),
        "days": days,
        "daily_roi": round((bankroll / starting_bankroll - 1) * 100 / days, 1),
        "first_trade": str(first_trade.date()),
        "last_trade": str(last_trade.date()),
        "equity_curve": equity_curve,
        "trades": all_trades,
        "recovery_attempts": recovery_attempts,
        "mode_switches": mode_switches,
    }

    return results


def generate_html(results):
    """Generate beautiful HTML presentation"""

    equity_data = json.dumps(results["equity_curve"])
    trades_data = json.dumps(results["trades"][:100])  # First 100 for table

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATTACK/RECOVER Hybrid Strategy - Backtest Results</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            font-size: 2.5rem;
            margin-bottom: 10px;
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            text-align: center;
            color: #888;
            margin-bottom: 40px;
            font-size: 1.1rem;
        }}
        .hero-stats {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 40px;
        }}
        .stat-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 25px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .stat-card.profit {{
            border-color: #00ff88;
            box-shadow: 0 0 30px rgba(0,255,136,0.2);
        }}
        .stat-value {{
            font-size: 2.5rem;
            font-weight: bold;
            margin-bottom: 5px;
        }}
        .stat-value.green {{ color: #00ff88; }}
        .stat-value.blue {{ color: #00d4ff; }}
        .stat-value.yellow {{ color: #ffd93d; }}
        .stat-value.red {{ color: #ff6b6b; }}
        .stat-label {{
            color: #888;
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .section {{
            background: rgba(255,255,255,0.03);
            border-radius: 15px;
            padding: 30px;
            margin-bottom: 30px;
            border: 1px solid rgba(255,255,255,0.05);
        }}
        .section h2 {{
            color: #00d4ff;
            margin-bottom: 20px;
            font-size: 1.5rem;
            border-bottom: 2px solid rgba(0,212,255,0.3);
            padding-bottom: 10px;
        }}
        .chart-container {{
            position: relative;
            height: 400px;
            margin: 20px 0;
        }}
        .strategy-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
        }}
        .mode-card {{
            background: rgba(0,0,0,0.3);
            border-radius: 10px;
            padding: 25px;
        }}
        .mode-card.attack {{
            border-left: 4px solid #ff6b6b;
        }}
        .mode-card.recover {{
            border-left: 4px solid #00ff88;
        }}
        .mode-card h3 {{
            font-size: 1.3rem;
            margin-bottom: 15px;
        }}
        .mode-card.attack h3 {{ color: #ff6b6b; }}
        .mode-card.recover h3 {{ color: #00ff88; }}
        .mode-card ul {{
            list-style: none;
            padding: 0;
        }}
        .mode-card li {{
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            display: flex;
            align-items: center;
        }}
        .mode-card li:before {{
            content: "→";
            margin-right: 10px;
            color: #00d4ff;
        }}
        .formula {{
            background: rgba(0,0,0,0.5);
            padding: 15px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 0.95rem;
            margin: 15px 0;
            border: 1px solid rgba(0,212,255,0.3);
        }}
        .comparison-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        .comparison-table th, .comparison-table td {{
            padding: 15px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        .comparison-table th {{
            background: rgba(0,212,255,0.1);
            color: #00d4ff;
        }}
        .comparison-table tr:hover {{
            background: rgba(255,255,255,0.03);
        }}
        .win {{ color: #00ff88; }}
        .loss {{ color: #ff6b6b; }}
        .highlight {{
            background: linear-gradient(90deg, rgba(0,255,136,0.2), transparent);
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
            border-left: 4px solid #00ff88;
        }}
        .flow-diagram {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 20px;
            margin: 30px 0;
            flex-wrap: wrap;
        }}
        .flow-box {{
            background: rgba(0,0,0,0.4);
            padding: 20px 30px;
            border-radius: 10px;
            text-align: center;
            min-width: 150px;
        }}
        .flow-box.attack {{ border: 2px solid #ff6b6b; }}
        .flow-box.recover {{ border: 2px solid #00ff88; }}
        .flow-box.win {{ border: 2px solid #00d4ff; }}
        .flow-arrow {{
            font-size: 2rem;
            color: #888;
        }}
        .persistence-table {{
            width: 100%;
            margin: 20px 0;
        }}
        .persistence-table td {{
            padding: 8px 15px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .trades-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }}
        .trades-table th, .trades-table td {{
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        .trades-table th {{
            background: rgba(0,0,0,0.3);
            position: sticky;
            top: 0;
        }}
        .trades-table .attack {{ background: rgba(255,107,107,0.1); }}
        .trades-table .recover {{ background: rgba(0,255,136,0.1); }}
        .badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: bold;
        }}
        .badge.attack {{ background: #ff6b6b; color: #000; }}
        .badge.recover {{ background: #00ff88; color: #000; }}
        .badge.win {{ background: #00ff88; color: #000; }}
        .badge.loss {{ background: #ff6b6b; color: #fff; }}
        .scroll-table {{
            max-height: 500px;
            overflow-y: auto;
        }}
        footer {{
            text-align: center;
            padding: 40px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚔️ ATTACK/RECOVER Hybrid Strategy</h1>
        <p class="subtitle">Backtest Results: {results["first_trade"]} to {results["last_trade"]} ({results["days"]} days)</p>

        <!-- Hero Stats -->
        <div class="hero-stats">
            <div class="stat-card profit">
                <div class="stat-value green">${results["profit"]:,}</div>
                <div class="stat-label">Total Profit</div>
            </div>
            <div class="stat-card">
                <div class="stat-value green">{results["roi"]}%</div>
                <div class="stat-label">Return on Investment</div>
            </div>
            <div class="stat-card">
                <div class="stat-value blue">{results["win_rate"]}%</div>
                <div class="stat-label">Win Rate</div>
            </div>
            <div class="stat-card">
                <div class="stat-value red">{results["max_drawdown"]}%</div>
                <div class="stat-label">Max Drawdown</div>
            </div>
        </div>

        <!-- More Stats -->
        <div class="hero-stats">
            <div class="stat-card">
                <div class="stat-value blue">{results["total_trades"]}</div>
                <div class="stat-label">Total Trades</div>
            </div>
            <div class="stat-card">
                <div class="stat-value yellow">{results["daily_roi"]}%</div>
                <div class="stat-label">Daily ROI</div>
            </div>
            <div class="stat-card">
                <div class="stat-value green">${results["final_bankroll"]:,}</div>
                <div class="stat-label">Final Bankroll</div>
            </div>
            <div class="stat-card">
                <div class="stat-value blue">${results["starting_bankroll"]:,}</div>
                <div class="stat-label">Starting Bankroll</div>
            </div>
        </div>

        <!-- Equity Curve -->
        <div class="section">
            <h2>📈 Equity Curve</h2>
            <div class="chart-container">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <!-- How It Works -->
        <div class="section">
            <h2>🎯 How The Strategy Works</h2>

            <div class="flow-diagram">
                <div class="flow-box attack">
                    <strong>ATTACK MODE</strong><br>
                    <small>S2 System</small>
                </div>
                <div class="flow-arrow">→ Loss →</div>
                <div class="flow-box recover">
                    <strong>RECOVER MODE</strong><br>
                    <small>S3 System</small>
                </div>
                <div class="flow-arrow">→ Win →</div>
                <div class="flow-box attack">
                    <strong>ATTACK MODE</strong><br>
                    <small>Back to profit</small>
                </div>
            </div>

            <div class="strategy-grid">
                <div class="mode-card attack">
                    <h3>⚔️ ATTACK MODE (S2 System)</h3>
                    <p style="color:#888; margin-bottom:15px;">Aggressive profit-making using edge-based entries</p>
                    <ul>
                        <li><strong>Wait 5 minutes</strong> into 15-min window</li>
                        <li><strong>Check BTC position</strong> - above or below strike?</li>
                        <li><strong>Calculate edge</strong> from 5-year persistence data</li>
                        <li><strong>Enter if edge ≥ 10%</strong></li>
                        <li><strong>Dynamic bet sizing</strong> based on edge strength</li>
                    </ul>

                    <div class="formula">
                        <strong>Edge Calculation:</strong><br>
                        edge = historical_persistence - kalshi_price<br><br>
                        <strong>Example at minute 5:</strong><br>
                        History says BTC stays in direction 71.3% of time<br>
                        Kalshi prices it at 55c (55% implied)<br>
                        Edge = 71.3% - 55% = <span class="win">16.3% edge</span>
                    </div>

                    <div class="formula">
                        <strong>Dynamic Bet Sizing:</strong><br>
                        scale = (edge - 10%) / 20%  <span style="color:#888;"># 0 to 1</span><br>
                        bet_pct = 2% + scale × 6%  <span style="color:#888;"># 2% to 8% of bankroll</span><br><br>
                        <strong>Examples:</strong><br>
                        • 10% edge → 2% of bankroll<br>
                        • 20% edge → 5% of bankroll<br>
                        • 30%+ edge → 8% of bankroll
                    </div>
                </div>

                <div class="mode-card recover">
                    <h3>🛡️ RECOVER MODE (S3 System)</h3>
                    <p style="color:#888; margin-bottom:15px;">Conservative recovery using high win-rate entries</p>
                    <ul>
                        <li><strong>Wait 10 minutes</strong> into 15-min window</li>
                        <li><strong>Only enter at 80-92c</strong> (sweet spot)</li>
                        <li><strong>90%+ win rate</strong> at these prices</li>
                        <li><strong>Martingale sizing</strong> to recover losses</li>
                        <li><strong>Max 2 recovery attempts</strong> then reset</li>
                    </ul>

                    <div class="formula">
                        <strong>Recovery Bet Calculation:</strong><br>
                        recovery_target = total_loss × 1.10  <span style="color:#888;"># +10% buffer</span><br>
                        net_profit_per_contract = (100¢ - entry) - fee<br>
                        contracts = recovery_target / net_profit_per_contract<br><br>
                        <strong>Example:</strong><br>
                        Lost $50 → Need to recover $55 (with buffer)<br>
                        Entry at 85c → Net profit = 15c - 1c fee = 14c<br>
                        Contracts needed = $55 / $0.14 = <span class="win">393 contracts</span>
                    </div>

                    <div class="formula">
                        <strong>Why 80-92c?</strong><br>
                        • At 10 min mark, persistence is 84%+<br>
                        • Kalshi prices at 80-92c = slight edge<br>
                        • Combined = <span class="win">90%+ actual win rate</span><br>
                        • Safe for recovery!
                    </div>
                </div>
            </div>
        </div>

        <!-- Persistence Data -->
        <div class="section">
            <h2>📊 5-Year BTC Persistence Data</h2>
            <p style="color:#888; margin-bottom:20px;">This is the historical data that powers the edge calculation. Based on 137,206 fifteen-minute windows from 2019-2024.</p>

            <table class="persistence-table">
                <tr style="background:rgba(0,212,255,0.1);">
                    <td><strong>Minute</strong></td>
                    <td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td><td>8</td><td>9</td><td>10</td><td>11</td><td>12</td><td>13</td>
                </tr>
                <tr>
                    <td><strong>Mins Left</strong></td>
                    <td>13</td><td>12</td><td>11</td><td>10</td><td>9</td><td>8</td><td>7</td><td>6</td><td>5</td><td>4</td><td>3</td><td>2</td><td>1</td>
                </tr>
                <tr style="background:rgba(0,255,136,0.1);">
                    <td><strong>Persistence</strong></td>
                    <td>58.7%</td><td>62.6%</td><td>65.7%</td><td>68.1%</td><td class="win"><strong>71.3%</strong></td><td>74.1%</td><td>76.7%</td><td>79.2%</td><td>81.4%</td><td class="win"><strong>84.1%</strong></td><td>87.0%</td><td>89.9%</td><td>93.2%</td>
                </tr>
            </table>

            <div class="highlight">
                <strong>Key Insight:</strong> By minute 5, if BTC is above the strike, it stays above 71.3% of the time. By minute 10, it's 84.1%. This "momentum" or "persistence" is the edge we exploit.
            </div>
        </div>

        <!-- Mode Breakdown -->
        <div class="section">
            <h2>📊 Performance by Mode</h2>

            <table class="comparison-table">
                <tr>
                    <th>Mode</th>
                    <th>Trades</th>
                    <th>Wins</th>
                    <th>Losses</th>
                    <th>Win Rate</th>
                    <th>Purpose</th>
                </tr>
                <tr>
                    <td><span class="badge attack">ATTACK</span></td>
                    <td>{results["attack_trades"]}</td>
                    <td class="win">{results["attack_wins"]}</td>
                    <td class="loss">{results["attack_losses"]}</td>
                    <td>{results["attack_win_rate"]}%</td>
                    <td>Profit generation</td>
                </tr>
                <tr>
                    <td><span class="badge recover">RECOVER</span></td>
                    <td>{results["recover_trades"]}</td>
                    <td class="win">{results["recover_wins"]}</td>
                    <td class="loss">{results["recover_losses"]}</td>
                    <td>{results["recover_win_rate"]}%</td>
                    <td>Loss recovery</td>
                </tr>
            </table>

            <div class="chart-container" style="height:300px;">
                <canvas id="modeChart"></canvas>
            </div>
        </div>

        <!-- Comparison -->
        <div class="section">
            <h2>🏆 Strategy Comparison</h2>

            <table class="comparison-table">
                <tr>
                    <th>Strategy</th>
                    <th>Final Bankroll</th>
                    <th>Profit</th>
                    <th>ROI</th>
                    <th>Max Drawdown</th>
                </tr>
                <tr style="background:rgba(0,255,136,0.15);">
                    <td><strong>⚔️ ATTACK/RECOVER Hybrid</strong></td>
                    <td class="win"><strong>${results["final_bankroll"]:,}</strong></td>
                    <td class="win"><strong>${results["profit"]:,}</strong></td>
                    <td class="win"><strong>{results["roi"]}%</strong></td>
                    <td>{results["max_drawdown"]}%</td>
                </tr>
                <tr>
                    <td>Pure S2 (edge-based only)</td>
                    <td>$6,829</td>
                    <td>$5,829</td>
                    <td>582.9%</td>
                    <td>65.2%</td>
                </tr>
                <tr>
                    <td>Pure S3 (martingale only)</td>
                    <td>$2,019</td>
                    <td>$1,019</td>
                    <td>101.9%</td>
                    <td>18.3%</td>
                </tr>
            </table>

            <div class="highlight">
                <strong>The hybrid outperforms both pure strategies!</strong> By using S2 for aggressive profit-making and switching to S3's high win-rate for recovery, we get the best of both worlds.
            </div>
        </div>

        <!-- Drawdown Chart -->
        <div class="section">
            <h2>📉 Drawdown Analysis</h2>
            <div class="chart-container">
                <canvas id="drawdownChart"></canvas>
            </div>
        </div>

        <!-- Recent Trades -->
        <div class="section">
            <h2>📝 Sample Trades (First 100)</h2>
            <div class="scroll-table">
                <table class="trades-table">
                    <tr>
                        <th>#</th>
                        <th>Mode</th>
                        <th>Entry</th>
                        <th>Contracts</th>
                        <th>Cost</th>
                        <th>Edge</th>
                        <th>Bet %</th>
                        <th>Result</th>
                        <th>P/L</th>
                        <th>Bankroll</th>
                    </tr>
                    <tbody id="tradesBody"></tbody>
                </table>
            </div>
        </div>

        <footer>
            <p>Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            <p>ATTACK/RECOVER Hybrid Strategy Backtest</p>
        </footer>
    </div>

    <script>
        // Data
        const equityData = {equity_data};
        const tradesData = {trades_data};

        // Equity Chart
        const equityCtx = document.getElementById('equityChart').getContext('2d');
        new Chart(equityCtx, {{
            type: 'line',
            data: {{
                labels: equityData.map(d => d.trade),
                datasets: [{{
                    label: 'Bankroll',
                    data: equityData.map(d => d.bankroll),
                    borderColor: '#00ff88',
                    backgroundColor: 'rgba(0, 255, 136, 0.1)',
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: function(ctx) {{ return '$' + ctx.raw.toLocaleString(); }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        title: {{ display: true, text: 'Trade #', color: '#888' }},
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#888' }}
                    }},
                    y: {{
                        title: {{ display: true, text: 'Bankroll ($)', color: '#888' }},
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{
                            color: '#888',
                            callback: v => '$' + v.toLocaleString()
                        }}
                    }}
                }}
            }}
        }});

        // Drawdown Chart
        const ddCtx = document.getElementById('drawdownChart').getContext('2d');
        new Chart(ddCtx, {{
            type: 'line',
            data: {{
                labels: equityData.map(d => d.trade),
                datasets: [{{
                    label: 'Drawdown %',
                    data: equityData.map(d => -d.drawdown),
                    borderColor: '#ff6b6b',
                    backgroundColor: 'rgba(255, 107, 107, 0.3)',
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    x: {{
                        title: {{ display: true, text: 'Trade #', color: '#888' }},
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#888' }}
                    }},
                    y: {{
                        title: {{ display: true, text: 'Drawdown (%)', color: '#888' }},
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{
                            color: '#888',
                            callback: v => v + '%'
                        }},
                        max: 0,
                        min: -80
                    }}
                }}
            }}
        }});

        // Mode Chart
        const modeCtx = document.getElementById('modeChart').getContext('2d');
        new Chart(modeCtx, {{
            type: 'bar',
            data: {{
                labels: ['ATTACK Mode', 'RECOVER Mode'],
                datasets: [
                    {{
                        label: 'Wins',
                        data: [{results["attack_wins"]}, {results["recover_wins"]}],
                        backgroundColor: '#00ff88',
                    }},
                    {{
                        label: 'Losses',
                        data: [{results["attack_losses"]}, {results["recover_losses"]}],
                        backgroundColor: '#ff6b6b',
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        labels: {{ color: '#888' }}
                    }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#888' }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#888' }}
                    }}
                }}
            }}
        }});

        // Populate trades table
        const tbody = document.getElementById('tradesBody');
        tradesData.forEach(t => {{
            const row = document.createElement('tr');
            row.className = t.mode.toLowerCase();
            row.innerHTML = `
                <td>${{t.num}}</td>
                <td><span class="badge ${{t.mode.toLowerCase()}}">${{t.mode}}</span></td>
                <td>${{t.entry_price}}¢</td>
                <td>${{t.contracts}}</td>
                <td>$${{t.cost}}</td>
                <td>${{t.edge}}%</td>
                <td>${{t.bet_pct}}%</td>
                <td><span class="badge ${{t.outcome.toLowerCase()}}">${{t.outcome}}</span></td>
                <td class="${{t.profit >= 0 ? 'win' : 'loss'}}">$${{t.profit}}</td>
                <td>$${{t.bankroll_after.toLocaleString()}}</td>
            `;
            tbody.appendChild(row);
        }});
    </script>
</body>
</html>'''

    return html


if __name__ == "__main__":
    # Load data
    s2_path = "/Users/corbandamukaitis/Downloads/s2_dynamic_scaled_wait5_trades.csv"
    s3_path = "/Users/corbandamukaitis/Downloads/s3_sentiment_odds80_wait10_trades (3).csv"

    df_s2 = pd.read_csv(s2_path)
    df_s3 = pd.read_csv(s3_path)
    df_s3 = df_s3[(df_s3['Entry Price'] >= 80) & (df_s3['Entry Price'] <= 92)]

    print("Running detailed backtest...")
    results = run_detailed_backtest(df_s2, df_s3, starting_bankroll=1000)

    print(f"\nResults:")
    print(f"  Period: {results['first_trade']} to {results['last_trade']} ({results['days']} days)")
    print(f"  Profit: ${results['profit']:,}")
    print(f"  ROI: {results['roi']}%")
    print(f"  Daily ROI: {results['daily_roi']}%")

    print("\nGenerating HTML presentation...")
    html = generate_html(results)

    output_path = "/Users/corbandamukaitis/Desktop/Personal Projects/15 minute trade strategy/kalshi-official-trader-corbeezy-the-king/backtest_analysis/presentation/ATTACK_RECOVER_STRATEGY.html"
    with open(output_path, 'w') as f:
        f.write(html)

    print(f"\n✅ Presentation saved to: {output_path}")
    print(f"\nOpen in browser: file://{output_path}")
