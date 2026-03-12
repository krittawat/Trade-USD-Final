import pandas as pd

try:
    df = pd.read_csv(r'c:\VibeCode\Trade\backend\data\exports\tournament_results_full.csv')
    print("Unique Symbols in CSV:", df['symbol'].unique())
    print("Unique Strategies in CSV:", df['strategy_name'].unique())
    
    # Get top 1 strategy for each symbol based on composite_score
    top_strategies = df.sort_values('composite_score', ascending=False).groupby('symbol').head(1)
    print("\nTop Strategies per Symbol:")
    print(top_strategies[['symbol', 'strategy_name', 'win_rate', 'profit_factor', 'total_profit_usd', 'max_drawdown_pct', 'composite_score']])

except Exception as e:
    print(f"Error: {e}")
