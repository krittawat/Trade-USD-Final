# Forensic Audit Report
**Date:** 2026-02-12 09:27:38

## 1. Executive Summary
- **Total Trades:** 284
- **Net PnL:** $-25.58
- **Win Rate:** 65.85%
- **Profit Factor:** 0.86
- **Max Drawdown:** $-55.49
- **Expectancy:** $-0.09 per trade

## 2. Root Cause Analysis
### Loss Clusters (Consecutive Losses)
Top 5 worst streaks:
- ** Streak #1**: 5 losses, Total: $-21.96, Time: 2026-02-10T12:16:57
- ** Streak #2**: 2 losses, Total: $-12.7, Time: 2026-02-11T04:53:02
- ** Streak #3**: 2 losses, Total: $-12.53, Time: 2026-02-06T23:12:37
- ** Streak #4**: 5 losses, Total: $-8.25, Time: 2026-02-08T14:41:09
- ** Streak #5**: 2 losses, Total: $-7.63, Time: 2026-02-09T03:16:58

### Session Bleed (PnL by Hour)
- **Hour 05:00**: $-17.93
- **Hour 12:00**: $-10.35
- **Hour 17:00**: $-7.88
- **Hour 13:00**: $-6.93
- **Hour 03:00**: $-6.57

### Trade Exit Reasons
- **Gateway**: 95
- **SL**: 79
- **Mobile**: 55
- **Client**: 44
- **TP**: 11

## 3. Recommended Fixes (Blueprint)
- [ ] CRITICAL: System is losing money. Enable Dry Run immediately.
- [ ] Implement Cooldown: Detected 5 consecutive losses.
- [ ] Review SL Placement: SL hits are > 2x TP hits. Stop might be too tight.
