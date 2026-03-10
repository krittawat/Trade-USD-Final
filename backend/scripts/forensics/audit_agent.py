import os
import sys
import json
import math
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

# Allow running from anywhere - add 'backend' to sys.path
# script is in backend/scripts/forensics/audit_agent.py
# ../../.. is project root
# ../.. is backend where 'app' resides
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

try:
    import MetaTrader5 as mt5
    from app.core.config import get_settings
    from app.core.logging import get_logger
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

logger = get_logger("forensic_audit")
settings = get_settings()

class MT5HistoryFetcher:
    """Connects to MT5 and fetches trade history deals."""
    
    def __init__(self):
        self.connected = False

    def connect(self) -> bool:
        """Connects to MT5 terminal."""
        if not mt5.initialize():
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False
        
        # If login provided in settings, try to login (optional, usually terminal is already logged in)
        if settings.mt5_login:
            authorized = mt5.login(
                login=settings.mt5_login, 
                password=settings.mt5_password, 
                server=settings.mt5_server
            )
            if not authorized:
                logger.warning(f"MT5 login failed: {mt5.last_error()}, continuing with current terminal state")
        
        self.connected = True
        logger.info(f"Connected to MT5: {mt5.terminal_info()}")
        return True

    def fetch_deals(self, days: int = 30, symbol: Optional[str] = None) -> pd.DataFrame:
        """Fetches deal history and returns a standardized DataFrame."""
        if not self.connected:
            if not self.connect():
                return pd.DataFrame()

        to_date = datetime.now(timezone.utc)
        from_date = to_date - timedelta(days=days)

        logger.info(f"Fetching deals from {from_date} to {to_date}...")
        
        if symbol:
            deals = mt5.history_deals_get(from_date, to_date, group=symbol)
        else:
            deals = mt5.history_deals_get(from_date, to_date)

        if deals is None or len(deals) == 0:
            logger.warning("No deals found.")
            return pd.DataFrame()

        # Convert to list of dicts for DataFrame
        data = []
        for d in deals:
            data.append(d._asdict())

        df = pd.DataFrame(data)
        
        # Normalize and filter
        # Keep only ENTRY_OUT (1) and ENTRY_INOUT (2) for closed trades mostly, 
        # but to reconstruct full PnL we need all deals with profit != 0
        
        # Convert timestamp
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Calculate real profit (profit + commission + swap)
        if 'commission' not in df.columns: df['commission'] = 0.0
        if 'swap' not in df.columns: df['swap'] = 0.0
        df['net_profit'] = df['profit'] + df['commission'] + df['swap']

        logger.info(f"Fetched {len(df)} raw deals.")
        return df

    def shutdown(self):
        mt5.shutdown()


class PerformanceAnalyzer:
    """Analyzes trade data for performance metrics."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.trades = self._filter_trades(df)

    def _filter_trades(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filters for actual closed trades (Entry OUT) with valid profit."""
        if df.empty: return df
        # ENTRY_OUT = 1, ENTRY_INOUT = 2
        mask = (df['entry'].isin([1, 2])) 
        trades = df[mask].copy()
        return trades

    def get_equity_curve(self) -> List[Dict]:
        """Calculates equity curve from trade list."""
        if self.trades.empty: return []
        
        # Sort by time
        trades = self.trades.sort_values('time')
        
        cumulative = 0.0
        curve = []
        for _, row in trades.iterrows():
            cumulative += row['net_profit']
            curve.append({
                "time": row['time'].isoformat(),
                "balance": round(cumulative, 2),
                "profit": round(row['net_profit'], 2)
            })
        return curve

    def calculate_summary(self) -> Dict[str, Any]:
        """Calculates standard performance metrics."""
        if self.trades.empty:
            return {
                "total_trades": 0,
                "net_pnl": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_pct": 0.0
            }

        wins = self.trades[self.trades['net_profit'] > 0]
        losses = self.trades[self.trades['net_profit'] <= 0]

        n_trades = len(self.trades)
        n_wins = len(wins)
        n_losses = len(losses)
        
        gross_profit = wins['net_profit'].sum()
        gross_loss = abs(losses['net_profit'].sum())
        net_pnl = self.trades['net_profit'].sum()

        win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.99

        # Drawdown calculation
        equity_curve = [x['balance'] for x in self.get_equity_curve()]
        running_max = np.maximum.accumulate(equity_curve) if equity_curve else []
        drawdown = np.array(equity_curve) - running_max if equity_curve else []
        max_dd = drawdown.min() if len(drawdown) > 0 else 0.0
        
        # Approximate DD % (assuming starting balance = 0 in curve, this is relative DD)
        # To get real % we need account balance. Here we just return absolute value.
        
        avg_win = wins['net_profit'].mean() if n_wins > 0 else 0
        avg_loss = losses['net_profit'].mean() if n_losses > 0 else 0
        expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)

        return {
            "total_trades": n_trades,
            "net_pnl": round(net_pnl, 2),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2)
        }


