import sys
import os
import json
from datetime import datetime, timedelta
import argparse

# Add backend to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.analysis.behavior import BehavioralAnalyzer
from app.analysis.patterns import PatternRecognizer
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

def generate_report(stats: dict, patterns: dict, days: int) -> str:
    """Generate a Markdown report from analysis results."""
    
    # Determine psychological profile
    profile = "Balanced"
    if patterns['loss_chasing']['detected'] or patterns['martingale']['detected']:
        profile = "High Risk / Gambler"
    elif stats.get('overtrading', {}).get('overtrading_detected', False):
        profile = "Overactive / Impulsive"
        
    report = f"""# 🧠 Auto Coach Report
**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Period:** Last {days} days
**Trader Profile:** {profile}

## 📊 Performance Overview
- **Total Trades:** {stats.get('basic', {}).get('total_trades', 0)}
- **Win Rate:** {stats.get('basic', {}).get('win_rate', 0)}%
- **Profit Factor:** {stats.get('basic', {}).get('profit_factor', 0)}
- **Net Profit:** ${stats.get('basic', {}).get('net_profit', 0)}

## 🚨 Behavioral Flags
"""

    # Overtrading
    ot = stats.get('overtrading', {})
    if ot.get('overtrading_detected'):
        report += f"- **OVERTRADING DETECTED** (Score: {ot['score']}/100)\n"
        report += f"  - Avg trades/hour: {ot['mean_hourly_trades']}\n"
        report += f"  - Max trades/hour: {ot['max_hourly_trades']}\n"
    else:
        report += f"- Overtrading: Clean (Avg {ot.get('mean_hourly_trades', 0)} trades/h)\n"

    # Revenge Trading
    rt = stats.get('revenge', {})
    if rt.get('score', 0) > 0:
        report += f"- **REVENGE TRADING WARNING** (Score: {rt['score']}/100)\n"
        report += f"  - {rt['revenge_trades_count']} trades entered < 5min after loss\n"
    else:
        report += "- Revenge Trading: None detected\n"

    # Tilt
    tilt = stats.get('tilt', {})
    if tilt.get('is_tilted'):
        report += f"- **TILT MODE ACTIVE**\n"
        report += f"  - {tilt['tilt_instances']} instances of lot sizing increasing >1.5x after loss\n"
    else:
        report += "- Tilt Control: Stable lot sizing\n"

    report += "\n## 🕸️ Negative Patterns\n"
    
    # Patterns
    if patterns['loss_chasing']['detected']:
        report += f"- ❌ **Chasing Losses**: {patterns['loss_chasing']['count']} sequences found.\n"
    if patterns['martingale']['detected']:
        report += f"- ❌ **Martingale/Doubling Down**: {patterns['martingale']['count']} sequences found.\n"
    if patterns['session_discipline']['detected']:
        report += f"- ⚠️ **Toxic Session Trading**: {patterns['session_discipline']['count']} trades during bad hours.\n"

    if not any(p['detected'] for p in patterns.values()):
        report += "No major negative patterns detected. Good job!\n"

    report += "\n## 🛡️ Action Plan\n"
    if patterns['loss_chasing']['detected'] or rt.get('score', 0) > 20:
        report += "1. **Mandatory Cooldown**: System will lock for 1 hour after 2 consecutive losses.\n"
    if ot.get('overtrading_detected'):
        report += "2. **Reduce Frequency**: Limit max trades per day to 5.\n"
    if tilt.get('is_tilted') or patterns['martingale']['detected']:
        report += "3. **Hard Lot Cap**: Lot size locked to base risk unit (no scaling).\n"
    
    if "Action Plan" not in report:
        report += "Continue following current specific rules. Maintain consistency.\n"

    return report

def main():
    parser = argparse.ArgumentParser(description="Auto Coach Analysis")
    parser.add_argument("--days", type=int, default=30, help="Days of history to analyze")
    parser.add_argument("--output", type=str, default="logs/coach_report.md", help="Output file path")
    args = parser.parse_args()

    print(f"Starting Auto Coach Analysis (Last {args.days} days)...")
    
    client = MT5Client(settings)
    
    try:
        if not client.connect():
            print("Failed to connect to MT5.")
            return
            
        now = datetime.now()
        start = now - timedelta(days=args.days)
        
        print("Fetching trade history...")
        deals = client.get_history_deals(start, now)
        print(f"Fetched {len(deals)} deals.")
        
        if not deals:
            print("No history found.")
            return
            
        # Analysis
        analyzer = BehavioralAnalyzer(deals)
        pattern_recognizer = PatternRecognizer(deals)
        
        stats = {
            "basic": analyzer.calculate_basic_stats(),
            "overtrading": analyzer.calculate_overtrading_score(),
            "revenge": analyzer.calculate_revenge_trading(),
            "tilt": analyzer.calculate_tilt_metric()
        }
        
        patterns = pattern_recognizer.analyze_all()
        
        # Report
        report_content = generate_report(stats, patterns, args.days)
        
        # Save
        output_path = os.path.join(os.getcwd(), args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_content)
            
        print(f"\nAnalysis Complete. Report saved to: {output_path}")
        print("-" * 50)
        print(report_content)
        print("-" * 50)
        
    except Exception as e:
        logger.error("auto_coach_failed", extra={"error": str(e)})
        print(f"Error: {e}")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()
