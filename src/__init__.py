"""
Directional Stop-Loss Trading Strategy
15-Minute BTC Prediction Markets
"""

from .config import AppConfig, TradingConfig, KalshiConfig, load_config
from .auth import KalshiAuth, generate_key_pair
from .kalshi_client import KalshiClient, MarketData, OrderResponse
from .kraken import KrakenClient
from .market_scanner import MarketScanner, TradingOpportunity
from .bet_calculator import BetCalculator, BetCalculation, MartingaleCalculator
from .trade_executor import TradeExecutor, TradeRecord, TradeStatus
from .trader import Trader, TradingState
from .recovery_stages import RecoveryStagesState, RecoveryStagesCalculator

__all__ = [
    "AppConfig",
    "TradingConfig",
    "KalshiConfig",
    "load_config",
    "KalshiAuth",
    "generate_key_pair",
    "KalshiClient",
    "KrakenClient",
    "MarketData",
    "OrderResponse",
    "MarketScanner",
    "TradingOpportunity",
    "BetCalculator",
    "BetCalculation",
    "MartingaleCalculator",  # Backwards compatibility
    "TradeExecutor",
    "TradeRecord",
    "TradeStatus",
    "Trader",
    "TradingState",
    "RecoveryStagesState",
    "RecoveryStagesCalculator",
]
