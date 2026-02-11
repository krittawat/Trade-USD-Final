# ============================================================================
# smoke_live.ps1 — Smoke test สำหรับ LIVE mode
# ============================================================================
# ตรวจว่า: SL ถูกใส่, BE ทำงาน, kill switch ทำงาน
# ============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "Running LIVE Smoke Test..." -ForegroundColor Red
Write-Host "WARNING: ต้องเชื่อมต่อ MT5 จริง!" -ForegroundColor Red

# TODO: implement live smoke tests
# 1. ส่ง micro lot order
# 2. ตรวจว่า SL ถูกใส่
# 3. ทดสอบ break-even
# 4. ทดสอบ kill switch
# 5. ปิดออเดอร์

Write-Host "Smoke test stub — ยังไม่ได้ implement" -ForegroundColor Yellow
