"""
Configuration management for the 15-minute trading strategy.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class TradingConfig:
    """Trading strategy configuration - Directional Stop-Loss Strategy."""

    # Entry criteria - Directional Stop-Loss Strategy
    min_entry_price: int = 60  # cents - minimum entry price
    max_entry_price: int = 85  # cents - maximum entry price
    optimal_min_price: int = 65  # sweet spot
    optimal_max_price: int = 80  # sweet spot

    # Stop Loss
    stop_loss_price: int = 50  # cents - exit if bid drops to this level

    # Timing
    wait_minutes: int = 10  # wait 10 minutes into 15-min window (5 min remaining)
    window_duration: int = 15  # 15-minute windows

    # Risk management - NO RECOVERY, simple 5% bet
    bankroll_bet_percentage: float = 0.05  # 5% of bankroll per bet

    # Order execution
    limit_order_offset: int = 1  # place limit 1c above ask
    use_market_orders: bool = False  # True = market orders (guaranteed fill), False = limit orders

    # Bankroll management
    starting_bankroll: float = None  # None = use full Kalshi balance
    auto_compound: bool = True  # Automatically increase bet size after wins


@dataclass
class KalshiConfig:
    """Kalshi API configuration."""

    api_key_id: str = ""
    private_key_path: str = ""
    private_key_base64: str = ""

    # API endpoints
    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    demo_url: str = "https://demo-api.kalshi.co/trade-api/v2"

    environment: str = "production"  # or "demo"

    @property
    def api_url(self) -> str:
        if self.environment == "demo":
            return self.demo_url
        return self.base_url


@dataclass
class AppConfig:
    """Main application configuration."""

    trading: TradingConfig
    kalshi: KalshiConfig

    # Paths
    base_dir: Path = Path(__file__).parent.parent
    logs_dir: Path = None
    data_dir: Path = None

    # State
    starting_bankroll: float = 250.0
    target_profit_per_trade: float = 1.0

    def __post_init__(self):
        self.logs_dir = self.base_dir / "logs"
        self.data_dir = self.base_dir / "data"
        self.logs_dir.mkdir(exist_ok=True)
        self.data_dir.mkdir(exist_ok=True)


def load_config() -> AppConfig:
    """Load configuration from environment variables."""

    trading = TradingConfig(
        min_entry_price=int(os.getenv("MIN_ENTRY_PRICE", "60")),
        max_entry_price=int(os.getenv("MAX_ENTRY_PRICE", "85")),
        stop_loss_price=int(os.getenv("STOP_LOSS_PRICE", "50")),
        bankroll_bet_percentage=float(os.getenv("BET_PERCENTAGE", "0.05")),
        starting_bankroll=float(os.getenv("STARTING_BANKROLL")) if os.getenv("STARTING_BANKROLL") else None,
        auto_compound=os.getenv("AUTO_COMPOUND", "true").lower() == "true",
        use_market_orders=os.getenv("USE_MARKET_ORDERS", "false").lower() == "true",
    )

    kalshi = KalshiConfig(
        api_key_id=os.getenv("KALSHI_API_KEY_ID", ""),
        private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", ""),
        private_key_base64=os.getenv("KALSHI_PRIVATE_KEY_BASE64", ""),
        environment=os.getenv("KALSHI_ENV", "production"),
    )

    return AppConfig(
        trading=trading,
        kalshi=kalshi,
        starting_bankroll=float(os.getenv("STARTING_BANKROLL", "250")),
        target_profit_per_trade=float(os.getenv("TARGET_PROFIT_PER_TRADE", "1.0")),
    )
