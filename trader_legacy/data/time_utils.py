from datetime import datetime
import pandas as pd
import pytz
import json

class TimeUtils:
    def __init__(self, config_path: str = "d:/VibeCode/Trade/trader/config/settings.json"):
        with open(config_path, 'r') as f:
            config = json.load(f)
        self.session_hours = config.get("session_hours_utc", {})

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
        Overlap logic can be added if required.
        """
        time_str = dt.strftime("%H:%M")
        
        # Simple session rules (assuming non-overlapping strictly for baseline)
        if self.session_hours.get("asia_start") <= time_str < self.session_hours.get("asia_end"):
            return "ASIA"
        elif self.session_hours.get("london_start") <= time_str < self.session_hours.get("london_end"):
            return "LONDON"
        elif self.session_hours.get("ny_start") <= time_str < self.session_hours.get("ny_end"):
            return "NY"
        return "OUT_OF_SESSION"
        
    def add_session_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Adds a 'session' column to historical bars dataframe."""
        if 'time' not in df.columns:
            return df
        
        # Ensure time is datetime and UTC
        df['datetime_utc'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df['session'] = df['datetime_utc'].apply(self.assign_session)
        return df

time_utils = TimeUtils()