class RootCauseDetector:
    """Diagnoses why the system is losing money."""

    def __init__(self, trades: pd.DataFrame):
        self.trades = trades

    def analyze_session_bleed(self) -> Dict[str, float]:
        """Analyzes PnL by hour of day."""
        if self.trades.empty: return {}
        
        df = self.trades.copy()
        df['hour'] = df['time'].dt.hour
        
        hourly_pnl = df.groupby('hour')['net_profit'].sum().to_dict()
        return hourly_pnl

    def analyze_loss_clusters(self) -> List[Dict]:
        """Detects consecutive loss streaks."""
        if self.trades.empty: return []
        
        df = self.trades.sort_values('time')
        streaks = []
        current_streak = 0
        current_loss = 0.0
        start_time = None
        
        for _, row in df.iterrows():
            if row['net_profit'] < 0:
                if current_streak == 0:
                    start_time = row['time']
                current_streak += 1
                current_loss += row['net_profit']
            else:
                if current_streak >= 2:
                    streaks.append({
                        "count": current_streak,
                        "total_loss": round(current_loss, 2),
                        "start_time": start_time.isoformat(),
                        "end_time": row['time'].isoformat()
                    })
                current_streak = 0
                current_loss = 0.0
        
        # Check last streak
        if current_streak >= 2:
             streaks.append({
                "count": current_streak,
                "total_loss": round(current_loss, 2),
                "start_time": start_time.isoformat(),
                "end_time": df.iloc[-1]['time'].isoformat()
            })
            
        return sorted(streaks, key=lambda x: x['total_loss']) # Ascending (max loss first)

    def analyze_stop_dist(self) -> Dict[str, Any]:
        """Analyzes Stop Loss vs TP hits (approximate mainly by reason if available, or PnL)."""
        if self.trades.empty: return {}
        
        # MT5 Deal Reason: 4=SL, 5=TP, 0=Client
        if 'reason' in self.trades.columns:
            reason_counts = self.trades['reason'].value_counts().to_dict()
            # Map codes
            reason_map = {0: 'Client', 1: 'Mobile', 2: 'Web', 3: 'Gateway', 4: 'SL', 5: 'TP', 6: 'SO', 7: 'Roll'}
            mapped_counts = {reason_map.get(k, f"Code_{k}"): v for k, v in reason_counts.items()}
            return mapped_counts
        return {}


