#!/usr/bin/env python3
"""
SETTLEMENT TIMING TEST
======================
Monitors how long it takes for Kalshi to settle markets and expose the 'result' field.

Run this alongside your bot to see:
1. When windows close
2. When Kalshi marks them as settled
3. What the official result is

Logs to: settlement_timing.log
"""

import requests
import time
import os
from datetime import datetime, timezone

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_TICKER = "KXBTC15M"

# Log file path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "settlement_timing.log")

def write_log(msg):
    """Write to both console and log file"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + "\n")

def api_get(endpoint, params=None):
    """Generic Kalshi API call"""
    try:
        resp = requests.get(f"{KALSHI_API}/{endpoint}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  API error: {e}")
        return None

def get_active_market():
    """Get the currently active BTC 15-minute market"""
    data = api_get("events", {"series_ticker": SERIES_TICKER, "status": "open"})
    if not data or not data.get('events'):
        return None
    event = data['events'][0]
    markets = api_get("markets", {"event_ticker": event['event_ticker']})
    if not markets or not markets.get('markets'):
        return None

    now = datetime.now(timezone.utc)

    def get_secs_left(m):
        close_str = m.get('close_time', '')
        if not close_str:
            return -9999
        try:
            close = datetime.fromisoformat(close_str.replace('Z', '+00:00'))
            return (close - now).total_seconds()
        except:
            return -9999

    # Get markets that are still open
    active_markets = [(m, get_secs_left(m)) for m in markets['markets']]
    active_markets = [(m, secs) for m, secs in active_markets if secs > 0]

    if not active_markets:
        return None

    active_markets.sort(key=lambda x: x[1])
    return active_markets[0][0]

def get_settled_markets():
    """Get recently settled markets with full details"""
    data = api_get("markets", {"series_ticker": SERIES_TICKER, "status": "settled", "limit": 5})
    return data.get('markets', []) if data else []

def get_market_by_ticker(ticker):
    """Get specific market details"""
    data = api_get(f"markets/{ticker}")
    return data.get('market') if data else None

def get_kraken_btc():
    """Get BTC price from Kraken"""
    try:
        resp = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": "XBTUSD"}, timeout=5)
        result = resp.json().get('result', {})
        for key, data in result.items():
            return float(data['c'][0])
    except:
        return None

def log(msg):
    write_log(msg)

def main():
    write_log("=" * 70)
    write_log("KALSHI SETTLEMENT TIMING TEST")
    write_log("=" * 70)
    write_log("Monitoring BTC 15-minute market settlements...")
    write_log("Will track: window close time -> settlement available time")
    write_log(f"Logging to: {LOG_FILE}")
    write_log("=" * 70)

    known_settled = set()
    pending_settlement = {}  # ticker -> close_time

    # Get existing settled markets (don't alert on historical)
    for m in get_settled_markets():
        ticker = m.get('ticker')
        if ticker:
            known_settled.add(ticker)
    log(f"Ignoring {len(known_settled)} historical settlements")

    last_ticker = None

    while True:
        try:
            now = datetime.now(timezone.utc)
            btc_price = get_kraken_btc()

            # Get current active market
            market = get_active_market()
            if market:
                ticker = market.get('ticker')
                close_str = market.get('close_time')
                strike = market.get('floor_strike', 0)

                if close_str:
                    close_time = datetime.fromisoformat(close_str.replace('Z', '+00:00'))
                    secs_left = (close_time - now).total_seconds()

                    # New window
                    if ticker != last_ticker:
                        log(f"ACTIVE: {ticker} | Strike: ${strike:,.2f} | Closes: {close_str}")
                        last_ticker = ticker

                        # Track this for settlement timing
                        if ticker not in known_settled:
                            pending_settlement[ticker] = {
                                'close_time': close_time,
                                'strike': strike,
                                'close_logged': False
                            }

                    # About to close
                    if secs_left <= 5 and ticker in pending_settlement:
                        if not pending_settlement[ticker].get('close_logged'):
                            log(f">>> WINDOW CLOSING: {ticker} in {secs_left:.1f}s | BTC: ${btc_price:,.2f} | Strike: ${strike:,.2f}")
                            pending_settlement[ticker]['close_logged'] = True
                            pending_settlement[ticker]['btc_at_close'] = btc_price

            # Check for NEW settlements
            settled = get_settled_markets()
            for m in settled:
                ticker = m.get('ticker')
                if ticker and ticker not in known_settled:
                    result = m.get('result')
                    strike = m.get('floor_strike', 0)

                    if result:
                        known_settled.add(ticker)
                        settlement_time = datetime.now(timezone.utc)

                        # Calculate delay from window close
                        if ticker in pending_settlement:
                            close_time = pending_settlement[ticker]['close_time']
                            delay_secs = (settlement_time - close_time).total_seconds()
                            btc_at_close = pending_settlement[ticker].get('btc_at_close', 'N/A')

                            outcome = "YES (UP)" if result == 'yes' else "NO (DOWN)"

                            log("=" * 60)
                            log(f"SETTLED: {ticker}")
                            log(f"  Result from Kalshi API: {result} -> {outcome}")
                            log(f"  Strike: ${strike:,.2f}")
                            log(f"  BTC at close: ${btc_at_close:,.2f}" if btc_at_close != 'N/A' else "  BTC at close: N/A")
                            log(f"  Settlement delay: {delay_secs:.1f} seconds after close")
                            log("=" * 60)

                            del pending_settlement[ticker]
                        else:
                            log(f"SETTLED: {ticker} -> {result} (no timing data)")

            # Check pending windows that should have settled by now
            for ticker, info in list(pending_settlement.items()):
                if info['close_time'] < now:
                    wait_secs = (now - info['close_time']).total_seconds()
                    if wait_secs > 60 and wait_secs % 30 < 2:  # Log every ~30 secs after 1 min
                        log(f"WAITING for settlement: {ticker} (closed {wait_secs:.0f}s ago)")

            time.sleep(2)

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            log(f"ERROR: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
