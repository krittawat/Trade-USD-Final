
    def save_backtest_run(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        config: dict,
        result: dict,
    ) -> None:
        """
        บันทึกผลการ Backtest ลง SQLite.

        Args:
            strategy_name: ชื่อ strategy
            symbol: คู่เงิน
            timeframe: Timeframe ที่ใช้ test
            config: dict ของ parameters ที่ใช้ (SL, TP, etc.)
            result: dict ของผลลัพธ์ (win_rate, pf, pnl, dd, trades)
        """
        if not self._conn:
            return

        now = datetime.now(timezone.utc).isoformat()
        config_json = json.dumps(config)

        try:
            self._conn.execute("""
                INSERT INTO backtest_history
                (strategy_name, symbol, timeframe, config_json,
                 win_rate, profit_factor, total_trades,
                 total_pnl, max_dd, run_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                strategy_name,
                symbol,
                timeframe,
                config_json,
                result.get("win_rate", 0),
                result.get("profit_factor", 0),
                result.get("total_trades", 0),
                result.get("total_pnl", 0),
                result.get("max_dd", 0),
                now
            ))
            self._conn.commit()
            logger.info("backtest_result_saved", extra={
                "strategy": strategy_name,
                "symbol": symbol,
                "pf": result.get("profit_factor"),
                "pnl": result.get("total_pnl")
            })

        except Exception as e:
            logger.error("save_backtest_error", extra={"error": str(e)})
