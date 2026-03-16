"""
Simple Bet Calculator for Directional Stop-Loss Strategy.

No recovery, no martingale - just 5% of bankroll per bet with compounding.

The strategy relies on stop-loss at 50c to limit losses, not recovery bets.
"""

import math
from dataclasses import dataclass
from typing import Optional

from .market_scanner import MarketScanner


@dataclass
class BetCalculation:
    """A calculated bet for the directional strategy."""
    contracts: int
    cost_dollars: float
    entry_price_cents: int
    net_profit_if_win: float
    bankroll_percentage: float  # What % of bankroll this bet represents
    max_loss_with_stop: float  # Max loss if stopped out at 50c


class BetCalculator:
    """
    Simple bet calculator for Directional Stop-Loss Strategy.

    Rules:
    - Bet 5% of current bankroll
    - Contracts = bet_amount / (price / 100)
    - Stop loss at 50c limits downside
    """

    def __init__(self, bet_percentage: float = 0.05, stop_loss_price: int = 50):
        self.bet_percentage = bet_percentage
        self.stop_loss_price = stop_loss_price

    def calculate_bet(
        self,
        bankroll: float,
        entry_price_cents: int,
    ) -> Optional[BetCalculation]:
        """
        Calculate bet size for given bankroll and entry price.

        Args:
            bankroll: Current bankroll in dollars
            entry_price_cents: Entry price in cents (60-85)

        Returns:
            BetCalculation with contracts and expected outcomes
        """
        if bankroll <= 0:
            return None

        if entry_price_cents <= 0 or entry_price_cents >= 100:
            return None

        # Step 1: Calculate bet amount (5% of bankroll)
        bet_amount = bankroll * self.bet_percentage

        # Step 2: Calculate contracts
        price_dollars = entry_price_cents / 100
        contracts = int(bet_amount / price_dollars)

        if contracts < 1:
            contracts = 1  # Minimum 1 contract

        # Step 3: Calculate actual cost
        cost = contracts * price_dollars

        # Step 4: Calculate profit if win
        net_profit_per_contract = MarketScanner.calc_net_profit(entry_price_cents)
        profit_if_win = contracts * net_profit_per_contract

        # Step 5: Calculate max loss with stop loss
        # If stopped at 50c, loss = (entry_price - 50c) per contract
        stop_loss_per_contract = (entry_price_cents - self.stop_loss_price) / 100
        max_loss_with_stop = contracts * stop_loss_per_contract

        return BetCalculation(
            contracts=contracts,
            cost_dollars=cost,
            entry_price_cents=entry_price_cents,
            net_profit_if_win=profit_if_win,
            bankroll_percentage=cost / bankroll * 100,
            max_loss_with_stop=max_loss_with_stop,
        )

    def print_bet_info(self, bankroll: float, entry_price_cents: int):
        """Print bet calculation details."""
        bet = self.calculate_bet(bankroll, entry_price_cents)

        if not bet:
            print("Cannot calculate bet - invalid inputs")
            return

        print(f"\n{'='*60}")
        print(f"DIRECTIONAL STOP-LOSS BET CALCULATION")
        print(f"{'='*60}")
        print(f"Bankroll: ${bankroll:.2f}")
        print(f"Entry Price: {entry_price_cents}c")
        print(f"Bet Percentage: {self.bet_percentage * 100:.0f}%")
        print(f"Stop Loss: {self.stop_loss_price}c")
        print(f"{'='*60}")
        print(f"Contracts: {bet.contracts}")
        print(f"Cost: ${bet.cost_dollars:.2f} ({bet.bankroll_percentage:.1f}% of bankroll)")
        print(f"If WIN: +${bet.net_profit_if_win:.2f}")
        print(f"If STOPPED at 50c: -${bet.max_loss_with_stop:.2f}")
        print(f"If FULL LOSS: -${bet.cost_dollars:.2f}")
        print(f"{'='*60}")

    def simulate_growth(
        self,
        starting_bankroll: float,
        num_trades: int,
        win_rate: float = 0.575,
        avg_entry_price: int = 70,
    ):
        """
        Simulate bankroll growth with compounding.

        Args:
            starting_bankroll: Starting amount
            num_trades: Number of trades to simulate
            win_rate: Expected win rate (default 57.5%)
            avg_entry_price: Average entry price in cents
        """
        import random

        bankroll = starting_bankroll
        wins = 0
        losses = 0
        stopped = 0

        print(f"\n{'='*60}")
        print(f"SIMULATING {num_trades} TRADES @ {win_rate*100:.1f}% WIN RATE")
        print(f"Starting: ${starting_bankroll:.2f}")
        print(f"{'='*60}")

        for i in range(num_trades):
            bet = self.calculate_bet(bankroll, avg_entry_price)
            if not bet:
                print(f"Trade {i+1}: Cannot afford bet - BUST")
                break

            # Simulate outcome
            if random.random() < win_rate:
                # Win
                bankroll += bet.net_profit_if_win
                wins += 1
            else:
                # Lose - assume 50% of losses are stopped at 50c
                if random.random() < 0.5:
                    bankroll -= bet.max_loss_with_stop
                    stopped += 1
                else:
                    bankroll -= bet.cost_dollars
                losses += 1

        total = wins + losses
        print(f"\nRESULTS:")
        print(f"Trades: {total}")
        print(f"Wins: {wins} ({wins/total*100:.1f}%)")
        print(f"Losses: {losses} (Stopped: {stopped})")
        print(f"Final Bankroll: ${bankroll:.2f}")
        print(f"Return: {(bankroll - starting_bankroll) / starting_bankroll * 100:+.1f}%")
        print(f"{'='*60}")


# Keep MartingaleCalculator as alias for backwards compatibility
# but it just uses the simple BetCalculator now
class MartingaleCalculator:
    """Backwards-compatible wrapper - now just uses simple 5% betting."""

    def __init__(self, max_consecutive_losses: int = 2):
        self.calculator = BetCalculator()
        # These are kept for compatibility but not used
        self.max_consecutive_losses = max_consecutive_losses

    def calculate_next_bet(self, bankroll: float, entry_price_cents: int):
        """Calculate bet using simple 5% rule."""
        return self.calculator.calculate_bet(bankroll, entry_price_cents)

    @property
    def is_bust(self):
        """Never bust with simple betting - just keep going."""
        return False

    @property
    def current_bet_number(self):
        """Always bet 1 - no recovery."""
        return 1

    def reset(self):
        """Nothing to reset."""
        pass

    def record_loss(self, *args, **kwargs):
        """No recovery tracking needed."""
        pass

    def record_win(self):
        """No recovery tracking needed."""
        pass

    def print_sequence(self, bankroll: float, entry_price_cents: int):
        """Print bet info."""
        self.calculator.print_bet_info(bankroll, entry_price_cents)
