"""
Directional Stop-Loss Strategy - Market Scanner
Scans for 15-minute trading windows and applies direction filter.

Strategy Rules:
1. Wait until 5 minutes remaining (10 min elapsed)
2. Check BTC direction: Above strike = YES, Below strike = NO
3. Only enter if favored side ask is 60-85c
"""

import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .kalshi_client import KalshiClient, MarketData
from .kraken import KrakenClient


@dataclass
class TradingOpportunity:
    """A trading opportunity that meets our directional criteria."""
    ticker: str
    side: str  # "yes" or "no" - determined by BTC direction
    entry_price: int  # cents (ask price for the side)
    close_time: datetime
    minutes_remaining: float
    net_profit_per_contract: float  # dollars
    return_percentage: float
    floor_strike: float  # BTC strike price
    btc_price: float  # Current BTC price when opportunity was found
    btc_direction: str  # "above" or "below"

    def __str__(self):
        return (
            f"{self.ticker} | {self.side.upper()} @ {self.entry_price}c | "
            f"BTC ${self.btc_price:,.0f} {self.btc_direction} ${self.floor_strike:,.0f} | "
            f"{self.minutes_remaining:.1f}m left | "
            f"+{self.net_profit_per_contract*100:.0f}c ({self.return_percentage:.1f}%)"
        )


@dataclass
class OrderBookSnapshot:
    """Snapshot of order book for analysis."""
    ticker: str
    timestamp: datetime
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    btc_price: float
    floor_strike: float
    btc_direction: str
    intended_side: Optional[str] = None
    intended_price: Optional[int] = None
    actual_fill_price: Optional[int] = None


