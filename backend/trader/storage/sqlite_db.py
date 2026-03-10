import sqlite3
from pathlib import Path
from datetime import datetime, timezone

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
