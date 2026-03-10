import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.trader.storage.sqlite_db import DataStore


def _new_store(tmp_path):
    db_path = tmp_path / "override_store.db"
    return DataStore(db_path=str(db_path))


def _create_completed_run(store: DataStore, run_id: str, engine: str = "both"):
    store.upsert_backtest_override_run(
        run_id,
        engine=engine,
        days=360,
        equity=120.0,
        lookback=100,
        hold_bars=50,
        min_trades=5,
        max_bars_per_run=5000,
        status="RUNNING",
    )
    store.complete_backtest_override_run(run_id, status="COMPLETED", total_rows=0)


def test_latest_top2_filters_and_upsert_idempotency(tmp_path):
    store = _new_store(tmp_path)
    run_id = "run_20260310_new"
    _create_completed_run(store, run_id)

    rows = [
        {"symbol": "XAUUSD", "timeframe": "M5", "rank": 1, "score": 22.5, "total_trades": 14, "win_rate": 62.0, "profit_factor": 1.9, "net_pnl": 40.0, "max_dd": 6.0, "engine": "trader"},
        {"symbol": "XAUUSD", "timeframe": "M15", "rank": 2, "score": 11.2, "total_trades": 9, "win_rate": 55.0, "profit_factor": 1.3, "net_pnl": 15.0, "max_dd": 8.0, "engine": "app"},
        {"symbol": "XAUUSD", "timeframe": "H1", "rank": 3, "score": 30.0, "total_trades": 2, "win_rate": 90.0, "profit_factor": 5.0, "net_pnl": 100.0, "max_dd": 2.0, "engine": "app"},
        {"symbol": "BTCUSD", "timeframe": "M5", "rank": 1, "score": -1.0, "total_trades": 12, "win_rate": 45.0, "profit_factor": 0.9, "net_pnl": -10.0, "max_dd": 9.0, "engine": "trader"},
        {"symbol": "BTCUSD", "timeframe": "H1", "rank": 2, "score": 6.0, "total_trades": 7, "win_rate": 58.0, "profit_factor": 1.2, "net_pnl": 8.0, "max_dd": 7.0, "engine": "app"},
    ]
    inserted = store.upsert_backtest_tf_overrides(run_id, rows, replace_run_rows=True)
    assert inserted == 5

    # Same key should update, not duplicate.
    rows_update = [
        {"symbol": "XAUUSD", "timeframe": "M5", "rank": 1, "score": 25.0, "total_trades": 15, "win_rate": 63.0, "profit_factor": 2.0, "net_pnl": 44.0, "max_dd": 6.0, "engine": "trader"},
    ]
    store.upsert_backtest_tf_overrides(run_id, rows_update, replace_run_rows=False)

    cur = store.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM backtest_tf_overrides WHERE run_id = ? AND symbol = ? AND timeframe = ?", (run_id, "XAUUSD", "M5"))
    assert int(cur.fetchone()[0]) == 1
    cur.execute("SELECT score FROM backtest_tf_overrides WHERE run_id = ? AND symbol = ? AND timeframe = ?", (run_id, "XAUUSD", "M5"))
    assert float(cur.fetchone()[0]) == 25.0

    payload = store.get_latest_backtest_tf_overrides(top_k=2, min_trades=5, require_positive_score=True)
    assert payload["run"]["run_id"] == run_id
    assert set(payload["by_symbol"].keys()) == {"XAUUSD", "BTCUSD"}
    assert [r["timeframe"] for r in payload["by_symbol"]["XAUUSD"]] == ["M5", "M15"]
    assert [r["timeframe"] for r in payload["by_symbol"]["BTCUSD"]] == ["H1"]
    assert len(payload["rows"]) == 3


def test_latest_overrides_empty_fallback(tmp_path):
    store = _new_store(tmp_path)
    payload = store.get_latest_backtest_tf_overrides(top_k=2, min_trades=5, require_positive_score=True)
    assert payload["run"] is None
    assert payload["rows"] == []
    assert payload["by_symbol"] == {}


def test_prune_backtest_override_runs(tmp_path):
    store = _new_store(tmp_path)
    old_run = "run_old"
    new_run = "run_new"
    _create_completed_run(store, old_run)
    _create_completed_run(store, new_run)

    store.upsert_backtest_tf_overrides(
        old_run,
        [{"symbol": "XAUUSD", "timeframe": "M5", "rank": 1, "score": 10, "total_trades": 8, "engine": "trader"}],
        replace_run_rows=True,
    )
    store.upsert_backtest_tf_overrides(
        new_run,
        [{"symbol": "BTCUSD", "timeframe": "M15", "rank": 1, "score": 12, "total_trades": 9, "engine": "app"}],
        replace_run_rows=True,
    )

    old_dt = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    cur = store.conn.cursor()
    cur.execute(
        """
        UPDATE backtest_tf_override_runs
        SET started_at = ?, finished_at = ?
        WHERE run_id = ?
        """,
        (old_dt, old_dt, old_run),
    )
    store.conn.commit()

    deleted = store.prune_backtest_override_runs(retention_days=180)
    assert deleted > 0

    cur.execute("SELECT COUNT(*) FROM backtest_tf_override_runs WHERE run_id = ?", (old_run,))
    assert int(cur.fetchone()[0]) == 0
    cur.execute("SELECT COUNT(*) FROM backtest_tf_overrides WHERE run_id = ?", (old_run,))
    assert int(cur.fetchone()[0]) == 0
    cur.execute("SELECT COUNT(*) FROM backtest_tf_override_runs WHERE run_id = ?", (new_run,))
    assert int(cur.fetchone()[0]) == 1
