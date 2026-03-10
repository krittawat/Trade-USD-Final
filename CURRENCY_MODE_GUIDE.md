# 🔄 คู่มือเปลี่ยนโหมด USD ↔ USC (Exness Standard Cent)

## สรุปโหมด

| โหมด | บัญชี | Symbol Suffix | Lot | Equity |
|------|--------|---------------|-----|--------|
| **USD** | Standard / Micro | `m` หรือไม่มี | Standard (0.01 = 0.01 lot) | แสดงเป็น USD |
| **USC** | Standard Cent | `c` | Cent (0.01 = 1 cent lot) | แสดงเป็น USC (100 USC = 1 USD) |

---

## Step 1: แก้ `.env`

### เปลี่ยนเป็น USC (Cent)
```env
TRADING_SYMBOLS=XAUUSDc,EURUSDc,GBPUSDc,USDJPYc,BTCUSDc
ACCOUNT_CURRENCY=USC
LOT_MODE=CENT
```

### เปลี่ยนกลับเป็น USD (Standard/Micro)
```env
TRADING_SYMBOLS=XAUUSDm,EURUSDm,GBPUSDm,USDJPYm
ACCOUNT_CURRENCY=USD
LOT_MODE=STANDARD
```

### ใช้ Auto-Detect (ระบบตรวจเอง)
```env
TRADING_SYMBOLS=XAUUSD,EURUSD,GBPUSD,USDJPY
ACCOUNT_CURRENCY=AUTO
LOT_MODE=AUTO
```

> [!IMPORTANT]
> Symbol suffix ต้องตรงกับ MT5 Market Watch — เช็คใน MT5 ว่าชื่อ symbol ลงท้ายด้วยอะไร (`c`, `m`, หรือไม่มี)

---

## Step 2: ตรวจสอบ MT5

1. เปิด MetaTrader 5
2. ดูที่ **Market Watch** → ชื่อ symbol ต้องตรงกับที่ตั้งใน `.env`
   - Cent account: `XAUUSDc`, `EURUSDc`, ...
   - Micro account: `XAUUSDm`, `EURUSDm`, ...
3. ดูที่ **Navigator → Accounts** → account currency ควรเป็น `USC` (Cent) หรือ `USD`

---

## Step 3: ตรวจสอบว่าระบบทำงานถูกต้อง

### Smoke Test (แนะนำ)
```powershell
# USC mode
powershell scripts/smoke_usc.ps1

# USD mode
powershell scripts/smoke_usd.ps1
```

### ดู Log ที่ต้องเห็น
```
✅ USC mode:
  "mode": "USC"
  "lot_mode": "CENT"  
  "cent_multiplier": 100.0
  "symbol_suffix": "c"

✅ USD mode:
  "mode": "USD"
  "lot_mode": "STANDARD"
  "cent_multiplier": 1.0
  "symbol_suffix": ""
```

---

## ความแตกต่างที่ระบบจัดการอัตโนมัติ

เมื่อตั้งค่าถูกต้อง ระบบจะจัดการสิ่งเหล่านี้เอง — **ไม่ต้องแก้โค้ด**:

| รายการ | USD Mode | USC Mode |
|--------|----------|----------|
| Equity normalization | ใช้ตรง | หาร 100 (500 USC → 5 USD) |
| Lot size | 0.01 = 0.01 std lot | 0.01 std → 1.00 cent lot |
| Symbol mapping | XAUUSD → XAUUSDm | XAUUSD → XAUUSDc |
| Max lot cap | 50.0 | 200.0 |
| Min lot | 0.01 | 0.0001 (std equiv) |
| Risk calculation | ใช้ USD ตรง | แปลง USC→USD ก่อนคำนวณ |

---

## ⚠️ ข้อควรระวัง

1. **ห้ามใช้ symbol suffix ผิดกับประเภทบัญชี** — จะหา symbol ไม่เจอ
2. **Equity ใน USC สูงกว่า 100 เท่า** — ถ้า deposit $5 จะเห็น 500 USC ในบัญชี (ปกติ)
3. **Risk Engine คำนวณเป็น USD เสมอ** — ระบบแปลง USC → USD ก่อนเช็ค risk limit
4. **Backtest scripts มี symbol hardcoded** — ถ้าจะ backtest ต้องระบุ `--symbols XAUUSDc` (ดูที่ scripts แต่ละตัว)
5. **เปลี่ยนโหมดแล้วต้อง restart bot** — config โหลดตอน startup เท่านั้น

---

## ไฟล์ที่เกี่ยวข้อง

| ไฟล์ | หน้าที่ |
|------|---------|
| [.env](file:///d:/VibeCode/Trade/.env) | ตั้งค่าหลัก (symbols, currency, lot mode) |
| [config.py](file:///d:/VibeCode/Trade/backend/app/core/config.py) | Schema + defaults |
| [mode_resolver.py](file:///d:/VibeCode/Trade/backend/app/core/mode_resolver.py) | Auto-detect USD/USC |
| [currency_adapter.py](file:///d:/VibeCode/Trade/backend/app/core/currency_adapter.py) | แปลงเงิน/lot/symbol |
| [client.py](file:///d:/VibeCode/Trade/backend/app/mt5/client.py) | ใช้ adapter ตอนส่ง order |
