-- Daily Strategy Ranking Report
-- Usage example:
--   sqlite3 backend/data/sqlite/trading.db ".read backend/sql/reports/daily_strategy_ranking.sql"

-- 1) Latest ranked strategies (all rows)
WITH latest AS (
  SELECT MAX(run_at) AS run_at
  FROM strategy_rankings_daily
)
SELECT
  r.symbol,
  r.regime,
  r.rank,
  r.strategy,
  r.total_trades,
  ROUND(r.win_rate, 2) AS win_rate,
  ROUND(r.profit_factor, 3) AS profit_factor,
  ROUND(r.max_drawdown_pct, 2) AS max_drawdown_pct,
  ROUND(r.total_pnl, 2) AS total_pnl,
  ROUND(r.score, 3) AS score,
  r.production_eligible,
  r.reject_reasons,
  r.source_mix
FROM strategy_rankings_daily r
JOIN latest l ON r.run_at = l.run_at
ORDER BY r.symbol, r.regime, r.rank;

-- 2) Latest production winners (rank=1 + eligible)
WITH latest AS (
  SELECT MAX(run_at) AS run_at
  FROM strategy_rankings_daily
)
SELECT
  r.symbol,
  r.regime,
  r.strategy,
  r.total_trades,
  ROUND(r.win_rate, 2) AS win_rate,
  ROUND(r.profit_factor, 3) AS profit_factor,
  ROUND(r.max_drawdown_pct, 2) AS max_drawdown_pct,
  ROUND(r.score, 3) AS score
FROM strategy_rankings_daily r
JOIN latest l ON r.run_at = l.run_at
WHERE r.rank = 1
  AND r.production_eligible = 1
ORDER BY r.symbol, r.regime;

-- 3) Current backtest_routing snapshot (what runtime uses)
SELECT
  symbol,
  regime,
  strategy,
  total_trades,
  ROUND(win_rate, 2) AS win_rate,
  ROUND(profit_factor, 3) AS profit_factor,
  ROUND(max_drawdown_pct, 2) AS max_drawdown_pct,
  ROUND(score, 3) AS score,
  updated_at
FROM backtest_routing
ORDER BY symbol, regime;
