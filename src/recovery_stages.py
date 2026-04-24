"""
Recovery Stages Mode for Directional Stop-Loss Trading Bot

A 2-stage recovery system without stop-losses:
- Base bet at 80-92c, 5 min or less remaining
- If base loses -> Stage 1 recovery at 87c cap
- If Stage 1 loses -> Stage 2 recovery at 87c cap
- If Stage 2 loses -> Give up, reset, start fresh
- Any win resets to base bet and auto-compounds
"""

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class RecoveryStagesState:
    """Persistent state for recovery stages mode."""
    enabled: bool = False
    allocation_dollars: float = 0.0
    current_stage: int = 0  # 0=base, 1=stage1, 2=stage2
    initial_loss_cents: int = 0  # Loss from base bet in cents
    stage1_loss_cents: int = 0  # Loss from stage 1 in cents
    total_loss_cents: int = 0  # Cumulative loss to recover

    # Track the base contract count for reference
    base_contracts: int = 0

    def reset_to_base(self):
        """Reset to base bet state (after win or stage 2 loss)."""
        self.current_stage = 0
        self.initial_loss_cents = 0
        self.stage1_loss_cents = 0
        self.total_loss_cents = 0
        self.base_contracts = 0

    def advance_to_stage1(self, loss_cents: int):
        """Advance to stage 1 after base bet loss."""
        self.current_stage = 1
        self.initial_loss_cents = loss_cents
        self.total_loss_cents = loss_cents

    def advance_to_stage2(self, loss_cents: int):
        """Advance to stage 2 after stage 1 loss."""
        self.current_stage = 2
        self.stage1_loss_cents = loss_cents
        self.total_loss_cents = self.initial_loss_cents + loss_cents

    def save(self, path: Path):
        """Save state to JSON file."""
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "RecoveryStagesState":
        """Load state from JSON file."""
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                return cls(**data)
        return cls()


