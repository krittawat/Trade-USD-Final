from datetime import datetime
import pandas as pd
import pytz
import json

from backend.trader.config.paths import SETTINGS_PATH

class TimeUtils:
    def __init__(self, config_path=SETTINGS_PATH):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            self.session_hours = config.get("session_hours_utc", {})
        except FileNotFoundError:
            # Fallback for direct script execution or misplaced config
            self.session_hours = {
                "asia_start": "00:00", "asia_end": "08:00",
                "london_start": "08:00", "london_end": "16:00",
                "ny_start": "13:00", "ny_end": "21:00"
            }

    def to_utc(self, timestamp) -> datetime:
        """Converts MT5 timestamp (broker time) or int to UTC datetime."""
        if isinstance(timestamp, (int, float)):
            dt = datetime.utcfromtimestamp(timestamp)
        else:
            dt = timestamp
        return dt.replace(tzinfo=pytz.UTC)

    def assign_session(self, dt: datetime) -> str:
        """
        Assigns a session label (Asia, London, NY) to a UTC datetime.
        Supports overlapping sessions and WEEKEND.
        """
        # [USER RULE] Weekend detection (Saturday=5, Sunday=6)
        # Saturday all day + Sunday until 21:00 UTC (Market usually opens around 21:00-22:00 Sunday UTC)
        if dt.weekday() == 5:  # Saturday
            return "WEEKEND"
        if dt.weekday() == 6 and dt.hour < 21:  # Sunday morning/afternoon
            return "WEEKEND"

        time_str = dt.strftime("%H:%M")
        
        active_sessions = []
        
        # Check Asia
        if self.session_hours.get("asia_start") <= time_str < self.session_hours.get("asia_end"):
            active_sessions.append("ASIA")
            
        # Check London
        if self.session_hours.get("london_start") <= time_str < self.session_hours.get("london_end"):
            active_sessions.append("LONDON")
            
        # Check NY
        if self.session_hours.get("ny_start") <= time_str < self.session_hours.get("ny_end"):
            active_sessions.append("NY")
            
        if not active_sessions:
            return "SLEEP"
            
        return "/".join(active_sessions)

    def get_current_session_label(self) -> str:
        """Returns the current market session as a colored string for terminal display."""
        now_utc = datetime.now(pytz.UTC)
        session = self.assign_session(now_utc)
        
        # ANSI colors (consistent with main.py C class if available, else literal)
        YELLOW = "\033[93m"
        CYAN = "\033[96m"
        MAGENTA = "\033[95m"
        GRAY = "\033[90m"
        RST = "\033[0m"
        BOLD = "\033[1m"
        
        styled = session
        if "NY" in session: styled = styled.replace("NY", f"{MAGENTA}NY{RST}")
        if "LONDON" in session: styled = styled.replace("LONDON", f"{CYAN}LONDON{RST}")
        if "ASIA" in session: styled = styled.replace("ASIA", f"{YELLOW}ASIA{RST}")
        if "SLEEP" in session: styled = f"{GRAY}SLEEP{RST}"
        
        return f"{BOLD}{styled}{RST}"
        
    def add_session_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Adds a 'session' column to historical bars dataframe."""
        if 'time' not in df.columns:
            return df
        
        # Ensure time is datetime and UTC
        df['datetime_utc'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df['session'] = df['datetime_utc'].apply(self.assign_session)
        return df

time_utils = TimeUtils()
