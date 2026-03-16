"""
Kraken API for BTC price data.
Used for instant settlement determination instead of waiting for Kalshi API.
"""

import requests
from typing import Optional


class KrakenClient:
    """Simple client to fetch BTC price from Kraken."""

    BASE_URL = "https://api.kraken.com/0/public"

    @staticmethod
    def get_btc_price() -> Optional[float]:
        """
        Get current BTC/USD price from Kraken.

        Returns:
            Current BTC price in USD, or None if error
        """
        try:
            response = requests.get(
                f"{KrakenClient.BASE_URL}/Ticker",
                params={"pair": "XBTUSD"},
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if data.get("error"):
                print(f"Kraken API error: {data['error']}")
                return None

            result = data.get("result", {})
            ticker = result.get("XXBTZUSD", {})

            # 'c' is the last trade closed [price, lot volume]
            last_price = ticker.get("c", [None])[0]

            if last_price:
                return float(last_price)
            return None

        except Exception as e:
            print(f"Error fetching Kraken price: {e}")
            return None

    @staticmethod
    def get_btc_distance_from_strike(
        floor_strike: float,
        max_retries: int = 3,
        retry_delay: float = 0.5,
    ) -> tuple[Optional[float], Optional[str], Optional[float]]:
        """
        Get BTC distance from strike price with retry logic for zero values.

        Args:
            floor_strike: The strike price to compare against
            max_retries: Number of times to retry if we get zero/invalid price
            retry_delay: Seconds to wait between retries

        Returns:
            (distance_pct, direction, btc_price) where:
            - distance_pct: Absolute percentage distance from strike
            - direction: 'above' if BTC > strike, 'below' if BTC < strike
            - btc_price: The actual BTC price retrieved
            Returns (None, None, None) if couldn't get valid price after retries
        """
        import time

        for attempt in range(max_retries):
            btc_price = KrakenClient.get_btc_price()

            # Skip invalid prices (None, 0, or unreasonable values)
            if btc_price is None or btc_price == 0 or btc_price < 1000:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                continue

            # Calculate distance from strike
            distance = btc_price - floor_strike
            distance_pct = abs(distance / floor_strike) * 100
            direction = 'above' if distance > 0 else 'below'

            return distance_pct, direction, btc_price

        return None, None, None

    @staticmethod
    def passes_distance_filter(
        floor_strike: float,
        side: str,
        min_distance_pct: float = 0.15,
    ) -> tuple[bool, Optional[str], Optional[float]]:
        """
        Check if BTC price passes the distance filter for a given side.

        For YES bets: BTC must be >= min_distance_pct ABOVE strike
        For NO bets: BTC must be >= min_distance_pct BELOW strike

        Args:
            floor_strike: The strike price
            side: "yes" or "no"
            min_distance_pct: Minimum required distance (default 0.15%)

        Returns:
            (passes, reason, btc_price) where:
            - passes: True if filter passes, False otherwise
            - reason: Human-readable explanation
            - btc_price: The BTC price used for check
        """
        distance_pct, direction, btc_price = KrakenClient.get_btc_distance_from_strike(floor_strike)

        if distance_pct is None:
            return False, "Could not get valid BTC price from Kraken", None

        # Check if distance is sufficient
        if distance_pct < min_distance_pct:
            return False, f"BTC only {distance_pct:.3f}% from strike (need {min_distance_pct}%)", btc_price

        # Check if direction matches the bet side
        if side == "yes" and direction != "above":
            return False, f"BTC is {direction} strike but betting YES", btc_price
        if side == "no" and direction != "below":
            return False, f"BTC is {direction} strike but betting NO", btc_price

        return True, f"BTC {direction} strike by {distance_pct:.3f}% - FILTER PASSED", btc_price

    @staticmethod
    def determine_settlement(
        floor_strike: float,
        side: str,
    ) -> Optional[bool]:
        """
        Determine if a trade won based on current BTC price.

        Args:
            floor_strike: The strike price (price to beat)
            side: "yes" or "no"

        Returns:
            True if won, False if lost, None if couldn't get price
        """
        btc_price = KrakenClient.get_btc_price()

        if btc_price is None:
            return None

        # YES wins if BTC >= strike
        # NO wins if BTC < strike
        if side == "yes":
            won = btc_price >= floor_strike
        else:
            won = btc_price < floor_strike

        return won
