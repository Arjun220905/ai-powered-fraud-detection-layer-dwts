"""Exercise the launcher on a clean checkout without browser or RPC dependencies."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def check_stack(api: str, frontend: str) -> None:
    with urlopen(frontend, timeout=2) as response:
        if b'<meta name="application-name" content="DWTS"' not in response.read():
            raise ValueError("The frontend did not serve the DWTS dashboard")
    with urlopen(f"{api}/health", timeout=2) as response:
        health = json.load(response)
        if health.get("service") != "dwts" or health.get("model_loaded") is not True:
            raise ValueError("The backend did not load the model")


def main() -> None:
    with socket.socket() as backend, socket.socket() as frontend:
        backend.bind(("127.0.0.1", 0))
        frontend.bind(("127.0.0.1", 0))
        backend_port, frontend_port = backend.getsockname()[1], frontend.getsockname()[1]
    api = f"http://127.0.0.1:{backend_port}"
    dashboard = f"http://127.0.0.1:{frontend_port}"

    with tempfile.TemporaryDirectory(prefix="dwts-launcher-") as directory:
        log_path = Path(directory) / "launcher.log"
        env = {
            **os.environ,
            **dict.fromkeys(("WEB3_PROVIDER_URL", "WEB3_PROVIDER_URLS",
                             "WEB3_WS_PROVIDER_URL", "WEB3_WS_PROVIDER_URLS",
                             "DATABASE_URL", "SSL_CERTFILE", "SSL_KEYFILE",
                             "ALERT_WEBHOOK_URL", "ALERT_SMTP_HOST"), ""),
            "DATABASE_PATH": str(Path(directory) / "smoke.sqlite"),
            "DWTS_BACKEND_PORT": str(backend_port),
            "DWTS_FRONTEND_PORT": str(frontend_port),
        }
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "dev.py"], cwd=ROOT, env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 480
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"Launcher exited with code {process.returncode}")
                    try:
                        check_stack(api, dashboard)
                        if "Press Ctrl+C to stop both." in log_path.read_text(encoding="utf-8", errors="replace"):
                            break
                    except (OSError, ValueError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Launcher never reported both services ready")
                    time.sleep(0.5)

                replay = Request(f"{api}/api/stream/next", method="POST", headers={"Origin": dashboard})
                with urlopen(replay, timeout=10) as response:
                    decision = json.load(response)
                    if response.headers.get("Access-Control-Allow-Origin") != dashboard or not decision.get("explanation"):
                        raise RuntimeError("Dataset replay or the browser origin failed")
                print("One-command startup, dashboard, model health, and dataset replay passed.", flush=True)
            except Exception:
                print(log_path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        # CI has no interactive console. Stop this test's entire process tree.
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
                    else:
                        process.send_signal(signal.SIGINT)
                    process.wait(timeout=20)
            for port in (backend_port, frontend_port):
                with socket.socket() as probe:
                    probe.settimeout(1)
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        raise RuntimeError(f"Test service is still listening on port {port}")


if __name__ == "__main__":
    main()