class RecoveryStagesCalculator:
    """
    Calculator for recovery stages betting.

    Uses Kalshi fee formula: 0.07 * price * (1 - price)

    Entry conditions:
    - Base bet: 80-92c, 5 min or less remaining
    - Recovery bets: 87c cap

    Recovery formula:
    - contracts_needed = ceil((loss_to_recover * 1.05) / net_profit_per_contract)
    - The 1.05 buffer accounts for slippage and provides small profit

    Conservative mode:
    - Stage 2 capped at 20% of bankroll
    - Base + Stage 1 sized so Stage 2 can recover them
    """

    # Entry price limits
    BASE_MIN_PRICE = 80  # cents
    BASE_MAX_PRICE = 92  # cents
    RECOVERY_MAX_PRICE = 87  # cents (cap for recovery bets)

    # Assumed slippage for calculations
    SLIPPAGE_CENTS = 1

    # Recovery buffer (5% over loss)
    RECOVERY_BUFFER = 1.05

    # Conservative mode: Stage 2 max percentage of bankroll
    CONSERVATIVE_STAGE2_PCT = 0.20

    @staticmethod
    def calc_fee(price_cents: int) -> float:
        """
        Calculate Kalshi fee per contract in dollars.
        Formula: ceil(0.07 * price * (1 - price)) rounded to nearest cent
        """
        price = price_cents / 100
        fee = 0.07 * price * (1 - price)
        return max(0.01, round(fee + 0.005, 2))

    @staticmethod
    def calc_net_profit(entry_price_cents: int) -> float:
        """
        Calculate net profit per contract in dollars.

        net_profit = (1.00 - fill_price) - fee
        """
        price = entry_price_cents / 100
        gross_profit = 1.0 - price
        fee = RecoveryStagesCalculator.calc_fee(entry_price_cents)
        return gross_profit - fee

    @staticmethod
    def calc_cost_per_contract(entry_price_cents: int) -> float:
        """Calculate cost per contract including potential slippage."""
        return entry_price_cents / 100

    def calculate_max_base_contracts(self, allocation: float) -> dict:
        """
        Calculate the maximum safe base bet size that allows both recovery stages.

        Works backwards from allocation to find base contracts that leave room
        for stage 1 and stage 2 if needed.

        Args:
            allocation: Total allocation in dollars

        Returns:
            dict with base_contracts, stage1_cost, stage2_cost, total_risk, valid
        """
        if allocation <= 0:
            return {"base_contracts": 0, "valid": False, "error": "Invalid allocation"}

        # Use worst-case entry price (88c = 87c ask + 1c slippage)
        worst_fill_price = self.RECOVERY_MAX_PRICE + self.SLIPPAGE_CENTS
        cost_per_contract = worst_fill_price / 100
        net_profit = self.calc_net_profit(worst_fill_price)
        fee = self.calc_fee(worst_fill_price)

        # Binary search for max base contracts
        # Start with a rough upper bound
        max_possible = int(allocation / cost_per_contract)

        best_valid = 0
        best_result = None

        for base_contracts in range(1, max_possible + 1):
            result = self._calculate_full_risk(base_contracts, worst_fill_price)

            if result["total_risk"] <= allocation:
                best_valid = base_contracts
                best_result = result
            else:
                break  # Once we exceed allocation, stop

        if best_valid == 0:
            return {
                "base_contracts": 0,
                "stage1_contracts": 0,
                "stage2_contracts": 0,
                "base_cost": 0,
                "stage1_cost": 0,
                "stage2_cost": 0,
                "total_risk": 0,
                "valid": False,
                "error": f"Allocation too small. Minimum ${self._min_allocation():.2f} required."
            }

        return {
            **best_result,
            "valid": True,
            "allocation": allocation,
        }

    def _calculate_full_risk(self, base_contracts: int, fill_price: int) -> dict:
        """
        Calculate total risk for a given base contract count.

        Assumes worst case: all bets fill at fill_price, all lose.
        """
        cost_per_contract = fill_price / 100
        fee = self.calc_fee(fill_price)
        net_profit = self.calc_net_profit(fill_price)

        # Base bet cost and loss
        base_cost = base_contracts * cost_per_contract
        base_loss = base_cost + (base_contracts * fee)  # Full loss + fees

        # Stage 1: recover base loss with 5% buffer
        s1_contracts = math.ceil((base_loss * self.RECOVERY_BUFFER) / net_profit)
        s1_cost = s1_contracts * cost_per_contract
        s1_loss = s1_cost + (s1_contracts * fee)

        # Stage 2: recover base + stage 1 losses with 5% buffer
        total_loss_before_s2 = base_loss + s1_loss
        s2_contracts = math.ceil((total_loss_before_s2 * self.RECOVERY_BUFFER) / net_profit)
        s2_cost = s2_contracts * cost_per_contract

        # Total risk = all three bets if all lose
        total_risk = base_cost + s1_cost + s2_cost

        return {
            "base_contracts": base_contracts,
            "stage1_contracts": s1_contracts,
            "stage2_contracts": s2_contracts,
            "base_cost": round(base_cost, 2),
            "stage1_cost": round(s1_cost, 2),
            "stage2_cost": round(s2_cost, 2),
            "total_risk": round(total_risk, 2),
            "net_profit_per_contract": round(net_profit, 2),
        }

    def _min_allocation(self) -> float:
        """Calculate minimum allocation needed for at least 1 base contract."""
        result = self._calculate_full_risk(1, self.RECOVERY_MAX_PRICE + self.SLIPPAGE_CENTS)
        return result["total_risk"]

    def calculate_recovery_bet(
        self,
        loss_to_recover: float,
        entry_price_cents: int,
    ) -> Optional[dict]:
        """
        Calculate recovery bet size to recover a loss plus buffer.

        Args:
            loss_to_recover: Total loss to recover in dollars
            entry_price_cents: Current ask price in cents

        Returns:
            dict with contracts, cost, expected_profit, or None if invalid
        """
        if loss_to_recover <= 0:
            return None

        if entry_price_cents > self.RECOVERY_MAX_PRICE:
            return None  # Price too high for recovery

        # Add slippage assumption
        fill_price = entry_price_cents + self.SLIPPAGE_CENTS
        net_profit = self.calc_net_profit(fill_price)

        if net_profit <= 0:
            return None  # No profit possible

        # Calculate contracts needed
        target_recovery = loss_to_recover * self.RECOVERY_BUFFER
        contracts = math.ceil(target_recovery / net_profit)

        if contracts < 1:
            contracts = 1

        cost = contracts * (fill_price / 100)
        expected_profit = contracts * net_profit

        return {
            "contracts": contracts,
            "cost": round(cost, 2),
            "expected_profit": round(expected_profit, 2),
            "net_recovery": round(expected_profit - loss_to_recover, 2),
            "entry_price": entry_price_cents,
            "assumed_fill": fill_price,
        }

    def validate_allocation(self, allocation: float) -> dict:
        """
        Validate if an allocation is sufficient for recovery stages mode.

        Returns detailed breakdown of what the allocation can support.
        """
        result = self.calculate_max_base_contracts(allocation)

        if not result["valid"]:
            return result

        # Add helpful info
        result["min_allocation"] = round(self._min_allocation(), 2)
        result["utilization_pct"] = round(result["total_risk"] / allocation * 100, 1)

        return result

    def calculate_conservative_base_contracts(self, bankroll: float) -> dict:
        """
        Calculate base contracts for Conservative mode.

        In Conservative mode:
        - Stage 2 is capped at 20% of bankroll
        - Base + Stage 1 must be recoverable by Stage 2

        Args:
            bankroll: Current bankroll in dollars

        Returns:
            dict with base_contracts, stage1_cost, stage2_cost, total_risk, valid
        """
        if bankroll <= 0:
            return {"base_contracts": 0, "valid": False, "error": "Invalid bankroll"}

        # Stage 2 max cost = 20% of bankroll
        stage2_max_cost = bankroll * self.CONSERVATIVE_STAGE2_PCT

        # Use worst-case fill price (88c = 87c ask + 1c slippage)
        worst_fill_price = self.RECOVERY_MAX_PRICE + self.SLIPPAGE_CENTS
        cost_per_contract = worst_fill_price / 100
        net_profit = self.calc_net_profit(worst_fill_price)
        fee_per_contract = self.calc_fee(worst_fill_price)
        loss_per_contract = cost_per_contract + fee_per_contract  # Full loss including fee

        # Max Stage 2 contracts given cost cap
        max_s2_contracts = int(stage2_max_cost / cost_per_contract)
        if max_s2_contracts < 1:
            return {
                "base_contracts": 0,
                "valid": False,
                "error": f"Bankroll too small. Need at least ${cost_per_contract / self.CONSERVATIVE_STAGE2_PCT:.2f}",
            }

        # Max loss Stage 2 can recover = s2_contracts * net_profit / buffer
        max_recoverable_loss = (max_s2_contracts * net_profit) / self.RECOVERY_BUFFER

        # Now find max base contracts where base_loss + stage1_loss <= max_recoverable
        best_valid = 0
        best_result = None

        for base_contracts in range(1, 100):
            base_cost = base_contracts * cost_per_contract
            base_loss = base_contracts * loss_per_contract

            # Stage 1 to recover base
            s1_contracts = math.ceil((base_loss * self.RECOVERY_BUFFER) / net_profit)
            s1_cost = s1_contracts * cost_per_contract
            s1_loss = s1_contracts * loss_per_contract

            # Total loss before Stage 2
            total_loss_before_s2 = base_loss + s1_loss

            # Can Stage 2 recover this?
            s2_contracts_needed = math.ceil((total_loss_before_s2 * self.RECOVERY_BUFFER) / net_profit)
            s2_cost_needed = s2_contracts_needed * cost_per_contract

            if s2_cost_needed <= stage2_max_cost:
                # Valid configuration
                total_risk = base_cost + s1_cost + s2_cost_needed
                best_valid = base_contracts
                best_result = {
                    "base_contracts": base_contracts,
                    "stage1_contracts": s1_contracts,
                    "stage2_contracts": s2_contracts_needed,
                    "base_cost": round(base_cost, 2),
                    "stage1_cost": round(s1_cost, 2),
                    "stage2_cost": round(s2_cost_needed, 2),
                    "total_risk": round(total_risk, 2),
                    "stage2_max_allowed": round(stage2_max_cost, 2),
                    "stage2_pct_of_bankroll": round(s2_cost_needed / bankroll * 100, 1),
                    "net_profit_per_contract": round(net_profit, 2),
                }
            else:
                break  # Exceeded Stage 2 cap

        if best_valid == 0:
            return {
                "base_contracts": 0,
                "stage1_contracts": 0,
                "stage2_contracts": 0,
                "base_cost": 0,
                "stage1_cost": 0,
                "stage2_cost": 0,
                "total_risk": 0,
                "valid": False,
                "error": f"Bankroll ${bankroll:.2f} too small for conservative mode. Stage 2 cap (${stage2_max_cost:.2f}) cannot recover minimum losses.",
            }

        return {
            **best_result,
            "valid": True,
            "bankroll": bankroll,
            "mode": "conservative",
        }

    def validate_conservative(self, bankroll: float) -> dict:
        """
        Validate bankroll for conservative recovery stages mode.

        Returns detailed breakdown of what the bankroll can support.
        """
        result = self.calculate_conservative_base_contracts(bankroll)

        if not result.get("valid"):
            return result

        # Add helpful info
        result["stage2_cap_pct"] = f"{self.CONSERVATIVE_STAGE2_PCT * 100:.0f}%"

        return result


