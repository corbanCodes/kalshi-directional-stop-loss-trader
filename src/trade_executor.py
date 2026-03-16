"""
Trade execution and order management.
Handles placing orders, tracking fills, and monitoring settlements.
"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .kalshi_client import KalshiClient, OrderResponse, MarketData
from .market_scanner import TradingOpportunity, MarketScanner
from .bet_calculator import BetCalculation


class TradeStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELED = "canceled"
    SETTLED_WIN = "settled_win"
    SETTLED_LOSS = "settled_loss"
    UNFILLED = "unfilled"


@dataclass
class TradeRecord:
    """Complete record of a trade."""
    trade_id: str
    timestamp: datetime
    ticker: str
    side: str
    action: str

    # Order details
    intended_price: int  # what we wanted
    limit_price: int  # what we submitted (intended + offset)
    actual_fill_price: int  # what we got
    contracts: int
    filled_contracts: int

    # Costs and P&L
    cost_dollars: float
    fee_dollars: float
    gross_profit_dollars: float = 0.0
    net_profit_dollars: float = 0.0

    # Status
    status: TradeStatus = TradeStatus.PENDING
    order_id: str = ""
    settlement_result: str = ""  # "yes" or "no"

    # Martingale context
    bet_number: int = 1
    is_recovery: bool = False
    recovering_loss: float = 0.0

    def __post_init__(self):
        if not self.trade_id:
            self.trade_id = str(uuid.uuid4())[:8]


@dataclass
class ExecutionResult:
    """Result of attempting to execute a trade."""
    success: bool
    trade: Optional[TradeRecord] = None
    error: str = ""
    order_response: Optional[OrderResponse] = None


class TradeExecutor:
    """
    Executes trades with limit orders and tracks results.

    Rules:
    - Place limit order at 1c above ask to ensure fill
    - Track intended vs actual fill price
    - Monitor for settlement
    """

    def __init__(
        self,
        client: KalshiClient,
        limit_offset: int = 1,  # cents above ask
    ):
        self.client = client
        self.limit_offset = limit_offset
        self.trades: list[TradeRecord] = []
        self.pending_orders: dict[str, TradeRecord] = {}

    def execute_opportunity(
        self,
        opportunity: TradingOpportunity,
        bet: BetCalculation,
    ) -> ExecutionResult:
        """
        Execute a trade for a given opportunity.

        Args:
            opportunity: The market opportunity
            bet: The calculated bet

        Returns:
            ExecutionResult with trade details
        """
        # Calculate limit price (1c above ask to ensure fill)
        limit_price = min(opportunity.entry_price + self.limit_offset, 99)

        # Create trade record
        trade = TradeRecord(
            trade_id="",
            timestamp=datetime.now(timezone.utc),
            ticker=opportunity.ticker,
            side=opportunity.side,
            action="buy",
            intended_price=opportunity.entry_price,
            limit_price=limit_price,
            actual_fill_price=0,
            contracts=bet.contracts,
            filled_contracts=0,
            cost_dollars=bet.cost_dollars,
            fee_dollars=bet.contracts * MarketScanner.calc_fee(opportunity.entry_price),
            bet_number=1,  # Always bet 1, no recovery
            is_recovery=False,
            recovering_loss=0,
        )

        try:
            # Place the order
            client_order_id = f"strat_{trade.trade_id}_{int(time.time())}"

            order = self.client.place_order(
                ticker=opportunity.ticker,
                side=opportunity.side,
                action="buy",
                count=bet.contracts,
                price=limit_price,
                client_order_id=client_order_id,
            )

            trade.order_id = order.order_id
            trade.filled_contracts = order.filled_count
            trade.actual_fill_price = order.average_fill_price or limit_price

            # Check order status from response
            if order.status == "executed":
                trade.status = TradeStatus.FILLED
                # If executed but API returned 0 filled_count, assume all contracts filled
                if trade.filled_contracts == 0:
                    trade.filled_contracts = bet.contracts
            elif order.filled_count > 0:
                trade.status = TradeStatus.FILLED if order.filled_count == bet.contracts else TradeStatus.PARTIAL
            else:
                trade.status = TradeStatus.PENDING

            self.trades.append(trade)
            self.pending_orders[order.order_id] = trade

            return ExecutionResult(
                success=True,
                trade=trade,
                order_response=order,
            )

        except Exception as e:
            trade.status = TradeStatus.CANCELED
            return ExecutionResult(
                success=False,
                trade=trade,
                error=str(e),
            )

    def check_order_status(self, order_id: str) -> Optional[TradeRecord]:
        """Check and update status of a pending order."""
        if order_id not in self.pending_orders:
            return None

        trade = self.pending_orders[order_id]

        try:
            order = self.client.get_order(order_id)

            trade.filled_contracts = order.filled_count
            trade.actual_fill_price = order.average_fill_price or trade.limit_price

            if order.status == "executed":
                trade.status = TradeStatus.FILLED
                # If executed but API returned 0 filled_count, assume all contracts filled
                if trade.filled_contracts == 0:
                    trade.filled_contracts = trade.contracts
            elif order.status == "canceled":
                trade.status = TradeStatus.CANCELED
            elif order.filled_count > 0:
                trade.status = TradeStatus.PARTIAL

            return trade

        except Exception as e:
            # 404 = order already executed and purged, assume filled
            if "404" in str(e):
                trade.status = TradeStatus.FILLED
                if trade.filled_contracts == 0:
                    trade.filled_contracts = trade.contracts
            return trade

    def check_settlement(self, ticker: str) -> Optional[str]:
        """
        Check if a market has settled and what the result was.

        Returns:
            "yes", "no", or None if not settled
        """
        try:
            market = self.client.get_market(ticker)
            if market.status == "settled":
                # Check settlements endpoint for result
                settlements = self.client.get_settlements(limit=50)
                for s in settlements:
                    if s.get("ticker") == ticker:
                        return s.get("result")

            return None

        except Exception as e:
            print(f"Error checking settlement for {ticker}: {e}")
            return None

    def update_trade_settlement(self, trade: TradeRecord, result: str):
        """Update trade record with settlement result."""
        trade.settlement_result = result

        # Did we win?
        won = (trade.side == result)

        if won:
            trade.status = TradeStatus.SETTLED_WIN
            # Gross profit = $1 per contract - cost
            trade.gross_profit_dollars = trade.filled_contracts * 1.0 - (trade.filled_contracts * trade.actual_fill_price / 100)
            trade.net_profit_dollars = trade.gross_profit_dollars - trade.fee_dollars
        else:
            trade.status = TradeStatus.SETTLED_LOSS
            # Loss = cost + fee
            trade.gross_profit_dollars = -(trade.filled_contracts * trade.actual_fill_price / 100)
            trade.net_profit_dollars = trade.gross_profit_dollars - trade.fee_dollars

    def wait_for_fill(
        self,
        order_id: str,
        timeout_seconds: float = 30,
        poll_interval: float = 0.5,
    ) -> TradeRecord:
        """Wait for an order to fill."""
        start = time.time()

        while time.time() - start < timeout_seconds:
            trade = self.check_order_status(order_id)

            if trade and trade.status in [TradeStatus.FILLED, TradeStatus.CANCELED]:
                return trade

            time.sleep(poll_interval)

        # Timeout - mark as unfilled
        if order_id in self.pending_orders:
            trade = self.pending_orders[order_id]
            if trade.filled_contracts == 0:
                trade.status = TradeStatus.UNFILLED
                # Try to cancel
                try:
                    self.client.cancel_order(order_id)
                except:
                    pass
            return trade

        return None

    def wait_for_settlement(
        self,
        trade: TradeRecord,
        timeout_seconds: float = 600,  # 10 minutes max
        poll_interval: float = 5,
    ) -> TradeRecord:
        """Wait for a trade's market to settle."""
        start = time.time()

        while time.time() - start < timeout_seconds:
            result = self.check_settlement(trade.ticker)

            if result:
                self.update_trade_settlement(trade, result)
                return trade

            time.sleep(poll_interval)

        return trade

    def get_trade_summary(self) -> dict:
        """Get summary statistics for all trades."""
        total = len(self.trades)
        wins = sum(1 for t in self.trades if t.status == TradeStatus.SETTLED_WIN)
        losses = sum(1 for t in self.trades if t.status == TradeStatus.SETTLED_LOSS)
        pending = sum(1 for t in self.trades if t.status in [TradeStatus.PENDING, TradeStatus.FILLED])
        unfilled = sum(1 for t in self.trades if t.status == TradeStatus.UNFILLED)

        total_profit = sum(t.net_profit_dollars for t in self.trades)
        total_cost = sum(t.cost_dollars for t in self.trades if t.status != TradeStatus.UNFILLED)

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "unfilled": unfilled,
            "win_rate": wins / (wins + losses) * 100 if (wins + losses) > 0 else 0,
            "total_profit": total_profit,
            "total_cost": total_cost,
            "roi": total_profit / total_cost * 100 if total_cost > 0 else 0,
        }

    def print_trade_log(self):
        """Print all trades in a readable format."""
        print("\n" + "=" * 80)
        print("TRADE LOG")
        print("=" * 80)

        for trade in self.trades:
            status_emoji = {
                TradeStatus.SETTLED_WIN: "[WIN]",
                TradeStatus.SETTLED_LOSS: "[LOSS]",
                TradeStatus.PENDING: "[PEND]",
                TradeStatus.FILLED: "[FILL]",
                TradeStatus.UNFILLED: "[UNFIL]",
                TradeStatus.CANCELED: "[CANC]",
            }.get(trade.status, "[?]")

            recovery_note = f" (recovery #{trade.bet_number})" if trade.is_recovery else ""

            print(
                f"{trade.timestamp.strftime('%H:%M:%S')} | "
                f"{status_emoji} {trade.ticker} | "
                f"{trade.side.upper()} @ {trade.actual_fill_price or trade.intended_price}c | "
                f"{trade.filled_contracts}/{trade.contracts} contracts | "
                f"${trade.net_profit_dollars:+.2f}{recovery_note}"
            )

        summary = self.get_trade_summary()
        print("-" * 80)
        print(
            f"Total: {summary['total_trades']} trades | "
            f"W/L: {summary['wins']}/{summary['losses']} ({summary['win_rate']:.1f}%) | "
            f"P&L: ${summary['total_profit']:+.2f}"
        )