class MarketScanner:
    """
    Directional Stop-Loss Strategy Scanner.

    Rules:
    1. Wait 10 minutes into window (5 minutes remaining)
    2. Get current BTC price from Kraken
    3. Determine direction: BTC > Strike = bet YES, BTC < Strike = bet NO
    4. Only enter if favored side ask is 60-85c
    """

    # 15-minute BTC market series
    CRYPTO_15M_SERIES = ["KXBTC15M"]

    def __init__(
        self,
        client: KalshiClient,
        min_price: int = 60,
        max_price: int = 85,
        wait_minutes: int = 10,
        data_dir: Path = None,
    ):
        self.client = client
        self.min_price = min_price
        self.max_price = max_price
        self.wait_minutes = wait_minutes
        self.data_dir = data_dir or Path("./data")
        self.order_book_log: list[OrderBookSnapshot] = []

    @staticmethod
    def calc_fee(price_cents: int) -> float:
        """
        Calculate Kalshi fee per contract.
        Formula: ceil(0.07 * price * (1 - price))
        """
        price = price_cents / 100
        fee = 0.07 * price * (1 - price)
        return max(0.01, round(fee + 0.005, 2))

    @staticmethod
    def calc_net_profit(entry_price_cents: int) -> float:
        """Calculate net profit per contract after fees (in dollars)."""
        price = entry_price_cents / 100
        gross_profit = 1.0 - price
        fee = MarketScanner.calc_fee(entry_price_cents)
        return gross_profit - fee

    @staticmethod
    def calc_return_pct(entry_price_cents: int) -> float:
        """Calculate return percentage after fees."""
        net_profit = MarketScanner.calc_net_profit(entry_price_cents)
        return (net_profit / (entry_price_cents / 100)) * 100

    def is_valid_entry(self, price: int) -> bool:
        """Check if price is in valid entry range (60-85c)."""
        return self.min_price <= price <= self.max_price

    def parse_close_time(self, close_time_str: str) -> datetime:
        """Parse ISO format close time."""
        if close_time_str.endswith("Z"):
            close_time_str = close_time_str[:-1] + "+00:00"
        return datetime.fromisoformat(close_time_str)

    def get_minutes_remaining(self, close_time: datetime) -> float:
        """Get minutes remaining until market closes."""
        now = datetime.now(timezone.utc)
        delta = close_time - now
        return delta.total_seconds() / 60

    def get_btc_direction(self, floor_strike: float) -> tuple[str, float]:
        """
        Get BTC direction relative to strike.

        Returns:
            (direction, btc_price) where direction is "above" or "below"
            Returns (None, None) if BTC price unavailable
        """
        btc_price = KrakenClient.get_btc_price()

        if btc_price is None or btc_price == 0:
            return None, None

        if btc_price > floor_strike:
            return "above", btc_price
        else:
            return "below", btc_price

    def scan_market(self, market: MarketData) -> Optional[TradingOpportunity]:
        """
        Analyze a single market for trading opportunity.

        Directional Stop-Loss Rules:
        1. Must be within last 5 minutes (waited 10 minutes)
        2. Check BTC direction relative to strike
        3. Bet YES if above, NO if below
        4. Only enter if favored side ask is 60-85c

        Returns:
            TradingOpportunity if valid entry found, None otherwise
        """
        if market.status not in ("open", "active"):
            return None

        # Check if strike is valid (not zero)
        if market.floor_strike == 0:
            return None

        close_time = self.parse_close_time(market.close_time)
        minutes_remaining = self.get_minutes_remaining(close_time)

        # Rule 1: Must be within last 5 minutes (waited 10 minutes)
        if minutes_remaining > 5 or minutes_remaining < 0.5:
            return None

        # Rule 2: Get BTC direction
        btc_direction, btc_price = self.get_btc_direction(market.floor_strike)

        if btc_direction is None:
            return None  # Couldn't get BTC price

        # Rule 3: Determine side based on direction
        # BTC > Strike = ABOVE = bet YES
        # BTC < Strike = BELOW = bet NO
        if btc_direction == "above":
            side = "yes"
            entry_price = market.yes_ask
        else:
            side = "no"
            entry_price = market.no_ask

        # Record order book snapshot
        snapshot = OrderBookSnapshot(
            ticker=market.ticker,
            timestamp=datetime.now(timezone.utc),
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            no_bid=market.no_bid,
            no_ask=market.no_ask,
            btc_price=btc_price,
            floor_strike=market.floor_strike,
            btc_direction=btc_direction,
            intended_side=side,
            intended_price=entry_price,
        )
        self.order_book_log.append(snapshot)

        # Rule 4: Only enter if favored side ask is 60-85c
        if not self.is_valid_entry(entry_price):
            return None

        return TradingOpportunity(
            ticker=market.ticker,
            side=side,
            entry_price=entry_price,
            close_time=close_time,
            minutes_remaining=minutes_remaining,
            net_profit_per_contract=self.calc_net_profit(entry_price),
            return_percentage=self.calc_return_pct(entry_price),
            floor_strike=market.floor_strike,
            btc_price=btc_price,
            btc_direction=btc_direction,
        )

    def scan_all_markets(self) -> list[TradingOpportunity]:
        """
        Scan 15-minute crypto markets for opportunities.

        Returns:
            List of valid trading opportunities
        """
        opportunities = []

        for series in self.CRYPTO_15M_SERIES:
            try:
                response = self.client.get_markets(
                    status="open",
                    series_ticker=series,
                    limit=50
                )
                markets = response.get("markets", [])

                for market_data in markets:
                    market = MarketData.from_api(market_data)
                    opp = self.scan_market(market)
                    if opp:
                        opportunities.append(opp)

            except Exception as e:
                print(f"Error scanning {series}: {e}")

        return opportunities

    def get_all_crypto_markets(self) -> list[MarketData]:
        """
        Get all 15-minute crypto markets (for dashboard display).

        Returns:
            List of MarketData for all 15M crypto markets
        """
        all_markets = []

        for series in self.CRYPTO_15M_SERIES:
            try:
                response = self.client.get_markets(
                    status="open",
                    series_ticker=series,
                    limit=200
                )
                for market_data in response.get("markets", []):
                    all_markets.append(MarketData.from_api(market_data))
            except Exception as e:
                print(f"Error fetching {series}: {e}")

        return all_markets

    def find_best_opportunity(self) -> Optional[TradingOpportunity]:
        """
        Find the best current opportunity.
        Prefers: optimal price range (65-80c) > higher return > more time
        """
        opportunities = self.scan_all_markets()

        if not opportunities:
            return None

        # Sort by: optimal range first, then by return percentage
        def score(opp: TradingOpportunity) -> tuple:
            in_optimal = 65 <= opp.entry_price <= 80
            return (in_optimal, opp.return_percentage, opp.minutes_remaining)

        opportunities.sort(key=score, reverse=True)
        return opportunities[0]

    def watch_for_entry(
        self,
        tickers: list[str] = None,
        poll_interval: float = 1.0,
        timeout_seconds: float = 300,
    ) -> Optional[TradingOpportunity]:
        """
        Watch specific markets until one hits our entry criteria.

        Args:
            tickers: Specific tickers to watch (or all if None)
            poll_interval: Seconds between checks
            timeout_seconds: Max time to watch

        Returns:
            First opportunity that meets criteria
        """
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            if tickers:
                for ticker in tickers:
                    try:
                        market = self.client.get_market(ticker)
                        opp = self.scan_market(market)
                        if opp:
                            return opp
                    except Exception as e:
                        print(f"Error checking {ticker}: {e}")
            else:
                opp = self.find_best_opportunity()
                if opp:
                    return opp

            time.sleep(poll_interval)

        return None

    def save_order_book_log(self, filename: str = None):
        """Save order book snapshots to JSON for analysis."""
        if not filename:
            filename = f"orderbook_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        filepath = self.data_dir / filename
        data = [
            {
                "ticker": s.ticker,
                "timestamp": s.timestamp.isoformat(),
                "yes_bid": s.yes_bid,
                "yes_ask": s.yes_ask,
                "no_bid": s.no_bid,
                "no_ask": s.no_ask,
                "btc_price": s.btc_price,
                "floor_strike": s.floor_strike,
                "btc_direction": s.btc_direction,
                "intended_side": s.intended_side,
                "intended_price": s.intended_price,
                "actual_fill_price": s.actual_fill_price,
            }
            for s in self.order_book_log
        ]

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        return filepath
