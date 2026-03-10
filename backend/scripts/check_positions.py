"""
Quick script to check all open positions and generate a risk report.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
from datetime import datetime, timezone

def main():
    if not mt5.initialize():
        print("❌ MT5 initialize failed:", mt5.last_error())
        return
    
    # Account info
    acc = mt5.account_info()
    if acc:
        print("=" * 70)
        print(f"📊 ACCOUNT STATUS")
        print("=" * 70)
        print(f"  Balance:      ${acc.balance:,.2f}")
        print(f"  Equity:       ${acc.equity:,.2f}")
        print(f"  Margin:       ${acc.margin:,.2f}")
        print(f"  Free Margin:  ${acc.margin_free:,.2f}")
        print(f"  Margin Level: {acc.margin_level:.1f}%")
        print(f"  Floating P/L: ${acc.profit:,.2f}")
        capital_floor = acc.balance * 0.90
        print(f"  Capital Floor (90%): ${capital_floor:,.2f}")
        if acc.equity >= capital_floor:
            print(f"  ✅ Equity ABOVE capital floor (+${acc.equity - capital_floor:,.2f})")
        else:
            print(f"  ⚠️ Equity BELOW capital floor (-${capital_floor - acc.equity:,.2f})")
    
    # Positions
    positions = mt5.positions_get()
    if positions is None or len(positions) == 0:
        print("\n📭 No open positions.")
        mt5.shutdown()
        return
    
    print(f"\n{'=' * 70}")
    print(f"📋 OPEN POSITIONS ({len(positions)} total)")
    print(f"{'=' * 70}")
    
    total_profit = 0
    total_risk_usd = 0
    
    for p in positions:
        is_buy = (p.type == mt5.ORDER_TYPE_BUY)
        direction = "🟢 BUY" if is_buy else "🔴 SELL"
        
        # Get symbol info for point value
        sym_info = mt5.symbol_info(p.symbol)
        point = sym_info.point if sym_info else 0.00001
        digits = sym_info.digits if sym_info else 5
        contract_size = sym_info.trade_contract_size if sym_info else 100000
        
        # Calculate R-value
        if p.sl > 0:
            if is_buy:
                sl_dist = p.price_open - p.sl
                profit_dist = p.price_current - p.price_open
            else:
                sl_dist = p.sl - p.price_open
                profit_dist = p.price_open - p.price_current
            
            current_r = profit_dist / sl_dist if sl_dist > 0 else 0
            
            # Estimate risk in USD
            sl_points = sl_dist / point if point > 0 else 0
            tick_value = sym_info.trade_tick_value if sym_info else 1.0
            tick_size = sym_info.trade_tick_size if sym_info else point
            risk_usd = (sl_dist / tick_size) * tick_value * p.volume if tick_size > 0 else 0
        else:
            current_r = 0
            sl_dist = 0
            risk_usd = 0
        
        total_profit += p.profit
        total_risk_usd += risk_usd
        
        # Time held
        open_time = datetime.fromtimestamp(p.time, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        held = now - open_time
        held_hours = held.total_seconds() / 3600
        
        # TP distance
        if p.tp > 0:
            if is_buy:
                tp_dist = p.tp - p.price_open
            else:
                tp_dist = p.price_open - p.tp
            rr_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
        else:
            tp_dist = 0
            rr_ratio = 0
        
        # Status emoji
        if current_r >= 1.0:
            status = "🚀 In Profit (+1R+)"
        elif current_r >= 0:
            status = "📈 Positive"
        elif current_r >= -0.5:
            status = "⚠️ Slightly Negative"
        else:
            status = "🔥 At Risk"
        
        print(f"\n{'─' * 70}")
        print(f"  {direction}  {p.symbol}  |  Ticket: {p.ticket}")
        print(f"  Volume: {p.volume} lots  |  Held: {held_hours:.1f} hours")
        print(f"  Entry:    {p.price_open:.{digits}f}")
        print(f"  Current:  {p.price_current:.{digits}f}")
        print(f"  SL:       {p.sl:.{digits}f}  {'✅' if p.sl > 0 else '⚠️ NO SL!'}")
        print(f"  TP:       {p.tp:.{digits}f}  {'✅' if p.tp > 0 else '⚠️ NO TP!'}")
        print(f"  Profit:   ${p.profit:,.2f}")
        print(f"  Risk USD: ${risk_usd:,.2f}")
        print(f"  R-Value:  {current_r:+.2f}R")
        print(f"  RR Ratio: 1:{rr_ratio:.1f}")
        print(f"  Status:   {status}")
        
        # Risk per trade vs equity
        if acc and risk_usd > 0:
            risk_pct = (risk_usd / acc.equity) * 100
            print(f"  Risk/Equity: {risk_pct:.1f}%  {'✅ ≤2%' if risk_pct <= 2 else '⚠️ >2%!'}")
        
        # Weekend gap risk assessment
        print(f"  📊 Gap Risk: ", end="")
        if "XAU" in p.symbol or "GOLD" in p.symbol:
            print("MEDIUM (Gold can gap on geopolitics)")
        elif "BTC" in p.symbol:
            print("HIGH (Crypto trades 24/7, watch for weekend moves)")
        elif "XAG" in p.symbol:
            print("MEDIUM (Silver correlates with Gold)")
        else:
            print("LOW-MEDIUM (Major Forex)")
    
    # Summary
    print(f"\n{'=' * 70}")
    print(f"📊 SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Total Positions: {len(positions)}")
    print(f"  Total Floating P/L: ${total_profit:,.2f}")
    print(f"  Total Risk (SL): ${total_risk_usd:,.2f}")
    if acc:
        print(f"  Total Risk/Equity: {(total_risk_usd/acc.equity)*100:.1f}%")
        dd_pct = abs(min(0, total_profit)) / acc.equity * 100 if total_profit < 0 else 0
        print(f"  Floating DD: {dd_pct:.1f}% {'✅ <10%' if dd_pct < 10 else '⚠️ >10%!'}")
    
    # Recommendations
    print(f"\n{'=' * 70}")
    print(f"💡 RECOMMENDATIONS FOR MARKET OPEN")
    print(f"{'=' * 70}")
    
    for p in positions:
        is_buy = (p.type == mt5.ORDER_TYPE_BUY)
        if p.sl > 0:
            if is_buy:
                sl_dist = p.price_open - p.sl
                profit_dist = p.price_current - p.price_open
            else:
                sl_dist = p.sl - p.price_open
                profit_dist = p.price_open - p.price_current
            current_r = profit_dist / sl_dist if sl_dist > 0 else 0
        else:
            current_r = 0
        
        print(f"\n  {p.symbol} (Ticket {p.ticket}):")
        if p.sl <= 0:
            print(f"    🚨 CRITICAL: No SL! Set SL immediately at market open!")
        
        if current_r >= 1.0:
            print(f"    ✅ Consider moving SL to Break-Even (currently {current_r:+.1f}R)")
            print(f"    💡 Trailing stop should activate automatically")
        elif current_r >= 0:
            print(f"    👀 Monitor closely, still in profit zone")
            print(f"    💡 Watch for gap at open, SL is protecting")
        elif current_r >= -0.5:
            print(f"    ⚠️ Slightly underwater, let trade play out")
            print(f"    💡 SL will protect if gap goes wrong")
        else:
            print(f"    🔥 Significantly underwater ({current_r:+.1f}R)")
            print(f"    💡 Consider closing at market open if fundamentals changed")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
