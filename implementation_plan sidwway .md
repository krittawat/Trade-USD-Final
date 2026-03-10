# Implementation Plan: Antigravity V2 Optimization (USC-Version)

นี่คือแผนงาน (Implementation Plan) สำหรับการปรับปรุงระบบ Antigravity V2 ตามคำสั่ง 7 ข้อของคุณ เพื่อเจาะเป้าหมายทำกำไร 600-2000 บาท/วัน (USC) และรักษาทุนเป็นหลัก

## User Review Required
> [!IMPORTANT]
> - **ข้อมูล Sideways:** เพื่อให้ Train AI Brain ได้แม่นยำที่สุด คุณต้องการให้ดึงข้อมูลย้อนหลังจาก MT5 อัตโนมัติ (เช่น 3-6 เดือนย้อนหลัง) หรือคุณมีไฟล์ Dataset ที่เตรียมไว้แล้ว?
> - **Symbol ใหม่:** การเพิ่ม `BTCUSDc` (Crypto) และ `USDJPYc` (Forex) จะทำให้การจัดการ Risk Layer ซับซ้อนขึ้นเล็กน้อย (Tick value แตกต่างจาก Gold/Silver) ระบบจะคำนวณอัตโนมัติ แต่ต้องให้ชัวร์ว่า MT5 Account อนุญาตการเทรด Lot size ขั้นต่ำตามสเปกของ 2 ตัวนี้

---

## Proposed Changes

### 1. Strategy Tuning & Backtesting (หาและซ่อมเกณฑ์ที่ Win Rate ต่ำ)
- **เป้าหมาย:** ค้นหากลยุทธ์ที่ Win Rate < 50% หรือ Profit Factor < 1.3
- **Action:** สร้าง/ปรับปรุง Python script เพื่อดึงประวัติการรันย้อนหลัง แล้วทำการปรับ Parameter (เช่น SL ATR multiplier, R:R ratio) ให้สอดคล้องกับพฤติกรรมปัจจุบัน

### 2. EMA180 MTF FVG Optimization
#### [MODIFY] [backend/app/strategy/templates/ema180_mtf_fvg.py](file:///d:/VibeCode/Trade/backend/app/strategy/templates/ema180_mtf_fvg.py)
- **Action:** ปรับ Parameter สำหรับการตรวจจับ Fair Value Gap (FVG) และ Bounce Tolerance
- **Verify:** สร้างบอทจำลองเพื่อรัน Backtest ย้อนหลังเฉพาะ `ema180_mtf_fvg` เพื่อดู Performance จริง (Win/Loss ratio, Max DD)

### 3. AI Brain Sideways Training
#### [MODIFY] [backend/app/brain/trainer.py](file:///d:/VibeCode/Trade/backend/app/brain/trainer.py) (หรือสร้างสคริปต์ Trainer แยก)
- **Action:** พัฒนาฟังก์ชันพิเศษสำหรับป้อนข้อมูลสภาวะตลาดแบบ Sideways (`RegimeType.RANGING`) ให้กับ `MemoryStore` เพื่อให้ AI ลดน้ำหนัก (Penalize) กลยุทธ์แบบ Trend Follower เมื่ออยู่ใน Sideways ป้องกันการเกิด Overtrade
- **Verify:** เช็คค่าในตาราง SQLite (Memory) ว่าสถิติของกลยุทธ์เมื่อ `regime = RANGING` มีการอัปเดตอย่างถูกต้อง

### 4. Shadow Mode Evaluation
#### [MODIFY] [backend/app/brain/shadow_evaluator.py](file:///d:/VibeCode/Trade/backend/app/brain/shadow_evaluator.py)
- **Action:** ตรวจสอบและทำให้ระบบประเมินผลออเดอร์เงามีความละเอียดมากขึ้น ว่า Strategy ไหนทำได้ดีใน Symbol ไหนและ Regime ไหน
- **Verify:** จำลองการทำงาน 10 นาทีเพื่อให้ Shadow Evaluator ทำงาน และเช็ค Logs ว่าผลเทียบกันแล้วถูกต้องเป๊ะ

### 5. Dashboard Block Reason ตรวจหาสาเหตุการ Reject
#### [MODIFY] `frontend/components/...` (Nuxt.js Dashboard)
- **Action:** ตรวจสอบ Source code ของฝั่ง Frontend ว่ารองรับการแสดงผล Block Reason จาก `app.domain.enums.BlockReason` ครบทั้ง 14+ อาการหรือไม่ (เช่น `FLOATING_DD_EXCEEDED`, `NO_STOP_LOSS`)
- **Verify:** ส่ง Mock blocked decision จาก Backend และดูว่าหน้าจอแสดงผลถูกต้องและครบถ้วน ไม่เป็นแค่ UI ว่างๆ

### 6. New Symbol Onboarding
#### [MODIFY] `.env` or `backend/app/core/config.py`
- **Action:** เพิ่ม `BTCUSDc` และ `USDJPYc` เข้าสู่ Config ของระบบ
- **Verify:** ทำการทดสอบโหลด Symbol Profile จาก Broker (Exness) ว่าอ่านค่า Tick Size, Contract Size และ Volume Step ถูกต้อง 100%

---

## Verification Plan
1. **Automated QC Suite:** 
   - รันคำสั่ง `python backend/scripts/verify/qc_suite.py` จะต้อง **PASS 100% (16/16)** ไม่ผ่านห้ามขึ้น LIVE เด็ดขาด
2. **Backtest Fidelity:**
   - รัน Script ทดสอบ `ema180_mtf_fvg` ต้องได้ Win rate ขั้นต่ำ 50%
3. **DRY_RUN Validation (30 mins)**
   - เปิดระบบรัน `run_bot.py` ในโหมด `DRY_RUN` เป็นเวลา 30 นาที 
   - เฝ้าดูว่า Memory และ SQLite ไม่เกิด Lock-up และไม่มี exception ประหลาดหลุดออกมาเงียบๆ
