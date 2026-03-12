
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.domain.enums import RegimeType
from app.domain.models import RegimeContext
import pydantic

def test():
    ctx = RegimeContext(regime=RegimeType.TRENDING_UP, actionable=True)
    print(f"Context: {ctx}")
    print(f"Regime: {ctx.regime}")
    print(f"Value: {ctx.regime.value}")
    
    try:
        print(f"Accessing .value on Context: {ctx.value}")
    except AttributeError as e:
        print(f"Caught expected error: {e}")

if __name__ == "__main__":
    test()
