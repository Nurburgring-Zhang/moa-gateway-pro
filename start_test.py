"""Background server starter - launches uvicorn detached."""
import os
import sys
import subprocess

# Force UTF-8 to avoid Windows console encoding issues
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

if __name__ == "__main__":
    log_path = r"D:\MoA Gateway Pro\server_test.log"
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(
            [r"D:\MoA Gateway Pro\.venv\Scripts\python.exe", "-m", "uvicorn",
             "moa_gateway.server:app",
             "--host", "127.0.0.1", "--port", "8910", "--log-level", "info"],
            cwd=r"D:\MoA Gateway Pro",
            env={**os.environ, "MOA_ADMIN_PASSWORD": "TestPassword123!"},
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=0x00000008,  # DETACHED_PROCESS
        )
        print(f"Started PID {proc.pid}")
        sys.exit(0)
