import subprocess

p = subprocess.run(
    [".venv\\Scripts\\python.exe", "-m", "backend.trader.scripts.qc_suite"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace"
)

with open("qc_diag.txt", "w", encoding="utf-8") as f:
    f.write("=== STDOUT ===\n")
    f.write(p.stdout)
    f.write("\n=== STDERR ===\n")
    f.write(p.stderr)
