# XAU/XAG WR60 Trend Pullback Framework (TradingView)

File: `scripts/tradingview/xau_xag_wr60_framework.pine`

## Quick start
1. Open TradingView -> Pine Editor.
2. Paste code from `xau_xag_wr60_framework.pine`.
3. Click **Add to chart** on `XAUUSD` or `XAGUSD`.
4. Set chart timeframe to `15m`.
5. Keep `Trend Timeframe` at `60` (H1).

## Default logic
- Trend filter: H1 `EMA50/EMA200`.
- Entry: pullback to EMA20 on chart TF + RSI(2) extreme + reversal candle.
- Exit: SL = `1.0 x ATR(14)`, TP = `1.2 x ATR(14)`.
- Daily stop: no new entries if daily loss reaches `2%`.

## Important settings
- `Risk % per Trade`: default `0.5`.
- `Use Risk-Based Position Sizing`: on by default.
- `Use Manual News Window Filter`: on by default.

## News filter
TradingView strategy cannot auto-read economic calendar directly in Pine.
Use `News Event #1..#5` to input important news times manually each week.
Recommended events: US CPI, NFP, FOMC rate decision, Powell speech.
