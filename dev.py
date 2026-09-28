"""Bootstrap and run the FastAPI backend and Vite frontend in one terminal."""

import hashlib
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
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
PACKAGE_LOCK = ROOT / "frontend" / "package-lock.json"
MODEL = ROOT / "backend" / "app" / "ml" / "model.pkl"


def fail(message: str) -> None:
    raise SystemExit(message)


def run_step(command: list[str], label: str) -> None:
    print(f"\n{label}...", flush=True)
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        hint = ""
        if sys.platform == "darwin" and "train_model.py" in " ".join(command):
            hint = "\nOn macOS, run 'brew install libomp' and retry."
        fail(f"{label} failed with exit code {exc.returncode}.{hint}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_python() -> None:
    if not PYTHON.is_file():
        if not (3, 10) <= sys.version_info[:2] < (3, 14):
            fail("Python 3.10 through 3.13 is required to create .venv.")
        run_step([sys.executable, "-m", "venv", str(VENV)], "Creating Python virtual environment")
    if Path(sys.prefix).resolve() != VENV.resolve():
        command = [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
        try:
            return_code = subprocess.call(command, cwd=ROOT)
        except KeyboardInterrupt:
            return_code = 0
        raise SystemExit(return_code)
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        fail("The existing .venv must use Python 3.10 through 3.13.")
    marker = VENV / ".dwts-requirements.sha256"
    expected = digest(REQUIREMENTS)
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != expected:
        run_step([str(PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS)], "Installing Python requirements")
        marker.write_text(expected, encoding="utf-8")


def ensure_node() -> str:
    node = shutil.which("node.exe" if os.name == "nt" else "node") or shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        fail("Install Node.js 20.19.x or 22.12+ (including npm), then retry.")
    try:
        version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
        major, minor = (int(part) for part in version.split(".")[:2])
    except (OSError, ValueError, subprocess.CalledProcessError):
        fail("Could not determine the installed Node.js version.")
    supported = (major == 20 and minor >= 19) or (major >= 22 and not (major == 22 and minor < 12))
    if not supported:
        fail(f"Node.js {version} is unsupported. Install Node.js 20.19.x or 22.12+.")
    marker = ROOT / "frontend" / "node_modules" / ".dwts-lock.sha256"
    expected = digest(PACKAGE_LOCK)
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != expected:
        run_step([npm, "--prefix", str(ROOT / "frontend"), "ci"], "Installing frontend packages")
        marker.write_text(expected, encoding="utf-8")
    return npm


def ensure_model() -> None:
    sources = [
        ROOT / "data" / "transaction_dataset.csv",
        ROOT / "backend" / "app" / "ml" / "train_model.py",
        ROOT / "backend" / "app" / "ml" / "feature_engineering.py",
    ]
    missing = [str(path.relative_to(ROOT)) for path in sources if not path.is_file()]
    if missing:
        fail(f"Required training file is missing: {', '.join(missing)}")
    if not MODEL.is_file() or any(path.stat().st_mtime > MODEL.stat().st_mtime for path in sources):
        run_step([str(PYTHON), str(ROOT / "backend" / "app" / "ml" / "train_model.py")], "Training the local fraud model")


ensure_python()

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    npm = ensure_node()
    ensure_model()
    if "--check" in sys.argv or "--setup-only" in sys.argv:
        print("\nReady: Python, frontend packages, dataset, and model are available.", flush=True)
        return 0

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
                    if os.name == "nt":
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
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
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(main())
