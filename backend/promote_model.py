"""Promote a validated registry model, retaining one exact rollback copy."""

import shutil
import sys
from pathlib import Path

import joblib
import json

ML = Path(__file__).resolve().parent / "app" / "ml"
CURRENT = ML / "model.pkl"
REGISTRY = ML / "registry"


def main(version: str) -> None:
    candidate = REGISTRY / f"{version}.pkl"
    if not candidate.is_file():
        raise SystemExit(f"Unknown model version: {version}")
    artifact = joblib.load(candidate)
    if artifact.get("model_version") != version or "pipeline" not in artifact:
        raise SystemExit("Candidate artifact is invalid")
    report_path = REGISTRY / f"{version}.json"
    if not report_path.is_file():
        raise SystemExit("Candidate metrics report is missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    metrics = report.get("models", {}).get("XGBoost", {})
    if (
        report.get("model_version") != version
        or metrics.get("roc_auc", 0) < 0.95
        or metrics.get("f1", 0) < 0.85
        or metrics.get("false_positive_rate", 1) > 0.05
    ):
        raise SystemExit("Candidate failed promotion quality gates")
    if CURRENT.is_file():
        shutil.copy2(CURRENT, REGISTRY / "rollback.pkl")
    shutil.copy2(candidate, CURRENT)
    print(f"Promoted {version}; previous model saved as registry/rollback.pkl")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python backend/promote_model.py <model-version>")
    main(sys.argv[1])
