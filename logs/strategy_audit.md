# 🕵️ Strategy Audit & EV Analysis: XAUUSDm
**Generated:** 2026-02-12T12:06:07.253783

## 🔥 Heatmap: Expected Value (EV) per Regime
EV = Average Profit per Trade (USD). Negative EV means you are *paying* to trade this setup.

| Strategy | Regime | Trades | Win% | EV ($) | Profit Factor | Status |
|---|---|---|---|---|---|---|
| **BOT_888999** | RANGING | 16 | 62.5% | 🔴 -0.59 | 0.59 | ❌ TOXIC (Negative EV) |
| **BOT_888999** | TRENDING_UP | 7 | 100.0% | **1.18** | 8.27 | ✅ Healthy |
| **BOT_999111** | RANGING | 1 | 0.0% | 🔴 -5.00 | 0.00 | ❌ TOXIC (Negative EV) |
| **BOT_999112** | RANGING | 1 | 0.0% | 🔴 -4.03 | 0.00 | ❌ TOXIC (Negative EV) |
| **MANUAL** | RANGING | 15 | 73.3% | **0.44** | 1.70 | ✅ Healthy |
| **MANUAL** | TRENDING_DOWN | 6 | 100.0% | **1.64** | 9.86 | ✅ Healthy |
| **MANUAL** | TRENDING_UP | 5 | 60.0% | 🔴 -0.99 | 0.61 | ❌ TOXIC (Negative EV) |

## ☢️ Toxic Combinations (Recommend Blocking)
- BLOCK `BOT_888999` in `RANGING`
- BLOCK `BOT_999111` in `RANGING`
- BLOCK `BOT_999112` in `RANGING`
- BLOCK `MANUAL` in `TRENDING_UP`