import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

class DataStore:
    def __init__(self, db_path="d:/VibeCode/Trade/trader/data/opus.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.create_schema()

    def create_schema(self):
        cursor = self.conn.cursor()
        
        # Incident / Blocked events
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                reason TEXT,
                severity TEXT
            )
        ''')
        
        # Executed trades (Live + Dry Run)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER,
                symbol TEXT,
                mode TEXT,
                side TEXT,
                entry_price REAL,
                sl REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                lot_size REAL,
                status TEXT,
                opened_at TEXT,
                closed_at TEXT,
                pnl REAL
            )
        ''')

        # Closed deals from broker history (used for rolling winrate / auto-tuning)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_ticket INTEGER UNIQUE,
                order_ticket INTEGER,
                position_id INTEGER,
                symbol TEXT,
                standard_symbol TEXT,
                model TEXT,
                side TEXT,
                volume REAL,
                price REAL,
                profit REAL,
                commission REAL,
                swap REAL,
                net_profit REAL,
                closed_at TEXT,
                close_reason TEXT
            )
        ''')

        # Daily per-symbol rollup (fast dashboarding + audit trail)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS symbol_winrate_daily (
                day TEXT,
                symbol TEXT,
                trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                net_pnl REAL DEFAULT 0.0,
                gross_profit REAL DEFAULT 0.0,
                gross_loss REAL DEFAULT 0.0,
                updated_at TEXT,
                PRIMARY KEY(day, symbol)
            )
        ''')

        # Daily snapshots of auto-tuned per-symbol thresholds (from real 360D performance)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS symbol_tuner_snapshots (
                day TEXT,
                symbol TEXT,
                lookback_days INTEGER,
                trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0.0,
                profit_factor REAL DEFAULT 0.0,
                net_pnl REAL DEFAULT 0.0,
                min_confidence REAL DEFAULT 0.0,
                min_rr REAL DEFAULT 0.0,
                cooldown_bars INTEGER DEFAULT 0,
                source TEXT,
                updated_at TEXT,
                PRIMARY KEY(day, symbol)
            )
        ''')
        
        # Signals
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                model TEXT,
                side TEXT,
                confidence REAL,
                rationale TEXT
            )
        ''')

        # Shadow Experience (Virtual Trades for Practice)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shadow_experience (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                model TEXT,
                side TEXT,
                entry_price REAL,
                sl REAL,
                tp REAL,
                features TEXT,
                outcome INTEGER DEFAULT 0, -- 0: Pending, 1: Win, -1: Loss
                pnl_r REAL DEFAULT 0,
                closed_at TEXT
            )
        ''')

        # Tournament / Backtest Results
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tournament_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                timeframe TEXT,
                strategy TEXT,
                trades INTEGER,
                win_rate REAL,
                profit_factor REAL,
                max_dd REAL,
                net_pnl REAL,
                equity REAL
            )
        ''')
        
        self.conn.commit()

    def log_incident(self, symbol, reason, severity="WARN"):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO incidents (timestamp, symbol, reason, severity) VALUES (?, ?, ?, ?)",
                       (datetime.now(timezone.utc).isoformat(), symbol, reason, severity))
        self.conn.commit()

    def record_signal(self, signal):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO signals (timestamp, symbol, model, side, confidence, rationale)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (datetime.now(timezone.utc).isoformat(), signal.get('symbol'), signal.get('model'), 
              signal.get('side'), signal.get('confidence'), str(signal.get('rationale'))))
        self.conn.commit()
        
    def record_trade(self, trade_data):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO trades (ticket, symbol, mode, side, entry_price, sl, tp1, tp2, tp3, lot_size, status, opened_at, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_data.get('ticket'), trade_data.get('symbol'), trade_data.get('mode'), trade_data.get('side'),
            trade_data.get('entry_price'), trade_data.get('sl'), trade_data.get('tp1'), trade_data.get('tp2'),
            trade_data.get('tp3'), trade_data.get('lot'), 'OPEN', datetime.now(timezone.utc).isoformat(), 0.0
        ))
        self.conn.commit()

    def record_closed_trade(self, trade_data: Dict) -> bool:
        """
        Store one closed deal from MT5 history.
        Returns True only if a new row was inserted (deduplicated by deal_ticket).
        """
        cursor = self.conn.cursor()

        deal_ticket = trade_data.get("deal_ticket")
        order_ticket = trade_data.get("order_ticket")
        position_id = trade_data.get("position_id")
        symbol = trade_data.get("symbol")
        standard_symbol = trade_data.get("standard_symbol", symbol)
        model = trade_data.get("model", "UNKNOWN")
        side = trade_data.get("side", "")
        volume = float(trade_data.get("volume", 0.0) or 0.0)
        price = float(trade_data.get("price", 0.0) or 0.0)
        profit = float(trade_data.get("profit", 0.0) or 0.0)
        commission = float(trade_data.get("commission", 0.0) or 0.0)
        swap = float(trade_data.get("swap", 0.0) or 0.0)
        net_profit = profit + commission + swap
        closed_at = trade_data.get("closed_at") or datetime.now(timezone.utc).isoformat()
        close_reason = trade_data.get("close_reason", "")

        cursor.execute(
            '''
            INSERT OR IGNORE INTO closed_trades (
                deal_ticket, order_ticket, position_id, symbol, standard_symbol,
                model, side, volume, price, profit, commission, swap, net_profit,
                closed_at, close_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                deal_ticket, order_ticket, position_id, symbol, standard_symbol,
                model, side, volume, price, profit, commission, swap, net_profit,
                closed_at, close_reason
            )
        )

        inserted = cursor.rowcount > 0
        if inserted:
            day = str(closed_at)[:10]
            wins = 1 if net_profit > 0 else 0
            losses = 1 if net_profit < 0 else 0
            gross_profit = net_profit if net_profit > 0 else 0.0
            gross_loss = abs(net_profit) if net_profit < 0 else 0.0
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                '''
                INSERT INTO symbol_winrate_daily (
                    day, symbol, trades, wins, losses, net_pnl, gross_profit, gross_loss, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(day, symbol) DO UPDATE SET
                    trades = trades + 1,
                    wins = wins + excluded.wins,
                    losses = losses + excluded.losses,
                    net_pnl = net_pnl + excluded.net_pnl,
                    gross_profit = gross_profit + excluded.gross_profit,
                    gross_loss = gross_loss + excluded.gross_loss,
                    updated_at = excluded.updated_at
                ''',
                (
                    day,
                    standard_symbol,
                    wins,
                    losses,
                    net_profit,
                    gross_profit,
                    gross_loss,
                    now_iso,
                )
            )

        self.conn.commit()
        return inserted

    def get_symbol_performance(self, days: int = 360, min_trades: int = 0) -> List[Dict]:
        """
        Rolling performance stats by symbol from closed_trades table.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            '''
            SELECT
                COALESCE(standard_symbol, symbol) AS symbol,
                COUNT(*) AS trades,
                SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN net_profit < 0 THEN 1 ELSE 0 END) AS losses,
                SUM(net_profit) AS net_pnl,
                SUM(CASE WHEN net_profit > 0 THEN net_profit ELSE 0 END) AS gross_profit,
                SUM(CASE WHEN net_profit < 0 THEN ABS(net_profit) ELSE 0 END) AS gross_loss
            FROM closed_trades
            WHERE datetime(closed_at) >= datetime('now', ?)
            GROUP BY COALESCE(standard_symbol, symbol)
            HAVING COUNT(*) >= ?
            ORDER BY trades DESC
            ''',
            (f'-{max(1, int(days))} days', max(0, int(min_trades))),
        )

        out = []
        for row in cursor.fetchall():
            symbol, trades, wins, losses, net_pnl, gross_profit, gross_loss = row
            trades = int(trades or 0)
            wins = int(wins or 0)
            losses = int(losses or 0)
            net_pnl = float(net_pnl or 0.0)
            gross_profit = float(gross_profit or 0.0)
            gross_loss = float(gross_loss or 0.0)
            win_rate = (wins / trades) if trades > 0 else 0.0
            pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
            out.append(
                {
                    "symbol": symbol,
                    "trades": trades,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                    "net_pnl": net_pnl,
                    "gross_profit": gross_profit,
                    "gross_loss": gross_loss,
                    "profit_factor": pf,
                }
            )
        return out

    def upsert_symbol_tuner_snapshot(
        self,
        symbol: str,
        stats: Dict,
        profile: Dict,
        lookback_days: int = 360,
    ) -> None:
        cursor = self.conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()
        day = now_iso[:10]

        trades = int((stats or {}).get("trades", profile.get("trades_360d", 0)) or 0)
        wins = int((stats or {}).get("wins", 0) or 0)
        losses = int((stats or {}).get("losses", 0) or 0)
        win_rate = float((stats or {}).get("win_rate", profile.get("win_rate_360d", 0.0)) or 0.0)
        pf = float((stats or {}).get("profit_factor", profile.get("profit_factor_360d", 0.0)) or 0.0)
        net_pnl = float((stats or {}).get("net_pnl", profile.get("net_pnl_360d", 0.0)) or 0.0)
        min_confidence = float((profile or {}).get("min_confidence", 0.0) or 0.0)
        min_rr = float((profile or {}).get("min_rr", 0.0) or 0.0)
        cooldown_bars = int((profile or {}).get("cooldown_bars", 0) or 0)
        source = str((profile or {}).get("source", "default"))

        cursor.execute(
            '''
            INSERT INTO symbol_tuner_snapshots (
                day, symbol, lookback_days, trades, wins, losses, win_rate,
                profit_factor, net_pnl, min_confidence, min_rr, cooldown_bars,
                source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, symbol) DO UPDATE SET
                lookback_days = excluded.lookback_days,
                trades = excluded.trades,
                wins = excluded.wins,
                losses = excluded.losses,
                win_rate = excluded.win_rate,
                profit_factor = excluded.profit_factor,
                net_pnl = excluded.net_pnl,
                min_confidence = excluded.min_confidence,
                min_rr = excluded.min_rr,
                cooldown_bars = excluded.cooldown_bars,
                source = excluded.source,
                updated_at = excluded.updated_at
            ''',
            (
                day, symbol, int(max(1, lookback_days)), trades, wins, losses, win_rate,
                pf, net_pnl, min_confidence, min_rr, cooldown_bars, source, now_iso,
            )
        )
        self.conn.commit()

    def get_latest_symbol_tuner_snapshots(self, symbol: Optional[str] = None) -> List[Dict]:
        cursor = self.conn.cursor()
        params = []
        where_clause = ""

        if symbol:
            where_clause = "WHERE symbol = ?"
            params.append(str(symbol).upper())

        query = f'''
            SELECT
                s.day, s.symbol, s.lookback_days, s.trades, s.wins, s.losses,
                s.win_rate, s.profit_factor, s.net_pnl,
                s.min_confidence, s.min_rr, s.cooldown_bars, s.source, s.updated_at
            FROM symbol_tuner_snapshots s
            JOIN (
                SELECT symbol, MAX(day) AS max_day
                FROM symbol_tuner_snapshots
                GROUP BY symbol
            ) latest
              ON latest.symbol = s.symbol
             AND latest.max_day = s.day
            {where_clause}
            ORDER BY s.symbol ASC
        '''
        cursor.execute(query, tuple(params))

        out = []
        for row in cursor.fetchall():
            (
                day, sym, lookback_days, trades, wins, losses, win_rate, profit_factor,
                net_pnl, min_confidence, min_rr, cooldown_bars, source, updated_at
            ) = row
            wr = float(win_rate or 0.0)
            out.append(
                {
                    "day": day,
                    "symbol": sym,
                    "lookback_days": int(lookback_days or 0),
                    "trades": int(trades or 0),
                    "wins": int(wins or 0),
                    "losses": int(losses or 0),
                    "win_rate": wr,
                    "win_rate_pct": round(wr * 100.0, 2),
                    "profit_factor": float(profit_factor or 0.0),
                    "net_pnl": float(net_pnl or 0.0),
                    "min_confidence": float(min_confidence or 0.0),
                    "min_rr": float(min_rr or 0.0),
                    "cooldown_bars": int(cooldown_bars or 0),
                    "source": source or "default",
                    "updated_at": updated_at,
                }
            )
        return out

    def prune_symbol_tuner_snapshots(self, retention_days: int = 400) -> int:
        cursor = self.conn.cursor()
        days = max(1, int(retention_days))
        cursor.execute(
            '''
            DELETE FROM symbol_tuner_snapshots
            WHERE date(day) < date('now', ?)
            ''',
            (f"-{days} days",)
        )
        deleted = int(cursor.rowcount or 0)
        self.conn.commit()
        return deleted

    def record_shadow_entry(self, signal: dict, features: str):
        """Record a virtual trade for practice."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO shadow_experience (timestamp, symbol, model, side, entry_price, sl, tp, features, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now(timezone.utc).isoformat(), signal.get('symbol'), signal.get('model'), 
            signal.get('side'), signal.get('entry_price'), signal.get('sl'), signal.get('tp1'),
            features, 0
        ))
        self.conn.commit()
        return cursor.lastrowid

    def update_shadow_outcome(self, shadow_id: int, outcome: int, pnl_r: float):
        """Update the result of a virtual trade."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE shadow_experience 
            SET outcome = ?, pnl_r = ?, closed_at = ?
            WHERE id = ?
        ''', (outcome, pnl_r, datetime.now(timezone.utc).isoformat(), shadow_id))
        self.conn.commit()

    def get_pending_shadow_trades(self, symbol: str = None):
        """Get all shadow trades that haven't hit TP/SL yet."""
        cursor = self.conn.cursor()
        if symbol:
            cursor.execute("SELECT * FROM shadow_experience WHERE outcome = 0 AND symbol = ?", (symbol,))
        else:
            cursor.execute("SELECT * FROM shadow_experience WHERE outcome = 0")
        
        # Convert to list of dicts
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def record_tournament_result(self, symbol: str, timeframe: str, result: dict):
        """Record a tournament/backtest result for a strategy."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO tournament_results (
                timestamp, symbol, timeframe, strategy, trades, win_rate, 
                profit_factor, max_dd, net_pnl, equity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            symbol,
            timeframe,
            result.get('name'),
            result.get('trades', 0),
            result.get('wr', 0.0),
            result.get('pf', 0.0),
            result.get('max_dd', 0.0),
            result.get('net', 0.0),
            result.get('eq', 0.0)
        ))
        self.conn.commit()

db = DataStore()
