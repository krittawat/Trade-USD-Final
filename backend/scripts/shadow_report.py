"""
Shadow Mode Evaluation Report Generator.
Queries the shadow scoreboard and generates a breakdown of strategy vs symbol vs regime.
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.sqlite import SQLiteStore
from app.core.config import Settings

def generate_shadow_report():
    print("=" * 60)
    print(" SHADOW MODE PERFORMANCE REPORT")
    print("=" * 60)

    settings = Settings()
    db = SQLiteStore(settings)
    db.connect()

    rows = db.get_shadow_scoreboard()
    if not rows:
        print(" No shadow scoreboard data found. Perhaps shadow mode hasn't logged enough evaluated trades yet.")
        return

    df = pd.DataFrame(rows)
    print("\n[TOP STRATEGIES BY WIN RATE]")
    top_wr = df.sort_values(by="win_rate", ascending=False).head(10)
    print(top_wr[["strategy_name", "symbol", "regime", "wins", "losses", "win_rate", "total_pnl"]].to_string(index=False))

    print("\n[TOP STRATEGIES BY PNL]")
    top_pnl = df.sort_values(by="total_pnl", ascending=False).head(10)
    print(top_pnl[["strategy_name", "symbol", "regime", "wins", "losses", "win_rate", "total_pnl"]].to_string(index=False))

    print("\n" + "=" * 60)
    print(" END OF REPORT")

if __name__ == "__main__":
    generate_shadow_report()
