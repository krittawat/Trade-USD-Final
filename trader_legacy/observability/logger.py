import json
import logging
from datetime import datetime
from pathlib import Path

# Setup logging directory
LOG_DIR = Path("d:/VibeCode/Trade/trader/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        
        # Add any extra metrics/reasons passed in the `extra` kwarg
        if hasattr(record, "reason"):
            log_record["reason"] = record.reason
        if hasattr(record, "metrics"):
            log_record["metrics"] = record.metrics
        if hasattr(record, "symbol"):
            log_record["symbol"] = record.symbol
        if hasattr(record, "mode"):
            log_record["mode"] = record.mode
            
        return json.dumps(log_record)

def setup_logger(name="opus_logger", log_file="trade.log"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers
    if not logger.handlers:
        file_handler = logging.FileHandler(LOG_DIR / log_file)
        file_handler.setFormatter(JSONFormatter())
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
    return logger

logger = setup_logger()

# Example usage:
# logger.info("Trade blocked by risk engine", extra={"reason": "Daily loss exceeded", "metrics": {"daily_loss": 6.5}})
