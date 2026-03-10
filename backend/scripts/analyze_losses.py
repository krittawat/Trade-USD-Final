
import sqlite3
import pandas as pd
import json

# Correct path to the ACTIVE database
DB_PATH = r"d:\VibeCode\Trade\backend\backend\data\sqlite\trading.db"

try:
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Overall Performance Summary
    print("\n=== PERFORMANCE SUMMARY ===")
    query_summary = """
    SELECT 
        COUNT(*) as total_trades,
        SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN profit_usd < 0 THEN 1 ELSE 0 END) as losses,
        ROUND(SUM(profit_usd), 2) as net_profit,
        ROUND(AVG(profit_usd), 2) as avg_trade,
        ROUND(MAX(profit_usd), 2) as max_win,
        ROUND(MIN(profit_usd), 2) as max_loss
    FROM trade_journal
    WHERE profit_usd IS NOT NULL
    """
    df_summary = pd.read_sql_query(query_summary, conn)
    print(df_summary.to_string(index=False))
    
    # 2. Performance by Strategy
    print("\n=== BY STRATEGY ===")
    query_strategy = """
    SELECT 
        strategy_name,
        COUNT(*) as count,
        SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as wins,
        ROUND(CAST(SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100, 1) as win_rate,
        ROUND(SUM(profit_usd), 2) as pnl,
        ROUND(SUM(CASE WHEN profit_usd > 0 THEN profit_usd ELSE 0 END) / ABS(SUM(CASE WHEN profit_usd < 0 THEN profit_usd ELSE 0 END)), 2) as pf
    FROM trade_journal
    WHERE profit_usd IS NOT NULL
    GROUP BY strategy_name
    ORDER BY pnl DESC
    """
    df_strategy = pd.read_sql_query(query_strategy, conn)
    print(df_strategy.to_string(index=False))

    # 3. Performance by Symbol
    print("\n=== BY SYMBOL ===")
    query_symbol = """
    SELECT 
        symbol,
        COUNT(*) as count,
        ROUND(SUM(profit_usd), 2) as pnl
    FROM trade_journal
    WHERE profit_usd IS NOT NULL
    GROUP BY symbol
    ORDER BY pnl DESC
    """
    df_symbol = pd.read_sql_query(query_symbol, conn)
    print(df_symbol.to_string(index=False))
    
    # 4. Recent Losses Detail
    print("\n=== LAST 20 LOSSES ===")
    query_losses = """
    SELECT 
        entry_time, symbol, strategy_name, action, 
        entry_price, exit_price, 
        profit_usd, regime, session, notes
    FROM trade_journal
    WHERE profit_usd < 0
    ORDER BY entry_time DESC
    LIMIT 20
    """
    try:
        df_losses = pd.read_sql_query(query_losses, conn)
        print(df_losses.to_string(index=False))
    except Exception as e:
        print(f"Error reading losses: {e}")

    # 5. Decision Traces - Successful Executions
    print("\n=== DECISION TRACES: EXECUTED TRADES ===")
    query_exec = """
    SELECT timestamp, symbol, stage, result, action, strategy_name, reason
    FROM decision_traces
    WHERE result = 'ok' AND stage IN ('execution', 'order')
    ORDER BY timestamp DESC
    LIMIT 20
    """
    try:
        df_exec = pd.read_sql_query(query_exec, conn)
        print(df_exec.to_string(index=False))
    except Exception as e:
        print(f"Error reading decision_traces: {e}")

    # 6. Decision Traces - Errors (FULL DETAIL)
    print("\n=== DECISION TRACES: ERRORS (Full) ===")
    query_errors = """
    SELECT timestamp, symbol, stage, result, reason, details
    FROM decision_traces
    WHERE result = 'error'
    ORDER BY timestamp DESC
    LIMIT 10
    """
    try:
        df_errors = pd.read_sql_query(query_errors, conn)
        # Set pandas options to show full content
        pd.set_option('display.max_colwidth', None)
        print(df_errors.to_string(index=False))
    except Exception as e:
        print(f"Error reading decision_traces: {e}")

    conn.close()

except Exception as e:
    print(f"ERROR: {e}")
