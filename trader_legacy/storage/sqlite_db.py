import sqlite3
from pathlib import Path
from datetime import datetime

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
        
        self.conn.commit()

    def log_incident(self, symbol, reason, severity="WARN"):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO incidents (timestamp, symbol, reason, severity) VALUES (?, ?, ?, ?)",
                       (datetime.utcnow().isoformat(), symbol, reason, severity))
        self.conn.commit()

    def record_signal(self, signal):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO signals (timestamp, symbol, model, side, confidence, rationale)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (datetime.utcnow().isoformat(), signal.get('symbol'), signal.get('model'), 
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
            trade_data.get('tp3'), trade_data.get('lot'), 'OPEN', datetime.utcnow().isoformat(), 0.0
        ))
        self.conn.commit()

db = DataStore()
