# 📦 คู่มือติดตั้ง OPUS Trading Engine บนเครื่องใหม่

> **เวอร์ชัน:** v3.0 — อัปเดตล่าสุด: 2026-03-02
>
> **Engine:** OPUS Trading Engine (Smart Money + Liquidity Hunting)
>
> **DB Stack:** SQLite (embedded) — ไฟล์ `backend/trader/data/opus.db`
>
> ⚡ ฉบับย่อ: ดู [QUICK_START.md](QUICK_START.md)

---

## สารบัญ

1. [ข้อกำหนดเครื่อง](#1-ข้อกำหนดเครื่อง)
2. [ซอฟต์แวร์ที่ต้องติดตั้ง](#2-ซอฟต์แวร์ที่ต้องติดตั้ง)
3. [ดาวน์โหลดโปรเจค](#3-ดาวน์โหลดโปรเจค)
4. [ติดตั้ง Python Environment](#4-ติดตั้ง-python-environment)
5. [ตั้งค่า Environment (.env)](#5-ตั้งค่า-environment-env)
6. [ตั้งค่า MetaTrader 5](#6-ตั้งค่า-metatrader-5)
7. [เริ่มระบบ (Startup)](#7-เริ่มระบบ-startup)
8. [ตรวจสอบระบบ (Verification)](#8-ตรวจสอบระบบ-verification)
9. [โครงสร้างไดเรกทอรี](#9-โครงสร้างไดเรกทอรี)
10. [คำสั่งที่ใช้บ่อย](#10-คำสั่งที่ใช้บ่อย)
11. [แก้ปัญหาที่พบบ่อย](#11-แก้ปัญหาที่พบบ่อย)
12. [Safety Checklist ก่อนเปิด LIVE](#12-safety-checklist-ก่อนเปิด-live)

---

## 1. ข้อกำหนดเครื่อง

| รายการ | ขั้นต่ำ | แนะนำ |
|--------|---------|-------|
| **OS** | Windows 10 (64-bit) | Windows 11 |
| **RAM** | 8 GB | 16 GB+ |
| **CPU** | 4 Cores | 8 Cores |
| **พื้นที่ว่าง** | 2 GB | 5 GB+ |
| **อินเทอร์เน็ต** | จำเป็น (ต่อ MT5 Broker) | เสถียร / สาย LAN |

> [!IMPORTANT]
> ระบบถูกออกแบบสำหรับเครื่อง **8 GB RAM** — ใช้ SQLite embedded เท่านั้น
> ไม่ต้องรัน service ภายนอก (ไม่มี QuestDB, Redis, Java)

---

## 2. ซอฟต์แวร์ที่ต้องติดตั้ง

### 2.1 Python 3.11+

1. ดาวน์โหลดจาก https://www.python.org/downloads/
2. **ตอนติดตั้ง ✅ ติ๊ก "Add Python to PATH"** ← สำคัญมาก!
3. ตรวจ:
   ```powershell
   python --version   # ต้องได้ 3.11.x หรือสูงกว่า
   pip --version
   ```

### 2.2 Git (แนะนำ)

1. ดาวน์โหลดจาก https://git-scm.com/download/win
2. ตรวจ:
   ```powershell
   git --version
   ```

### 2.3 MetaTrader 5 Terminal

1. ดาวน์โหลดจาก Broker (เช่น Exness) หรือ https://www.metatrader5.com/en/download
2. **เปิดโปรแกรม → ล็อกอินให้เรียบร้อย** ก่อนรันระบบ
3. จดเส้นทาง `terminal64.exe` (ปกติ: `C:\Program Files\MetaTrader 5\terminal64.exe`)

> [!NOTE]
> MT5 ต้อง **เปิดอยู่ตลอด** ขณะรันระบบ — Python ใช้ MT5 API ผ่าน terminal

### สรุปตาราง

| ซอฟต์แวร์ | เวอร์ชัน | ใช้ทำอะไร |
|-----------|----------|-----------|
| Python | 3.11+ | OPUS Trading Engine, Risk Gate, Strategies |
| Git | ล่าสุด | Clone โปรเจค |
| MetaTrader 5 | 5.x | เชื่อมต่อ Broker / เทรดจริง |

> [!TIP]
> **ไม่ต้องติดตั้ง Node.js, Java, หรือ service อื่น** — OPUS Engine เป็น Python ล้วน

---

## 3. ดาวน์โหลดโปรเจค

### วิธีที่ 1: Git Clone (แนะนำ)

```powershell
cd D:\VibeCode
git clone <URL-ของ-Repository> Trade
cd Trade
```

### วิธีที่ 2: Copy โฟลเดอร์

คัดลอกทั้งโปรเจค → เครื่องใหม่

| โฟลเดอร์ | จำเป็น? | คำอธิบาย |
|----------|---------|----------|
| `backend/` | ✅ ต้องมี | โค้ด Python ทั้งหมด (OPUS Engine) |
| `การติดตั้ง/` | ✅ ต้องมี | คู่มือ + setup script |
| `.env.example` | ✅ ต้องมี | ตัวอย่างค่าตั้ง |
| `GEMINI.md` | ✅ ต้องมี | Master Prompt สำหรับ AI |
| `.env` | ⚠️ **อย่า** คัดลอก | สร้างใหม่จาก .env.example (มีรหัสผ่าน) |
| `backend/trader/data/opus.db` | 🔶 ถ้าต้องการ | SQLite DB เดิม (trade journal, stats) |
| `.venv/` | ❌ ไม่ต้อง | สร้าง venv ใหม่ |
| `backend/logs/` | ❌ ไม่ต้อง | Log เก่า |

> [!CAUTION]
> **ห้ามคัดลอกไฟล์ `.env` ผ่านช่องทางไม่ปลอดภัย** (email, chat)
> ให้สร้างใหม่จาก `.env.example` แล้วกรอกรหัสผ่านเอง

---

## 4. ติดตั้ง Python Environment

### วิธี Auto (แนะนำ)

```powershell
cd D:\VibeCode\Trade\การติดตั้ง
.\setup_new_machine.bat
# สคริปต์จะทำทุกอย่างให้อัตโนมัติ
```

### วิธี Manual

#### 4.1 สร้าง Virtual Environment

```powershell
cd D:\VibeCode\Trade

# สร้าง venv
python -m venv .venv

# เปิดใช้ venv
.\.venv\Scripts\Activate.ps1
```

> [!NOTE]
> ถ้าเจอ error เรื่อง Execution Policy:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

#### 4.2 ติดตั้ง Dependencies

```powershell
# ตรวจว่า venv เปิดอยู่ (จะเห็น (.venv) หน้า prompt)
pip install MetaTrader5 pandas pandas-ta numpy pydantic pydantic-settings python-dotenv httpx duckdb scikit-learn joblib
```

**Packages หลักที่จะถูกติดตั้ง:**

| Package | ใช้ทำอะไร |
|---------|-----------|
| `MetaTrader5` | เชื่อมต่อ MT5 Terminal (Windows เท่านั้น) |
| `pandas` + `pandas-ta` | Data + Technical Analysis (EMA, RSI, ATR) |
| `numpy` | คำนวณตัวเลข |
| `pydantic` + `pydantic-settings` | Data validation + .env config |
| `python-dotenv` | โหลดไฟล์ .env |
| `httpx` | HTTP client (Telegram alerts) |

#### 4.3 ตรวจว่าติดตั้งสำเร็จ

```powershell
python -c "import MetaTrader5; import pandas; import pandas_ta; print('All imports OK')"
```

---

## 5. ตั้งค่า Environment (.env)

### 5.1 สร้างจากตัวอย่าง

```powershell
cd D:\VibeCode\Trade
Copy-Item .env.example .env
```

### 5.2 แก้ไขค่าที่จำเป็น

เปิด `.env` ด้วย text editor:

```ini
# ============================================================================
# ค่าที่ ต้อง แก้
# ============================================================================

# --- โหมด (เริ่มด้วย DRY_RUN เสมอ!) ---
TRADING_MODE=DRY_RUN

# --- MT5 Connection ---
MT5_LOGIN=12345678                    # ← เลขบัญชี MT5
MT5_PASSWORD=YourPasswordHere         # ← รหัสผ่าน MT5
MT5_SERVER=Exness-MT5Real             # ← Server ของ Broker
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# --- Account Currency ---
ACCOUNT_CURRENCY=USC                  # USC = Exness Cent, USD = Standard
LOT_MODE=CENT                         # CENT หรือ STANDARD

# --- Symbols ที่จะเทรด ---
TRADING_SYMBOLS=XAUUSDc,XAGUSDc,BTCUSDc

# --- Telegram (ถ้าต้องการแจ้งเตือน) ---
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ENABLED=false

# ============================================================================
# ค่าที่ไม่ต้องแก้ (ค่าเริ่มต้นใช้ได้เลย)
# ============================================================================
MAX_RISK_PER_TRADE_PCT=5
MAX_POSITIONS_PER_SYMBOL=3
CAPITAL_FLOOR_PCT=90.0
FLOATING_DD_BLOCK_PCT=20.0
BREAKEVEN_R_MULTIPLE=0.5
SL_ATR_MULTIPLIER=1.5
TP_ATR_MULTIPLIER=1.5
```

> [!CAUTION]
> **อย่าเปลี่ยน `TRADING_MODE` เป็น `LIVE` จนกว่าจะผ่าน QC Suite + DRY_RUN สำเร็จ!**
> ในโหมด LIVE ระบบจะส่งคำสั่งเทรดจริง → มีความเสี่ยงเรื่องเงิน

---

## 6. ตั้งค่า MetaTrader 5

1. ติดตั้ง MT5 จาก Broker → เปิด → ล็อกอิน
2. เมนู `Tools` → `Options` → แท็บ `Expert Advisors`:
   - ✅ **Allow algorithmic trading**
   - ✅ **Allow DLL imports**
3. ตรวจเส้นทาง:
   ```powershell
   Test-Path "C:\Program Files\MetaTrader 5\terminal64.exe"
   # ต้องได้ True — ถ้าไม่ใช่ แก้ MT5_PATH ใน .env
   ```

> [!IMPORTANT]
> **MT5 ต้องเปิดอยู่ตลอดเวลา** ขณะรันระบบ

---

## 7. เริ่มระบบ (Startup)

### ลำดับการเริ่ม

```
1. เปิด MetaTrader 5     → ล็อกอินให้เรียบร้อย
2. เปิด PowerShell       → activate venv
3. รัน OPUS Engine       → python backend/trader/main.py --mode dry_run
4. รัน QC Suite          → ตรวจว่าทุกอย่างปกติ
```

> **ไม่ต้องเริ่ม service อื่นก่อน!** SQLite เปิดอัตโนมัติเมื่อรัน

### วิธีเริ่ม

**Terminal 1 — OPUS Engine:**
```powershell
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1
python backend/trader/main.py --mode dry_run
# หรือ live mode:
# python backend/trader/main.py --mode live
```

**Terminal 2 — QC Suite:**
```powershell
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1
python backend/trader/scripts/qc_suite.py
# ต้องผ่านทุก test
```

**Terminal 3 — Live Gate (ทดสอบ signal):**
```powershell
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1
python backend/trader/scripts/live_gate.py --run-fresh --symbol XAUUSD --bars 25920
```

---

## 8. ตรวจสอบระบบ (Verification)

### 8.1 QC Suite

```powershell
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1
python backend/trader/scripts/qc_suite.py
# ผลลัพธ์ที่ต้องการ: ทุก test ผ่าน (PASS)
```

### 8.2 Smoke Test Order

```powershell
python backend/trader/scripts/smoke_test_order.py
# ตรวจว่า MT5 เชื่อมต่อได้ + ส่ง test order ใน DRY mode
```

### 8.3 ตรวจ Logs

```powershell
Get-Content trader\logs\opus_trading.log -Tail 30
```

### 8.4 ตรวจ Live Gate Results

ไฟล์ JSON จะถูกสร้างใน `backend/data/live_gate_*.json`

---

## 9. โครงสร้างไดเรกทอรี

```
Trade/                               ← Root
├─ .env                              ← ⚠️ ค่าตั้งจริง (ห้ามแชร์)
├─ .env.example                      ← ตัวอย่างค่าตั้ง
├─ GEMINI.md                         ← Master Prompt (AI)
│
├─ การติดตั้ง/                        ← 📖 คู่มือ + scripts
│  ├─ README.md                      ← คู่มือเต็ม (ไฟล์นี้)
│  ├─ QUICK_START.md                 ← ฉบับย่อ 5 นาที
│  ├─ HANDOFF_PROMPT.md              ← Prompt พัฒนาต่อ
│  └─ setup_new_machine.bat          ← ติดตั้งอัตโนมัติ
│
├─ backend/                           ← 🧠 OPUS Trading Engine
│  ├─ main.py                        ← 🚀 Entry point หลัก
│  ├─ README.md                      ← Engine documentation
│  │
│  ├─ config/                        ← ⚙️ Configuration
│  │  └─ settings.json               ← Risk, regime, session, symbol settings
│  │
│  ├─ strategy/                      ← 📊 Trading Strategies
│  │  ├─ selector.py                 ← Strategy selector (regime-based)
│  │  ├─ trend_killer.py             ← Smart Money Trend Killer
│  │  └─ liquidity_hunter.py         ← Liquidity Sweep Hunter
│  │
│  ├─ features/                      ← 📈 Feature Engineering
│  │  ├─ volatility.py               ← ATR, volatility metrics
│  │  ├─ structure.py                ← Market structure (BOS, HH/HL)
│  │  └─ candle_patterns.py          ← Candlestick pattern detection
│  │
│  ├─ regime/                        ← 🎯 Market Regime
│  │  └─ classifier.py               ← Regime classifier (Trend/Sideways/etc.)
│  │
│  ├─ liquidity/                     ← 💧 Liquidity Detection
│  │  └─ detector.py                 ← EQH/EQL sweep, displacement
│  │
│  ├─ risk/                          ← 🛡️ Risk Engine
│  │  └─ gate.py                     ← Pre-Trade Gate (hard limits)
│  │
│  ├─ execution/                     ← ⚡ Order Execution
│  │  ├─ mt5_order.py                ← MT5 order sender
│  │  └─ position_manager.py         ← Position management (BE, trailing, TP)
│  │
│  ├─ data/                          ← 💾 Data Layer
│  │  ├─ fetcher.py                  ← MT5 data fetcher
│  │  ├─ mapper.py                   ← Symbol mapping (XAUUSD→XAUUSDc)
│  │  ├─ time_utils.py               ← UTC/Session time helpers
│  │  └─ opus.db                     ← SQLite database
│  │
│  ├─ storage/                       ← 🗄️ Persistence
│  │  └─ sqlite_db.py                ← SQLite CRUD operations
│  │
│  ├─ observability/                 ← 📝 Logging
│  │  └─ logger.py                   ← JSON structured logger
│  │
│  ├─ scripts/                       ← 🧪 Scripts & Tools
│  │  ├─ qc_suite.py                 ← Quality Control tests
│  │  ├─ live_gate.py                ← Live signal gate test
│  │  ├─ run_backtest.py             ← Backtest runner
│  │  ├─ smoke_test_order.py         ← Order smoke test
│  │  └─ debug_vol.py                ← Volatility debugger
│  │
│  └─ logs/                          ← Log files
│     └─ opus_trading.log
│
├─ frontend/                         ← (Legacy) Nuxt Dashboard
├─ backend/                          ← (Legacy) Old backend
└─ scripts/                          ← (Legacy) Old startup scripts
```

---

## 10. คำสั่งที่ใช้บ่อย

```powershell
# ─── เข้า environment ─────────────────────────────────
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1

# ─── เริ่ม OPUS Engine ────────────────────────────────
python backend/trader/main.py --mode dry_run     # DRY_RUN (ไม่ส่งคำสั่งจริง)
python backend/trader/main.py --mode live        # LIVE (⚠️ เทรดจริง!)
python backend/trader/main.py --mode backtest    # Backtest

# ─── QC / Testing ─────────────────────────────────────
python backend/trader/scripts/qc_suite.py        # รัน QC Suite
python backend/trader/scripts/smoke_test_order.py # ทดสอบ MT5 connection
python backend/trader/scripts/live_gate.py --run-fresh --symbol XAUUSD --bars 25920

# ─── Backtest ─────────────────────────────────────────
python backend/trader/scripts/run_backtest.py --symbol XAUUSD --start 2025-01-01 --end 2025-12-31
```

---

## 11. แก้ปัญหาที่พบบ่อย

### ❌ `python: not recognized`

**สาเหตุ:** Python ไม่อยู่ใน PATH
**แก้:** ติดตั้ง Python ใหม่ → ✅ ติ๊ก "Add to PATH"

---

### ❌ `ModuleNotFoundError: No module named 'MetaTrader5'`

**สาเหตุ:** ไม่ได้เปิด venv หรือยังไม่ได้ติดตั้ง

**แก้:**
```powershell
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1
pip install MetaTrader5
```

---

### ❌ `MT5 connection failed`

**สาเหตุ:** MT5 ไม่ได้เปิด หรือ path ไม่ถูก

**แก้:**
1. ตรวจว่า MT5 **เปิดอยู่** และ **ล็อกอินสำเร็จ**
2. ตรวจ `MT5_PATH` ใน `.env`
3. ตรวจว่า `Allow algorithmic trading` เปิดใน MT5 Options

---

### ❌ `Execution Policy` error ใน PowerShell

**แก้:**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

### ❌ `ModuleNotFoundError: No module named 'trader'`

**สาเหตุ:** รันจากผิด directory

**แก้:**
```powershell
cd D:\VibeCode\Trade     # ต้องอยู่ที่ project root
python backend/trader/main.py --mode dry_run
```

---

## 12. Safety Checklist ก่อนเปิด LIVE

> [!CAUTION]
> **โหมด LIVE ใช้เงินจริง! อ่านให้จบก่อนเปิด**

| # | รายการตรวจ | ✓ |
|---|-----------|---|
| 1 | QC Suite ผ่าน 100% | ☐ |
| 2 | DRY_RUN เสถียร ≥ 30 นาที ไม่มี error | ☐ |
| 3 | MT5 connected + ล็อกอินสำเร็จ | ☐ |
| 4 | SL ติดทุกออเดอร์ (ตรวจจาก DRY log) | ☐ |
| 5 | Risk per trade ≤ configured % | ☐ |
| 6 | Capital Floor ตั้งค่าถูก | ☐ |
| 7 | Telegram แจ้งเตือนทำงาน (ถ้าเปิด) | ☐ |
| 8 | Smoke test order ผ่าน | ☐ |

### เปิด LIVE:

```powershell
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1
python backend/trader/main.py --mode live
```

> [!WARNING]
> **ห้ามทิ้งระบบ LIVE ไว้โดยไม่มีคนดู** ใน 24 ชม. แรก

---

## ฐานข้อมูลที่ระบบใช้

| ฐานข้อมูล | ประเภท | ไฟล์ | เก็บอะไร |
|-----------|--------|------|----------|
| **SQLite** | Embedded (.db) | `backend/trader/data/opus.db` | Trade journal, daily stats, incidents, parameters |

> **ไม่ใช้** QuestDB, Redis, MongoDB, DuckDB — ใช้แค่ SQLite ตัวเดียว (embedded)

---

## OPUS Engine — สรุปหลักการเทรด

| Component | หน้าที่ |
|-----------|---------|
| **Regime Classifier** | จำแนกตลาด: Strong Trend, Weak Trend, Distribution, Accumulation, Compression, Expansion |
| **Trend Killer** | เทรดตาม Structure (HL/LH) + Displacement + Candlestick patterns ในตลาด Trend |
| **Liquidity Hunter** | จับ Liquidity Sweep events (EQH/EQL) + Displacement confirmed ในตลาด Sideways |
| **Strategy Selector** | เลือก strategy ตาม regime: Trend→TrendKiller, Sideways→LiquidityHunter |
| **Risk Gate** | ตรวจสอบ pre-trade: spread, DD, daily loss, position count, SL mandatory |
| **Position Manager** | จัดการ position: trailing stop, break-even, partial TP |
