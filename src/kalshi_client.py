"""
Kalshi API client for trading operations.
"""

import json
import time
from typing import Any, Optional
from dataclasses import dataclass

import requests

from .auth import KalshiAuth
from .config import KalshiConfig


@dataclass
class OrderResponse:
    """Response from order placement."""
    order_id: str
    ticker: str
    side: str  # "yes" or "no"
    action: str  # "buy" or "sell"
    price: int  # cents
    count: int
    status: str
    filled_count: int = 0
    remaining_count: int = 0
    average_fill_price: int = 0

    @classmethod
    def from_api(cls, data: dict) -> "OrderResponse":
        order = data.get("order", data)
        # Kalshi API uses "fill_count", not "filled_count"
        fill_count = order.get("fill_count", order.get("filled_count", 0))
        return cls(
            order_id=order.get("order_id", ""),
            ticker=order.get("ticker", ""),
            side=order.get("side", ""),
            action=order.get("action", ""),
            price=order.get("yes_price", order.get("no_price", 0)),
            count=order.get("count", order.get("initial_count", 0)),
            status=order.get("status", ""),
            filled_count=fill_count,
            remaining_count=order.get("remaining_count", 0),
            average_fill_price=order.get("average_fill_price", 0),
        )


@dataclass
class MarketData:
    """Market data snapshot."""
    ticker: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    last_price: int
    volume_24h: int
    status: str
    close_time: str
    floor_strike: float = 0.0  # The BTC price to beat

    @property
    def yes_price(self) -> int:
        """Current YES price (ask for buying)."""
        return self.yes_ask

    @property
    def no_price(self) -> int:
        """Current NO price (ask for buying)."""
        return self.no_ask

    @classmethod
    def from_api(cls, data: dict) -> "MarketData":
        market = data.get("market", data)

        # Helper to convert dollar strings to cents
        def to_cents(val) -> int:
            if val is None:
                return 0
            if isinstance(val, (int, float)):
                return int(val * 100) if val < 10 else int(val)  # Handle both formats
            try:
                return int(float(val) * 100)
            except:
                return 0

        # New API uses *_dollars fields (strings), old used cents (ints)
        yes_bid = to_cents(market.get("yes_bid_dollars") or market.get("yes_bid", 0))
        yes_ask = to_cents(market.get("yes_ask_dollars") or market.get("yes_ask", 0))
        no_bid = to_cents(market.get("no_bid_dollars") or market.get("no_bid", 0))
        no_ask = to_cents(market.get("no_ask_dollars") or market.get("no_ask", 0))
        last_price = to_cents(market.get("last_price_dollars") or market.get("last_price", 0))

        return cls(
            ticker=market.get("ticker", ""),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            last_price=last_price,
            volume_24h=int(float(market.get("volume_24h_fp", market.get("volume_24h", "0")) or "0")),
            status=market.get("status", ""),
            close_time=market.get("close_time", ""),
            floor_strike=float(market.get("floor_strike", 0) or 0),
        )


