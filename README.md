# Directional Stop-Loss Strategy Bot

Automated trading bot for Kalshi 15-minute BTC prediction markets.

## Strategy

Based on backtesting 503 trades over 17 days:
- **Win Rate**: 57.5%
- **Return**: +477%
- **Entry**: 60-85c
- **Stop Loss**: 50c

### Rules

1. **WAIT** - 10 minutes into window (5 min remaining)
2. **CHECK DIRECTION** - BTC > Strike = YES, BTC < Strike = NO
3. **CHECK PRICE** - Only enter if 60c <= ask <= 85c
4. **BET** - 5% of bankroll
5. **STOP LOSS** - Exit if bid drops to 50c
6. **HOLD** - Otherwise hold to expiry

## Setup

### Environment Variables

```bash
# Required - Kalshi API
KALSHI_API_KEY=your_api_key
KALSHI_PRIVATE_KEY=your_private_key

# Optional
STARTING_BANKROLL=100.00      # Starting amount (default: full balance)
BET_PERCENTAGE=0.05           # Bet size (default: 5%)
MIN_ENTRY_PRICE=60            # Min entry (default: 60c)
MAX_ENTRY_PRICE=85            # Max entry (default: 85c)
STOP_LOSS_PRICE=50            # Stop loss (default: 50c)
AUTO_COMPOUND=true            # Auto-compound wins
PORT=8081                     # Dashboard port
DASHBOARD_PASS=secret         # Dashboard password (optional)
```

### Local Run

```bash
# Install dependencies
pip install requests cryptography

# Test connection
python main.py test

# Run bot
python main.py run
```

### Railway Deployment

1. Create new project on Railway
2. Connect GitHub repo
3. Add environment variables:
   - `KALSHI_API_KEY`
   - `KALSHI_PRIVATE_KEY`
   - `STARTING_BANKROLL` (e.g., 100)
   - `DASHBOARD_PASS` (for security)
4. Deploy

The dashboard will be available at your Railway URL.

## Dashboard

- **START/STOP** - Control trading
- **Bankroll** - Set starting amount and enable auto-compound
- **Live Markets** - See current 15-min BTC markets
- **Activity Log** - Real-time updates
- **Recent Trades** - Trade history with P&L

## Files

- `main.py` - Entry point + dashboard
- `src/trader.py` - Trading logic with stop loss
- `src/market_scanner.py` - Direction checking + opportunity finding
- `src/bet_calculator.py` - 5% bet sizing
- `src/kalshi_client.py` - Kalshi API
- `src/kraken.py` - BTC price from Kraken

## How It Works

1. Scanner polls 15-min BTC markets every 300ms
2. When market has 5 min remaining, checks BTC vs strike
3. If BTC > Strike, bet YES; if BTC < Strike, bet NO
4. Only enters if favored side ask is 60-85c
5. After entry, monitors bid price every 1s
6. If bid drops to 50c, exits immediately
7. Otherwise holds to expiry for settlement

## Risk

- **Stop Loss** limits individual trade loss to ~20c/contract
- **5% betting** prevents single trade from destroying bankroll
- **Direction filter** only bets with momentum

## Expected Returns

At 70c average entry with 57.5% win rate:
- Win: +30c/contract (43% return)
- Stop: -20c/contract (29% loss)
- Expected per trade: +12%

With 5% betting and compounding:
- $100 -> $577 over 503 trades (+477%)
