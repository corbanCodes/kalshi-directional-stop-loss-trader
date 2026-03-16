#!/usr/bin/env python3
"""
Directional Stop-Loss Strategy - Trading Bot
15-Minute BTC Prediction Markets

Strategy:
- Wait 10 minutes (5 min remaining)
- BTC > Strike = YES, BTC < Strike = NO
- Only enter at 60-85c
- 5% of bankroll per bet
- Exit if bid drops to 50c

Dashboard on PORT (default 8081)
"""

import sys
import os
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from urllib.parse import parse_qs

# Dashboard authentication
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "")

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import Trader, load_config, KalshiClient, MarketScanner, BetCalculator
from src.kraken import KrakenClient

# Global trader reference
GLOBAL_TRADER = None

# Dashboard state
DASHBOARD_STATE = {
    "status": "stopped",
    "trading_enabled": False,
    "bankroll": 0,
    "starting_bankroll": None,
    "effective_bankroll": 0,
    "auto_compound": True,
    "stop_loss_enabled": True,
    "today_profit": 0,
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "stopped_out": 0,
    "last_trade": None,
    "last_update": None,
    "recent_trades": [],
    "error": None,
    "activity_log": [],
    "current_market": None,
    "market_prices": {},
    "btc_price": 0,
    "pending_trade": None,
}


