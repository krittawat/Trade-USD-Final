# 📦 คู่มือติดตั้ง Antigravity AI Trading System บนเครื่องใหม่

> **เวอร์ชัน:** v1.0 — อัปเดตล่าสุด: 2026-02-11
>
> คู่มือนี้อธิบายทุกขั้นตอนสำหรับติดตั้งระบบ Antigravity AI Trading System
> บนเครื่อง Windows ใหม่ ตั้งแต่เตรียมซอฟต์แวร์ → ตั้งค่า → รันระบบ → ทดสอบ

---

## สารบัญ <!-- Table of Contents -->

1. [ข้อกำหนดเครื่อง (System Requirements)](#1-ข้อกำหนดเครื่อง-system-requirements)
2. [ซอฟต์แวร์ที่ต้องติดตั้ง (Prerequisites)](#2-ซอฟต์แวร์ที่ต้องติดตั้ง-prerequisites)
3. [ดาวน์โหลดโปรเจค (Clone/Copy Project)](#3-ดาวน์โหลดโปรเจค-clonecopy-project)
4. [ติดตั้ง QuestDB (ฐานข้อมูลเวลา)](#4-ติดตั้ง-questdb-ฐานข้อมูลเวลา)
5. [ติดตั้ง Backend (Python)](#5-ติดตั้ง-backend-python)
6. [ติดตั้ง Frontend (Nuxt Dashboard)](#6-ติดตั้ง-frontend-nuxt-dashboard)
7. [ตั้งค่า Environment (.env)](#7-ตั้งค่า-environment-env)
8. [ตั้งค่า MetaTrader 5](#8-ตั้งค่า-metatrader-5)
9. [เริ่มระบบ (Startup)](#9-เริ่มระบบ-startup)
10. [ตรวจสอบว่าระบบทำงานถูกต้อง (Verification)](#10-ตรวจสอบว่าระบบทำงานถูกต้อง-verification)
11. [คำสั่งที่ใช้บ่อย (Cheat Sheet)](#11-คำสั่งที่ใช้บ่อย-cheat-sheet)
12. [โครงสร้างไดเรกทอรี (Directory Structure)](#12-โครงสร้างไดเรกทอรี-directory-structure)
13. [แก้ปัญหาที่พบบ่อย (Troubleshooting)](#13-แก้ปัญหาที่พบบ่อย-troubleshooting)
14. [Safety Checklist ก่อนเปิด LIVE](#14-safety-checklist-ก่อนเปิด-live)

---

## 1. ข้อกำหนดเครื่อง (System Requirements)

| รายการ | ขั้นต่ำ | แนะนำ |
|--------|---------|-------|
| **OS** | Windows 10 (64-bit) | Windows 11 |
| **RAM** | 8 GB | 16 GB+ |
| **CPU** | 4 Cores | 8 Cores |
| **พื้นที่ว่าง** | 5 GB | 10 GB+ |
| **อินเทอร์เน็ต** | จำเป็น (ต่อ MT5 Broker) | เสถียร / สาย LAN |

> [!IMPORTANT]
> ระบบถูกออกแบบสำหรับเครื่อง **8 GB RAM** โดยเฉพาะ
> จำกัด QuestDB JVM heap ไว้ที่ 512 MB เพื่อป้องกัน memory เต็ม

---

## 2. ซอฟต์แวร์ที่ต้องติดตั้ง (Prerequisites)

### 2.1 Python 3.11 ขึ้นไป

1. ดาวน์โหลดจาก https://www.python.org/downloads/
2. **ตอนติดตั้ง ติ๊กช่อง ✅ "Add Python to PATH"** ← สำคัญมาก!
3. เปิด PowerShell ตรวจว่าใช้ได้:
   ```powershell
   python --version
   # ต้องได้ Python 3.11.x หรือสูงกว่า

   pip --version
   # ต้องแสดงเวอร์ชัน pip
   ```

### 2.2 Java (JRE/JDK 11 ขึ้นไป) — สำหรับ QuestDB

1. ดาวน์โหลด **Eclipse Temurin JDK 17** จาก https://adoptium.net/
   - เลือก OS: Windows, Architecture: x64, Package Type: JDK
2. ติดตั้ง → ติ๊กช่อง **"Add to PATH"**
3. ตรวจใน PowerShell:
   ```powershell
   java -version
   # ต้องได้ openjdk version "17.x.x" หรือสูงกว่า
   ```

### 2.3 Node.js (v18 ขึ้นไป) — สำหรับ Frontend Dashboard

1. ดาวน์โหลดจาก https://nodejs.org/ (เลือก **LTS**)
2. ติดตั้งตามปกติ
3. ตรวจ:
   ```powershell
   node --version
   # v18.x.x หรือสูงกว่า

   npm --version
   # 9.x.x หรือสูงกว่า
   ```

### 2.4 Git (แนะนำ)

1. ดาวน์โหลดจาก https://git-scm.com/download/win
2. ติดตั้งตามปกติ (ค่าเริ่มต้นได้เลย)
3. ตรวจ:
   ```powershell
   git --version
   ```

### 2.5 MetaTrader 5 Terminal

1. ดาวน์โหลดจาก https://www.metatrader5.com/en/download หรือจากเว็บ Broker (เช่น Exness)
2. ติดตั้งแล้ว **เปิดโปรแกรมเข้าล็อกอินให้เรียบร้อย** ก่อน
3. จดเส้นทาง `terminal64.exe` (ปกติอยู่ที่ `C:\Program Files\MetaTrader 5\terminal64.exe`)

> [!NOTE]
> MT5 ต้อง **เปิดอยู่ตลอดเวลา** ขณะรันระบบ — Python ใช้ MT5 API ผ่านโปรแกรม terminal

### 2.6 สรุปตาราง Prerequisites

| ซอฟต์แวร์ | เวอร์ชันขั้นต่ำ | ใช้ทำอะไร | ลิงก์ดาวน์โหลด |
|-----------|----------------|-----------|----------------|
| Python | 3.11+ | Backend, Bot, Risk Engine | python.org |
| Java JDK | 11+ (แนะนำ 17) | รัน QuestDB | adoptium.net |
| Node.js | 18+ LTS | Frontend Dashboard | nodejs.org |
| Git | ล่าสุด | Clone โปรเจค | git-scm.com |
| MetaTrader 5 | 5.x | เชื่อมต่อ Broker / เทรดจริง | metatrader5.com |

---

## 3. ดาวน์โหลดโปรเจค (Clone/Copy Project)

### วิธีที่ 1: Git Clone (แนะนำ)

```powershell
# Clone จาก repository
cd D:\VibeCode
git clone <URL-ของ-Repository> Trade

# เข้าไปในโฟลเดอร์โปรเจค
cd Trade
```

### วิธีที่ 2: Copy โฟลเดอร์โดยตรง

1. คัดลอกโฟลเดอร์โปรเจคทั้งหมดจากเครื่องเดิมไปยังเครื่องใหม่
2. **ต้องคัดลอกโฟลเดอร์เหล่านี้ด้วย:**

   | โฟลเดอร์ | จำเป็น? | คำอธิบาย |
   |----------|---------|----------|
   | `backend/` | ✅ ต้องมี | โค้ด Python ทั้งหมด |
   | `frontend-USD/` | ✅ ต้องมี | โค้ด Dashboard (Nuxt) |
   | `scripts/` | ✅ ต้องมี | สคริปต์สั่งรันระบบ |
   | `vendor/questdb/` | ✅ ต้องมี | QuestDB binary (ดูส่วนที่ 4) |
   | `.env.example` | ✅ ต้องมี | ตัวอย่างไฟล์ config |
   | `.env` | ⚠️ อย่าคัดลอก | สร้างใหม่จาก .env.example (มีรหัสผ่าน) |
   | `backend/data/` | 🔶 ถ้าต้องการ | ฐานข้อมูล SQLite/DuckDB เดิม |
   | `vendor/questdb/data/` | 🔶 ถ้าต้องการ | ข้อมูลเก่าใน QuestDB |
   | `backend/logs/` | ❌ ไม่ต้อง | Log เก่า |
   | `node_modules/` | ❌ ไม่ต้อง | ติดตั้งใหม่โดย npm |
   | `.venv/` | ❌ ไม่ต้อง | สร้าง venv ใหม่ |

> [!CAUTION]
> **ห้ามคัดลอกไฟล์ `.env` ที่มีรหัสผ่านจริงผ่านช่องทางไม่ปลอดภัย** (เช่น email, chat)
> ให้สร้างไฟล์ `.env` ใหม่จาก `.env.example` แล้วกรอกรหัสผ่านเอง

---

## 4. ติดตั้ง QuestDB (ฐานข้อมูลเวลา)

QuestDB เป็นฐานข้อมูลสำหรับเก็บ OHLCV (แท่งเทียน), tick data และ symbol profiles
ระบบใช้ **แบบ native** (ไม่ใช้ Docker)

### 4.1 ดาวน์โหลด QuestDB

1. ไปที่ https://questdb.io/download/
2. ดาวน์โหลด **"No-JVM binaries" > Windows** (ไฟล์ `.zip`)
   - หรือเลือก "With JVM" ถ้าไม่อยากติดตั้ง Java แยก

### 4.2 แตกไฟล์ไปที่ตำแหน่งที่ถูกต้อง

ต้องวางไฟล์ให้ตรงตามโครงสร้างนี้:

```
<โปรเจค>/
└─ vendor/
   └─ questdb/
      ├─ questdb/          ← แตก zip ไว้ที่นี่
      │  ├─ questdb.jar    ← **ต้องมีไฟล์นี้**
      │  ├─ bin/
      │  └─ ...
      └─ data/             ← สร้างอัตโนมัติตอนรัน
```

**ขั้นตอน:**

```powershell
# สมมติโปรเจคอยู่ที่ D:\VibeCode\Trade
cd D:\VibeCode\Trade

# สร้างโฟลเดอร์ (ถ้ายังไม่มี)
mkdir -Force vendor\questdb

# แตก zip ที่ดาวน์โหลดมา → ย้ายเนื้อหาเข้าไปใน vendor\questdb\questdb\
# ตรวจสอบว่ามีไฟล์ questdb.jar:
Test-Path vendor\questdb\questdb\questdb.jar
# ต้องได้ True
```

### 4.3 ทดสอบรัน QuestDB

```powershell
.\scripts\start_questdb.ps1
```

ถ้าสำเร็จจะเห็นข้อความ:

```
Starting QuestDB (Native, No Docker)
Starting QuestDB on ports: HTTP=9000, ILP=9009, PG=8812
```

✅ เปิดเบราว์เซอร์ไปที่ http://localhost:9000 → จะเห็น QuestDB Web Console

> [!WARNING]
> JVM heap ถูกจำกัดที่ `-Xms256m -Xmx512m` เพื่อรองรับเครื่อง 8 GB RAM
> **ห้ามเปลี่ยนค่าเป็นมากกว่า 768m** ยกเว้นเครื่องมี RAM 16 GB ขึ้นไป

---

## 5. ติดตั้ง Backend (Python)

### 5.1 สร้าง Virtual Environment (แนะนำอย่างยิ่ง)

```powershell
cd D:\VibeCode\Trade\backend

# สร้าง venv
python -m venv .venv

# เปิดใช้ venv
.\.venv\Scripts\Activate.ps1
```

> [!NOTE]
> ถ้าเจอ error เกี่ยวกับ Execution Policy ให้รัน:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```
> แล้วลองใหม่

### 5.2 ติดตั้ง Dependencies

```powershell
# ตรวจว่า venv เปิดอยู่ (จะเห็น (.venv) หน้า prompt)

# ติดตั้ง package ทั้งหมดจาก pyproject.toml
pip install -e .

# (ถ้าจะพัฒนาด้วย ติดตั้ง dev tools)
pip install -e ".[dev]"
```

**รายการ packages หลักที่จะถูกติดตั้ง:**

| Package | เวอร์ชัน | ใช้ทำอะไร |
|---------|---------|-----------|
| `fastapi` | ≥0.109.0 | API Server (REST + WebSocket) |
| `uvicorn[standard]` | ≥0.27.0 | ASGI Server สำหรับรัน FastAPI |
| `MetaTrader5` | ≥5.0.45 | เชื่อมต่อ MT5 Terminal (Windows เท่านั้น) |
| `pandas` | ≥2.1.0 | จัดการข้อมูลตาราง |
| `pandas-ta` | ≥0.3.14b | Technical Analysis indicators (EMA, RSI, ATR ฯลฯ) |
| `numpy` | ≥1.26.0 | คำนวณตัวเลข |
| `questdb` | ≥1.1.0 | เขียนข้อมูลเข้า QuestDB (ILP) |
| `duckdb` | ≥0.10.0 | Analytics engine (อ่าน/ประมวลผลข้อมูล) |
| `pydantic` | ≥2.5.0 | Data validation & settings |
| `pydantic-settings` | ≥2.1.0 | อ่าน .env → Python settings |
| `python-dotenv` | ≥1.0.0 | โหลดไฟล์ .env |
| `httpx` | ≥0.26.0 | HTTP client (เรียก API ภายนอก) |
| `websockets` | ≥12.0 | WebSocket สำหรับ real-time streaming |

### 5.3 สร้างโฟลเดอร์ข้อมูล

```powershell
# สร้างโฟลเดอร์ที่จำเป็น (ถ้ายังไม่มี)
mkdir -Force backend\data\sqlite
mkdir -Force backend\data\duckdb
mkdir -Force backend\data\exports
mkdir -Force backend\logs
```

### 5.4 ตรวจว่าติดตั้งสำเร็จ

```powershell
# ทดสอบ import
python -c "import fastapi; import MetaTrader5; import questdb; import duckdb; print('All imports OK')"
```

> [!NOTE]
> `MetaTrader5` package ทำงานได้เฉพาะบน **Windows** เท่านั้น
> ไม่สามารถรันบน macOS หรือ Linux ได้

---

## 6. ติดตั้ง Frontend (Nuxt Dashboard)

### 6.1 ติดตั้ง Dependencies

```powershell
cd D:\VibeCode\Trade\frontend-USD

# ติดตั้ง Node packages
npm install
```

**รายการ packages หลักของ Frontend:**

| Package | เวอร์ชัน | ใช้ทำอะไร |
|---------|---------|-----------|
| `nuxt` | ^3.10.0 | Framework สำหรับ Vue.js |
| `@nuxtjs/tailwindcss` | ^6.11.0 | CSS framework |
| `pinia` | ^2.1.7 | State management |
| `@pinia/nuxt` | ^0.5.1 | Pinia integration กับ Nuxt |

### 6.2 ตรวจว่าติดตั้งสำเร็จ

```powershell
# ทดลองรันในโหมด dev (กด Ctrl+C หยุดได้)
npm run dev
# ต้องเห็น "Nuxt ready on http://localhost:3000"
```

---

## 7. ตั้งค่า Environment (.env)

### 7.1 สร้างไฟล์ .env จากตัวอย่าง

```powershell
cd D:\VibeCode\Trade
Copy-Item .env.example .env
```

### 7.2 แก้ไขไฟล์ .env

เปิดไฟล์ `.env` ด้วย text editor แล้วแก้ค่าต่อไปนี้:

```ini
# ============================================================================
# ค่าที่ ต้อง แก้
# ============================================================================

# --- โหมดการทำงาน (เริ่มด้วย DRY_RUN เสมอ!) ---
TRADING_MODE=DRY_RUN

# --- MT5 Connection (ข้อมูลบัญชี MT5 ของคุณ) ---
MT5_LOGIN=12345678                    # ← ใส่เลขบัญชี MT5
MT5_PASSWORD=YourPasswordHere         # ← ใส่รหัสผ่าน MT5
MT5_SERVER=Exness-MT5Real             # ← ชื่อ Server ของ Broker
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# --- Telegram (ถ้าต้องการแจ้งเตือน) ---
TELEGRAM_BOT_TOKEN=your_bot_token     # ← ขอจาก @BotFather
TELEGRAM_CHAT_ID=your_chat_id        # ← Chat ID ของคุณ
TELEGRAM_ENABLED=true                 # ← ตั้ง false ถ้ายังไม่ใช้

# ============================================================================
# ค่าที่ปกติ ไม่ต้อง แก้ (ค่าเริ่มต้นใช้ได้เลย)
# ============================================================================

# QuestDB (ค่า default — ไม่ต้องแก้ถ้ารันบนเครื่องเดียวกัน)
QUESTDB_HOST=localhost
QUESTDB_HTTP_PORT=9000
QUESTDB_ILP_PORT=9009
QUESTDB_PG_PORT=8812
QUESTDB_PG_USER=admin
QUESTDB_PG_PASSWORD=quest
QUESTDB_JAVA_OPTS=-Xms256m -Xmx512m

# SQLite & DuckDB (path สัมพัทธ์ — ไม่ต้องแก้)
SQLITE_DB_PATH=backend/data/sqlite/trading.db
DUCKDB_DB_PATH=backend/data/duckdb/analytics.duckdb

# Risk Engine (ค่าเริ่มต้นปลอดภัย — ไม่ต้องแก้)
MAX_RISK_PER_TRADE_PCT=2.0
MAX_POSITIONS_PER_SYMBOL=2
CAPITAL_FLOOR_PCT=90.0
FLOATING_DD_BLOCK_PCT=10.0
BREAKEVEN_R_MULTIPLE=1.0
NEWS_BLOCK_MINUTES=30

# API & Frontend
API_HOST=0.0.0.0
API_PORT=8000
FRONTEND_PORT=3000
API_BASE_URL=http://localhost:8000

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

> [!CAUTION]
> **อย่าเปลี่ยน `TRADING_MODE` เป็น `LIVE` จนกว่าจะผ่าน QC Suite + DRY_RUN สำเร็จ!**
> ในโหมด LIVE ระบบจะส่งคำสั่งเทรดจริง → มีความเสี่ยงเรื่องเงิน

---

## 8. ตั้งค่า MetaTrader 5

### 8.1 ติดตั้งและล็อกอิน

1. ติดตั้ง MetaTrader 5 จาก Broker (เช่น Exness)
2. เปิดโปรแกรม → ล็อกอินด้วยบัญชีจริง/demo
3. ตรวจว่าเชื่อมต่อสำเร็จ (มุมล่างขวาไม่มีสีแดง)

### 8.2 เปิด Algorithmic Trading

1. ใน MT5 → เมนู `Tools` → `Options`
2. แท็บ `Expert Advisors`
3. ✅ ติ๊กช่อง **"Allow algorithmic trading"**
4. ✅ ติ๊กช่อง **"Allow DLL imports"** (จำเป็นสำหรับ Python API)
5. กด OK

### 8.3 ตรวจเส้นทาง terminal64.exe

```powershell
# ตรวจว่าไฟล์ terminal64.exe มีอยู่ตามที่ตั้งค่าใน .env
Test-Path "C:\Program Files\MetaTrader 5\terminal64.exe"
# ต้องได้ True

# ถ้าติดตั้งที่อื่น ให้แก้ค่า MT5_PATH ใน .env ให้ตรง
```

> [!IMPORTANT]
> **MT5 ต้องเปิดอยู่ตลอดเวลา** ขณะรันระบบ
> Python API ใช้วิธีเชื่อมต่อกับ MT5 Terminal ที่กำลังทำงานอยู่

---

## 9. เริ่มระบบ (Startup)

### ลำดับการเริ่มระบบ (สำคัญ — ต้องเริ่มตามลำดับ)

```
1. เปิด MetaTrader 5     → ล็อกอินให้เรียบร้อย
2. เริ่ม QuestDB          → ฐานข้อมูลต้องพร้อมก่อน
3. เริ่ม Backend (Bot)    → เชื่อมต่อ MT5 + QuestDB + เริ่มวนลูปเทรด
4. รัน QC Suite           → ตรวจว่าทุกอย่างปกติ
5. เริ่ม Frontend         → Dashboard สำหรับดูสถานะ
```

### วิธีที่ 1: เริ่มทั้งหมดด้วยคำสั่งเดียว (แนะนำ)

```powershell
# เปิด PowerShell ในโฟลเดอร์โปรเจค
cd D:\VibeCode\Trade

# รันสคริปต์เริ่มทั้งหมด
.\scripts\start_all.ps1
```

หรือใช้ไฟล์ `.bat` (ดับเบิ้ลคลิกเปิดได้):

```
ดับเบิ้ลคลิก: scripts\start_all.bat
```

### วิธีที่ 2: เริ่มทีละส่วน (สำหรับ Debug)

**Terminal 1 — QuestDB:**
```powershell
cd D:\VibeCode\Trade
.\scripts\start_questdb.ps1
# รอจนเห็น "Starting QuestDB on ports..."
```

**Terminal 2 — Backend Bot:**
```powershell
cd D:\VibeCode\Trade\backend

# เปิด venv (ถ้าใช้)
.\.venv\Scripts\Activate.ps1

# รัน Bot (จะเริ่ม API server + Master Loop ด้วย)
python run_bot.py
```

**Terminal 3 — QC Suite:**
```powershell
cd D:\VibeCode\Trade
.\scripts\qc.ps1
# ต้องผ่านทุก test
```

**Terminal 4 — Frontend:**
```powershell
cd D:\VibeCode\Trade
.\scripts\start_frontend.ps1
# หรือ
cd frontend-USD
npm run dev
```

### URLs หลังเริ่มระบบ

| บริการ | URL | คำอธิบาย |
|--------|-----|----------|
| **API** | http://localhost:8000 | Backend REST API |
| **Swagger Docs** | http://localhost:8000/docs | ดูเอกสาร API + ทดลองเรียก |
| **Health Check** | http://localhost:8000/api/health | ตรวจสถานะระบบ |
| **Dashboard** | http://localhost:3000 | หน้า Dashboard หลัก |
| **QuestDB Console** | http://localhost:9000 | เขียน SQL query ดูข้อมูล |

---

## 10. ตรวจสอบว่าระบบทำงานถูกต้อง (Verification)

### 10.1 ตรวจ Health Check

เปิดเบราว์เซอร์ → http://localhost:8000/api/health

ต้องได้ JSON ที่แสดง:
```json
{
  "status": "ok",
  "mode": "DRY_RUN",
  "services": {
    "mt5": "connected",
    "questdb": "ok",
    "sqlite": "ok"
  }
}
```

### 10.2 รัน QC Suite

```powershell
cd D:\VibeCode\Trade
.\scripts\qc.ps1

# ผลลัพธ์ที่ต้องการ: ทุก test ผ่าน (PASS)
```

### 10.3 ตรวจ Logs

```powershell
# ดู log ว่า bot ทำงานปกติ
Get-Content backend\logs\trade.log -Tail 20
```

### 10.4 ตรวจ Dashboard

เปิด http://localhost:3000 → ตรวจว่า:
- แสดงโหมด: **DRY_RUN**
- Health: API / MT5 / QuestDB = ✅
- เห็นรายชื่อ Symbols

---

## 11. คำสั่งที่ใช้บ่อย (Cheat Sheet)

```powershell
# ─── เริ่ม / หยุดระบบ ─────────────────────────────────────────────
.\scripts\start_all.ps1          # เริ่มทั้งระบบ
.\scripts\start_all.bat          # เริ่มทั้งระบบ (ดับเบิ้ลคลิก)
.\scripts\start_questdb.ps1      # เริ่ม QuestDB อย่างเดียว
.\scripts\start_backend.ps1      # เริ่ม Backend API อย่างเดียว
.\scripts\start_frontend.ps1     # เริ่ม Frontend อย่างเดียว
.\scripts\dev.ps1                # เริ่ม Backend + Frontend (สำหรับพัฒนา)

# ─── หยุดฉุกเฉิน (Kill Switch) ──────────────────────────────────
.\scripts\kill_switch.bat        # หยุดทันที + ปิด Python ทั้งหมด
# หรือเรียก API:
Invoke-WebRequest -Uri 'http://localhost:8000/api/kill-switch' -Method POST

# ─── ตรวจสอบ / ทดสอบ ──────────────────────────────────────────
.\scripts\qc.ps1                 # รัน QC Suite
.\scripts\smoke_live.ps1         # Smoke test สำหรับ LIVE (ยังไม่ implement)

# ─── Backend (ต้องเปิด venv ก่อน) ─────────────────────────────
cd backend
.\.venv\Scripts\Activate.ps1     # เปิด virtual environment
python run_bot.py                # รัน Bot + API
pip install -e .                 # ติดตั้ง/อัปเดต dependencies
pip install -e ".[dev]"          # ติดตั้ง dev tools ด้วย

# ─── Frontend ──────────────────────────────────────────────────
cd frontend-USD
npm install                      # ติดตั้ง packages
npm run dev                      # รัน dev server
npm run build                    # Build production
```

---

## 12. โครงสร้างไดเรกทอรี (Directory Structure)

```
Trade/                           ← โฟลเดอร์ Root ของโปรเจค
├─ .env                          ← ⚠️ ค่าตั้งจริง (ห้ามแชร์)
├─ .env.example                  ← ตัวอย่างค่าตั้ง
├─ .gitignore                    ← ไฟล์ที่ Git ไม่ track
├─ GEMINI.md                     ← Master Prompt สำหรับ AI
├─ README.md                     ← เอกสารสรุปโปรเจค
├─ DEPLOY_GUIDE.md               ← 📖 คู่มือนี้
│
├─ scripts/                      ← สคริปต์สั่งรัน
│  ├─ start_all.ps1              ← เริ่มทั้งระบบ (PowerShell)
│  ├─ start_all.bat              ← เริ่มทั้งระบบ (ดับเบิ้ลคลิก)
│  ├─ start_questdb.ps1          ← เริ่ม QuestDB
│  ├─ start_backend.ps1          ← เริ่ม Backend API
│  ├─ start_frontend.ps1         ← เริ่ม Frontend
│  ├─ dev.ps1                    ← โหมดพัฒนา (Backend + Frontend)
│  ├─ qc.ps1                     ← รัน QC Suite
│  ├─ kill_switch.bat            ← ⛔ หยุดฉุกเฉิน
│  └─ smoke_live.ps1             ← Smoke test สำหรับ LIVE
│
├─ backend/                      ← Python Backend ทั้งหมด
│  ├─ pyproject.toml             ← รายการ dependencies
│  ├─ run_bot.py                 ← 🚀 Entry point หลัก
│  ├─ app/                       ← ซอร์สโค้ดหลัก
│  │  ├─ api/                    ← FastAPI routes
│  │  ├─ brain/                  ← AI Brain (Market Memory)
│  │  ├─ core/                   ← Config, Logging, Mode
│  │  ├─ db/                     ← QuestDB, SQLite, DuckDB
│  │  ├─ domain/                 ← Models, Enums
│  │  ├─ execution/              ← Pipeline, Simulator, Replay
│  │  ├─ mt5/                    ← MT5 Client + Market Data
│  │  ├─ risk/                   ← Risk Engine (Gate, Guards, BE)
│  │  ├─ services/               ← News Filter, Session
│  │  └─ strategy/               ← Strategy Factory + Templates
│  ├─ data/                      ← ข้อมูล Runtime
│  │  ├─ sqlite/                 ← trading.db (trade journal)
│  │  ├─ duckdb/                 ← analytics.duckdb
│  │  └─ exports/                ← exported data
│  ├─ logs/                      ← Log files
│  └─ scripts/verify/            ← QC test scripts
│
├─ frontend-USD/                 ← Nuxt Dashboard
│  ├─ package.json               ← Node packages
│  ├─ nuxt.config.ts             ← Nuxt config
│  ├─ pages/                     ← หน้าเว็บ
│  ├─ components/                ← Component ย่อย
│  ├─ stores/                    ← Pinia stores
│  └─ composables/               ← Composables (shared logic)
│
└─ vendor/                       ← ซอฟต์แวร์ภายนอก
   └─ questdb/
      ├─ questdb/                ← QuestDB binary (questdb.jar)
      └─ data/                   ← QuestDB data files
```

---

## 13. แก้ปัญหาที่พบบ่อย (Troubleshooting)

### ❌ `python: command not found` หรือ `python is not recognized`

**สาเหตุ:** Python ไม่อยู่ใน PATH

**แก้ไข:**
1. ติดตั้ง Python ใหม่ → ✅ ติ๊ก "Add to PATH"
2. หรือเพิ่ม PATH เอง:
   ```powershell
   # ตรวจว่า Python อยู่ที่ไหน
   Get-Command python -ErrorAction SilentlyContinue
   # ถ้าไม่เจอ ให้เพิ่มใน System Environment Variables
   ```

---

### ❌ `java: command not found`

**สาเหตุ:** Java ไม่อยู่ใน PATH

**แก้ไข:** ติดตั้ง JDK ใหม่ → ✅ ติ๊ก "Add to PATH" ตอนติดตั้ง

---

### ❌ `questdb.jar not found`

**สาเหตุ:** ยังไม่ได้แตก QuestDB zip หรือแตกผิดที่

**แก้ไข:**
1. ตรวจว่ามีไฟล์ `vendor\questdb\questdb\questdb.jar`
2. ถ้าไม่มี ดาวน์โหลดจาก https://questdb.io/download/ แล้วแตกใส่ให้ถูกตำแหน่ง

---

### ❌ `ModuleNotFoundError: No module named 'MetaTrader5'`

**สาเหตุ:** รัน Python โดยไม่ได้เปิด venv หรือยังไม่ได้ติดตั้ง

**แก้ไข:**
```powershell
cd backend
.\.venv\Scripts\Activate.ps1  # เปิด venv
pip install -e .               # ติดตั้ง dependencies ใหม่
```

---

### ❌ `MT5 connection failed` หรือ `MT5 initialize failed`

**สาเหตุ:** MT5 Terminal ไม่ได้เปิด หรือ path ไม่ถูก

**แก้ไข:**
1. ตรวจว่า MT5 **เปิดอยู่** และ **ล็อกอินสำเร็จ**
2. ตรวจค่า `MT5_PATH` ใน `.env`:
   ```powershell
   Test-Path "C:\Program Files\MetaTrader 5\terminal64.exe"
   ```
3. ตรวจว่า `Allow algorithmic trading` ถูกเปิดใน MT5 Options

---

### ❌ `Port 8000 already in use`

**สาเหตุ:** มีโปรเซส Python/API เก่ายังเปิดอยู่

**แก้ไข:**
```powershell
# หาว่าใครใช้ port 8000
netstat -ano | findstr :8000

# ปิดโปรเซส (แทน <PID> ด้วยเลข PID ที่เจอ)
taskkill /PID <PID> /F

# หรือใช้ Kill Switch
.\scripts\kill_switch.bat
```

---

### ❌ `npm install` ช้ามากหรือ error

**แก้ไข:**
```powershell
# ลบ node_modules แล้วลงใหม่
cd frontend-USD
Remove-Item -Recurse -Force node_modules
npm install

# ถ้ายังช้า ลองใช้ npm cache clean
npm cache clean --force
npm install
```

---

### ❌ `ENOSPC: System limit for number of file watchers reached` (Frontend)

**สาเหตุ:** ระบบเฝ้าดูไฟล์เกินลิมิต (ปกติพบบน WSL)

**แก้ไข:** บน Windows ปกติไม่เจอปัญหานี้ ถ้าเจอให้รีสตาร์ทเครื่อง

---

### ❌ QuestDB กินแรมเยอะ

**แก้ไข:**
1. ตรวจค่า `JAVA_OPTS` ใน `start_questdb.ps1` ว่าเป็น `-Xms256m -Xmx512m`
2. ถ้าเครื่องมี RAM 8 GB → **ห้ามเกิน 512m**
3. รีสตาร์ท QuestDB

---

## 14. Safety Checklist ก่อนเปิด LIVE

> [!CAUTION]
> **อ่านให้จบก่อนเปิด LIVE — โหมดนี้ใช้เงินจริง!**

### ✅ ก่อนเปิด LIVE ต้องผ่านทุกข้อ:

| # | รายการตรวจ | สถานะ |
|---|-----------|-------|
| 1 | ✅ QC Suite ผ่าน 100% | ☐ |
| 2 | ✅ DRY_RUN ทำงานเสถียร ≥ 30 นาที ไม่มี error | ☐ |
| 3 | ✅ Health Check: MT5 = connected, QuestDB = ok | ☐ |
| 4 | ✅ Dashboard แสดง BLOCK reason เมื่อไม่เทรด | ☐ |
| 5 | ✅ Kill Switch ทำงาน (ทดสอบกด kill_switch.bat) | ☐ |
| 6 | ✅ SL ถูกติดทุกออเดอร์ (ตรวจจาก DRY log) | ☐ |
| 7 | ✅ Risk per trade ≤ 2% equity | ☐ |
| 8 | ✅ Max 2 positions per symbol | ☐ |
| 9 | ✅ Telegram แจ้งเตือนทำงาน (ถ้าเปิด) | ☐ |
| 10 | ✅ Capital Floor 90% ตั้งค่าถูก | ☐ |

### เปิด LIVE:

```powershell
# แก้ไฟล์ .env
TRADING_MODE=LIVE

# รีสตาร์ท Backend
# (กด Ctrl+C ที่ terminal เดิม แล้วรันใหม่)
cd backend
python run_bot.py
```

> [!WARNING]
> **ห้ามทิ้งระบบ LIVE ไว้โดยไม่มีคนดู** ในช่วง 24 ชม. แรก
> คอยตรวจ Dashboard + Telegram เป็นระยะ

---

## ฐานข้อมูลที่ระบบใช้ (สรุป)

| ฐานข้อมูล | ประเภท | ไฟล์/ที่อยู่ | ใช้เก็บอะไร |
|-----------|--------|-------------|------------|
| **QuestDB** | Time-series (Java process) | `vendor/questdb/data/` | OHLCV, ticks, symbol profiles |
| **SQLite** | Embedded (ไฟล์ .db) | `backend/data/sqlite/trading.db` | Trade journal, state, decision traces |
| **DuckDB** | Embedded (ไฟล์ .duckdb) | `backend/data/duckdb/analytics.duckdb` | Analytics, backtest metrics |

> **ห้ามเพิ่ม** Redis, Elasticsearch, MongoDB, InfluxDB — ใช้แค่ 3 ตัวนี้เท่านั้น

---

## Ports ที่ระบบใช้ (สรุป)

| Port | Service | Protocol |
|------|---------|----------|
| 8000 | FastAPI Backend | HTTP / WebSocket |
| 3000 | Nuxt Frontend | HTTP |
| 9000 | QuestDB HTTP Console | HTTP |
| 9009 | QuestDB ILP (เขียนข้อมูล) | TCP |
| 8812 | QuestDB PostgreSQL Wire | TCP |

ตรวจว่า ports เหล่านี้ไม่ถูกซอฟต์แวร์อื่นใช้งานอยู่ก่อนเริ่มระบบ

---

> 💡 **คำแนะนำ:** เก็บคู่มือนี้ไว้ในโปรเจคเพื่อใช้อ้างอิง — อัปเดตเมื่อมีการเปลี่ยนแปลง
