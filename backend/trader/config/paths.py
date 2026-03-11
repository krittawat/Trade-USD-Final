from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"
TRADER_ROOT = BACKEND_ROOT / "trader"
TRADER_CONFIG_DIR = TRADER_ROOT / "config"
TRADER_DATA_DIR = TRADER_ROOT / "data"
BACKEND_SQLITE_DIR = BACKEND_ROOT / "data" / "sqlite"

SETTINGS_PATH = TRADER_CONFIG_DIR / "settings.json"
ENV_PATH = PROJECT_ROOT / ".env"
OPUS_DB_PATH = TRADER_DATA_DIR / "opus.db"
REPLAY_DB_PATH = TRADER_DATA_DIR / "replay_180d.db"
BRAIN_DB_PATH = BACKEND_SQLITE_DIR / "brain.db"
TRADING_DB_PATH = BACKEND_SQLITE_DIR / "trading.db"