def log_activity(msg):
    """Add message to activity log."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    DASHBOARD_STATE["activity_log"].append(f"{timestamp} {msg}")
    DASHBOARD_STATE["activity_log"] = DASHBOARD_STATE["activity_log"][-20:]
    print(f"{timestamp} {msg}")


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard."""

    def log_message(self, format, *args):
        pass

    def check_auth(self):
        if not DASHBOARD_PASS:
            return True
        import base64
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return False
        try:
            encoded = auth_header[6:]
            decoded = base64.b64decode(encoded).decode("utf-8")
            _, password = decoded.split(":", 1)
            return password == DASHBOARD_PASS
        except:
            return False

    def send_auth_required(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Dashboard"')
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Authentication Required</h1>")

    def do_GET(self):
        if self.path == "/health":
            self.send_json({"status": "ok"})
            return

        if not self.check_auth():
            self.send_auth_required()
            return

        if self.path == "/" or self.path == "/dashboard":
            self.send_dashboard()
        elif self.path == "/api/status":
            self.send_json(DASHBOARD_STATE)
        elif self.path == "/api/export":
            self.send_csv_export()
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.check_auth():
            self.send_auth_required()
            return

        if self.path == "/api/start":
            DASHBOARD_STATE["trading_enabled"] = True
            DASHBOARD_STATE["status"] = "running"
            DASHBOARD_STATE["error"] = None
            log_activity("Trading STARTED")
            self.send_json({"success": True})
        elif self.path == "/api/stop":
            DASHBOARD_STATE["trading_enabled"] = False
            DASHBOARD_STATE["status"] = "stopped"
            log_activity("Trading STOPPED")
            self.send_json({"success": True})
        elif self.path == "/api/set-bankroll":
            self.handle_set_bankroll()
        elif self.path == "/api/reset-stats":
            self.handle_reset_stats()
        else:
            self.send_error(404)

    def handle_reset_stats(self):
        """Reset session stats (P&L, wins, losses, trades)."""
        global GLOBAL_TRADER

        # Reset dashboard state
        DASHBOARD_STATE["today_profit"] = 0
        DASHBOARD_STATE["total_trades"] = 0
        DASHBOARD_STATE["wins"] = 0
        DASHBOARD_STATE["losses"] = 0
        DASHBOARD_STATE["stopped_out"] = 0
        DASHBOARD_STATE["recent_trades"] = []

        # Reset trader state if available
        if GLOBAL_TRADER:
            GLOBAL_TRADER.state.total_profit = 0
            GLOBAL_TRADER.state.total_trades = 0
            GLOBAL_TRADER.state.total_wins = 0
            GLOBAL_TRADER.state.total_losses = 0
            GLOBAL_TRADER.state.total_stopped = 0
            GLOBAL_TRADER.trade_history = []
            GLOBAL_TRADER._traded_tickers.clear()

        log_activity("Stats RESET")
        self.send_json({"success": True})

    def handle_set_bankroll(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body) if body else {}

            amount = data.get('amount')
            auto_compound = data.get('auto_compound', True)
            stop_loss_enabled = data.get('stop_loss_enabled', True)

            if amount and amount > 0:
                DASHBOARD_STATE["starting_bankroll"] = amount
                DASHBOARD_STATE["effective_bankroll"] = amount
                DASHBOARD_STATE["auto_compound"] = auto_compound
                DASHBOARD_STATE["stop_loss_enabled"] = stop_loss_enabled

                # Calculate bet info
                calc = BetCalculator(bet_percentage=0.05, stop_loss_price=50)
                bet = calc.calculate_bet(amount, 70)

                sl_status = "ON" if stop_loss_enabled else "OFF"
                log_activity(f"Bankroll set to ${amount:.2f} (Stop Loss: {sl_status})")

                self.send_json({
                    "success": True,
                    "starting_bankroll": amount,
                    "contracts_at_70c": bet.contracts if bet else 0,
                    "profit_per_win": bet.net_profit_if_win if bet else 0,
                    "max_loss_with_stop": bet.max_loss_with_stop if bet else 0,
                })
            else:
                self.send_json({"success": False, "error": "Invalid amount"})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)})

    def send_csv_export(self):
        import csv
        import io

        output = io.StringIO()
        fieldnames = ["timestamp", "ticker", "side", "contracts", "entry", "fill", "exit", "profit", "stopped"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for t in DASHBOARD_STATE.get("recent_trades", []):
            writer.writerow({
                "timestamp": t.get("time", ""),
                "ticker": t.get("ticker", ""),
                "side": t.get("side", ""),
                "contracts": t.get("contracts", 0),
                "entry": t.get("entry_price", 0),
                "fill": t.get("fill_price", 0),
                "exit": t.get("exit_price", ""),
                "profit": t.get("profit", 0),
                "stopped": t.get("stopped_out", False),
            })

        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", f"attachment; filename=trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        self.end_headers()
        self.wfile.write(output.getvalue().encode())

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_dashboard(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <title>BTC 15-Min Strategy</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0f0f0f;
            --card: #1a1a1a;
            --card-hover: #222;
            --border: #2a2a2a;
            --text: #fff;
            --muted: #888;
            --green: #00d26a;
            --green-glow: rgba(0,210,106,0.3);
            --red: #ff4757;
            --red-glow: rgba(255,71,87,0.3);
            --yellow: #ffa502;
            --blue: #00d4ff;
            --purple: #a855f7;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 30px; }

        /* Hero Section - Bankroll Setup */
        .hero {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f0f0f 100%);
            border: 1px solid var(--border);
            border-radius: 24px;
            padding: 40px;
            margin-bottom: 30px;
            position: relative;
            overflow: hidden;
        }
        .hero::before {
            content: '';
            position: absolute;
            top: -50%;
            right: -20%;
            width: 400px;
            height: 400px;
            background: radial-gradient(circle, var(--green-glow) 0%, transparent 70%);
            pointer-events: none;
        }
        .hero-title {
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(135deg, var(--green) 0%, var(--blue) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .hero-subtitle { color: var(--muted); margin-bottom: 30px; }

        .bankroll-setup {
            display: grid;
            grid-template-columns: 300px 1fr auto;
            gap: 20px;
            align-items: end;
            position: relative;
            z-index: 1;
        }
        .input-group { position: relative; }
        .input-group label {
            display: block;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--muted);
            margin-bottom: 8px;
        }
        .input-group input {
            width: 100%;
            padding: 16px 20px;
            padding-left: 35px;
            font-size: 1.5rem;
            font-weight: 600;
            background: rgba(255,255,255,0.05);
            border: 2px solid var(--border);
            border-radius: 12px;
            color: var(--text);
            font-family: inherit;
            transition: all 0.2s;
        }
        .input-group input:focus {
            outline: none;
            border-color: var(--green);
            box-shadow: 0 0 0 4px var(--green-glow);
        }
        .input-group .dollar-sign {
            position: absolute;
            left: 16px;
            bottom: 18px;
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--muted);
        }

        .bet-preview {
            background: rgba(0,0,0,0.3);
            border-radius: 12px;
            padding: 20px;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            text-align: center;
        }
        .bet-preview-item { }
        .bet-preview-value {
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--green);
        }
        .bet-preview-value.loss { color: var(--red); }
        .bet-preview-label {
            font-size: 0.7rem;
            text-transform: uppercase;
            color: var(--muted);
            margin-top: 4px;
        }

        .btn-activate {
            padding: 18px 40px;
            font-size: 1rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            background: linear-gradient(135deg, var(--green) 0%, #00b359 100%);
            border: none;
            border-radius: 12px;
            color: #000;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        .btn-activate:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 30px var(--green-glow);
        }
        .btn-activate:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }
        .btn-activate.active {
            background: linear-gradient(135deg, var(--red) 0%, #cc3d4a 100%);
        }

        .compound-toggle {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 20px;
            font-size: 0.9rem;
            color: var(--muted);
        }
        .compound-toggle input[type="checkbox"] {
            width: 20px;
            height: 20px;
            accent-color: var(--green);
        }

        /* Status Bar */
        .status-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px 30px;
            margin-bottom: 30px;
        }
        .status-item {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: var(--muted);
        }
        .status-dot.active {
            background: var(--green);
            box-shadow: 0 0 20px var(--green-glow);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .status-text { font-weight: 600; }
        .btc-price {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--yellow);
        }

        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 25px;
            text-align: center;
            transition: all 0.2s;
        }
        .stat-card:hover {
            background: var(--card-hover);
            transform: translateY(-2px);
        }
        .stat-value {
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--green) 0%, var(--blue) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .stat-value.negative {
            background: linear-gradient(135deg, var(--red) 0%, #ff6b6b 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .stat-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--muted);
            margin-top: 8px;
        }

        /* Strategy Rules */
        .rules-bar {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin-bottom: 30px;
        }
        .rule-item {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 15px;
            text-align: center;
        }
        .rule-value {
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--purple);
        }
        .rule-label {
            font-size: 0.7rem;
            text-transform: uppercase;
            color: var(--muted);
            margin-top: 4px;
        }

        /* Two Column Layout */
        .two-col {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }
        .section {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 25px;
        }
        .section-title {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--muted);
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid var(--border);
        }

        /* Markets */
        .market-list { max-height: 250px; overflow-y: auto; }
        .market-row {
            display: grid;
            grid-template-columns: 2fr 1fr 1fr 1fr 1fr;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
            font-size: 0.85rem;
            align-items: center;
        }
        .market-strike { color: var(--muted); font-size: 0.8rem; }
        .market-row:last-child { border-bottom: none; }
        .market-ticker { font-weight: 600; color: var(--text); }
        .market-time { color: var(--yellow); font-weight: 500; }
        .market-price { font-family: monospace; }
        .market-price.yes { color: var(--blue); }
        .market-price.no { color: var(--red); }
        .market-price.hot {
            background: var(--green);
            color: #000;
            padding: 4px 8px;
            border-radius: 6px;
            font-weight: 600;
        }

        /* Trades */
        .trades-list { max-height: 300px; overflow-y: auto; }
        .trade-row {
            display: grid;
            grid-template-columns: 100px 1fr 80px 100px;
            padding: 15px;
            margin-bottom: 10px;
            background: rgba(255,255,255,0.02);
            border-radius: 10px;
            align-items: center;
            font-size: 0.85rem;
        }
        .trade-row.win { border-left: 3px solid var(--green); }
        .trade-row.loss { border-left: 3px solid var(--red); }
        .trade-row.stopped { border-left: 3px solid var(--yellow); }
        .trade-time { color: var(--muted); }
        .trade-details { font-weight: 500; }
        .trade-status {
            font-size: 0.7rem;
            text-transform: uppercase;
            font-weight: 600;
            padding: 4px 8px;
            border-radius: 4px;
        }
        .trade-status.win { background: rgba(0,210,106,0.2); color: var(--green); }
        .trade-status.loss { background: rgba(255,71,87,0.2); color: var(--red); }
        .trade-status.stopped { background: rgba(255,165,2,0.2); color: var(--yellow); }
        .trade-profit { font-weight: 700; text-align: right; }
        .trade-profit.positive { color: var(--green); }
        .trade-profit.negative { color: var(--red); }

        /* Activity Log */
        .activity-log {
            max-height: 250px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.8rem;
            line-height: 2;
            color: var(--muted);
        }

        /* Footer */
        .footer {
            text-align: center;
            color: var(--muted);
            font-size: 0.75rem;
            padding: 20px;
        }

        @media (max-width: 1024px) {
            .bankroll-setup { grid-template-columns: 1fr; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .rules-bar { grid-template-columns: repeat(2, 1fr); }
            .two-col { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Hero: Bankroll Setup -->
        <div class="hero">
            <h1 class="hero-title">Directional Stop-Loss</h1>
            <p class="hero-subtitle">15-Minute BTC Prediction Markets</p>

            <div class="bankroll-setup">
                <div class="input-group">
                    <label>Trading Bankroll</label>
                    <span class="dollar-sign">$</span>
                    <input type="number" id="bankrollInput" placeholder="100" step="1" value="100">
                </div>

                <div class="bet-preview" id="betPreview">
                    <div class="bet-preview-item">
                        <div class="bet-preview-value" id="previewContracts">7</div>
                        <div class="bet-preview-label">Contracts @ 70c</div>
                    </div>
                    <div class="bet-preview-item">
                        <div class="bet-preview-value" id="previewWin">+$1.89</div>
                        <div class="bet-preview-label">If Win</div>
                    </div>
                    <div class="bet-preview-item">
                        <div class="bet-preview-value loss" id="previewStop">-$1.40</div>
                        <div class="bet-preview-label">If Stopped</div>
                    </div>
                </div>

                <button class="btn-activate" id="activateBtn" onclick="toggleTrading()">
                    ACTIVATE
                </button>
            </div>

            <div class="compound-toggle">
                <input type="checkbox" id="autoCompound" checked>
                <label for="autoCompound">Auto-compound wins (grow bankroll after each win)</label>
            </div>
            <div class="compound-toggle" style="margin-top:8px;">
                <input type="checkbox" id="stopLossEnabled" checked>
                <label for="stopLossEnabled">Stop loss at 50c <span style="color:var(--green);font-size:0.85em;">(RECOMMENDED)</span></label>
            </div>
        </div>

        <!-- Status Bar -->
        <div class="status-bar">
            <div class="status-item">
                <div class="status-dot" id="statusDot"></div>
                <span class="status-text" id="statusText">STOPPED</span>
            </div>
            <div class="status-item">
                <span style="color:var(--muted)">BTC</span>
                <span class="btc-price" id="btcPrice">$0</span>
            </div>
            <div class="status-item" id="directionIndicator" style="display:none;">
                <span id="directionText" style="font-weight:700;padding:6px 12px;border-radius:6px;">--</span>
            </div>
            <div class="status-item">
                <span style="color:var(--muted)">Kalshi:</span>
                <span style="font-weight:600" id="kalshiBalance">$0.00</span>
            </div>
        </div>

        <!-- Stats -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value" id="effectiveBankroll">$100.00</div>
                <div class="stat-label">Active Bankroll</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="sessionPnL">$0.00</div>
                <div class="stat-label">Session P&L</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="winRate">0%</div>
                <div class="stat-label">Win Rate</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="tradeCount">0</div>
                <div class="stat-label">Trades</div>
            </div>
        </div>
        <div style="text-align:center;margin-bottom:15px;">
            <button onclick="resetStats()" style="background:transparent;border:1px solid #444;color:#888;padding:6px 16px;border-radius:6px;cursor:pointer;font-size:0.85em;">Reset Stats</button>
        </div>

        <!-- Strategy Rules -->
        <div class="rules-bar">
            <div class="rule-item">
                <div class="rule-value">60-85c</div>
                <div class="rule-label">Entry Range</div>
            </div>
            <div class="rule-item">
                <div class="rule-value">50c</div>
                <div class="rule-label">Stop Loss</div>
            </div>
            <div class="rule-item">
                <div class="rule-value">5%</div>
                <div class="rule-label">Bet Size</div>
            </div>
            <div class="rule-item">
                <div class="rule-value">5 min</div>
                <div class="rule-label">Entry Window</div>
            </div>
        </div>

        <!-- Two Column -->
        <div class="two-col">
            <div class="section">
                <div class="section-title">Live Markets</div>
                <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr;padding:8px 0;border-bottom:1px solid var(--border);font-size:0.7rem;color:var(--muted);text-transform:uppercase;">
                    <span>Ticker</span>
                    <span>Strike</span>
                    <span>Time</span>
                    <span>YES</span>
                    <span>NO</span>
                </div>
                <div class="market-list" id="marketList">Loading markets...</div>
            </div>
            <div class="section">
                <div class="section-title">Activity Log</div>
                <div class="activity-log" id="activityLog">Waiting for activity...</div>
            </div>
        </div>

        <!-- Trades -->
        <div class="section">
            <div class="section-title">Recent Trades</div>
            <div class="trades-list" id="tradesList">No trades yet</div>
        </div>

        <div class="footer">
            <a href="/api/export" style="color:var(--blue);margin-right:20px;">Export Trades CSV</a>
            Last updated: <span id="lastUpdate">--</span>
        </div>
    </div>

    <script>
        // Calculate bet preview on input
        document.getElementById('bankrollInput').addEventListener('input', updateBetPreview);

        function updateBetPreview() {
            const bankroll = parseFloat(document.getElementById('bankrollInput').value) || 0;
            const betAmount = bankroll * 0.05;
            const contracts = Math.floor(betAmount / 0.70);
            const winProfit = contracts * 0.27; // 30c - 3c fee
            const stopLoss = contracts * 0.20; // entry - 50c

            document.getElementById('previewContracts').textContent = contracts;
            document.getElementById('previewWin').textContent = '+$' + winProfit.toFixed(2);
            document.getElementById('previewStop').textContent = '-$' + stopLoss.toFixed(2);
        }

        function resetStats() {
            if (confirm('Reset P&L, win rate, and trade history?')) {
                fetch('/api/reset-stats', {method: 'POST'}).then(() => updateUI());
            }
        }

        function toggleTrading() {
            const btn = document.getElementById('activateBtn');
            const isActive = btn.classList.contains('active');

            if (!isActive) {
                // Starting - save bankroll first
                const amount = parseFloat(document.getElementById('bankrollInput').value);
                const autoCompound = document.getElementById('autoCompound').checked;
                const stopLossEnabled = document.getElementById('stopLossEnabled').checked;

                if (!amount || amount <= 0) {
                    alert('Enter a valid bankroll amount');
                    return;
                }

                // Save bankroll then start
                fetch('/api/set-bankroll', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({amount, auto_compound: autoCompound, stop_loss_enabled: stopLossEnabled})
                }).then(r => r.json()).then(d => {
                    if (d.success) {
                        fetch('/api/start', {method: 'POST'}).then(() => {
                            btn.textContent = 'STOP';
                            btn.classList.add('active');
                            updateUI();
                        });
                    }
                });
            } else {
                // Stopping
                fetch('/api/stop', {method: 'POST'}).then(() => {
                    btn.textContent = 'ACTIVATE';
                    btn.classList.remove('active');
                    updateUI();
                });
            }
        }

        function updateUI() {
            fetch('/api/status').then(r => r.json()).then(data => {
                // Status
                const isRunning = data.trading_enabled;
                document.getElementById('statusDot').className = 'status-dot' + (isRunning ? ' active' : '');
                document.getElementById('statusText').textContent = isRunning ? 'RUNNING' : 'STOPPED';

                const btn = document.getElementById('activateBtn');
                if (isRunning) {
                    btn.textContent = 'STOP';
                    btn.classList.add('active');
                } else {
                    btn.textContent = 'ACTIVATE';
                    btn.classList.remove('active');
                }

                // BTC
                document.getElementById('btcPrice').textContent = '$' + (data.btc_price || 0).toLocaleString();

                // Balances
                document.getElementById('kalshiBalance').textContent = '$' + (data.bankroll || 0).toFixed(2);

                const effective = data.effective_bankroll || data.starting_bankroll || 0;
                document.getElementById('effectiveBankroll').textContent = '$' + effective.toFixed(2);

                // P&L
                const pnl = data.today_profit || 0;
                const pnlEl = document.getElementById('sessionPnL');
                pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
                pnlEl.className = 'stat-value' + (pnl < 0 ? ' negative' : '');

                // Win rate
                const wins = data.wins || 0;
                const losses = data.losses || 0;
                const total = wins + losses;
                document.getElementById('winRate').textContent = total > 0 ? (wins/total*100).toFixed(0) + '%' : '0%';
                document.getElementById('tradeCount').textContent = total;

                // Markets
                const markets = data.market_prices || {};
                let mHtml = '';
                const btcPrice = data.btc_price || 0;
                let firstStrike = null;

                Object.entries(markets)
                    .sort((a,b) => parseFloat(a[1].mins_remaining) - parseFloat(b[1].mins_remaining))
                    .forEach(([ticker, m]) => {
                        if (!firstStrike && m.floor_strike) firstStrike = m.floor_strike;
                        const yesHot = m.yes_ask >= 60 && m.yes_ask <= 85;
                        const noHot = m.no_ask >= 60 && m.no_ask <= 85;
                        const strike = m.floor_strike ? '$' + m.floor_strike.toLocaleString() : '--';
                        const aboveBelow = btcPrice > m.floor_strike ? 'ABOVE' : 'BELOW';
                        const diff = btcPrice - m.floor_strike;
                        mHtml += '<div class="market-row">' +
                            '<span class="market-ticker">' + ticker.replace('KXBTC15M-', '') + '</span>' +
                            '<span class="market-strike">' + strike + '</span>' +
                            '<span class="market-time">' + m.mins_remaining + '</span>' +
                            '<span class="market-price yes' + (yesHot ? ' hot' : '') + '">Y ' + m.yes_ask + 'c</span>' +
                            '<span class="market-price no' + (noHot ? ' hot' : '') + '">N ' + m.no_ask + 'c</span>' +
                            '</div>';
                    });
                document.getElementById('marketList').innerHTML = mHtml || '<div style="color:var(--muted)">No markets available</div>';

                // Direction indicator
                if (firstStrike && btcPrice) {
                    const dirEl = document.getElementById('directionIndicator');
                    const dirText = document.getElementById('directionText');
                    dirEl.style.display = 'flex';
                    if (btcPrice > firstStrike) {
                        dirText.textContent = 'ABOVE Strike = BET YES';
                        dirText.style.background = 'rgba(0,210,106,0.2)';
                        dirText.style.color = 'var(--green)';
                    } else {
                        dirText.textContent = 'BELOW Strike = BET NO';
                        dirText.style.background = 'rgba(255,71,87,0.2)';
                        dirText.style.color = 'var(--red)';
                    }
                }

                // Activity
                const logs = data.activity_log || [];
                document.getElementById('activityLog').innerHTML = logs.slice(-15).join('<br>') || 'No activity yet';

                // Trades
                const trades = data.recent_trades || [];
                let tHtml = '';
                trades.slice(-10).reverse().forEach(t => {
                    const status = t.stopped_out ? 'stopped' : (t.profit >= 0 ? 'win' : 'loss');
                    const statusLabel = t.stopped_out ? 'STOPPED' : (t.profit >= 0 ? 'WIN' : 'LOSS');
                    tHtml += '<div class="trade-row ' + status + '">' +
                        '<span class="trade-time">' + (t.time || '') + '</span>' +
                        '<span class="trade-details">' + (t.side || '').toUpperCase() + ' @ ' + (t.fill_price || '?') + 'c</span>' +
                        '<span class="trade-status ' + status + '">' + statusLabel + '</span>' +
                        '<span class="trade-profit ' + (t.profit >= 0 ? 'positive' : 'negative') + '">' +
                        (t.profit >= 0 ? '+' : '') + '$' + (t.profit || 0).toFixed(2) + '</span>' +
                        '</div>';
                });
                document.getElementById('tradesList').innerHTML = tHtml || '<div style="color:var(--muted);padding:20px;text-align:center;">No trades yet</div>';

                // Update time
                document.getElementById('lastUpdate').textContent = data.last_update || '--';
            });
        }

        // Initial
        updateBetPreview();
        updateUI();
        setInterval(updateUI, 500);
    </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())


def run_web_server(port):
    """Run the web server."""
    server = HTTPServer(("", port), DashboardHandler)
    print(f"Dashboard: http://localhost:{port}")
    server.serve_forever()


def update_dashboard(trader):
    """Update dashboard state from trader."""
    starting = DASHBOARD_STATE.get("starting_bankroll")

    # Update basic stats from trader
    DASHBOARD_STATE.update({
        "bankroll": trader.state.bankroll,
        "today_profit": trader.state.total_profit,
        "total_trades": trader.state.total_trades,
        "wins": trader.state.total_wins,
        "losses": trader.state.total_losses,
        "stopped_out": trader.state.total_stopped,
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    # Handle effective bankroll sync
    if starting:
        # User set a starting bankroll - sync FROM dashboard TO trader (not vice versa)
        trader.effective_bankroll = DASHBOARD_STATE.get("effective_bankroll", starting)
    else:
        # No user bankroll set - use full Kalshi balance
        DASHBOARD_STATE["effective_bankroll"] = trader.state.bankroll

    # Update BTC price
    btc = KrakenClient.get_btc_price()
    if btc:
        DASHBOARD_STATE["btc_price"] = btc


def cmd_run():
    """Start live trading with dashboard."""
    port = int(os.environ.get("PORT", 8081))

    print("=" * 60)
    print("  DIRECTIONAL STOP-LOSS STRATEGY")
    print(f"  Dashboard: http://localhost:{port}")
    print("=" * 60)

    # Start web server
    web_thread = threading.Thread(target=run_web_server, args=(port,), daemon=True)
    web_thread.start()
    time.sleep(1)

    try:
        global GLOBAL_TRADER
        trader = Trader()
        GLOBAL_TRADER = trader

        DASHBOARD_STATE["bankroll"] = trader.state.bankroll
        DASHBOARD_STATE["effective_bankroll"] = trader.effective_bankroll
        DASHBOARD_STATE["status"] = "stopped"

        print(f"Bankroll: ${trader.state.bankroll:.2f}")
        print("Waiting for START command...")

        last_balance_check = 0

        while True:
            # Refresh balance
            if time.time() - last_balance_check > 1:
                trader.refresh_bankroll()
                last_balance_check = time.time()

            update_dashboard(trader)

            # Scan markets
            try:
                markets = trader.scanner.get_all_crypto_markets()
                DASHBOARD_STATE["market_prices"] = {}

                now = datetime.now(timezone.utc)
                for m in markets:
                    try:
                        close_time = trader.scanner.parse_close_time(m.close_time)
                        mins = (close_time - now).total_seconds() / 60
                        mins_str = f"{mins:.1f}m"
                    except:
                        mins_str = "N/A"

                    DASHBOARD_STATE["market_prices"][m.ticker] = {
                        "yes_bid": m.yes_bid,
                        "yes_ask": m.yes_ask,
                        "no_bid": m.no_bid,
                        "no_ask": m.no_ask,
                        "mins_remaining": mins_str,
                        "floor_strike": m.floor_strike,
                    }
            except Exception as e:
                log_activity(f"Scan error: {e}")

            # Check if trading enabled
            if not DASHBOARD_STATE["trading_enabled"]:
                DASHBOARD_STATE["status"] = "stopped"
                time.sleep(0.5)
                continue

            if not trader.can_trade():
                DASHBOARD_STATE["status"] = "paused"
                DASHBOARD_STATE["error"] = "Bankroll too low"
                time.sleep(5)
                continue

            DASHBOARD_STATE["status"] = "running"
            DASHBOARD_STATE["error"] = None

            # Sync stop loss setting from dashboard
            trader.stop_loss_enabled = DASHBOARD_STATE.get("stop_loss_enabled", True)

            # Run trading cycle
            profit_before = trader.state.total_profit
            result = trader.run_once()

            if result:
                trade_profit = trader.state.total_profit - profit_before

                trade_details = {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "ticker": result.ticker,
                    "side": result.side,
                    "contracts": result.contracts,
                    "entry_price": result.entry_price,
                    "fill_price": result.fill_price,
                    "exit_price": result.exit_price,
                    "profit": trade_profit,
                    "stopped_out": result.stopped_out,
                }

                log_activity(f"TRADE: {result.side.upper()} @ {result.fill_price}c -> ${trade_profit:+.2f}" +
                           (" [STOPPED]" if result.stopped_out else ""))
                DASHBOARD_STATE["recent_trades"].append(trade_details)

                # Auto-compound
                if trade_profit > 0 and DASHBOARD_STATE.get("auto_compound", True):
                    current = DASHBOARD_STATE.get("effective_bankroll", 0)
                    if current > 0:
                        DASHBOARD_STATE["effective_bankroll"] = current + trade_profit
                        log_activity(f"Compound: bankroll now ${DASHBOARD_STATE['effective_bankroll']:.2f}")

                update_dashboard(trader)
                time.sleep(1)
            else:
                time.sleep(0.3)

    except Exception as e:
        DASHBOARD_STATE["status"] = "error"
        DASHBOARD_STATE["error"] = str(e)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        while True:
            time.sleep(60)


def cmd_test():
    """Test API connection."""
    print("Testing connection...")

    try:
        config = load_config()
        client = KalshiClient(config.kalshi)

        status = client.get_exchange_status()
        print(f"Exchange: {status}")

        balance = client.get_balance_dollars()
        print(f"Balance: ${balance:.2f}")

        btc = KrakenClient.get_btc_price()
        print(f"BTC: ${btc:,.2f}")

        print("Connection OK!")
    except Exception as e:
        print(f"Failed: {e}")


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "run"

    if cmd == "run":
        cmd_run()
    elif cmd == "test":
        cmd_test()
    else:
        print(f"Usage: python main.py [run|test]")


if __name__ == "__main__":
    main()
