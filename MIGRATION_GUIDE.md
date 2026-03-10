# คู่มือการย้ายระบบสู่ Bi-Directional Currency Mode (รองรับ Standard/Cent)

## ภาพรวม (Overview)
ขณะนี้ระบบรองรับการทำงาน 2 โหมดพร้อมกัน:
1. **USD Mode (Standard)**: สำหรับบัญชี Standard ทั่วไป (เช่น Exness Standard)
2. **USC Mode (Cent)**: สำหรับบัญชี Cent (เช่น Exness Standard Cent)

ระบบจะ **ตรวจสอบอัตโนมัติ (Auto-detect)** ว่าต้องใช้โหมดไหน โดยดูจากสกุลเงินของพอร์ต MT5 และรายชื่อสัญลักษณ์ที่มีให้เทรด หรือคุณสามารถบังคับเลือกโหมดผ่าน Config ได้

## หลักการทำงานภายใน (Internal Logic)
- **Always USD**: ระบบความคิดภายใน (Risk Engine, Strategy, Database) จะคำนวณทุกอย่างเป็น **USD** และ **Standard Lots** เสมอ เพื่อความแม่นยำและเป็นมาตรฐานเดียว
- **Conversion at Boundary**: การแปลงค่า (USD <-> USC) และแปลง Lot (Standard <-> Cent) จะเกิดขึ้นเฉพาะตอน **รับ/ส่ง ข้อมูลกับ MT5** เท่านั้น (Boundaries)

## การตั้งค่า (Configuration - .env)

มีการเพิ่มตัวแปรใหม่ใน `.env`:

```ini
# เลือกสกุลเงิน: AUTO | USD | USC
MODE_ACCOUNT_CURRENCY=AUTO

# เลือกโหมด Lot: AUTO | STANDARD | CENT
MODE_LOT=AUTO
```

### การตั้งค่าที่แนะนำ (Recommended)

#### แบบ Auto-Detection (แนะนำ - สลับบัญชีได้เลย)
```ini
MODE_ACCOUNT_CURRENCY=AUTO
MODE_LOT=AUTO
```
ระบบจะเช็คเองว่าถ้าเจอสกุลเงิน "USC" หรือมีคู่เงินลงท้ายด้วย "c" จะสลับเป็น Cent Mode ให้ทันที

#### แบบบังคับ USD Mode (Standard)
```ini
MODE_ACCOUNT_CURRENCY=USD
MODE_LOT=STANDARD
```

#### แบบบังคับ Cent Mode
```ini
MODE_ACCOUNT_CURRENCY=USC
MODE_LOT=CENT
```

## การตรวจสอบ (Verification)

### วิธีเช็ค USD Mode
1. รันไฟล์ `scripts/smoke_usd.ps1`
2. ดู Logs ว่า:
   - `mt5_mode_detected ... account_currency='USD'`
   - `lot_calculated ... lot=0.01` (คือ 0.01 Standard)
   - `send_order ... lot_broker=0.01` (ส่งไป MT5 เป็น 0.01)

### วิธีเช็ค USC Mode (Cent)
1. รันไฟล์ `scripts/smoke_usc.ps1`
2. ดู Logs ว่า:
   - `mt5_mode_detected ... account_currency='USC'`
   - `lot_calculated ... lot=0.01` (ภายในคำนวณเป็น 0.01 Standard)
   - `send_order ... lot_broker=1.00` (ส่งไป MT5 เป็น 1.00 Cent Lot) -> **จุดสำคัญ**
   - Symbol ถูกแมพเป็น `XAUUSDc` (ถ้ามี suffix)

## การเปลี่ยนแปลงฐานข้อมูล (Database Changes)
เพิ่มคอลัมน์ใหม่ในตาราง `trade_journal` อัตโนมัติ:
- `account_currency`
- `lot_mode`
- `symbol_suffix`
- `balance_usd` (ยอดเงินแปลงเป็น USD)
- `balance_account` (ยอดเงินจริงในพอร์ต)

เพียงแค่รันบอทหนึ่งครั้ง ระบบจะทำการ Migrate ฐานข้อมูลให้เอง
