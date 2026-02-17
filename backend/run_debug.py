"""Quick debug runner — captures clean stdout/stderr to file."""
import subprocess
import sys
import time

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    encoding="utf-8",
    errors="replace",
)

start = time.time()
lines = []
try:
    while time.time() - start < 20:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        if line:
            lines.append(line.rstrip())
except KeyboardInterrupt:
    proc.terminate()

proc.terminate()
proc.wait(timeout=5)

# Write clean output
with open("debug_output.txt", "w", encoding="utf-8") as f:
    for l in lines:
        f.write(l + "\n")

print(f"Captured {len(lines)} lines to debug_output.txt")
# Print last 40 lines
for l in lines[-40:]:
    print(l)
