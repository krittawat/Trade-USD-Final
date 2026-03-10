derb.darb
derb.darb
Online

This is the start of the #trade private channel. 
derb.darb — 26-02-26 6:54 PM
จากเป้าหมายของคุณที่ต้องการ "กำไร 600 - 2,000 บาทต่อวันแบบเสถียร ถอนเงินได้ทุกวัน และเน้นปกป้องทุนสูงสุดระดับสถาบันการเงิน" ระบบ Antigravity V2 ของเราในตอนนี้มีโครงสร้าง Risk Management และ AI พื้นฐานที่แน่นหนามากแล้ว

แต่ถ้าต้องการยกระดับ (Upgrade) เพื่อให้บอทมีความเฉียบคม มั่นคงขั้นสุด และลด Drawdown ลงไปอีก นี่คือ "5 โมดูลเสริมระดับ Advance (Next-Level)" ที่น่าสนใจและเราสามารถพัฒนาต่อได้ทันทีครับ:

1. 🛡️ Portfolio Correlation Guard (ระบบป้องกันความเสี่ยงพอร์ตโดยรวม)
ปัญหาปัจจุบัน: ตอนนี้บอทเทรด XAUUSD, XAGUSD, BTCUSD, USDJPY แยกกันอิสระ สมมติบอทเปิด BUY ทองคำ และ BUY แร่เงิน พร้อมกัน ถ้าเงินดอลลาร์แข็งค่าอย่างรุนแรง พอร์ตจะโดนลาก (Drawdown) ทั้ง 2 ออเดอร์แบบคูณสอง สิ่งที่จะทำ: โมดูลนี้จะคำนวณ "ความผูกพัน (Correlation)" แบบ Real-time เช่น ตรวจจับได้ว่า XAU กับ XAG วิ่งทิศเดียวกันกว่า 85% หากบอทเปิด BUY XAU ไปแล้ว ระบบโมดูลนี้จะ Block ไม่ให้เปิดตำแหน่ง BUY ใน XAG เพิ่มเติมเพื่อป้องกัน Over-exposure แต่จะอนุญาตให้เปิด USDJPY ได้แทน เพราะความเสี่ยงไม่ได้ผูกกัน

message.txt
8 KB
derb.darb — 12:25 AM
SYSTEM ROLE:
You are OPUS – Ghost Protocol AI, an aggressive smart-money hunter.
Your mission is to extract profit from manipulation, liquidity sweeps, and volatility expansion
in XAUUSD, XAGUSD, BTCUSD.

You must be ruthless on entries and even more ruthless on risk controls.

message.txt
6 KB
derb.darb — 2:05 AM
# MASTER PROMPT — GOOGLE ANTIGRAVITY AI TRADING SYSTEM (PRODUCTION-GRADE, REAL MONEY SAFE)

ROLE:
You are **Google Antigravity** — Senior Quant Engineer, Trading Risk Architect, AI Systems Engineer, and Production DevOps Lead.

MISSION:

message.txt
17 KB
# SYSTEM PRE-PROMPT: ANTIGRAVITY TRADING CO-PILOT

## 1. CORE IDENTITY & DIRECTIVES
* **Role:** Professional trading co-pilot and strict risk guardian.
* **Priority 1:** Capital preservation.
* **Priority 2:** Consistent profitability.

message.txt
3 KB
derb.darb — 4:37 PM
ร้าง Handoff Prompt อัปเดตใหม่เสร็จแล้วครับ ที่ การติดตั้ง/HANDOFF_PROMPT.md

สิ่งที่อัปเดต:

✅ แก้ path ให้ถูกต้อง → trader/ (ไม่ใช่ backend/)
✅ เพิ่ม Pipeline Flow ครบ 9 ขั้นตอน (Ingest → Feature → Regime → Liquidity → Pattern → Strategy → Risk → Execute → Position Mgmt)
✅ บันทึกผล Live Gate Test ล่าสุด (WR=20%, PF=0.37 — ❌ ยังไม่ผ่าน)
✅ จัดลำดับ TODO ตาม priority: 🔴 Critical / 🟡 Important / 🟢 Nice-to-have
✅ แก้คำสั่งรันให้ใช้ python -m trader.main (module-based import)
✅ เพิ่ม Risk Parameters จาก settings.json จริง
✅ เพิ่ม Signal Output Format
Priority แรกที่ต้องทำ: ปรับปรุง Win Rate + Profit Factor ให้ผ่าน Live Gate (WR≥50%, PF≥1.3) ก่อนเปิด LIVE
# 🚀 Handoff Prompt — OPUS Trading Engine
# ใช้ Prompt นี้เมื่อเริ่ม Conversation ใหม่กับ AI เพื่อพัฒนาต่อ
# อัปเดตล่าสุด: 2026-03-02 16:33 (ICT)

