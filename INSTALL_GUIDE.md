# 📦 คู่มือติดตั้ง Antigravity AI Trading System บนเครื่องใหม่

> **เวอร์ชัน:** v2.0 — อัปเดตล่าสุด: 2026-02-26
>
> **สถาปัตยกรรม DB:** SQLite + DuckDB (embedded) — **ไม่ใช้ QuestDB แล้ว**

---

## สารบัญ

1. [ข้อกำหนดเครื่อง](#1-ข้อกำหนดเครื่อง)
2. [ซอฟต์แวร์ที่ต้องติดตั้ง](#2-ซอฟต์แวร์ที่ต้องติดตั้ง)
3. [ดาวน์โหลดโปรเจค](#3-ดาวน์โหลดโปรเจค)
4. [ติดตั้ง Backend (Python)](#4-ติดตั้ง-backend-python)
5. [ติดตั้ง Frontend (Nuxt Dashboard)](#5-ติดตั้ง-frontend-nuxt-dashboard)
6. [ตั้งค่า Environment (.env)](#6-ตั้งค่า-environment-env)
7. [ตั้งค่า MetaTrader 5](#7-ตั้งค่า-metatrader-5)
8. [เริ่มระบบ (Startup)](#8-เริ่มระบบ-startup)
9. [ตรวจสอบระบบ (Verification)](#9-ตรวจสอบระบบ-verification)
10. [คำสั่งที่ใช้บ่อย (Cheat Sheet)](#10-คำสั่งที่ใช้บ่อย)
11. [โครงสร้างไดเรกทอรี](#11-โครงสร้างไดเรกทอรี)
12. [แก้ปัญหาที่พบบ่อย](#12-แก้ปัญหาที่พบบ่อย)
13. [Safety Checklist ก่อนเปิด LIVE](#13-safety-checklist-ก่อนเปิด-live)

---

## 1. ข้อกำหนดเครื่อง

| รายการ | ขั้นต่ำ | แนะนำ |
|--------|---------|-------|
| **OS** | Windows 10 (64-bit) | Windows 11 |
| **RAM** | 8 GB | 16 GB+ |
| **CPU** | 4 Cores | 8 Cores |
| **พื้นที่ว่าง** | 5 GB | 10 GB+ |
| **อินเทอร์เน็ต** | จำเป็น (ต่อ MT5 Broker) | เสถียร / สาย LAN |

> [!IMPORTANT]
> ระบบถูกออกแบบสำหรับเครื่อง **8 GB RAM** — ไม่ต้องการ Java/QuestDB อีกแล้ว

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

### 2.2 Node.js (v18+) — สำหรับ Frontend Dashboard

1. ดาวน์โหลดจาก https://nodejs.org/ (เลือก **LTS**)
2. ตรวจ:
   ```powershell
   node --version   # v18.x.x หรือสูงกว่า
   npm --version
   ```

### 2.3 Git (แนะนำ)

1. ดาวน์โหลดจาก https://git-scm.com/download/win
2. ตรวจ:
   ```powershell
   git --version
   ```

### 2.4 MetaTrader 5 Terminal

1. ดาวน์โหลดจาก Broker (เช่น Exness) หรือ https://www.metatrader5.com/en/download
2. **เปิดโปรแกรม → ล็อกอินให้เรียบร้อย** ก่อนรันระบบ
3. จดเส้นทาง `terminal64.exe` (ปกติ: `C:\Program Files\MetaTrader 5\terminal64.exe`)

> [!NOTE]
> MT5 ต้อง **เปิดอยู่ตลอด** ขณะรันระบบ — Python ใช้ MT5 API ผ่าน terminal

### สรุปตาราง

| ซอฟต์แวร์ | เวอร์ชัน | ใช้ทำอะไร |
|-----------|----------|-----------|
| Python | 3.11+ | Backend, Bot, Risk Engine, AI Brain |
| Node.js | 18+ LTS | Frontend Dashboard (Nuxt) |
| Git | ล่าสุด | Clone โปรเจค |
| MetaTrader 5 | 5.x | เชื่อมต่อ Broker / เทรดจริง |

> [!TIP]
> **ไม่ต้องติดตั้ง Java/QuestDB อีกแล้ว** — ระบบใช้ SQLite + DuckDB (embedded) แทน

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
| `backend/` | ✅ ต้องมี | โค้ด Python ทั้งหมด |
| `frontend/` | ✅ ต้องมี | Dashboard (Nuxt) |
| `scripts/` | ✅ ต้องมี | สคริปต์สั่งรัน |
| `.env.example` | ✅ ต้องมี | ตัวอย่างค่าตั้ง |
| `GEMINI.md` | ✅ ต้องมี | Master Prompt สำหรับ AI |
| `.env` | ⚠️ อย่าคัดลอก | สร้างใหม่จาก .env.example (มีรหัสผ่าน) |
| `backend/data/` | 🔶 ถ้าต้องการ | DB เดิม (SQLite/DuckDB) |
| `node_modules/` | ❌ ไม่ต้อง | ติดตั้งใหม่โดย npm |
| `.venv/` | ❌ ไม่ต้อง | สร้าง venv ใหม่ |
| `backend/logs/` | ❌ ไม่ต้อง | Log เก่า |
| `vendor/` | ❌ ไม่ต้อง | QuestDB เลิกใช้แล้ว |

> [!CAUTION]
> **ห้ามคัดลอกไฟล์ `.env` ผ่านช่องทางไม่ปลอดภัย** (email, chat)
> ให้สร้างใหม่จาก `.env.example` แล้วกรอกรหัสผ่านเอง

---

## 4. ติดตั้ง Backend (Python)

### 4.1 สร้าง Virtual Environment

```powershell
cd D:\VibeCode\Trade\backend

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

### 4.2 ติดตั้ง Dependencies

```powershell
# ตรวจว่า venv เปิดอยู่ (จะเห็น (.venv) หน้า prompt)

# ติดตั้ง package ทั้งหมดจาก pyproject.toml
pip install -e .

# (ถ้าจะพัฒนาด้วย)
pip install -e ".[dev]"
```

**Packages หลัก:**

| Package | ใช้ทำอะไร |
|---------|-----------|
| `fastapi` + `uvicorn` | API Server (REST + WebSocket) |
| `MetaTrader5` | เชื่อมต่อ MT5 Terminal (Windows เท่านั้น) |
| `pandas` + `pandas-ta` | Data + Technical Analysis (EMA, RSI, ATR) |
| `numpy` | คำนวณตัวเลข |
| `duckdb` | Analytics engine |
| `pydantic` + `pydantic-settings` | Data validation + .env |
| `scikit-learn` + `joblib` | ML models |
| `httpx` + `websockets` | HTTP client + real-time streaming |

### 4.3 สร้างโฟลเดอร์ข้อมูล

```powershell
cd D:\VibeCode\Trade
mkdir -Force backend\data\sqlite
mkdir -Force backend\data\duckdb
mkdir -Force backend\data\exports
mkdir -Force backend\logs
```

### 4.4 ตรวจว่าติดตั้งสำเร็จ

```powershell
python -c "import fastapi; import MetaTrader5; import duckdb; import pandas_ta; print('All imports OK')"
```

---

## 5. ติดตั้ง Frontend (Nuxt Dashboard)

```powershell
cd D:\VibeCode\Trade\frontend
npm install
```

ทดลองรัน:
```powershell
npm run dev
# ต้องเห็น "Nuxt ready on http://localhost:3000"
# กด Ctrl+C หยุดได้
```

---

## 6. ตั้งค่า Environment (.env)

### 6.1 สร้างจากตัวอย่าง

```powershell
cd D:\VibeCode\Trade
Copy-Item .env.example .env
```

### 6.2 แก้ไขค่าที่จำเป็น

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
TRADING_SYMBOLS=XAUUSDc,XAGUSDc

# --- Telegram (ถ้าต้องการแจ้งเตือน) ---
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ENABLED=false

# ============================================================================
# ค่าที่ไม่ต้องแก้ (ค่าเริ่มต้นใช้ได้เลย)
# ============================================================================
SQLITE_DB_PATH=backend/data/sqlite/trading.db
DUCKDB_DB_PATH=backend/data/duckdb/analytics.duckdb
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO
LOG_FORMAT=json
```

> [!CAUTION]
> **อย่าเปลี่ยน `TRADING_MODE` เป็น `LIVE` จนกว่าจะผ่าน QC Suite + DRY_RUN สำเร็จ!**

---

## 7. ตั้งค่า MetaTrader 5

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

## 8. เริ่มระบบ (Startup)

### ลำดับการเริ่ม

```
1. เปิด MetaTrader 5     → ล็อกอินให้เรียบร้อย
2. เริ่ม Backend (Bot)    → เชื่อมต่อ MT5 + SQLite + เริ่มวนลูปเทรด
3. รัน QC Suite           → ตรวจว่าทุกอย่างปกติ
4. เริ่ม Frontend         → Dashboard สำหรับดูสถานะ
```

### วิธีเริ่มทีละส่วน

**Terminal 1 — Backend Bot:**
```powershell
cd D:\VibeCode\Trade\backend
.\.venv\Scripts\Activate.ps1
python run_bot.py
# หรือกำหนดโหมด:
# python run_bot.py --mode DRY_RUN --symbols ALL
```

**Terminal 2 — QC Suite:**
```powershell
cd D:\VibeCode\Trade
.\scripts\qc.ps1
# ต้องผ่านทุก test
```

**Terminal 3 — Frontend:**
```powershell
cd D:\VibeCode\Trade\frontend
npm run dev
```

### URLs หลังเริ่มระบบ

| บริการ | URL |
|--------|-----|
| **API** | http://localhost:8000 |
| **Swagger Docs** | http://localhost:8000/docs |
| **Health Check** | http://localhost:8000/api/health |
| **Dashboard** | http://localhost:3000 |

---

## 9. ตรวจสอบระบบ (Verification)

### 9.1 Health Check

เปิดเบราว์เซอร์ → http://localhost:8000/api/health

```json
{
  "status": "ok",
  "mode": "DRY_RUN",
  "services": {
    "mt5": "connected",
    "sqlite": "ok"
  }
}
```

### 9.2 QC Suite

```powershell
cd D:\VibeCode\Trade
.\scripts\qc.ps1
# ผลลัพธ์: ทุก test ผ่าน (PASS)
```

### 9.3 ตรวจ Dashboard

เปิด http://localhost:3000 → ตรวจว่า:
- แสดงโหมด: **DRY_RUN**
- Health: API / MT5 = ✅
- เห็นรายชื่อ Symbols

---

## 10. คำสั่งที่ใช้บ่อย

```powershell
# ─── เริ่ม / หยุด ────────────────────────────────────
cd D:\VibeCode\Trade\backend
.\.venv\Scripts\Activate.ps1
python run_bot.py                # รัน Bot + API
python run_bot.py --mode DRY_RUN # Force DRY_RUN

# ─── Frontend ────────────────────────────────────────
cd D:\VibeCode\Trade\frontend
npm run dev                      # Dev server :3000

# ─── QC ──────────────────────────────────────────────
.\scripts\qc.ps1                 # รัน QC Suite

# ─── Kill Switch ─────────────────────────────────────
.\scripts\kill_switch.bat        # หยุดทันที
```

---

## 11. โครงสร้างไดเรกทอรี

```
Trade/
├─ .env.example                  ← ตัวอย่างค่าตั้ง
├─ GEMINI.md                     ← Master Prompt สำหรับ AI
├─ INSTALL_GUIDE.md              ← 📖 คู่มือนี้
│
├─ backend/                      ← Python Backend
│  ├─ pyproject.toml             ← Dependencies
│  ├─ run_bot.py                 ← 🚀 Entry point
│  ├─ app/
│  │  ├─ api/                    ← FastAPI routes
│  │  ├─ brain/                  ← AI Brain (38 modules)
│  │  ├─ core/                   ← Config, Logging, Mode
│  │  ├─ db/                     ← SQLite + DuckDB
│  │  ├─ domain/                 ← Models, Enums
│  │  ├─ execution/              ← Pipeline, Backtester
│  │  ├─ mt5/                    ← MT5 Client + Market Data
│  │  ├─ risk/                   ← Risk Engine (Gate, Guards, BE)
│  │  ├─ services/               ← News Filter, Session
│  │  └─ strategy/               ← Strategy Factory + Templates
│  │     └─ templates/           ← 31 strategies (gold_elite, btc_elite, etc.)
│  ├─ data/
│  │  ├─ sqlite/                 ← trading.db
│  │  └─ duckdb/                 ← analytics.duckdb
│  └─ logs/
│
├─ frontend/                     ← Nuxt Dashboard
│  ├─ package.json
│  ├─ pages/
│  ├─ components/
│  └─ stores/
│
└─ scripts/                      ← สคริปต์สั่งรัน
   ├─ start_all.ps1
   ├─ kill_switch.bat
   └─ qc.ps1
```

---

## 12. แก้ปัญหาที่พบบ่อย

| ปัญหา | สาเหตุ | แก้ไข |
|--------|--------|-------|
| `python: not recognized` | Python ไม่อยู่ใน PATH | ติดตั้งใหม่ ✅ ติ๊ก "Add to PATH" |
| `ModuleNotFoundError: MetaTrader5` | ไม่ได้เปิด venv | `.\.venv\Scripts\Activate.ps1` แล้ว `pip install -e .` |
| `MT5 connection failed` | MT5 ไม่ได้เปิด / path ผิด | ตรวจว่า MT5 เปิดอยู่ + `MT5_PATH` ถูกต้อง |
| `Port 8000 already in use` | Process เก่าค้าง | `.\scripts\kill_switch.bat` |
| `Execution Policy error` | PowerShell บล็อก scripts | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `npm install` ช้า/error | Cache เสีย | `rm -r node_modules; npm cache clean --force; npm install` |

---

## 13. Safety Checklist ก่อนเปิด LIVE

> [!CAUTION]
> **โหมด LIVE ใช้เงินจริง! อ่านให้จบก่อนเปิด**

| # | รายการตรวจ | ✓ |
|---|-----------|---|
| 1 | QC Suite ผ่าน 100% | ☐ |
| 2 | DRY_RUN เสถียร ≥ 30 นาที ไม่มี error | ☐ |
| 3 | Health Check: MT5 = connected | ☐ |
| 4 | Dashboard แสดง BLOCK reason เมื่อไม่เทรด | ☐ |
| 5 | Kill Switch ทำงาน | ☐ |
| 6 | SL ติดทุกออเดอร์ (ตรวจจาก DRY log) | ☐ |
| 7 | Risk per trade ≤ configured % | ☐ |
| 8 | Telegram แจ้งเตือนทำงาน (ถ้าเปิด) | ☐ |

### เปิด LIVE:

```powershell
# 1. แก้ .env → TRADING_MODE=LIVE
# 2. รีสตาร์ท Backend
cd D:\VibeCode\Trade\backend
python run_bot.py
```

> [!WARNING]
> **ห้ามทิ้งระบบ LIVE ไว้โดยไม่มีคนดู** ใน 24 ชม. แรก

---

## ฐานข้อมูลที่ระบบใช้

| ฐานข้อมูล | ประเภท | ไฟล์ | เก็บอะไร |
|-----------|--------|------|----------|
| **SQLite** | Embedded (.db) | `backend/data/sqlite/trading.db` | Trade journal, state, decisions, ticks, OHLCV, profiles |
| **DuckDB** | Embedded (.duckdb) | `backend/data/duckdb/analytics.duckdb` | Analytics, backtest metrics |

> **ไม่ใช้** QuestDB, Redis, MongoDB — ใช้แค่ 2 ตัวนี้ (embedded, ไม่ต้องรัน service แยก)
