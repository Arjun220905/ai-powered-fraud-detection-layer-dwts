"""Repeatable local inference/API latency and DWTS convergence benchmark."""

import argparse
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import joblib
from fastapi.testclient import TestClient


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * percent))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument(
        "--output", default=str(Path(__file__).with_name("benchmark_results.json"))
    )
    args = parser.parse_args()
    if args.requests < 10:
        raise SystemExit("--requests must be at least 10")

    root = Path(__file__).resolve().parents[1]
    row = pd.read_csv(root / "data" / "transaction_dataset.csv").iloc[0].to_dict()
    feature_names = joblib.load(root / "backend" / "app" / "ml" / "model.pkl")["features"]
    address = str(row.pop("Address")).lower()
    row.pop("FLAG")
    row.pop("Index", None)
    row = {
        key: None if pd.isna(row.get(key)) else row.get(key)
        for key in feature_names
    }
    api_key = "benchmark-key-123456789"
    with TemporaryDirectory() as directory:
        os.environ.update({
            "DATABASE_PATH": str(Path(directory) / "benchmark.db"),
            "API_KEY": api_key,
            "WEB3_PROVIDER_URL": "",
            "WEB3_PROVIDER_URLS": "",
            "WEB3_WS_PROVIDER_URL": "",
        })
        from app.main import app
        from app.trust_score.scorer import convergence_steps

        headers = {"X-API-Key": api_key}
        latencies = []
        started = time.perf_counter()
        with TestClient(app) as client:
            for _ in range(args.requests):
                request_started = time.perf_counter()
                response = client.post(
                    "/api/predict",
                    headers=headers,
                    json={"address": address, "features": row},
                )
                if not response.is_success:
                    raise RuntimeError(response.text)
                response.raise_for_status()
                latencies.append((time.perf_counter() - request_started) * 1000)
        duration = time.perf_counter() - started

    result = {
        "requests": args.requests,
        "median_ms": round(percentile(latencies, 0.5), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "p99_ms": round(percentile(latencies, 0.99), 3),
        "throughput_requests_per_second": round(args.requests / duration, 2),
        "requirement_under_2000ms": percentile(latencies, 0.99) < 2000,
        "dwts_convergence": {
            "definition": "within 5 points of repeated-prediction steady state",
            "safe_updates": convergence_steps(70, 0),
            "fraud_updates": convergence_steps(70, 1),
            "requirement_within_10": (
                convergence_steps(70, 0) <= 10
                and convergence_steps(70, 1) <= 10
            ),
        },
    }
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
