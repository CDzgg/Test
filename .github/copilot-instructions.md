# Copilot Instructions for Trading Bot Project

## Project Overview
This is a **quantitative trading bot** that combines Wyckoff technical analysis with AI-powered market analysis. It integrates three key systems: Tiger Securities API (market data & trading), DeepSeek API (AI analysis), and Telegram (notifications).

**Core Architecture:**
- **[main.py](../main.py)**: Orchestrates market data fetching, AI analysis requests, and trade execution via Tiger API
- **[data_processor.py](../data_processor.py)**: Processes OHLCV candles into technical indicators (MA, MACD, RSI, ATR) and generates JSON payloads for AI analysis
- **[config.py](../config.py)**: Centralized configuration (API keys, credentials, strategy parameters)

## Critical Design Patterns

### 1. AI-Driven Decision System (Wyckoff Method)
The bot uses DeepSeek API with a sophisticated system prompt based on **Wyckoff theory** for swing trading:
- **5-Step Analysis Framework**: Context → Volume-Price → Candlestick Patterns → Indicators → Decision
- AI output must include JSON summary: `{action: BUY|SELL|WAIT, confidence: 0-100, entry: price, stop_loss: price}`
- All AI analysis is based on structured JSON from `MarketDataProcessor.get_analysis_payload()`, NOT raw market data

**Pattern**: When implementing features that call AI analysis, always:
1. Build a clean JSON payload with recent candles, indicators, and key levels
2. Include Wyckoff-specific context (trend stage, volume ratios, candlestick features)
3. Parse JSON response to extract action + confidence before trade execution

### 2. Multi-Client Integration
Three independent client objects must be initialized in `init_services()`:
- `deepseek_client`: OpenAI-compatible interface
- `tiger_client`: Quote/market data (uses `QuoteClient`)
- `tiger_trade_client`: Trade execution (uses `TradeClient`)

**Pattern**: Clients are global; always check they are initialized before use. Each has distinct error handling—Tiger failures may return None (empty data) rather than exceptions.

### 3. Data Flow: Tiger API → DataFrame → JSON Payload → DeepSeek → Decision
- **Fetching**: `tiger_client.get_bars()` returns pandas DataFrame with OHLCV
- **Processing**: `MarketDataProcessor._process_indicators()` adds MA10/20/60, MACD, RSI, Volume Ratio, ATR
- **AI Input**: `get_analysis_payload()` formats recent 5 candles + indicators as structured JSON
- **AI Output**: DeepSeek returns Markdown report + JSON summary block (`JSON_SUMMARY`)

**Important**: Always validate DataFrame length ≥60 before analysis (sufficient for 60-period indicators).

## Developer Workflows

### Running the Bot
```bash
python main.py
```
- Logs to both console (stdout) and `trade_bot.log` (UTF-8 encoded)
- Uses `logging.basicConfig(force=True)` to override third-party logging configs
- If dependencies missing, exits with clear error message

### Adding New Indicators
1. Add calculation in `MarketDataProcessor._process_indicators()` using `pandas_ta` library
2. Include in JSON payload (`get_analysis_payload()`) if AI analysis needs it
3. Update system prompt in `main.py` if changing Wyckoff analysis framework

### Debugging Market Data Issues
- Tiger API returns None for empty/invalid symbols—check `if bars is None or bars.empty`
- Symbol format: auto-detects (6-digit CN stocks → `.SH`/`.SZ`, 5-digit HK → `.HK`)
- K-line period: Use string format directly (`'60min'`, not integer) in `tiger_client.get_bars()`

## Project-Specific Conventions

### Config Management
- All sensitive data (API keys, account IDs) lives in [config.py](../config.py)
- `TIGER_PRIVATE_KEY_PATH`: Must be relative to project root; ensure `private_key.pem` exists
- `IS_SANDBOX`: Boolean for sandbox vs. production trading (affects all Trade operations)
- `PROXIES`: Set to None for direct connection, or dict with http/https for proxy routing

### Logging & Output
- Use `logger.info()`, `logger.warning()`, `logger.critical()` for structured logs
- Emoji prefixes used in logs: ✅ success, ❌ error, 🔍 searching, 🕯️ analysis, ⚠️ warning
- Console output duplicates to file (`trade_bot.log`) for audit trail

### JSON Payload Format (Critical for AI)
Always use structure from `get_analysis_payload()`:
```json
{
  "target_info": {"symbol": "...", "analysis_timeframe": "..."},
  "current_market_data": {
    "price": float,
    "indicators": {"ma20": float, "rsi": float, "macd_hist": string, "trend_structure": string}
  },
  "recent_price_action": [
    {"offset": "T-0", "close": float, "volume": int, "features": "放量,长下影"}
  ],
  "key_levels_context": {"recent_30_period_high": float, "recent_30_period_low": float}
}
```

## Integration Points & External Dependencies

### Tiger Securities API
- **Sandbox Mode**: `TigerOpenClientConfig(sandbox_debug=config.IS_SANDBOX)`
- **Quote Operations**: `tiger_client.get_bars(symbols=[...], period='60min', limit=100, right=QuoteRight.BR)`
- **Trade Operations**: Use `tiger_trade_client` (separate from quote client)
- **Returns**: DataFrame with index as datetime; check `.empty` before processing

### DeepSeek API (OpenAI-Compatible)
- Endpoint: `config.DEEPSEEK_BASE_URL` (default: https://api.deepseek.com)
- System prompt drives all analysis—modifying it changes bot behavior fundamentally
- Response parsing: Look for `JSON_SUMMARY` block at end of markdown output

### Telegram Bot Integration
- Bot token + chat IDs in [config.py](../config.py)
- Chat IDs are strings with negative prefix for groups: `"-5264761252"`
- Use for trade signals and status notifications

## Common Pitfalls to Avoid

1. **Symbol Format**: Always strip/upper input, handle with/without exchange suffix (`.SH`, `.SZ`, `.HK`)
2. **DataFrame Length**: Ensure ≥60 bars before calling indicators or AI analysis
3. **Empty Data Handling**: Tiger API returns None, not exception—check explicitly
4. **Logging Override**: `force=True` in basicConfig is essential to suppress third-party library logs
5. **Private Key Path**: Must be readable from project root; verify permissions before init
6. **System Prompt Drift**: The Wyckoff methodology is core to decisions—only modify if strategy changes fundamentally

## Key Files Reference
- **Strategy Logic**: [main.py](../main.py#L62) (system prompt defines all analysis rules)
- **Technical Indicators**: [data_processor.py](../data_processor.py#L11) (MA, MACD, RSI, ATR calculations)
- **Config & Credentials**: [config.py](../config.py)