class KalshiClient:
    """
    Client for interacting with Kalshi API.
    """

    def __init__(self, config: KalshiConfig):
        self.config = config
        self.auth = KalshiAuth(
            api_key_id=config.api_key_id,
            private_key_path=config.private_key_path,
            private_key_base64=config.private_key_base64,
        )
        self.session = requests.Session()

    def _request(
        self,
        method: str,
        path: str,
        params: dict = None,
        data: dict = None,
    ) -> dict:
        """Make authenticated request to Kalshi API."""

        url = f"{self.config.api_url}{path}"
        body = json.dumps(data) if data else ""

        # Signature must include full path with /trade-api/v2
        full_path = f"/trade-api/v2{path}"
        headers = self.auth.get_auth_headers(method, full_path, body)

        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            data=body if data else None,
        )

        if response.status_code == 429:
            # Rate limited - wait and retry
            time.sleep(1)
            return self._request(method, path, params, data)

        response.raise_for_status()
        return response.json() if response.text else {}

    # ========== Account ==========

    def get_balance(self) -> dict:
        """Get account balance."""
        return self._request("GET", "/portfolio/balance")

    def get_balance_cents(self) -> int:
        """Get available balance in cents."""
        data = self.get_balance()
        return data.get("balance", 0)

    def get_balance_dollars(self) -> float:
        """Get available balance in dollars."""
        return self.get_balance_cents() / 100

    # ========== Markets ==========

    def get_markets(
        self,
        status: str = "open",
        limit: int = 200,
        cursor: str = None,
        tickers: list[str] = None,
        event_ticker: str = None,
        series_ticker: str = None,
    ) -> dict:
        """Get list of markets."""
        params = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if tickers:
            params["tickers"] = ",".join(tickers)
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._request("GET", "/markets", params=params)

    def get_market(self, ticker: str) -> MarketData:
        """Get single market data."""
        data = self._request("GET", f"/markets/{ticker}")
        return MarketData.from_api(data)

    def get_events(
        self,
        status: str = "open",
        with_nested_markets: bool = True,
        limit: int = 100,
    ) -> dict:
        """Get list of events."""
        params = {
            "status": status,
            "with_nested_markets": with_nested_markets,
            "limit": limit,
        }
        return self._request("GET", "/events", params=params)

    # ========== Orders ==========

    def place_order(
        self,
        ticker: str,
        side: str,  # "yes" or "no"
        action: str,  # "buy" or "sell"
        count: int,
        price: int,  # cents
        client_order_id: str = None,
    ) -> OrderResponse:
        """
        Place a limit order.

        Args:
            ticker: Market ticker
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts
            price: Price in cents (1-99)
            client_order_id: Optional custom order ID

        Returns:
            OrderResponse with order details
        """
        data = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": "limit",
        }

        # Set price based on side
        if side == "yes":
            data["yes_price"] = price
        else:
            data["no_price"] = price

        if client_order_id:
            data["client_order_id"] = client_order_id

        response = self._request("POST", "/portfolio/orders", data=data)
        return OrderResponse.from_api(response)

    def place_market_order(
        self,
        ticker: str,
        side: str,
        action: str,
        count: int,
    ) -> OrderResponse:
        """Place a market order (fills immediately at best price).

        Per Kalshi docs: market orders are inferred when no price is specified.
        We also use immediate_or_cancel to ensure it fills now or cancels.
        """
        data = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "time_in_force": "immediate_or_cancel",  # Fill now or cancel
        }
        response = self._request("POST", "/portfolio/orders", data=data)
        return OrderResponse.from_api(response)

    def get_order(self, order_id: str) -> OrderResponse:
        """Get order status."""
        data = self._request("GET", f"/portfolio/orders/{order_id}")
        return OrderResponse.from_api(data)

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order."""
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def get_orders(
        self,
        status: str = None,  # "resting", "pending", "executed", "canceled"
        ticker: str = None,
        limit: int = 100,
    ) -> list[OrderResponse]:
        """Get list of orders."""
        params = {"limit": limit}
        if status:
            params["status"] = status
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/orders", params=params)
        return [OrderResponse.from_api(o) for o in data.get("orders", [])]

    # ========== Positions ==========

    def get_positions(self, ticker: str = None) -> list[dict]:
        """Get current positions."""
        params = {}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/positions", params=params)
        return data.get("market_positions", [])

    def get_fills(self, ticker: str = None, limit: int = 100) -> list[dict]:
        """Get fill history."""
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/fills", params=params)
        return data.get("fills", [])

    def get_settlements(self, limit: int = 100) -> list[dict]:
        """Get settlement history."""
        params = {"limit": limit}
        data = self._request("GET", "/portfolio/settlements", params=params)
        return data.get("settlements", [])

    # ========== Exchange ==========

    def get_exchange_status(self) -> dict:
        """Get exchange status."""
        return self._request("GET", "/exchange/status")
