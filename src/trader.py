"""
Directional Stop-Loss Strategy Trader

Main trading loop that implements:
1. Wait 10 minutes (5 min remaining)
2. Check BTC direction vs Strike
3. Bet YES if above, NO if below
4. Only enter at 60-85c
5. Monitor bid and exit if it drops to 50c
6. Hold to expiry if not stopped
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

from .config import AppConfig, load_config
from .kalshi_client import KalshiClient
from .kraken import KrakenClient
from .market_scanner import MarketScanner, TradingOpportunity
from .bet_calculator import BetCalculator, BetCalculation
from .trade_executor import TradeExecutor, TradeRecord, TradeStatus


@dataclass
class TradingState:
    """Persistent trading state."""
    bankroll: float
    starting_bankroll: float = 0.0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_stopped: int = 0  # Trades exited via stop loss
    total_profit: float = 0.0
    session_start: str = ""
    last_trade_time: str = ""

    def save(self, path: Path):
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "TradingState":
        if path.exists():
            with open(path) as f:
                return cls(**json.load(f))
        return cls(bankroll=0)


@dataclass
class TradeResult:
    """Result of a single trade."""
    ticker: str
    side: str
    contracts: int
    entry_price: int
    fill_price: int
    exit_price: Optional[int]  # If stopped out
    cost: float
    profit: float
    won: bool
    stopped_out: bool
    btc_price_entry: float
    btc_price_exit: float
    floor_strike: float


class Trader:
    """
    Directional Stop-Loss Strategy Trader.

    Strategy:
    1. Wait until 5 minutes remaining in 15-min window
    2. Check BTC direction vs strike price
    3. Bet YES if BTC > Strike, NO if BTC < Strike
    4. Only enter if favored side ask is 60-85c
    5. Bet 5% of bankroll
    6. Monitor bid price - EXIT if it drops to 50c
    7. Hold to expiry if not stopped out
    """

    STOP_LOSS_PRICE = 50  # Exit if bid drops to this

    def __init__(self, config: AppConfig = None):
        self.config = config or load_config()

        # Initialize components
        self.client = KalshiClient(self.config.kalshi)
        self.scanner = MarketScanner(
            client=self.client,
            min_price=self.config.trading.min_entry_price,
            max_price=self.config.trading.max_entry_price,
            data_dir=self.config.data_dir,
        )
        self.bet_calculator = BetCalculator(
            bet_percentage=self.config.trading.bankroll_bet_percentage,
            stop_loss_price=self.config.trading.stop_loss_price,
        )
        self.executor = TradeExecutor(
            client=self.client,
            limit_offset=self.config.trading.limit_order_offset,
        )

        # State
        self.state_path = self.config.data_dir / "trading_state.json"
        self.state = TradingState.load(self.state_path)

        # Get real balance
        self.refresh_bankroll()

        # Set starting bankroll if provided
        if self.config.trading.starting_bankroll:
            self.state.starting_bankroll = self.config.trading.starting_bankroll
            self.effective_bankroll = self.config.trading.starting_bankroll
        else:
            self.effective_bankroll = self.state.bankroll

        self.state.session_start = datetime.now(timezone.utc).isoformat()

        # Trade history
        self.trade_history: list[TradeResult] = []

        # Track traded windows to prevent re-entry after stop loss
        self._traded_tickers: set[str] = set()

        # Logging
        self.log_path = self.config.logs_dir / f"trades_{datetime.now().strftime('%Y%m%d')}.json"

        # Rate limiting for scan logs
        self._last_scan_log_time = 0
        self._scan_log_interval = 10

    def log(self, message: str, level: str = "INFO"):
        """Log a message with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = {
            "INFO": "[INFO ]",
            "TRADE": "[TRADE]",
            "WIN": "[WIN  ]",
            "LOSS": "[LOSS ]",
            "STOP": "[STOP ]",
            "ERROR": "[ERROR]",
            "WARN": "[WARN ]",
            "DEBUG": "[DEBUG]",
        }
        print(f"{timestamp} {prefix.get(level, '[INFO ]')} {message}")

    def refresh_bankroll(self):
        """Refresh bankroll from Kalshi account."""
        try:
            balance = self.client.get_balance_dollars()
            self.state.bankroll = balance
            self.log(f"Bankroll: ${balance:.2f}")
        except Exception as e:
            self.log(f"Could not refresh bankroll: {e}", "WARN")

    def can_trade(self) -> bool:
        """Check if we can place another trade."""
        # Check real bankroll
        if self.state.bankroll < 5:
            self.log("Real bankroll too low", "ERROR")
            return False

        # Check effective bankroll
        if self.effective_bankroll < 5:
            self.log(f"Effective bankroll too low (${self.effective_bankroll:.2f})", "ERROR")
            return False

        return True

    def calculate_bet(self, opportunity: TradingOpportunity) -> Optional[BetCalculation]:
        """Calculate the bet for an opportunity."""
        bet = self.bet_calculator.calculate_bet(
            bankroll=self.effective_bankroll,
            entry_price_cents=opportunity.entry_price,
        )

        if not bet:
            self.log(f"Cannot calculate bet - insufficient bankroll", "ERROR")
            return None

        # Verify we can afford with real balance
        if bet.cost_dollars > self.state.bankroll:
            self.log(f"Bet cost ${bet.cost_dollars:.2f} exceeds real bankroll ${self.state.bankroll:.2f}", "ERROR")
            return None

        return bet

    def monitor_stop_loss(
        self,
        ticker: str,
        side: str,
        close_time: datetime,
        poll_interval: float = 1.0,
    ) -> tuple[bool, int, float]:
        """
        Monitor position and exit if bid drops to stop loss.

        Args:
            ticker: Market ticker
            side: "yes" or "no"
            close_time: When market closes
            poll_interval: Seconds between price checks

        Returns:
            (stopped_out, exit_price, btc_price_at_exit)
        """
        self.log(f"Monitoring stop loss at {self.STOP_LOSS_PRICE}c...", "INFO")

        while True:
            now = datetime.now(timezone.utc)

            # Check if market has closed
            if now >= close_time:
                self.log("Market closed - holding to settlement", "INFO")
                btc_price = KrakenClient.get_btc_price() or 0
                return False, 0, btc_price

            # Get current bid price
            try:
                market = self.client.get_market(ticker)

                if side == "yes":
                    current_bid = market.yes_bid
                else:
                    current_bid = market.no_bid

                # Check stop loss
                if current_bid <= self.STOP_LOSS_PRICE:
                    self.log(f"STOP LOSS TRIGGERED! Bid dropped to {current_bid}c", "STOP")
                    btc_price = KrakenClient.get_btc_price() or 0
                    return True, current_bid, btc_price

                # Log position status every 30 seconds
                time_left = (close_time - now).total_seconds()
                if int(time_left) % 30 == 0:
                    self.log(f"Position OK - bid at {current_bid}c, {time_left:.0f}s remaining", "DEBUG")

            except Exception as e:
                self.log(f"Error checking bid: {e}", "WARN")

            time.sleep(poll_interval)

    def exit_position(self, ticker: str, side: str, contracts: int, target_price: int) -> tuple[bool, int]:
        """
        Exit position by SELLING contracts with aggressive limit order.

        Args:
            ticker: Market ticker
            side: "yes" or "no"
            contracts: Number of contracts to sell
            target_price: Current bid price

        Returns:
            (success, actual_fill_price)
        """
        try:
            # Use aggressive limit price (5c below bid) to guarantee fill
            # The order will execute at the best available price
            sell_price = max(1, target_price - 5)

            self.log(f"STOP LOSS: SELLING {contracts} {side.upper()} @ {sell_price}c limit (bid was {target_price}c)", "STOP")

            # Place LIMIT SELL order - NOT a buy, NOT opposite side
            order = self.client.place_order(
                ticker=ticker,
                side=side,  # Same side we own (e.g., "no")
                action="sell",  # SELL to exit
                count=contracts,
                price=sell_price,  # Aggressive price below bid
            )

            self.log(f"SELL order placed: {order.order_id} (status: {order.status})", "STOP")

            # Check immediate fill
            if order.filled_count > 0:
                actual_price = order.average_fill_price or target_price
                self.log(f"SELL FILLED immediately: {order.filled_count} @ {actual_price}c", "STOP")
                return True, actual_price

            # Wait for fill
            max_wait = 15
            start = time.time()
            while time.time() - start < max_wait:
                try:
                    updated = self.client.get_order(order.order_id)
                    self.log(f"Order status: {updated.status}, filled: {updated.filled_count}/{contracts}", "DEBUG")

                    if updated.filled_count >= contracts:
                        actual_price = updated.average_fill_price or target_price
                        self.log(f"SELL FILLED: {updated.filled_count} @ {actual_price}c", "STOP")
                        return True, actual_price
                    elif updated.status in ["canceled", "cancelled"]:
                        self.log(f"SELL order CANCELED - position NOT exited!", "ERROR")
                        return False, 0
                except Exception as e:
                    self.log(f"Error checking order: {e}", "WARN")
                time.sleep(1)

            # Timeout - cancel and report
            self.log(f"SELL order timeout - attempting cancel", "ERROR")
            try:
                self.client.cancel_order(order.order_id)
            except:
                pass
            return False, 0

        except Exception as e:
            self.log(f"Error in exit_position: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return False, 0

    def get_official_settlement(self, ticker: str, max_wait: int = 180, poll_interval: int = 5) -> Optional[str]:
        """
        Poll Kalshi's settled markets API for the official result.

        Returns:
            'yes' or 'no' if settled, None if timeout
        """
        import requests

        KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
        series_ticker = "KXBTC15M"

        start_time = time.time()
        attempts = 0

        while (time.time() - start_time) < max_wait:
            attempts += 1
            try:
                resp = requests.get(
                    f"{KALSHI_API}/markets",
                    params={"series_ticker": series_ticker, "status": "settled", "limit": 20},
                    timeout=10
                )
                resp.raise_for_status()
                data = resp.json()

                for market in data.get('markets', []):
                    if market.get('ticker') == ticker:
                        result = market.get('result')
                        if result:
                            elapsed = time.time() - start_time
                            self.log(f"Settlement: {result.upper()} ({elapsed:.1f}s)", "INFO")
                            return result

                if attempts % 6 == 0:
                    self.log(f"Waiting for settlement... ({time.time() - start_time:.0f}s)", "DEBUG")
                time.sleep(poll_interval)

            except Exception as e:
                self.log(f"Settlement poll error: {e}", "WARN")
                time.sleep(poll_interval)

        return None

    def execute_trade(self, opportunity: TradingOpportunity, bet: BetCalculation) -> Optional[TradeResult]:
        """
        Execute a complete trade with stop loss monitoring.

        Returns:
            TradeResult with outcome details
        """
        self.log("=" * 60, "TRADE")
        self.log(f"EXECUTING TRADE", "TRADE")
        self.log(f"  Ticker: {opportunity.ticker}", "TRADE")
        self.log(f"  Side: {opportunity.side.upper()}", "TRADE")
        self.log(f"  Direction: BTC ${opportunity.btc_price:,.0f} {opportunity.btc_direction} strike ${opportunity.floor_strike:,.0f}", "TRADE")
        self.log(f"  Entry price: {opportunity.entry_price}c", "TRADE")
        self.log(f"  Contracts: {bet.contracts}", "TRADE")
        self.log(f"  Cost: ${bet.cost_dollars:.2f} ({bet.bankroll_percentage:.1f}% of bankroll)", "TRADE")
        self.log(f"  If WIN: +${bet.net_profit_if_win:.2f}", "TRADE")
        self.log(f"  If STOP at 50c: -${bet.max_loss_with_stop:.2f}", "TRADE")

        # Place order
        result = self.executor.execute_opportunity(opportunity, bet)

        if not result.success:
            self.log(f"ORDER FAILED: {result.error}", "ERROR")
            return None

        trade = result.trade
        self.log(f"Order submitted: {trade.order_id}", "DEBUG")

        # Wait for fill
        self.log(f"Waiting for fill...", "DEBUG")
        trade = self.executor.wait_for_fill(trade.order_id, timeout_seconds=30)

        if trade.status == TradeStatus.UNFILLED:
            self.log(f"Order not filled - canceled", "WARN")
            return None

        slippage = trade.actual_fill_price - opportunity.entry_price
        self.log(f"ORDER FILLED at {trade.actual_fill_price}c (slip: {slippage:+d}c)", "TRADE")

        # Monitor stop loss until market close
        stopped_out, exit_price, btc_price_exit = self.monitor_stop_loss(
            ticker=opportunity.ticker,
            side=opportunity.side,
            close_time=opportunity.close_time,
            poll_interval=1.0,
        )

        if stopped_out:
            # Exit position with market order
            exit_success, actual_exit_price = self.exit_position(
                ticker=opportunity.ticker,
                side=opportunity.side,
                contracts=trade.filled_contracts,
                target_price=exit_price,
            )

            # Use actual exit price if available, otherwise use trigger price
            final_exit_price = actual_exit_price if actual_exit_price > 0 else exit_price

            # Calculate loss from stop (entry price - exit price)
            loss_per_contract = (trade.actual_fill_price - final_exit_price) / 100
            total_loss = trade.filled_contracts * loss_per_contract

            self.log("=" * 60, "STOP")
            self.log(f"STOPPED OUT: {opportunity.ticker}", "STOP")
            self.log(f"  Entry: {trade.actual_fill_price}c -> Exit: {final_exit_price}c", "STOP")
            self.log(f"  Loss: ${total_loss:.2f}", "STOP")
            if not exit_success:
                self.log(f"  WARNING: Exit order may not have filled!", "ERROR")

            # Update stats
            self.state.total_losses += 1
            self.state.total_stopped += 1
            self.state.total_profit -= total_loss
            self.effective_bankroll -= total_loss

            return TradeResult(
                ticker=opportunity.ticker,
                side=opportunity.side,
                contracts=trade.filled_contracts,
                entry_price=opportunity.entry_price,
                fill_price=trade.actual_fill_price,
                exit_price=final_exit_price,
                cost=bet.cost_dollars,
                profit=-total_loss,
                won=False,
                stopped_out=True,
                btc_price_entry=opportunity.btc_price,
                btc_price_exit=btc_price_exit,
                floor_strike=opportunity.floor_strike,
            )

        # Wait for settlement
        self.log("Waiting for settlement...", "INFO")

        # Wait for market to close
        now = datetime.now(timezone.utc)
        wait_seconds = (opportunity.close_time - now).total_seconds()
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        # Get official result
        result = self.get_official_settlement(opportunity.ticker, max_wait=180)

        if not result:
            self.log("Settlement timeout - checking bankroll", "WARN")
            old_bankroll = self.state.bankroll
            self.refresh_bankroll()
            delta = self.state.bankroll - old_bankroll
            result = opportunity.side if delta > 0 else ("no" if opportunity.side == "yes" else "yes")

        # Determine win/loss
        won = (opportunity.side == result)
        btc_price_exit = KrakenClient.get_btc_price() or 0

        if won:
            profit = bet.net_profit_if_win
            self.log("=" * 60, "WIN")
            self.log(f"TRADE WON: {opportunity.ticker}", "WIN")
            self.log(f"  Profit: +${profit:.2f}", "WIN")
            self.state.total_wins += 1
            self.state.total_profit += profit
            self.effective_bankroll += profit
        else:
            loss = bet.cost_dollars
            self.log("=" * 60, "LOSS")
            self.log(f"TRADE LOST: {opportunity.ticker}", "LOSS")
            self.log(f"  Loss: -${loss:.2f}", "LOSS")
            self.state.total_losses += 1
            self.state.total_profit -= loss
            self.effective_bankroll -= loss
            profit = -loss

        self.state.total_trades += 1
        self.state.last_trade_time = datetime.now(timezone.utc).isoformat()
        self.state.save(self.state_path)

        return TradeResult(
            ticker=opportunity.ticker,
            side=opportunity.side,
            contracts=trade.filled_contracts,
            entry_price=opportunity.entry_price,
            fill_price=trade.actual_fill_price,
            exit_price=None,
            cost=bet.cost_dollars,
            profit=profit,
            won=won,
            stopped_out=False,
            btc_price_entry=opportunity.btc_price,
            btc_price_exit=btc_price_exit,
            floor_strike=opportunity.floor_strike,
        )

    def run_once(self) -> Optional[TradeResult]:
        """
        Run one trading cycle.

        Returns:
            TradeResult if trade was executed, None otherwise
        """
        if not self.can_trade():
            return None

        # Scan for opportunities
        opportunity = self.scanner.find_best_opportunity()

        if not opportunity:
            now = time.time()
            if now - self._last_scan_log_time >= self._scan_log_interval:
                self._last_scan_log_time = now
                self.log(f"No opportunities in 60-85c range (polling every 300ms)")
            return None

        # Check if we already traded this window (prevents re-entry after stop loss)
        if opportunity.ticker in self._traded_tickers:
            return None

        # Found opportunity
        print("=" * 60)
        print(f"[OPPORTUNITY] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"  {opportunity}")
        print("=" * 60)

        # Mark this ticker as traded BEFORE executing
        self._traded_tickers.add(opportunity.ticker)
        self.log(f"Marked {opportunity.ticker} as traded (no re-entry)", "DEBUG")

        # Calculate bet
        bet = self.calculate_bet(opportunity)
        if not bet:
            return None

        # Execute trade with stop loss monitoring
        result = self.execute_trade(opportunity, bet)

        if result:
            self.trade_history.append(result)

        return result

    def run_continuous(self, poll_interval: float = 0.3):
        """Run continuously, scanning for opportunities."""
        self.log("=" * 60)
        self.log("DIRECTIONAL STOP-LOSS STRATEGY")
        self.log("=" * 60)
        self.log("CONFIGURATION:")
        self.log(f"  Real Bankroll: ${self.state.bankroll:.2f}")
        self.log(f"  Effective Bankroll: ${self.effective_bankroll:.2f}")
        self.log(f"  Bet Size: {self.config.trading.bankroll_bet_percentage * 100:.0f}% of bankroll")
        self.log(f"  Entry Range: {self.config.trading.min_entry_price}-{self.config.trading.max_entry_price}c")
        self.log(f"  Stop Loss: {self.config.trading.stop_loss_price}c")
        self.log("RULES:")
        self.log("  1. Wait 10 minutes (5 min remaining)")
        self.log("  2. BTC > Strike = YES, BTC < Strike = NO")
        self.log("  3. Only enter at 60-85c")
        self.log("  4. Exit if bid drops to 50c")
        self.log("=" * 60)

        try:
            while self.can_trade():
                result = self.run_once()

                if not result:
                    time.sleep(poll_interval)
                else:
                    time.sleep(2)  # Brief pause after trade

                # Refresh bankroll periodically
                if self.state.total_trades % 5 == 0:
                    self.refresh_bankroll()

        except KeyboardInterrupt:
            self.log("Shutting down...")

        finally:
            self.shutdown()

    def shutdown(self):
        """Clean shutdown."""
        self.log("=" * 60)
        self.log("SESSION SUMMARY")
        self.log(f"Total trades: {self.state.total_trades}")
        self.log(f"Wins: {self.state.total_wins}")
        self.log(f"Losses: {self.state.total_losses} (Stopped: {self.state.total_stopped})")
        if self.state.total_wins + self.state.total_losses > 0:
            wr = self.state.total_wins / (self.state.total_wins + self.state.total_losses) * 100
            self.log(f"Win rate: {wr:.1f}%")
        self.log(f"Total P&L: ${self.state.total_profit:+.2f}")
        self.log(f"Final bankroll: ${self.state.bankroll:.2f}")
        self.log("=" * 60)

        self.state.save(self.state_path)
        self.scanner.save_order_book_log()

    def show_status(self):
        """Print current status."""
        self.refresh_bankroll()

        print("\n" + "=" * 50)
        print("CURRENT STATUS")
        print("=" * 50)
        print(f"Real Bankroll: ${self.state.bankroll:.2f}")
        print(f"Effective Bankroll: ${self.effective_bankroll:.2f}")
        print(f"Session trades: {self.state.total_trades}")
        print(f"Wins/Losses: {self.state.total_wins}/{self.state.total_losses}")
        print(f"Stopped out: {self.state.total_stopped}")
        print(f"Session P&L: ${self.state.total_profit:+.2f}")
        print()

        # Show next bet info
        bet = self.bet_calculator.calculate_bet(self.effective_bankroll, 70)
        if bet:
            print(f"Next bet (at 70c):")
            print(f"  Contracts: {bet.contracts}")
            print(f"  Cost: ${bet.cost_dollars:.2f}")
            print(f"  If WIN: +${bet.net_profit_if_win:.2f}")
            print(f"  If STOP: -${bet.max_loss_with_stop:.2f}")
        print()

    def get_trade_history(self) -> list[TradeResult]:
        """Get trade history."""
        return self.trade_history
