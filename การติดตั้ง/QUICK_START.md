# ⚡ Quick Start — ติดตั้งเร่งด่วน (5 นาที)

> ฉบับย่อสำหรับคนที่เปิดหน้าคู่มือเต็มแล้วรู้สึกยาว
> คู่มือเต็ม: ดู [README.md](README.md)
> **อัปเดต:** 2026-03-02 — OPUS Trading Engine v3.0

---

## ✅ ต้องมีก่อน

- [ ] Python 3.11+ (ติ๊ก "Add to PATH")
- [ ] MetaTrader 5 (เปิด + ล็อกอินแล้ว)

---

## 🚀 ขั้นตอน

### 1. Copy โปรเจคมาที่เครื่องใหม่

```powershell
# วิธี Git Clone
cd D:\VibeCode
git clone <URL> Trade

# หรือ Copy โฟลเดอร์จากเครื่องเดิม (ไม่ต้อง copy .venv, .env)
```

### 2. รันสคริปต์ติดตั้งอัตโนมัติ

```powershell
cd D:\VibeCode\Trade\การติดตั้ง

# ดับเบิ้ลคลิก หรือรัน:
.\setup_new_machine.bat
```

> สคริปต์จะตรวจ prerequisites → สร้าง venv → ติดตั้ง packages → สร้างโฟลเดอร์ → เปิด .env ให้แก้

### 3. แก้ไฟล์ .env

```ini
# ค่าที่ต้องแก้ (สำคัญมาก!)
TRADING_MODE=DRY_RUN
MT5_LOGIN=12345678
MT5_PASSWORD=YourPassword
MT5_SERVER=Exness-MT5Real
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
ACCOUNT_CURRENCY=USC
LOT_MODE=CENT
TRADING_SYMBOLS=XAUUSDc,XAGUSDc,BTCUSDc
```

### 4. เปิดระบบ

```powershell
cd D:\VibeCode\Trade
.\.venv\Scripts\Activate.ps1
python backend/trader/main.py --mode dry_run
```

### 5. ตรวจสอบ

```powershell
# รัน QC Suite
python backend/trader/scripts/qc_suite.py

# ดู logs
Get-Content trader\logs\opus_trading.log -Tail 20
```

---

## ⛔ หยุดฉุกเฉิน

กด `Ctrl+C` ใน terminal ที่รัน OPUS Engine

---

## ⚠️ ก่อนเปิด LIVE

1. QC Suite ผ่าน 100%
2. DRY_RUN เสถียร ≥ 30 นาที
3. ดู Safety Checklist ใน [README.md](README.md#12-safety-checklist-ก่อนเปิด-live)
