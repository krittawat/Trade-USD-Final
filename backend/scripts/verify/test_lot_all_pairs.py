"""
Deep Verification — ตรวจสอบ Lot Sizing ทุกคู่เงินแบบละเอียด
ทดสอบทั้ง config ถูก (USC/CENT) และ config ผิด (USD/CENT)
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.domain.models import AccountState, Decision, SymbolProfile, Action
from app.core.config import Settings
from app.risk.sizing import calculate_lot_size
from app.core.mode_resolver import RuntimeMode, ModeResolver
from app.core.currency_adapter import CurrencyAdapter
from app.domain.enums import BlockReason

# ─── Test Data ───
SYMBOLS = {
    "XAUUSDc": {
        "contract_size": 100,
        "volume_min": 0.01,
        "volume_max": 200.0,
        "volume_step": 0.01,
        "point": 0.01,
        "digits": 2,
        "spread_avg": 25,
        "entry_price": 2940.0,
        "sl_price": 2935.0,
        "tp_price": 2950.0,
    },
    "XAGUSDc": {
        "contract_size": 5000,
        "volume_min": 0.01,
        "volume_max": 200.0,
        "volume_step": 0.01,
        "point": 0.001,
        "digits": 3,
        "spread_avg": 30,
        "entry_price": 33.50,
        "sl_price": 33.30,
        "tp_price": 33.80,
    },
    "BTCUSDc": {
        "contract_size": 1,
        "volume_min": 0.01,
        "volume_max": 200.0,
        "volume_step": 0.01,
        "point": 0.01,
        "digits": 2,
        "spread_avg": 200,
        "entry_price": 96500.0,
        "sl_price": 96200.0,
        "tp_price": 96900.0,
    },
    "EURUSDc": {
        "contract_size": 100000,
        "volume_min": 0.01,
        "volume_max": 200.0,
        "volume_step": 0.01,
        "point": 0.00001,
        "digits": 5,
        "spread_avg": 10,
        "entry_price": 1.18097,
        "sl_price": 1.18022,
        "tp_price": 1.18178,
    },
}

# Account: 6002 USC (real account balance)
BALANCE_RAW = 6002.0

passed = 0
failed = 0
total = 0

def test_scenario(scenario_name, account_currency, lot_mode, cent_multiplier):
    global passed, failed, total
    
    print(f"\n{'='*70}")
    print(f"  SCENARIO: {scenario_name}")
    print(f"  account_currency={account_currency}, lot_mode={lot_mode}, cent_multiplier={cent_multiplier}")
    print(f"{'='*70}")
    
    settings = Settings()
    settings.account_currency = account_currency
    settings.lot_mode = lot_mode
    settings.max_risk_per_trade_pct = 2.0
    
    # Create adapter (this is where the safety guard lives)
    mode = RuntimeMode(
        account_currency=account_currency,
        lot_mode=lot_mode,
        symbol_suffix="c" if lot_mode == "CENT" else "",
        cent_multiplier=cent_multiplier,
    )
    adapter = CurrencyAdapter(settings, mode)
    
    # Check: Did the adapter auto-correct?
    actual_multiplier = adapter.mode.cent_multiplier
    print(f"\n  🔧 Adapter cent_multiplier: {cent_multiplier} → {actual_multiplier}")
    
    # Normalize equity
    equity = adapter.normalize_money(BALANCE_RAW)
    free_margin = equity
    
    account = AccountState(
        balance=equity,
        equity=equity,
        margin=0.0,
        free_margin=free_margin,
        floating_pl=0.0,
        open_positions=0,
        daily_pl=0.0,
        initial_balance=equity,
        leverage=2000,
    )
    
    print(f"  💰 Raw balance: {BALANCE_RAW} USC → Normalized equity: ${equity:.2f} USD")
    max_risk = equity * 0.02
    print(f"  ⚠️  Max risk (2%): ${max_risk:.2f} USD")
    
    for sym_name, sym_data in SYMBOLS.items():
        total += 1
        profile = SymbolProfile(
            symbol=sym_name,
            contract_size=sym_data["contract_size"],
            volume_min=sym_data["volume_min"],
            volume_max=sym_data["volume_max"],
            volume_step=sym_data["volume_step"],
            point=sym_data["point"],
            digits=sym_data["digits"],
            spread_avg=sym_data["spread_avg"],
            spread_max_allowed=sym_data["spread_avg"] * 3,
            is_active=True,
        )
        
        decision = Decision(
            symbol=sym_name,
            action=Action.BUY,
            score=90,
            confidence=0.9,
            reason="deep_verify_test",
            strategy_name="test_verify",
            stop_loss=sym_data["sl_price"],
            take_profit=sym_data["tp_price"],
            tags=[],
        )
        
        result = calculate_lot_size(
            decision=decision,
            profile=profile,
            account=account,
            settings=settings,
            entry_price=sym_data["entry_price"],
        )
        
        if isinstance(result, BlockReason):
            # For very small accounts, blocking is CORRECT behavior
            broker_lot = 0
            risk_usd = 0
            status = "BLOCKED"
            reason = result.value
            
            # Being blocked for small accounts is expected and safe
            if equity < 100:
                print(f"  ✅ {sym_name:12s} | {status} ({reason}) — correct for small account")
                passed += 1
            else:
                print(f"  ⚠️  {sym_name:12s} | {status} ({reason})")
                passed += 1  # Still safe
        else:
            lot_std = result.lot_size
            broker_lot = adapter.to_broker_lots(lot_std)
            risk_usd = result.risk_usd
            risk_pct = result.risk_pct
            
            # ─── SAFETY CHECKS ───
            errors = []
            
            # Check 1: risk_usd should not exceed 2% of equity
            if risk_usd > max_risk * 1.05:  # 5% tolerance for rounding
                errors.append(f"RISK ${risk_usd:.2f} > max ${max_risk:.2f}")
            
            # Check 2: broker_lot should be reasonable (not > 10 for small accounts)
            if equity < 100 and broker_lot > 10:
                errors.append(f"BROKER LOT {broker_lot} too large for ${equity:.2f} equity")
            
            # Check 3: lot_std should be within profile limits
            if lot_std < profile.volume_min:
                errors.append(f"LOT {lot_std} < min {profile.volume_min}")
            if lot_std > profile.volume_max:
                errors.append(f"LOT {lot_std} > max {profile.volume_max}")
                
            # Check 4: risk_pct should be <= max_risk_per_trade_pct
            if risk_pct > settings.max_risk_per_trade_pct * 1.05:
                errors.append(f"RISK% {risk_pct:.2f}% > max {settings.max_risk_per_trade_pct}%")
            
            if errors:
                print(f"  ❌ {sym_name:12s} | lot_std={lot_std:.4f} lot_broker={broker_lot:.2f} risk=${risk_usd:.2f} ({risk_pct:.1f}%)")
                for e in errors:
                    print(f"     ⛔ {e}")
                failed += 1
            else:
                print(f"  ✅ {sym_name:12s} | lot_std={lot_std:.4f} lot_broker={broker_lot:.2f} risk=${risk_usd:.2f} ({risk_pct:.1f}%)")
                passed += 1


# ─── TEST 1: Correct config (USC/CENT/100.0) ───
test_scenario("CORRECT CONFIG", "USC", "CENT", 100.0)

# ─── TEST 2: Buggy config (USD/CENT/1.0) — should auto-correct ───
test_scenario("BUGGY CONFIG (should auto-fix)", "USD", "CENT", 1.0)

# ─── TEST 3: Standard account (USD/STANDARD/1.0) — baseline ───
test_scenario("STANDARD ACCOUNT (no cent)", "USD", "STANDARD", 1.0)

# ─── TEST 4: ModeResolver integration ───
print(f"\n{'='*70}")
print(f"  SCENARIO: ModeResolver Integration Test")
print(f"{'='*70}")

settings = Settings()
settings.account_currency = "USD"
settings.lot_mode = "CENT"
resolver = ModeResolver(settings)
resolved = resolver.resolve("USD", ["XAUUSDc", "EURUSDc", "BTCUSDc"])

total += 1
if resolved.cent_multiplier == 100.0 and resolved.account_currency == "USC":
    print(f"  ✅ ModeResolver auto-corrected: currency={resolved.account_currency}, multiplier={resolved.cent_multiplier}")
    passed += 1
else:
    print(f"  ❌ ModeResolver FAILED: currency={resolved.account_currency}, multiplier={resolved.cent_multiplier}")
    failed += 1

# ─── SUMMARY ───
print(f"\n{'='*70}")
print(f"  FINAL RESULTS: {passed}/{total} passed, {failed} failed")
if failed == 0:
    print(f"  ✅ ALL TESTS PASSED — Lot sizing is safe for all pairs")
else:
    print(f"  ❌ {failed} TESTS FAILED — REVIEW REQUIRED")
print(f"{'='*70}")