class ReportGenerator:
    """Generates Markdown and JSON reports."""
    
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

    def save_json(self, data: Dict[str, Any], filename: str = "audit_data.json"):
        path = os.path.join(self.output_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Saved JSON report to {path}")

    def generate_markdown(self, summary: Dict, root_causes: Dict, fixes: List[str], filename: str = "audit_report.md"):
        path = os.path.join(self.output_dir, filename)
        
        content = f"""# Forensic Audit Report
**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. Executive Summary
- **Total Trades:** {summary.get('total_trades')}
- **Net PnL:** ${summary.get('net_pnl')}
- **Win Rate:** {summary.get('win_rate')}%
- **Profit Factor:** {summary.get('profit_factor')}
- **Max Drawdown:** ${summary.get('max_drawdown')}
- **Expectancy:** ${summary.get('expectancy')} per trade

## 2. Root Cause Analysis
### Loss Clusters (Consecutive Losses)
Top 5 worst streaks:
"""
        for i, streak in enumerate(root_causes.get('loss_clusters', [])[:5]):
            content += f"- ** Streak #{i+1}**: {streak['count']} losses, Total: ${streak['total_loss']}, Time: {streak['start_time']}\n"
            
        content += "\n### Session Bleed (PnL by Hour)\n"
        hourly = root_causes.get('session_bleed', {})
        # Find worst hours
        sorted_hours = sorted(hourly.items(), key=lambda item: item[1])
        for h, pnl in sorted_hours[:5]:
            if pnl < 0:
                content += f"- **Hour {h:02d}:00**: ${pnl:.2f}\n"

        content += "\n### Trade Exit Reasons\n"
        exits = root_causes.get('exit_reasons', {})
        for r, count in exits.items():
            content += f"- **{r}**: {count}\n"

        content += "\n## 3. Recommended Fixes (Blueprint)\n"
        for fix in fixes:
            content += f"- [ ] {fix}\n"

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Saved Markdown report to {path}")


def main():
    parser = argparse.ArgumentParser(description="MT5 Forensic Audit Agent")
    parser.add_argument("--days", type=int, default=30, help="Days to look back")
    parser.add_argument("--symbol", type=str, help="Filter by symbol")
    parser.add_argument("--output", type=str, default="backend/reports/forensics", help="Output directory")
    
    args = parser.parse_args()
    
    # 1. Fetch Data
    fetcher = MT5HistoryFetcher()
    df = fetcher.fetch_deals(days=args.days, symbol=args.symbol)
    fetcher.shutdown()
    
    if df.empty:
        print("No data found. Exiting.")
        return

    # 2. Analyze
    analyzer = PerformanceAnalyzer(df)
    summary = analyzer.calculate_summary()
    equity_curve = analyzer.get_equity_curve()
    
    detector = RootCauseDetector(analyzer.trades)
    loss_clusters = detector.analyze_loss_clusters()
    session_bleed = detector.analyze_session_bleed()
    exit_reasons = detector.analyze_stop_dist()
    
    root_causes = {
        "loss_clusters": loss_clusters,
        "session_bleed": session_bleed,
        "exit_reasons": exit_reasons
    }
    
    # 3. Generate Recommendations
    fixes = []
    if summary['profit_factor'] < 1.0:
        fixes.append("CRITICAL: System is losing money. Enable Dry Run immediately.")
    
    if loss_clusters and loss_clusters[0]['count'] >= 3:
        fixes.append(f"Implement Cooldown: Detected {loss_clusters[0]['count']} consecutive losses.")
        
    worst_hour_pnl = min(session_bleed.values()) if session_bleed else 0
    if worst_hour_pnl < -100: # Threshold
        fixes.append("Session Filter: Stop trading during high-loss hours (check Session Bleed section).")

    if exit_reasons.get('SL', 0) > exit_reasons.get('TP', 0) * 2:
        fixes.append("Review SL Placement: SL hits are > 2x TP hits. Stop might be too tight.")

    # 4. Report
    reporter = ReportGenerator(args.output)
    
    full_data = {
        "summary": summary,
        "root_causes": root_causes,
        "equity_curve": equity_curve,
        "trades": analyzer.trades.to_dict(orient='records')
    }
    
    reporter.save_json(full_data)
    reporter.generate_markdown(summary, root_causes, fixes)
    
    print("\n--- Audit Complete ---")
    print(f"Summary: {summary}")
    print(f"Report saved to: {args.output}")

if __name__ == "__main__":
    main()
