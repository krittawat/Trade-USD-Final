"""Move orphan strategy files to _archive/orphan_20250222/."""
import shutil
import os

SRC = r'd:\VibeCode\Trade\backend\app\strategy\templates'
DST = os.path.join(SRC, '_archive', 'orphan_20250222')
os.makedirs(DST, exist_ok=True)

ORPHANS = [
    'antichop.py', 'antigravity.py', 'antigravity_fusion.py', 'asian_range_breakout.py',
    'aud_mean_revert.py', 'btc_momentum_strategy.py', 'btc_ultimate_strategy.py',
    'counter_trend_scalper.py', 'dual_scalp.py', 'easy_entry.py', 'easy_fusion.py',
    'easy_trend.py', 'eurusd_scalp.py', 'evolved_candle.py', 'gbp_session_breakout.py',
    'gbpusd_momentum.py', 'ghost_protocol.py', 'gold_proven_edge.py', 'gold_scalp_daily.py',
    'gold_scalp_wr60.py', 'gold_sniper_mini.py', 'hybrid_gold.py', 'hyper_scalp.py',
    'institutional_scalp.py', 'jpy_trend_follow.py', 'kill_zone_strategy.py',
    'omega_emperor.py', 'omega_strike.py', 'omega_strike_v2.py',
    'predicta_futures_v4.py', 'predicta_strategy.py', 'predicta_v4.py',
    'smart_fusion.py', 'smart_sniper.py', 'trend_filter_m15.py',
    'universal_hybrid.py', 'usdjpy_smart.py', 'usdjpy_trend.py', 'vfinal_strategy.py',
]

moved = 0
for f in ORPHANS:
    fp = os.path.join(SRC, f)
    if os.path.exists(fp):
        shutil.move(fp, os.path.join(DST, f))
        moved += 1
        print(f'  moved: {f}')
    else:
        print(f'  skip (not found): {f}')

# Also move ADD_11022026 folder
add11_src = os.path.join(SRC, 'ADD_11022026')
add11_dst = os.path.join(SRC, '_archive', 'ADD_11022026')
if os.path.exists(add11_src):
    if os.path.exists(add11_dst):
        # Merge contents
        for item in os.listdir(add11_src):
            s = os.path.join(add11_src, item)
            d = os.path.join(add11_dst, item)
            if os.path.isfile(s):
                shutil.move(s, d)
        shutil.rmtree(add11_src)
    else:
        shutil.move(add11_src, add11_dst)
    print(f'  moved: ADD_11022026/ -> _archive/ADD_11022026/')
    moved += 1

print(f'\nTotal moved: {moved}')
