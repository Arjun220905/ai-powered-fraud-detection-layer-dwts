"""Bootstrap and run the FastAPI backend and Vite frontend in one terminal."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import shutil
import socket
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
SUPPORTED_PYTHON = ((3, 10), (3, 14))


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


def bootstrap_python() -> list[str]:
    minimum, maximum = SUPPORTED_PYTHON
    if minimum <= sys.version_info[:2] < maximum:
        return [sys.executable]

    # Apple ships an older python3, and the Windows launcher can select a newer one.
    windows_launcher = shutil.which("py") if os.name == "nt" else None
    for minor in range(maximum[1] - 1, minimum[1] - 1, -1):
        version = f"3.{minor}"
        executable = shutil.which(f"python{version}")
        command = [windows_launcher, f"-{version}"] if windows_launcher else [executable] if executable else []
        if command:
            try:
                available = subprocess.run(
                    [*command, "-c", f"import sys; sys.exit(sys.version_info[:2] != (3, {minor}))"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                ).returncode == 0
                if available:
                    return command
            except (OSError, subprocess.TimeoutExpired):
                continue
    fail("Install Python 3.10 through 3.13 and put it on PATH, then rerun the launcher.")


def ensure_python() -> None:
    if not PYTHON.is_file():
        run_step([*bootstrap_python(), "-m", "venv", str(VENV)], "Creating Python virtual environment")
    if Path(sys.prefix).resolve() != VENV.resolve():
        command = [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
        if os.name != "nt":
            os.chdir(ROOT)
            os.execv(str(PYTHON), command)
        try:
            return_code = subprocess.call(command, cwd=ROOT)
        except KeyboardInterrupt:
            return_code = 0
        raise SystemExit(return_code)
    if not SUPPORTED_PYTHON[0] <= sys.version_info[:2] < SUPPORTED_PYTHON[1]:
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


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def configured_port(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        fail(f"{name} must be a port number between 1 and 65535.")
    return int(value)


def url_is_ready(url: str, *, backend: bool = False) -> bool:
    try:
        with urlopen(url, timeout=1) as response:
            body = response.read(65536)
            if backend:
                data = json.loads(body)
                return isinstance(data, dict) and data.get("service") == "dwts" and data.get("model_loaded") is True
            return b'<meta name="application-name" content="DWTS"' in body
    except (OSError, URLError, ValueError):
        return False


ensure_python()

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    npm = ensure_node()
    ensure_model()
    if "--check" in sys.argv or "--setup-only" in sys.argv:
        print("\nReady: Python, frontend packages, dataset, and model are available.", flush=True)
        return 0

    certificate, key = os.getenv("SSL_CERTFILE"), os.getenv("SSL_KEYFILE")
    if bool(certificate) != bool(key):
        raise SystemExit("Set both SSL_CERTFILE and SSL_KEYFILE, or neither")
    backend_port = configured_port("DWTS_BACKEND_PORT", 8000)
    frontend_port = configured_port("DWTS_FRONTEND_PORT", 5173)
    backend_url = f"{'https' if certificate else 'http'}://{'localhost' if certificate else '127.0.0.1'}:{backend_port}"
    health_url = f"{backend_url}/health"
    frontend_ports = [frontend_port] if "DWTS_FRONTEND_PORT" in os.environ else [5173, 5174]
    if url_is_ready(health_url, backend=True):
        for port in frontend_ports:
            frontend_url = f"http://127.0.0.1:{port}"
            if url_is_ready(frontend_url):
                print(f"Project is already running. Frontend: {frontend_url}  Backend: {backend_url}", flush=True)
                return 0
    if port_in_use(backend_port):
        fail(f"Backend port {backend_port} is in use. Stop its owner or set DWTS_BACKEND_PORT.")
    frontend_port = next((port for port in frontend_ports if port != backend_port and not port_in_use(port)), None)
    if frontend_port is None:
        fail("No requested frontend port is available. Set DWTS_FRONTEND_PORT to a free port.")
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend = [str(PYTHON), "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
               "--host", "127.0.0.1", "--port", str(backend_port)]
    if certificate:
        backend.extend(["--ssl-certfile", certificate, "--ssl-keyfile", key])
    commands = [
        backend,
        [
            npm,
            "--prefix",
            str(ROOT / "frontend"),
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(frontend_port),
            "--strictPort",
        ],
    ]
    print("Starting backend and frontend; waiting for both services...", flush=True)
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    origins = os.getenv("ALLOWED_ORIGINS", "")
    env = {**os.environ, "VITE_API_URL": backend_url,
           "ALLOWED_ORIGINS": f"{origins},{frontend_url},http://localhost:{frontend_port}"}
    processes = []
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, **group))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for process, service in zip(processes, ("Backend", "Frontend")):
                return_code = process.poll()
                if return_code is not None:
                    raise SystemExit(f"{service} exited during startup (exit code {return_code}).")
            backend_ready = url_is_ready(health_url, backend=True)
            frontend_ready = url_is_ready(frontend_url)
            if backend_ready and frontend_ready:
                break
            time.sleep(0.25)
        else:
            raise SystemExit("Backend and frontend did not both become ready within 60 seconds")
        print(f"\nReady: {health_url}  Frontend: {frontend_url}", flush=True)
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