---

HANDOFF_PROMPT.md
15 KB
# ⚡ Quick Start — ติดตั้งเร่งด่วน (5 นาที)

> ฉบับย่อสำหรับคนที่เปิดหน้าคู่มือเต็มแล้วรู้สึกยาว
> คู่มือเต็ม: ดู [README.md](README.md)
> **อัปเดต:** 2026-03-02 — OPUS Trading Engine v3.0

QUICK_START.md
3 KB
# 📦 คู่มือติดตั้ง OPUS Trading Engine บนเครื่องใหม่

> **เวอร์ชัน:** v3.0 — อัปเดตล่าสุด: 2026-03-02
>
> **Engine:** OPUS Trading Engine (Smart Money + Liquidity Hunting)
>

README.md
22 KB
﻿
# 🚀 Handoff Prompt — OPUS Trading Engine
# ใช้ Prompt นี้เมื่อเริ่ม Conversation ใหม่กับ AI เพื่อพัฒนาต่อ
# อัปเดตล่าสุด: 2026-03-02 16:33 (ICT)

---

## Copy ข้อความด้านล่างนี้ ใส่เป็น Prompt แรกให้ AI:

---

```
ระบบ: OPUS Trading Engine — Production-grade MT5 Bot (Smart Money + Liquidity Hunting)
Project path: d:\VibeCode\Trade
อ่าน GEMINI.md ที่ root → เป็น Master Prompt (single source of truth)

═══════════════════════════════════════════════════════════
สถาปัตยกรรมปัจจุบัน (2 มี.ค. 2026)
═══════════════════════════════════════════════════════════

1. Engine: OPUS Trading Engine อยู่ใน trader/ (ไม่ใช่ backend/)
   - backend/ คือ legacy code เก่า (ยังอยู่แต่ไม่ใช้งาน)
   - trader/ คือตัวหลักที่ใช้รันจริง
2. Entry point: python trader/main.py --mode live|dry_run|backtest
3. DB: SQLite embedded — trader/data/opus.db
4. Account: Exness Cent (USC)
5. Symbols: XAUUSDc, XAGUSDc, BTCUSDc (mapping ใน trader/data/mapper.py)
6. Timeframe: M5 (5 นาที), ดึง 200 bars ต่อ cycle
7. Cycle interval: 10 วินาที
8. Config: trader/config/settings.json (risk, regime, liquidity params)

═══════════════════════════════════════════════════════════
โครงสร้าง trader/ MODULE (ทุกไฟล์ import ด้วย "from trader.*")
═══════════════════════════════════════════════════════════

trader/
├─ main.py               ← Entry point: tick_cycle loop per symbol
├─ config/
│  └─ settings.json      ← Risk limits, regime thresholds, session hours, symbol mapping
│
├─ strategy/              ← Trading Strategies
│  ├─ selector.py         ← Strategy selector (regime-based routing)
│  ├─ trend_killer.py     ← Smart Money: Structure HL/LH + Displacement + Candle Patterns
│  └─ liquidity_hunter.py ← Liquidity Sweep: EQH/EQL sweep + Displacement confirmed
│
├─ features/              ← Feature Engineering
│  ├─ volatility.py       ← ATR, vol_ratio, vol_spike, vol_dryup, compression_ratio, net_power
│  ├─ structure.py        ← Market structure: HH/HL/LH/LL, BOS + detect_displacement()
│  └─ candle_patterns.py  ← Engulfing, Pin Bar, 3 Soldiers/Crows, Morning/Evening Star
│
├─ regime/classifier.py   ← Market regime: Strong/Weak Trend (Up/Down), Distribution,
│                            Accumulation, Sideways, Vol Compression, Vol Expansion
│
├─ liquidity/detector.py  ← EQH/EQL detection, sweep events, displacement, reacceptance
│
├─ risk/gate.py            ← Pre-Trade Gate: SL mandatory, consecutive losses, daily loss,
│                            daily target, spread check, news filter (stub)
│
├─ execution/
│  ├─ mt5_order.py        ← MT5 order sender (mode-aware: LIVE/DRY_RUN/BACKTEST)
│  └─ position_manager.py ← ATR trailing stop, anti-stophunt SL (3x ATR),
│                            multi-tier profit locking (0.5R/1R→20%, 2R→50%, 3R→70%)
│
├─ data/
│  ├─ fetcher.py          ← MT5 data fetcher (get_rates with rate limiting)
│  ├─ mapper.py           ← Symbol mapping: XAUUSD → XAUUSDc (Cent suffix)
│  ├─ time_utils.py       ← UTC/TH time helpers, session detection
│  └─ opus.db             ← SQLite database (incidents, trades, signals tables)
│
├─ storage/sqlite_db.py   ← SQLite CRUD: DataStore class
│                            Tables: incidents, trades, signals
│
├─ observability/logger.py ← Structured JSON logging
│
└─ scripts/
   ├─ qc_suite.py         ← Quality Control test suite
   ├─ live_gate.py        ← Live signal gate tester (backtest → approve/reject LIVE)
   ├─ run_backtest.py     ← Backtest runner
   ├─ smoke_test_order.py ← Order smoke test
   └─ debug_vol.py        ← Volatility debugging

═══════════════════════════════════════════════════════════
Pipeline การเทรด (main.py → tick_cycle)
═══════════════════════════════════════════════════════════

1. Ingest: fetcher.get_rates(symbol, M5, 200 bars)
2. Feature Engineering:
   - add_volatility_features(df)    → ATR, vol_ratio, compression, net_power
   - add_structure_features(df)     → HH/HL/LH/LL structure labels
   - detect_displacement(df)        → displacement_up / displacement_down
   - detect_candle_patterns(df)     → Engulfing, Pin, Star, Soldiers/Crows
3. Regime Classification:
   - classify_regime(df, config) → regime name + confidence
4. Liquidity Events:
   - detect_liquidity_events(df, config) → SWEEP / DISPLACEMENT_CONFIRMED
5. Pattern Signal:
   - get_pattern_signal(latest) → BUY/SELL direction + strength
6. Strategy Selection (selector.py):
   - Compression → ไม่เทรด
   - Extreme Expansion (>2.5x baseline) → ไม่เทรด
   - Trend regimes → Trend Killer primary, Liquidity Hunter fallback
   - Sideways/Distribution/Accumulation → Liquidity Hunter primary, TK fallback
7. Risk Gate:
   - SL mandatory, consecutive losses check, daily loss cap, spread check
   - Position count limit (max 2 per symbol)
8. Execution:
   - Dynamic lot sizing (2% equity risk → lot)
   - MT5 order placement via Executor
9. Position Management (LIVE mode only):
   - ATR trailing stop + anti-stophunt
   - Multi-tier profit locking

═══════════════════════════════════════════════════════════
Signal Output Format
═══════════════════════════════════════════════════════════
{
  "symbol": "XAUUSD",
  "side": "BUY|SELL",
  "entry_type": "MARKET",
  "entry_price": float,
  "sl": float,                    // entry ± (ATR × 1.2)
  "tp1": float, "tp2": float, "tp3": float,  // 1.5R / 3.5R / 5.0R
  "rationale": ["reason1", ...],
  "confidence": 0.0-1.0,
  "model": "TREND|LIQUIDITY"
}

═══════════════════════════════════════════════════════════
Risk Parameters (settings.json)
═══════════════════════════════════════════════════════════
- max_risk_per_trade_percent: 2%
- max_daily_loss_percent: 6%
- max_consecutive_losses: 3
- daily_target_currency: 500 USC
- max_positions_per_symbol: 2 (hardcoded in main.py)
- SL: entry ± (ATR × 1.2)
- TP: 1.5R / 3.5R / 5.0R (3 tiers)
- Max spread: XAUUSD=300, XAGUSD=400, BTCUSD=5000 points

═══════════════════════════════════════════════════════════
สิ่งที่พัฒนาแล้วจนถึงวันนี้ (2 มี.ค. 2026)
═══════════════════════════════════════════════════════════

✅ OPUS Engine Architecture — เสร็จ
   - Single entry point, clean module structure
   - SQLite embedded (opus.db) with schema: incidents, trades, signals

✅ Candlestick Pattern Integration — เสร็จ
   - candle_patterns.py: detect Engulfing, Pin Bar, 3 Soldiers/Crows, Morning/Evening Star
   - Integrated into trend_killer as Trigger 2 (strength ≥ 0.3, trend regime only)
   - Pattern signal logged in cycle output

✅ Strategy Selector — เสร็จ
   - Regime-based routing: Trend Killer vs Liquidity Hunter
   - Hard blocks on Compression + Extreme Expansion

✅ Position Manager — เสร็จ
   - ATR-based trailing stop (anti-stophunt: 3x ATR SL)
   - Multi-tier profit locking (0.5R/1R/2R/3R thresholds)

✅ Risk Gate — เสร็จ (basic)
   - SL mandatory, consecutive losses, daily loss, spread check
   - News filter = stub (not implemented)

═══════════════════════════════════════════════════════════
ปัญหาที่พบจาก Live Gate Test (ล่าสุด)
═══════════════════════════════════════════════════════════

⚠️ Live Gate NOT APPROVED — ยังไม่ผ่านเกณฑ์:
   - Win Rate: 20% (ต้องการ ≥ 50%)
   - Profit Factor: 0.37 (ต้องการ ≥ 1.3)
   - Max Drawdown: 0.42% (ผ่าน ≤ 6%)
   - Total trades: 5, Wins: 1, Losses: 4, Net PnL: -21.13 USC

═══════════════════════════════════════════════════════════
สิ่งที่ต้องทำต่อ (Priority สูง → ต่ำ)
═══════════════════════════════════════════════════════════

🔴 CRITICAL (ต้องแก้ก่อนเปิด LIVE):
1. ปรับปรุง Win Rate + Profit Factor — ปัจจุบันขาดทุน
   - รัน backtest ยาว 1 ปี (XAUUSD, BTCUSD, XAGUSD) เพื่อหา optimal parameters
   - Tune regime thresholds (trend_threshold=0.45 อาจต่ำเกินไป)
   - ปรับ candle pattern strength threshold (0.3 อาจต่ำเกินไป)
   - ปรับ SL/TP multipliers ตาม backtest
2. ทำ strategy validation ด้วย Live Gate test ให้ผ่าน (WR≥50%, PF≥1.3)

🟡 IMPORTANT:
3. เพิ่ม Fair Value Gap (FVG) detection ใน features/ — สร้าง confluence filter เพิ่ม
4. เพิ่ม News Filter จริง ใน risk/gate.py (ตอนนี้เป็น stub)
5. เพิ่ม Session Filter (allow trade in specific sessions only)
6. เพิ่ม strategy ใหม่: mean-reversion สำหรับ Accumulation/Distribution regime
7. ทดสอบ DRY_RUN ≥ 30 นาที ก่อนเปิด LIVE

🟢 NICE TO HAVE:
8. พัฒนา AI Brain module สำหรับ adaptive parameter tuning
9. เพิ่ม Dashboard (Nuxt/Streamlit) ดูสถานะ real-time
10. ปรับ Position Manager: partial TP (close 30% at TP1, 30% at TP2, rest at TP3)
11. Train regime classifier ด้วยข้อมูลเพิ่ม
12. Cleanup: ลบ backend/ legacy code ที่ไม่ใช้แล้ว
13. ตรวจ trade journal ใน opus.db ว่า log ครบถ้วน

═══════════════════════════════════════════════════════════
คำสั่งรัน
═══════════════════════════════════════════════════════════
- Activate venv: cd D:\VibeCode\Trade && .\.venv\Scripts\Activate.ps1
- LIVE:      python -m trader.main --mode live
- DRY_RUN:   python -m trader.main --mode dry_run
- Backtest:  python -m trader.scripts.run_backtest --symbol XAUUSD --start 2025-01-01 --end 2025-12-31
- QC:        python -m trader.scripts.qc_suite
- Live Gate: python -m trader.scripts.live_gate --run-fresh --symbol XAUUSD --bars 25920
- Smoke:     python -m trader.scripts.smoke_test_order

═══════════════════════════════════════════════════════════
IMPORTANT RULES
═══════════════════════════════════════════════════════════
- ห้าม Hard Code: dynamic code เท่านั้น, Config ทุกอย่างใน settings.json
- ห้ามลบ Risk Gate: ต้องมี SL ทุก position
- Default mode = DRY_RUN เสมอ (ไม่ใช่ LIVE)
- Capital preservation first, then profitability
- All imports ใช้ "from trader.*" (ไม่ใช่ "from backend.*")
- บันทึกทุกอย่างลง SQLite (opus.db)

กรุณาอ่าน GEMINI.md ก่อนเริ่มงานทุกครั้ง
```

---

## หมายเหตุ

- ทุกครั้งที่เริ่ม conversation ใหม่ ให้ copy prompt ด้านบนใส่เป็นข้อความแรก
- AI จะอ่าน GEMINI.md แล้วเข้าใจบริบทระบบทั้งหมด
- ถ้าต้องการให้ AI ทำงานเฉพาะ ให้เพิ่มคำสั่งต่อท้าย เช่น:
  - "ช่วย optimize strategy parameters ให้ผ่าน Live Gate (WR≥50%, PF≥1.3)"
  - "รัน backtest XAUUSD 1 ปี แล้ว tune SL/TP multipliers"
  - "เพิ่ม FVG detection ใน features/"
  - "เพิ่ม News Filter + Session Filter ใน risk/gate.py"
  - "แก้ bug ที่ position_manager ไม่ move SL to BE"
  - "เพิ่ม strategy ใหม่สำหรับ sideways regime"
  - "สร้าง Dashboard แสดง trade journal real-time"
  - "ลบ backend/ legacy code แล้ว refactor ให้ clean"