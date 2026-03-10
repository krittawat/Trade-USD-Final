"""
Test Sizing — ทดสอบการคำนวณ lot size และ Risk Parity.
"""
import pytest
from app.core.config import Settings
from app.domain.enums import Action, BlockReason
from app.domain.models import AccountState, Decision, SymbolProfile
from app.risk.sizing import calculate_lot_size, get_risk_parity_multiplier


# ─── Mock Data ───

@pytest.fixture
def mock_settings():
    settings = Settings()
    settings.account_currency = "USD"
    settings.lot_mode = "STANDARD"
    settings.max_risk_per_trade_pct = 2.0
    settings.risk_parity_enabled = True
    return settings

@pytest.fixture
def mock_profile():
    return SymbolProfile(
        symbol="USDJPY",
        name="USDJPY", # ใช้ USDJPY จะได้ไม่ต้องติด Gold cap (0.5%)
        digits=3,
        point=0.001,
        contract_size=100000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        spread_avg=15.0,
        margin_currency="USD",
        profit_currency="USD",
        stops_level=0,
    )

@pytest.fixture
def mock_account():
    return AccountState(
        balance=10000.0,
        equity=10000.0,
        free_margin=10000.0,
        margin_level=1000.0,
        day_drawdown_pct=0.0,
        daily_pl=0.0,
        open_positions=0,
        leverage=2000,
        floating_pl=0.0,
        initial_balance=10000.0,
    )

@pytest.fixture
def mock_decision():
    return Decision(
        symbol="USDJPY",
        action=Action.BUY,
        confidence=0.9,
        strategy_name="test_strat",
        stop_loss=150.000,
        take_profit=152.000,
        reason="Test",
        risk_pct=2.0,
    )


# ─── Tests ───

def test_risk_parity_multiplier_normal():
    # ตลาดปกติ: current_atr แถวๆ baseline
    # current = 14, baseline = 14 -> multiplier = 1.0 (ไม่มีผล)
    mult = get_risk_parity_multiplier(current_atr=14.0, baseline_atr=14.0)
    assert mult == 1.0

def test_risk_parity_multiplier_high_volatility():
    # ตลาดสวิงแรง: current_atr > baseline_atr (เช่น ช่วงข่าว)
    # current = 28, baseline = 14 -> multiplier = 0.5 (ลด lot ลงครึ่งนึง)
    mult = get_risk_parity_multiplier(current_atr=28.0, baseline_atr=14.0)
    assert mult == 0.5

def test_risk_parity_multiplier_low_volatility():
    # ตลาดซึม: current_atr < baseline_atr
    # current = 7, baseline = 14 -> multiplier = 2.0 แต่ระบบห้ามเกิน 1.0
    mult = get_risk_parity_multiplier(current_atr=7.0, baseline_atr=14.0)
    assert mult == 1.0  # ป้องกันไม่ให้ over-leverage ตอนตลาดซึม

def test_risk_parity_multiplier_clip_min():
    # ตลาดสวิงโหดมาก (Flash Crash) -> ลดสุดแค่เหลือ 20% (0.2x)
    mult = get_risk_parity_multiplier(current_atr=140.0, baseline_atr=14.0)
    assert mult == 0.2

def test_calculate_lot_size_with_risk_parity(mock_settings, mock_profile, mock_account, mock_decision):
    """ทดสอบว่า lot size ลดลงจริงมั้ยเมื่อ Risk Parity สั่งลด"""
    
    entry_price = 151.000
    sl_distance = 1.0  # เข้า 151.000, SL 150.000 -> 1.0 ระยะ
    # Base risk = 2% of 10000 = $200
    # $200 / (1.0 * 100000) = $200 / 100000 = 0.002
    # But max risk for FX inside calculate_lot_size is capped at 1.0%
    # So max_risk_pct = 1.0 -> Risk USD = $100
    # $100 / (1.0 * 100000) = 0.001 -> lot < min (0.01) so blocked!
    
    # Let's adjust sl_distance so lot >= 0.01:
    # Say we want lot = 0.10: $100 / (sl_dist * 100000) = 0.10 -> sl_dist * 10000 = 100 -> sl_dist = 0.01
    entry_price = 150.010
    mock_decision.stop_loss = 150.000 # SL distance = 0.010
    
    # 1. เทรดปกติ (Risk Parity ไม่ปรับลด)
    plan_normal = calculate_lot_size(
        decision=mock_decision,
        profile=mock_profile,
        account=mock_account,
        settings=mock_settings,
        entry_price=entry_price,
        current_atr=14.0,
        baseline_atr=14.0
    )
    assert not isinstance(plan_normal, BlockReason)
    assert plan_normal.lot_size == 0.10 # $100 risk / (0.01 * 100000) = 0.10 lot

    # 2. เทรดตอนผันผวนสูง (Risk Parity ตัด Lot ลงครึ่งนึง)
    plan_volatile = calculate_lot_size(
        decision=mock_decision,
        profile=mock_profile,
        account=mock_account,
        settings=mock_settings,
        entry_price=entry_price,
        current_atr=28.0,
        baseline_atr=14.0
    )
    
    assert plan_volatile.lot_size == 0.05  # lot ควรจะลดลงเหลือ 0.05 (ลด 50%!)

from unittest.mock import patch
from app.domain.enums import MarketSession

@patch("app.risk.sizing.get_current_session")
def test_calculate_lot_size_session_targeting(mock_get_session, mock_settings, mock_profile, mock_account, mock_decision):
    """ทดสอบว่า lot size ลดลง 50% ในช่วง ASIA Session"""
    
    mock_settings.account_currency = "USC"
    mock_settings.lot_mode = "CENT"
    mock_decision.symbol = "XAUUSDc"
    mock_profile.symbol = "XAUUSDc"
    mock_profile.name = "XAUUSDc"
    # Base risk settings: 2.0% -> risk USD = $200
    entry_price = 2000.00
    mock_decision.confidence = 0.5    # Lower confidence so min_lot = 0.10
    mock_decision.stop_loss = 1995.00 # sl_distance = 5.0 -> raw lot = 0.40
    
    # 1. NEW_YORK Session (Lot เต็ม)
    mock_get_session.return_value = MarketSession.NEW_YORK
    plan_ny = calculate_lot_size(
        decision=mock_decision,
        profile=mock_profile,
        account=mock_account,
        settings=mock_settings,
        entry_price=entry_price,
    )
    assert not isinstance(plan_ny, BlockReason)
    # $200 / (10 * 100000) = 0.0002 std lots ?
    # Let's just check relative sizes: lot in ASIA should be exactly half of NY
    
    # 2. ASIA Session (Lot ลดครึ่ง)
    mock_get_session.return_value = MarketSession.ASIA
    plan_asia = calculate_lot_size(
        decision=mock_decision,
        profile=mock_profile,
        account=mock_account,
        settings=mock_settings,
        entry_price=entry_price,
    )
    assert not isinstance(plan_asia, BlockReason)
    assert plan_asia.lot_size == plan_ny.lot_size * 0.5
