import sys
sys.path.append('d:/VibeCode/Trade')
import backend.trader.scripts.qc_suite as qc

class NullWriter:
    def write(self, s): pass
    def flush(self): pass

sys.stdout = NullWriter()
sys.stderr = NullWriter()

try:
    qc.main()
except Exception:
    pass

sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

for r in qc.results:
    if 'FAIL' in r['status']:
        print("FAILED TEST:", r['test'])
        print("REASON:", r['detail'])
