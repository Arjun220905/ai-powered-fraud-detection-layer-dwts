"""Run the FastAPI backend and Vite frontend in one terminal."""

import os
import signal
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not PYTHON.is_file():
    raise SystemExit("Create .venv and install dependencies first; see README.md")
if Path(sys.executable).resolve() != PYTHON.resolve():
    os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("Install Node.js dependencies first; see README.md")

    backend = [str(PYTHON), "-m", "uvicorn", "app.main:app", "--app-dir", "backend"]
    certificate, key = os.getenv("SSL_CERTFILE"), os.getenv("SSL_KEYFILE")
    if bool(certificate) != bool(key):
        raise SystemExit("Set both SSL_CERTFILE and SSL_KEYFILE, or neither")
    if certificate:
        backend.extend(["--ssl-certfile", certificate, "--ssl-keyfile", key])
    commands = [
        backend,
        [npm, "--prefix", str(ROOT / "frontend"), "run", "dev"],
    ]
    print("Starting backend and frontend; waiting for the health check...", flush=True)
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    processes = [subprocess.Popen(command, cwd=ROOT, **group) for command in commands]
    try:
        protocol = "https" if certificate else "http"
        health_url = f"{protocol}://localhost:8000/health"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            return_code = processes[0].poll()
            if return_code is not None:
                return return_code or 1
            try:
                with urlopen(health_url, timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.25)
        else:
            raise SystemExit("Backend did not become healthy within 60 seconds")
        print(f"\nReady: {health_url}  Frontend: http://localhost:5173", flush=True)
        print("Press Ctrl+C to stop both.\n", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        return next(
            process.returncode or 1
            for process in processes if process.returncode is not None
        )
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    process.send_signal(
                        signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM
                    )
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    else:
                        process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
