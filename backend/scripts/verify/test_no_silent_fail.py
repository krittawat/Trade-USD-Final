"""
Test No Silent Fail — ทดสอบว่าไม่มี silent exceptions.

ตรวจว่า:
    - ทุก exception ถูก log พร้อม stacktrace + context
    - ไม่มี bare except:pass
    - Strategy errors return HOLD (ไม่ crash ทั้งระบบ)
"""
# TODO: implement grep-based test + runtime exception tests