def print_allocation_analysis(allocation: float):
    """Print detailed analysis of an allocation for recovery stages mode."""
    calc = RecoveryStagesCalculator()
    result = calc.validate_allocation(allocation)

    print(f"\n{'='*60}")
    print(f"RECOVERY STAGES ALLOCATION ANALYSIS")
    print(f"{'='*60}")
    print(f"Allocation: ${allocation:.2f}")
    print(f"{'='*60}")

    if not result.get("valid"):
        print(f"INVALID: {result.get('error', 'Unknown error')}")
        return

    print(f"\nBET SIZING (worst case at 88c fill):")
    print(f"  Base bet:   {result['base_contracts']} contracts @ ${result['base_cost']:.2f}")
    print(f"  Stage 1:    {result['stage1_contracts']} contracts @ ${result['stage1_cost']:.2f}")
    print(f"  Stage 2:    {result['stage2_contracts']} contracts @ ${result['stage2_cost']:.2f}")
    print(f"  Total risk: ${result['total_risk']:.2f}")
    print(f"\nVALIDATION:")
    print(f"  Utilization: {result['utilization_pct']:.1f}% of allocation")
    print(f"  Minimum allocation: ${result['min_allocation']:.2f}")
    print(f"  Status: VALID - Both recovery stages can be funded")
    print(f"{'='*60}")


if __name__ == "__main__":
    # Test with various allocations
    for alloc in [10, 25, 50, 100, 200]:
        print_allocation_analysis(alloc)
